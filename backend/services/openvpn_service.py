# -*- coding: utf-8 -*-
"""
OpenVPN 管理服务
- 读取服务器地址等
- 生成 .ovpn 客户端配置（Nyr inline / 传统内联，PEM 严格清洗）
- 强制踢下线（management / iptables）
- 服务重载与配置校验
"""
import logging
import os
import re
import socket
import time
from pathlib import Path

from .. import config
from ..security import is_safe_path_component
from . import easyrsa_service
from .cmd_runner import run_as_root

logger = logging.getLogger("ovpn.openvpn")


class OpenVPNError(Exception):
    """对外友好的 OpenVPN 异常。"""
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


def get_server_ip() -> str:
    """
    Return only a configured server address; never guess from a live route.
    """
    # 优先读环境变量
    env_ip = os.environ.get("OVPM_SERVER_IP")
    if env_ip:
        return env_ip
    try:
        common = easyrsa_service.read_client_common()
        for raw in common.splitlines():
            line = raw.strip()
            if line.startswith("remote ") and not line.startswith("#"):
                fields = line.split()
                if len(fields) >= 2 and fields[1]:
                    return fields[1]
    except Exception:
        pass
    try:
        from ..database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='server_ip'").fetchone()
        if row and str(row[0]).strip():
            return str(row[0]).strip()
    except Exception:
        pass
    raise OpenVPNError("未配置服务器公网 IP/域名，请在 client-common.txt 或 OVPM_SERVER_IP 中设置")


def _has_directive(content: str, name: str) -> bool:
    return bool(re.search(rf"(?mi)^\s*{re.escape(name)}(?:\s|$)", content))


def _ensure_client_tuning(content: str) -> str:
    """Add missing transport defaults without replacing administrator values."""
    defaults = [
        ("tun-mtu", f"tun-mtu {config.OPENVPN_TUN_MTU}"),
        ("mssfix", f"mssfix {config.OPENVPN_MSSFIX}"),
        ("mtu-disc", "mtu-disc yes"),
        ("data-ciphers", "data-ciphers AES-128-GCM:AES-256-GCM"),
        ("data-ciphers-fallback", "data-ciphers-fallback AES-128-GCM"),
        ("sndbuf", f"sndbuf {config.OPENVPN_SNDBUF}"),
        ("rcvbuf", f"rcvbuf {config.OPENVPN_RCVBUF}"),
    ]
    if not _has_directive(content, "cipher"):
        defaults.append(("cipher", "cipher AES-128-GCM"))
    missing = [line for key, line in defaults if not _has_directive(content, key)]
    if not missing:
        return content
    insert_at = content.find("<ca>")
    block = "\n".join(missing) + "\n"
    return (content[:insert_at] + block + content[insert_at:]) if insert_at >= 0 else block + content


def build_ovpn_config(name: str) -> str:
    """
    组装客户端 .ovpn。

    多级回退，任一成功即返回：
      A. Nyr: client-common.txt + pki/inline/private/<name>.inline
      B. 传统: <ca>/<cert>/<key> 内联 + tls-crypt/tls-auth
    全部失败时抛出带原因的 OpenVPNError（前端可展示）。
    """
    if not is_safe_path_component(name):
        raise OpenVPNError("客户端名称不合法")

    errors = []

    # ---------- 方式 A：Nyr inline ----------
    try:
        common = easyrsa_service.read_client_common()
        inline = easyrsa_service.read_client_inline(name)
        if not inline:
            raise OpenVPNError("未找到 inline 配置文件")
        lines = []
        for block in (common, inline):
            for line in block.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                lines.append(line)
        content = "\n".join(lines).rstrip() + "\n"
        # EasyRSA inline 通常只有 CA/证书/私钥；tls-crypt 密钥在
        # OpenVPN 目录中，且往往是 root-only。统一补齐缺失的块。
        content = _sanitize_ovpn_pem_blocks(content)
        content = _ensure_client_tuning(content)

        if "<ca>" not in content:
            ca_cert = easyrsa_service.read_ca_cert()
            content += f"\n<ca>\n{ca_cert.strip()}\n</ca>\n"

        if "<tls-crypt>" not in content and "<tls-auth>" not in content:
            tls_auth = easyrsa_service.read_tls_auth_key()
            if tls_auth:
                try:
                    cfg_txt = Path(config.OPENVPN_CONFIG).read_text(
                        encoding="utf-8", errors="replace"
                    )
                    tls_tag = "tls-crypt" if "tls-crypt" in cfg_txt else "tls-auth"
                except Exception:
                    tls_tag = "tls-crypt"
                content += f"\n<{tls_tag}>\n{tls_auth.strip()}\n</{tls_tag}>\n"

        _validate_ovpn(content, name)
        logger.info("生成 ovpn 成功(Nyr inline): %s", name)
        return content
    except (easyrsa_service.EasyRSAError, OpenVPNError) as e:
        msg = e.message if hasattr(e, "message") else str(e)
        errors.append(f"inline方式: {msg}")
        logger.warning("Nyr inline 组装失败(%s): %s", name, msg)

    # ---------- 方式 B：传统内联 ----------
    try:
        ca_cert = easyrsa_service.read_ca_cert()
        client_cert, client_key = easyrsa_service.read_client_files(name)
    except easyrsa_service.EasyRSAError as e:
        errors.append(f"读取证书: {e.message}")
        logger.error(
            "读取证书失败 name=%s easyrsa=%s ovpn_dir=%s cert_dir=%s",
            name, config.EASYRSA_DIR, config.OPENVPN_DIR, config.CLIENT_CERT_DIR,
        )
        raise OpenVPNError("无法生成配置：" + "；".join(errors))

    try:
        cert_pem = easyrsa_service.extract_pem_only(client_cert, "cert")
        key_pem = easyrsa_service.extract_pem_only(client_key, "key")
        if not cert_pem or "BEGIN CERTIFICATE" not in cert_pem:
            raise OpenVPNError("客户端证书不是有效 PEM")
        if not key_pem or "BEGIN" not in key_pem:
            raise OpenVPNError("客户端私钥不是有效 PEM")
    except OpenVPNError as e:
        errors.append(e.message)
        raise OpenVPNError("无法生成配置：" + "；".join(errors))

    tls_auth = easyrsa_service.read_tls_auth_key()

    # remote 优先取 client-common（Nyr 会写入公网/正确地址）
    server_ip = get_server_ip()
    vpn_port = config.VPN_PORT
    try:
        common_txt = easyrsa_service.read_client_common()
        for line in common_txt.splitlines():
            line = line.strip()
            if line.startswith("remote ") and not line.startswith("#"):
                parts = line.split()
                if len(parts) >= 3:
                    server_ip = parts[1]
                    vpn_port = int(parts[2])
                elif len(parts) == 2:
                    server_ip = parts[1]
                break
    except Exception:
        pass

    tls_is_crypt = False
    if tls_auth:
        try:
            cfg_txt = Path(config.OPENVPN_CONFIG).read_text(encoding="utf-8", errors="replace")
            tls_is_crypt = "tls-crypt" in cfg_txt
        except Exception:
            tls_is_crypt = "BEGIN OpenVPN Static key" in tls_auth

    parts = [
        f"client",
        "dev tun",
        f"proto {config.VPN_PROTO}",
        f"remote {server_ip} {vpn_port}",
        "resolv-retry infinite",
        "nobind",
        "persist-key",
        "persist-tun",
        "remote-cert-tls server",
        f"tun-mtu {config.OPENVPN_TUN_MTU}",
        f"mssfix {config.OPENVPN_MSSFIX}",
        "mtu-disc yes",
        "data-ciphers AES-128-GCM:AES-256-GCM",
        "data-ciphers-fallback AES-128-GCM",
        "cipher AES-128-GCM",
        f"sndbuf {config.OPENVPN_SNDBUF}",
        f"rcvbuf {config.OPENVPN_RCVBUF}",
        "auth SHA512",
        "verb 3",
        "",
        "<ca>",
        ca_cert.strip(),
        "</ca>",
        "",
        "<cert>",
        cert_pem,
        "</cert>",
        "",
        "<key>",
        key_pem,
        "</key>",
        "",
    ]

    if tls_auth:
        if tls_is_crypt:
            parts.extend(["<tls-crypt>", tls_auth.strip(), "</tls-crypt>", ""])
        else:
            parts.extend([
                "key-direction 1",
                "<tls-auth>",
                tls_auth.strip(),
                "</tls-auth>",
                "",
            ])
    else:
        logger.warning("未找到 tls-crypt/tls-auth 密钥: %s", name)

    content = "\n".join(parts)
    try:
        _validate_ovpn(content, name)
    except OpenVPNError as e:
        errors.append(f"配置校验: {e.message}")
        raise OpenVPNError("无法生成配置：" + "；".join(errors))

    logger.info("生成 ovpn 成功(传统内联): %s", name)
    return content


_TAG_KIND = {
    "ca": "cert",
    "cert": "cert",
    "key": "key",
    "tls-crypt": "tls",
    "tls-auth": "tls",
}


def _sanitize_ovpn_pem_blocks(content: str) -> str:
    """
    将 <ca>/<cert>/<key>/<tls-crypt>/<tls-auth> 收成纯 PEM。
    剥掉 EasyRSA/openssl 文本 dump；清洗失败的块保持原样，交给校验拒绝。
    """
    if not content:
        return content

    def _repl(match: "re.Match") -> str:
        tag = match.group(1)
        inner = match.group(2)
        kind = _TAG_KIND.get(tag)
        if not kind:
            return match.group(0)
        cleaned = easyrsa_service.extract_pem_only(inner, kind)
        if not cleaned:
            return match.group(0)
        return f"<{tag}>\n{cleaned}\n</{tag}>"

    return re.sub(
        r"<(ca|cert|key|tls-crypt|tls-auth)>(.*?)</\1>",
        _repl,
        content,
        flags=re.S,
    )


def _validate_ovpn(content: str, name: str) -> None:
    """
    下载前校验 ovpn 合法性，避免把坏配置交给客户端。
    """
    if not content or len(content) < 80:
        raise OpenVPNError("配置内容过短，生成失败")

    # 必须含证书/密钥/CA 或 Nyr inline 的 tls 块
    if "<cert>" not in content or "</cert>" not in content:
        raise OpenVPNError("配置缺少证书块")
    if "<key>" not in content or "</key>" not in content:
        raise OpenVPNError("配置缺少私钥块")
    if "<ca>" not in content or "</ca>" not in content:
        raise OpenVPNError("配置缺少 CA 块")

    # <cert> 内必须是纯 PEM，禁止 openssl 文本
    cert_start = content.find("<cert>")
    cert_end = content.find("</cert>")
    if cert_start < 0 or cert_end < 0 or cert_end <= cert_start:
        raise OpenVPNError("证书块格式错误")
    cert_inner = content[cert_start + len("<cert>"):cert_end]
    if "-----BEGIN CERTIFICATE-----" not in cert_inner:
        raise OpenVPNError("证书块不是有效 PEM")
    # 仅检查 PEM 之前的文本 dump；pem_start==0 表示本身就是纯 PEM，不得误杀
    pem_start = cert_inner.find("-----BEGIN CERTIFICATE-----")
    if pem_start > 0:
        garbage = cert_inner[:pem_start]
        if garbage.strip() and any(k in garbage for k in ("Certificate:", "Signature Algorithm", "Modulus:")):
            raise OpenVPNError("证书块含非 PEM 文本，已拒绝输出")

    # remote 必须存在
    if "remote " not in content:
        raise OpenVPNError("配置缺少服务器地址")
    logger.info("ovpn 校验通过: %s", name)


def _session_is_online(real_address: str, common_name: str = "") -> bool:
    from .status_parser import parse_status_log
    return any(
        item.real_address == real_address
        and (not common_name or item.common_name == common_name)
        for item in parse_status_log()
    )


def kick_client(real_address: str, common_name: str = "") -> dict:
    """
    强制踢下线客户端。

    优先 management 接口 kill；否则对客户端来源 IPv4 添加 iptables DROP。
    """
    if not real_address or ":" not in real_address:
        raise OpenVPNError("无效的客户端地址")
    if common_name and not is_safe_path_component(common_name):
        raise OpenVPNError("客户端名称不合法")

    # 安全校验：IPv4:端口 严格校验（防 iptables 参数注入）
    from ..security import is_valid_ip_port
    if not is_valid_ip_port(real_address):
        raise OpenVPNError("客户端地址格式不正确")

    # 方案A：management interface（若配置）
    mgmt_port = os.environ.get("OVPM_OVPN_MGMT_PORT")
    if mgmt_port:
        try:
            target = common_name or real_address
            if _kick_via_management(target, int(mgmt_port)):
                for _ in range(6):
                    if not _session_is_online(real_address, common_name):
                        logger.info("management 主动断开成功: cn=%s address=%s", common_name, real_address)
                        return {"method": "management", "message": "已通过 management 强制下线"}
                    time.sleep(0.5)
                logger.warning("management 返回成功但会话仍在线，转入临时阻断: cn=%s address=%s", common_name, real_address)
            else:
                logger.warning("management 不支持或未确认 kill: target=%s", target)
        except Exception as e:
            logger.warning("management 方式踢人失败，尝试备用方案: %s", e)

    # 方案B：将客户端 IP 加入 iptables DROP（规则不会自动过期）（-w 等待锁，避免 xtables 锁冲突）
    try:
        ip_only = real_address.rsplit(":", 1)[0]
        from ..security import is_valid_ipv4
        if not is_valid_ipv4(ip_only):
            raise OpenVPNError("客户端地址格式不正确")
        result = run_as_root(
            ["kick-block", ip_only, common_name or "unknown", str(config.OPENVPN_KICK_BLOCK_SECONDS)],
            timeout=15,
        )
        if result.success:
            logger.warning("已添加临时阻断并安排自动回收: cn=%s ip=%s ttl=%ss", common_name, ip_only, config.OPENVPN_KICK_BLOCK_SECONDS)
            return {
                "method": "temporary_block",
                "message": f"management 未确认断开；已临时阻断来源并将在 {config.OPENVPN_KICK_BLOCK_SECONDS} 秒内自动回收",
            }
    except OpenVPNError:
        raise
    except Exception as e:
        logger.warning("iptables 方式失败: %s", e)

    raise OpenVPNError("management 不支持或未确认断开，临时阻断规则也未能安全创建")


def _kick_via_management(target: str, port: int) -> bool:
    """
    通过 OpenVPN management interface 踢人。
    management 通常绑定 127.0.0.1:port
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(("127.0.0.1", port))
        # 读取欢迎信息
        s.recv(1024)
        # 发送 kill 命令
        cmd = f"kill {target}\n"
        s.sendall(cmd.encode("utf-8"))
        resp = s.recv(1024).decode("utf-8", errors="replace")
        s.close()
        return "SUCCESS" in resp.upper() or "ok" in resp.lower()
    except Exception as e:
        logger.warning("management 连接失败: %s", e)
        return False





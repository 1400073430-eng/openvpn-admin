# -*- coding: utf-8 -*-
"""
EasyRSA 证书服务
- 生成客户端证书/私钥
- 吊销证书
- 读取证书/私钥/inline 用于组装 ovpn

安全：全部使用参数列表，禁止 shell 拼接。
"""
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from .. import config
from ..config import safe_exists, safe_is_file
from ..security import is_safe_path_component
from .cmd_runner import CommandError, run_as_root

logger = logging.getLogger("ovpn.easyrsa")


class EasyRSAError(Exception):
    """对外友好的 EasyRSA 异常。"""
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


def _map_easyrsa_error(stderr: str) -> str:
    """把 helper/easyrsa 的 stderr 映射为前端可读文案。"""
    s = (stderr or "").lower()
    if "no new privileges" in s:
        return "提权失败：服务禁止 sudo（请检查 systemd NoNewPrivileges）"
    if "client name required" in s or "not allowed" in s or "invalid" in s:
        return "证书操作参数被 helper 拒绝"
    if "already" in s or "exists" in s or "in use" in s or "重复" in s:
        return "客户端证书已存在，请先吊销或更换名称"
    if "permission" in s or "denied" in s or "无权限" in s:
        return "证书目录权限不足"
    if "not found" in s or "no such" in s or "未安装" in s:
        return "EasyRSA 未安装或路径不正确"
    if "unable to decrypt" in s or "ca" in s and "error" in s:
        return "EasyRSA/CA 初始化异常"
    return "证书操作失败"


def _run_easyrsa(args: list, timeout: int = 120) -> str:
    """通过固定 root helper 执行白名单 EasyRSA 操作。"""
    allowed_subcmds = {
        "build-client-full", "build-server-full", "revoke", "gen-crl",
        "init-pki", "build-ca", "show-cert", "list-crl", "gen-tls-crypt-key",
    }
    allowed_option_prefixes = ("--days=", "--batch", "--req-cn=", "--digest=")
    subcmd = None
    for a in args:
        if a.startswith("--"):
            if not any(a == p or a.startswith(p) for p in allowed_option_prefixes):
                raise EasyRSAError("不支持的证书操作选项")
            continue
        subcmd = a
        break
    if not subcmd or subcmd not in allowed_subcmds:
        raise EasyRSAError("不支持的证书操作")
    if not safe_exists(config.EASYRSA_DIR / "easyrsa") and not safe_exists(config.EASYRSA_BIN):
        raise EasyRSAError("EasyRSA 未安装或路径不正确")
    clean_args = [a for a in args if a != "--batch"]
    try:
        result = run_as_root(["easyrsa"] + clean_args, timeout=timeout)
        if not result.success:
            detail = (result.stderr or result.stdout or "").strip()
            logger.warning("easyrsa 失败 rc=%s detail=%s", result.returncode, detail[:500])
            raise EasyRSAError(_map_easyrsa_error(detail), detail=detail)
        return result.stdout
    except CommandError as e:
        detail = e.detail or str(e)
        logger.warning("easyrsa 命令异常: %s", detail[:500])
        raise EasyRSAError(_map_easyrsa_error(detail), detail=detail)


def generate_client_cert(name: str, nopass: bool = True) -> Tuple[str, str]:
    """
    生成客户端完整证书（对齐 Nyr/openvpn-install）。

    命令: easyrsa --batch --days=3650 build-client-full <name> nopass

    返回:
        (cert_path, key_path)
    """
    if not is_safe_path_component(name):
        raise EasyRSAError("客户端名称不合法")

    pki = config.EASYRSA_DIR / "pki"
    cert_path = pki / "issued" / f"{name}.crt"
    key_path = pki / "private" / f"{name}.key"
    if safe_exists(cert_path):
        raise EasyRSAError(
            "客户端证书已存在，请先吊销或更换名称",
            detail=str(cert_path),
        )

    days = str(config.EASYRSA_CERT_DAYS)
    args = ["--batch", f"--days={days}", "build-client-full", name]
    if nopass:
        args.append("nopass")

    _run_easyrsa(args, timeout=180)

    if not safe_exists(cert_path) or not safe_exists(key_path):
        raise EasyRSAError("证书生成后文件缺失")

    # 证书副本目录可能位于 root-only 的 /etc/openvpn 路径。
    # Web 服务保持 NoNewPrivileges=true，不直接写该目录；由 root helper
    # 在白名单内完成复制。若目录本身就是 PKI inline/private，则无需复制。
    try:
        client_dir = config.CLIENT_CERT_DIR.resolve()
        pki_root = pki.resolve()
        if not (client_dir == pki_root or str(client_dir).startswith(str(pki_root) + os.sep)):
            result = run_as_root(["copy-client-files", name], timeout=30)
            if not result.success:
                detail = (result.stderr or result.stdout or "").strip()
                logger.warning("复制证书副本失败（不影响使用）: %s", detail[:500])
    except Exception as e:
        logger.warning("复制证书副本失败（不影响使用）: %s", e)

    logger.info("已生成客户端证书: %s (days=%s)", name, days)
    return str(cert_path), str(key_path)


def revoke_client_cert(name: str) -> None:
    """
    吊销客户端证书（对齐 Nyr/openvpn-install）。

    步骤:
      1. easyrsa --batch revoke <name>
      2. easyrsa --batch --days=3650 gen-crl
      3. 删除 reqs/<name>.req、private/<name>.key（Nyr 同款清理）
      4. 复制 crl.pem 到 OpenVPN 目录并 chown nobody
    """
    if not is_safe_path_component(name):
        raise EasyRSAError("客户端名称不合法")

    _run_easyrsa(["--batch", "revoke", name], timeout=60)

    # 重新生成 CRL（失败不回滚吊销）
    try:
        _run_easyrsa(["--batch", f"--days={config.EASYRSA_CERT_DAYS}", "gen-crl"], timeout=60)
    except EasyRSAError as e:
        logger.warning("生成 CRL 失败（证书已吊销）: %s", e.detail or e.message)

    pki = config.EASYRSA_DIR / "pki"

    # Nyr 同款：清理私钥与请求文件
    for rel in (f"reqs/{name}.req", f"private/{name}.key"):
        p = pki / rel
        try:
            if safe_exists(p):
                if hasattr(os, "geteuid") and os.geteuid() != 0:
                    run_as_root(["remove-client-key", name], timeout=10)
                else:
                    p.unlink(missing_ok=True)
                logger.info("已清理: %s", p)
        except Exception as e:
            logger.warning("清理文件失败 %s: %s", p, e)

    # 复制 CRL 并设置 nobody 可读（OpenVPN 以 nobody 运行读 CRL）
    pki_crl = pki / "crl.pem"
    ovpn_crl = Path(config.CRL_FILE) if hasattr(config, "CRL_FILE") else config.OPENVPN_DIR / "crl.pem"
    if safe_exists(pki_crl):
        try:
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                run_as_root(["copy-crl"], timeout=15)
            else:
                import shutil
                shutil.copy2(str(pki_crl), str(ovpn_crl))
                try:
                    import pwd
                    nobody = pwd.getpwnam("nobody")
                    os.chown(str(ovpn_crl), nobody.pw_uid, nobody.pw_gid)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("复制 CRL 失败: %s", e)

    logger.info("已吊销客户端证书: %s", name)


def read_client_files(name: str) -> Tuple[str, str]:
    """
    读取客户端证书和私钥内容（组装 ovpn 用）。
    优先直接读；无权限时回退 root helper。
    """
    if not is_safe_path_component(name):
        raise EasyRSAError("客户端名称不合法")

    # 优先读客户端配置目录副本
    cert_candidates = [
        config.CLIENT_CERT_DIR / f"{name}.crt",
        config.EASYRSA_DIR / "pki" / "issued" / f"{name}.crt",
    ]
    key_candidates = [
        config.CLIENT_CERT_DIR / f"{name}.key",
        config.EASYRSA_DIR / "pki" / "private" / f"{name}.key",
    ]

    cert_raw, cert_src = _read_first_existing_ex(cert_candidates, "客户端证书文件不存在")
    key_raw, key_src = _read_first_existing_ex(key_candidates, "客户端私钥文件不存在")
    cert_pem = extract_pem_only(cert_raw, "cert")
    key_pem = extract_pem_only(key_raw, "key")
    logger.debug("读取客户端材料 %s: cert=%s key=%s", name, cert_src, key_src)
    return cert_pem, key_pem


def read_ca_cert() -> str:
    """读取 CA 证书内容。"""
    candidates = [
        config.EASYRSA_DIR / "pki" / "ca.crt",
        config.OPENVPN_DIR / "ca.crt",
    ]
    return _read_first_existing(candidates, "CA 证书文件不存在")


def read_tls_auth_key() -> str:
    """
    读取 tls-auth / tls-crypt 密钥（若存在）。
    不存在时返回空字符串，组装 ovpn 时跳过。
    优先 Nyr 的 tc.key（tls-crypt）。
    """
    candidates = []
    # tls-crypt（Nyr）
    if hasattr(config, "TLS_KEY_FILE"):
        candidates.append(Path(config.TLS_KEY_FILE))
    candidates.extend([
        config.OPENVPN_DIR / "tc.key",
        config.OPENVPN_DIR / "ta.key",
        config.EASYRSA_DIR / "pki" / "ta.key",
    ])
    for p in candidates:
        try:
            if not p or not safe_exists(p):
                continue
            path = Path(p)
            if os.access(str(path), os.R_OK):
                return path.read_text(encoding="utf-8")
            # tc.key/ta.key 通常是 root-only；不要因为降权后不可读而
            # 生成一个缺少 tls-crypt 的、无法连接的 ovpn。
            text = _read_via_helper(path)
            if text:
                return text
        except Exception as e:
            logger.debug("读取 TLS 密钥失败 %s: %s", p, e)
            continue
    return ""


def read_client_inline(name: str) -> Optional[str]:
    """
    读取 Nyr/EasyRSA 3.2 的内联客户端块：
      pki/inline/private/<name>.inline
    不存在时返回 None（调用方走传统证书组装）。
    """
    if not is_safe_path_component(name):
        raise EasyRSAError("客户端名称不合法")
    candidates = [
        config.EASYRSA_DIR / "pki" / "inline" / "private" / f"{name}.inline",
        config.CLIENT_CERT_DIR / f"{name}.inline",
    ]
    for p in candidates:
        try:
            path = Path(p)
            if safe_exists(path) and os.access(str(path), os.R_OK):
                return path.read_text(encoding="utf-8")
        except Exception:
            continue
    return None


def read_client_common() -> str:
    """
    读取 Nyr 的客户端模板 client-common.txt。
    """
    p = Path(config.CLIENT_COMMON_FILE) if hasattr(config, "CLIENT_COMMON_FILE") else config.OPENVPN_DIR / "client-common.txt"
    if safe_exists(p) and os.access(str(p), os.R_OK):
        return p.read_text(encoding="utf-8")
    raise EasyRSAError("客户端模板 client-common.txt 不存在")


def _read_first_existing_ex(paths: list, error_msg: str) -> Tuple[str, str]:
    """
    按顺序读取第一个可读文件，返回 (内容, 来源路径)。
    区分「不存在」和「存在但无权限」；无权限时尝试 root helper。
    """
    existed_but_denied = []
    for p in paths:
        try:
            path = Path(p)
            if not safe_exists(path):
                continue
            if os.access(str(path), os.R_OK):
                return path.read_text(encoding="utf-8"), str(path)
            existed_but_denied.append(str(path))
        except Exception as e:
            logger.warning("读取文件失败 %s: %s", p, e)
            continue

    # 权限不足：用 root helper 读
    for p in existed_but_denied:
        text = _read_via_helper(Path(p))
        if text is not None:
            return text, f"{p}(helper)"

    if existed_but_denied:
        raise EasyRSAError(
            error_msg.replace("不存在", "无权限读取"),
            detail="denied=" + ",".join(existed_but_denied),
        )
    raise EasyRSAError(error_msg)


def _read_via_helper(path: Path) -> Optional[str]:
    """通过 root helper 读取受保护文件；失败返回 None。"""
    try:
        # 只允许读 EasyRSA/OpenVPN 目录下的材料
        allowed_roots = (
            Path(config.EASYRSA_DIR).resolve(),
            Path(config.OPENVPN_DIR).resolve(),
            Path(config.CLIENT_CERT_DIR).resolve(),
        )
        resolved = path.resolve()
        if not any(str(resolved).startswith(str(root) + os.sep) or resolved == root for root in allowed_roots):
            logger.warning("helper 读取被拒绝（越界路径）: %s", resolved)
            return None
        result = run_as_root(["read-client-file", str(resolved)], timeout=15)
        if result.success:
            return result.stdout
        logger.warning("helper 读取失败 %s: %s", path, (result.stderr or "")[:200])
    except Exception as e:
        logger.warning("helper 读取异常 %s: %s", path, e)
    return None


def _read_first_existing(paths: list, error_msg: str) -> str:
    """按顺序读取第一个存在的文件。"""
    text, _src = _read_first_existing_ex(paths, error_msg)
    return text


def extract_pem_block(text: str, begin: str, end: str) -> str:
    """严格提取 PEM 块（BEGIN..END 各恰好一次）。失败返回空串。"""
    if not text or not begin or not end:
        return ""
    start = text.find(begin)
    if start < 0:
        return ""
    stop = text.find(end, start)
    if stop < 0:
        return ""
    block = text[start:stop + len(end)].strip()
    if block.count("-----BEGIN") != 1 or block.count("-----END") != 1:
        return ""
    return block


# 常见私钥 PEM 头（PKCS#8 / 传统 RSA / EC）
_KEY_PEM_MARKERS = (
    ("-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"),
    ("-----BEGIN RSA PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----"),
    ("-----BEGIN EC PRIVATE KEY-----", "-----END EC PRIVATE KEY-----"),
)
_CERT_PEM_MARKERS = (("-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"),)
_TLS_PEM_MARKERS = (
    ("-----BEGIN OpenVPN Static key V1-----", "-----END OpenVPN Static key V1-----"),
)


def extract_pem_only(text: str, kind: str = "cert") -> str:
    """
    从 EasyRSA/openssl 文本中只保留 PEM 块。
    kind: cert / key / tls / any
    issued/*.crt 常带 x509 文本说明，必须清洗后再拼进 ovpn。
    """
    if not text:
        return ""
    if kind == "cert":
        pairs = _CERT_PEM_MARKERS
    elif kind == "key":
        pairs = _KEY_PEM_MARKERS
    elif kind == "tls":
        pairs = _TLS_PEM_MARKERS
    else:
        return text.strip()
    for begin, end in pairs:
        block = extract_pem_block(text, begin, end)
        if block:
            return block
    return ""


# EasyRSA 服务器/CA 名称，扫描导入时排除
_RESERVED_NAMES = {"ca", "server", "ta", "dh", "crl"}


def list_existing_certs() -> list:
    """
    扫描 EasyRSA PKI 中已存在的客户端证书。
    返回: [{"name", "status", "serial", "has_key", "has_cert"}]
    status: active / revoked / expired

    数据来源：
    1. pki/index.txt（权威状态：V/R/E）
    2. pki/issued/*.crt（文件存在性交叉验证）

    任何探测失败只降级为空结果，不向上抛（否则前端会显示「导入失败」）。
    """
    results = {}
    pki = config.EASYRSA_DIR / "pki"

    # ---------- 1. 解析 index.txt ----------
    index_file = pki / "index.txt"
    if safe_exists(index_file):
        try:
            content = index_file.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                # 格式: 状态\t到期\t吊销日\t序列\t未知\t/CN=名称
                parts = line.split("\t")
                if len(parts) < 6:
                    continue
                flag = parts[0].strip().upper()  # V / R / E
                serial = parts[3].strip() if len(parts) > 3 else ""
                cn_part = parts[-1]
                # 提取 CN=xxx
                name = ""
                for seg in cn_part.split("/"):
                    if seg.startswith("CN="):
                        name = seg[3:].strip()
                        break
                if not name or name.lower() in _RESERVED_NAMES:
                    continue
                status_map = {"V": "active", "R": "revoked", "E": "expired"}
                status = status_map.get(flag, "active")
                results[name] = {
                    "name": name,
                    "status": status,
                    "serial": serial,
                    "has_key": False,
                    "has_cert": False,
                    "source": "index",
                }
        except Exception as e:
            logger.warning("解析 EasyRSA index.txt 失败: %s", e)

    # ---------- 2. 扫描 issued 目录 ----------
    issued_dir = pki / "issued"
    if safe_exists(issued_dir):
        try:
            for f in issued_dir.iterdir():
                if not safe_is_file(f) or f.suffix.lower() != ".crt":
                    continue
                name = f.stem
                if name.lower() in _RESERVED_NAMES:
                    continue
                if name not in results:
                    results[name] = {
                        "name": name,
                        "status": "active",
                        "serial": "",
                        "has_key": False,
                        "has_cert": True,
                        "source": "issued",
                    }
                else:
                    results[name]["has_cert"] = True
        except Exception as e:
            logger.warning("扫描 issued 目录失败: %s", e)

    # ---------- 3. 校验私钥是否存在 ----------
    private_dir = pki / "private"
    for name, info in results.items():
        try:
            if safe_exists(private_dir / f"{name}.key") or safe_exists(
                config.CLIENT_CERT_DIR / f"{name}.key"
            ):
                info["has_key"] = True
        except Exception as e:
            logger.warning("检查私钥是否存在失败 %s: %s", name, e)

    return list(results.values())

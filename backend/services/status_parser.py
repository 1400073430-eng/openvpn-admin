# -*- coding: utf-8 -*-
"""
openvpn-status.log 解析服务
容错要求：
- 文件不存在 → 返回空列表
- 文件为空 → 返回空列表
- 格式错乱 → 跳过坏行，不崩溃
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .. import config

logger = logging.getLogger("ovpn.status")

# status 日志允许的根目录（与 root helper read-client-file 白名单对齐）
_STATUS_ROOTS = (
    "/var/log/openvpn",
    "/run/openvpn-server",
    "/var/log/openvpn-server",
    "/etc/openvpn",
    "/etc/openvpn/server",
)


def _read_status_via_helper(path: Path):
    """服务用户无读权限时，经 root helper 读 status 日志。失败返回 None。"""
    try:
        from .cmd_runner import run_as_root
        resolved = str(Path(path).resolve())
        if not any(resolved == r or resolved.startswith(r + os.sep) for r in _STATUS_ROOTS):
            return None
        result = run_as_root(["read-client-file", resolved], timeout=10)
        if result.success:
            return result.stdout
    except Exception as e:
        logger.warning("helper 读取 status 失败: %s", e)
    return None


@dataclass
class OnlineClient:
    """一条在线会话记录"""
    common_name: str      # 客户端证书 CN（客户端名称）
    real_address: str     # 公网 IP:端口
    virtual_address: str  # 虚拟 IP
    virtual_ipv6: str     # 虚拟 IPv6
    bytes_received: int   # 上行流量（字节）
    bytes_sent: int       # 下行流量（字节）
    connected_since: str  # 连接时间字符串
    connected_since_ts: int  # 连接时间时间戳


def _candidate_status_paths() -> List[Path]:
    """候选 status 文件：配置路径优先，再常见 Ubuntu/Nyr 路径。"""
    ordered: List[Path] = []
    seen = set()

    def _add(p):
        try:
            key = str(Path(p))
        except Exception:
            return
        if key not in seen:
            seen.add(key)
            ordered.append(Path(p))

    try:
        if config.OPENVPN_STATUS_LOG:
            _add(config.OPENVPN_STATUS_LOG)
    except Exception:
        pass
    for p in (
        "/run/openvpn-server/status-server.log",
        "/var/log/openvpn/openvpn-status.log",
        "/run/openvpn-server/status.log",
        "/var/log/openvpn/status.log",
        "/etc/openvpn/openvpn-status.log",
    ):
        _add(p)
    return ordered


def _safe_exists_file(path: Path) -> bool:
    """exists() 在目录无执行权限时可能报 OSError；失败时也尝试 helper。"""
    try:
        return path.exists() and path.is_file()
    except OSError:
        return _read_status_via_helper(path) is not None


def _read_text_file(path: Path):
    """读文件；无权限/无法 stat 时走 helper。成功返回 str，失败返回 None。"""
    try:
        if os.access(str(path), os.R_OK):
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.debug("直接读 status 失败 %s: %s", path, e)
    content = _read_status_via_helper(path)
    if content is not None:
        return content
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("读取状态文件失败 %s: %s", path, e)
    return None


def _split_status_line(line: str) -> List[str]:
    """status-version 2 分隔符可能是 TAB 或逗号。"""
    if "\t" in line:
        return line.split("\t")
    return line.split(",")


def _parse_status_content(content: str) -> List[OnlineClient]:
    """按内容结构选择 v1/v2 解析器（不要求必须含 HEADER）。"""
    if not content or not content.strip():
        return []
    # v2：HEADER/CLIENT_LIST 记录行（TAB 或逗号）
    if (
        "HEADER" in content
        and "CLIENT_LIST" in content
        and ("\t" in content or "HEADER,CLIENT_LIST" in content or "CLIENT_LIST," in content)
    ):
        parsed = _parse_content_v2(content)
        if parsed:
            return parsed
    # v1：OpenVPN CLIENT LIST 段落
    return _parse_content(content)


def parse_status_log(log_path: Path = None) -> List[OnlineClient]:
    """
    解析 openvpn status 日志（兼容 status-version 1 与 2）。

    指定 log_path 时只解析该文件；否则在多个候选路径中找有客户端的那个。
    （Ubuntu 可能把 status 写到 /run/openvpn-server/status-server.log，
      而安装脚本配置的是 /var/log/openvpn/openvpn-status.log，导致页面空白。）
    """
    clients: List[OnlineClient] = []

    if log_path is not None:
        path = Path(log_path)
        try:
            if not path.exists():
                logger.info("状态文件不存在: %s", path)
                return clients
        except OSError as e:
            logger.warning("状态文件不可访问: %s (%s)", path, e)
            return clients
        content = _read_text_file(path)
        if content is None:
            logger.warning("状态文件无读权限: %s", path)
            return clients
        if not content.strip():
            logger.info("状态文件为空")
            return clients
        try:
            return _parse_status_content(content)
        except Exception as e:
            logger.warning("解析状态文件异常: %s", e)
            return clients

    # 未指定路径：按候选列表找真正含在线客户端的文件
    best: List[OnlineClient] = []
    checked = 0
    for path in _candidate_status_paths():
        if not _safe_exists_file(path):
            continue
        checked += 1
        content = _read_text_file(path)
        if content is None or not content.strip():
            continue
        try:
            parsed = _parse_status_content(content)
        except Exception as e:
            logger.warning("解析状态文件异常 %s: %s", path, e)
            continue
        if parsed:
            logger.debug("status 命中: %s (%s 条)", path, len(parsed))
            return parsed
        best = best or parsed

    if checked == 0:
        logger.info("未找到任何 status 文件（候选: %s）", ", ".join(str(p) for p in _candidate_status_paths()[:4]))
    return best


def _parse_content_v2(content: str) -> List[OnlineClient]:
    """解析 status-version 2（TAB 或逗号分隔，按 HEADER 列名定位字段）。"""
    clients: List[OnlineClient] = []
    routing: dict = {}
    rows: list = []
    # HEADER 行: HEADER, CLIENT_LIST, Common Name, Real Address, ...
    # 数据行:   CLIENT_LIST, cn, real, ...
    # 因此数据列 j 对应 HEADER 列 j+1
    col = {}  # name -> data index

    for raw in content.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        parts = _split_status_line(line)
        if not parts:
            continue

        if parts[0] == "HEADER":
            if len(parts) > 1 and parts[1] == "CLIENT_LIST":
                col = {}
                for i, name in enumerate(parts):
                    if i >= 2:
                        key = name.strip().lower().replace(" ", "_")
                        col[key] = i - 1
            continue
        if parts[0] in ("GLOBAL_STATS", "END", "TITLE", "TIME", "VERSION"):
            continue

        if parts[0] == "CLIENT_LIST":
            def idx(*names, default=None):
                for n in names:
                    if n in col:
                        j = col[n]
                        if 0 <= j < len(parts):
                            return parts[j]
                return default

            try:
                cn = idx("common_name") or (parts[1] if len(parts) > 1 else "")
                real = idx("real_address") or (parts[2] if len(parts) > 2 else "")
                vaddr = idx("virtual_address") or (parts[3] if len(parts) > 3 else "")
                recv_s = idx("bytes_received")
                sent_s = idx("bytes_sent")
                since = idx("connected_since") or (parts[7] if len(parts) > 7 else "")
                # OpenVPN 可能在 Common Name 列输出 UNDEF（无可用 CN），
                # 它不是客户端证书名称，不应出现在在线客户端列表/统计中。
                if _is_placeholder_common_name(cn):
                    logger.debug("忽略无效 Common Name 状态行: %r", cn)
                    continue
                # 无 HEADER 时按固定列（含空 VirtIPv6）
                if recv_s is None and len(parts) > 6:
                    recv_s = parts[5]
                    sent_s = parts[6]
                    since = since or (parts[7] if len(parts) > 7 else "")
                rows.append({
                    "cn": cn,
                    "real": real,
                    "vaddr": vaddr,
                    "recv": _safe_int(recv_s or 0),
                    "sent": _safe_int(sent_s or 0),
                    "since": since or "",
                })
            except Exception:
                continue
        elif parts[0] == "ROUTING_TABLE" and len(parts) >= 3:
            # ROUTING_TABLE VirtAddr CN RealAddr ...
            routing[parts[2]] = parts[1]

    for r in rows:
        clients.append(OnlineClient(
            common_name=r["cn"],
            real_address=r["real"],
            virtual_address=r.get("vaddr") or routing.get(r["cn"], ""),
            virtual_ipv6="",
            bytes_received=r["recv"],
            bytes_sent=r["sent"],
            connected_since=r["since"],
            connected_since_ts=_parse_time_ts(r["since"]) if r.get("since") else 0,
        ))
    return clients


def _parse_content(content: str) -> List[OnlineClient]:
    """解析 status 文件内容。"""
    clients: List[OnlineClient] = []
    section = ""  # 当前段落: client_list / routing / other
    routing_map = {}  # common_name -> virtual_address

    lines = content.splitlines()
    client_rows = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # 识别段落
        if line.startswith("OpenVPN CLIENT LIST"):
            section = "client_list"
            continue
        if line.startswith("ROUTING TABLE"):
            section = "routing"
            continue
        if line.startswith("GLOBAL STATS"):
            section = "global"
            continue
        if line.startswith("END"):
            section = ""
            continue

        # 跳过表头
        if line.startswith("Common Name") or line.startswith("Virtual Address") or line.startswith("Updated"):
            continue

        try:
            if section == "client_list":
                parts = [p.strip() for p in line.split(",")]
                # 期望: CN,RealAddr,BytesRecv,BytesSent,ConnectedSince
                if len(parts) >= 5:
                    client_rows.append(parts)
            elif section == "routing":
                parts = [p.strip() for p in line.split(",")]
                # 期望: VirtualAddr,CN,RealAddr,LastRef
                if len(parts) >= 2:
                    routing_map[parts[1]] = parts[0]
        except Exception:
            # 单行解析失败，跳过
            continue

    # 组装结果
    for parts in client_rows:
        try:
            cn = parts[0]
            if _is_placeholder_common_name(cn):
                logger.debug("忽略无效 Common Name 状态行: %r", cn)
                continue
            real_addr = parts[1]
            bytes_recv = _safe_int(parts[2])
            bytes_sent = _safe_int(parts[3])
            connected_since = ",".join(parts[4:]) if len(parts) > 5 else parts[4]

            client = OnlineClient(
                common_name=cn,
                real_address=real_addr,
                virtual_address=routing_map.get(cn, ""),
                virtual_ipv6="",
                bytes_received=bytes_recv,
                bytes_sent=bytes_sent,
                connected_since=connected_since,
                connected_since_ts=_parse_time_ts(connected_since),
            )
            clients.append(client)
        except Exception:
            continue

    return clients


def _is_placeholder_common_name(value: str) -> bool:
    """识别 OpenVPN 状态文件中的占位 CN，禁止将其显示为真实客户端。"""
    normalized = (value or "").strip().strip('"').upper()
    return normalized in {"", "UNDEF", "UNDEFINED", "N/A", "<UNDEF>"}


def _safe_int(s: str) -> int:
    """安全转 int。"""
    try:
        return int(s.strip())
    except Exception:
        return 0


def _parse_time_ts(time_str: str) -> int:
    """
    尝试解析时间字符串为时间戳。
    失败返回 0，不抛异常。
    """
    from datetime import datetime
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%a %b %d %H:%M:%S %Y",
    ]
    for fmt in formats:
        try:
            return int(datetime.strptime(time_str.strip(), fmt).timestamp())
        except Exception:
            continue
    return 0


def format_rate(bps: float) -> str:
    """格式化字节/秒速率；状态文件轮询得到的是近似平均速率。"""
    try:
        n = max(0.0, float(bps))
        if n < 1024:
            return f"{n:.0f} B/s"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB/s"
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.2f} MB/s"
        return f"{n / (1024 * 1024 * 1024):.2f} GB/s"
    except Exception:
        return "0 B/s"


def format_duration(seconds: int) -> str:
    try:
        s = max(0, int(seconds))
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        m, s = divmod(s, 60)
        if d:
            return f"{d}天 {h:02d}:{m:02d}:{s:02d}"
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return "00:00:00"


def format_bytes(n: int) -> str:
    """字节数人性化显示。"""
    try:
        n = int(n)
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MB"
        return f"{n / (1024 * 1024 * 1024):.2f} GB"
    except Exception:
        return "0 B"

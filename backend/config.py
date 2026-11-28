# -*- coding: utf-8 -*-
"""
全局配置模块
支持两种 OpenVPN 安装布局，自动识别：

1. Nyr/openvpn-install 布局（推荐，兼容用户已有安装）
   - 服务: openvpn-server@server
   - EasyRSA: /etc/openvpn/server/easy-rsa
   - 配置: /etc/openvpn/server/server.conf
   - 客户端模板: /etc/openvpn/server/client-common.txt
   - 证书 inline: easy-rsa/pki/inline/private/<name>.inline
   - TLS: tls-crypt (tc.key)

2. 本项目 install.sh 布局
   - EasyRSA: /etc/openvpn/easy-rsa
   - 配置: /etc/openvpn/server.conf
"""
import os
from pathlib import Path

# ==================== 项目根路径 ====================
BASE_DIR = Path(__file__).resolve().parent.parent


def safe_is_file(p: Path) -> bool:
    """Path.is_file 在无权限时抛 PermissionError（而非返回 False），探测必须吞掉。"""
    try:
        return Path(p).is_file()
    except OSError:
        return False


def safe_is_dir(p: Path) -> bool:
    try:
        return Path(p).is_dir()
    except OSError:
        return False


def safe_exists(p: Path) -> bool:
    try:
        return Path(p).exists()
    except OSError:
        return False


# 兼容旧内部名
_safe_is_file = safe_is_file
_safe_is_dir = safe_is_dir
_safe_exists = safe_exists

DATA_DIR = Path(os.environ.get("OVPM_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ==================== 数据库 ====================
DATABASE_PATH = DATA_DIR / "ovpn_admin.db"

# ==================== JWT ====================
JWT_SECRET = os.environ.get("OVPM_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("OVPM_JWT_EXPIRE", "30"))
AUTH_COOKIE_NAME = os.environ.get("OVPM_AUTH_COOKIE", "ovpn_session")
AUTH_COOKIE_SECURE = os.environ.get("OVPM_COOKIE_SECURE", "0") == "1"
AUTH_COOKIE_SAMESITE = os.environ.get("OVPM_COOKIE_SAMESITE", "strict")
AUTH_COOKIE_PATH = "/"

# ==================== 管理员默认账号 ====================
DEFAULT_ADMIN_USER = os.environ.get("OVPM_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("OVPM_ADMIN_PASSWORD", "")


# ==================== 自动探测 OpenVPN 安装布局 ====================
def detect_layout() -> dict:
    """
    探测服务器上 OpenVPN 的实际安装布局。
    返回路径字典；环境变量可强制覆盖。
    """
    nyr_root = Path("/etc/openvpn/server")
    classic_root = Path("/etc/openvpn")

    # Nyr 特征：server/easy-rsa/pki 或 server/client-common.txt
    is_nyr = (
        _safe_is_dir(nyr_root / "easy-rsa" / "pki")
        or _safe_is_file(nyr_root / "client-common.txt")
        or _safe_is_file(nyr_root / "server.conf")
    )

    if is_nyr:
        base = nyr_root
        layout = {
            "type": "nyr",
            "easyrsa_dir": base / "easy-rsa",
            "openvpn_dir": base,
            "openvpn_config": base / "server.conf",
            "status_log": Path(os.environ.get(
                "OVPM_STATUS_LOG",
                "/run/openvpn-server/status-server.log",
            )),
            "client_common": base / "client-common.txt",
            "client_cert_dir": base / "easy-rsa" / "pki" / "inline" / "private",
            "tls_key": base / "tc.key",
            "crl": base / "crl.pem",
            "service": "openvpn-server@server",
        }
    else:
        base = classic_root
        layout = {
            "type": "classic",
            "easyrsa_dir": base / "easy-rsa",
            "openvpn_dir": base,
            "openvpn_config": base / "server.conf",
            "status_log": Path(os.environ.get(
                "OVPM_STATUS_LOG",
                "/var/log/openvpn/openvpn-status.log",
            )),
            "client_common": base / "client-common.txt",
            "client_cert_dir": base / "client-configs",
            "tls_key": base / "ta.key",
            "crl": base / "crl.pem",
            "service": os.environ.get("OVPM_OPENVPN_SERVICE", "openvpn@server"),
        }

    # 环境变量覆盖（安装脚本写入）
    # 兼容别名：OVPM_TLS_CRYPT / OVPM_CRL_FILE（install.sh 历史写法）
    env_map = {
        "OVPM_EASYRSA_DIR": "easyrsa_dir",
        "OVPM_OPENVPN_DIR": "openvpn_dir",
        "OVPM_OVPN_CONFIG": "openvpn_config",
        "OVPM_STATUS_LOG": "status_log",
        "OVPM_CLIENT_CERT_DIR": "client_cert_dir",
        "OVPM_OPENVPN_SERVICE": "service",
        "OVPM_CLIENT_COMMON": "client_common",
        "OVPM_TLS_KEY": "tls_key",
        "OVPM_TLS_CRYPT": "tls_key",
        "OVPM_CRL": "crl",
        "OVPM_CRL_FILE": "crl",
    }
    seen_fields = set()
    for env_key, field in env_map.items():
        val = os.environ.get(env_key)
        if val and field not in seen_fields:
            layout[field] = val if field == "service" else Path(val)
            seen_fields.add(field)

    # 如果未由 .env 固定服务名，优先使用当前 active 的实例，避免 Nyr/classic 共存时盲选。
    if not os.environ.get("OVPM_OPENVPN_SERVICE"):
        try:
            import subprocess
            active_candidates = []
            for svc in ("openvpn-server@server", "openvpn@server", "openvpn"):
                r = subprocess.run(["systemctl", "show", svc, "-p", "ActiveState", "--value"], capture_output=True, text=True, timeout=3)
                if r.returncode == 0 and r.stdout.strip() == "active":
                    active_candidates.append(svc)
            if active_candidates:
                layout["service"] = active_candidates[0]
        except Exception:
            pass

    # status 路径智能探测（覆盖可能过时的 OVPM_STATUS_LOG）：
    # 优先「存在且 mtime 最新」的候选；Ubuntu 默认写 /run/openvpn-server/status-server.log，
    # 安装脚本却常写 /var/log/openvpn/openvpn-status.log（可能为空），导致在线列表空白。
    status_cands = [
        Path(layout["status_log"]),
        Path("/run/openvpn-server/status-server.log"),
        Path("/var/log/openvpn/openvpn-status.log"),
        Path("/run/openvpn-server/status.log"),
        Path("/var/log/openvpn/status.log"),
        Path("/etc/openvpn/openvpn-status.log"),
    ]
    best = None
    best_mtime = -1.0
    for cand in status_cands:
        if not _safe_exists(cand):
            continue
        try:
            mtime = float(cand.stat().st_mtime)
        except OSError:
            mtime = -1.0
        if mtime > best_mtime:
            best, best_mtime = cand, mtime
    if best is not None:
        layout["status_log"] = best

    # 覆盖 openvpn_dir / easyrsa_dir 后，同步仍指向旧 base 的相对默认值
    if os.environ.get("OVPM_OPENVPN_DIR"):
        base = Path(os.environ["OVPM_OPENVPN_DIR"])
        if not os.environ.get("OVPM_OVPN_CONFIG"):
            layout["openvpn_config"] = base / "server.conf"
        if not os.environ.get("OVPM_CLIENT_COMMON"):
            layout["client_common"] = base / "client-common.txt"
        if not (os.environ.get("OVPM_TLS_KEY") or os.environ.get("OVPM_TLS_CRYPT")):
            layout["tls_key"] = base / ("tc.key" if layout["type"] == "nyr" else "ta.key")
        if not (os.environ.get("OVPM_CRL") or os.environ.get("OVPM_CRL_FILE")):
            layout["crl"] = base / "crl.pem"
    if os.environ.get("OVPM_EASYRSA_DIR"):
        easy = Path(os.environ["OVPM_EASYRSA_DIR"])
        if not os.environ.get("OVPM_CLIENT_CERT_DIR") and layout["type"] == "nyr":
            layout["client_cert_dir"] = easy / "pki" / "inline" / "private"

    return layout


LAYOUT = detect_layout()

# ==================== OpenVPN / EasyRSA 路径 ====================
EASYRSA_DIR: Path = Path(LAYOUT["easyrsa_dir"])
OPENVPN_DIR: Path = Path(LAYOUT["openvpn_dir"])
OPENVPN_STATUS_LOG: Path = Path(LAYOUT["status_log"])
OPENVPN_CONFIG: Path = Path(LAYOUT["openvpn_config"])
CLIENT_CERT_DIR: Path = Path(LAYOUT["client_cert_dir"])
CLIENT_COMMON_FILE: Path = Path(LAYOUT["client_common"])
TLS_KEY_FILE: Path = Path(LAYOUT["tls_key"])
CRL_FILE: Path = Path(LAYOUT["crl"])
OPENVPN_SERVICE: str = LAYOUT["service"]

# EasyRSA 可执行文件
EASYRSA_BIN = Path(os.environ.get(
    "OVPM_EASYRSA_BIN",
    str(EASYRSA_DIR / "easyrsa"),
))

# 证书有效期（与 Nyr 脚本一致 3650 天）
EASYRSA_CERT_DAYS = int(os.environ.get("OVPM_CERT_DAYS", "3650"))

# ==================== 域名黑名单：nftables ====================
# 域名在服务器侧解析成 A/AAAA，最终由 nftables set 拦截。
BLACKLIST_DOMAIN_FILE = Path(os.environ.get(
    "OVPM_BLACKLIST_DOMAIN_FILE", str(DATA_DIR / "blacklist-domains.txt")
))
BLACKLIST_STATE_FILE = Path(os.environ.get(
    "OVPM_BLACKLIST_STATE_FILE", str(DATA_DIR / "blacklist-state.json")
))
NFT_TABLE = os.environ.get("OVPM_NFT_TABLE", "ovpn_admin")
NFT_SET_V4 = os.environ.get("OVPM_NFT_SET_V4", "blocked_ipv4")
NFT_SET_V6 = os.environ.get("OVPM_NFT_SET_V6", "blocked_ipv6")
NFT_BIN = os.environ.get("OVPM_NFT_BIN", "/usr/sbin/nft")
# HTTPS 默认关闭：没有证书时继续使用现有 HTTP；有证书后可通过 OVPM_HTTPS_ENABLED=1 开启。
HTTPS_ENABLED = os.environ.get("OVPM_HTTPS_ENABLED", "0") == "1"
HTTPS_PORT = int(os.environ.get("OVPM_HTTPS_PORT", "443"))

# ==================== 服务器网络 ====================
VPN_SUBNET = os.environ.get("OVPM_VPN_SUBNET", "10.8.0.0")

# 优先从 server.conf 读取端口/协议（Nyr 安装会写入）
def _read_server_conf_net() -> tuple:
    proto = os.environ.get("OVPM_VPN_PROTO", "udp")
    port = int(os.environ.get("OVPM_VPN_PORT", "1194"))
    try:
        conf = OPENVPN_CONFIG
        if _safe_exists(conf):
            for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("port ") and port == 1194:
                    try:
                        port = int(line.split()[1])
                    except Exception:
                        pass
                elif line.startswith("proto ") and proto == "udp":
                    p = line.split()[1].strip().lower()
                    if p in ("udp", "tcp", "udp4", "tcp4", "udp6", "tcp6"):
                        proto = p
                elif line.startswith("remote "):
                    pass
    except Exception:
        pass
    return proto, port


VPN_PROTO, VPN_PORT = _read_server_conf_net()


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if minimum <= value <= maximum else default


# OpenVPN transport tuning. The installer writes the same values to server and
# client templates; these values cover the backend fallback config generator.
OPENVPN_TUN_MTU = _bounded_int("OVPM_TUN_MTU", 1500, 1200, 2000)
OPENVPN_MSSFIX = _bounded_int("OVPM_MSSFIX", 1400, 900, 1900)
OPENVPN_SNDBUF = _bounded_int("OVPM_SNDBUF", 524288, 65536, 4194304)
OPENVPN_RCVBUF = _bounded_int("OVPM_RCVBUF", 524288, 65536, 4194304)
OPENVPN_KEEPALIVE_PING = _bounded_int("OVPM_KEEPALIVE_PING", 10, 5, 60)
OPENVPN_KEEPALIVE_TIMEOUT = _bounded_int("OVPM_KEEPALIVE_TIMEOUT", 120, 30, 600)
OPENVPN_KICK_BLOCK_SECONDS = _bounded_int("OVPM_KICK_BLOCK_SECONDS", 300, 30, 3600)

WEB_PORT = int(os.environ.get("OVPM_WEB_PORT", "80"))
WEB_HOST = os.environ.get("OVPM_WEB_HOST", "0.0.0.0")

# 可信反向代理（当前 IP 直连时留空）
TRUSTED_PROXY_IPS = {x.strip() for x in os.environ.get("OVPM_TRUSTED_PROXY_IPS", "").split(",") if x.strip()}

# Root helper：所有高权限系统操作必须经过这个固定程序
PRIV_HELPER = Path(os.environ.get("OVPM_PRIV_HELPER", "/usr/local/sbin/ovpn-admin-priv"))

# HTTPS 配置
SSL_CERT_FILE = Path(os.environ.get("OVPM_SSL_CERT", "/etc/openvpn/web/ssl/server.crt"))
SSL_KEY_FILE = Path(os.environ.get("OVPM_SSL_KEY", "/etc/openvpn/web/ssl/server.key"))

# ==================== 审计 ====================
AUDIT_LOG_FILE = DATA_DIR / "audit.log"

# ==================== 校验规则 ====================
CLIENT_NAME_PATTERN = r"^[a-zA-Z0-9_\-]{2,32}$"
DOMAIN_PATTERN = r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
PASSWORD_MIN_LEN = 8


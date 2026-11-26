# -*- coding: utf-8 -*-
"""
安全工具模块
- 密码哈希（Argon2id，兼容历史 PBKDF2）
- 参数校验（域名、客户端名、密码）
"""
import hashlib
import hmac
import re
from typing import Optional

from . import config

# 密码哈希：新密码使用 Argon2id；兼容旧 PBKDF2 并在登录成功后自动升级。
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
_ITERATIONS = 200_000
_SALT_LEN = 16


def hash_password(password: str) -> str:
    """使用 Argon2id 哈希密码。"""
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, stored: str) -> bool:
    """兼容 Argon2id 与历史 PBKDF2；验证失败返回 False。"""
    try:
        if stored.startswith("$argon2"):
            return _PASSWORD_HASHER.verify(stored, password)
        parts = stored.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk, expected)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError, TypeError):
        return False
    except Exception:
        return False


def needs_password_rehash(stored: str) -> bool:
    """判断是否需要升级为当前 Argon2id 参数。"""
    try:
        if stored.startswith("$argon2"):
            return _PASSWORD_HASHER.check_needs_rehash(stored)
        return stored.startswith("pbkdf2$")
    except Exception:
        return True




# ==================== 输入校验 ====================

def is_valid_domain(domain: str) -> bool:
    """
    校验域名格式。
    规则：字母数字开头结尾，可含中划线，至少两段，TLD 为 2+ 字母。
    """
    if not domain or not isinstance(domain, str):
        return False
    domain = domain.strip().lower()
    if len(domain) > 253:
        return False
    return bool(re.match(config.DOMAIN_PATTERN, domain))


def is_valid_client_name(name: str) -> bool:
    """
    校验 VPN 客户端名称。
    规则：字母数字下划线中划线，2-32 位。
    """
    if not name or not isinstance(name, str):
        return False
    return bool(re.match(config.CLIENT_NAME_PATTERN, name))


def is_valid_password(password: str) -> bool:
    """密码至少 8 位，且不含控制字符。"""
    if not password or not isinstance(password, str):
        return False
    if len(password) < config.PASSWORD_MIN_LEN or len(password) > 128:
        return False
    # 拒绝控制字符
    return not any(ord(c) < 32 for c in password)


def sanitize_remark(text: Optional[str], max_len: int = 200) -> str:
    """
    备注文本清洗：
    - 去掉控制字符
    - 截断到 max_len
    - 转义危险字符（防 XSS，前端也会转义）
    """
    if not text:
        return ""
    text = "".join(c for c in str(text) if ord(c) >= 32 or c == "\n")
    text = text[:max_len]
    return text.strip()


def is_safe_path_component(name: str) -> bool:
    """
    防路径穿越：文件名组件只允许安全字符。
    """
    if not name or not isinstance(name, str):
        return False
    # 禁止路径分隔符和 ..
    if "/" in name or "\\" in name or ".." in name:
        return False
    return bool(re.match(r"^[a-zA-Z0-9_\-]{1,64}$", name))


def validate_username(username: str) -> bool:
    """管理员用户名：3-32 位字母数字下划线。"""
    if not username or not isinstance(username, str):
        return False
    return bool(re.match(r"^[a-zA-Z0-9_]{3,32}$", username))


def is_valid_ipv4(ip: str) -> bool:
    """严格 IPv4 校验：四段十进制，每段 0-255，无前导零（除单个 0）。"""
    if not ip or not isinstance(ip, str):
        return False
    parts = ip.strip().split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or not (1 <= len(p) <= 3):
            return False
        if len(p) > 1 and p[0] == "0":
            return False
        if not (0 <= int(p) <= 255):
            return False
    return True


def is_valid_ip_port(addr: str) -> bool:
    """
    校验 IP:端口（IPv4）。
    用于强制踢下线等场景。
    """
    if not addr or not isinstance(addr, str):
        return False
    if ":" not in addr:
        return False
    ip, _, port = addr.rpartition(":")
    if not is_valid_ipv4(ip):
        return False
    return port.isdigit() and 1 <= int(port) <= 65535


# 前端页面白名单（防路径遍历 / 越权读文件）
ALLOWED_PAGES = frozenset({
    "index.html",
    "dashboard.html",
    "clients.html",
    "connections.html",
    "dns.html",
    "audit.html",
    "settings.html",
})


def is_allowed_page(name: str) -> bool:
    """页面名白名单校验。"""
    return name in ALLOWED_PAGES


# 简单登录失败计数（防暴力破解，进程内）
_LOGIN_FAILS: dict = {}  # (username, ip) -> [fail_count, lock_until_ts]
_LOGIN_LOCK_SECONDS = 300  # 锁定 5 分钟
_LOGIN_MAX_FAILS = 5
_LOGIN_FAILS_MAX_ENTRIES = 10000


def record_login_failure(username: str, ip: str = "") -> bool:
    """
    记录登录失败。返回 True 表示已触发锁定。
    """
    import time
    if not username:
        return False
    key = (username, ip or "-")
    now = time.time()
    entry = _LOGIN_FAILS.get(key)
    if entry and entry[1] > now:
        return True  # 仍处于锁定期
    if entry and 0 < entry[1] <= now:
        # 只重置已结束的锁定；未达到阈值时 lock_until 为 0，必须累加
        entry = [0, 0]

    count = (entry[0] if entry else 0) + 1
    if count >= _LOGIN_MAX_FAILS:
        _LOGIN_FAILS[key] = [count, now + _LOGIN_LOCK_SECONDS]
        return True
    _LOGIN_FAILS[key] = [count, 0]
    if len(_LOGIN_FAILS) > _LOGIN_FAILS_MAX_ENTRIES:
        for old_key, old_value in list(_LOGIN_FAILS.items()):
            if old_value[1] <= now:
                _LOGIN_FAILS.pop(old_key, None)
            if len(_LOGIN_FAILS) <= _LOGIN_FAILS_MAX_ENTRIES:
                break
        while len(_LOGIN_FAILS) > _LOGIN_FAILS_MAX_ENTRIES:
            _LOGIN_FAILS.pop(next(iter(_LOGIN_FAILS)))
    return False


def is_login_locked(username: str, ip: str = "") -> bool:
    """检查账号是否处于登录锁定期。"""
    import time
    entry = _LOGIN_FAILS.get((username, ip or "-"))
    return bool(entry and entry[1] > time.time())


def clear_login_failures(username: str, ip: str = "") -> None:
    """登录成功后清除指定 username + IP 的失败计数。"""
    _LOGIN_FAILS.pop((username, ip or "-"), None)

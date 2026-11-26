# -*- coding: utf-8 -*-
"""域名黑名单：服务器侧解析 + nftables IP set。"""
import json
import logging
import os
import socket
import tempfile
import threading
from pathlib import Path
from typing import Iterable

from .. import config
from ..security import is_valid_domain
from .cmd_runner import run_as_root

logger = logging.getLogger("ovpn.blacklist")
_SYNC_LOCK = threading.RLock()

class BlacklistError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail

def normalize_domain(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip(".")
    if not is_valid_domain(d):
        raise BlacklistError("域名格式不正确")
    return d

def resolve_domain(domain: str) -> dict:
    d = normalize_domain(domain)
    addresses = set()
    errors = []
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(d, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
            if sockaddr:
                addresses.add(sockaddr[0])
    except socket.gaierror as e:
        errors.append(str(e))
    desired = is_domain_blocked(d)
    enforced = False
    if desired and addresses:
        try:
            state = json.loads(config.BLACKLIST_STATE_FILE.read_text(encoding="utf-8"))
            item = (state.get("items") or {}).get(d) or {}
            enforced = bool(item.get("ok")) and any(ip in (item.get("addresses") or []) for ip in addresses)
        except Exception:
            enforced = False
    return {"domain": d, "addresses": sorted(addresses), "blocked": desired, "enforced": enforced, "errors": errors}


def _write_desired_domains(domains: Iterable[str]):
    valid = sorted({normalize_domain(d) for d in domains})
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(valid) + ("\n" if valid else "")
    fd, tmp_name = tempfile.mkstemp(prefix="blacklist-domains-", suffix=".tmp", dir=str(config.DATA_DIR))
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(config.BLACKLIST_DOMAIN_FILE)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return valid

def _read_domain_file_bytes():
    try:
        return config.BLACKLIST_DOMAIN_FILE.read_bytes() if config.BLACKLIST_DOMAIN_FILE.is_file() else None
    except OSError:
        return None

def _restore_domain_file(previous):
    if previous is None:
        try:
            config.BLACKLIST_DOMAIN_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return
    fd, tmp_name = tempfile.mkstemp(prefix="blacklist-domains-rollback-", suffix=".tmp", dir=str(config.DATA_DIR))
    try:
        with open(fd, "wb") as f:
            f.write(previous)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(config.BLACKLIST_DOMAIN_FILE)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass

def sync(domains: Iterable[str], timeout: int = 120) -> dict:
    # 串行化“期望域名文件 -> root nft 同步”，避免两个管理员请求互相覆盖。
    # root 同步失败时恢复旧文件，避免出现“第一次 500、第二次 400 已存在”的假状态。
    with _SYNC_LOCK:
        previous = _read_domain_file_bytes()
        valid = _write_desired_domains(domains)
        result = run_as_root(["blacklist-sync"], timeout=timeout)
        if not result.success:
            logger.error("nftables 黑名单同步失败: %s", result.stderr[:1000])
            try:
                _restore_domain_file(previous)
            except Exception as rollback_error:
                logger.error("黑名单域名文件回滚失败: %s", rollback_error)
            raise BlacklistError("黑名单同步失败，请检查 nftables 服务状态", result.stderr)
        try:
            state = json.loads(result.stdout.strip() or "{}")
        except Exception:
            state = {"raw": result.stdout[-4000:]}
        return {"domains": valid, **state}


def is_domain_blocked(domain: str) -> bool:
    d = normalize_domain(domain)
    try:
        if not config.BLACKLIST_DOMAIN_FILE.is_file():
            return False
        for line in config.BLACKLIST_DOMAIN_FILE.read_text(encoding="utf-8").splitlines():
            x = line.strip().lower()
            if x == d or d.endswith("." + x):
                return True
    except OSError:
        return False
    return False

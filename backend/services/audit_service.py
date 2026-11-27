# -*- coding: utf-8 -*-
"""
审计日志服务
- 记录管理员所有操作
- 写入 SQLite + 本地文件双备份
"""
import logging
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import List, Tuple

from .. import config
from ..database import DatabaseError, get_db

logger = logging.getLogger("ovpn.audit")


_file_lock = threading.Lock()
_cleanup_lock = threading.Lock()
_last_cleanup = 0.0
_last_database = None


def cleanup_records():
    """Retain 30 days/10,000 audit rows and remove expired/revoked sessions."""
    global _last_cleanup, _last_database
    with _cleanup_lock:
        now = time.monotonic()
        if _last_database == str(config.DATABASE_PATH) and now - _last_cleanup < 3600:
            return
        with get_db() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= datetime('now') OR revoked_at IS NOT NULL")
            conn.execute("DELETE FROM audit_logs WHERE created_at < datetime('now', '-30 days')")
            conn.execute("DELETE FROM audit_logs WHERE id NOT IN (SELECT id FROM audit_logs ORDER BY id DESC LIMIT 10000)")
        _last_cleanup, _last_database = now, str(config.DATABASE_PATH)


def log_action(username: str, action: str, detail: str = "", ip: str = "") -> None:
    """
    记录一条审计日志。
    写库失败不阻断业务，仅记本地日志。
    """
    try:
        # Prevent forged multiline entries in both database and file output.
        def clean(value, limit):
            return "".join(char if char.isprintable() else " " for char in str(value))[:limit]
        username = clean(username or "unknown", 32)
        action = clean(action or "unknown", 64)
        detail = clean(detail or "", 500)
        ip = clean(ip or "", 64)

        cleanup_records()
        with get_db() as conn:
            conn.execute(
                "INSERT INTO audit_logs (username, action, detail, ip) VALUES (?, ?, ?, ?)",
                (username, action, detail, ip),
            )
    except Exception as e:
        logger.error("写入审计日志失败: %s", e)

    # 同时追加到文件
    try:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] user={username} action={action} ip={ip} detail={detail}"
        with _file_lock:
            handler = RotatingFileHandler(config.AUDIT_LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
            try:
                handler.emit(logging.LogRecord("audit", logging.INFO, "", 0, line, (), None))
            finally:
                handler.close()
    except Exception:
        pass


def query_logs(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
) -> Tuple[List[dict], int]:
    """
    分页查询审计日志。
    返回: (logs_list, total)
    """
    try:
        offset = (page - 1) * page_size
        with get_db() as conn:
            if keyword:
                like = f"%{keyword}%"
                total_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM audit_logs WHERE username LIKE ? OR action LIKE ? OR detail LIKE ?",
                    (like, like, like),
                ).fetchone()
                rows = conn.execute(
                    """SELECT id, username, action, detail, ip, created_at 
                       FROM audit_logs 
                       WHERE username LIKE ? OR action LIKE ? OR detail LIKE ?
                       ORDER BY id DESC LIMIT ? OFFSET ?""",
                    (like, like, like, page_size, offset),
                ).fetchall()
            else:
                total_row = conn.execute("SELECT COUNT(*) AS c FROM audit_logs").fetchone()
                rows = conn.execute(
                    """SELECT id, username, action, detail, ip, created_at 
                       FROM audit_logs 
                       ORDER BY id DESC LIMIT ? OFFSET ?""",
                    (page_size, offset),
                ).fetchall()

            total = total_row["c"] if total_row else 0
            logs = [dict(r) for r in rows]
            return logs, total
    except DatabaseError:
        raise
    except Exception as e:
        logger.exception("查询审计日志失败: %s", e)
        raise DatabaseError("查询日志失败")

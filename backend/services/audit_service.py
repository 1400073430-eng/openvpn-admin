# -*- coding: utf-8 -*-
"""
审计日志服务
- 记录管理员所有操作
- 写入 SQLite + 本地文件双备份
"""
import logging
from typing import List, Tuple

from .. import config
from ..database import DatabaseError, get_db

logger = logging.getLogger("ovpn.audit")


def log_action(username: str, action: str, detail: str = "", ip: str = "") -> None:
    """
    记录一条审计日志。
    写库失败不阻断业务，仅记本地日志。
    """
    try:
        # 清洗
        username = (username or "unknown")[:32]
        action = (action or "unknown")[:64]
        detail = (detail or "")[:500]
        ip = (ip or "")[:64]

        with get_db() as conn:
            conn.execute(
                "INSERT INTO audit_logs (username, action, detail, ip) VALUES (?, ?, ?, ?)",
                (username, action, detail, ip),
            )
    except Exception as e:
        logger.error("写入审计日志失败: %s", e)

    # 同时追加到文件
    try:
        import time
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] user={username} action={action} ip={ip} detail={detail}\n"
        with open(config.AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
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

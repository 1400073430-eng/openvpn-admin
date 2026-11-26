# -*- coding: utf-8 -*-
"""
仪表盘路由：统计数据 + 最近日志
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user
from ..database import DatabaseError, get_db
from ..services.status_parser import parse_status_log

logger = logging.getLogger("ovpn.router.dashboard")
router = APIRouter(prefix="/api/dashboard", tags=["仪表盘"])


@router.get("/stats")
async def get_stats(username: str = Depends(get_current_user)):
    """
    获取仪表盘统计数据。
    - VPN 总用户数
    - 在线客户端数量
    - DNS 黑名单总数
    """
    try:
        with get_db() as conn:
            user_row = conn.execute("SELECT COUNT(*) AS c FROM vpn_clients").fetchone()
            dns_row = conn.execute("SELECT COUNT(*) AS c FROM dns_blacklist").fetchone()
            dns_enabled = conn.execute(
                "SELECT COUNT(*) AS c FROM dns_blacklist WHERE enabled = 1"
            ).fetchone()
            revoked_row = conn.execute(
                "SELECT COUNT(*) AS c FROM vpn_clients WHERE status = 'revoked'"
            ).fetchone()

        # 在线数量
        try:
            online_clients = parse_status_log()
            online_count = len(online_clients)
        except Exception:
            online_count = 0

        return {
            "code": 200,
            "message": "ok",
            "data": {
                "total_users": user_row["c"] if user_row else 0,
                "online_clients": online_count,
                "dns_blacklist_total": dns_row["c"] if dns_row else 0,
                "dns_blacklist_enabled": dns_enabled["c"] if dns_enabled else 0,
                "revoked_clients": revoked_row["c"] if revoked_row else 0,
            },
        }

    except DatabaseError as e:
        logger.error("仪表盘统计数据库异常: %s", e)
        raise HTTPException(status_code=500, detail="获取统计数据失败")
    except Exception as e:
        logger.exception("仪表盘统计异常: %s", e)
        raise HTTPException(status_code=500, detail="获取统计数据失败")


@router.get("/recent-logs")
async def get_recent_logs(
    limit: int = 10,
    username: str = Depends(get_current_user),
):
    """获取最近操作日志（默认 10 条）。"""
    try:
        limit = max(1, min(limit, 50))
        with get_db() as conn:
            rows = conn.execute(
                """SELECT username, action, detail, ip, created_at 
                   FROM audit_logs ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()

        logs = [dict(r) for r in rows]
        return {"code": 200, "message": "ok", "data": logs}

    except DatabaseError as e:
        logger.error("获取日志数据库异常: %s", e)
        raise HTTPException(status_code=500, detail="获取日志失败")
    except Exception as e:
        logger.exception("获取日志异常: %s", e)
        raise HTTPException(status_code=500, detail="获取日志失败")

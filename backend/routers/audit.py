# -*- coding: utf-8 -*-
"""
审计日志路由
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_current_user
from ..database import DatabaseError
from ..services.audit_service import query_logs

logger = logging.getLogger("ovpn.router.audit")
router = APIRouter(prefix="/api/audit", tags=["审计日志"])


@router.get("")
async def list_logs(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str = Query("", max_length=64),
    username: str = Depends(get_current_user),
):
    """分页查询审计日志。"""
    try:
        logs, total = query_logs(page=page, page_size=page_size, keyword=keyword)
        return {
            "code": 200,
            "message": "ok",
            "data": {
                "list": logs,
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }

    except DatabaseError as e:
        logger.error("审计日志查询失败: %s", e)
        raise HTTPException(status_code=500, detail="查询日志失败")
    except Exception as e:
        logger.exception("审计日志异常: %s", e)
        raise HTTPException(status_code=500, detail="查询日志失败")

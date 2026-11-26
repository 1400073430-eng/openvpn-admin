# -*- coding: utf-8 -*-
"""
在线连接监控路由
- 解析 openvpn-status.log
- 强制踢下线
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import get_client_ip, get_current_user
from ..services.audit_service import log_action
from ..services.openvpn_service import OpenVPNError, kick_client
from ..services.status_parser import format_bytes, format_rate, format_duration, parse_status_log
from ..services.connection_tracker import tracker
from ..database import get_db

logger = logging.getLogger("ovpn.router.connections")
router = APIRouter(prefix="/api/connections", tags=["在线连接"])


class KickRequest(BaseModel):
    real_address: str = Field(..., min_length=3, max_length=64)


@router.get("")
async def list_connections(username: str = Depends(get_current_user)):
    """
    获取在线客户端列表。
    解析失败/无数据时返回空列表，页面提示"暂无在线客户端"。
    """
    try:
        clients = parse_status_log()
        data = []
        for c in clients:
            key = __import__("hashlib").sha256("|".join([c.common_name, c.real_address, c.connected_since]).encode("utf-8")).hexdigest()
            spd = tracker.speed_for(key)
            duration = 0
            if c.connected_since_ts:
                import time
                duration = max(0, int(time.time()) - c.connected_since_ts)
            data.append({
                "common_name": c.common_name,
                "real_address": c.real_address,
                "virtual_address": c.virtual_address,
                "connected_since": c.connected_since,
                "bytes_received": c.bytes_received,
                "bytes_sent": c.bytes_sent,
                "bytes_received_text": format_bytes(c.bytes_received),
                "bytes_sent_text": format_bytes(c.bytes_sent),
                "connected_duration_seconds": duration,
                "connected_duration_text": format_duration(duration),
                "upload_speed_bps": round(spd.get("upload_bps", 0.0), 1),
                "download_speed_bps": round(spd.get("download_bps", 0.0), 1),
                "upload_speed_text": format_rate(spd.get("upload_bps", 0.0)),
                "download_speed_text": format_rate(spd.get("download_bps", 0.0)),
            })
        return {"code": 200, "message": "ok", "data": data}

    except Exception as e:
        logger.exception("获取在线连接异常: %s", e)
        # 容错：不崩溃，返回空列表
        return {"code": 200, "message": "ok", "data": []}


@router.get("/history")
async def connection_history(limit: int = 100, username: str = Depends(get_current_user)):
    """返回最近 VPN 会话历史；每条记录对应一次 OpenVPN 连接。"""
    limit = max(1, min(int(limit), 500))
    try:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT common_name, real_address, virtual_address, connected_at, disconnected_at,
                          bytes_received_start, bytes_received_end, bytes_sent_start, bytes_sent_end, status
                   FROM vpn_sessions ORDER BY connected_at DESC LIMIT ?""", (limit,)
            ).fetchall()
        import datetime as _dt
        data=[]
        now=_dt.datetime.now(_dt.timezone.utc)
        for r in rows:
            start = r["connected_at"]
            end = r["disconnected_at"]
            duration = 0
            try:
                st=_dt.datetime.strptime(start, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc)
                et=_dt.datetime.strptime(end, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc) if end else now
                duration=max(0,int((et-st).total_seconds()))
            except Exception:
                pass
            recv=max(0,int(r["bytes_received_end"])-int(r["bytes_received_start"]))
            sent=max(0,int(r["bytes_sent_end"])-int(r["bytes_sent_start"]))
            data.append({
                "common_name": r["common_name"], "real_address": r["real_address"],
                "virtual_address": r["virtual_address"], "connected_at": r["connected_at"],
                "disconnected_at": r["disconnected_at"], "status": r["status"],
                "duration_seconds": duration, "duration_text": format_duration(duration),
                "bytes_received": recv, "bytes_sent": sent,
                "bytes_received_text": format_bytes(recv), "bytes_sent_text": format_bytes(sent),
            })
        return {"code":200,"message":"ok","data":data}
    except Exception as e:
        logger.exception("获取连接历史异常: %s", e)
        raise HTTPException(status_code=500, detail="获取连接历史失败")


@router.post("/kick")
async def kick(
    req: KickRequest,
    request: Request,
    username: str = Depends(get_current_user),
):
    """强制将客户端踢下线。"""
    try:
        success = kick_client(req.real_address)
        if success:
            log_action(username, "强制下线", f"地址={req.real_address}", get_client_ip(request))
            return {"code": 200, "message": "已强制下线", "data": None}
        else:
            raise HTTPException(status_code=500, detail="强制下线失败")

    except OpenVPNError as e:
        log_action(username, "强制下线失败", f"{req.real_address}: {e.message}", get_client_ip(request))
        raise HTTPException(status_code=500, detail=e.message)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("强制下线异常: %s", e)
        raise HTTPException(status_code=500, detail="操作失败")

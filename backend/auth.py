# -*- coding: utf-8 -*-
"""认证与服务端会话模块。"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from . import config
from .database import get_db

logger = logging.getLogger("ovpn.auth")


def create_access_token(username: str, expires_minutes: Optional[int] = None) -> str:
    """生成带 jti 的短期 JWT；服务端 auth_sessions 决定其是否仍然有效。"""
    expire = timedelta(minutes=expires_minutes or config.JWT_EXPIRE_MINUTES)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + expire,
        "jti": secrets.token_urlsafe(24),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    """解析 Token，返回用户名；无效/过期返回 None。"""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        username = payload.get("sub")
        return username if username else None
    except JWTError as e:
        logger.warning("JWT 校验失败: %s", e)
        return None
    except Exception as e:
        logger.exception("JWT 解析异常: %s", e)
        return None


def decode_token_claims(token: str) -> Optional[dict]:
    """返回已验证 JWT claims；仅内部认证使用。"""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        if not payload.get("sub") or not payload.get("jti"):
            return None
        return payload
    except Exception:
        return None


def _session_active(jti: str, username: str) -> bool:
    """检查服务端会话是否存在、未吊销且未过期。"""
    with get_db() as conn:
        row = conn.execute(
            """SELECT 1 FROM auth_sessions
               WHERE jti = ? AND username = ? AND revoked_at IS NULL
                 AND datetime(expires_at) > datetime('now')""",
            (jti, username),
        ).fetchone()
    return bool(row)


async def get_current_user(request: Request) -> str:
    """仅接受 HttpOnly Cookie；服务端会话可主动吊销。"""
    token = request.cookies.get(config.AUTH_COOKIE_NAME, "")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录，请先登录",
        )

    claims = decode_token_claims(token)
    if not claims:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")

    username = claims["sub"]
    jti = claims["jti"]
    try:
        active = _session_active(jti, username)
    except Exception:
        logger.exception("会话状态查询失败")
        raise HTTPException(status_code=500, detail="认证服务暂时不可用")
    if not active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录")

    request.state.auth_jti = jti
    request.state.auth_username = username
    return username


def get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（仅信任配置的反向代理）。"""
    try:
        peer = request.client.host if request.client else ""
        if peer in config.TRUSTED_PROXY_IPS:
            xff = request.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[0].strip()
        return peer
    except Exception:
        return ""

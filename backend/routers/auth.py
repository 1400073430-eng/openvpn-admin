# -*- coding: utf-8 -*-
"""认证路由：HttpOnly Cookie 登录、注销、修改密码。"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import config
from ..services import login_guard
from ..auth import create_access_token, decode_token_claims, get_client_ip, get_current_user
from ..database import DatabaseError, get_db
from ..models import ChangePasswordRequest, ChangeUsernameRequest, LoginRequest, LoginResponse
from ..security import (
    clear_login_failures,
    hash_password,
    is_login_locked,
    needs_password_rehash,
    record_login_failure,
    verify_password,
)
from ..services.audit_service import log_action

logger = logging.getLogger("ovpn.router.auth")
router = APIRouter(prefix="/api/auth", tags=["认证"])


def _set_auth_cookie(response: Response, token: str, expires_seconds: int) -> None:
    response.set_cookie(
        key=config.AUTH_COOKIE_NAME,
        value=token,
        max_age=expires_seconds,
        httponly=True,
        secure=config.AUTH_COOKIE_SECURE,
        samesite=config.AUTH_COOKIE_SAMESITE,
        path=config.AUTH_COOKIE_PATH,
    )


def _revoke_session(jti: str) -> None:
    if not jti:
        return
    with get_db() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = datetime('now') WHERE jti = ? AND revoked_at IS NULL",
            (jti,),
        )


@router.get("/captcha")
@login_guard.bounded_work
def captcha(request: Request):
    import base64
    ident, png = login_guard.issue(request.client.host if request.client else "")
    return Response(content=__import__("json").dumps({"id": ident, "image": "data:image/png;base64," + base64.b64encode(png).decode()}), media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/login", response_model=LoginResponse)
@login_guard.bounded_work
def login(req: LoginRequest, request: Request, response: Response):
    """登录成功后签发短期 JWT，并写入 HttpOnly Cookie。"""
    ip = request.client.host if request.client else ""
    login_guard.throttle("login", ip, 10, 60)
    login_guard.verify(req.captcha_id, req.captcha_answer, ip)
    login_guard.throttle("account", req.username, 8, 60)
    login_guard.throttle("password-global", "all", 30, 60)
    try:
        if is_login_locked(req.username, ip):
            log_action(req.username, "登录拒绝", "账号锁定中", ip)
            raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")

        with get_db() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash FROM admins WHERE username = ?",
                (req.username,),
            ).fetchone()

            if not row or not verify_password(req.password, row["password_hash"]):
                record_login_failure(req.username, ip)
                log_action(req.username, "登录失败", "密码错误或用户不存在", ip)
                raise HTTPException(status_code=401, detail="用户名或密码错误")

        verified_hash = row["password_hash"]
        upgraded_hash = hash_password(req.password) if needs_password_rehash(verified_hash) else verified_hash

        clear_login_failures(req.username, ip)
        token = create_access_token(row["username"])
        claims = decode_token_claims(token)
        if not claims:
            raise HTTPException(status_code=500, detail="无法创建登录会话")

        exp = claims["exp"]
        if isinstance(exp, (int, float)):
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
            expires_seconds = max(1, int(exp - datetime.now(timezone.utc).timestamp()))
        else:
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.JWT_EXPIRE_MINUTES)
            expires_seconds = config.JWT_EXPIRE_MINUTES * 60

        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT username, password_hash FROM admins WHERE id = ?", (row["id"],)).fetchone()
            if not current or current["username"] != row["username"] or current["password_hash"] != verified_hash:
                raise HTTPException(status_code=401, detail="账号凭据已变更，请重新登录")
            if upgraded_hash != verified_hash:
                conn.execute("UPDATE admins SET password_hash = ?, updated_at = datetime('now') WHERE id = ?", (upgraded_hash, row["id"]))
            conn.execute(
                "INSERT INTO auth_sessions (jti, username, expires_at) VALUES (?, ?, ?)",
                (claims["jti"], row["username"], expires_at.strftime("%Y-%m-%d %H:%M:%S")),
            )

        _set_auth_cookie(response, token, expires_seconds)
        log_action(row["username"], "登录成功", "HttpOnly Cookie 会话", ip)
        return LoginResponse(username=row["username"], expires_in=expires_seconds)

    except HTTPException:
        raise
    except DatabaseError as e:
        logger.error("登录时数据库异常: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="登录服务暂时不可用")
    except Exception as e:
        logger.exception("登录异常: %s", e)
        raise HTTPException(status_code=500, detail="登录失败，请稍后重试")


@router.post("/logout")
def logout(request: Request, response: Response, username: str = Depends(get_current_user)):
    """主动吊销当前会话并删除 Cookie。"""
    try:
        _revoke_session(getattr(request.state, "auth_jti", ""))
        response.delete_cookie(config.AUTH_COOKIE_NAME, path=config.AUTH_COOKIE_PATH)
        log_action(username, "退出登录", "会话已吊销", get_client_ip(request))
        return {"code": 200, "message": "已退出登录", "data": None}
    except DatabaseError:
        raise HTTPException(status_code=500, detail="退出登录失败")


@router.get("/me")
def get_me(username: str = Depends(get_current_user)):
    return {"code": 200, "message": "ok", "data": {"username": username}}


@router.post("/change-username")
@login_guard.bounded_work
def change_username(req: ChangeUsernameRequest, request: Request, response: Response, username: str = Depends(get_current_user)):
    """Rename the current admin and revoke all existing sessions atomically."""
    try:
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id, password_hash FROM admins WHERE username = ?", (username,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="用户不存在")
            if not verify_password(req.current_password, row["password_hash"]):
                raise HTTPException(status_code=400, detail="当前密码错误")
            if req.new_username == username:
                raise HTTPException(status_code=400, detail="新用户名不能与当前用户名相同")
            if conn.execute("SELECT 1 FROM admins WHERE username = ?", (req.new_username,)).fetchone():
                raise HTTPException(status_code=409, detail="用户名已存在")
            conn.execute("UPDATE admins SET username = ?, updated_at = datetime('now') WHERE id = ?", (req.new_username, row["id"]))
            conn.execute("UPDATE auth_sessions SET revoked_at = datetime('now') WHERE username = ? AND revoked_at IS NULL", (username,))

        response.delete_cookie(config.AUTH_COOKIE_NAME, path=config.AUTH_COOKIE_PATH)
        log_action(req.new_username, "修改管理员用户名", "原用户名=" + username + "；全部旧会话已吊销", get_client_ip(request))
        return {"code": 200, "message": "用户名修改成功，请重新登录", "data": {"username": req.new_username}}
    except HTTPException:
        raise
    except DatabaseError as exc:
        if "约束" in str(exc):
            raise HTTPException(status_code=409, detail="用户名已存在")
        raise HTTPException(status_code=500, detail="服务暂时不可用")
    except Exception:
        logger.exception("修改用户名异常")
        raise HTTPException(status_code=500, detail="修改用户名失败")


@router.post("/change-password")
@login_guard.bounded_work
def change_password(req: ChangePasswordRequest, request: Request, response: Response, username: str = Depends(get_current_user)):
    """修改密码并吊销该账号全部旧会话，使旧 Token 立即失效。"""
    try:
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id, password_hash FROM admins WHERE username = ?",
                (username,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="用户不存在")
            if not verify_password(req.old_password, row["password_hash"]):
                raise HTTPException(status_code=400, detail="旧密码错误")
            if req.old_password == req.new_password:
                raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")

            conn.execute(
                "UPDATE admins SET password_hash = ?, updated_at = datetime('now') WHERE id = ?",
                (hash_password(req.new_password), row["id"]),
            )
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = datetime('now') WHERE username = ? AND revoked_at IS NULL",
                (username,),
            )

        response.delete_cookie(config.AUTH_COOKIE_NAME, path=config.AUTH_COOKIE_PATH)
        log_action(username, "修改密码", "成功，全部旧会话已吊销", get_client_ip(request))
        return {"code": 200, "message": "密码修改成功，请重新登录", "data": None}
    except HTTPException:
        raise
    except DatabaseError:
        raise HTTPException(status_code=500, detail="服务暂时不可用")
    except Exception:
        logger.exception("修改密码异常")
        raise HTTPException(status_code=500, detail="修改密码失败")

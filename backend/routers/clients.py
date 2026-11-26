# -*- coding: utf-8 -*-
"""
VPN 客户端管理路由
- 列表/搜索/分页
- 新增（调用 EasyRSA）
- 下载 ovpn
- 吊销证书
- 编辑备注 / 删除
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .. import config
from ..config import safe_exists
from ..auth import get_client_ip, get_current_user
from ..database import DatabaseError, get_db
from ..models import ClientCreateRequest, ClientUpdateRequest
from ..services import easyrsa_service, openvpn_service
from ..services.audit_service import log_action
from ..services.easyrsa_service import EasyRSAError
from ..services.openvpn_service import OpenVPNError

logger = logging.getLogger("ovpn.router.clients")
router = APIRouter(prefix="/api/clients", tags=["VPN客户端"])


@router.get("")
async def list_clients(
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str = Query("", max_length=64),
    username: str = Depends(get_current_user),
):
    """客户端列表（分页 + 搜索）。"""
    try:
        offset = (page - 1) * page_size
        with get_db() as conn:
            if keyword:
                like = f"%{keyword}%"
                total_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM vpn_clients WHERE name LIKE ? OR remark LIKE ?",
                    (like, like),
                ).fetchone()
                rows = conn.execute(
                    """SELECT id, name, remark, status, cert_serial, created_at, updated_at, revoked_at
                       FROM vpn_clients WHERE name LIKE ? OR remark LIKE ?
                       ORDER BY id DESC LIMIT ? OFFSET ?""",
                    (like, like, page_size, offset),
                ).fetchall()
            else:
                total_row = conn.execute("SELECT COUNT(*) AS c FROM vpn_clients").fetchone()
                rows = conn.execute(
                    """SELECT id, name, remark, status, cert_serial, created_at, updated_at, revoked_at
                       FROM vpn_clients ORDER BY id DESC LIMIT ? OFFSET ?""",
                    (page_size, offset),
                ).fetchall()

        return {
            "code": 200,
            "message": "ok",
            "data": {
                "list": [dict(r) for r in rows],
                "total": total_row["c"] if total_row else 0,
                "page": page,
                "page_size": page_size,
            },
        }

    except DatabaseError as e:
        logger.error("客户端列表查询失败: %s", e)
        raise HTTPException(status_code=500, detail="获取客户端列表失败")
    except Exception as e:
        logger.exception("客户端列表异常: %s", e)
        raise HTTPException(status_code=500, detail="获取客户端列表失败")


@router.post("")
async def create_client(
    req: ClientCreateRequest,
    request: Request,
    username: str = Depends(get_current_user),
):
    """
    新增 VPN 客户端：
    1. 检查名称唯一
    2. EasyRSA 生成证书
    3. 写入数据库
    """
    try:
        # 检查是否已存在（数据库 + EasyRSA）
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM vpn_clients WHERE name = ?", (req.name,)
            ).fetchone()
            if existing:
                raise HTTPException(status_code=400, detail="客户端名称已存在")

        try:
            for c in easyrsa_service.list_existing_certs():
                if c.get("name") == req.name and c.get("status") != "revoked":
                    raise HTTPException(
                        status_code=400,
                        detail="客户端证书已存在（EasyRSA），请先吊销或更换名称",
                    )
        except HTTPException:
            raise
        except Exception:
            pass  # 扫描失败不阻断，交给签发阶段报错

        # 生成证书
        try:
            easyrsa_service.generate_client_cert(req.name, nopass=True)
        except EasyRSAError as e:
            log_action(username, "创建客户端失败", f"{req.name}: {e.message}", get_client_ip(request))
            raise HTTPException(status_code=500, detail=e.message)

        # 写入数据库；失败则清理孤儿证书，避免残留私钥
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO vpn_clients (name, remark, status) VALUES (?, ?, 'active')",
                    (req.name, req.remark),
                )
        except Exception:
            # 尽力清理客户端证书副本
            for ext in ("crt", "key"):
                try:
                    p = config.CLIENT_CERT_DIR / f"{req.name}.{ext}"
                    if safe_exists(p):
                        p.unlink()
                except Exception:
                    pass
            raise

        log_action(username, "创建VPN客户端", f"名称={req.name} 备注={req.remark}", get_client_ip(request))
        return {"code": 200, "message": "客户端创建成功", "data": {"name": req.name}}

    except HTTPException:
        raise
    except DatabaseError as e:
        logger.error("创建客户端数据库异常: %s", e)
        raise HTTPException(status_code=500, detail="创建客户端失败")
    except Exception as e:
        logger.exception("创建客户端异常: %s", e)
        raise HTTPException(status_code=500, detail="创建客户端失败")


@router.get("/{name}/ovpn")
async def download_ovpn(
    name: str,
    request: Request,
    username: str = Depends(get_current_user),
):
    """下载客户端 .ovpn 配置文件。"""
    try:
        # 校验名称
        from ..security import is_valid_client_name
        if not is_valid_client_name(name):
            raise HTTPException(status_code=400, detail="客户端名称格式不正确")

        # 检查是否在数据库且未吊销
        with get_db() as conn:
            row = conn.execute(
                "SELECT name, status FROM vpn_clients WHERE name = ?", (name,)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="客户端不存在")
        if row["status"] == "revoked":
            raise HTTPException(status_code=400, detail="证书已吊销，无法下载")

        # 生成 ovpn
        try:
            content = openvpn_service.build_ovpn_config(name)
        except OpenVPNError as e:
            log_action(username, "下载ovpn失败", f"{name}: {e.message}", get_client_ip(request))
            raise HTTPException(status_code=500, detail=e.message)

        log_action(username, "下载ovpn配置", f"名称={name}", get_client_ip(request))

        return Response(
            content=content,
            media_type="application/x-openvpn-profile",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.ovpn"'
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("下载 ovpn 异常: %s", e)
        raise HTTPException(status_code=500, detail="下载配置文件失败")


@router.post("/{name}/revoke")
async def revoke_client(
    name: str,
    request: Request,
    username: str = Depends(get_current_user),
):
    """吊销客户端证书（禁用 VPN 账号）。"""
    try:
        from ..security import is_valid_client_name
        if not is_valid_client_name(name):
            raise HTTPException(status_code=400, detail="客户端名称格式不正确")

        with get_db() as conn:
            row = conn.execute(
                "SELECT id, status FROM vpn_clients WHERE name = ?", (name,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="客户端不存在")
            if row["status"] == "revoked":
                raise HTTPException(status_code=400, detail="证书已吊销")

        # 吊销证书
        try:
            easyrsa_service.revoke_client_cert(name)
        except EasyRSAError as e:
            log_action(username, "吊销证书失败", f"{name}: {e.message}", get_client_ip(request))
            raise HTTPException(status_code=500, detail=e.message)

        # 更新状态
        with get_db() as conn:
            conn.execute(
                "UPDATE vpn_clients SET status='revoked', revoked_at=datetime('now'), updated_at=datetime('now') WHERE name=?",
                (name,),
            )

        log_action(username, "吊销客户端证书", f"名称={name}", get_client_ip(request))
        return {"code": 200, "message": "证书已吊销", "data": None}

    except HTTPException:
        raise
    except DatabaseError as e:
        logger.error("吊销证书数据库异常: %s", e)
        raise HTTPException(status_code=500, detail="操作失败")
    except Exception as e:
        logger.exception("吊销证书异常: %s", e)
        raise HTTPException(status_code=500, detail="吊销证书失败")


@router.put("/{name}")
async def update_client(
    name: str,
    req: ClientUpdateRequest,
    request: Request,
    username: str = Depends(get_current_user),
):
    """编辑客户端备注。"""
    try:
        from ..security import is_valid_client_name
        if not is_valid_client_name(name):
            raise HTTPException(status_code=400, detail="客户端名称格式不正确")

        with get_db() as conn:
            row = conn.execute("SELECT id FROM vpn_clients WHERE name = ?", (name,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="客户端不存在")

            conn.execute(
                "UPDATE vpn_clients SET remark = ?, updated_at = datetime('now') WHERE name = ?",
                (req.remark, name),
            )

        log_action(username, "编辑客户端备注", f"名称={name} 备注={req.remark}", get_client_ip(request))
        return {"code": 200, "message": "更新成功", "data": None}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("编辑客户端异常: %s", e)
        raise HTTPException(status_code=500, detail="更新失败")


@router.delete("/{name}")
async def delete_client(
    name: str,
    request: Request,
    username: str = Depends(get_current_user),
):
    """删除客户端记录（仅删数据库，证书文件保留以便审计）。"""
    try:
        from ..security import is_valid_client_name
        if not is_valid_client_name(name):
            raise HTTPException(status_code=400, detail="客户端名称格式不正确")

        with get_db() as conn:
            row = conn.execute("SELECT id FROM vpn_clients WHERE name = ?", (name,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="客户端不存在")
            conn.execute("DELETE FROM vpn_clients WHERE name = ?", (name,))

        log_action(username, "删除客户端", f"名称={name}", get_client_ip(request))
        return {"code": 200, "message": "客户端已删除", "data": None}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除客户端异常: %s", e)
        raise HTTPException(status_code=500, detail="删除失败")

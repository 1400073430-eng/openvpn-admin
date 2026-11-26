# -*- coding: utf-8 -*-
"""
EasyRSA 已有证书扫描 / 导入路由

说明：
- 独立挂在 /api/easyrsa 下，路径不含 {name} 参数，
  不会与 /api/clients/{name} 路由冲突，避免 405 Method Not Allowed。
- 全部接口 JWT 鉴权。
- 仅扫描 EasyRSA PKI，不接受用户指定路径，无路径穿越风险。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import get_client_ip, get_current_user
from ..database import DatabaseError, get_db
from ..security import is_valid_client_name
from ..services import easyrsa_service
from ..services.audit_service import log_action

logger = logging.getLogger("ovpn.router.easyrsa_sync")
router = APIRouter(prefix="/api/easyrsa", tags=["证书同步"])


@router.get("/scan")
async def scan_certs(username: str = Depends(get_current_user)):
    """
    扫描服务器 EasyRSA 中的客户端证书，并标注是否已纳入平台。
    """
    try:
        certs = easyrsa_service.list_existing_certs()
        with get_db() as conn:
            known = {
                r["name"]
                for r in conn.execute("SELECT name FROM vpn_clients").fetchall()
            }
        for c in certs:
            c["imported"] = c["name"] in known
        missing = [c for c in certs if not c["imported"]]
        return {
            "code": 200,
            "message": "ok",
            "data": {
                "all": certs,
                "missing": missing,
                "missing_count": len(missing),
            },
        }
    except Exception as e:
        logger.exception("扫描 EasyRSA 证书失败: %s", e)
        raise HTTPException(status_code=500, detail="扫描服务器证书失败，请检查 EasyRSA 路径配置")


@router.post("/import")
async def import_certs(
    request: Request,
    username: str = Depends(get_current_user),
):
    """
    将 EasyRSA 已有、数据库中不存在的客户端导入平台。
    已存在的记录跳过，不覆盖备注。
    """
    try:
        imported, skipped, failed = _do_import()
        log_action(
            username,
            "导入EasyRSA已有客户端",
            f"成功={len(imported)} 跳过={len(skipped)} 失败={len(failed)}",
            get_client_ip(request),
        )
        return {
            "code": 200,
            "message": f"导入完成：新增 {len(imported)}，跳过 {len(skipped)}，失败 {len(failed)}",
            "data": {
                "imported": imported,
                "skipped": skipped,
                "failed": failed,
            },
        }
    except DatabaseError as e:
        logger.error("导入客户端数据库异常: %s", e)
        raise HTTPException(status_code=500, detail="导入失败，请稍后重试")
    except PermissionError as e:
        logger.error("导入 EasyRSA 客户端权限不足: %s", e)
        raise HTTPException(status_code=500, detail="导入失败：无权限读取 EasyRSA 证书目录")
    except Exception as e:
        logger.exception("导入 EasyRSA 客户端异常: %s", e)
        raise HTTPException(status_code=500, detail="导入失败")


def _do_import():
    """
    执行导入（供路由与列表自动同步共用）。
    返回 (imported, skipped, failed)
    """
    certs = easyrsa_service.list_existing_certs()
    imported, skipped, failed = [], [], []

    with get_db() as conn:
        for c in certs:
            name = c.get("name") or ""
            if not name:
                continue
            if not is_valid_client_name(name):
                failed.append(name)
                continue

            existing = conn.execute(
                "SELECT id FROM vpn_clients WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                skipped.append(name)
                continue

            status = "revoked" if c.get("status") == "revoked" else "active"
            try:
                conn.execute(
                    "INSERT INTO vpn_clients (name, remark, status, cert_serial) VALUES (?, ?, ?, ?)",
                    (name, "从服务器 EasyRSA 导入", status, c.get("serial", "")),
                )
                imported.append(name)
            except Exception:
                logger.exception("导入单条失败: %s", name)
                failed.append(name)

    return imported, skipped, failed


@router.post("/auto-import")
async def auto_import(
    request: Request,
    username: str = Depends(get_current_user),
):
    """
    安静导入：仅在「平台列表为空且服务器有证书」时执行。
    用于客户端列表页自动补齐，避免用户手动点同步。
    """
    try:
        with get_db() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM vpn_clients").fetchone()
        if total and total["c"] > 0:
            return {
                "code": 200,
                "message": "ok",
                "data": {"imported": [], "skipped": [], "failed": [], "auto": False},
            }

        imported, skipped, failed = _do_import()
        if imported:
            log_action(
                username,
                "自动导入EasyRSA客户端",
                f"平台列表为空，自动导入 {len(imported)} 个",
                get_client_ip(request),
            )
        return {
            "code": 200,
            "message": "ok",
            "data": {
                "imported": imported,
                "skipped": skipped,
                "failed": failed,
                "auto": True,
            },
        }
    except Exception as e:
        logger.exception("自动导入异常: %s", e)
        # 自动导入失败不阻断列表
        return {"code": 200, "message": "ok", "data": {"imported": [], "auto": False}}

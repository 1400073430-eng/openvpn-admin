# -*- coding: utf-8 -*-
"""
系统设置路由
- 基础路径配置（只读展示 + 可更新部分项）
- 服务状态
- HTTP / HTTPS 当前配置
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config
from ..auth import get_client_ip, get_current_user
from ..database import get_db
from ..services.audit_service import log_action
from ..services.cmd_runner import run_command

logger = logging.getLogger("ovpn.router.settings")
router = APIRouter(prefix="/api/settings", tags=["系统设置"])


class SettingsUpdateRequest(BaseModel):
    """可动态修改的设置项（白名单，防止任意路径写入）。"""
    server_ip: Optional[str] = Field(None, max_length=64)
    vpn_port: Optional[int] = Field(None, ge=1, le=65535)


@router.get("")
async def get_settings(username: str = Depends(get_current_user)):
    """获取系统配置与路径信息（只读展示）。"""
    try:
        settings = {}
        with get_db() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            for r in rows:
                settings[r["key"]] = r["value"]

        https_enabled = bool(config.HTTPS_ENABLED and config.safe_exists(config.SSL_CERT_FILE) and config.safe_exists(config.SSL_KEY_FILE))
        data = {
            "paths": {
                "easyrsa_dir": str(config.EASYRSA_DIR),
                "openvpn_dir": str(config.OPENVPN_DIR),
                "status_log": str(config.OPENVPN_STATUS_LOG),
                "blacklist_domains": str(config.BLACKLIST_DOMAIN_FILE),
                "database": str(config.DATABASE_PATH),
                "client_cert_dir": str(config.CLIENT_CERT_DIR),
            },
            "network": {
                "server_ip": settings.get("server_ip", ""),
                "vpn_port": settings.get("vpn_port", str(config.VPN_PORT)),
                "vpn_proto": config.VPN_PROTO,
                "vpn_subnet": config.VPN_SUBNET,
                "web_port": config.WEB_PORT,
            },
            "services": _get_service_status(),
            "https": {
                "enabled": https_enabled,
                "cert_file": str(config.SSL_CERT_FILE),
                "key_file": str(config.SSL_KEY_FILE),
                "port": config.HTTPS_PORT,
                "note": ("已配置 HTTPS，证书或端口更改后需重启 Web 服务。" if https_enabled else "当前配置为 HTTP；配置可读证书和私钥并设置 OVPM_HTTPS_ENABLED=1，重启 Web 服务后启用 HTTPS。"),
            },
        }
        return {"code": 200, "message": "ok", "data": data}

    except Exception as e:
        logger.exception("获取设置异常: %s", e)
        raise HTTPException(status_code=500, detail="获取配置失败")


@router.put("")
async def update_settings(
    req: SettingsUpdateRequest,
    request: Request,
    username: str = Depends(get_current_user),
):
    """更新基础网络配置。"""
    try:
        updates = {}
        if req.server_ip is not None:
            from ..security import is_valid_ipv4
            if not is_valid_ipv4(req.server_ip):
                raise HTTPException(status_code=400, detail="IP 地址格式不正确")
            updates["server_ip"] = req.server_ip
        if req.vpn_port is not None:
            updates["vpn_port"] = str(req.vpn_port)

        if not updates:
            raise HTTPException(status_code=400, detail="无有效更新项")

        with get_db() as conn:
            for k, v in updates.items():
                conn.execute(
                    """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')""",
                    (k, v),
                )

        log_action(username, "更新系统设置", str(updates), get_client_ip(request))
        return {"code": 200, "message": "设置已更新", "data": updates}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("更新设置异常: %s", e)
        raise HTTPException(status_code=500, detail="更新失败")


def _get_service_status() -> dict:
    """查询 OpenVPN 与 nftables 黑名单同步服务状态。"""
    status = {}
    for name, service in [("openvpn", config.OPENVPN_SERVICE), ("blacklist", "openvpn-admin-blacklist.timer")]:
        try:
            result = run_command(["systemctl", "is-active", service], timeout=5)
            status[name] = {
                "service": service,
                "active": result.stdout.strip() == "active",
                "state": result.stdout.strip() or "unknown",
            }
        except Exception:
            status[name] = {"service": service, "active": False, "state": "unknown"}
    return status


@router.get("/ipv6")
def get_ipv6_status(username: str = Depends(get_current_user)):
    """Read-only, bounded IPv6 capability detection; never mutates networking."""
    from ..services.ipv6_upgrade import probe, render_config
    status = probe()
    status["vpn_config_readable"] = False
    status["upgrade_supported"] = False
    try:
        text = config.OPENVPN_CONFIG.read_text(encoding="utf-8")
        status["vpn_config_readable"] = True
        status["managed_dualstack"] = "# BEGIN OVPM DUALSTACK" in text
        render_config(text)
        status["upgrade_supported"] = status["ready"] and not status["managed_dualstack"]
        status["vpn_note"] = ("已写入双栈配置；请从客户端验证出口和黑名单。" if status["managed_dualstack"] else "可生成升级脚本；执行前将再次检查防火墙及配置兼容性。")
    except (OSError, ValueError) as exc:
        status["vpn_note"] = "配置不可读或需人工合并：" + str(exc)[:200]
    return {"code": 200, "message": "ok", "data": status}


@router.post("/ipv6/upgrade-script")
def download_ipv6_script(request: Request, username: str = Depends(get_current_user)):
    """Download only; executing the script is an explicit SSH administrator action."""
    from ..services import ipv6_upgrade
    from pathlib import Path
    log_action(username, "下载双栈升级脚本", "未更改服务器网络配置", get_client_ip(request))
    return {"code": 200, "message": "脚本仅下载，尚未应用", "data": {
        "filename": "ovpn-dualstack-upgrade.py",
        "script": Path(ipv6_upgrade.__file__).read_text(encoding="utf-8")}}


@router.post("/node-check")
def check_node(username: str = Depends(get_current_user)):
    """Manually query fixed third-party services; no network mutation."""
    from ..services.node_check import check
    return {"code": 200, "message": "ok", "data": check()}

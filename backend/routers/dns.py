# -*- coding: utf-8 -*-
"""域名黑名单 API：服务器侧 DNS 解析 + nftables IP set。"""
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from ..auth import get_client_ip, get_current_user
from ..database import get_db
from ..models import DnsAddRequest, DnsBatchAddRequest, DnsResolveRequest, DnsUpdateRequest
from ..security import is_valid_domain
from ..services.audit_service import log_action
from ..services.blacklist_service import BlacklistError, resolve_domain, sync

logger = logging.getLogger("ovpn.router.dns")
router = APIRouter(prefix="/api/dns", tags=["域名黑名单"])

def _load_enabled_domains():
    with get_db() as conn:
        rows = conn.execute("SELECT domain FROM dns_blacklist WHERE enabled = 1 ORDER BY domain").fetchall()
    return [r["domain"] for r in rows]

def _sync(username, ip):
    return sync(_load_enabled_domains())

@router.get("")
async def list_domains(page:int=Query(1,ge=1,le=10000), page_size:int=Query(10,ge=1,le=100), keyword:str=Query("",max_length=64), username:str=Depends(get_current_user)):
    try:
        offset=(page-1)*page_size
        with get_db() as conn:
            if keyword:
                like=f"%{keyword}%"
                total=conn.execute("SELECT COUNT(*) AS c FROM dns_blacklist WHERE domain LIKE ? OR remark LIKE ?",(like,like)).fetchone()["c"]
                rows=conn.execute("SELECT id,domain,enabled,remark,created_at,updated_at FROM dns_blacklist WHERE domain LIKE ? OR remark LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",(like,like,page_size,offset)).fetchall()
            else:
                total=conn.execute("SELECT COUNT(*) AS c FROM dns_blacklist").fetchone()["c"]
                rows=conn.execute("SELECT id,domain,enabled,remark,created_at,updated_at FROM dns_blacklist ORDER BY id DESC LIMIT ? OFFSET ?",(page_size,offset)).fetchall()
        return {"code":200,"message":"ok","data":{"list":[dict(r) for r in rows],"total":total,"page":page,"page_size":page_size}}
    except Exception as e:
        logger.exception("黑名单列表异常: %s",e); raise HTTPException(500,"获取列表失败")

@router.post("")
async def add_domain(req:DnsAddRequest, request:Request, username:str=Depends(get_current_user)):
    try:
        with get_db() as conn:
            if conn.execute("SELECT id FROM dns_blacklist WHERE domain=?",(req.domain,)).fetchone():
                raise HTTPException(400,"域名已存在")
            conn.execute("INSERT INTO dns_blacklist(domain,enabled,remark) VALUES(?,1,?)",(req.domain,req.remark))
        try:
            state=_sync(username,get_client_ip(request))
        except BlacklistError:
            with get_db() as conn: conn.execute("DELETE FROM dns_blacklist WHERE domain=?",(req.domain,))
            try: _sync(username,get_client_ip(request))
            except Exception: logger.exception("回滚后重新同步黑名单失败")
            raise
        log_action(username,"添加域名黑名单",f"域名={req.domain}",get_client_ip(request))
        return {"code":200,"message":"添加成功","data":{"domain":req.domain,"sync":state}}
    except HTTPException: raise
    except BlacklistError as e: raise HTTPException(500,e.message)
    except Exception as e:
        logger.exception("添加黑名单异常: %s",e); raise HTTPException(500,"添加失败")

@router.post("/batch")
async def batch_add(req:DnsBatchAddRequest, request:Request, username:str=Depends(get_current_user)):
    try:
        raw=[x for x in re.split(r"[\s,;]+",req.domains.strip()) if x]
        if len(raw) > 200:
            raise HTTPException(400,"单次最多导入 200 个域名")
        valid=[]; invalid=[]; duplicates=[]
        with get_db() as conn:
            for item in raw:
                d=item.strip().lower()
                if not d: continue
                if not is_valid_domain(d): invalid.append(d); continue
                if conn.execute("SELECT id FROM dns_blacklist WHERE domain=?",(d,)).fetchone(): duplicates.append(d); continue
                conn.execute("INSERT INTO dns_blacklist(domain,enabled,remark) VALUES(?,1,'')",(d,)); valid.append(d)
        try:
            state=_sync(username,get_client_ip(request)) if valid else {}
        except BlacklistError:
            with get_db() as conn:
                for d in valid: conn.execute("DELETE FROM dns_blacklist WHERE domain=?",(d,))
            try: _sync(username,get_client_ip(request))
            except Exception: logger.exception("批量回滚后重新同步黑名单失败")
            raise
        log_action(username,"批量导入域名黑名单",f"成功={len(valid)} 重复={len(duplicates)} 无效={len(invalid)}",get_client_ip(request))
        return {"code":200,"message":f"导入完成：成功 {len(valid)}，重复 {len(duplicates)}，无效 {len(invalid)}","data":{"success":valid,"duplicates":duplicates,"invalid":invalid,"sync":state}}
    except HTTPException: raise
    except BlacklistError as e: raise HTTPException(500,e.message)
    except Exception as e:
        logger.exception("批量导入异常: %s",e); raise HTTPException(500,"批量导入失败")

@router.put("/{domain_id}")
async def toggle_domain(domain_id:int, req:DnsUpdateRequest, request:Request, username:str=Depends(get_current_user)):
    try:
        with get_db() as conn:
            row=conn.execute("SELECT id,domain,enabled FROM dns_blacklist WHERE id=?",(domain_id,)).fetchone()
            if not row: raise HTTPException(404,"域名不存在")
            conn.execute("UPDATE dns_blacklist SET enabled=?,updated_at=datetime('now') WHERE id=?",(1 if req.enabled else 0,domain_id))
        try: _sync(username,get_client_ip(request))
        except BlacklistError:
            with get_db() as conn: conn.execute("UPDATE dns_blacklist SET enabled=?,updated_at=datetime('now') WHERE id=?",(row["enabled"],domain_id))
            try: _sync(username,get_client_ip(request))
            except Exception: logger.exception("状态回滚后重新同步黑名单失败")
            raise
        action="启用" if req.enabled else "禁用"; log_action(username,f"{action}域名黑名单",f"域名={row['domain']}",get_client_ip(request))
        return {"code":200,"message":f"已{action}","data":None}
    except HTTPException: raise
    except BlacklistError as e: raise HTTPException(500,e.message)
    except Exception as e:
        logger.exception("切换黑名单异常: %s",e); raise HTTPException(500,"操作失败")

@router.delete("/{domain_id}")
async def delete_domain(domain_id:int, request:Request, username:str=Depends(get_current_user)):
    try:
        with get_db() as conn:
            row=conn.execute("SELECT id,domain,enabled,remark,created_at,updated_at FROM dns_blacklist WHERE id=?",(domain_id,)).fetchone()
            if not row: raise HTTPException(404,"域名不存在")
            conn.execute("DELETE FROM dns_blacklist WHERE id=?",(domain_id,))
        try: _sync(username,get_client_ip(request))
        except BlacklistError:
            with get_db() as conn: conn.execute("INSERT OR IGNORE INTO dns_blacklist(id,domain,enabled,remark,created_at,updated_at) VALUES(?,?,?,?,?,?)",(row["id"],row["domain"],row["enabled"],row["remark"],row["created_at"],row["updated_at"]))
            try: _sync(username,get_client_ip(request))
            except Exception: logger.exception("删除回滚后重新同步黑名单失败")
            raise
        log_action(username,"删除域名黑名单",f"域名={row['domain']}",get_client_ip(request))
        return {"code":200,"message":"删除成功","data":None}
    except HTTPException: raise
    except BlacklistError as e: raise HTTPException(500,e.message)
    except Exception as e:
        logger.exception("删除黑名单异常: %s",e); raise HTTPException(500,"删除失败")

@router.post("/resolve")
async def test_resolve(req:DnsResolveRequest, request:Request, username:str=Depends(get_current_user)):
    try:
        result=resolve_domain(req.domain); log_action(username,"域名解析测试",f"域名={req.domain}",get_client_ip(request)); return {"code":200,"message":"ok","data":result}
    except BlacklistError as e: raise HTTPException(400,e.message)
    except Exception as e:
        logger.exception("解析测试异常: %s",e); raise HTTPException(500,"解析测试失败")

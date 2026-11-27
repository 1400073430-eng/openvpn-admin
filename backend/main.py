# -*- coding: utf-8 -*-
"""
OpenVPN 管理平台 - FastAPI 主入口

启动方式:
    uvicorn backend.main:app --host 0.0.0.0 --port 80

"""
import logging
from logging.handlers import RotatingFileHandler
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .config import safe_exists, safe_is_file
from .auth import get_current_user
from .database import DatabaseError, init_database
from .routers import audit, auth, clients, connections, dashboard, dns, easyrsa_sync, settings
from .services.connection_tracker import tracker
from .services import vpn_access
from .security import is_allowed_page

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(config.DATA_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
    ],
)
logger = logging.getLogger("ovpn.main")


# ==================== 生命周期 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭钩子。"""
    logger.info("=" * 50)
    logger.info("OpenVPN 管理平台启动中...")
    logger.info("=" * 50)

    vpn_access.NETWORKS = vpn_access.load_networks()
    if not vpn_access.NETWORKS:
        raise RuntimeError("无法确定管理访问 VPN 网段，请设置 OVPM_ADMIN_VPN_CIDRS，例如 10.8.0.0/24")

    # 启动前安全检查：生产环境必须显式提供随机 JWT 密钥和管理员密码
    if len(config.JWT_SECRET) < 32:
        raise RuntimeError("OVPM_JWT_SECRET 未配置或长度不足 32 字节，拒绝启动")
    if not config.DEFAULT_ADMIN_PASSWORD:
        raise RuntimeError("OVPM_ADMIN_PASSWORD 未配置，拒绝启动")

    try:
        init_database()
        logger.info("数据库初始化完成: %s", config.DATABASE_PATH)
    except Exception as e:
        logger.exception("数据库初始化失败: %s", e)
        raise

    await tracker.start()
    yield
    await tracker.stop()
    logger.info("OpenVPN 管理平台已关闭")


# ==================== 创建应用 ====================
app = FastAPI(
    title="OpenVPN 管理平台",
    description="OpenVPN 客户端、连接、域名黑名单与网络设置管理",
    version="4.3.10",
    lifespan=lifespan,
    docs_url="/api/docs",      # Swagger 文档（可按需关闭）
    redoc_url=None,
)

# CORS：内网管理台，收紧为可配置；默认允许常见本机/内网访问头
# 生产环境应通过 OVPM_ALLOWED_ORIGINS 限定，避免 "*" + credentials
_cors_origins_env = __import__("os").environ.get("OVPM_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    peer = request.client.host if request.client else ""
    if not vpn_access.allowed(peer, request.url.path):
        return JSONResponse(status_code=403, content={"detail": "管理平台仅允许通过 VPN 访问，请先连接 VPN，并使用服务器 VPN 内网地址"}, headers={"Cache-Control": "no-store"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # 当前允许 HTTP/IP 直连，因此暂不启用 HSTS；未来切换 HTTPS 后再打开。
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    if request.url.path == "/pages/settings.html":
        response.headers["Content-Security-Policy"] += "; connect-src 'self' https://api.ipify.org https://api6.ipify.org"
    return response


# ==================== 全局异常处理 ====================
@app.exception_handler(DatabaseError)
async def db_error_handler(request: Request, exc: DatabaseError):
    """数据库异常 → 友好提示，不暴露堆栈。"""
    logger.error("数据库错误: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器繁忙，请稍后重试", "data": None},
    )


@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    """兜底异常处理：绝不向前端返回原始堆栈。"""
    logger.exception("未捕获异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误，请联系管理员", "data": None},
    )


# ==================== 注册路由 ====================
# 注意：easyrsa_sync 必须注册；路径为 /api/easyrsa/*，与 /api/clients/{name} 无冲突
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(easyrsa_sync.router)
app.include_router(clients.router)
app.include_router(connections.router)
app.include_router(dns.router)
app.include_router(audit.router)
app.include_router(settings.router)


# ==================== 自测接口 ====================
@app.post("/api/selftest")
async def run_selftest(username: str = Depends(get_current_user)):
    """
    运行内置自测（需 JWT 登录）。
    供部署后快速验证平台功能。
    """
    from .selftest import run_all_tests

    try:
        summary = run_all_tests()
        return {"code": 200, "message": "自测完成", "data": summary}
    except Exception as e:
        logger.exception("自测运行异常: %s", e)
        raise HTTPException(status_code=500, detail="自测执行失败")


# ==================== 健康检查 ====================
@app.get("/api/health")
async def health():
    """健康检查（无需登录，供部署脚本检测）。"""
    return {"code": 200, "message": "ok", "data": {"status": "running"}}


# ==================== 静态文件与前端 ====================
# 前端目录
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

if safe_exists(FRONTEND_DIR):
    # 挂载静态资源
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def index():
        """首页 → 登录页。"""
        index_file = FRONTEND_DIR / "index.html"
        if safe_exists(index_file):
            return FileResponse(str(index_file))
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "前端文件未找到", "data": None},
        )

    @app.get("/pages/{page_name}")
    async def serve_page(page_name: str):
        """服务前端页面（pages/ 子目录）。"""
        # 防路径穿越 + 白名单双重校验
        if "/" in page_name or "\\" in page_name or ".." in page_name:
            return JSONResponse(status_code=400, content={"code": 400, "message": "非法路径", "data": None})
        if not page_name.endswith(".html"):
            page_name += ".html"
        # 仅允许白名单页面，防止读取任意文件
        if not is_allowed_page(page_name):
            return JSONResponse(status_code=404, content={"code": 404, "message": "页面不存在", "data": None})
        page_file = FRONTEND_DIR / "pages" / page_name
        if safe_exists(page_file) and safe_is_file(page_file):
            return FileResponse(str(page_file))
        return JSONResponse(status_code=404, content={"code": 404, "message": "页面不存在", "data": None})


# ==================== HTTPS 启动配置 ====================
def create_ssl_context():
    """
    从配置读取 HTTPS 启动参数。
    当 SSL 证书文件存在时，可使用 uvicorn 的 ssl 参数启动：
        uvicorn backend.main:app --ssl-certfile=... --ssl-keyfile=...
    或调用本函数获取 ssl 参数字典。
    """
    if config.HTTPS_ENABLED and safe_exists(config.SSL_CERT_FILE) and safe_exists(config.SSL_KEY_FILE):
        return {
            "ssl_certfile": str(config.SSL_CERT_FILE),
            "ssl_keyfile": str(config.SSL_KEY_FILE),
        }
    return None


if __name__ == "__main__":
    import uvicorn

    ssl_args = create_ssl_context()
    if ssl_args:
        logger.info("检测到 SSL 证书，以 HTTPS 模式启动")
        uvicorn.run(
            "backend.main:app",
            proxy_headers=False,
            host=config.WEB_HOST,
            port=config.HTTPS_PORT,
            **ssl_args,
        )
    else:
        logger.info("以 HTTP 模式启动（可挂载证书切换 HTTPS）")
        uvicorn.run(
            "backend.main:app",
            proxy_headers=False,
            host=config.WEB_HOST,
            port=config.WEB_PORT,
        )

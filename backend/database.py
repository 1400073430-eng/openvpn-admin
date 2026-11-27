# -*- coding: utf-8 -*-
"""
SQLite 数据库模块
- 建表 / 初始化管理员
- 统一异常捕获，不向前端暴露原始数据库错误
- 处理数据库锁定场景（busy_timeout + 重试）
"""
import sqlite3
import logging
from contextlib import contextmanager

from fastapi import HTTPException

from . import config
from .security import hash_password

logger = logging.getLogger("ovpn.database")


def _get_connection() -> sqlite3.Connection:
    """创建数据库连接，启用 WAL 与超时，降低锁定概率。"""
    conn = sqlite3.connect(
        str(config.DATABASE_PATH),
        timeout=15.0,  # 锁等待 15 秒
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row  # 结果可按列名访问
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    """
    数据库上下文管理器。
    用法：
        with get_db() as conn:
            conn.execute(...)
    异常时回滚并记录日志，向上抛出 DatabaseError（友好错误）。
    """
    conn = None
    try:
        conn = _get_connection()
        yield conn
        conn.commit()
    except sqlite3.OperationalError as e:
        if conn:
            conn.rollback()
        logger.error("数据库操作失败(OperationalError): %s", e)
        raise DatabaseError("数据库暂时不可用，请稍后重试")
    except sqlite3.IntegrityError as e:
        if conn:
            conn.rollback()
        logger.error("数据库约束冲突(IntegrityError): %s", e)
        raise DatabaseError("数据已存在或违反约束")
    except DatabaseError:
        if conn:
            conn.rollback()
        raise
    except HTTPException:
        # 业务异常（404/400 等）原样上抛，避免被误包成 500
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("数据库未知异常: %s", e)
        raise DatabaseError("数据库操作失败")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


class DatabaseError(Exception):
    """对外友好的数据库异常，不携带原始堆栈。"""
    pass


def init_database() -> None:
    """
    初始化数据库表结构，并创建默认管理员。
    可重复调用（幂等）。

    注意：Web 服务启动与安装诊断可能并发执行；
    只在管理员表为空时创建初始账号；已改名的管理员不得触发默认账号重建。
    """
    try:
        with get_db() as conn:
            conn.executescript(SCHEMA_SQL)
            existing = conn.execute(
                "SELECT id FROM admins LIMIT 1",
            ).fetchone()
            if existing:
                return
            try:
                pwd_hash = hash_password(config.DEFAULT_ADMIN_PASSWORD)
                conn.execute(
                    "INSERT OR IGNORE INTO admins (username, password_hash, created_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (config.DEFAULT_ADMIN_USER, pwd_hash),
                )
                logger.info("已创建默认管理员: %s", config.DEFAULT_ADMIN_USER)
            except sqlite3.IntegrityError:
                # 并发下另一进程已插入同一用户名
                logger.info("默认管理员已存在（并发创建）: %s", config.DEFAULT_ADMIN_USER)
    except DatabaseError:
        raise
    except Exception as e:
        logger.exception("初始化数据库失败: %s", e)
        raise DatabaseError("数据库初始化失败")


# ==================== 建表 SQL ====================
SCHEMA_SQL = """
-- 管理员账号
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);

-- 登录会话：JWT 只作为签名载体，真正的会话状态由服务端控制
CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jti TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT
);

-- VPN 客户端
CREATE TABLE IF NOT EXISTS vpn_clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    remark TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',  -- active / revoked
    cert_serial TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT,
    revoked_at TEXT
);

-- VPN 连接历史（每次 OpenVPN 会话一条记录）
CREATE TABLE IF NOT EXISTS vpn_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL UNIQUE,
    common_name TEXT NOT NULL,
    real_address TEXT DEFAULT '',
    virtual_address TEXT DEFAULT '',
    connected_at TEXT NOT NULL,
    disconnected_at TEXT,
    last_seen_at TEXT NOT NULL,
    bytes_received_start INTEGER NOT NULL DEFAULT 0,
    bytes_received_end INTEGER NOT NULL DEFAULT 0,
    bytes_sent_start INTEGER NOT NULL DEFAULT 0,
    bytes_sent_end INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'online'
);

CREATE INDEX IF NOT EXISTS idx_vpn_sessions_user ON vpn_sessions(common_name, connected_at DESC);
CREATE INDEX IF NOT EXISTS idx_vpn_sessions_status ON vpn_sessions(status);
CREATE INDEX IF NOT EXISTS idx_vpn_sessions_connected ON vpn_sessions(connected_at DESC);

-- DNS 域名黑名单
CREATE TABLE IF NOT EXISTS dns_blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,  -- 1 启用 / 0 禁用
    remark TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);

-- 操作审计日志
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 系统设置（键值对）
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_auth_sessions_jti ON auth_sessions(jti);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(username, revoked_at);
CREATE INDEX IF NOT EXISTS idx_vpn_clients_status ON vpn_clients(status);
CREATE INDEX IF NOT EXISTS idx_dns_enabled ON dns_blacklist(enabled);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
"""

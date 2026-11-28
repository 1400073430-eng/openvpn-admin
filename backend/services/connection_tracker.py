# -*- coding: utf-8 -*-
"""OpenVPN 在线会话跟踪器。

每 5 秒读取 status.log：
- 新出现的客户端 -> 建立一条 vpn_sessions
- 已存在客户端 -> 更新计数器/last_seen
- 消失客户端 -> 关闭会话并记录最终流量

OpenVPN status 中的 Bytes Received/Sent 是当前连接累计值，因此历史会话
流量使用 end-start 计算；在线速率使用相邻采样点的增量计算，是近似平均速率。
"""
import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Dict

from ..database import get_db
from .status_parser import parse_status_log

logger = logging.getLogger("ovpn.connection_tracker")
POLL_SECONDS = 5


def _now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _key(c) -> str:
    raw = "|".join([c.common_name, c.real_address, c.connected_since])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ConnectionTracker:
    def __init__(self):
        self._task = None
        self._last: Dict[str, dict] = {}
        self._speed: Dict[str, dict] = {}

    async def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="openvpn-connection-tracker")
        logger.info("OpenVPN 会话跟踪器已启动，轮询间隔=%ss", POLL_SECONDS)

    async def stop(self):
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("OpenVPN 会话跟踪器已停止")

    async def _run(self):
        # 启动时立即采样一次
        while True:
            try:
                self.sample()
            except Exception:
                logger.exception("OpenVPN 会话采样失败")
            await asyncio.sleep(POLL_SECONDS)

    def sample(self):
        clients = parse_status_log()
        now = time.time()
        current = {}
        for c in clients:
            key = _key(c)
            current[key] = c
            prev = self._last.get(key)
            if prev:
                dt = max(0.5, now - prev["ts"])
                up = max(0, c.bytes_received - prev["recv"]) / dt
                down = max(0, c.bytes_sent - prev["sent"]) / dt
            else:
                up = down = 0.0
            self._speed[key] = {
                "upload_bps": up,
                "download_bps": down,
                "ts": now,
                "sampled_at": datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
            self._last[key] = {
                "ts": now,
                "recv": c.bytes_received,
                "sent": c.bytes_sent,
                "common_name": c.common_name,
                "real_address": c.real_address,
            }

            connected_at = c.connected_since
            if c.connected_since_ts:
                connected_at = datetime.fromtimestamp(c.connected_since_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with get_db() as conn:
                row = conn.execute("SELECT id FROM vpn_sessions WHERE session_key=?", (key,)).fetchone()
                if row:
                    conn.execute(
                        """UPDATE vpn_sessions SET last_seen_at=?, real_address=?, virtual_address=?,
                           bytes_received_end=?, bytes_sent_end=?, status='online', disconnected_at=NULL
                           WHERE session_key=?""",
                        (_now_sql(), c.real_address, c.virtual_address, c.bytes_received, c.bytes_sent, key),
                    )
                else:
                    conn.execute(
                        """INSERT INTO vpn_sessions
                           (session_key, common_name, real_address, virtual_address, connected_at, last_seen_at,
                            bytes_received_start, bytes_received_end, bytes_sent_start, bytes_sent_end, status)
                           VALUES (?,?,?,?,?,?,?,?,?,?, 'online')""",
                        (key, c.common_name, c.real_address, c.virtual_address, connected_at, _now_sql(),
                         c.bytes_received, c.bytes_received, c.bytes_sent, c.bytes_sent),
                    )

        disappeared = set(self._last) - set(current)
        if disappeared:
            cleanup = []
            with get_db() as conn:
                for key in disappeared:
                    previous = self._last.get(key, {})
                    conn.execute(
                        """UPDATE vpn_sessions SET status='offline', disconnected_at=?, last_seen_at=?
                           WHERE session_key=? AND status='online'""",
                        (_now_sql(), _now_sql(), key),
                    )
                    cleanup.append((previous.get("real_address", ""), previous.get("common_name", "")))
                    self._speed.pop(key, None)
            for real_address, common_name in cleanup:
                if real_address and common_name:
                    try:
                        ip_only = real_address.rsplit(":", 1)[0]
                        from .cmd_runner import run_as_root
                        result = run_as_root(["kick-unblock", ip_only, common_name], timeout=10)
                        if result.success:
                            logger.info("会话结束，已检查并回收临时阻断: cn=%s ip=%s", common_name, ip_only)
                    except Exception:
                        logger.exception("会话结束时回收临时阻断失败: cn=%s", common_name)
        self._last = {k: self._last[k] for k in current if k in self._last}

    def speed_for(self, key: str) -> dict:
        return self._speed.get(key, {"upload_bps": 0.0, "download_bps": 0.0, "sampled_at": ""})


tracker = ConnectionTracker()

"""Hourly lightweight cleanup while the Web service is running. No file deletion."""
import asyncio
import logging
from datetime import datetime, timezone
from .audit_service import cleanup_records

logger = logging.getLogger('ovpn.maintenance')
INTERVAL_SECONDS = 3600
_status = {'interval_seconds': INTERVAL_SECONDS, 'last_run': None, 'result': 'not_run', 'deleted': {}}


def status():
    return dict(_status, deleted=dict(_status['deleted']))


async def run_once():
    global _status
    try:
        deleted = await asyncio.to_thread(cleanup_records, force=True)
        _status = {'interval_seconds': INTERVAL_SECONDS, 'last_run': datetime.now(timezone.utc).isoformat(), 'result': 'ok', 'deleted': deleted or {}}
        logger.info('定期清理完成，删除过期记录数量：%s', deleted)
    except Exception:
        _status = {'interval_seconds': INTERVAL_SECONDS, 'last_run': datetime.now(timezone.utc).isoformat(), 'result': 'failed', 'deleted': {}}
        logger.exception('定期清理失败，下一周期重试；未清理文件或升级备份')


async def run():
    # First run on service startup; subsequent runs require no user traffic.
    while True:
        await run_once()
        await asyncio.sleep(INTERVAL_SECONDS)

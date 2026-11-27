import asyncio
import unittest
from unittest.mock import patch, AsyncMock
from backend.services import maintenance


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_run_and_hourly_wait(self):
        with patch.object(maintenance, 'cleanup_records', return_value={'login_challenges': 2}) as cleanup, patch.object(maintenance.asyncio, 'sleep', new_callable=AsyncMock, side_effect=asyncio.CancelledError) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await maintenance.run()
            cleanup.assert_called_once_with(force=True)
            sleep.assert_awaited_once_with(3600)
            self.assertEqual(maintenance.status()['result'], 'ok')

    async def test_cleanup_failure_is_reported_without_raising(self):
        with patch.object(maintenance, 'cleanup_records', side_effect=RuntimeError('test failure')):
            await maintenance.run_once()
        self.assertEqual(maintenance.status()['result'], 'failed')
        self.assertEqual(maintenance.status()['deleted'], {})

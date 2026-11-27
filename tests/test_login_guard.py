import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from backend import config
from backend.services import login_guard as guard


class LoginGuardTests(unittest.TestCase):
    def test_single_use_and_persistent_limit(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, 'DATABASE_PATH', Path(temp) / 'db'), patch.object(config, 'JWT_SECRET', 'test-secret'), patch.object(guard.secrets, 'choice', return_value='A'):
            ident, png = guard.issue('test-ip')
            self.assertTrue(png.startswith(b'\x89PNG'))
            guard.verify(ident, 'AAAAA', 'test-ip')
            with self.assertRaises(HTTPException):
                guard.verify(ident, 'AAAAA', 'test-ip')
            for _ in range(10):
                guard.throttle('login', 'test-ip', 10, 60)
            with self.assertRaises(HTTPException) as error:
                guard.throttle('login', 'test-ip', 10, 60)
            self.assertEqual(error.exception.status_code, 429)

    def test_concurrency_budget_rejects_without_running_work(self):
        import threading
        with patch.object(guard, '_WORK_SLOTS', threading.BoundedSemaphore(1)):
            guard._WORK_SLOTS.acquire()
            try:
                @guard.bounded_work
                def expensive():
                    raise AssertionError('must not execute')
                with self.assertRaises(HTTPException) as error:
                    expensive()
                self.assertEqual(error.exception.status_code, 429)
            finally:
                guard._WORK_SLOTS.release()

    def test_account_limit_shared_across_sources(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, 'DATABASE_PATH', Path(temp) / 'db'), patch.object(config, 'JWT_SECRET', 'test-secret'):
            for _ in range(8):
                guard.throttle('account', 'admin', 8, 60)
            with self.assertRaises(HTTPException):
                guard.throttle('account', 'admin', 8, 60)

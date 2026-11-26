"""Run with project dependencies installed; no network or privileged operations."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from backend.routers import settings
    from backend.services import ipv6_upgrade
except ModuleNotFoundError as exc:
    raise unittest.SkipTest('API tests require project dependencies: ' + str(exc))


class IPv6ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(settings.router)
        self.client = TestClient(self.app)

    def test_unauthenticated_detection_and_download_denied(self):
        with patch.object(ipv6_upgrade, 'probe') as probe:
            self.assertEqual(self.client.get('/api/settings/ipv6').status_code, 401)
            self.assertEqual(self.client.post('/api/settings/ipv6/upgrade-script', json={}).status_code, 401)
            probe.assert_not_called()

    def auth(self):
        self.app.dependency_overrides[settings.get_current_user] = lambda: 'admin'

    def test_no_ipv6_disables_upgrade(self):
        self.auth()
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / 'server.conf'
            conf.write_text('server 10.8.0.0 255.255.255.0\n')
            with patch.object(settings.config, 'OPENVPN_CONFIG', conf), patch.object(ipv6_upgrade, 'probe', return_value={'ready': False}):
                data = self.client.get('/api/settings/ipv6').json()['data']
                self.assertFalse(data['upgrade_supported'])

    def test_ready_allows_upgrade_but_existing_config_refused(self):
        self.auth()
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / 'server.conf'
            conf.write_text('server 10.8.0.0 255.255.255.0\n')
            with patch.object(settings.config, 'OPENVPN_CONFIG', conf), patch.object(ipv6_upgrade, 'probe', return_value={'ready': True}):
                self.assertTrue(self.client.get('/api/settings/ipv6').json()['data']['upgrade_supported'])
                conf.write_text('server-ipv6 fd00::/64\n')
                self.assertFalse(self.client.get('/api/settings/ipv6').json()['data']['upgrade_supported'])

    def test_download_is_audited_source_only(self):
        self.auth()
        with patch.object(settings, 'log_action') as audit, patch.object(ipv6_upgrade, 'apply') as apply:
            response = self.client.post('/api/settings/ipv6/upgrade-script', json={})
            self.assertEqual(response.status_code, 200)
            source = response.json()['data']['script']
            compile(source, 'download.py', 'exec')
            self.assertIn('--confirm-disconnect', source)
            audit.assert_called_once()
            apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()

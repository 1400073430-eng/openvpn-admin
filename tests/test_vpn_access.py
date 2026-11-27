import ipaddress
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import config
from backend.services import vpn_access


class VpnAccessTests(unittest.TestCase):
    def test_public_requests_denied_even_with_forged_headers(self):
        from backend.main import app
        with patch.object(vpn_access, 'NETWORKS', (ipaddress.ip_network('10.8.0.0/24'),)):
            client = TestClient(app, client=('203.0.113.3', 1234))
            self.addCleanup(client.close)
            for path in ('/', '/index.html', '/static/index.html', '/api/health', '/api/docs', '/api/auth/captcha', '/pages/settings.html'):
                self.assertEqual(client.get(path, headers={'X-Forwarded-For': '10.8.0.2', 'X-Real-IP': '10.8.0.2'}).status_code, 403, path)
            self.assertEqual(client.post('/api/auth/login', json={}).status_code, 403)
            vpn = TestClient(app, client=('10.8.0.2', 1234))
            self.addCleanup(vpn.close)
            self.assertEqual(vpn.get('/').status_code, 200)
            self.assertEqual(vpn.get('/api/auth/me').status_code, 401)
            local = TestClient(app, client=('127.0.0.1', 1234))
            self.addCleanup(local.close)
            self.assertEqual(local.get('/api/health').status_code, 200)
            self.assertEqual(local.get('/').status_code, 403)

    def test_ipv6_and_no_network_fail_closed(self):
        with patch.object(vpn_access, 'NETWORKS', (ipaddress.ip_network('fd42:1234::/64'),)):
            self.assertTrue(vpn_access.allowed('fd42:1234::2', '/'))
            self.assertFalse(vpn_access.allowed('2606:4700::1111', '/'))
        with patch.object(vpn_access, 'NETWORKS', ()):
            self.assertFalse(vpn_access.allowed('10.8.0.2', '/'))

    def test_network_detection_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            conf = Path(temp) / 'server.conf'
            conf.write_text('server 10.9.0.0 255.255.255.0\nserver-ipv6 fd42:abcd::/64\n')
            with patch.object(config, 'OPENVPN_CONFIG', conf), patch.dict('os.environ', {'OVPM_ADMIN_VPN_CIDRS': ''}):
                self.assertEqual([str(n) for n in vpn_access.load_networks()], ['10.9.0.0/24', 'fd42:abcd::/64'])
            with patch.dict('os.environ', {'OVPM_ADMIN_VPN_CIDRS': '0.0.0.0/0'}):
                with self.assertRaises(ValueError):
                    vpn_access.load_networks()

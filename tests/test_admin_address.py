import tempfile
import unittest
from pathlib import Path
from backend.services.admin_address import summary, tunnel_addresses


class AdminAddressTests(unittest.TestCase):
    def test_detect_custom_vpn_subnet_and_ipv6(self):
        self.assertEqual(tunnel_addresses('server 10.9.7.0 255.255.255.0\nserver-ipv6 fd42:abcd::/64\n'), ['10.9.7.1', 'fd42:abcd::1'])

    def test_summary_uses_tunnel_and_actual_ports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            conf = root / 'vpn.conf'
            conf.write_text('server 10.9.7.0 255.255.255.0\nserver-ipv6 fd42:abcd::/64\n')
            env = root / 'env'
            env.write_text('OVPM_OVPN_CONFIG=' + str(conf) + '\nOVPM_SERVER_IP=203.0.113.10\nOVPM_WEB_PORT=8088\n')
            self.assertEqual(summary(env), ['http://10.9.7.1:8088', 'http://[fd42:abcd::1]:8088'])
            env.write_text(env.read_text() + 'OVPM_HTTPS_ENABLED=1\nOVPM_HTTPS_PORT=8443\n')
            self.assertEqual(summary(env)[0], 'https://10.9.7.1:8443')

    def test_missing_topology_never_returns_public_ip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            conf = root / 'vpn.conf'
            conf.write_text('port 1194\n')
            env = root / 'env'
            env.write_text('OVPM_OVPN_CONFIG=' + str(conf) + '\nOVPM_SERVER_IP=203.0.113.10\n')
            with self.assertRaises(ValueError):
                summary(env)

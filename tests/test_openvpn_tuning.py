import os
import unittest
from unittest.mock import patch

from backend.services import openvpn_service
from backend.services.cmd_runner import CommandResult


class OpenVPNTuningTests(unittest.TestCase):
    def test_client_defaults_are_added_without_overwriting_explicit_values(self):
        source = 'client\nremote vpn.example 1194\nmssfix 1320\ncipher AES-256-GCM\n<ca>\nCERT\n</ca>\n'
        result = openvpn_service._ensure_client_tuning(source)
        self.assertIn('mssfix 1320', result)
        self.assertNotIn('mssfix 1400', result)
        self.assertIn('cipher AES-256-GCM', result)
        self.assertNotIn('cipher AES-128-GCM', result)
        for expected in (
            'tun-mtu 1500', 'mtu-disc yes',
            'data-ciphers AES-128-GCM:AES-256-GCM',
            'data-ciphers-fallback AES-128-GCM',
            'sndbuf 524288', 'rcvbuf 524288',
        ):
            self.assertIn(expected, result)

    def test_server_address_uses_config_then_template_and_never_route_probe(self):
        with patch.dict(os.environ, {'OVPM_SERVER_IP': 'vpn.example'}, clear=False):
            self.assertEqual(openvpn_service.get_server_ip(), 'vpn.example')
        with patch.dict(os.environ, {}, clear=True), patch.object(
            openvpn_service.easyrsa_service, 'read_client_common', return_value='remote template.example 1194\n'
        ):
            self.assertEqual(openvpn_service.get_server_ip(), 'template.example')

    def test_management_kick_prefers_common_name_and_confirms_disappearance(self):
        with patch.dict(os.environ, {'OVPM_OVPN_MGMT_PORT': '7505'}), patch.object(
            openvpn_service, '_kick_via_management', return_value=True
        ) as management, patch.object(openvpn_service, '_session_is_online', return_value=False):
            result = openvpn_service.kick_client('198.51.100.7:443', 'alice')
        management.assert_called_once_with('alice', 7505)
        self.assertEqual(result['method'], 'management')

    def test_kick_fallback_is_bounded_and_named(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            openvpn_service, 'run_as_root', return_value=CommandResult(True, '', '', 0)
        ) as root:
            result = openvpn_service.kick_client('198.51.100.7:443', 'alice')
        root.assert_called_once_with(['kick-block', '198.51.100.7', 'alice', '300'], timeout=15)
        self.assertEqual(result['method'], 'temporary_block')


if __name__ == '__main__':
    unittest.main()

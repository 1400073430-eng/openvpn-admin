import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ipv6_upgrade', Path(__file__).resolve().parents[1] / 'backend/services/ipv6_upgrade.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def result(out='', rc=0):
    return subprocess.CompletedProcess([], rc, out, '')


class IPv6Tests(unittest.TestCase):
    def probe_with(self, address='2606:4700::1234', route=True, egress='2606:4700::1234', fail=False):
        def fake(args, **kwargs):
            if args[0] == 'curl':
                if fail:
                    raise RuntimeError('network unavailable')
                return result(egress)
            if 'addr' in args:
                return result(json.dumps([{'ifname': 'ens3', 'addr_info': [{'local': address}]}]))
            return result(json.dumps([{'dev': 'ens3'}] if route else []))
        with patch.object(m, 'run', side_effect=fake):
            return m.probe()

    def test_full_dualstack_ready(self):
        self.assertTrue(self.probe_with()['ready'])

    def test_no_default_route_not_ready(self):
        self.assertFalse(self.probe_with(route=False)['ready'])

    def test_private_address_not_public(self):
        self.assertFalse(self.probe_with(address='fd00::1')['ready'])

    def test_curl_failure_not_ready(self):
        self.assertFalse(self.probe_with(fail=True)['ready'])

    def test_ipv4_response_rejected(self):
        self.assertFalse(self.probe_with(egress='173.254.220.24')['ready'])

    def test_markers_idempotent(self):
        config = 'server 10.8.0.0 255.255.255.0\npush "dhcp-option DNS 1.1.1.1"\n'
        first = m.render_config(config)
        self.assertEqual(first, m.render_config(first))
        self.assertIn('dhcp-option DNS 1.1.1.1', first)
        self.assertEqual(first.count('server-ipv6'), 1)

    def test_existing_ipv6_refused(self):
        for text in ['server-ipv6 fd00::/64', 'push "route-ipv6 ::/0"', 'block-ipv6', 'client-config-dir ccd', 'config other.conf']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.render_config(text)

    def test_invalid_markers_refused(self):
        with self.assertRaises(ValueError):
            m.render_config(m.START)

    def test_comments_preserved(self):
        self.assertIn('# server-ipv6 old', m.render_config('# server-ipv6 old'))

    def test_interface_injection_refused(self):
        for name in ['ens3"; drop', '../etc', 'ens3\n']:
            with self.assertRaises(ValueError):
                m.nft_rules(name)

    def test_nat_scoped_to_vpn_subnet_and_interface(self):
        rules = m.nft_rules('ens3')
        self.assertIn('ip6 saddr ' + m.SUBNET + ' oifname "ens3" counter masquerade', rules)
        self.assertIn('priority -50', rules)
        self.assertNotIn('flush ruleset', rules)

    def test_no_ipv6_no_network_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'state'
            state.mkdir()
            conf = root / 'server.conf'
            conf.write_text('server 10.8.0.0 255.255.255.0\n')
            with patch.object(m, 'STATE', state), patch.object(m, 'find_config', return_value=(conf, 'openvpn@server')), patch.object(m, 'probe', return_value={'ready': False}), patch.object(m, 'run') as run:
                with self.assertRaises(RuntimeError):
                    m.apply()
                run.assert_not_called()
                self.assertEqual(conf.read_text(), 'server 10.8.0.0 255.255.255.0\n')
                self.assertFalse((state / 'manifest.json').exists())

    def test_apply_rollback_and_failure_rollback(self):
        for fail_restart in (False, True):
            with self.subTest(fail_restart=fail_restart), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state = root / 'state'
                state.mkdir()
                conf = root / 'server.conf'
                original = 'server 10.8.0.0 255.255.255.0\n'
                conf.write_text(original)
                sysctl, nftfile, unit = root / 'sysctl.conf', root / 'rules.nft', root / 'unit.service'
                ready = {'ready': True, 'addresses': [{'interface': 'ens3'}], 'egress_ipv6': '2606:4700::1234'}
                calls = []
                restarts = 0
                def fake(args, **kwargs):
                    nonlocal restarts
                    calls.append(args)
                    if args[:3] == ['systemctl', 'is-active', '--quiet']:
                        return result(rc=3 if args[-1] in ('ufw', 'firewalld') else 0)
                    if args[:3] == ['nft', 'list', 'table']:
                        return result(rc=1)
                    if args[:4] == ['ip', '-j', '-6', 'route']:
                        return result('[{"dev":"ens3"}]')
                    if args[:2] == ['nft', '-j']:
                        return result('{"nftables":[]}')
                    if args[:2] == ['sysctl', '-n']:
                        return result('0\n')
                    if args[:2] == ['systemctl', 'restart']:
                        restarts += 1
                        if fail_restart and restarts == 1:
                            raise RuntimeError('restart failed')
                    return result()
                with patch.multiple(m, STATE=state, SYSCTL=sysctl, NFTFILE=nftfile, UNIT=unit), patch.object(m, 'find_config', return_value=(conf, 'openvpn@server')), patch.object(m, 'probe', return_value=ready), patch.object(m, 'run', side_effect=fake), patch.object(m.subprocess, 'run', return_value=result()):
                    if fail_restart:
                        with self.assertRaises(RuntimeError):
                            m.apply()
                    else:
                        m.apply()
                        self.assertIn(m.START, conf.read_text())
                        self.assertTrue(unit.exists())
                        m.rollback()
                    self.assertEqual(conf.read_text(), original)
                    self.assertFalse(sysctl.exists())
                    self.assertFalse(nftfile.exists())
                    self.assertFalse(unit.exists())
                    self.assertEqual(json.loads((state / 'manifest.json').read_text())['status'], 'rolled_back')
                    self.assertIn(['sysctl', '-w', 'net.ipv6.conf.ens3.accept_ra=0'], calls)


if __name__ == '__main__':
    unittest.main()

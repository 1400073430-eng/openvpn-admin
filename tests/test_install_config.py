"""Execute the real Nyr config repair branch against temporary paths only."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallConfigTests(unittest.TestCase):
    def repair(self, content):
        source = (ROOT / 'scripts/install.sh').read_text()
        start = source.index('setup_openvpn_config() {')
        end = source.index('# 第9步', start)
        function = source[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for old, new in [('/etc/openvpn', str(base / 'etc')), ('/run/openvpn-server', str(base / 'run')), ('/var/log/openvpn', str(base / 'log'))]:
                function = function.replace(old, new)
            conf = base / 'etc/server/server.conf'
            conf.parent.mkdir(parents=True)
            conf.write_text(content)
            prelude = 'set -euo pipefail\nlog_info(){ :; }; log_success(){ :; }; log_warn(){ :; }\n'
            subprocess.run(['bash', '-c', prelude + function + '\nsetup_openvpn_config\n'], check=True, capture_output=True, text=True, timeout=5)
            return conf.read_text()

    def test_remove_invalid_directive_and_keep_existing_crypto(self):
        result = self.repair('server 10.8.0.0 255.255.255.0\nstatus-update 5\ndata-ciphers AES-128-GCM\ntls-crypt tc.key\n')
        self.assertNotIn('status-update', result)
        self.assertIn('data-ciphers AES-128-GCM', result)
        self.assertIn('tls-crypt tc.key', result)
        self.assertRegex(result, r'status .+status-server\.log 5\n')

    def test_preserve_existing_status_interval(self):
        result = self.repair('status /custom/status.log 15\nstatus-version 2\n')
        self.assertEqual(result.count('status '), 1)
        self.assertIn('status /custom/status.log 15', result)


    def test_repair_preserves_existing_security_settings(self):
        source = (ROOT / 'scripts/install.sh').read_text()
        start = source.index('    # Keep configured transport and access controls')
        end = source.index('    if [[ "$preserve_env" -eq 1 ]]; then', start)
        fragment = source[start:end]
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / '.env'
            env.write_text('OVPM_HTTPS_ENABLED=1\nOVPM_HTTPS_PORT=8443\nOVPM_COOKIE_SECURE=1\nOVPM_ADMIN_VPN_CIDRS=10.9.0.0/24\nOVPM_SSL_CERT=/custom/cert.pem\n')
            subprocess.run(['bash', '-c', 'INSTALL_DIR="$1"; repair(){\n' + fragment + '\n}; repair', 'bash', temp], check=True, capture_output=True)
            values = dict(line.split('=', 1) for line in env.read_text().splitlines() if '=' in line and not line.startswith('#'))
            self.assertEqual(values['OVPM_HTTPS_ENABLED'], '1')
            self.assertEqual(values['OVPM_HTTPS_PORT'], '8443')
            self.assertEqual(values['OVPM_COOKIE_SECURE'], '1')
            self.assertEqual(values['OVPM_ADMIN_VPN_CIDRS'], '10.9.0.0/24')
            self.assertEqual(values['OVPM_SSL_CERT'], '/custom/cert.pem')


if __name__ == '__main__':
    unittest.main()

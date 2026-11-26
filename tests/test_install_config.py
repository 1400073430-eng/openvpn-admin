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


if __name__ == '__main__':
    unittest.main()

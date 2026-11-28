import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ClientQuickTests(unittest.TestCase):
    def test_list_parses_valid_and_revoked_index_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            easy = Path(tmp) / 'easy-rsa'
            (easy / 'pki').mkdir(parents=True)
            (easy / 'pki' / 'index.txt').write_text(
                'V\t361124000000Z\t\tABC123\tunknown\t/CN=alice\n'
                'R\t361124000000Z\t261128000000Z\tDEF456\tunknown\t/CN=bob\n',
                encoding='utf-8',
            )
            env = dict(os.environ, OVPM_EASYRSA_DIR=str(easy), OVPM_OPENVPN_DIR=tmp)
            result = subprocess.run(
                ['bash', str(ROOT / 'scripts/cli/client-quick.sh'), 'list'],
                env=env, capture_output=True, text=True, check=True,
            )
        self.assertIn('启用 | alice | ABC123', result.stdout)
        self.assertIn('吊销 | bob | DEF456', result.stdout)


if __name__ == '__main__':
    unittest.main()

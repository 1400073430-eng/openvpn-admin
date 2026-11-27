import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import sqlite3


class BootstrapConcurrencyTests(unittest.TestCase):
    def test_concurrent_initializers_create_only_one_admin(self):
        script = '''import time
from backend import database
original = database.hash_password
def delayed(password):
    time.sleep(0.15)
    return original(password)
database.hash_password = delayed
database.init_database()
'''
        with tempfile.TemporaryDirectory() as temp:
            processes = []
            for username in ('first_admin', 'second_admin'):
                env = dict(os.environ, OVPM_DATA_DIR=temp, OVPM_ADMIN_USER=username, OVPM_ADMIN_PASSWORD='temporary-test-password')
                processes.append(subprocess.Popen([sys.executable, '-c', script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            try:
                for process in processes:
                    out, err = process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, err.decode())
                with sqlite3.connect(str(Path(temp) / 'ovpn_admin.db')) as conn:
                    self.assertEqual(conn.execute('SELECT count(*) FROM admins').fetchone()[0], 1)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

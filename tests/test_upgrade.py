import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.services.frontend_render import render

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deploy_release', ROOT / 'scripts/deploy_release.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


class UpgradeTests(unittest.TestCase):
    def source(self, root):
        source = root / 'source'
        (source / 'backend').mkdir(parents=True)
        (source / 'backend/main.py').write_text('value = 2\n')
        (source / 'frontend').mkdir()
        (source / 'frontend/index.html').write_text('new')
        (source / 'requirements.txt').write_text('')
        return source

    def test_exact_replacement_preserves_data_and_removes_stale_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            (source / 'backend/__pycache__').mkdir()
            (source / 'backend/__pycache__/bad.pyc').write_bytes(b'old')
            target = root / 'target'
            (target / 'backend').mkdir(parents=True)
            (target / 'backend/removed.py').write_text('old')
            (target / 'data').mkdir()
            (target / 'data/keep.txt').write_text('keep')
            (target / '.env').write_text('KEEP=1\n')
            backup = deployment.deploy(source, target)
            self.assertFalse((target / 'backend/removed.py').exists())
            self.assertFalse((target / 'backend/__pycache__').exists())
            self.assertEqual((target / 'data/keep.txt').read_text(), 'keep')
            self.assertEqual((target / '.env').read_text(), 'KEEP=1\n')
            self.assertTrue((backup / 'backend/removed.py').exists())

    def test_mid_copy_failure_restores_old_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            target = root / 'target'
            (target / 'backend').mkdir(parents=True)
            (target / 'backend/main.py').write_text('old')
            original = deployment.os.replace
            def fail_new_backend(src, dst):
                if '.code-stage-' in str(src) and Path(src).name == 'backend':
                    raise OSError('simulated failure')
                return original(src, dst)
            with patch.object(deployment.os, 'replace', side_effect=fail_new_backend):
                with self.assertRaises(OSError):
                    deployment.deploy(source, target)
            self.assertEqual((target / 'backend/main.py').read_text(), 'old')

    def test_self_deploy_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self.source(Path(temp))
            with self.assertRaises(ValueError):
                deployment.deploy(source, source)

    def test_asset_digest_changes_with_same_size_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'js').mkdir()
            script = root / 'js/app.js'
            script.write_text('old')
            page = root / 'index.html'
            page.write_text('<script src="/static/js/app.js"></script>')
            first = render(page, root)
            script.write_text('new')
            second = render(page, root)
            self.assertNotEqual(first, second)
            self.assertIn('?v=', second)

    def test_virtual_environment_switch_keeps_previous_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            target = root / 'target'
            (target / 'venv').mkdir(parents=True)
            (target / 'venv/old-marker').write_text('old')
            new = target / '.venv-release-new'
            (new / 'bin').mkdir(parents=True)
            (new / 'bin/python').write_text('placeholder')
            backup = deployment.deploy(source, target, new)
            self.assertTrue((target / 'venv').is_symlink())
            self.assertEqual((target / 'venv').resolve(), new)
            self.assertEqual((backup / 'venv/old-marker').read_text(), 'old')
            self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)

    def test_dependency_preflight_failure_does_not_touch_active_install(self):
        import subprocess
        text = (ROOT / 'scripts/install.sh').read_text()
        start = text.index('install_python_deps() {')
        end = text.index('\n}\n', start) + 2
        function = text[start:end]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / 'venv').mkdir()
            (target / 'venv/marker').write_text('old')
            script = '''log_info(){ :; }; log_success(){ :; }; log_fail(){ :; }
python3(){ return 1; }
''' + function + '\nINSTALL_DIR="$1"; LOG_FILE="$1/log"; SCRIPT_DIR="$2"; install_python_deps'
            result = subprocess.run(['bash', '-c', script, 'bash', str(target), str(ROOT / 'scripts')], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((target / 'venv/marker').read_text(), 'old')
            self.assertEqual(list(target.glob('.venv-release.*')), [])

    def test_custom_data_directory_refused_before_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            target = root / 'target'
            target.mkdir()
            (target / '.env').write_text('OVPM_DATA_DIR=/external/custom-data\n')
            with self.assertRaises(ValueError):
                deployment.deploy(source, target)
            self.assertEqual((target / '.env').read_text(), 'OVPM_DATA_DIR=/external/custom-data\n')

    def test_schema_preflight_only_migrates_clone(self):
        import os
        import sqlite3
        import subprocess
        import sys
        from backend.database import SCHEMA_SQL
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for compatible in (True, False):
                target = root / ('good' if compatible else 'bad')
                (target / 'data').mkdir(parents=True)
                db = target / 'data/ovpn_admin.db'
                with sqlite3.connect(db) as conn:
                    if compatible:
                        conn.executescript(SCHEMA_SQL)
                        conn.execute("INSERT INTO admins (username, password_hash) VALUES ('admin', 'test-hash')")
                    else:
                        conn.executescript("CREATE TABLE admins (id INTEGER PRIMARY KEY, username TEXT); INSERT INTO admins VALUES (1, 'admin');")
                before = db.read_bytes()
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/preflight_release.py'), '--source', str(ROOT), '--target', str(target), '--data', str(target / 'clone')], env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, timeout=25)
                if compatible:
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                else:
                    self.assertNotEqual(result.returncode, 0)
                self.assertEqual(db.read_bytes(), before)

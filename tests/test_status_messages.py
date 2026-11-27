"""Execute reporting functions only; never run install or uninstall actions."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StatusMessageTests(unittest.TestCase):
    def test_uninstall_partial_failure_returns_nonzero(self):
        source = (ROOT / 'scripts/uninstall.sh').read_text()
        function = source[source.index('report_uninstall_result() {'):source.index('[[ ${EUID')]
        for leftover, issues, expected in ((0, 0, 0), (1, 0, 1), (0, 1, 1)):
            result = subprocess.run(['bash', '-c', 'log(){ echo "$*"; }; ' + function + '\nleftover=$1; uninstall_issues=$2; report_uninstall_result', 'bash', str(leftover), str(issues)], capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected:
                self.assertIn('未完全完成', result.stdout)

    def test_install_summary_does_not_hide_failed_checks(self):
        source = (ROOT / 'scripts/install.sh').read_text()
        start = source.index('print_summary() {')
        end = source.index('\n# ============================================================\n# 主流程', start)
        function = source[start:end]
        with tempfile.TemporaryDirectory() as temp:
            script = '''configured_health_url(){ :; }
print_summary_stub(){ :; }
''' + function + '''
INSTALL_DIR="$1"; CHECK_FAILURES=2; RUN_MODE=repair
print_summary
'''
            result = subprocess.run(['bash', '-c', script, 'bash', temp], capture_output=True, text=True)
            self.assertIn('2 项检查失败', result.stdout)
            self.assertNotIn('安装完成！', result.stdout)
            script = function + '\nINSTALL_DIR="$1"; CHECK_FAILURES=0; RUN_MODE=install; INITIAL_CREDENTIALS_REUSED=1; DEFAULT_ADMIN_PASSWORD=should-not-be-shown; print_summary'
            result = subprocess.run(['bash', '-c', script, 'bash', temp], capture_output=True, text=True)
            self.assertNotIn('should-not-be-shown', result.stdout)
            self.assertNotIn('初始密码为本次安装随机生成', result.stdout)


#!/usr/bin/env bash
# Local verification only: no package installation or server networking mutations.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
command -v node >/dev/null 2>&1 || { echo '需要 Node.js 20+ 运行前端语法和交互测试'; exit 1; }
python3 -m ruff check backend tests --select F401,F841,F821
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash tests/security_review_static.sh
python3 - <<'PY'
from pathlib import Path
import ast
import subprocess
for p in Path('.').rglob('*.sh'):
    if '.venv' not in p.parts and 'venv' not in p.parts:
        subprocess.run(['bash', '-n', str(p)], check=True)
for folder in ('backend', 'tests', 'scripts/privileged'):
    for p in Path(folder).rglob('*'):
        if p.is_file() and (p.suffix == '.py' or p.name.startswith('ovpn-admin-')):
            ast.parse(p.read_text(), filename=str(p))
print('[PASS] All project shell/Python syntax')
PY
for file in frontend/js/*.js; do node --check "$file"; done
node tests/test_ipv6_ui.js
node tests/test_list_view.js
node tests/test_egress_ui.js
node tests/test_password_ui.js
node tests/test_auth_ui.js
printf '%s\n' '[PASS] Local test suite completed. Real VPS networking still requires deployment acceptance.'

#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 - <<'PY'
import ast
from pathlib import Path
files = list(Path('backend').rglob('*.py')) + [p for p in Path('scripts/privileged').iterdir() if p.is_file()]
for path in files:
    if path.suffix == '.py' or path.name == 'ovpn-admin-job-worker':
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('[PASS] Python syntax')
PY

bash -n scripts/install.sh scripts/uninstall.sh scripts/preflight-real-server.sh
echo '[PASS] Shell syntax'

if grep -RIn 'onclick=' frontend/js; then
  echo '[FAIL] inline onclick remains' >&2
  exit 1
fi
echo '[PASS] no inline onclick in frontend JS'

grep -q 'O_NONBLOCK' scripts/privileged/ovpn-admin-job-worker
grep -q 'MAX_JOBS_PER_RUN = 64' scripts/privileged/ovpn-admin-job-worker
grep -q 'job queue overflow; retry later' scripts/privileged/ovpn-admin-job-worker
echo '[PASS] worker FIFO and queue guards'

grep -q 'O_NOFOLLOW' scripts/privileged/ovpn-admin-priv.py
grep -q 'root-owned regular file' scripts/privileged/ovpn-admin-priv.py
echo '[PASS] privileged config file guard'

if command -v node >/dev/null 2>&1; then
  for file in frontend/js/*.js; do node --check "$file" >/dev/null; done
  echo '[PASS] JavaScript syntax'
else
  echo '[SKIP] JavaScript syntax (node not installed)'
fi

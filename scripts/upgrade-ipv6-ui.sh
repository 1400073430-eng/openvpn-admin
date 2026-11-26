#!/usr/bin/env bash
# Update only the settings UI/API and IPv6 module. Does not modify VPN networking.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 root 执行'; exit 1; }
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGET=${1:-/opt/openvpn-admin}
[[ -f "$TARGET/backend/routers/settings.py" && -x "$TARGET/venv/bin/python" ]] || {
  echo '未找到现有平台及虚拟环境，请传入正确安装目录'; exit 1;
}
"$TARGET/venv/bin/python" -c 'import fastapi, pydantic'
BACKUP=$(mktemp -d /var/backups/ovpn-ipv6-ui.XXXXXXXX)
FILES=(backend/routers/settings.py backend/services/ipv6_upgrade.py frontend/js/settings.js)
for relative in "${FILES[@]}"; do
  [[ ! -L "$TARGET/$relative" ]] || { echo "拒绝覆盖符号链接：$relative"; exit 1; }
  mkdir -p "$BACKUP/$(dirname "$relative")"
  if [[ -f "$TARGET/$relative" ]]; then
    cp -a "$TARGET/$relative" "$BACKUP/$relative"
  else
    touch "$BACKUP/$relative.absent"
  fi
done
rollback_ui() {
  trap - ERR
  echo "更新失败，恢复备份：$BACKUP"
  for relative in "${FILES[@]}"; do
    if [[ -f "$BACKUP/$relative.absent" ]]; then
      rm -f "$TARGET/$relative"
    else
      cp -a "$BACKUP/$relative" "$TARGET/$relative"
    fi
  done
  systemctl restart openvpn-admin || true
  exit 1
}
trap rollback_ui ERR
for relative in "${FILES[@]}"; do
  install -o root -g root -m 0644 "$SOURCE/$relative" "$TARGET/$relative"
done
"$TARGET/venv/bin/python" - "$TARGET" <<'PY'
import ast
import sys
from pathlib import Path
root = Path(sys.argv[1])
for name in ('backend/routers/settings.py', 'backend/services/ipv6_upgrade.py'):
    ast.parse((root / name).read_text())
sys.path.insert(0, str(root))
from backend.routers.settings import router
assert any(r.path == '/api/settings/ipv6' for r in router.routes)
PY
systemctl restart openvpn-admin
systemctl is-active --quiet openvpn-admin
trap - ERR
echo "IPv6 检测页面已更新；备份：$BACKUP"
echo '未修改 VPN 配置，也未重启 OpenVPN。请刷新系统设置页面。'

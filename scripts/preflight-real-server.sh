#!/usr/bin/env bash
set -u
APP_DIR="/opt/openvpn-admin"
fail=0
ok(){ echo "[PASS] $*"; }
warn(){ echo "[WARN] $*"; }
bad(){ echo "[FAIL] $*"; fail=$((fail+1)); }

check_cmd(){ command -v "$1" >/dev/null 2>&1 && ok "命令存在: $1" || bad "缺少命令: $1"; }

echo "===== OpenVPN Admin real-server preflight ====="
for c in systemctl nft ip python3; do check_cmd "$c"; done

echo
echo "===== Web service ====="
if systemctl is-active --quiet openvpn-admin; then ok "openvpn-admin active"; else bad "openvpn-admin 未运行"; fi
systemctl show openvpn-admin -p User -p Group -p ActiveState -p SubState --no-pager 2>/dev/null || true

 echo
echo "===== Job queue ====="
if [[ -d /var/spool/ovpn-admin ]]; then ok "spool exists"; else bad "spool 不存在"; fi
if systemctl is-active --quiet ovpn-admin-jobs.path; then ok "ovpn-admin-jobs.path active"; else bad "ovpn-admin-jobs.path 未运行"; fi
if [[ -x /usr/local/sbin/ovpn-admin-priv ]]; then ok "priv helper installed"; else bad "priv helper missing"; fi
if [[ -x /usr/local/sbin/ovpn-admin-job-worker ]]; then ok "job worker installed"; else bad "job worker missing"; fi
if [[ -x /usr/local/sbin/ovpn-admin-blacklist-sync ]]; then ok "blacklist sync installed"; else bad "blacklist sync missing"; fi

 echo
echo "===== Blacklist paths ====="
for f in "$APP_DIR/data/ovpn_admin.db" "$APP_DIR/data/blacklist-domains.txt" "$APP_DIR/data/blacklist-state.json"; do
  [[ -e "$f" ]] && ls -l "$f" || true
done
if [[ -d "$APP_DIR/data" && -w "$APP_DIR/data" ]]; then ok "data 可写"; else bad "data 不可写"; fi

 echo
echo "===== nftables ====="
if nft list table inet ovpn_admin >/dev/null 2>&1; then ok "inet ovpn_admin table exists"; else bad "ovpn_admin nft table missing"; fi
if nft list set inet ovpn_admin blocked_ipv4 >/dev/null 2>&1; then ok "blocked_ipv4 exists"; else bad "blocked_ipv4 missing"; fi
if nft list set inet ovpn_admin blocked_ipv6 >/dev/null 2>&1; then ok "blocked_ipv6 exists"; else bad "blocked_ipv6 missing"; fi

 echo
echo "===== OpenVPN ====="
for svc in openvpn-server@server openvpn@server openvpn; do
  state=$(systemctl show "$svc" -p LoadState -p ActiveState -p SubState --no-pager 2>/dev/null | tr '\n' ' ')
  [[ -n "$state" ]] && echo "$svc: $state"
done
ip -brief addr show tun0 2>/dev/null || true

 echo
echo "===== Recent logs ====="
journalctl -u openvpn-admin -n 30 --no-pager 2>/dev/null | tail -30

echo
echo "===== Result-file permission regression check ====="
if grep -q 'fchown' /usr/local/sbin/ovpn-admin-job-worker 2>/dev/null; then ok "job worker result ownership fix present"; else bad "job worker 仍未包含 result ownership fix"; fi

 echo
echo "===== RESULT ====="
if [[ $fail -eq 0 ]]; then echo "PREFLIGHT PASS"; else echo "PREFLIGHT FAIL: $fail 项"; fi
exit "$fail"

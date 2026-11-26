#!/bin/bash
set -u
PASS=0; WARN=0; FAIL=0
ok(){ echo "[PASS] $1"; PASS=$((PASS+1)); }
warn(){ echo "[WARN] $1"; WARN=$((WARN+1)); }
fail(){ echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

command -v nft >/dev/null 2>&1 && ok "nft 命令存在" || fail "nft 命令不存在"
command -v getent >/dev/null 2>&1 && ok "系统 DNS 解析工具存在" || fail "getent 不存在"

if systemctl is-enabled --quiet openvpn-admin-blacklist.timer 2>/dev/null; then ok "黑名单刷新 timer 已启用"; else warn "黑名单刷新 timer 未启用"; fi
if systemctl is-active --quiet openvpn-admin-blacklist.timer 2>/dev/null; then ok "黑名单刷新 timer 正在运行"; else warn "黑名单刷新 timer 当前未 active"; fi

if nft list table inet ovpn_admin >/dev/null 2>&1; then
  ok "nftables ovpn_admin 表存在"
  nft list set inet ovpn_admin blocked_ipv4 >/dev/null 2>&1 && ok "IPv4 黑名单 set 存在" || fail "IPv4 黑名单 set 缺失"
  nft list set inet ovpn_admin blocked_ipv6 >/dev/null 2>&1 && ok "IPv6 黑名单 set 存在" || fail "IPv6 黑名单 set 缺失"
  if nft list chain inet ovpn_admin forward 2>/dev/null | grep -q 'ip saddr .*10\.8\.0\.0/24.*ip daddr @blocked_ipv4.*drop'; then
    ok "VPN IPv4 → 黑名单 IP 的 DROP 规则存在"
  else
    warn "未匹配到标准 VPN IPv4 DROP 规则，请执行 nft list chain inet ovpn_admin forward 检查"
  fi
else
  warn "nftables ovpn_admin 表尚未创建（可执行 systemctl start openvpn-admin-blacklist.service）"
fi

if [[ -f /opt/openvpn-admin/data/blacklist-domains.txt ]]; then ok "黑名单域名文件存在"; else warn "尚无黑名单域名文件（添加第一个域名后自动生成）"; fi
if [[ -f /opt/openvpn-admin/data/blacklist-state.json ]]; then ok "黑名单解析状态文件存在"; else warn "尚无解析状态文件"; fi

if getent ahosts example.com >/dev/null 2>&1; then ok "服务器当前可以进行系统 DNS 解析"; else warn "当前服务器系统 DNS 无法解析 example.com；黑名单同步也会暂时无法获得 IP"
fi

echo
echo "结果：PASS=$PASS WARN=$WARN FAIL=$FAIL"
[[ $FAIL -eq 0 ]]

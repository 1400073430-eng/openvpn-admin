#!/bin/bash
# ============================================================
# openvpn-admin v4.3.10 安装验收脚本（在 Ubuntu 服务器上以 root 运行）
# 用法:
#   1) 先完成安装或修复:
#        cd /root/openvpn-admin-v4.3.10/scripts && bash install.sh --repair
#   2) 再跑本验收:
#        bash /root/openvpn-admin-v4.3.10/tests/install_acceptance.sh
# ============================================================
set -u

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS=0; FAIL=0; WARN=0

ok()   { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS+1)); }
bad()  { echo -e "${RED}[FAIL]${NC} $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; WARN=$((WARN+1)); }

echo "========== openvpn-admin v4.3.10 安装验收 =========="
echo "时间: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo

# ---------- 1. OpenVPN 版本与配置 ----------
echo "--- OpenVPN 配置兼容 ---"
ovpn_ver=""
if command -v openvpn >/dev/null 2>&1; then
  ovpn_ver=$(openvpn --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  ok "openvpn 已安装: ${ovpn_ver:-未知}"
else
  bad "未找到 openvpn 命令"
fi

conf=""
for c in /etc/openvpn/server/server.conf /etc/openvpn/server.conf; do
  [[ -f "$c" ]] && conf="$c" && break
done
if [[ -n "$conf" ]]; then
  ok "配置文件: $conf"
  if grep -qE '^[[:space:]]*status-update[[:space:]]' "$conf"; then
    bad "含无效 status-update，请运行修复；刷新间隔使用 status <文件> <秒数>"
  else
    ok "不含无效 status-update 指令"
  fi
  grep -qE '^\s*status\s+' "$conf" && ok "含 status 日志配置" || warn "缺少 status 行，在线列表可能不可用"
  grep -qE '^\s*data-ciphers' "$conf" && ok "含 data-ciphers" || warn "缺少 data-ciphers"
else
  bad "未找到 OpenVPN server.conf"
fi

# ---------- 2. 服务状态 ----------
echo
echo "--- systemd 服务 ---"
ovpn_svc=""
for s in openvpn-server@server openvpn@server openvpn; do
  load=$(systemctl show "$s" -p LoadState --value 2>/dev/null || true)
  if [[ -n "$load" && "$load" != "not-found" ]]; then
    ovpn_svc="$s"
    break
  fi
done
if [[ -n "$ovpn_svc" ]]; then
  ok "OpenVPN 单元: $ovpn_svc"
  st=$(systemctl show "$ovpn_svc" -p ActiveState --value 2>/dev/null || true)
  res=$(systemctl show "$ovpn_svc" -p Result --value 2>/dev/null || true)
  [[ "$st" == "active" ]] && ok "ActiveState=active" || bad "ActiveState=$st (期望 active)"
  [[ "$res" == "success" ]] && ok "Result=success" || warn "Result=$res"
  # 崩溃循环检测
  nrestart=$(systemctl show "$ovpn_svc" -p NRestarts --value 2>/dev/null || echo 0)
  if [[ "${nrestart:-0}" -gt 20 ]]; then
    bad "NRestarts=$nrestart（疑似崩溃循环）"
  else
    ok "NRestarts=$nrestart"
  fi
else
  bad "未找到 OpenVPN systemd 单元"
fi

if systemctl is-active --quiet openvpn-admin 2>/dev/null; then
  ok "openvpn-admin Web 服务 active"
else
  bad "openvpn-admin Web 服务未运行"
fi

# ---------- 3. 网络就绪 ----------
echo
echo "--- 网络就绪 ---"
ip link show tun0 >/dev/null 2>&1 && ok "tun0 存在" || bad "tun0 不存在"
if command -v ss >/dev/null 2>&1; then
  ss -ulnp 2>/dev/null | grep -qE 'openvpn|:1190|:1194' \
    && ok "OpenVPN UDP 端口在监听" \
    || warn "未检测到 OpenVPN UDP 监听（请核对端口配置）"
fi

# ---------- 4. 启动日志 ----------
echo
echo "--- 最近启动日志 ---"
if [[ -n "$ovpn_svc" ]]; then
  invocation=$(systemctl show "$ovpn_svc" -p InvocationID --value 2>/dev/null || true)
  if [[ -n "$invocation" ]]; then
    recent=$(journalctl "_SYSTEMD_INVOCATION_ID=$invocation" -n 80 --no-pager -o cat 2>/dev/null || true)
  else
    recent=$(journalctl -u "$ovpn_svc" --since '5 minutes ago' -n 80 --no-pager -o cat 2>/dev/null || true)
  fi
  echo "$recent" | grep -q 'Initialization Sequence Completed' \
    && ok "出现 Initialization Sequence Completed" \
    || bad "未出现 Initialization Sequence Completed"
  if echo "$recent" | grep -q 'Options error'; then
    bad "日志存在 Options error："
    echo "$recent" | grep 'Options error' | tail -5
  else
    ok "无 Options error"
  fi

fi

# ---------- 5. Web 健康 ----------
echo
echo "--- Web 健康 ---"
env_file=/opt/openvpn-admin/.env
read_setting() { grep -E "^$1=" "$env_file" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r'; }
scheme=http
port=$(read_setting OVPM_WEB_PORT)
port=${port:-80}
curl_args=(--silent --show-error --connect-timeout 3 --max-time 8 -o /dev/null -w '%{http_code}')
if [[ "$(read_setting OVPM_HTTPS_ENABLED)" == 1 ]]; then
  scheme=https
  port=$(read_setting OVPM_HTTPS_PORT)
  port=${port:-443}
  # Local reachability only; no credentials are sent. Certificate identity is a separate check.
  curl_args+=(--insecure)
  warn "本机 HTTPS 健康检查不验证证书身份，请另行验证对外域名和证书链"
fi
code=$(curl "${curl_args[@]}" "${scheme}://127.0.0.1:${port}/api/health" 2>/dev/null) || code=000
if [[ "$code" == 200 ]]; then
  ok "${scheme} /api/health 返回 200，端口 ${port}"
else
  bad "${scheme} 健康检查失败，HTTP 状态 ${code}，端口 ${port}"
fi

# ---------- 6. 黑名单（可选） ----------
echo
echo "--- 黑名单（可选） ---"
if systemctl is-enabled --quiet openvpn-admin-blacklist.timer 2>/dev/null; then
  ok "blacklist.timer 已启用"
else
  warn "blacklist.timer 未启用"
fi
if command -v nft >/dev/null 2>&1 && nft list table inet ovpn_admin >/dev/null 2>&1; then
  ok "nftables ovpn_admin 表存在"
else
  warn "nftables ovpn_admin 表不存在"
fi

echo
echo "========== 结果: PASS=${PASS} FAIL=${FAIL} WARN=${WARN} =========="
if [[ "$FAIL" -gt 0 ]]; then
  echo -e "${RED}存在失败项，请检查上方 FAIL${NC}"
  exit 1
fi
echo -e "${GREEN}验收通过${NC}"
exit 0

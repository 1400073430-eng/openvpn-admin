#!/bin/bash
# ============================================================
# openvpn-admin 证书链路诊断（网页无法创建/同步证书时使用）
# 用法: sudo bash cert_pipeline_diag.sh
# ============================================================
set -u
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS=0; FAIL=0; WARN=0
ok()   { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS+1)); }
bad()  { echo -e "${RED}[FAIL]${NC} $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; WARN=$((WARN+1)); }

APP_USER="${OVPM_SERVICE_USER:-ovpn-admin}"
HELPER="/usr/local/sbin/ovpn-admin-priv"
WORKER="/usr/local/sbin/ovpn-admin-job-worker"
SPOOL="/var/spool/ovpn-admin"
EASY=""
for d in /etc/openvpn/server/easy-rsa /etc/openvpn/easy-rsa; do
  [[ -d "$d" ]] && EASY="$d" && break
done

echo "========== 证书链路诊断 =========="
echo "时间: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo

echo "--- 1. 依赖文件 ---"
[[ -x "$HELPER" ]] && ok "helper 可执行: $HELPER" || bad "helper 缺失: $HELPER"
[[ -x "$WORKER" ]] && ok "job worker 可执行: $WORKER" || bad "job worker 缺失: $WORKER"
[[ -d "$SPOOL" ]] && ok "spool 目录存在: $SPOOL" || bad "spool 目录缺失: $SPOOL"
if [[ -n "$EASY" ]]; then
  ok "EasyRSA 目录: $EASY"
  [[ -x "$EASY/easyrsa" ]] && ok "easyrsa 可执行" || bad "easyrsa 不可执行: $EASY/easyrsa"
  [[ -d "$EASY/pki" ]] && ok "pki 目录存在" || bad "pki 目录缺失"
  [[ -f "$EASY/pki/index.txt" ]] && ok "index.txt 存在" || warn "index.txt 不存在（新 PKI？）"
else
  bad "未找到 EasyRSA 目录"
fi

echo
echo "--- 2. helper 直接调用（root） ---"
if [[ -x "$HELPER" ]]; then
  if "$HELPER" easyrsa show-cert server >/tmp/ovpn-cert-diag.out 2>/tmp/ovpn-cert-diag.err; then
    ok "helper easyrsa show-cert server 成功"
  else
    bad "helper show-cert server 失败:"
    sed 's/^/    /' /tmp/ovpn-cert-diag.err | tail -10
  fi
  # 模拟一次 build-client-full 参数校验（不真正签发）：用非法名看是否走到 easyrsa
  if "$HELPER" easyrsa build-client-full 2>/tmp/ovpn-cert-diag.err; then
    warn "缺少客户端名却成功，参数校验异常"
  else
    grep -q "client name required" /tmp/ovpn-cert-diag.err \
      && ok "helper 参数白名单正常（client name required）" \
      || warn "helper 拒绝原因: $(head -1 /tmp/ovpn-cert-diag.err)"
  fi
fi

echo
echo "--- 3. 任务队列 ---"
if [[ -d "$SPOOL" ]]; then
  mode=$(stat -c '%a' "$SPOOL" 2>/dev/null || echo "?")
  grp=$(stat -c '%G' "$SPOOL" 2>/dev/null || echo "?")
  own=$(stat -c '%U' "$SPOOL" 2>/dev/null || echo "?")
  info="mode=${mode} owner=${own} group=${grp}"
  [[ "$grp" == "$APP_USER" || "$grp" == "ovpn-admin" ]] && ok "spool 属组正确 ($info)" || warn "spool 属组异常 ($info)"
  [[ "$mode" == "1730" || "$mode" == "1733" || "$mode" == "730" ]] && ok "spool 权限: $mode" || warn "spool 权限: $mode（期望 1730）"
  # 以服务用户写测试任务文件
  if id "$APP_USER" >/dev/null 2>&1; then
    testf="$SPOOL/.diag-write-test"
    if sudo -u "$APP_USER" bash -c "echo test > '$testf' 2>/dev/null"; then
      ok "服务用户可写 spool"
      rm -f "$testf"
    else
      bad "服务用户不可写 spool（创建客户端会超时）"
    fi
  fi
fi

echo
echo "--- 4. path unit / worker ---"
if systemctl is-active --quiet ovpn-admin-jobs.path 2>/dev/null; then
  ok "ovpn-admin-jobs.path 运行中"
else
  bad "ovpn-admin-jobs.path 未运行 → 任务不会被执行"
  systemctl status ovpn-admin-jobs.path --no-pager -l 2>/dev/null | head -15 | sed 's/^/    /'
fi
if systemctl is-enabled --quiet ovpn-admin-jobs.path 2>/dev/null; then
  ok "ovpn-admin-jobs.path 已开机启用"
else
  warn "ovpn-admin-jobs.path 未 enable"
fi
n=$(systemctl show ovpn-admin-jobs.service -p NRestarts --value 2>/dev/null || echo 0)
[[ "${n:-0}" -gt 10 ]] && warn "jobs.service NRestarts=$n" || ok "jobs.service NRestarts=${n:-0}"

echo
echo "--- 5. PKI ACL（网页同步需要读 index.txt / issued） ---"
if [[ -n "$EASY" && -d "$EASY/pki" ]]; then
  pki="$EASY/pki"
  if sudo -u "$APP_USER" test -r "$pki/index.txt" 2>/dev/null; then
    ok "$APP_USER 可读 index.txt（同步可用）"
  else
    bad "$APP_USER 不可读 index.txt → 网页同步/扫描会失败或空列表"
  fi
  if sudo -u "$APP_USER" test -r "$pki" 2>/dev/null; then
    ok "$APP_USER 可进入 pki 目录"
  else
    bad "$APP_USER 不可进入 pki 目录"
  fi
  if [[ -d "$pki/issued" ]]; then
    if sudo -u "$APP_USER" ls "$pki/issued" >/dev/null 2>&1; then
      ok "$APP_USER 可读 issued/"
    else
      bad "$APP_USER 不可读 issued/"
    fi
  fi
  if [[ -f "$pki/private/ca.key" ]]; then
    if sudo -u "$APP_USER" test -r "$pki/private/ca.key" 2>/dev/null; then
      warn "ca.key 对服务用户可读（应拒绝）"
    else
      ok "ca.key 对服务用户不可读（安全）"
    fi
  fi
fi

echo
echo "--- 6. Web 服务近期证书相关错误 ---"
if command -v journalctl >/dev/null 2>&1; then
  logs=$(journalctl -u openvpn-admin -n 200 --no-pager -o cat 2>/dev/null || true)
  hits=$(echo "$logs" | grep -E 'easyrsa|证书|job queue|job timeout|Permission|PKI|helper|import|scan' | tail -20)
  if [[ -n "$hits" ]]; then
    warn "发现相关日志："
    echo "$hits" | sed 's/^/    /'
  else
    ok "近 200 条日志无证书链路错误"
  fi
  # 最近一次 API 500
  echo "$logs" | grep -E '创建客户端|扫描 EasyRSA|导入|easyrsa 失败' | tail -10 | sed 's/^/    /'
fi

echo
echo "--- 7. 端到端：提交一个只读 show-cert 任务 ---"
if id "$APP_USER" >/dev/null 2>&1 && [[ -d "$SPOOL" ]] && [[ -x "$HELPER" ]]; then
  jid="diag-$$-$(date +%s)"
  job="$SPOOL/${jid}.job"
  res="$SPOOL/${jid}.result"
  if sudo -u "$APP_USER" python3 - <<PY
import json, os
from pathlib import Path
job = Path("${job}")
job.write_text(json.dumps({"argv": ["easyrsa", "show-cert", "server"]}), encoding="utf-8")
os.chmod(job, 0o640)
print("wrote", job)
PY
  then
    ok "任务已写入队列"
    # 手动触发 worker（不依赖 path unit 是否及时）
    /usr/local/sbin/ovpn-admin-job-worker >/dev/null 2>&1 || true
    if [[ -f "$res" ]]; then
      ok "收到 result: $(head -c 200 "$res")"
    else
      bad "5 秒内无 result（path unit / worker 未处理任务）"
    fi
    rm -f "$job" "$res"
  else
    bad "无法以服务用户写入任务"
  fi
fi

echo
echo "========== 结果: PASS=${PASS} FAIL=${FAIL} WARN=${WARN} =========="
if [[ "$FAIL" -gt 0 ]]; then
  echo -e "${RED}证书链路存在故障。建议执行：${NC}"
  echo "  sudo bash install.sh --fix-certs"
  echo "  sudo systemctl restart openvpn-admin"
  echo "  sudo bash cert_pipeline_diag.sh   # 复测"
  exit 1
fi
echo -e "${GREEN}证书链路看起来正常。若网页仍失败，请贴 journalctl -u openvpn-admin -n 50${NC}"
exit 0

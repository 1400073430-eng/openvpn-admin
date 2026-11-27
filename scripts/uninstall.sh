#!/usr/bin/env bash
# Complete destructive removal of OpenVPN Admin and the OpenVPN server configuration.
# Run on Ubuntu/Debian as root: sudo bash uninstall-openvpn-admin-complete.sh
set -Eeuo pipefail
umask 077
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { printf '[错误] %s\n' "$*" >&2; exit 1; }
uninstall_issues=0
mark_issue() {
  uninstall_issues=$((uninstall_issues + 1))
  log "[未完成] $*"
}
report_uninstall_result() {
  if [[ "$leftover" -ne 0 || "$uninstall_issues" -ne 0 ]]; then
    log "卸载未完全完成：检测到残留或失败步骤，请检查上方输出。"
    return 1
  fi
  log "已完成的检查未发现残留；缺少检查工具的项目未验证。共享 journal、外部备份及手动复制文件未清理。"
}
[[ ${EUID:-$(id -u)} -eq 0 ]] || die "请使用 root 运行：sudo bash $0"
command -v systemctl >/dev/null 2>&1 || die "未找到 systemctl；此脚本面向 Ubuntu/Debian 的 systemd 系统。"

cat <<'NOTICE'
============================================================
警告：彻底删除 OpenVPN Admin + OpenVPN 服务/配置
============================================================
将永久删除：
  - /opt/openvpn-admin 管理面板、数据库、项目数据及其中的证书
  - /etc/openvpn 下的服务配置、证书、客户端/服务器密钥
  - OpenVPN Admin 的 systemd 单元、root worker、任务队列和专用日志
  - nftables 中专用的 inet ovpn_admin 黑名单表及 inet ovpn_dualstack 双栈表
  - 双栈专用配置、开机服务及 /var/lib/ovpn-admin-dualstack 迁移备份
  - 明确命名为 ovpn-admin / ovpn-blacklist 的系统配置
  - apt 管理的 openvpn、easy-rsa 软件包（若已安装）

不会清空整个防火墙规则集，也不会删除其他应用的通用 DNS/TLS 证书。
不创建备份。现有 VPN 客户端将断开，之后无法再连接此服务器。
共享的 systemd journal 不会整体清空，以免删除其他服务的日志。
============================================================
NOTICE
read -r -p '如确认永久删除，请输入 DELETE-ALL-OPENVPN-DATA：' confirmation
[[ "$confirmation" == "DELETE-ALL-OPENVPN-DATA" ]] || die "确认文本不匹配，未执行删除。"

log "停止并禁用 OpenVPN / OpenVPN Admin 相关 systemd 单元"
mapfile -t units < <(
  {
    systemctl list-unit-files --no-legend --no-pager 2>/dev/null | awk '{print $1}'
    systemctl list-units --all --no-legend --no-pager 2>/dev/null | awk '{print $1}'
  } | grep -E '^(openvpn(@[^ ]*)?\.service|openvpn-server@[^ ]*\.service|openvpn-client@[^ ]*\.service|openvpn-admin[^ ]*\.(service|socket|path|timer|target)|ovpn-admin[^ ]*\.(service|socket|path|timer|target)|ovpn-blacklist[^ ]*\.(service|socket|path|timer|target))$' | sort -u || true
)
for unit in "${units[@]:-}"; do
  [[ -n "$unit" ]] || continue
  systemctl disable --now "$unit" >/dev/null 2>&1 || true
  systemctl stop "$unit" >/dev/null 2>&1 || true
done
for unit in openvpn.service openvpn@server.service openvpn-server@server.service; do
  systemctl disable --now "$unit" >/dev/null 2>&1 || true
done

log "删除 OpenVPN Admin 专用 systemd 单元与程序"
rm -f \
  /etc/systemd/system/openvpn-admin.service \
  /etc/systemd/system/openvpn-admin-job-worker.service \
  /etc/systemd/system/openvpn-admin-jobs.service \
  /etc/systemd/system/openvpn-admin-jobs.path \
  /etc/systemd/system/openvpn-admin-blacklist-sync.service \
  /etc/systemd/system/openvpn-admin-blacklist-sync.timer \
  /etc/systemd/system/ovpn-admin-priv.service \
  /etc/systemd/system/ovpn-admin-job-worker.service \
  /etc/systemd/system/ovpn-admin-jobs.service \
  /etc/systemd/system/ovpn-admin-jobs.path \
  /etc/systemd/system/ovpn-admin-blacklist-sync.service \
  /etc/systemd/system/ovpn-admin-blacklist-sync.timer \
  /usr/local/sbin/ovpn-admin-priv \
  /usr/local/sbin/ovpn-admin-job-worker \
  /usr/local/sbin/ovpn-admin-blacklist-sync \
  /usr/local/bin/openvpn-admin \
  /usr/local/bin/ovpn-admin \
  /usr/local/bin/ovpn-client-quick \
  /etc/sudoers.d/ovpn-admin \
  /etc/sudoers.d/openvpn-admin
find /etc/systemd/system -maxdepth 2 -type f \
  \( -name 'openvpn-admin*.service' -o -name 'openvpn-admin*.socket' -o -name 'openvpn-admin*.path' -o -name 'openvpn-admin*.timer' \
     -o -name 'ovpn-admin*.service' -o -name 'ovpn-admin*.socket' -o -name 'ovpn-admin*.path' -o -name 'ovpn-admin*.timer' \
     -o -name 'ovpn-blacklist*.service' -o -name 'ovpn-blacklist*.timer' \) -delete 2>/dev/null || true
if [[ -L /usr/local/bin/client-quick && "$(readlink /usr/local/bin/client-quick)" == /usr/local/bin/ovpn-client-quick ]]; then
  rm -f /usr/local/bin/client-quick
fi
systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true

log "删除 OpenVPN Admin 项目、队列、日志和专用运行目录"
rm -rf -- /opt/openvpn-admin /var/spool/ovpn-admin /var/lib/openvpn-admin /var/cache/openvpn-admin /var/log/openvpn-admin /run/ovpn-admin /run/openvpn-admin
find /tmp -maxdepth 1 -mindepth 1 \( -name 'openvpn-admin-*' -o -name 'ovpn-admin-*' \) -exec rm -rf -- {} + 2>/dev/null || true
find /var/log -maxdepth 1 -type f \
  \( -name 'openvpn-admin.log' -o -name 'openvpn-admin-install.log' -o -name 'openvpn-admin-uninstall.log' \
     -o -name 'ovpn-admin.log' -o -name 'ovpn-admin-install.log' -o -name 'ovpn-admin-uninstall.log' \) -delete 2>/dev/null || true

log "删除 OpenVPN 服务配置、证书、密钥和运行数据"
# Standard OpenVPN-specific paths only; do not delete generic /etc/ssl or other apps' PKI.
rm -rf -- /etc/openvpn /var/lib/openvpn /var/log/openvpn /run/openvpn /var/run/openvpn /etc/easy-rsa /opt/easy-rsa /usr/local/share/easy-rsa

log "删除域名黑名单与双栈专用配置"
rm -f /etc/sysctl.d/90-ovpn-admin-dualstack.conf /etc/openvpn-admin/dualstack.nft /etc/openvpn-admin/privileged.env
rmdir /etc/openvpn-admin 2>/dev/null || true
rm -rf -- /var/lib/ovpn-admin-dualstack
rm -f /etc/dnsmasq.d/ovpn-blacklist.conf /etc/dnsmasq.d/.ovpn-blacklist.conf.tmp
rm -rf -- /var/lib/ovpn-blacklist
# Remove only the dedicated persistent table block from common nftables config files,
# plus explicitly named product-owned snippets. Preserve unrelated firewall rules.
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY_NFT_CLEANUP'
from pathlib import Path
import re
candidates = [Path('/etc/nftables.conf')]
for base in (Path('/etc/nftables.d'), Path('/etc/nftables')):
    if base.is_dir():
        candidates.extend(p for p in base.iterdir() if p.is_file() and p.suffix in {'.conf', '.nft'})
for path in candidates:
    try:
        original = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        continue
    lines = original.splitlines(keepends=True)
    out, skipping, depth = [], False, 0
    changed = False
    for line in lines:
        if not skipping and re.match(r'^\s*table\s+inet\s+ovpn_admin\s*\{', line):
            skipping = True
            depth = 0
            changed = True
        if skipping:
            visible = line.split('#', 1)[0]
            depth += visible.count('{') - visible.count('}')
            if depth <= 0:
                skipping = False
            continue
        if re.match(r'^\s*include\s+', line) and re.search(r'(?i)(?:ovpn-admin|ovpn_admin|ovpn-blacklist)[^\s]*\.(?:nft|conf)', line):
            changed = True
            continue
        out.append(line)
    if changed:
        path.write_text(''.join(out), encoding='utf-8')
        print(f'[信息] 已清理专用 nftables 配置片段：{path}')
PY_NFT_CLEANUP
fi
for dir in /etc/nftables.d /etc/nftables; do
  [[ -d "$dir" ]] || continue
  find "$dir" -maxdepth 1 -type f \
    \( -iname '*ovpn-admin*.nft' -o -iname '*ovpn_admin*.nft' -o -iname '*ovpn-blacklist*.nft' \
       -o -iname '*ovpn-admin*.conf' -o -iname '*ovpn_admin*.conf' -o -iname '*ovpn-blacklist*.conf' \) -delete 2>/dev/null || true
done

if command -v nft >/dev/null 2>&1; then
  nft delete table inet ovpn_admin >/dev/null 2>&1 || true
  nft delete table inet ovpn_dualstack >/dev/null 2>&1 || true
fi

log "删除 OpenVPN Admin 专用系统账户（若存在）"
if getent passwd ovpn-admin >/dev/null 2>&1; then userdel ovpn-admin >/dev/null 2>&1 || mark_issue "系统用户 ovpn-admin 删除失败"; fi
if getent group ovpn-admin >/dev/null 2>&1; then groupdel ovpn-admin >/dev/null 2>&1 || mark_issue "系统组 ovpn-admin 删除失败"; fi

log "卸载 OpenVPN / Easy-RSA 软件包（若由 apt 安装）"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get purge -y openvpn easy-rsa || mark_issue "apt purge 未完全成功，请检查软件包状态。"
else
  mark_issue "未找到 apt-get，未验证软件包卸载状态"
fi

systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
log "执行完成，检查常见残留路径"
leftover=0
for path in /opt/openvpn-admin /etc/openvpn /var/spool/ovpn-admin /usr/local/sbin/ovpn-admin-priv /usr/local/sbin/ovpn-admin-job-worker; do
  if [[ -e "$path" ]]; then printf '[残留] %s\n' "$path"; leftover=1; fi
done
if command -v nft >/dev/null 2>&1 && nft list table inet ovpn_admin >/dev/null 2>&1; then
  printf '[残留] nft table inet ovpn_admin\n'; leftover=1
fi
if command -v nft >/dev/null 2>&1 && nft list table inet ovpn_dualstack >/dev/null 2>&1; then
  printf '[残留] nft table inet ovpn_dualstack\n'; leftover=1
fi
for unit in "${units[@]:-}"; do
  [[ -n "$unit" ]] || continue
  if systemctl is-active --quiet "$unit"; then
    printf '[残留] 仍在运行: %s\n' "$unit"; leftover=1
  fi
done
report_uninstall_result

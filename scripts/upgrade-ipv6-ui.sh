#!/usr/bin/env bash
# Retired: settings now depends on the complete authentication and network release.
set -euo pipefail
printf '%s\n' '此局部升级入口已停用：仅覆盖 IPv6 文件会造成新旧模块混用。' >&2
printf '%s\n' '请从最新完整源码目录执行：sudo bash scripts/install.sh --repair' >&2
printf '%s\n' '完整修复可能重启 VPN，请保留独立 SSH 会话并安排维护时间。' >&2
exit 2

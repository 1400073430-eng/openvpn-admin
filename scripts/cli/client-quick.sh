#!/bin/bash
# ============================================================
# OpenVPN 客户端快速管理（网页不可用时的备用通道）
# 系统命令: ovpn-client-quick
# ============================================================
#   sudo ovpn-client-quick list
#   sudo ovpn-client-quick add <名称>
#   sudo ovpn-client-quick revoke <名称>
#   sudo ovpn-client-quick download <名称>
#   sudo ovpn-client-quick status
#   sudo ovpn-client-quick sync-hint
# ============================================================
set -e
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

detect_paths() {
    if [[ -n "${OVPM_EASYRSA_DIR:-}" && -n "${OVPM_OPENVPN_DIR:-}" ]]; then
        EASYRSA_DIR="${OVPM_EASYRSA_DIR}"; OPENVPN_DIR="${OVPM_OPENVPN_DIR}"
    elif [[ -x /etc/openvpn/server/easy-rsa/easyrsa || -f /etc/openvpn/server/client-common.txt ]]; then
        EASYRSA_DIR="/etc/openvpn/server/easy-rsa"; OPENVPN_DIR="/etc/openvpn/server"
    elif [[ -x /etc/openvpn/easy-rsa/easyrsa || -f /etc/openvpn/server.conf ]]; then
        EASYRSA_DIR="/etc/openvpn/easy-rsa"; OPENVPN_DIR="/etc/openvpn"
    else
        EASYRSA_DIR="/etc/openvpn/server/easy-rsa"; OPENVPN_DIR="/etc/openvpn/server"
    fi
    CLIENT_COMMON="${OVPM_CLIENT_COMMON:-${OPENVPN_DIR}/client-common.txt}"
    INDEX="${EASYRSA_DIR}/pki/index.txt"
    INLINE_DIR="${EASYRSA_DIR}/pki/inline/private"
    CRL_PEM="${OVPM_CRL:-${OPENVPN_DIR}/crl.pem}"
    STATUS_LOG="${OVPM_STATUS_LOG:-/var/log/openvpn/openvpn-status.log}"
    if [[ ! -f "$STATUS_LOG" && -f /run/openvpn-server/status-server.log ]]; then
        STATUS_LOG="/run/openvpn-server/status-server.log"
    fi
    CERT_DAYS="${OVPM_CERT_DAYS:-3650}"
}
detect_paths
log_ok()   { echo -e "${GREEN}【成功】${NC} $1"; }
log_fail() { echo -e "${RED}【失败】${NC} $1"; exit 1; }
log_info() { echo -e "${YELLOW}[信息]${NC} $1"; }

check_name() {
    local name="$1"
    [[ -z "$name" ]] && log_fail "客户端名不能为空"
    [[ "$name" =~ ^[a-zA-Z0-9_\-]{2,32}$ ]] || log_fail "客户端名不合法（2-32位字母数字下划线中划线）"
}
require_root() { [[ $EUID -eq 0 ]] || log_fail "请使用 root 运行: sudo $0"; }
require_nyr() {
    [[ -x "${EASYRSA_DIR}/easyrsa" ]] || log_fail "未找到 EasyRSA: ${EASYRSA_DIR}/easyrsa"
    [[ -f "$CLIENT_COMMON" ]] || log_fail "未找到客户端模板: ${CLIENT_COMMON}"
}
build_ovpn() {
    local client="$1" out="${2:-./${client}.ovpn}" inline="${INLINE_DIR}/${client}.inline"
    [[ -f "$inline" ]] || log_fail "未找到 inline 配置: ${inline}"
    grep -vh '^#' "$CLIENT_COMMON" "$inline" > "$out"
    log_ok "已生成: $out"
}

cmd_list() {
    if [[ ! -f "$INDEX" ]]; then
        log_info "暂无客户端记录（${INDEX} 不存在）"; return
    fi
    echo "===== 客户端列表 ====="
    echo "状态 | 名称 | 序列"
    tail -n +2 "$INDEX" | while IFS=$'\t' read -r flag _e _r serial _u cn; do
        name=$(echo "$cn" | grep -oE 'CN=[^/]*' | head -1 | cut -d= -f2)
        [[ -z "$name" ]] && continue
        case "$flag" in V) st="启用" ;; R) st="吊销" ;; E) st="过期" ;; *) st="$flag" ;; esac
        echo "$st | $name | $serial"
    done
}
cmd_add() {
    local client="$1"
    check_name "$client"; require_root; require_nyr
    [[ -f "${EASYRSA_DIR}/pki/issued/${client}.crt" ]] && log_fail "客户端已存在: $client"
    log_info "正在签发证书..."
    cd "$EASYRSA_DIR"
    ./easyrsa --batch --days="$CERT_DAYS" build-client-full "$client" nopass
    build_ovpn "$client" "$(pwd)/${client}.ovpn"
    log_ok "客户端 $client 已创建"
}
cmd_revoke() {
    local client="$1"
    check_name "$client"; require_root; require_nyr
    [[ -f "${EASYRSA_DIR}/pki/issued/${client}.crt" ]] || log_fail "客户端不存在: $client"
    log_info "正在吊销..."
    cd "$EASYRSA_DIR"
    ./easyrsa --batch revoke "$client"
    ./easyrsa --batch --days="$CERT_DAYS" gen-crl
    rm -f "${CRL_PEM}" "${EASYRSA_DIR}/pki/reqs/${client}.req" "${EASYRSA_DIR}/pki/private/${client}.key"
    cp "${EASYRSA_DIR}/pki/crl.pem" "${CRL_PEM}"
    chown nobody:nogroup "${CRL_PEM}" 2>/dev/null || true
    log_ok "客户端 $client 已吊销"
}
cmd_download() {
    local client="$1"
    check_name "$client"; require_nyr
    [[ -f "${INLINE_DIR}/${client}.inline" ]] || log_fail "无 inline 配置，客户端可能已吊销: $client"
    build_ovpn "$client" "$(pwd)/${client}.ovpn"
}
cmd_status() {
    if [[ ! -f "$STATUS_LOG" || ! -s "$STATUS_LOG" ]]; then
        log_info "暂无在线客户端"; return
    fi
    echo "===== 在线客户端 ====="
    if grep -q '^CLIENT_LIST' "$STATUS_LOG" 2>/dev/null; then
        awk -F'\t' '$1=="CLIENT_LIST" {printf "%s | %s | %s\n",$2,$3,$4}' "$STATUS_LOG"
    else
        awk '/OpenVPN CLIENT LIST/,/ROUTING TABLE/' "$STATUS_LOG" | tail -n +4
    fi
}
cmd_sync_hint() {
    echo "===== 网页证书功能依赖检查 ====="
    echo "EasyRSA 目录: ${EASYRSA_DIR}"
    echo "OpenVPN 目录: ${OPENVPN_DIR}"
    local ok=1
    if [[ -x "${EASYRSA_DIR}/easyrsa" ]]; then log_ok "EasyRSA 可执行"; else log_info "缺少 EasyRSA"; ok=0; fi
    if [[ -f "$CLIENT_COMMON" ]]; then log_ok "客户端模板"; else log_info "缺少客户端模板"; ok=0; fi
    if [[ -f "$INDEX" ]]; then log_ok "PKI 索引"; else log_info "缺少 PKI 索引"; fi
    local helper="${OVPM_PRIV_HELPER:-/usr/local/sbin/ovpn-admin-priv}"
    if [[ -x "$helper" ]]; then log_ok "root helper"; else log_info "缺少 root helper（网页签发会失败）"; ok=0; fi
    if [[ -x /usr/local/sbin/ovpn-admin-job-worker && -f /etc/systemd/system/ovpn-admin-jobs.path ]]; then
        log_ok "root 任务队列"
    else
        log_info "缺少 root 任务队列"; ok=0
    fi
    if [[ "$ok" -eq 1 ]]; then
        log_ok "网页证书功能依赖齐备"
    else
        log_info "请执行: sudo bash install.sh --repair"
        return 1
    fi
}

ACTION="${1:-list}"
case "$ACTION" in
    list) cmd_list ;;
    add) cmd_add "${2:-}" ;;
    revoke) cmd_revoke "${2:-}" ;;
    download|export) cmd_download "${2:-}" ;;
    status) cmd_status ;;
    sync-hint|check) cmd_sync_hint ;;
    *)
        echo "用法: ovpn-client-quick {list|add|revoke|download|status|sync-hint}"
        exit 1
        ;;
esac

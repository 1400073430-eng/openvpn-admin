#!/bin/bash
# ============================================================
# OpenVPN 管理平台 · Ubuntu 一键安装脚本
# ============================================================
# 功能：
#   1. 检测 Ubuntu 系统版本
#   2. 检测 80 端口占用，可更换端口
#   3. 自动获取内网 IP，支持手动覆盖
#   4. 安装 OpenVPN / EasyRSA / nftables / Python / FastAPI
#   5. 初始化数据库、根证书、基础配置
#   6. 配置防火墙
#   7. 输出访问地址与初始账号密码
#   8. 清理旧版入口并部署当前 backend 包
#   9. 提供登录、数据库与证书诊断
#  10. 提供证书链路修复与诊断
# ============================================================
# 用法：
#   sudo bash install.sh              # 全新安装 / 覆盖升级
#   sudo bash install.sh --repair     # 仅修复：清旧入口、重部署、重写 systemd、校验登录/证书
#   sudo bash install.sh --diagnose   # 仅诊断：数据库、登录 API、证书链路（不改代码）
#   sudo bash install.sh --fix-certs  # 仅修复证书链路（systemd 提权 + PKI 权限）
# ============================================================

set -e  # 任一命令失败则终止（依赖安装失败等关键步骤）
set -o pipefail

# ------------------- 颜色输出定义 -------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # 无颜色

# ------------------- 全局变量 -------------------
APP_NAME="openvpn-admin"
INSTALL_DIR="/opt/${APP_NAME}"
LOG_FILE="/var/log/${APP_NAME}-install.log"
WEB_PORT=80
WEB_HOST="0.0.0.0"
SERVER_IP=""
DEFAULT_ADMIN_USER="admin"
DEFAULT_ADMIN_PASSWORD=""
APP_USER="ovpn-admin"
APP_GROUP="ovpn-admin"
PRIV_HELPER="/usr/local/sbin/ovpn-admin-priv"
PRIV_ENV="/etc/openvpn-admin/privileged.env"
JWT_SECRET=""
VPN_PORT=1194

# 运行模式：install | repair | diagnose | fix-certs
RUN_MODE="install"

# 脚本自身目录（用于定位内嵌 Nyr 安装器）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================================
# 工具函数：执行反馈
# ============================================================

# 成功提示
log_success() {
    echo -e "${GREEN}【成功】${NC} $1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [成功] $1" >> "$LOG_FILE"
}

# 失败提示（带原因）
log_fail() {
    echo -e "${RED}【失败】${NC} $1"
    echo -e "${RED}       原因：${NC} $2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [失败] $1 | 原因: $2" >> "$LOG_FILE"
}

# 普通信息
log_info() {
    echo -e "${BLUE}[信息]${NC} $1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [信息] $1" >> "$LOG_FILE"
}

# 警告
log_warn() {
    echo -e "${YELLOW}[警告]${NC} $1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [警告] $1" >> "$LOG_FILE"
}

# 执行命令并检查结果
# 参数: 命令描述 | 命令...
# ============================================================
# 第0步：初始化日志、检查 root
# ============================================================
init_install() {
    # 解析命令行模式
    for arg in "$@"; do
        case "$arg" in
            --repair|-r)     RUN_MODE="repair" ;;
            --diagnose|-d)   RUN_MODE="diagnose" ;;
            --install|-i)    RUN_MODE="install" ;;
            --fix-certs|-c)  RUN_MODE="fix-certs" ;;
            --help|-h)
                echo "用法: sudo bash install.sh [--install|--repair|--diagnose|--fix-certs]"
                echo "  --install    全新安装 / 覆盖升级（默认）"
                echo "  --repair     修复启动/登录/证书（清旧入口、重部署、重写 systemd、修 PKI 权限）"
                echo "  --diagnose   仅诊断登录、数据库、证书链路（不改动代码）"
                echo "  --fix-certs  仅修复证书链路（NoNewPrivileges / PKI 权限 / root 任务队列）"
                exit 0
                ;;
        esac
    done

    local mode_label="全新安装 / 覆盖升级"
    [[ "$RUN_MODE" == "repair" ]] && mode_label="修复模式（清旧入口 / 重部署 / 重写 systemd / 证书）"
    [[ "$RUN_MODE" == "diagnose" ]] && mode_label="诊断模式（登录 / 数据库 / 证书链路）"
    [[ "$RUN_MODE" == "fix-certs" ]] && mode_label="证书修复模式（root 任务队列 + PKI 权限）"

    echo "============================================================"
    echo "       OpenVPN 管理平台一键安装脚本"
    echo "       模式: ${mode_label}"
    echo "============================================================"
    echo ""

    # 检查是否 root
    if [[ $EUID -ne 0 ]]; then
        log_fail "权限检查" "请使用 root 权限运行: sudo bash install.sh"
        exit 1
    fi
    log_success "权限检查（root）"

    # 初始化日志文件
    touch "$LOG_FILE"
    chmod 644 "$LOG_FILE"
    log_success "日志文件初始化: ${LOG_FILE}"
}

# ============================================================
# 第1步：检测 Ubuntu 系统版本
# ============================================================
check_ubuntu() {
    log_info "检测系统版本..."

    if [[ ! -f /etc/os-release ]]; then
        log_fail "系统检测" "无法读取 /etc/os-release，非标准 Linux 系统"
        exit 1
    fi

    source /etc/os-release

    if [[ "$ID" != "ubuntu" ]]; then
        log_fail "系统检测" "当前系统为 ${ID:-未知}，本脚本仅支持 Ubuntu"
        exit 1
    fi
    log_success "系统为 Ubuntu ${VERSION_ID}"

    # 建议 20.04 及以上
    major_version=$(echo "$VERSION_ID" | cut -d. -f1)
    if [[ "$major_version" -lt 20 ]]; then
        log_warn "Ubuntu ${VERSION_ID} 版本较低，建议升级到 20.04 或更高版本"
    fi
}

# ============================================================
# 第2步：检测端口占用
# ============================================================
check_port() {
    log_info "检测 Web 端口 ${WEB_PORT} 占用情况..."

    local port_in_use=false

    # 方法1: ss 命令（-A listen 严格匹配监听端口）
    if command -v ss &>/dev/null; then
        if ss -tulnp 2>/dev/null | grep -E ":${WEB_PORT}\s" | grep -qi "LISTEN\|UNCONN"; then
            port_in_use=true
        fi
    # 方法2: netstat
    elif command -v netstat &>/dev/null; then
        if netstat -tuln 2>/dev/null | grep -E ":${WEB_PORT}\s" | grep -qi "LISTEN\|UNCONN"; then
            port_in_use=true
        fi
    # 方法3: /dev/tcp 探测
    else
        if timeout 1 bash -c "echo >/dev/tcp/127.0.0.1/${WEB_PORT}" 2>/dev/null; then
            port_in_use=true
        fi
    fi

    if [[ "$port_in_use" == true ]]; then
        log_warn "端口 ${WEB_PORT} 已被占用"
        echo ""
        read -p "是否更换端口？(y/n，默认 y): " change_port
        if [[ "$change_port" != "n" && "$change_port" != "N" ]]; then
            while true; do
                if ! IFS= read -r -p "请输入新的 Web 端口 (1-65535): " new_port; then
                    log_fail "端口配置" "未收到端口输入（stdin 已关闭），终止安装，避免无限循环"
                    exit 1
                fi
                # 校验是否为数字
                if [[ "$new_port" =~ ^[0-9]+$ ]] && [[ "$new_port" -ge 1 ]] && [[ "$new_port" -le 65535 ]]; then
                    # 检查新端口是否也占用
                    if command -v ss &>/dev/null && ss -tulnp | grep -q ":${new_port} "; then
                        echo "端口 ${new_port} 也被占用，请重新选择"
                    else
                        WEB_PORT="$new_port"
                        log_success "Web 端口设置为 ${WEB_PORT}"
                        break
                    fi
                else
                    echo "请输入有效的端口号 (1-65535)"
                fi
            done
        else
            log_fail "端口检测" "端口 ${WEB_PORT} 被占用且用户选择不更换"
            exit 1
        fi
    else
        log_success "端口 ${WEB_PORT} 可用"
    fi
}

# ============================================================
# 第3步：获取服务器 IP
# ============================================================
get_server_ip() {
    log_info "自动获取服务器内网 IP..."

    # 自动获取内网 IP（通过默认路由）
    auto_ip=$(ip route get 8.8.8.8 2>/dev/null | grep -oP 'src \K\S+' | head -1)

    if [[ -z "$auto_ip" ]]; then
        # 备用方法
        auto_ip=$(hostname -I | awk '{print $1}')
    fi

    if [[ -n "$auto_ip" ]]; then
        log_success "自动获取到 IP: ${auto_ip}"
    else
        log_warn "自动获取 IP 失败"
        auto_ip=""
    fi

    echo ""
    read -p "使用该 IP 作为访问地址？(直接回车使用 ${auto_ip:-手动输入}, 或输入其他 IP): " user_ip

    if [[ -n "$user_ip" ]]; then
        # 校验 IP 格式
        if [[ "$user_ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
            SERVER_IP="$user_ip"
            log_success "使用手动输入的 IP: ${SERVER_IP}"
        else
            log_warn "输入的 IP 格式不正确，使用自动获取结果"
            SERVER_IP="$auto_ip"
        fi
    else
        SERVER_IP="$auto_ip"
    fi

    if [[ -z "$SERVER_IP" ]]; then
        log_fail "IP 获取" "无法确定服务器 IP，请手动指定"
        exit 1
    fi
}

# ============================================================
# 第4步：安装系统依赖
# ============================================================
install_system_deps() {
    log_info "更新软件包索引..."
    if ! apt-get update >> "$LOG_FILE" 2>&1; then
        log_fail "软件包索引更新" "apt-get update 失败，请检查网络或软件源"
        exit 1
    fi
    log_success "软件包索引更新"

    log_info "安装基础依赖（OpenVPN / EasyRSA / nftables / Python 等）..."
    local packages=(
        openvpn
        easy-rsa
        nftables
        python3
        python3-pip
        python3-venv
        iptables
        curl
        wget
        unzip
        acl
    )

    if ! apt-get install -y "${packages[@]}" >> "$LOG_FILE" 2>&1; then
        log_fail "系统依赖安装" "apt-get install 失败，详见 ${LOG_FILE}。请检查网络后重试"
        exit 1
    fi
    log_success "系统依赖安装（${#packages[@]} 个包）"

    # 验证关键命令
    local cmds=(openvpn python3 nft)
    for cmd in "${cmds[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            log_success "命令可用: ${cmd}"
        else
            log_fail "命令检查" "${cmd} 未正确安装"
            exit 1
        fi
    done
}

# ============================================================
# 第5步：创建项目目录并复制文件
# ============================================================
deploy_project() {
    log_info "部署项目文件到 ${INSTALL_DIR} ..."

    mkdir -p "$INSTALL_DIR"
    mkdir -p "${INSTALL_DIR}/data"
    mkdir -p "${INSTALL_DIR}/certs"
    mkdir -p "${INSTALL_DIR}/logs"

    # 复制当前脚本所在目录的项目文件
    local script_dir
    script_dir=$(cd "$(dirname "$0")/.." && pwd)

    if [[ ! -f "${script_dir}/backend/main.py" ]]; then
        log_fail "项目文件检查" "未找到 backend/main.py，请确保脚本位于项目 scripts/ 目录"
        exit 1
    fi

    # 复制后端、前端、配置
    # 清理早期版本的顶层入口，避免 server.py/storage.py 与当前 backend 包混用。
    # 这是 ModuleNotFoundError: No module named 'security' 的根因。
    if [[ -f "${INSTALL_DIR}/server.py" || -f "${INSTALL_DIR}/storage.py" || -f "${INSTALL_DIR}/security.py" ]]; then
        log_info "检测到旧版顶层入口（server.py/storage.py/security.py），清理中..."
    fi
    rm -f "${INSTALL_DIR}/server.py" "${INSTALL_DIR}/storage.py" "${INSTALL_DIR}/security.py" 2>/dev/null || true
    rm -rf "${INSTALL_DIR}/__pycache__" "${INSTALL_DIR}/routers" "${INSTALL_DIR}/services" 2>/dev/null || true
    cp -r "${script_dir}/backend" "$INSTALL_DIR/"
    cp -r "${script_dir}/frontend" "$INSTALL_DIR/"
    cp "${script_dir}/requirements.txt" "$INSTALL_DIR/"
    cp "${script_dir}/CHANGELOG.md" "$INSTALL_DIR/"
    [[ -d "${script_dir}/docs" ]] && cp -r "${script_dir}/docs" "$INSTALL_DIR/"
    [[ -f "${script_dir}/README.md" ]] && cp "${script_dir}/README.md" "$INSTALL_DIR/" 2>/dev/null || true

    log_success "项目文件部署完成（旧入口已清理，backend 包已就位）"
}

# ============================================================
# 第5b步：部署 CLI 快捷工具 ovpn-client-quick
# ============================================================
deploy_cli_tools() {
    log_info "部署 CLI 快捷工具 ovpn-client-quick ..."

    mkdir -p "${INSTALL_DIR}/scripts"
    install -m 0755 "${SCRIPT_DIR}/cli/client-quick.sh" "${INSTALL_DIR}/scripts/client-quick.sh"

    chmod 0755 "${INSTALL_DIR}/scripts/client-quick.sh"
    install -m 0755 "${INSTALL_DIR}/scripts/client-quick.sh" /usr/local/bin/ovpn-client-quick
    ln -sfn /usr/local/bin/ovpn-client-quick /usr/local/bin/client-quick 2>/dev/null || true
    log_success "CLI 已安装: ovpn-client-quick（list/add/revoke/download/status/sync-hint）"
}

# ============================================================
# 第6步：创建 Python 虚拟环境并安装依赖
# ============================================================
install_python_deps() {
    log_info "创建 Python 虚拟环境..."

    if ! python3 -m venv "${INSTALL_DIR}/venv" >> "$LOG_FILE" 2>&1; then
        log_fail "虚拟环境创建" "python3 -m venv 失败"
        exit 1
    fi
    log_success "虚拟环境创建: ${INSTALL_DIR}/venv"

    log_info "安装 Python 依赖（FastAPI 等）..."
    if ! "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip >> "$LOG_FILE" 2>&1; then
        log_warn "pip 升级失败，继续安装依赖"
    fi

    if ! "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" >> "$LOG_FILE" 2>&1; then
        log_fail "Python 依赖安装" "pip install 失败，请检查网络或 requirements.txt"
        exit 1
    fi
    log_success "Python 依赖安装完成"
}

# ============================================================
# 第7步：安装 OpenVPN（优先 Nyr/openvpn-install.sh）
# ============================================================
setup_openvpn() {
    local nyr_conf="/etc/openvpn/server/server.conf"
    local nyr_easyrsa="/etc/openvpn/server/easy-rsa/easyrsa"
    local classic_conf="/etc/openvpn/server.conf"
    local candidate load_state

    # 发布级原则：只要服务器上已经存在可解析的 OpenVPN 实例 + 配置，
    # 就绝不能再次运行内置 Nyr 安装器。否则 classic 布局会被误判为未安装，
    # 进而覆盖现有 VPN 配置。
    for candidate in openvpn-server@server openvpn@server openvpn; do
        load_state=$(systemctl show "$candidate" -p LoadState --value 2>/dev/null || true)
        if [[ "$load_state" != "not-found" && -n "$load_state" ]]; then
            if [[ -f "$nyr_conf" || -f "$classic_conf" ]]; then
                log_success "检测到已有 OpenVPN 服务 ${candidate}，保留现有 OpenVPN 安装"
                return 0
            fi
        fi
    done

    if [[ -f "$nyr_conf" && -x "$nyr_easyrsa" ]]; then
        log_success "检测到 Nyr/openvpn-install 已安装，跳过 OpenVPN 初始化"
        return 0
    fi

    log_info "未检测到 OpenVPN，使用内置 Nyr/openvpn-install 安装器"
    local nyr_installer
    nyr_installer=$(mktemp /tmp/openvpn-install.XXXXXX.sh)
    chmod 700 "$nyr_installer"
    cp "${SCRIPT_DIR}/vendor/openvpn-install.sh" "$nyr_installer"

    echo ""
    log_info "即将运行内置 Nyr OpenVPN 安装脚本（交互式）"
    echo "  安装器会询问 IP/协议/端口/DNS/客户端名等，按提示操作即可。"
    echo "  完成后本脚本会继续安装 Web 管理平台。"
    echo ""
    read -n1 -r -p "按任意键继续 OpenVPN 安装 ..."

    local rc=0
    bash "$nyr_installer" 2>&1 | tee -a "$LOG_FILE" || rc=${PIPESTATUS[0]}
    rm -f "$nyr_installer"
    if [[ "$rc" -ne 0 ]]; then
        log_fail "OpenVPN 安装" "内置安装器执行失败，详见 ${LOG_FILE}"
        exit 1
    fi

    if [[ ! -f "$nyr_conf" ]]; then
        log_fail "OpenVPN 安装" "安装结束后仍未找到 ${nyr_conf}"
        exit 1
    fi
    log_success "OpenVPN（Nyr）安装完成"
}

# ============================================================
# 第8步：生成 OpenVPN 服务器配置
# ============================================================
setup_openvpn_config() {
    # 若已是 Nyr 布局，只补 status 日志（在线监控需要），不覆盖原配置
    # Ubuntu openvpn-server@ 默认写 /run/openvpn-server/status-server.log（600 root）
    if [[ -f /etc/openvpn/server/server.conf ]]; then
        log_info "检测到 Nyr 布局 server.conf，补写 status 日志..."
        # 清理 v4.2 早期版本遗留的强制 DNS；不改动用户其他 DNS。
        sed -i '/^[[:space:]]*push[[:space:]]*"dhcp-option DNS 10\.8\.0\.1"[[:space:]]*$/d' /etc/openvpn/server/server.conf
        local status_file="/run/openvpn-server/status-server.log"
        mkdir -p /run/openvpn-server /var/log/openvpn
        chmod 755 /run/openvpn-server /var/log/openvpn 2>/dev/null || true

        if ! grep -qE '^\s*status\s+' /etc/openvpn/server/server.conf; then
            echo "status ${status_file} 5" >> /etc/openvpn/server/server.conf
            echo 'status-version 2' >> /etc/openvpn/server/server.conf
            log_success "已添加 status → ${status_file}"
        else
            log_success "status 日志已存在: $(grep -E '^\s*status\s+' /etc/openvpn/server/server.conf | head -1)"
        fi
        # OpenVPN refresh interval is the second argument of status, not status-update.
        if grep -qE '^[[:space:]]*status-update[[:space:]]' /etc/openvpn/server/server.conf; then
            sed -i '/^[[:space:]]*status-update[[:space:]]/d' /etc/openvpn/server/server.conf
            log_warn "已移除无效的 status-update 指令；刷新间隔由 status 文件路径后的参数指定"
        fi
        # OpenVPN 2.5+：仅在未配置 data-ciphers 时补充现代密码套件；
        # 不强制恢复旧 BF-CBC，也不覆盖用户已有 cipher/data-ciphers 配置。
        if ! grep -qE '^\s*data-ciphers([[:space:]]|$)' /etc/openvpn/server/server.conf; then
            echo 'data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305' >> /etc/openvpn/server/server.conf
            log_success "已补充现代 data-ciphers（不启用旧 BF-CBC）"
        fi
        return 0
    fi

    log_info "生成 OpenVPN 服务器配置..."

    # 不强制修改客户端 DNS；仅清理历史版本遗留的固定 DNS 推送行。
    for _ovpn_conf_cleanup in /etc/openvpn/server/server.conf /etc/openvpn/server.conf; do
        if [[ -f "$_ovpn_conf_cleanup" ]]; then
            sed -i '/^[[:space:]]*push[[:space:]]*"dhcp-option DNS 10\.8\.0\.1"[[:space:]]*$/d' "$_ovpn_conf_cleanup"
        fi
    done

    cat > /etc/openvpn/server.conf << EOF
# ============================================
# OpenVPN 服务器配置（由安装脚本生成）
# ============================================
port ${VPN_PORT}
proto udp
dev tun

# 证书路径
ca /etc/openvpn/ca.crt
cert /etc/openvpn/server.crt
key /etc/openvpn/server.key
dh none
ecdh-curve prime256v1

# CRL（吊销列表）
crl-verify /etc/openvpn/crl.pem

# 虚拟网段
server 10.8.0.0 255.255.255.0
ifconfig-pool-persist /var/log/openvpn/ipp.txt

# DNS 不由 域名黑名单强制接管；保留系统/安装时选择的客户端 DNS。
push "redirect-gateway def1 bypass-dhcp"

# 状态日志（管理平台解析此文件）
status /run/openvpn-server/status-server.log 5
status-version 2
log-append /var/log/openvpn/openvpn.log

# 并发连接上限
max-clients 100

# 压缩与加密
cipher AES-256-GCM
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
auth SHA256
persist-key
persist-tun

# 日志详细级别
verb 3
mute 20
EOF

    # 创建日志目录
    mkdir -p /var/log/openvpn
    chmod 755 /var/log/openvpn

    # 启用 IP 转发
    if ! grep -q "net.ipv4.ip_forward = 1" /etc/sysctl.conf; then
        echo "net.ipv4.ip_forward = 1" >> /etc/sysctl.conf
        sysctl -p >> "$LOG_FILE" 2>&1
    fi
    log_success "IP 转发已启用"

    # 配置 NAT（让 VPN 客户端可上网）
    # 获取主网卡
    local main_iface
    main_iface=$(ip route | grep default | awk '{print $5}' | head -1)
    if [[ -n "$main_iface" ]]; then
        iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o "$main_iface" -j MASQUERADE >> "$LOG_FILE" 2>&1
        log_success "NAT 规则已添加（网卡: ${main_iface}）"
    else
        log_warn "无法识别主网卡，请手动配置 NAT"
    fi

    log_success "OpenVPN 服务器配置生成"
}

# ============================================================
# 第9步：配置 nftables 域名黑名单基础环境
# ============================================================
setup_blacklist_base() {
    log_info "配置 nftables 域名黑名单基础环境..."
    if ! command -v nft >/dev/null 2>&1; then
        log_info "未检测到 nft，安装 nftables..."
        apt-get update >> "$LOG_FILE" 2>&1
        apt-get install -y nftables >> "$LOG_FILE" 2>&1
    fi
    if ! command -v nft >/dev/null 2>&1; then
        log_fail "nftables 检查" "nft 命令安装失败"
        exit 1
    fi
    log_success "nftables 已就绪；域名黑名单基础环境配置完成"
}

# ============================================================
# 第10步：生成配置文件与 JWT 密钥
# ============================================================
setup_app_config() {
    log_info "生成应用配置..."

    # 若已有 .env（修复/重装），保留 JWT 与管理员口令，避免失效
    local preserve_env=0
    if [[ -f "${INSTALL_DIR}/.env" ]]; then
        preserve_env=1
        # 读出旧值（打印到摘要时使用）
        DEFAULT_ADMIN_USER=$(grep -E '^OVPM_ADMIN_USER=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d ' \r')
        DEFAULT_ADMIN_PASSWORD=$(grep -E '^OVPM_ADMIN_PASSWORD=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r')
        JWT_SECRET=$(grep -E '^OVPM_JWT_SECRET=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r')
        local old_web_port old_web_host
        old_web_port=$(grep -E '^OVPM_WEB_PORT=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d ' \r')
        old_web_host=$(grep -E '^OVPM_WEB_HOST=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d ' \r')
        [[ "$old_web_port" =~ ^[0-9]+$ ]] && WEB_PORT="$old_web_port"
        [[ -n "$old_web_host" ]] && WEB_HOST="$old_web_host"
        DEFAULT_ADMIN_USER=${DEFAULT_ADMIN_USER:-admin}
        log_info "检测到已有 .env，保留 JWT 与管理员口令（不重新生成）"
    else
        # 生成随机 JWT 密钥与一次性管理员初始密码（不再使用源码固定密码）
        JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
        DEFAULT_ADMIN_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    fi

    # 写入环境配置文件（默认 Nyr 布局，若存在则自动探测）
    APP_EASYRSA_DIR="/etc/openvpn/server/easy-rsa"
    APP_OPENVPN_DIR="/etc/openvpn/server"
    APP_OVPN_CONF="/etc/openvpn/server/server.conf"
    APP_CLIENT_CERT_DIR="/etc/openvpn/server/client-configs"
    APP_OVPN_SERVICE="openvpn-server@server"
    local easyrsa_dir="$APP_EASYRSA_DIR"
    local openvpn_dir="$APP_OPENVPN_DIR"
    local ovpn_conf="$APP_OVPN_CONF"
    local client_cert_dir="$APP_CLIENT_CERT_DIR"
    local ovpn_service="$APP_OVPN_SERVICE"
    local status_log="/run/openvpn-server/status-server.log"

    # 服务名不要仅由目录布局推断。Ubuntu/Debian 常见的 OpenVPN 单元有：
    #   openvpn-server@server.service（Nyr/新布局）
    #   openvpn@server.service        （classic/旧布局）
    #   openvpn.service               （少数发行版/旧安装）
    # systemctl list-unit-files 对模板服务只显示 openvpn@.service，
    # 因此这里使用 systemctl show 的 LoadState 逐个探测“实例”是否可解析。
    detect_openvpn_service() {
        local candidate load_state active_state
        # 多套 OpenVPN 共存时，必须优先选择当前 active 的实例。
        for candidate in openvpn-server@server openvpn@server openvpn; do
            active_state=$(systemctl show "$candidate" -p ActiveState --value 2>/dev/null || true)
            if [[ "$active_state" == "active" ]]; then
                APP_OVPN_SERVICE="$candidate"
                ovpn_service="$candidate"
                return 0
            fi
        done
        # 当前都未运行时，再选择已加载的实例，交给后续启动阶段。
        for candidate in openvpn-server@server openvpn@server openvpn; do
            load_state=$(systemctl show "$candidate" -p LoadState --value 2>/dev/null || true)
            if [[ "$load_state" != "not-found" && -n "$load_state" ]]; then
                APP_OVPN_SERVICE="$candidate"
                ovpn_service="$candidate"
                return 0
            fi
        done
        return 1
    }

    if [[ ! -d "$easyrsa_dir" ]]; then
        # 回退旧布局
        APP_EASYRSA_DIR="/etc/openvpn/easy-rsa"
        APP_OPENVPN_DIR="/etc/openvpn"
        APP_OVPN_CONF="/etc/openvpn/server.conf"
        APP_CLIENT_CERT_DIR="/etc/openvpn/client-configs"
        APP_OVPN_SERVICE="openvpn@server"
        easyrsa_dir="$APP_EASYRSA_DIR"
        openvpn_dir="$APP_OPENVPN_DIR"
        ovpn_conf="$APP_OVPN_CONF"
        client_cert_dir="$APP_CLIENT_CERT_DIR"
        ovpn_service="$APP_OVPN_SERVICE"
        status_log="/var/log/openvpn/openvpn-status.log"
    fi

    # 服务名以实际 systemd 单元为准；即使目录是 Nyr 布局，也可能是
    # openvpn@server（反之亦然）。探测失败时保留布局默认值，后续启动阶段再给出明确错误。
    detect_openvpn_service || log_warn "未能从当前 systemd 解析 OpenVPN 服务实例，将在启动阶段再次探测"

    # 状态日志必须跟实际布局一致；不要把 classic 的状态文件错误写成 Nyr 路径。
    if [[ "$ovpn_conf" == "/etc/openvpn/server.conf" ]]; then
        status_log="/var/log/openvpn/openvpn-status.log"
    else
        status_log="/run/openvpn-server/status-server.log"
    fi

    # 从 server.conf 读取端口/协议
    if [[ -f "$ovpn_conf" ]]; then
        local p pr
        p=$(grep -E '^port ' "$ovpn_conf" | awk '{print $2}' | head -1)
        pr=$(grep -E '^proto ' "$ovpn_conf" | awk '{print $2}' | head -1)
        [[ "$p" =~ ^[0-9]+$ ]] && VPN_PORT="$p"
        [[ -n "$pr" ]] && VPN_PROTO="$pr"
    fi

    local admin_vpn_cidrs=""
    if [[ -f "${INSTALL_DIR}/.env" ]]; then
        admin_vpn_cidrs=$(sed -n 's/^OVPM_ADMIN_VPN_CIDRS=//p' "${INSTALL_DIR}/.env" | head -1)
    fi
    cat > "${INSTALL_DIR}/.env" << EOF
# OpenVPN 管理平台环境配置（由安装脚本生成，对齐 Nyr/openvpn-install）
OVPM_JWT_SECRET=${JWT_SECRET}
OVPM_ADMIN_USER=${DEFAULT_ADMIN_USER}
OVPM_ADMIN_PASSWORD=${DEFAULT_ADMIN_PASSWORD}
OVPM_SERVER_IP=${SERVER_IP}
OVPM_WEB_PORT=${WEB_PORT}
OVPM_WEB_HOST=${WEB_HOST:-0.0.0.0}
OVPM_ADMIN_VPN_CIDRS=${admin_vpn_cidrs}
OVPM_HTTPS_ENABLED=0
OVPM_HTTPS_PORT=443
OVPM_ALLOWED_ORIGINS=
OVPM_VPN_PORT=${VPN_PORT}
OVPM_VPN_PROTO=${VPN_PROTO:-udp}
OVPM_EASYRSA_DIR=${APP_EASYRSA_DIR}
OVPM_OPENVPN_DIR=${APP_OPENVPN_DIR}
OVPM_OVPN_CONFIG=${ovpn_conf}
OVPM_STATUS_LOG=${status_log}
OVPM_BLACKLIST_DOMAIN_FILE=${INSTALL_DIR}/data/blacklist-domains.txt
OVPM_BLACKLIST_STATE_FILE=${INSTALL_DIR}/data/blacklist-state.json
OVPM_NFT_TABLE=ovpn_admin
OVPM_NFT_SET_V4=blocked_ipv4
OVPM_NFT_SET_V6=blocked_ipv6
OVPM_CLIENT_CERT_DIR=${client_cert_dir}
OVPM_OPENVPN_SERVICE=${APP_OVPN_SERVICE}
OVPM_CLIENT_COMMON=${openvpn_dir}/client-common.txt
OVPM_TLS_KEY=${openvpn_dir}/$( [[ "${APP_OPENVPN_DIR}" == "/etc/openvpn/server" ]] && echo tc.key || echo ta.key )
OVPM_TLS_CRYPT=${openvpn_dir}/$( [[ "${APP_OPENVPN_DIR}" == "/etc/openvpn/server" ]] && echo tc.key || echo ta.key )
OVPM_CRL=${openvpn_dir}/crl.pem
OVPM_DATA_DIR=${INSTALL_DIR}/data
OVPM_CERT_DAYS=3650
OVPM_SERVICE_USER=${APP_USER}
OVPM_PRIV_HELPER=${PRIV_HELPER}
OVPM_SSL_CERT=/etc/openvpn/web/ssl/server.crt
OVPM_SSL_KEY=/etc/openvpn/web/ssl/server.key
EOF

    chmod 600 "${INSTALL_DIR}/.env"
    if [[ "$preserve_env" -eq 1 ]]; then
        log_success "应用配置已更新（保留原有 JWT 与管理员口令）"
    else
        log_success "应用配置生成（JWT 密钥已随机生成）"
    fi

    # 创建客户端证书副本目录。
    # Web 服务不直接写该目录；实际复制由 root helper 完成。
    local pki_root="${easyrsa_dir}/pki"
    if [[ "${client_cert_dir}" != "${pki_root}"* ]]; then
        mkdir -p "${client_cert_dir}"
        chown root:"${APP_GROUP}" "${client_cert_dir}" 2>/dev/null || true
        chmod 0750 "${client_cert_dir}" 2>/dev/null || true
    fi
}

# ============================================================
# 第11步：创建非 root 服务用户与 root helper
# ============================================================
setup_service_user() {
    log_info "创建非 root Web 服务用户并安装受控 root helper..."

    if ! getent group "${APP_GROUP}" >/dev/null; then
        groupadd --system "${APP_GROUP}"
    fi
    if ! id "${APP_USER}" >/dev/null 2>&1; then
        useradd --system --gid "${APP_GROUP}" --home-dir "${INSTALL_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
    fi

    mkdir -p "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs" /etc/openvpn-admin
    chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
    chmod 0750 "${INSTALL_DIR}" "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs"
    chmod 0600 "${INSTALL_DIR}/.env"

    # 将 root helper 直接内置在本安装脚本中，不再依赖 create-service-user.sh。
    install -d -o root -g root -m 0755 "$(dirname "${PRIV_HELPER}")"
    install -m 0700 "${SCRIPT_DIR}/privileged/ovpn-admin-priv.py" "${PRIV_HELPER}"
    chown root:root "${PRIV_HELPER}"
    chmod 0700 "${PRIV_HELPER}"

    cat > "${PRIV_ENV}" << EOF
OVPM_SERVICE_USER=${APP_USER}
OVPM_EASYRSA_DIR=${APP_EASYRSA_DIR}
OVPM_EASYRSA_BIN=${APP_EASYRSA_DIR}/easyrsa
OVPM_OPENVPN_DIR=${APP_OPENVPN_DIR}
OVPM_OPENVPN_SERVICE=${APP_OVPN_SERVICE}
OVPM_BLACKLIST_DOMAIN_FILE=${INSTALL_DIR}/data/blacklist-domains.txt
OVPM_BLACKLIST_STATE_FILE=${INSTALL_DIR}/data/blacklist-state.json
OVPM_NFT_TABLE=ovpn_admin
OVPM_NFT_SET_V4=blocked_ipv4
OVPM_NFT_SET_V6=blocked_ipv6
OVPM_CRL_FILE=${APP_OPENVPN_DIR}/crl.pem
OVPM_CRL=${APP_OPENVPN_DIR}/crl.pem
OVPM_TLS_KEY=${APP_OPENVPN_DIR}/$( [[ "${APP_OPENVPN_DIR}" == "/etc/openvpn/server" ]] && echo tc.key || echo ta.key )
OVPM_TLS_CRYPT=${APP_OPENVPN_DIR}/$( [[ "${APP_OPENVPN_DIR}" == "/etc/openvpn/server" ]] && echo tc.key || echo ta.key )
OVPM_CLIENT_COMMON=${APP_OPENVPN_DIR}/client-common.txt
OVPM_CLIENT_CERT_DIR=${APP_CLIENT_CERT_DIR}
OVPM_STATUS_LOG=$( [[ "${APP_OPENVPN_DIR}" == "/etc/openvpn/server" ]] && echo /run/openvpn-server/status-server.log || echo /var/log/openvpn/openvpn-status.log )
OVPM_PRIV_HELPER=${PRIV_HELPER}
EOF
    chown root:root "${PRIV_ENV}"
    chmod 0600 "${PRIV_ENV}"

    # ---- root 任务队列（NoNewPrivileges 环境下 sudo 不可用时的兜底）----
    mkdir -p /var/spool/ovpn-admin
    chown root:"${APP_GROUP}" /var/spool/ovpn-admin
    chmod 1730 /var/spool/ovpn-admin

    install -m 0755 "${SCRIPT_DIR}/privileged/ovpn-admin-job-worker" /usr/local/sbin/ovpn-admin-job-worker
    chown root:root /usr/local/sbin/ovpn-admin-job-worker
    chmod 0755 /usr/local/sbin/ovpn-admin-job-worker

    cat > /etc/systemd/system/ovpn-admin-jobs.service << 'UNIT'
[Unit]
Description=OpenVPN Admin privileged job worker
After=network.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ovpn-admin-job-worker
UNIT

    cat > /etc/systemd/system/ovpn-admin-jobs.path << 'UNIT'
[Unit]
Description=Watch OpenVPN Admin job spool
[Path]
# 只在真正的 .job 出现时触发；不要监听整个目录。
# .result 会被保留一段时间/由 Web 读取后删除，监听目录会导致 oneshot
# worker 在没有任何 .job 时反复启动，最终触发 start-limit-hit。
PathExistsGlob=/var/spool/ovpn-admin/*.job
Unit=ovpn-admin-jobs.service
[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    systemctl reset-failed ovpn-admin-jobs.path ovpn-admin-jobs.service >/dev/null 2>&1 || true
    systemctl enable --now ovpn-admin-jobs.path >/dev/null 2>&1 || true
    log_success "root 任务队列已就绪: /var/spool/ovpn-admin"

    # PKI/status/.env 权限统一由 grant_runtime_permissions() 处理，避免重复授权逻辑。

    # 不再给 Web 用户配置 sudoers。
    # Web 服务通过 /var/spool/ovpn-admin 提交任务，由 root worker 调用
    # root-owned helper；helper 自身仍执行严格的操作/参数白名单。
    # 兼容旧版本升级：删除历史的 helper * sudo 授权，避免留下通用提权入口。
    rm -f "/etc/sudoers.d/${APP_NAME}"
    log_success "已移除 Web 用户 sudo 提权规则，改用 root 任务队列"

    # 修复 sudo: unable to resolve host
    if [[ -n "${HOSTNAME}" ]] && ! grep -qE "[[:space:]]${HOSTNAME}([[:space:]]|$)" /etc/hosts 2>/dev/null; then
        echo "127.0.1.1 ${HOSTNAME}" >> /etc/hosts
        log_info "已补 /etc/hosts: ${HOSTNAME}"
    fi

    # 数据库与日志目录必须可被服务用户读写（否则登录报「服务暂时不可用」）
    mkdir -p "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs"
    chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs"
    chmod 0750 "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs"
    if [[ -f "${INSTALL_DIR}/data/ovpn_admin.db" ]]; then
        chown "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}/data/ovpn_admin.db"
        chmod 0640 "${INSTALL_DIR}/data/ovpn_admin.db"
        for f in "${INSTALL_DIR}/data/ovpn_admin.db-wal" "${INSTALL_DIR}/data/ovpn_admin.db-shm"; do
            [[ -f "$f" ]] && chown "${APP_USER}:${APP_GROUP}" "$f"
        done
    fi
    # .env 含 JWT/管理员凭据：保持 root:${APP_GROUP} 0640。

    log_success "非 root 服务用户、root helper 与任务队列配置完成：${APP_USER}"

    # 立即授予 PKI/证书路径 ACL。
    # 否则后续 OpenVPN 启动若失败 exit，会跳过权限，导致网页「无法同步/创建证书」。
    if ! grant_runtime_permissions; then
        log_warn "证书路径权限首次授予未完全成功，将在后续修复阶段重试"
    fi
}

# ============================================================
# 部署 root nftables 黑名单同步器
# ============================================================
setup_blacklist_runtime() {
    log_info "部署 nftables 黑名单同步器..."
    install -d -o root -g root -m 0755 /usr/local/sbin
    install -m 0755 "${SCRIPT_DIR}/privileged/ovpn-admin-blacklist-sync" /usr/local/sbin/ovpn-admin-blacklist-sync
    chown root:root /usr/local/sbin/ovpn-admin-blacklist-sync
    chmod 0755 /usr/local/sbin/ovpn-admin-blacklist-sync
    cat > /etc/systemd/system/openvpn-admin-blacklist.service <<'UNIT'
[Unit]
Description=OpenVPN Admin blacklist IP set refresh
After=network-online.target nftables.service
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ovpn-admin-blacklist-sync
UNIT
    cat > /etc/systemd/system/openvpn-admin-blacklist.timer <<'UNIT'
[Unit]
Description=Refresh OpenVPN Admin blacklist IP sets
[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
Unit=openvpn-admin-blacklist.service
[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload >> "$LOG_FILE" 2>&1
    systemctl enable --now openvpn-admin-blacklist.timer >> "$LOG_FILE" 2>&1
    systemctl start openvpn-admin-blacklist.service >> "$LOG_FILE" 2>&1 || log_warn "首次黑名单表初始化失败，定时器将自动重试"

    # 将 helper 的 blacklist 操作写入现有受控 root helper。
    log_success "nftables 黑名单同步器已部署"
}

# ============================================================
# 第11步：创建 systemd 服务
# ============================================================
setup_systemd() {
    log_info "创建 systemd 服务..."

    # 非 root 用户绑定 80/443 等特权端口需要 CAP_NET_BIND_SERVICE；
    # 否则 CapabilityBoundingSet= 会直接导致 bind Permission denied、端口不监听。
    local cap_line="CapabilityBoundingSet="
    local ambient_line="AmbientCapabilities="
    if [[ "${WEB_PORT}" -lt 1024 ]]; then
        cap_line="CapabilityBoundingSet=CAP_NET_BIND_SERVICE"
        ambient_line="AmbientCapabilities=CAP_NET_BIND_SERVICE"
        log_info "Web 端口 ${WEB_PORT} < 1024，授予 CAP_NET_BIND_SERVICE"
    fi

    # 注意：
    # - Web 进程永不直接 sudo；高权限动作走 root 任务队列。
    # - NoNewPrivileges=true 防止 Web 进程自身获得额外权限。
    # - ProtectSystem=strict 下，helper 仍需在允许的 OpenVPN/nftables 路径执行操作。
    cat > /etc/systemd/system/${APP_NAME}.service << EOF
# OpenVPN 管理平台 Web 服务
[Unit]
Description=OpenVPN Admin Web Platform
After=network.target
Wants=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/python -m backend.main
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${INSTALL_DIR}/data ${INSTALL_DIR}/logs /var/spool/ovpn-admin
${cap_line}
${ambient_line}
LockPersonality=true
MemoryDenyWriteExecute=false
RestrictRealtime=true
RestrictNamespaces=true
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # 旧版本可能仍在运行（例如 server.py/storage.py 启动链）。
    # 仅 daemon-reload + start 对已运行服务不会替换旧进程，因此必须先 stop。
    systemctl stop ${APP_NAME} >> "$LOG_FILE" 2>&1 || true

    # 清理此前手工创建的旧 drop-in；新的主 unit 已包含完整 ReadWritePaths。
    rm -f /etc/systemd/system/${APP_NAME}.service.d/override.conf 2>/dev/null || true
    rmdir /etc/systemd/system/${APP_NAME}.service.d 2>/dev/null || true

    systemctl daemon-reload >> "$LOG_FILE" 2>&1
    systemctl enable ${APP_NAME} >> "$LOG_FILE" 2>&1

    # 发布级启动前校验：禁止旧 server.py / storage.py 入口再次混入。
    local actual_execstart
    actual_execstart=$(systemctl show ${APP_NAME} -p ExecStart --value 2>/dev/null || true)
    if [[ "$actual_execstart" != *"-m backend.main"* ]]; then
        log_fail "systemd 启动入口检查" "ExecStart 不是 backend.main，实际为: ${actual_execstart}"
        exit 1
    fi

    systemctl start ${APP_NAME} >> "$LOG_FILE" 2>&1

    # 等待启动
    sleep 2
    if systemctl is-active --quiet ${APP_NAME}; then
        log_success "Web 服务启动成功"
    else
        log_fail "Web 服务启动" "服务启动失败，运行 journalctl -u ${APP_NAME} 查看详情"
        exit 1
    fi

    # 启动实际存在的 OpenVPN systemd 实例。模板服务不能靠 list-unit-files 精确匹配。
    # 选择顺序：当前 active 实例 > 已加载实例 > 失败。
    local ovpn_service="${APP_OVPN_SERVICE:-}"
    local detected_service=""
    local candidate load_state active_state
    local candidates=("${ovpn_service}" openvpn-server@server openvpn@server openvpn)
    for candidate in "${candidates[@]}"; do
        [[ -n "$candidate" ]] || continue
        active_state=$(systemctl show "$candidate" -p ActiveState --value 2>/dev/null || true)
        if [[ "$active_state" == "active" ]]; then
            detected_service="$candidate"
            break
        fi
    done
    if [[ -z "$detected_service" ]]; then
        for candidate in "${candidates[@]}"; do
            [[ -n "$candidate" ]] || continue
            load_state=$(systemctl show "$candidate" -p LoadState --value 2>/dev/null || true)
            if [[ "$load_state" != "not-found" && -n "$load_state" ]]; then
                detected_service="$candidate"
                break
            fi
        done
    fi

    if [[ -n "$detected_service" ]]; then
        APP_OVPN_SERVICE="$detected_service"
        ovpn_service="$detected_service"
        if ! systemctl enable "$ovpn_service" >> "$LOG_FILE" 2>&1; then
            log_warn "OpenVPN 服务 enable 失败，继续尝试启动: ${ovpn_service}"
        fi
        local readiness_start
        readiness_start=$(date --iso-8601=seconds)
        if ! systemctl restart "$ovpn_service" >> "$LOG_FILE" 2>&1; then
            # restart 退出码非 0 不一定是服务没起来（历史误报、限流、stop 超时等）。
            # 以 ActiveState + 后续 tun0/Initialization 就绪检查为准。
            log_warn "systemctl restart 返回非 0，继续核对服务实际状态: ${ovpn_service}"
            active_state=$(systemctl show "$ovpn_service" -p ActiveState --value 2>/dev/null || true)
            if [[ "$active_state" != "active" && "$active_state" != "activating" ]]; then
                systemctl reset-failed "$ovpn_service" >> "$LOG_FILE" 2>&1 || true
                if ! systemctl start "$ovpn_service" >> "$LOG_FILE" 2>&1; then
                    log_fail "OpenVPN 服务启动" "${ovpn_service} 启动失败。请运行 journalctl -u ${ovpn_service} 查看详情"
                    exit 1
                fi
            fi
        fi

        # systemd active 不等于 OpenVPN 已完成初始化。
        # 就绪标准（满足其一即可，最多 45 秒）：
        #   A. active + tun0 + 本次日志出现 Initialization Sequence Completed
        #   B. active + tun0 + 无 Options error，且连续 3 秒保持 active（慢机器/日志延迟）
        # 客户端 tls-crypt 错误不得影响启动判断。
        local ready=0
        local i recent_log active_streak=0
        for i in $(seq 1 45); do
            active_state=$(systemctl show "$ovpn_service" -p ActiveState --value 2>/dev/null || true)
            if [[ "$active_state" == "active" ]] && ip link show tun0 >/dev/null 2>&1; then
                recent_log=$(journalctl -u "$ovpn_service" --since "$readiness_start" --no-pager -o cat 2>/dev/null || true)
                if grep -q 'Initialization Sequence Completed' <<<"$recent_log"; then
                    ready=1
                    break
                fi
                if ! grep -q 'Options error' <<<"$recent_log"; then
                    active_streak=$((active_streak + 1))
                    if [[ "$active_streak" -ge 3 ]]; then
                        ready=1
                        break
                    fi
                else
                    active_streak=0
                fi
            else
                active_streak=0
            fi
            sleep 1
        done
        if [[ "$ready" -eq 1 ]]; then
            log_success "OpenVPN 服务启动成功（${ovpn_service}，tun0 已就绪）"
        else
            log_fail "OpenVPN 服务启动" "${ovpn_service} 未在 45 秒内完成初始化；请运行 journalctl -u ${ovpn_service} --since '${readiness_start}' 查看启动日志"
            exit 1
        fi
    else
        log_fail "OpenVPN 服务启动" "未找到可用 OpenVPN systemd 服务（尝试过 openvpn-server@server、openvpn@server、openvpn）"
        exit 1
    fi
}

# ============================================================
# 第12步：配置防火墙
# ============================================================
setup_firewall() {
    log_info "配置防火墙..."

    # 检测防火墙类型
    if command -v ufw &>/dev/null && ufw status | grep -q "Status: active"; then
        ufw allow "${WEB_PORT}/tcp" >> "$LOG_FILE" 2>&1
        if [[ "${HTTPS_ENABLED:-0}" == "1" ]]; then ufw allow 443/tcp >> "$LOG_FILE" 2>&1; fi
        ufw allow "${VPN_PORT}/udp" >> "$LOG_FILE" 2>&1
        log_success "UFW 防火墙放行端口: ${WEB_PORT}/tcp${HTTPS_ENABLED:+, 443/tcp}, ${VPN_PORT}/udp"
    elif command -v firewall-cmd &>/dev/null && firewall-cmd --state &>/dev/null 2>&1; then
        firewall-cmd --permanent --add-port="${WEB_PORT}/tcp" >> "$LOG_FILE" 2>&1
        if [[ "${HTTPS_ENABLED:-0}" == "1" ]]; then firewall-cmd --permanent --add-port=443/tcp >> "$LOG_FILE" 2>&1; fi
        firewall-cmd --permanent --add-port="${VPN_PORT}/udp" >> "$LOG_FILE" 2>&1
        firewall-cmd --reload >> "$LOG_FILE" 2>&1
        log_success "firewalld 放行端口: ${WEB_PORT}/tcp, ${VPN_PORT}/udp"
    else
        # 使用 iptables（-w 5 等待 xtables 锁，避免并发失败）
        iptables -w 5 -I INPUT -p tcp --dport "${WEB_PORT}" -j ACCEPT >> "$LOG_FILE" 2>&1
        if [[ "${HTTPS_ENABLED:-0}" == "1" ]]; then iptables -w 5 -I INPUT -p tcp --dport 443 -j ACCEPT >> "$LOG_FILE" 2>&1; fi
        iptables -w 5 -I INPUT -p udp --dport "${VPN_PORT}" -j ACCEPT >> "$LOG_FILE" 2>&1
        log_success "iptables 放行端口: ${WEB_PORT}/tcp, ${VPN_PORT}/udp"
    fi
}


# ============================================================
# 第14步：部署后验证 + 登录/数据库诊断
# ============================================================
# ============================================================
# 运行时权限：授予 ${APP_USER} 所需读权限（单一入口，禁止散落 ACL）
# 覆盖：数据目录 / EasyRSA PKI / client-common / status / .env
# ============================================================
grant_runtime_permissions() {
    log_info "授予 ${APP_USER} 运行时路径权限..."

    # --- 应用数据目录（SQLite 必须可写）---
    mkdir -p "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs"
    chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs"
    chmod 0750 "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs"
    local db="${INSTALL_DIR}/data/ovpn_admin.db"
    if [[ -f "$db" ]]; then
        chown "${APP_USER}:${APP_GROUP}" "$db"
        chmod 0640 "$db"
        for f in "${db}-wal" "${db}-shm"; do
            [[ -f "$f" ]] && chown "${APP_USER}:${APP_GROUP}" "$f"
        done
    fi
    # .env 含密钥：root 可读 + 服务用户只读
    if [[ -f "${INSTALL_DIR}/.env" ]]; then
        chown root:"${APP_GROUP}" "${INSTALL_DIR}/.env"
        chmod 0640 "${INSTALL_DIR}/.env"
    fi

    # --- OpenVPN/EasyRSA 路径 ---
    local easy="${APP_EASYRSA_DIR:-/etc/openvpn/server/easy-rsa}"
    local ovpn="${APP_OPENVPN_DIR:-/etc/openvpn/server}"
    local pki="${easy}/pki"

    # 服务用户需要读 server.conf / client-common（端口、remote、模板）
    for f in \
        "${ovpn}/server.conf" \
        "${ovpn}/client-common.txt" \
        "${ovpn}/ca.crt" \
        "${ovpn}/tc.key" \
        "${ovpn}/ta.key" \
        "${ovpn}/crl.pem" \
        "${easy}/easyrsa"
    do
        [[ -e "$f" ]] || continue
        if ! setfacl -m "u:${APP_USER}:rX" "$f"; then
            log_fail "文件 ACL" "无法授予 ${APP_USER} 最小读取权限: $f"
            return 1
        fi
    done

    # --- PKI：扫描/下载 ovpn 需要；CA 私钥拒绝 ---
    if [[ -d "$pki" ]]; then
        chmod 0750 "$pki" 2>/dev/null || true
        if command -v setfacl >/dev/null 2>&1; then
            setfacl -m "u:${APP_USER}:rx" "$pki"
            setfacl -m "d:u:${APP_USER}:rX" "$pki"
            [[ -f "$pki/index.txt" ]] && setfacl -m "u:${APP_USER}:r" "$pki/index.txt"
            [[ -f "$pki/ca.crt" ]] && setfacl -m "u:${APP_USER}:r" "$pki/ca.crt"
            for d in issued private inline private/reqs reqs; do
                [[ -d "${pki}/${d}" ]] || continue
                setfacl -m "u:${APP_USER}:rx" "${pki}/${d}"
                setfacl -m "d:u:${APP_USER}:rX" "${pki}/${d}"
            done
            find "${pki}/issued" -type f -name '*.crt' -exec setfacl -m "u:${APP_USER}:r" {} + 2>/dev/null || true
            find "${pki}/private" -type f -name '*.key' ! -name 'ca.key' -exec setfacl -m "u:${APP_USER}:r" {} + 2>/dev/null || true
            find "${pki}/inline" -type f -name '*.inline' -exec setfacl -m "u:${APP_USER}:r" {} + 2>/dev/null || true
            [[ -f "${pki}/private/ca.key" ]] && setfacl -x "u:${APP_USER}" "${pki}/private/ca.key" 2>/dev/null || true
        else
            log_fail "PKI ACL" "setfacl 不可用；拒绝将客户端私钥开放给所有系统用户"
            return 1
        fi
    fi

    # --- status 日志（在线列表；文件常为 600 root，目录+默认 ACL）---
    if ! command -v setfacl >/dev/null 2>&1; then
        log_fail "status ACL" "setfacl 不可用；拒绝将 OpenVPN 状态/日志改为全局可读"
        return 1
    fi
    for d in /run/openvpn-server /var/log/openvpn /var/log/openvpn-server; do
        [[ -d "$d" ]] || continue
        setfacl -m "u:${APP_USER}:rx" "$d"
        setfacl -m "d:u:${APP_USER}:r" "$d"
    done
    for f in \
        /run/openvpn-server/status-server.log \
        /run/openvpn-server/status.log \
        /var/log/openvpn/openvpn-status.log \
        /var/log/openvpn/status.log
    do
        [[ -f "$f" ]] || continue
        setfacl -m "u:${APP_USER}:r" "$f"
    done

    log_success "运行时路径权限已授予 ${APP_USER}"
}

fix_data_permissions() {
    grant_runtime_permissions
    log_success "数据目录/数据库权限已重置为 ${APP_USER}"
}

# ------------------------------------------------------------
# 证书链路：修复 systemd（非 root + root 任务队列）+ EasyRSA PKI 权限
# 对应「证书操作失败」「导入失败」两个报错
# ------------------------------------------------------------
detect_easyrsa_dir() {
    local d
    for d in "/etc/openvpn/server/easy-rsa" "/etc/openvpn/easy-rsa" "${APP_EASYRSA_DIR:-}"; do
        [[ -n "$d" && -d "$d" ]] && { echo "$d"; return 0; }
    done
    return 1
}

repair_service_unit() {
    # 兼容旧函数名：现在不是 sudo 提权，而是 root 任务队列。
    local unit="/etc/systemd/system/${APP_NAME}.service"
    log_info "重写 systemd 单元（Web 非 root + NoNewPrivileges=true + root 任务队列）..."

    if [[ -f "$unit" ]]; then
        cp -a "$unit" "${unit}.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
    fi

    local cap_line="CapabilityBoundingSet="
    local ambient_line="AmbientCapabilities="
    if [[ "${WEB_PORT}" -lt 1024 ]]; then
        cap_line="CapabilityBoundingSet=CAP_NET_BIND_SERVICE"
        ambient_line="AmbientCapabilities=CAP_NET_BIND_SERVICE"
    fi

    cat > "$unit" << EOF
# OpenVPN 管理平台 Web 服务
[Unit]
Description=OpenVPN Admin Web Platform
After=network.target
Wants=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/python -m backend.main
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${INSTALL_DIR}/data ${INSTALL_DIR}/logs /var/spool/ovpn-admin
${cap_line}
${ambient_line}
LockPersonality=true
MemoryDenyWriteExecute=false
RestrictRealtime=true
RestrictNamespaces=true
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # 历史版本的 sudo wildcard 必须清理。
    rm -f "/etc/sudoers.d/${APP_NAME}"

    # 确保 root worker/path unit 存在并由 systemd 监管。
    if [[ -x /usr/local/sbin/ovpn-admin-job-worker && -f /etc/systemd/system/ovpn-admin-jobs.path ]]; then
        systemctl daemon-reload
        systemctl enable --now ovpn-admin-jobs.path >/dev/null 2>&1 || true
        log_success "root 任务 worker/path 已启用"
    else
        log_fail "root 任务队列" "worker 或 path unit 缺失，请重新执行安装/修复"
        return 1
    fi

    systemctl daemon-reload
    systemctl restart "${APP_NAME}" 2>/dev/null || true
    sleep 1

    local nnp
    nnp=$(systemctl show "${APP_NAME}" -p NoNewPrivileges --value 2>/dev/null || echo "unknown")
    if [[ "$nnp" == "yes" || "$nnp" == "1" ]]; then
        log_success "Web 服务 NoNewPrivileges=${nnp}"
    else
        log_fail "systemd 安全策略" "NoNewPrivileges 未启用: ${nnp}"
        return 1
    fi

    if [[ -d /var/spool/ovpn-admin ]]; then
        local spool_mode spool_group
        spool_mode=$(stat -c '%a' /var/spool/ovpn-admin 2>/dev/null || echo "?")
        spool_group=$(stat -c '%G' /var/spool/ovpn-admin 2>/dev/null || echo "?")
        log_info "root 任务队列: /var/spool/ovpn-admin mode=${spool_mode} group=${spool_group}"
    fi

    # 直接以 root 验证 helper 白名单入口，不给 Web 用户任何 sudo 权限。
    if [[ -x "${PRIV_HELPER}" ]]; then
        if "${PRIV_HELPER}" easyrsa show-cert server >/dev/null 2>&1; then
            log_success "root helper 白名单检查通过"
        else
            log_warn "root helper 已存在，但 show-cert server 未通过；请检查 EasyRSA/PKI"
        fi
    fi
}

fix_cert_permissions() {
    log_info "校验并统一修复 EasyRSA/运行时最小读取权限..."
    local easyrsa_dir
    if ! easyrsa_dir=$(detect_easyrsa_dir); then
        log_fail "EasyRSA 目录" "未找到 easy-rsa"
        return 1
    fi
    local pki="${easyrsa_dir}/pki"
    [[ -d "$pki" ]] || { log_fail "PKI 目录" "$pki 不存在"; return 1; }

    # 唯一权限入口：PKI、client-common、TLS key、status、.env 全部由此处理。
    grant_runtime_permissions || return 1

    if ! sudo -u "${APP_USER}" test -r "$pki/index.txt" 2>/dev/null; then
        log_fail "PKI 可读性" "${APP_USER} 不可读 $pki/index.txt"
        return 1
    fi
    if ! sudo -u "${APP_USER}" ls "$pki/issued" >/dev/null 2>&1; then
        log_fail "PKI 可列" "${APP_USER} 无法列出 $pki/issued"
        return 1
    fi
    log_success "EasyRSA PKI 权限检查通过: $easyrsa_dir"
}

diagnose_certs() {
    # 证书链路诊断：定位「证书操作失败 / 导入失败」
    log_info "证书链路诊断..."

    local easyrsa_dir=""
    if easyrsa_dir=$(detect_easyrsa_dir); then
        log_success "EasyRSA 目录: $easyrsa_dir"
        if [[ -x "${easyrsa_dir}/easyrsa" ]]; then
            log_success "EasyRSA 可执行: ${easyrsa_dir}/easyrsa"
        else
            log_fail "EasyRSA 可执行" "${easyrsa_dir}/easyrsa 不存在或不可执行"
        fi
    else
        log_fail "EasyRSA" "未找到 easy-rsa 目录"
    fi

    local pki="${easyrsa_dir:-/etc/openvpn/server/easy-rsa}/pki"
    if [[ -d "$pki" ]]; then
        log_info "  PKI 权限:"
        ls -ld "$pki" "$pki/index.txt" "$pki/issued" "$pki/private" 2>/dev/null | sed 's/^/    /' || true
        if id "$APP_USER" >/dev/null 2>&1; then
            sudo -u "$APP_USER" test -r "$pki/index.txt" 2>/dev/null \
                && log_success "${APP_USER} 可读 index.txt" \
                || log_fail "PKI 可读" "${APP_USER} 不可读 index.txt → 导入失败/扫不到证书"
            sudo -u "$APP_USER" ls "$pki/issued" >/dev/null 2>&1 \
                && log_success "${APP_USER} 可列出 issued/" \
                || log_fail "PKI 可列" "${APP_USER} 无法列出 issued/ → Path.exists 可能抛 PermissionError"
        fi
        if [[ -r "$pki/index.txt" ]]; then
            log_info "  index.txt 客户端条目:"
            awk -F'\t' 'NF>=6 {print "    "$1" "$NF}' "$pki/index.txt" | head -20
        fi
        if [[ -d "$pki/issued" ]]; then
            log_info "  issued/*.crt:"
            ls -1 "$pki/issued" 2>/dev/null | head -20 | sed 's/^/    /'
        fi
    else
        log_warn "PKI 目录不存在: $pki"
    fi

    if [[ -x "${PRIV_HELPER}" ]]; then
        log_success "root helper 可执行: ${PRIV_HELPER}"
    else
        log_fail "root helper" "${PRIV_HELPER} 缺失 → 网页签发「证书操作失败」"
    fi

    if [[ -f "$PRIV_ENV" ]]; then
        log_success "privileged.env 存在"
        grep -E '^OVPM_' "$PRIV_ENV" 2>/dev/null | sed 's/^/    /' || true
    else
        log_fail "privileged.env" "缺少 $PRIV_ENV"
    fi

    # 新权限模型：不允许 Web 用户 sudo；仅检查 root worker + helper。
    rm -f "/etc/sudoers.d/${APP_NAME}"
    if [[ -x /usr/local/sbin/ovpn-admin-job-worker && -f /etc/systemd/system/ovpn-admin-jobs.path ]]; then
        log_success "root 任务 worker 已部署"
        systemctl is-active --quiet ovpn-admin-jobs.path             && log_success "root 任务 path 正常运行"             || log_warn "root 任务 path 未运行，请检查 systemctl status ovpn-admin-jobs.path"
    else
        log_fail "root 任务队列" "worker/path 缺失，网页证书操作无法提权"
    fi
    if [[ -x "${PRIV_HELPER}" ]]; then
        if "${PRIV_HELPER}" easyrsa show-cert server >/dev/null 2>&1; then
            log_success "root helper 白名单可执行"
        else
            log_warn "root helper 可执行，但 EasyRSA show-cert server 失败，请检查 PKI"
        fi
    fi

    log_info "  最近 openvpn-admin 日志（证书相关）:"
    journalctl -u "${APP_NAME}" -n 40 --no-pager 2>/dev/null \
        | grep -E 'easyrsa|证书|导入|sudo|Permission|ERROR|WARNING|ovpn\.(cmd|easyrsa)' \
        | tail -20 | sed 's/^/    /' || true
}

fix_certs_pipeline() {
    log_info "执行证书链路修复（含代码部署 + helper 重写）..."
    # 关键：网页跑的是 /opt/openvpn-admin，不是源码目录；不部署会一直用旧逻辑
    deploy_project || true
    setup_service_user || true
    repair_service_unit || true
    if ! fix_cert_permissions; then
        log_fail "证书权限" "无法安全授予 ${APP_USER} 最小读取权限；拒绝降级为全局可读，请安装 acl 后重试"
        return 1
    fi
    systemctl restart "${APP_NAME}" 2>/dev/null || true
    sleep 2
    diagnose_certs || true
    log_info "证书链路修复完成。请在网页验证：「同步已有证书」「新增客户端」"
    log_info "若创建仍失败，用新名称试：如 test01（避免与 EasyRSA 中已有 fjz 冲突）"
}

diagnose_login() {
    # 对应 diagnose-login.sh：定位「登录服务暂时不可用」
    log_info "登录 / 数据库诊断..."

    local db="${INSTALL_DIR}/data/ovpn_admin.db"
    local env_file="${INSTALL_DIR}/.env"
    local login_user="${DEFAULT_ADMIN_USER:-admin}"
    local login_pass="${DEFAULT_ADMIN_PASSWORD}"

    if [[ -f "$env_file" ]]; then
        login_user=$(grep -E '^OVPM_ADMIN_USER=' "$env_file" | cut -d= -f2- | tr -d ' \r')
        login_pass=$(grep -E '^OVPM_ADMIN_PASSWORD=' "$env_file" | cut -d= -f2- | tr -d '\r')
        login_user=${login_user:-admin}
    fi

    echo ""
    log_info "  .env 概要（口令只显示长度）"
    if [[ -f "$env_file" ]]; then
        local jwt_len pwd_len
        jwt_len=$(grep -E '^OVPM_JWT_SECRET=' "$env_file" | cut -d= -f2- | tr -d '\r' | wc -c)
        pwd_len=${#login_pass}
        echo "    OVPM_ADMIN_USER       = ${login_user}"
        echo "    OVPM_JWT_SECRET 长度   = ${jwt_len}"
        echo "    OVPM_ADMIN_PASSWORD 长度 = ${pwd_len}"
        echo "    OVPM_DATA_DIR         = $(grep -E '^OVPM_DATA_DIR=' "$env_file" | cut -d= -f2- | tr -d ' \r')"
        echo "    OVPM_STATUS_LOG       = $(grep -E '^OVPM_STATUS_LOG=' "$env_file" | cut -d= -f2- | tr -d ' \r')"
    else
        log_fail "登录诊断" ".env 不存在"
        return 1
    fi

    echo ""
    log_info "  以 ${APP_USER} 身份检测 SQLite"
    local db_ok=1
    if sudo -u "${APP_USER}" python3 - <<PY
import os, sqlite3, sys
db = "${db}"
print("    db exists =", os.path.exists(db))
try:
    conn = sqlite3.connect(db, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    print("    tables =", tables)
    if "admins" in tables:
        rows = conn.execute("SELECT id, username, length(password_hash) AS hlen FROM admins").fetchall()
        print("    admins:")
        for r in rows:
            print(f"      id={r['id']} username={r['username']} hash_len={r['hlen']}")
        if not rows:
            # 可能与 Web 启动 init_database 并发；不在此硬失败，后面会 init 并调登录 API
            print("    !! admins 暂为空（可能服务刚在初始化），继续 init/登录验证")
    else:
        print("    !! 缺少 admins 表")
        sys.exit(2)
    conn.execute("CREATE TABLE IF NOT EXISTS _diag_tmp(id INTEGER)")
    conn.execute("INSERT INTO _diag_tmp VALUES (1)")
    conn.execute("DELETE FROM _diag_tmp")
    conn.commit()
    conn.close()
    print("    读写测试: OK")
except Exception as e:
    print("    读写测试: FAIL ->", type(e).__name__, e)
    sys.exit(1)
PY
    then
        db_ok=0
        log_success "SQLite 可读写"
    else
        log_fail "SQLite / admins" "不可读写或表结构异常 —— 这很可能就是「登录服务暂时不可用」"
        fix_data_permissions
    fi

    echo ""
    log_info "  以 ${APP_USER} 身份初始化库并确认管理员"
    if sudo -u "${APP_USER}" env -i \
        HOME="${INSTALL_DIR}" \
        PATH="/usr/bin:/bin" \
        OVPM_DATA_DIR="${INSTALL_DIR}/data" \
        OVPM_JWT_SECRET="$(grep -E '^OVPM_JWT_SECRET=' "$env_file" | cut -d= -f2- | tr -d '\r')" \
        OVPM_ADMIN_USER="${login_user}" \
        OVPM_ADMIN_PASSWORD="${login_pass}" \
        bash -lc "cd ${INSTALL_DIR} && ./venv/bin/python -c \"
from backend.database import init_database, get_db
init_database()
with get_db() as conn:
    rows = conn.execute('SELECT username FROM admins').fetchall()
    print('    admins =', [r['username'] for r in rows])
print('    backend.database OK')
\""
    then
        log_success "init_database / backend.database 正常"
    else
        log_fail "backend.database" "导入或初始化失败，请看上方 traceback"
    fi

    echo ""
    log_info "  调用登录 API POST /api/auth/login"
    export DIAG_LOGIN_USER="${login_user}"
    export DIAG_LOGIN_PASS="${login_pass}"
    if sudo -u "${APP_USER}" env \
        DIAG_LOGIN_USER="${login_user}" \
        DIAG_LOGIN_PASS="${login_pass}" \
        python3 - <<'PY'
import json, os, urllib.request
user = os.environ.get("DIAG_LOGIN_USER", "admin")
password = os.environ.get("DIAG_LOGIN_PASS", "")
body = json.dumps({"username": user, "password": password}).encode()
req = urllib.request.Request(
    "http://127.0.0.1/api/auth/login",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print("    HTTP", resp.status)
        print("    body:", resp.read().decode()[:400])
except Exception as e:
    code = getattr(e, "code", None)
    if code == 403:
        print("    本机登录已拒绝；请连接 VPN 后使用隧道内地址验证真实登录。")
        raise SystemExit(0)
    data = e.read().decode()[:400] if hasattr(e, "read") else str(e)
    print("    HTTP", code)
    print("    body:", data)
    raise SystemExit(1)
PY
    then
        log_success "登录接口检查完成（本机 403 仅验证来源限制；登录需在 VPN 内验收）"
    else
        log_fail "登录 API" "仍失败，最近应用日志如下"
        journalctl -u "${APP_NAME}" -n 30 --no-pager 2>/dev/null | grep -E 'ERROR|数据库|登录|Traceback|OperationalError|IntegrityError|Exception|ovpn\.' \
            || journalctl -u "${APP_NAME}" -n 30 --no-pager 2>/dev/null || true
    fi
}

verify_install() {
    log_info "执行部署验证..."

    # 等待 Web 启动（uvicorn 绑定/装路由需要时间）
    local i health_ok=0 port_ok=0
    for i in 1 2 3 4 5 6 7 8 9 10; do
        sleep 1
        if ! port_ok && ss -lnt 2>/dev/null | grep -qE ":${WEB_PORT}\\b"; then
            port_ok=1
        fi
        if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${WEB_PORT}/api/health" 2>/dev/null | grep -q "200"; then
            health_ok=1
            break
        fi
    done

    if [[ "$health_ok" -eq 1 ]]; then
        log_success "Web 服务健康检查通过"
    else
        log_warn "Web 服务健康检查未通过（等待 10s），请检查 journalctl -u ${APP_NAME}"
        journalctl -u "${APP_NAME}" -n 20 --no-pager 2>/dev/null | grep -E 'error|Error|bind|Permission|address|Traceback|listen' | tail -10 | sed 's/^/    /' || true
    fi

    if [[ "$port_ok" -eq 1 ]] || ss -lnt 2>/dev/null | grep -qE ":${WEB_PORT}\\b"; then
        log_success "端口 ${WEB_PORT} 已监听"
    else
        log_warn "端口 ${WEB_PORT} 未监听（特权端口需 CAP_NET_BIND_SERVICE）"
    fi

    # 检查数据库 + 登录链路
    if [[ -f "${INSTALL_DIR}/data/ovpn_admin.db" ]]; then
        log_success "数据库文件已生成"
    else
        log_warn "数据库文件未生成（将在首次访问时创建）"
    fi
    fix_data_permissions
    diagnose_login

    # 检查 nftables 黑名单同步器
    if systemctl is-enabled --quiet openvpn-admin-blacklist.timer 2>/dev/null; then
        log_success "域名黑名单自动刷新定时器已启用"
    else
        log_warn "域名黑名单定时器未启用"
    fi
    if command -v nft >/dev/null 2>&1 && nft list table inet ovpn_admin >/dev/null 2>&1; then
        log_success "nftables ovpn_admin 黑名单表已就绪"
    else
        log_warn "nftables ovpn_admin 表尚未创建"
    fi

    # ---- 证书功能依赖（网页新增/同步会用到） ----
    local nyr_easyrsa="/etc/openvpn/server/easy-rsa/easyrsa"
    local classic_easyrsa="/etc/openvpn/easy-rsa/easyrsa"
    local app_easyrsa="${APP_EASYRSA_DIR:-/etc/openvpn/server/easy-rsa}/easyrsa"
    if [[ -x "$nyr_easyrsa" || -x "$classic_easyrsa" || -x "$app_easyrsa" ]]; then
        log_success "EasyRSA 已就绪（网页签发/同步可用）"
    else
        log_fail "EasyRSA 检查" "未找到 easyrsa，网页「新增客户端 / 同步已有证书」会报证书操作失败。请先完成 Nyr OpenVPN 安装"
    fi

    if [[ -x "${PRIV_HELPER}" ]]; then
        log_success "root helper 已安装: ${PRIV_HELPER}"
    else
        log_fail "root helper 检查" "${PRIV_HELPER} 不存在，网页证书操作会失败，请重跑安装"
    fi

    if [[ -f /etc/sudoers.d/openvpn-admin ]] || grep -qs 'ovpn-admin-priv' /etc/sudoers.d/* 2>/dev/null; then
        log_warn "检测到历史 helper sudoers 规则；当前版本会在修复阶段删除"
    else
        log_success "未给 Web 服务配置 sudo 提权"
    fi
    if [[ -x /usr/local/sbin/ovpn-admin-job-worker && -f /etc/systemd/system/ovpn-admin-jobs.path ]]; then
        log_success "root 任务队列已部署"
    else
        log_warn "root 任务队列缺失，网页证书操作可能失败"
    fi

    if command -v ovpn-client-quick >/dev/null 2>&1; then
        log_success "CLI 可用: ovpn-client-quick"
        # 非交互依赖体检（失败只告警，不中断）
        ovpn-client-quick sync-hint >> "$LOG_FILE" 2>&1 || true
    else
        log_warn "CLI 未在 PATH 中（可执行 ${INSTALL_DIR}/scripts/client-quick.sh）"
    fi

    # ---- 证书链路（导入失败 / 证书操作失败）----
    if ! fix_cert_permissions; then
        log_fail "证书权限" "无法安全授予 ${APP_USER} 最小读取权限；拒绝降级为全局可读"
        return 1
    fi
    diagnose_certs || true
}

# ============================================================
# 第15步：输出安装结果
# ============================================================
print_summary() {
    echo ""
    echo "============================================================"
    echo -e "${GREEN}       安装完成！${NC}"
    echo "============================================================"
    echo ""
    echo "📌 管理平台仅允许 VPN 网络访问："
    echo "   1. 先导入客户端配置并连接 OpenVPN。"
    echo "   2. 连接成功后使用以下 VPN 隧道地址访问后台："
    PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/venv/bin/python" -m backend.services.admin_address --env "${INSTALL_DIR}/.env" || true
    echo "   公网 IP/域名不能作为管理入口；公网 VPN 连接地址保持原用途。"
    echo "   无法连接时，请通过 SSH 检查 VPN 路由、防火墙和 OVPM_ADMIN_VPN_CIDRS。"
    echo ""
    echo "🔐 初始管理员账号（已改名时以现有数据库账号为准）："
    echo "   用户名：${DEFAULT_ADMIN_USER}"
    if [[ "$RUN_MODE" == "repair" || "$RUN_MODE" == "diagnose" ]]; then
        echo "   密码：  （保留数据库中的现有密码；.env 仅保存初始化口令，可能不是当前密码）"
    else
        echo "   密码：  ${DEFAULT_ADMIN_PASSWORD}"
        echo ""
        echo "⚠️  初始密码为本次安装随机生成，请首次登录后立即修改并妥善保存。"
    fi
    echo ""
    echo "📁 安装目录：${INSTALL_DIR}"
    echo "📋 安装日志：${LOG_FILE}"
    echo ""
    echo "🔧 常用命令："
    echo "   sudo bash scripts/install.sh --repair     # 启动失败/登录异常时修复"
    echo "   sudo bash scripts/install.sh --diagnose   # 仅诊断登录、数据库、证书链路"
    echo "   sudo bash scripts/install.sh --fix-certs  # 证书操作失败/导入失败时修复"
    echo "   journalctl -u ${APP_NAME} -f"
    echo ""
    echo "🧰 CLI 快捷证书操作（网页不可用时备用）："
    echo "   sudo ovpn-client-quick list"
    echo "   sudo ovpn-client-quick add <名称>"
    echo "   sudo ovpn-client-quick revoke <名称>"
    echo "   sudo ovpn-client-quick download <名称>"
    echo "   sudo ovpn-client-quick status"
    echo "   sudo ovpn-client-quick sync-hint        # 检查网页签发/同步依赖"
    echo ""
    echo "============================================================"
    echo "============================================================"
    echo ""
    echo "📖 详细操作说明请查看: ${INSTALL_DIR}/docs/OPERATIONS.md"
    echo ""
}

# ============================================================
# 主流程
# ============================================================
main() {
    init_install "$@"

    # ---- 仅诊断：不改代码，只查登录/数据库/证书 ----
    if [[ "$RUN_MODE" == "diagnose" ]]; then
        check_ubuntu
        # 读取现有 .env 端口用于展示
        if [[ -f "${INSTALL_DIR}/.env" ]]; then
            wp=$(grep -E '^OVPM_WEB_PORT=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d ' \r')
            [[ "$wp" =~ ^[0-9]+$ ]] && WEB_PORT="$wp"
        fi
        fix_data_permissions
        diagnose_login
        diagnose_certs
        exit 0
    fi

    # ---- 仅修复证书链路：systemd 提权 + PKI 权限 ----
    if [[ "$RUN_MODE" == "fix-certs" ]]; then
        check_ubuntu
        if [[ -f "${INSTALL_DIR}/.env" ]]; then
            wp=$(grep -E '^OVPM_WEB_PORT=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d ' \r')
            [[ "$wp" =~ ^[0-9]+$ ]] && WEB_PORT="$wp"
        fi
        fix_certs_pipeline
        exit 0
    fi

    # ---- 修复模式：清旧入口、重部署、重写 systemd、校验登录/证书 ----
    if [[ "$RUN_MODE" == "repair" ]]; then
        check_ubuntu
        if [[ -f "${INSTALL_DIR}/.env" ]]; then
            wp=$(grep -E '^OVPM_WEB_PORT=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d ' \r')
            [[ "$wp" =~ ^[0-9]+$ ]] && WEB_PORT="$wp"
            # 修复模式尽量非交互，复用已有 IP
            SERVER_IP=$(grep -E '^OVPM_SERVER_IP=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d ' \r')
            if [[ -z "$SERVER_IP" ]]; then
                SERVER_IP=$(ip route get 8.8.8.8 2>/dev/null | grep -oP 'src \K\S+' | head -1)
                SERVER_IP=${SERVER_IP:-$(hostname -I | awk '{print $1}')}
            fi
            log_info "修复模式复用配置: IP=${SERVER_IP:-auto} PORT=${WEB_PORT}"
        else
            get_server_ip
        fi
        deploy_project
        deploy_cli_tools
        install_python_deps
        setup_openvpn_config
        setup_blacklist_base
        setup_app_config
        setup_service_user
        setup_blacklist_runtime
        setup_systemd
        fix_data_permissions
        verify_install
        print_summary
        exit 0
    fi

    # ---- 默认：完整安装 / 覆盖升级 ----
    check_ubuntu
    check_port
    get_server_ip
    install_system_deps
    deploy_project
    deploy_cli_tools
    install_python_deps
    setup_openvpn
    setup_openvpn_config
    setup_blacklist_base
    setup_app_config
    setup_service_user
    setup_blacklist_runtime
    setup_systemd
    setup_firewall
    verify_install
    print_summary
}

# 执行主流程
main "$@"

exit 0


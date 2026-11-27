# 运维说明

## 路径与服务

默认安装目录 `/opt/openvpn-admin`；配置位于 `.env`，数据库为 `data/ovpn_admin.db`。Web 服务 `openvpn-admin` 使用安装目录内的 `venv/bin/python -m backend.main`。

| 布局 | OpenVPN 配置 | EasyRSA | 常用服务实例 |
| --- | --- | --- | --- |
| Nyr | `/etc/openvpn/server/server.conf` | `/etc/openvpn/server/easy-rsa` | `openvpn-server@server` |
| 经典 | `/etc/openvpn/server.conf` | `/etc/openvpn/easy-rsa` | `openvpn@server` |

实际选择会受到配置覆盖和当前活动实例影响，以安装输出和系统设置为准。网页能读取两类布局；CLI 的签发、导出要求存在 Nyr inline 文件及 `client-common.txt`，不能把它视为所有自定义 PKI 的通用管理器。

root 队列目录为 `/var/spool/ovpn-admin`，由 `ovpn-admin-jobs.path` 和 `ovpn-admin-jobs.service` 执行；固定 helper 位于 `/usr/local/sbin/ovpn-admin-priv`。不要通过取消 NoNewPrivileges 或将 Web 改成 root 来解决队列问题。

## 排查顺序

```bash
systemctl status openvpn-admin --no-pager
journalctl -u openvpn-admin -n 60 --no-pager
systemctl status ovpn-admin-jobs.path --no-pager
journalctl -u ovpn-admin-jobs.service -n 60 --no-pager
sudo ovpn-client-quick sync-hint
```

在源码目录使用 `sudo bash scripts/install.sh --diagnose` 查看诊断。该模式不重部署程序，但可能校正数据目录权限。`--fix-certs` 修复证书读取/队列，`--repair` 重部署完整平台并重启相关服务。

`.env` 中的 `OVPM_ADMIN_PASSWORD` 用于首次建库，不会在每次启动时覆盖数据库里的管理员密码。网页改密后，旧 `.env` 初始密码不会同步成为当前密码。

## 配置项

| 环境变量 | 用途 |
| --- | --- |
| `OVPM_JWT_SECRET` | 随机会话签名密钥，至少 32 字符 |
| `OVPM_ADMIN_USER` / `OVPM_ADMIN_PASSWORD` | 首次初始化管理员 |
| `OVPM_WEB_HOST` / `OVPM_WEB_PORT` | Web 监听地址/HTTP 端口 |
| `OVPM_OPENVPN_DIR` / `OVPM_OVPN_CONFIG` | OpenVPN 目录及配置路径 |
| `OVPM_OPENVPN_SERVICE` | 实际 systemd 服务实例 |
| `OVPM_EASYRSA_DIR` / `OVPM_CLIENT_COMMON` | PKI 和客户端模板 |
| `OVPM_STATUS_LOG` | OpenVPN 状态文件候选路径 |
| `OVPM_HTTPS_ENABLED` / `OVPM_HTTPS_PORT` | 内置 HTTPS 开关/端口 |
| `OVPM_SSL_CERT` / `OVPM_SSL_KEY` | Web 证书与私钥路径 |
| `OVPM_COOKIE_SECURE` | HTTPS 部署时设置为 `1`，Cookie 只经 HTTPS 发送 |
| `OVPM_ALLOWED_ORIGINS` | 跨源请求允许列表，逗号分隔；同源部署无需放开 |
| `OVPM_TRUSTED_PROXY_IPS` | 可信反向代理 IP，逗号分隔；其他来源的转发头不作为真实 IP |

改动 `.env` 后重启 Web。root helper 另读取 root 持有的 `/etc/openvpn-admin/privileged.env`，不应通过仅修改 Web 环境来假定 helper 的路径已改变。

HTTPS 生效需要证书与私钥可读。启用内置 HTTPS 后若端口低于 1024，还需确认服务保留 `CAP_NET_BIND_SERVICE`；安装器按 HTTP 端口生成能力配置，自定义高端口 HTTP 切换到 443 的情况需相应配置 systemd 能力或使用高位 HTTPS 端口。

网页“网络配置”保存的是平台生成客户端配置所用的服务器地址/端口信息，不会自动重写实际 OpenVPN 监听配置。改服务监听端口还需同步修改服务器配置及防火墙。

## 状态与速率

合法状态刷新设置为 `status /路径/status.log 5`；不使用 `status-update`，参见 [OpenVPN 官方手册](https://build.openvpn.net/man/openvpn-2.6/openvpn.8.html)。平台约每 5 秒采样，速率是相邻样本差分，不是逐包瞬时值。已有合法刷新间隔不会被安装器覆盖，过长间隔会降低显示实时性。

启动前的历史连接无法从状态快照完整重建。强制下线优先使用绑定本机的 management 接口；未配置时备用逻辑在 INPUT 链阻断来源 IPv4，可能影响同一 NAT 出口，且不自动撤销。要恢复来源访问，应检查并删除对应规则，勿清空整个防火墙。

## 黑名单

`openvpn-admin-blacklist.timer` 默认每 5 分钟刷新；期望域名和最近解析状态位于 `data/blacklist-domains.txt`、`data/blacklist-state.json`。地址集合为 `inet ovpn_admin` 下的 `blocked_ipv4` 和 `blocked_ipv6`。

```bash
sudo bash scripts/verify-blacklist.sh
sudo nft list chain inet ovpn_admin forward
sudo systemctl start openvpn-admin-blacklist.service
```

它不是 DNS 代理，客户端 DNS 返回地址可能与服务器不同；共享 IP 也可能影响其他域名。页面显示某域名属于黑名单，不保证所有子域地址已加入集合。IPv6 集合存在不等于 IPv6 规则存在：还需要隧道配置和匹配的源网段，客户端本地直出的 IPv6 不受 VPS 规则控制。

## 备份和卸载

备份数据库时停止 Web 后复制数据库及相关文件，或使用 SQLite backup API；不要只复制一个正在写入的 WAL 数据库文件。同步备份 `.env`、`/etc/openvpn` 和 root helper 配置，并限制备份读取权限。

`sudo bash scripts/uninstall.sh` 删除平台和 OpenVPN，包括证书、数据库及双栈迁移备份；不会自动备份。仅取消双栈时按 [IPv6 说明](IPV6.md) 回滚。旧入口 `uninstall-openvpn-admin-complete.sh` 只转发到同一卸载脚本。

卸载只移除专用资源，不恢复其他应用可能共享的系统全局转发开关，不清空整个 nftables 规则集或共享 systemd journal。

## 管理页面仅允许 VPN 访问

管理页面、静态资源和全部 API 均检查真实 TCP 来源地址。公网来源返回 403；
VPN 来源仍须完成验证码与密码认证。本机回环仅允许 `/api/health` 健康检查。
生产入口 `python -m backend.main` 禁用 Uvicorn 代理头解析，访问限制也不使用
`X-Forwarded-For` / `X-Real-IP`。不要改用会信任代理头的启动命令。

启动时优先读取 `OVPM_ADMIN_VPN_CIDRS`（逗号分隔的完整 CIDR）；未设置时，
从实际 OpenVPN 配置的 `server` 和 `server-ipv6` 指令读取。无法读取、缺少网段或
配置非法时拒绝启动。自定义拓扑使用显式 CIDR，例如：

```ini
OVPM_ADMIN_VPN_CIDRS=10.8.0.0/24,fd42:abcd::/64
```

示例网段必须换成实际 VPN 网段，不能直接照抄。不要填写公网客户端地址、整个
私网地址范围或默认路由。修改 `.env` 前先保留 SSH 会话并核对 `server.conf`。

连接 OpenVPN 后，使用服务器隧道内地址访问管理页面，例如 `http://10.8.0.1`，
端口按实际 Web 设置填写。若此前用公网域名/IP 登录，切换到隧道地址；防火墙
需允许 VPN 接口到 Web 端口。IPv6 需单独允许实际 VPN IPv6 网段；不在白名单内
的公网 IPv6 来源仍被拒绝。已有反向代理因来源为回环/代理地址会被拒绝，请改用
VPN 隧道内直连。无需为此改动 OpenVPN 的对外连接端口。

验收：公网访问页面、验证码和登录 API 均应返回 403；VPN 内可打开登录页面，
未登录访问业务 API 返回 401；本机健康检查返回 200。仅浏览器检测显示出口匹配
不能替代这一来源检查。来源网段限制不证明客户端证书身份；不要把该网段开放为
不可信网络，HTTPS 仍可作为额外传输保护。


## 认证与记录保留

登录/验证码及账号修改在同步工作线程执行；认证任务每个 Web 进程最多 2 个，
超过并发上限返回 429。登录按来源 IP 每分钟 10 次、账号每分钟 8 次、
全部来源合计每分钟 30 次限制密码验证；账号/IP 计数保存在 SQLite。
验证码仍为一次性、120 秒有效；这些限制用于减少资源消耗，不承诺抵挡 DDoS。

登录在密码验证后、写入会话前，在同一写事务中重新校验账号名称与密码哈希。
若期间发生改名或改密，请重新登录，不允许基于旧凭据补发有效会话。
账号修改与会话签发通过 SQLite 写事务协调。

审计记录最多保留 30 天/约 10,000 条；清理由审计写入触发，每小时最多一次，
同时删除过期和已吊销的会话。没有新操作时，旧记录将在下一次操作时清理。
应用文件日志单文件 5MB，最多 3 个备份；审计文件单文件 2MB，最多 2 个备份。
这不包含 systemd journal 和安装日志，后两者由主机日志保留策略管理。
文件日志轮转按项目默认单 Web 进程部署使用；多进程部署须改用集中日志或外部轮转。
SQLite 删除记录会复用数据库空间，不自动缩小已增长的数据库文件；不要在线强制 VACUUM。


## 边界限制与升级保留

管理 VPN 网段拒绝过宽配置（IPv4 短于 /16、IPv6 短于 /48），以及包含回环、
链路本地、多播、IPv4 映射等保留地址的范围。标准 /24 或 /64 VPN 子网可正常使用；
特殊大网段需先规划实际管理网段，不要扩大白名单以绕过检查。

POST/PUT/PATCH 请求体在 JSON 解析前受 128KiB 上限保护，分块传输同样适用。
接收请求体最多等待 10 秒；超大请求返回 413、慢请求返回 408、异常长度返回 400。
这限制应用接收请求体的开销，不替代网络层抗攻击能力。

安装修复保留已有 HTTPS 开关和端口、证书路径、Cookie 属性、JWT 有效期、
跨域配置、代理配置和自定义 VPN 来源网段，不会因修复重置为默认 HTTP。
保留代理配置不会放宽 VPN 来源限制，反向代理仍不自动获得后台访问权。
初始化账号使用 SQLite 写事务串行化，避免并发安装或启动重复创建初始管理员。
审计字段中的换行、控制字符转为空格，防止伪造多行日志。


## 安装、卸载与页面提示的含义

安装/修复结果会累计失败检查并返回非零退出码，不以步骤执行结束代替验收通过。
健康检查按保存的协议和端口访问本机地址，仅验证端点可用性，不验证 TLS 证书信任。
登录诊断不再用初始化口令请求需要验证码的登录接口；完整登录需连接 VPN 后人工验收。
诊断模式会修复数据权限并初始化缺失表，不能当作只读操作。覆盖升级时不重复显示
旧初始化口令为“本次新密码”。防火墙提示列出实际管理端口及 VPN 协议，不代表公网可登录。

卸载有软件包/账号删除失败或检测到残留时返回非零退出码，不报告完全清理。
两个卸载入口共用 scripts/uninstall.sh；不在开发环境执行真实卸载验收。

设置页的网络字段仅保存管理平台记录，不会修改 server.conf 或重启 OpenVPN。
改密成功后清除本地登录状态并跳转，不再调用已失效的退出接口。主动退出如遇网络
失败则显示“退出未完成”，不冒充服务器会话已吊销；登录密码错误保留后端具体错误，
不再误报为“会话过期”。

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
| `OVPM_SERVER_IP` | 生成客户端配置使用的明确公网 IP 或域名；缺失时依次读取客户端模板和数据库，不猜测默认路由 |
| `OVPM_TUN_MTU` / `OVPM_MSSFIX` | 新配置 MTU/MSS 默认值，默认 1500/1400 |
| `OVPM_SNDBUF` / `OVPM_RCVBUF` | OpenVPN 发送/接收缓冲，默认各 524288 字节 |
| `OVPM_KEEPALIVE_PING` / `OVPM_KEEPALIVE_TIMEOUT` | 服务端 keepalive 周期与超时，默认 10/120 秒 |
| `OVPM_KICK_BLOCK_SECONDS` | management 不可用时来源阻断上限，默认 300 秒，限制 30–3600 秒 |

改动 `.env` 后重启 Web。root helper 另读取 root 持有的 `/etc/openvpn-admin/privileged.env`，不应通过仅修改 Web 环境来假定 helper 的路径已改变。

HTTPS 生效需要证书与私钥可读。启用内置 HTTPS 后若端口低于 1024，还需确认服务保留 `CAP_NET_BIND_SERVICE`；安装器按 HTTP 端口生成能力配置，自定义高端口 HTTP 切换到 443 的情况需相应配置 systemd 能力或使用高位 HTTPS 端口。

网页“网络配置”保存的是平台生成客户端配置所用的服务器地址/端口信息，不会自动重写实际 OpenVPN 监听配置。改服务监听端口还需同步修改服务器配置及防火墙。

## 状态与速率

合法状态刷新设置为 `status /路径/status.log 5`；不使用 `status-update`，参见 [OpenVPN 官方手册](https://build.openvpn.net/man/openvpn-2.6/openvpn.8.html)。平台约每 5 秒采样，速率是相邻样本差分，不是逐包瞬时值。已有合法刷新间隔不会被安装器覆盖，过长间隔会降低显示实时性。

启动前的历史连接无法从状态快照完整重建。强制下线优先按证书名称使用绑定本机的 management 接口，并轮询状态确认会话消失；未配置或未成功时，备用逻辑在 INPUT 链限时阻断来源 IPv4，默认 300 秒，检测到客户端离线也会提前清理。共享 NAT 出口仍可能短时影响同一来源的其他用户；勿清空整个防火墙。

## OpenVPN 稳定性与吞吐调优

新安装及缺少相应指令的旧配置采用 `tun-mtu 1500`、`mssfix 1400`、`mtu-disc yes`、`sndbuf 524288`、`rcvbuf 524288`。安装修复只补充缺失项，不覆盖管理员已有值。`fragment` 默认保持注释，因为现代网络和防火墙经常丢弃 IP 分片；只应在确认链路必须使用且经过专项测试后启用。

客户端配置默认协商 `AES-128-GCM:AES-256-GCM` 并以 AES-128-GCM 回退。已有客户端仍可连接，只是不会自动得到新的 MTU、缓冲参数；需要重新下载配置并逐台替换。升级会重启 OpenVPN、短暂中断现有隧道，因此应保留独立 SSH 会话并安排维护窗口。此次优化不加入 `float`、额外 `ping-restart` 或激进自动重连参数。

优先使用 UDP；TCP 仅作为 UDP 被网络限制时的兼容入口，不能只改客户端协议而不配置对应服务端实例、防火墙和独立端口。不要在同一配置里盲目堆叠多端口。检查 CPU 是否支持 AES 指令可用：

```bash
lscpu | grep -i aes || grep -m1 -o '\baes\b' /proc/cpuinfo
```

MTU 应从客户端到 VPN 内目标实测，逐步降低载荷直到不分片成功；IPv4 常用起点如下，`-s` 不含 28 字节 ICMP/IP 头：

```bash
ping -M do -s 1472 -c 3 10.8.0.1
ping -M do -s 1372 -c 3 10.8.0.1
```

升级前先备份 `.env`、服务端配置和 `client-common.txt`。在维护窗口停止对应 OpenVPN 实例后，可用实际版本支持的配置启动检查确认无未知指令，再恢复 systemd 服务；最终以服务状态和日志为准：

```bash
openvpn --version
openvpn --config /etc/openvpn/server/server.conf --verb 4
systemctl restart openvpn-server@server
systemctl status openvpn-server@server --no-pager
journalctl -u openvpn-server@server -n 80 --no-pager
```

回滚时从升级备份恢复 `server.conf`、`client-common.txt` 和 `.env`，恢复上一版程序与 root helper，再重启 OpenVPN 和 `openvpn-admin`。不要只回滚一侧的加密或 MTU配置。DCO 暂不自动启用；只有服务器与客户端 OpenVPN、内核、驱动及所用指令全部兼容，并完成独立压测后再评估。

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

审计记录最多保留 30 天/约 10,000 条；Web 启动后及运行期间每小时执行一次
过期记录清理，审计写入也可触发清理。过期验证码、限流记录以及失效会话一并清理。
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

## 完整修复与缓存刷新

从独立的最新完整源码目录运行 `sudo bash scripts/install.sh --repair`，不要在
`/opt/openvpn-admin` 内直接执行自覆盖，也不要使用已停用的 `upgrade-ipv6-ui.sh`。
`--fix-certs` 仅修复已安装版本的证书权限和服务单元，不再夹带代码升级。

完整修复先在独立 `.venv-release.*` 目录安装依赖、执行 pip check、导入新应用；
对现有数据库以只读连接生成副本，在副本中检查建表与所需字段，不改原数据库。
预检失败时，旧服务、旧代码与旧环境保持原状。无法识别的旧数据库和自定义数据
目录会要求人工迁移，不会静默覆盖。新环境使用固定路径，激活时更新 venv 入口。

预检通过后先停止旧 Web 服务，再替换 backend/frontend/docs 等受管代码目录。
新版不再携带旧模块和 __pycache__；data、logs、证书和现有 .env 不随代码目录删除。
旧代码、.env、数据库快照及旧环境入口保存在 `.upgrade-backups` 下，目录仅 root 可读。
替换阶段异常会尝试恢复本轮移动的代码与环境入口，并尝试启动原先运行的 Web 服务。

这不是整台服务器的事务回滚：后续 systemd、root helper、VPN、防火墙配置或数据库
实际初始化失败，仍需通过独立 SSH 查看失败步骤和备份；不要直接以旧数据库快照
覆盖运行中的库，以免丢失升级后的写入。完整修复可能重启 VPN，应安排维护时间。
新旧环境及快照需要额外磁盘空间；确认升级验收完成后再人工清理不再使用的环境和备份。

HTML、静态资源和 API 响应标记为 no-store；页面中的 JS/CSS 使用内容摘要作为版本参数。
已打开的旧页面不会凭空替换自身代码，升级后请重新加载；第一次建议 Ctrl+Shift+R
强制刷新。旧浏览器此前已缓存的响应不会被服务器远程删除。支持 `/` 和 `/index.html`
两个登录入口，避免退出或过期跳转到不存在的地址。新版资源与头部随 Web 服务重启生效。

## 定期自清理

Web 服务启动后执行一次，运行期间每小时在工作线程中清理过期验证码、过期限流记录、
过期/已吊销登录会话；审计记录沿用既有的 30 天/约 10,000 条保留策略，文件日志继续轮转。
定期清理与访问触发的清理共用数据库事务和进程锁，不依赖有人访问后台。失败记录到日志，
下一周期重试，不使 Web 服务退出。Web 停止时任务停止，下次启动继续执行，无额外常驻服务。

系统设置的“自动清理”显示本 Web 进程最近执行状态。有效验证码、有效限流状态、有效会话、
管理员、证书、VPN 配置、升级备份和新旧虚拟环境均不因该任务删除。不会执行系统 drop_caches、
VACUUM 或任意目录清扫；服务器也不能远程清除浏览器此前存储的缓存。

# OpenVPN Admin

当前版本：**5.1.0**。使用网页管理 OpenVPN 客户端证书、连接状态、连接历史、域名黑名单和服务器网络设置。

前端使用原生 HTML/CSS/JavaScript，后端使用 FastAPI 和 SQLite。Web 进程以 `ovpn-admin` 用户运行，保留 `NoNewPrivileges=true`；证书与黑名单操作由独立 root 任务队列及固定白名单 helper 执行。

## 功能与边界

| 功能 | 当前行为 |
| --- | --- |
| 登录与会话 | HttpOnly Cookie、签名 JWT、服务端会话吊销；改密使全部旧会话失效 |
| 登录保护 | 一次性图形验证码、持久化限流与失败锁定；修改用户名或密码后旧会话失效 |
| 管理入口 | 页面、静态资源和 API 只允许从已配置的 VPN 网段访问；登录仍需验证码与密码 |
| 客户端证书 | 创建、下载配置、吊销、扫描并导入已有 EasyRSA 证书 |
| 在线连接 | 状态文件解析、连接时长、流量与约 5 秒采样的平均速率 |
| 历史连接 | 记录平台运行期间采样到的连接；不是 OpenVPN 历史日志的完整回放 |
| 强制下线 | 优先 management 接口；备用路径阻断来源 IPv4，可能影响共享同一出口的客户端，规则不自动过期 |
| 域名黑名单 | 在服务器解析域名为 IP，再通过 nftables 拦截经过 VPN 服务器的流量；不是 DNS 代理 |
| IPv6 检测 | 检查公网地址、默认路由及 HTTPS IPv6 出网；不能代替服务商开通 IPv6 |
| 双栈升级 | 满足条件后下载独立升级脚本，在 SSH 中应用；包含备份、失败回滚和开机加载配置 |
| HTTP / HTTPS | 默认 HTTP；配置证书、私钥、端口并重启 Web 后支持 HTTPS |
| 自动清理 | Web 启动后及每小时清理过期验证码、限流记录和失效会话，沿用审计保留策略 |

黑名单不能保证覆盖 CDN 的所有地址、未列出的子域名或未经过服务器的流量。页面“解析地址已记录到同步状态”不等同客户端实测拦截成功。双栈脚本已通过命令模拟测试，真实 NAT66、重启后的规则及 Windows 客户端路由仍需部署验收。

## 环境要求

- Ubuntu 22.04 或更新版本、systemd、Python 3.10+；安装和修复操作需要 root。
- OpenVPN 2.5/2.6、EasyRSA、nftables；支持 Nyr 和经典目录布局。
- 安装过程需访问系统软件源、Python 软件源及 EasyRSA 下载源。
- 使用 HTTPS 时，Web 用户需要读取证书和私钥的必要权限；无需将 Web 改为 root。

## 安装与升级

上传并解压源码后，从项目根目录执行：

```bash
sudo bash scripts/install.sh
```

已有平台更新到本版本，可执行：

```bash
sudo bash scripts/install.sh --repair
```

更新前备份 `/opt/openvpn-admin/data`、`/opt/openvpn-admin/.env` 和 `/etc/openvpn`。修复会重部署程序、修正权限并重启服务；请保留 SSH 会话。数据库和现有管理员账号保留，不把 `.env` 中的初始密码当作改密后的当前密码。

安装完成后先连接 VPN，再使用输出的隧道内管理地址和管理员账号登录；公网来源会收到 403。已有数据库沿用现有账号，首次安装使用初始化账号。旧的 `scripts/upgrade-ipv6-ui.sh` 已停用，避免新旧模块混用；完整升级请从独立源码目录使用 `--repair`。升级后首次打开页面建议强制刷新。

| 命令 | 用途 |
| --- | --- |
| `sudo bash scripts/install.sh --repair` | 重部署平台、修复启动与证书权限 |
| `sudo bash scripts/install.sh --diagnose` | 诊断登录、数据库和证书；可能校正数据目录权限，不是纯只读检查 |
| `sudo bash scripts/install.sh --fix-certs` | 修复证书读取权限及 root 任务队列 |
| `sudo bash scripts/preflight-real-server.sh` | 部署后检查服务、队列、黑名单及日志 |
| `sudo bash scripts/verify-blacklist.sh` | 检查黑名单基础环境与规则 |

## 日常使用

网页依次提供仪表盘、客户端、在线连接、域名黑名单、审计日志和系统设置。API 文档位于 `/api/docs`；页面、静态资源和 API 仅允许 VPN 网段访问，登录后业务 API 还需要 Cookie 认证。回环健康检查仅供本机诊断。

网页暂时不可用时，可通过 SSH 使用安装器部署的 CLI：

```bash
sudo ovpn-client-quick list
sudo ovpn-client-quick add alice
sudo ovpn-client-quick download alice
sudo ovpn-client-quick revoke alice
sudo ovpn-client-quick status
sudo ovpn-client-quick sync-hint
```

CLI 的签发与导出依赖 EasyRSA inline 文件和 `client-common.txt`；适用情况见 [运维说明](docs/OPERATIONS.md)。

## IPv6 与双栈

在“系统设置 → IPv6 与双栈 VPN”点击检测。服务器没有可用 IPv6 时，不允许生成迁移入口；先到 VPS 服务商控制台确认分配或开通选项。

检测通过后下载脚本并上传 VPS：

```bash
python3 ovpn-dualstack-upgrade.py
sudo python3 ovpn-dualstack-upgrade.py --apply --confirm-disconnect
```

应用会重启 OpenVPN 并断开客户端。重新连接后，分别检查 IPv4 和 IPv6 的实际出口及黑名单。[双栈操作与回滚](docs/IPV6.md) 包含支持范围和验收步骤。

## 测试

安装开发依赖并准备 Node.js 20+ 后：

```bash
python3 -m pip install -r requirements-dev.txt
bash scripts/test.sh
```

自动测试使用临时数据库，并模拟 DNS、特权命令和网络迁移，不修改真实服务器网络。[测试说明](docs/TESTING.md) 区分自动测试与必须在真实服务器执行的验收。GitHub Actions 配置位于 `.github/workflows/test.yml`；实际运行结果以对应提交的 Actions 状态为准。

## 卸载

```bash
sudo bash scripts/uninstall.sh
```

这是**彻底卸载**：删除平台数据库、OpenVPN 配置、证书和密钥、专用黑名单与双栈配置及迁移备份，并尝试卸载 OpenVPN/EasyRSA 软件包。不会自动备份；必须输入 `DELETE-ALL-OPENVPN-DATA` 才会继续。若只想撤销双栈配置而保留 VPN，请使用 [双栈回滚](docs/IPV6.md)，不要运行卸载脚本。

## 文件结构

| 路径 | 作用 |
| --- | --- |
| `backend/` | API、认证、数据库、证书、会话跟踪和网络逻辑 |
| `frontend/` | 网页与静态资源 |
| `scripts/install.sh` | 安装、升级、修复和诊断 |
| `scripts/deploy_release.py` / `preflight_release.py` | 升级时的源码替换与预检，供安装器调用 |
| `scripts/preflight-real-server.sh` / `verify-blacklist.sh` / `cert_pipeline_diag.sh` | 真实服务器上的诊断入口 |
| `scripts/privileged/` | root helper、队列 worker、黑名单同步器 |
| `scripts/cli/` | SSH 下可用的客户端证书管理 CLI |
| `scripts/vendor/` | 保留来源信息的内置 Nyr 安装脚本 |
| `tests/` | 自动回归、安全检查和需人工执行的服务器验收脚本 |
| `docs/` | 运维、IPv6 与测试说明 |
| `CHANGELOG.md` | 唯一更新记录 |
| `DESIGN.md` | 当前前端视觉规范 |

仓库与发布包不包含 `.env`、运行数据库、日志、私钥或生成的 `.ovpn` 配置。

内置安装脚本来自 [Nyr/openvpn-install](https://github.com/Nyr/openvpn-install)，其源码保留原作者声明，许可证见 [scripts/vendor/LICENSE](scripts/vendor/LICENSE)。

`scripts/upgrade-ipv6-ui.sh` 是已停用的旧升级入口，执行时只提示改用完整升级；`scripts/verify-v4.2-blacklist.sh` 是旧诊断命令的兼容转发入口。它们不参与新版本部署，保留是为了让旧操作命令给出明确结果。

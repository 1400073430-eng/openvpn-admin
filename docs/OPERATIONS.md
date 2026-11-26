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

# IPv6 检测、双栈升级与回滚

## 服务器条件

在网页“系统设置 → IPv6 与双栈 VPN”点击检测，检查公网地址、默认路由和一次 IPv6 HTTPS 查询。是否有 IPv6 分配/开通选项、是否收费，必须到 VPS 服务商控制台确认；程序无法从“当前无地址”推断“服务商不提供”。

检测请求发送到 `https://api64.ipify.org`，不继承代理。接口超时或无法访问会显示未通过，不能单凭该失败判断整个 IPv6 网络不可用。当前无可用 IPv6 时，程序不修改网络，也不会自动购买、申请 IPv6 或解决客户端本地 IPv6 绕过。

## 支持的自动迁移

- 标准 Nyr 或经典 OpenVPN systemd 实例正在运行。
- Linux/systemd，具备 iproute2、curl、nftables；服务器原生 IPv6 出网可用。
- 使用内部网段 `fd76:706e:6164::/64`，通过出口 NAT66 提供公网访问；不是分配独立公网 IPv6 给每个客户端。
- 已有第三方 IPv6 配置、额外 config/CCD、活动 ufw/firewalld、其他 IPv6 forward 链或专用资源冲突时，拒绝自动覆盖，需人工合并。
- IPv6 黑名单规则由原同步器读取 `server-ipv6` 网段后生成。已有黑名单链在双栈放行链之前处理。

## 操作步骤

1. 从服务商开通 IPv6，并在服务器配置地址、路由后重新检测。
2. 检测及基本配置兼容性检查通过，页面允许下载脚本。下载本身不改变网络。
3. 将脚本上传服务器，保留独立 SSH 或供应商控制台连接，再执行：

```bash
python3 ovpn-dualstack-upgrade.py
sudo python3 ovpn-dualstack-upgrade.py --apply --confirm-disconnect
```

预检不修改网络；应用会再次检查条件，备份配置，然后设置双栈地址/路由、系统转发、RA 接收策略、专用 NAT66 表及开机服务。应用会重启 OpenVPN、断开客户端。Web 仍保持非 root，不新增远程 root 命令执行接口。

备份及事务状态：`/var/lib/ovpn-admin-dualstack`，root 0700。专用资源：

| 资源 | 路径/名称 |
| --- | --- |
| nftables 表 | `inet ovpn_dualstack` |
| nftables 配置 | `/etc/openvpn-admin/dualstack.nft` |
| 转发与 RA 配置 | `/etc/sysctl.d/90-ovpn-admin-dualstack.conf` |
| 开机服务 | `ovpn-admin-dualstack.service` |

应用失败会尝试恢复原配置和运行时开关、移除专用服务及表，再重启原实例；回滚自身失败会报错，需要检查备份，不宣称已恢复。为保护备份，脚本拒绝重复覆盖既有事务目录。

## 客户端验收

重新连接 VPN 后在 Windows PowerShell 执行：

```powershell
curl.exe -4 --max-time 15 https://api.ipify.org
curl.exe -6 --max-time 15 https://api64.ipify.org
```

IPv4 应是 VPS 出口，IPv6 应是 VPS 的 IPv6 出口，不能仍是连接 VPN 前的本地宽带 IPv6。随后用自有测试目标验证 IPv4/IPv6 黑名单及规则计数；重启 VPS 后重复检查。

服务器自身能 IPv6 出网不证明客户端路由已生效。自定义客户端分流、拒绝推送、额外防火墙和其他网络工具均需检查。若其他服务重载并清空 nftables 规则或出口接口变化，应重新检查专用服务；此实现不是持续自动修复守护进程。

## 回滚

```bash
sudo python3 ovpn-dualstack-upgrade.py --rollback --confirm-disconnect
```

回滚同样会重启 OpenVPN。它恢复迁移前完整 server.conf；如果应用后手工修改了配置，请先保存这些变更。回滚后要再次部署，应先由管理员核对并归档旧事务目录。

若只想撤销双栈，使用回滚；彻底卸载脚本会删除整个 VPN，包括双栈备份，不能用来代替回滚。

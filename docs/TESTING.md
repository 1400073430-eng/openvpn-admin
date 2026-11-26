# 测试与部署验收

## 本地测试

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
bash scripts/test.sh
```

前端测试需要 Node.js 20+，不需要 npm 安装。统一入口包含：未使用导入/变量与未定义名称检查、Python 单元/接口测试、前端交互模拟、既有静态安全检查及所有项目 Shell/Python/前端 JS 的语法检查。

| 覆盖内容 | 方法 |
| --- | --- |
| 登录、失败锁定、注销和改密吊销旧会话 | 真实 SQLite、真实 Cookie/JWT 与 FastAPI TestClient |
| 31 个已有 API 的保留与未登录拒绝 | 对照 4.3.9 路径/方法快照，逐个请求实际应用 |
| 黑名单增删启停、重复提交、批量上限 | 真实数据库，模拟 root 同步成功/失败 |
| 黑名单失败后的文件/数据库回滚 | 检查原状态、备注和时间字段保留 |
| 状态文件解析、UNDEF 过滤、证书接口 | 内置自测，不签发真实证书 |
| OpenVPN 状态配置修复 | 将实际安装函数中的系统路径替换为临时目录后执行 |
| IPv6 条件检测及迁移/回滚 | 模拟系统命令，检查配置备份与恢复 |
| IPv6 页面按钮、错误恢复、HTML 转义 | Node.js DOM/API 模拟，不代替真实浏览器完整验收 |

自动测试不修改真实服务器路由、防火墙或 OpenVPN 配置，不发起真实证书签发。内置自测不会用替代 JWT 算法掩盖依赖缺失；域名自测明确使用模拟 A/AAAA 结果，不等于真实 DNS 或 nftables 拦截已通过。

`.github/workflows/test.yml` 配置了 Python 3.10/3.12 自动测试。发布前的本地通过不代表 GitHub Actions 已运行；远端执行结果应查看对应提交的 Actions 状态。

## 本次本地记录

本次本地测试包括 33 个 Python 测试、前端交互模拟、静态检查和语法检查。最终在新建 Python 3.12.14 虚拟环境安装 requirements-dev.txt 后执行全部测试通过，pip check 无依赖冲突；本地未运行 Python 3.10 矩阵。没有在真实 VPS 上进行安装、证书签发、NAT66 出网或开机验收，不以模拟测试宣称这些功能已经实机验证。

## 真实服务器验收

先备份并保留供应商控制台访问，再在测试服务器执行：

```bash
sudo bash scripts/preflight-real-server.sh
sudo bash tests/install_acceptance.sh
sudo bash scripts/verify-blacklist.sh
```

还需实际验证：

1. 安装/完整升级后 Web 仍为非 root，NoNewPrivileges 保留，root worker 可接收任务。
2. 现有密码可登录；退出、改密后旧会话拒绝；失败阈值按预期锁定。
3. 新建独立测试客户端、下载配置并连接；已有客户端仍可用；吊销测试证书后无法重新连接。
4. 在线速率、连接时长及离线历史正常；UNDEF 不冒充客户端。
5. 自有域名测试目标的黑名单启停生效；失败重试不会残留假成功记录。
6. 双栈升级、客户端出口、规则计数、VPS 重启和回滚按 [IPv6 说明](IPV6.md) 验证。
7. HTTP 或配置后的 HTTPS 可访问，页面协议配置显示正确。
8. 彻底卸载只在一次性测试服务器上验收；不在保留业务/证书的 VPS 上直接运行。

`--diagnose` 可能修正目录权限。`tests/install_acceptance.sh` 和在线检查需要真实服务条件，不能在普通开发环境运行后把环境缺失算作代码缺陷，也不能跳过后声称实机通过。

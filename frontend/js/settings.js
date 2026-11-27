/**
 * 系统设置页面
 * - 路径信息展示
 * - 网络配置
 * - 服务状态
 * - 修改管理员密码
 * - 自测入口
 */
(function () {
  "use strict";

  Layout.init("系统设置", "settings");
  var content = Layout.content();

  content.innerHTML =
    // 服务状态
    '<div class="panel">' +
      '<div class="panel-header"><h2 class="panel-title">服务状态</h2>' +
      '<button class="btn btn-secondary btn-sm" id="btn-selftest">运行自测</button></div>' +
      '<div class="panel-body" id="services-panel">加载中...</div>' +
    '</div>' +
    '<div class="panel"><div class="panel-header"><h2 class="panel-title">节点出口检测</h2></div>' +
    '<div class="panel-body"><p>手动向 ipify.org 和 ipwho.is 查询服务器出口及归属；查询服务会收到出口 IP。结果缓存 5 分钟。</p>' +
    '<button type="button" class="btn btn-secondary mt-2" id="btn-node-check">检测节点</button>' +
    '<div id="node-check-result" class="mt-2">尚未检测。此功能不检测黑名单、滥用历史或客户端泄漏。</div></div></div>' +
    // 网络配置
    '<div class="panel">' +
      '<div class="panel-header"><h2 class="panel-title">网络配置</h2></div>' +
      '<div class="panel-body">' +
        '<form id="form-network">' +
          '<div class="form-row">' +
            '<div class="form-group">' +
              '<label class="form-label">服务器 IP</label>' +
              '<input class="form-input" id="net-server-ip" placeholder="例如：192.168.1.100" maxlength="64">' +
              '<div class="form-error"></div>' +
            '</div>' +
            '<div class="form-group">' +
              '<label class="form-label">VPN 端口</label>' +
              '<input class="form-input" id="net-vpn-port" type="number" min="1" max="65535">' +
              '<div class="form-error"></div>' +
            '</div>' +
          '</div>' +
          '<button type="button" class="btn btn-primary" id="btn-save-network">保存配置</button>' +
        '</form>' +
      '</div>' +
    '</div>' +
    // IPv6 检测与双栈升级
    '<div class="panel">' +
      '<div class="panel-header"><h2 class="panel-title">IPv6 与双栈 VPN</h2></div>' +
      '<div class="panel-body">' +
      '<p>是否可分配或开通 IPv6，需到 VPS 服务商控制台确认；本页检测服务器当前配置。</p>' +
      '<button class="btn btn-secondary mt-2" id="btn-ipv6-probe">检测 IPv6</button> ' +
      '<button class="btn btn-primary mt-2" id="btn-ipv6-script" disabled>下载双栈升级脚本</button>' +
      '<div id="ipv6-result" class="mt-2">尚未检测。检测会向公网 IP 查询服务发起一次 IPv6 HTTPS 请求。</div>' +
      '<p class="mt-2">下载不修改网络。将脚本上传 VPS，在 SSH 中执行：<code>python3 ovpn-dualstack-upgrade.py</code> 预检；' +
      '<code>sudo python3 ovpn-dualstack-upgrade.py --apply --confirm-disconnect</code> 应用。应用会重启 VPN、断开客户端，建议保留 SSH 会话。</p>' +
      '<p>回滚：<code>sudo python3 ovpn-dualstack-upgrade.py --rollback --confirm-disconnect</code>。' +
      '升级后请重新连接，验证 IPv4、IPv6 出口及黑名单；服务端检测通过不等于客户端已完成验收。</p>' +
      '</div></div>' +
    // 路径信息
    '<div class="panel">' +
      '<div class="panel-header"><h2 class="panel-title">路径信息</h2></div>' +
      '<div class="panel-body" id="paths-panel">加载中...</div>' +
    '</div>' +
    // HTTPS 当前配置
    '<div class="panel">' +
      '<div class="panel-header"><h2 class="panel-title">HTTPS 配置</h2></div>' +
      '<div class="panel-body" id="https-panel">加载中...</div>' +
    '</div>' +
    // 修改用户名
    '<div class="panel">' +
      '<div class="panel-header"><h2 class="panel-title">修改管理员用户名</h2></div>' +
      '<div class="panel-body"><form id="form-username">' +
        '<div class="form-group"><label class="form-label">新用户名 *</label>' +
          '<input class="form-input" id="admin-new-username" autocomplete="username" maxlength="32" placeholder="3-32 位字母、数字或下划线"><div class="form-error"></div></div>' +
        '<div class="form-group"><label class="form-label">当前密码 *</label>' +
          '<input class="form-input" type="password" id="admin-current-password" autocomplete="current-password" maxlength="128"><div class="form-error"></div></div>' +
        '<button type="button" class="btn btn-primary" id="btn-save-username">修改用户名</button>' +
        '<p class="mt-2 text-secondary">修改后所有管理会话将退出，请用新用户名和原密码重新登录。</p>' +
      '</form></div>' +
    '</div>' +
    // 修改密码
    '<div class="panel">' +
      '<div class="panel-header"><h2 class="panel-title">修改管理员密码</h2></div>' +
      '<div class="panel-body">' +
        '<form id="form-password">' +
          '<div class="form-group">' +
            '<label class="form-label">当前密码 *</label>' +
            '<input class="form-input" type="password" id="pwd-old" maxlength="128">' +
            '<div class="form-error"></div>' +
          '</div>' +
          '<div class="form-group">' +
            '<label class="form-label">新密码 *</label>' +
            '<input class="form-input" type="password" id="pwd-new" placeholder="至少 8 位" maxlength="128">' +
            '<div class="form-error"></div>' +
          '</div>' +
          '<div class="form-group">' +
            '<label class="form-label">确认新密码 *</label>' +
            '<input class="form-input" type="password" id="pwd-confirm" maxlength="128">' +
            '<div class="form-error"></div>' +
          '</div>' +
          '<button type="button" class="btn btn-primary" id="btn-save-password">修改密码</button>' +
        '</form>' +
      '</div>' +
    '</div>';

  // ========== 加载设置 ==========
  function loadSettings() {
    Api.get("/api/settings").then(function (resp) {
      var d = resp.data || {};

      // 服务状态
      var svc = d.services || {};
      var svcHtml = "";
      ["openvpn", "blacklist"].forEach(function (key) {
        var s = svc[key] || {};
        var badge = s.active
          ? '<span class="badge badge-success">运行中</span>'
          : '<span class="badge badge-danger">' + escHtml(s.state || "未知") + "</span>";
        svcHtml += '<div class="mt-1"><strong>' + escHtml(key) + "</strong>（" + escHtml(s.service || "") + "）：" + badge + "</div>";
      });
      document.getElementById("services-panel").innerHTML = svcHtml || "无服务信息";

      // 网络
      var net = d.network || {};
      document.getElementById("net-server-ip").value = net.server_ip || "";
      document.getElementById("net-vpn-port").value = net.vpn_port || 1194;

      // 路径
      var paths = d.paths || {};
      var pathHtml = '<div class="table-wrapper"><table class="table">';
      pathHtml += "<thead><tr><th>项目</th><th>路径</th></tr></thead><tbody>";
      var labels = {
        easyrsa_dir: "EasyRSA 目录",
        openvpn_dir: "OpenVPN 目录",
        status_log: "状态日志",
        database: "数据库文件",
        client_cert_dir: "客户端证书目录"
      };
      for (var k in labels) {
        pathHtml += "<tr><td>" + labels[k] + '</td><td class="font-mono">' + escHtml(paths[k] || "-") + "</td></tr>";
      }
      pathHtml += "</tbody></table></div>";
      document.getElementById("paths-panel").innerHTML = pathHtml;

      // HTTPS
      var https = d.https || {};
      document.getElementById("https-panel").innerHTML =
        "<p>服务启动协议配置：<strong>" + (https.enabled ? "HTTPS" : "HTTP") + "</strong></p>" +
        '<p class="mt-1 text-secondary">' + escHtml(https.note || "") + "</p>" +
        '<p class="mt-1 font-mono">证书路径：' + escHtml(https.cert_file || "-") + "</p>" +
        '<p class="font-mono">私钥路径：' + escHtml(https.key_file || "-") + "</p>";

    }).catch(function (err) {
      Toast.error(err.message || "加载设置失败");
    });
  }

  // ========== 保存网络配置 ==========
  document.getElementById("btn-save-network").addEventListener("click", function () {
    var ipEl = document.getElementById("net-server-ip");
    var portEl = document.getElementById("net-vpn-port");
    Validator.clearAllErrors(document.getElementById("form-network"));

    var ip = ipEl.value.trim();
    var port = parseInt(portEl.value, 10);
    var valid = true;

    if (ip && !/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) {
      Validator.showError(ipEl, "IP 地址格式不正确");
      valid = false;
    }
    if (!port || port < 1 || port > 65535) {
      Validator.showError(portEl, "端口需为 1-65535");
      valid = false;
    }
    if (!valid) return;

    var btn = this;
    btn.disabled = true;
    Api.put("/api/settings", {
      server_ip: ip || undefined,
      vpn_port: port
    }).then(function () {
      Toast.success("配置已保存");
      loadSettings();
    }).catch(function (err) {
      Toast.error(err.message || "保存失败");
    }).then(function () {
      btn.disabled = false;
    });
  });

  // ========== 修改用户名 ==========
  document.getElementById("btn-save-username").addEventListener("click", function () {
    var userEl = document.getElementById("admin-new-username");
    var pwdEl = document.getElementById("admin-current-password");
    Validator.clearAllErrors(document.getElementById("form-username"));
    var newUsername = userEl.value.trim();
    if (!/^[a-zA-Z0-9_]{3,32}$/.test(newUsername)) {
      Validator.showError(userEl, "用户名需为 3-32 位字母、数字或下划线");
      return;
    }
    if (!pwdEl.value) {
      Validator.showError(pwdEl, "请输入当前密码");
      return;
    }
    var btn = this;
    btn.disabled = true;
    Api.post("/api/auth/change-username", {new_username: newUsername, current_password: pwdEl.value})
      .then(function () {
        Auth.clear();
        window.location.href = "/index.html";
      }).catch(function (err) {
        Toast.error(err.message || "修改用户名失败");
      }).then(function () { btn.disabled = false; });
  });

  // ========== 修改密码 ==========
  document.getElementById("btn-save-password").addEventListener("click", function () {
    var oldEl = document.getElementById("pwd-old");
    var newEl = document.getElementById("pwd-new");
    var confirmEl = document.getElementById("pwd-confirm");
    Validator.clearAllErrors(document.getElementById("form-password"));

    var oldPwd = oldEl.value;
    var newPwd = newEl.value;
    var confirmPwd = confirmEl.value;
    var valid = true;

    if (!oldPwd) {
      Validator.showError(oldEl, "请输入当前密码");
      valid = false;
    }
    if (!Validator.isPassword(newPwd)) {
      Validator.showError(newEl, "新密码至少 8 位");
      valid = false;
    }
    if (newPwd !== confirmPwd) {
      Validator.showError(confirmEl, "两次密码不一致");
      valid = false;
    }
    if (oldPwd && newPwd && oldPwd === newPwd) {
      Validator.showError(newEl, "新密码不能与旧密码相同");
      valid = false;
    }
    if (!valid) return;

    var btn = this;
    btn.disabled = true;
    btn.textContent = "提交中...";

    Api.post("/api/auth/change-password", {
      old_password: oldPwd,
      new_password: newPwd
    }).then(function () {
      Toast.success("密码修改成功，请重新登录");
      setTimeout(function () {
        Auth.logout();
      }, 1500);
    }).catch(function (err) {
      Toast.error(err.message || "修改失败");
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "修改密码";
    });
  });

  // ========== 自测 ==========
  document.getElementById("btn-selftest").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true;
    btn.textContent = "自测中...";

    Api.post("/api/selftest", {}).then(function (resp) {
      var d = resp.data || {};
      if (d.all_passed) {
        Toast.success("自测全部通过（" + d.passed + "/" + d.total + "）");
      } else {
        Toast.error("自测未全部通过（" + d.passed + "/" + d.total + "）");
      }
      // 展示详情
      var html = '<div class="mt-2">';
      (d.results || []).forEach(function (r) {
        var icon = r.passed ? "✅" : "❌";
        html += "<p>" + icon + " " + escHtml(r.name) + "：" + escHtml(r.message) + "</p>";
      });
      html += "</div>";
      var panel = document.getElementById("services-panel");
      panel.insertAdjacentHTML("beforeend", html);
    }).catch(function (err) {
      Toast.error(err.message || "自测执行失败");
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "运行自测";
    });
  });

  document.getElementById("btn-ipv6-probe").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true;
    document.getElementById("btn-ipv6-script").disabled = true;
    document.getElementById("ipv6-result").textContent = "检测中，请稍候…";
    Api.get("/api/settings/ipv6").then(function (resp) {
      var d = resp.data || {};
      var addresses = (d.addresses || []).map(function (a) { return a.interface + "：" + a.address; });
      var lines = [d.ready ? "服务器 IPv6 出网检测通过" : "尚未检测到完整可用的 IPv6 出口",
        "公网地址：" + (addresses.join("；") || "无"),
        "IPv6 默认路由：" + ((d.default_routes || []).length ? "有" : "无"),
        "实际出口：" + (d.egress_ipv6 || "未验证"), d.provider_note, d.vpn_note];
      (d.errors || []).forEach(function (e) { lines.push(e); });
      document.getElementById("ipv6-result").innerHTML = lines.map(function (line) {
        return "<p>" + escHtml(line || "") + "</p>";
      }).join("");
      document.getElementById("btn-ipv6-script").disabled = !d.upgrade_supported;
    }).catch(function (err) {
      document.getElementById("ipv6-result").textContent = err.message || "检测失败，可重新检测";
    }).then(function () { btn.disabled = false; });
  });
  document.getElementById("btn-ipv6-script").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true;
    Api.post("/api/settings/ipv6/upgrade-script", {}).then(function (resp) {
      var blob = new Blob([resp.data.script], {type: "text/x-python;charset=utf-8"});
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "ovpn-dualstack-upgrade.py";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      Toast.success("脚本已下载；尚未应用网络配置，请在 SSH 中预检后执行。");
    }).catch(function (err) { Toast.error(err.message || "下载失败"); })
      .then(function () { btn.disabled = false; });
  });
  document.getElementById("btn-node-check").addEventListener("click", function () {
    var btn = this;
    var output = document.getElementById("node-check-result");
    btn.disabled = true;
    output.textContent = "检测中，请稍候…";
    Api.post("/api/settings/node-check", {}).then(function (resp) {
      var data = resp.data;
      var html = "<p>检测时间：" + escHtml(data.checked_at) + (data.cached ? "（缓存）" : "") + "</p>";
      (data.results || []).forEach(function (r) {
        html += "<p><strong>IPv" + escHtml(r.version) + "</strong>：" + escHtml(r.ip || r.note || "查询失败") + "</p>";
        if (r.metadata_status === "ok") {
          html += "<p>地区：" + escHtml([r.country, r.region, r.city].join(" / ")) +
            "；ASN：" + escHtml(r.asn) + "；运营商：" + escHtml(r.isp) + "；组织：" + escHtml(r.organization) + "</p>";
        } else if (r.ip) { html += "<p>归属信息查询失败，未判定为安全或危险。</p>"; }
      });
      output.innerHTML = html + "<p>" + escHtml(data.note) + "</p>";
    }).catch(function (err) { output.textContent = err.message || "检测失败"; })
      .then(function () { btn.disabled = false; });
  });
  loadSettings();
})();

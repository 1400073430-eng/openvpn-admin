/** 在线连接 + VPN 会话历史。 */
(function () {
  "use strict";
  Layout.init("在线连接", "connections");
  var content = Layout.content();
  content.innerHTML =
    '<div class="panel"><div class="panel-header"><h2 class="panel-title">在线客户端</h2><button class="btn btn-secondary btn-sm" id="btn-refresh">刷新</button></div>' +
    '<div class="panel-body no-padding"><div class="table-wrapper"><table class="table"><thead><tr>' +
    '<th>客户端</th><th>虚拟IP</th><th>公网IP</th><th>上线时间</th><th>连接时长</th><th>最后采样</th><th>实时上行</th><th>实时下行</th><th>累计上行</th><th>累计下行</th><th>操作</th>' +
    '</tr></thead><tbody id="conn-tbody"><tr><td colspan="11" class="text-center text-secondary">加载中...</td></tr></tbody></table></div></div></div>' +
    '<div class="panel"><div class="panel-header"><h2 class="panel-title">VPN 连接历史</h2><span class="text-secondary">每条记录代表一次连接</span></div>' +
    '<div class="panel-body no-padding"><div class="table-wrapper"><table class="table"><thead><tr>' +
    '<th>客户端</th><th>登录时间</th><th>断开时间</th><th>连接时长</th><th>上行流量</th><th>下行流量</th><th>公网IP</th><th>状态</th>' +
    '</tr></thead><tbody id="history-tbody"><tr><td colspan="8" class="text-center text-secondary">加载中...</td></tr></tbody></table></div></div></div>';

  var onlineLoading = false;
  var historyLoading = false;

  function loadOnline() {
    if (onlineLoading) return;
    onlineLoading = true;
    var tbody = document.getElementById("conn-tbody");
    Api.get("/api/connections").then(function (resp) {
      var list = resp.data || [];
      if (!list.length) { tbody.innerHTML = '<tr><td colspan="11"><div class="empty-state"><div class="empty-icon">📡</div><div class="empty-text">暂无在线客户端</div></div></td></tr>'; return; }
      tbody.innerHTML = list.map(function (item) {
        return "<tr>" +
          "<td><strong>" + escHtml(item.common_name) + "</strong></td>" +
          '<td class="font-mono">' + escHtml(item.virtual_address || "-") + "</td>" +
          '<td class="font-mono">' + escHtml(item.real_address || "-") + "</td>" +
          '<td class="text-secondary">' + escHtml(item.connected_since || "-") + "</td>" +
          '<td>' + escHtml(item.connected_duration_text || "-") + "</td>" +
          '<td class="text-secondary">' + escHtml(item.last_sampled_at || "-") + "</td>" +
          '<td><strong>' + escHtml(item.upload_speed_text || "0 B/s") + '</strong></td>' +
          '<td><strong>' + escHtml(item.download_speed_text || "0 B/s") + '</strong></td>' +
          '<td>' + escHtml(item.bytes_received_text || "-") + '</td>' +
          '<td>' + escHtml(item.bytes_sent_text || "-") + '</td>' +
          '<td><button type="button" class="btn btn-danger-outline btn-sm" data-kick-address="' + escHtml(item.real_address || "") + '" data-kick-name="' + escHtml(item.common_name || "") + '">踢下线</button></td>' +
          "</tr>";
      }).join("");
    }).catch(function (err) { tbody.innerHTML = '<tr><td colspan="11" class="text-center text-secondary">' + escHtml(err.message || "加载失败") + '</td></tr>'; }).finally(function () { onlineLoading = false; });
  }

  function loadHistory() {
    if (historyLoading) return;
    historyLoading = true;
    var tbody = document.getElementById("history-tbody");
    Api.get("/api/connections/history?limit=100").then(function (resp) {
      var list = resp.data || [];
      if (!list.length) { tbody.innerHTML = '<tr><td colspan="8"><div class="empty-state"><div class="empty-icon">🕘</div><div class="empty-text">暂无连接历史</div></div></td></tr>'; return; }
      tbody.innerHTML = list.map(function (item) {
        return "<tr>" +
          "<td><strong>" + escHtml(item.common_name) + "</strong></td>" +
          '<td>' + escHtml(item.connected_at || "-") + '</td>' +
          '<td>' + escHtml(item.disconnected_at || "在线") + '</td>' +
          '<td>' + escHtml(item.duration_text || "-") + '</td>' +
          '<td>' + escHtml(item.bytes_received_text || "0 B") + '</td>' +
          '<td>' + escHtml(item.bytes_sent_text || "0 B") + '</td>' +
          '<td class="font-mono">' + escHtml(item.real_address || "-") + '</td>' +
          '<td>' + escHtml(item.status === "online" ? "在线" : "已断开") + '</td>' +
          "</tr>";
      }).join("");
    }).catch(function (err) { tbody.innerHTML = '<tr><td colspan="8" class="text-center text-secondary">' + escHtml(err.message || "加载失败") + '</td></tr>'; }).finally(function () { historyLoading = false; });
  }

  window.ConnPage = { kick: function (addr, name) {
    if (!confirm("确定强制踢下线「" + name + "」？")) return;
    Api.post("/api/connections/kick", { real_address: addr, common_name: name }).then(function (resp) { Toast.success(resp.message || "已强制下线"); loadOnline(); loadHistory(); }).catch(function (err) { Toast.error(err.message || "操作失败"); });
  }};
  document.getElementById("conn-tbody").addEventListener("click", function (event) {
    var button = event.target.closest("button[data-kick-address]");
    if (!button || !this.contains(button)) return;
    window.ConnPage.kick(button.dataset.kickAddress || "", button.dataset.kickName || "");
  });
  document.getElementById("btn-refresh").addEventListener("click", function () { loadOnline(); loadHistory(); });
  loadOnline(); loadHistory();
  setInterval(loadOnline, 5000);
  setInterval(loadHistory, 15000);
})();

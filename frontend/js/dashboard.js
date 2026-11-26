/**
 * 仪表盘页面逻辑
 * 统计卡片 + 最近操作日志
 */
(function () {
  "use strict";

  // 初始化布局
  Layout.init("仪表盘", "dashboard");

  var content = Layout.content();
  content.innerHTML =
    '<div class="stats-grid" id="stats-grid">' +
      '<div class="stat-card"><div class="stat-label">VPN 总用户数</div><div class="stat-value accent" id="stat-users">-</div></div>' +
      '<div class="stat-card"><div class="stat-label">在线客户端</div><div class="stat-value success" id="stat-online">-</div></div>' +
      '<div class="stat-card"><div class="stat-label">域名黑名单</div><div class="stat-value" id="stat-dns">-</div></div>' +
      '<div class="stat-card"><div class="stat-label">已吊销证书</div><div class="stat-value danger" id="stat-revoked">-</div></div>' +
    '</div>' +
    '<div class="panel">' +
      '<div class="panel-header"><h2 class="panel-title">最近操作日志</h2>' +
      '<a class="btn btn-secondary btn-sm" href="/pages/audit.html">查看全部</a></div>' +
      '<div class="panel-body no-padding">' +
        '<div class="table-wrapper"><table class="table">' +
          '<thead><tr><th>用户</th><th>操作</th><th>详情</th><th>IP</th><th>时间</th></tr></thead>' +
          '<tbody id="logs-tbody"><tr><td colspan="5" class="text-center text-secondary">加载中...</td></tr></tbody>' +
        '</table></div>' +
      '</div>' +
    '</div>';

  // 加载统计
  function loadStats() {
    Api.get("/api/dashboard/stats").then(function (resp) {
      var d = resp.data || {};
      document.getElementById("stat-users").textContent = d.total_users || 0;
      document.getElementById("stat-online").textContent = d.online_clients || 0;
      document.getElementById("stat-dns").textContent = d.dns_blacklist_total || 0;
      document.getElementById("stat-revoked").textContent = d.revoked_clients || 0;
    }).catch(function (err) {
      Toast.error(err.message || "获取统计数据失败");
    });
  }

  // 加载日志
  function loadLogs() {
    Api.get("/api/dashboard/recent-logs?limit=10").then(function (resp) {
      var list = resp.data || [];
      var tbody = document.getElementById("logs-tbody");
      if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="5"><div class="empty-state"><div class="empty-icon">📋</div><div class="empty-text">暂无操作日志</div></div></td></tr>';
        return;
      }
      var html = "";
      list.forEach(function (item) {
        html += "<tr>" +
          "<td>" + escHtml(item.username) + "</td>" +
          "<td>" + escHtml(item.action) + "</td>" +
          '<td class="text-secondary">' + escHtml(item.detail || "-") + "</td>" +
          '<td class="font-mono">' + escHtml(item.ip || "-") + "</td>" +
          '<td class="text-secondary">' + escHtml(item.created_at) + "</td>" +
          "</tr>";
      });
      tbody.innerHTML = html;
    }).catch(function (err) {
      document.getElementById("logs-tbody").innerHTML =
        '<tr><td colspan="5" class="text-center text-danger">' + escHtml(err.message || "加载失败") + "</td></tr>";
    });
  }

  loadStats();
  loadLogs();
})();

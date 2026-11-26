/**
 * 审计日志页面
 * 分页 + 关键字检索
 */
(function () {
  "use strict";

  Layout.init("审计日志", "audit");
  var content = Layout.content();

  content.innerHTML =
    '<div class="panel">' +
      '<div class="panel-header">' +
        '<h2 class="panel-title">操作日志</h2>' +
        '<div class="search-bar">' +
          '<input class="form-input" id="search-input" placeholder="搜索用户/操作/详情..." maxlength="64">' +
          '<button class="btn btn-secondary btn-sm" id="btn-search">搜索</button>' +
        '</div>' +
      '</div>' +
      '<div class="panel-body no-padding">' +
        '<div class="table-wrapper"><table class="table">' +
          '<thead><tr><th>ID</th><th>用户</th><th>操作</th><th>详情</th><th>IP</th><th>时间</th></tr></thead>' +
          '<tbody id="audit-tbody"><tr><td colspan="6" class="text-center text-secondary">加载中...</td></tr></tbody>' +
        '</table></div>' +
        '<div id="pager"></div>' +
      '</div>' +
    '</div>';

  var currentPage = 1;
  var pageSize = 10;
  var keyword = "";

  function loadList() {
    var tbody = document.getElementById("audit-tbody");
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-secondary">加载中...</td></tr>';

    var url = "/api/audit?page=" + currentPage + "&page_size=" + pageSize;
    if (keyword) url += "&keyword=" + encodeURIComponent(keyword);

    Api.get(url).then(function (resp) {
      var d = resp.data || {};
      var list = d.list || [];
      if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state"><div class="empty-icon">📋</div><div class="empty-text">暂无日志</div></div></td></tr>';
      } else {
        var html = "";
        list.forEach(function (item) {
          html += "<tr>" +
            '<td class="font-mono">' + escHtml(item.id) + "</td>" +
            "<td>" + escHtml(item.username) + "</td>" +
            "<td>" + escHtml(item.action) + "</td>" +
            '<td class="text-secondary">' + escHtml(item.detail || "-") + "</td>" +
            '<td class="font-mono">' + escHtml(item.ip || "-") + "</td>" +
            '<td class="text-secondary">' + escHtml(item.created_at) + "</td>" +
            "</tr>";
        });
        tbody.innerHTML = html;
      }

      Pager.render({
        el: document.getElementById("pager"),
        total: d.total || 0,
        page: currentPage,
        pageSize: pageSize,
        onChange: function (p) {
          currentPage = p;
          loadList();
        }
      });
    }).catch(function (err) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-danger">' + escHtml(err.message || "加载失败") + "</td></tr>";
    });
  }

  document.getElementById("btn-search").addEventListener("click", function () {
    keyword = document.getElementById("search-input").value.trim();
    currentPage = 1;
    loadList();
  });
  document.getElementById("search-input").addEventListener("keypress", function (e) {
    if (e.key === "Enter") {
      keyword = this.value.trim();
      currentPage = 1;
      loadList();
    }
  });

  loadList();
})();

/**
 * 域名黑名单管理页面（nftables）
 * 列表 / 搜索 / 分页 / 单条添加 / 批量导入 / 启用禁用 / 删除 / 解析/同步检查
 */
(function () {
  "use strict";

  Layout.init("域名黑名单", "dns");
  var content = Layout.content();

  content.innerHTML =
    '<div class="panel">' +
      '<div class="panel-header">' +
        '<h2 class="panel-title">域名黑名单</h2>' +
        '<div class="search-bar">' +
          '<input class="form-input" id="search-input" placeholder="搜索域名..." maxlength="64">' +
          '<button class="btn btn-secondary btn-sm" id="btn-search">搜索</button>' +
          '<button class="btn btn-secondary btn-sm" id="btn-batch">批量导入</button>' +
          '<button class="btn btn-secondary btn-sm" id="btn-resolve">解析/同步检查</button>' +
          '<button class="btn btn-primary btn-sm" id="btn-add">+ 添加域名</button>' +
        '</div>' +
      '</div>' +
      '<div class="panel-body no-padding">' +
        '<div class="table-wrapper"><table class="table">' +
          '<thead><tr><th>域名</th><th>备注</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>' +
          '<tbody id="dns-tbody"><tr><td colspan="5" class="text-center text-secondary">加载中...</td></tr></tbody>' +
        '</table></div>' +
        '<div id="pager"></div>' +
      '</div>' +
    '</div>' +
    // 添加单条
    '<div class="modal-overlay" id="modal-add">' +
      '<div class="modal">' +
        '<div class="modal-header"><h3 class="modal-title">添加域名黑名单</h3>' +
        '<button class="modal-close" id="modal-add-close">×</button></div>' +
        '<div class="modal-body">' +
          '<form id="form-add">' +
            '<div class="form-group">' +
              '<label class="form-label">域名 *</label>' +
              '<input class="form-input" id="add-domain" placeholder="例如：example.com" maxlength="253">' +
              '<div class="form-error"></div>' +
            '</div>' +
            '<div class="form-group">' +
              '<label class="form-label">备注</label>' +
              '<input class="form-input" id="add-remark" maxlength="200">' +
            '</div>' +
          '</form>' +
        '</div>' +
        '<div class="modal-footer">' +
          '<button class="btn btn-ghost" id="btn-add-cancel">取消</button>' +
          '<button class="btn btn-primary" id="btn-add-confirm">添加</button>' +
        '</div>' +
      '</div>' +
    '</div>' +
    // 批量导入
    '<div class="modal-overlay" id="modal-batch">' +
      '<div class="modal">' +
        '<div class="modal-header"><h3 class="modal-title">批量导入域名</h3>' +
        '<button class="modal-close" id="modal-batch-close">×</button></div>' +
        '<div class="modal-body">' +
          '<div class="form-group">' +
            '<label class="form-label">域名列表（每行一个，或逗号/空格分隔）</label>' +
            '<textarea class="form-textarea" id="batch-domains" rows="8" placeholder="example.com&#10;ads.example.net&#10;tracker.io"></textarea>' +
            '<div class="form-error"></div>' +
          '</div>' +
        '</div>' +
        '<div class="modal-footer">' +
          '<button class="btn btn-ghost" id="btn-batch-cancel">取消</button>' +
          '<button class="btn btn-primary" id="btn-batch-confirm">导入</button>' +
        '</div>' +
      '</div>' +
    '</div>' +
    // 解析/同步检查
    '<div class="modal-overlay" id="modal-resolve">' +
      '<div class="modal">' +
        '<div class="modal-header"><h3 class="modal-title">域名解析/同步检查</h3>' +
        '<button class="modal-close" id="modal-resolve-close">×</button></div>' +
        '<div class="modal-body">' +
          '<div class="form-group">' +
            '<label class="form-label">测试域名</label>' +
            '<input class="form-input" id="resolve-domain" placeholder="例如：example.com" maxlength="253">' +
            '<div class="form-error"></div>' +
          '</div>' +
          '<div id="resolve-result" class="mt-2"></div>' +
        '</div>' +
        '<div class="modal-footer">' +
          '<button class="btn btn-ghost" id="btn-resolve-close">关闭</button>' +
          '<button class="btn btn-primary" id="btn-resolve-test">测试</button>' +
        '</div>' +
      '</div>' +
    '</div>';

  var currentPage = 1;
  var pageSize = 10;
  var keyword = "";

  // ========== 加载列表 ==========
  function loadList() {
    var tbody = document.getElementById("dns-tbody");
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-secondary">加载中...</td></tr>';

    var url = "/api/dns?page=" + currentPage + "&page_size=" + pageSize;
    if (keyword) url += "&keyword=" + encodeURIComponent(keyword);

    Api.get(url).then(function (resp) {
      var d = resp.data || {};
      var list = d.list || [];
      if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="5"><div class="empty-state"><div class="empty-icon">🌐</div><div class="empty-text">暂无黑名单域名</div></div></td></tr>';
      } else {
        var html = "";
        list.forEach(function (item) {
          var statusBadge = item.enabled
            ? '<span class="badge badge-success">启用</span>'
            : '<span class="badge badge-neutral">禁用</span>';
          var toggleLabel = item.enabled ? "禁用" : "启用";

          html += "<tr>" +
            "<td><strong>" + escHtml(item.domain) + "</strong></td>" +
            '<td class="text-secondary">' + escHtml(item.remark || "-") + "</td>" +
            "<td>" + statusBadge + "</td>" +
            '<td class="text-secondary">' + escHtml(item.created_at) + "</td>" +
            '<td><div class="actions">' +
              '<button type="button" class="btn btn-secondary btn-sm" data-dns-action="toggle" data-id="' + Number(item.id) + '" data-enabled="' + (item.enabled ? 0 : 1) + '">' + toggleLabel + "</button>" +
              '<button type="button" class="btn btn-danger-outline btn-sm" data-dns-action="remove" data-id="' + Number(item.id) + '" data-domain="' + escHtml(item.domain) + '">删除</button>' +
            "</div></td></tr>";
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
      tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger">' + escHtml(err.message || "加载失败") + "</td></tr>";
    });
  }

  // ========== 添加单条 ==========
  document.getElementById("btn-add").addEventListener("click", function () {
    document.getElementById("modal-add").classList.add("show");
    document.getElementById("add-domain").value = "";
    document.getElementById("add-remark").value = "";
    Validator.clearAllErrors(document.getElementById("form-add"));
  });
  document.getElementById("modal-add-close").addEventListener("click", function () {
    document.getElementById("modal-add").classList.remove("show");
  });
  document.getElementById("btn-add-cancel").addEventListener("click", function () {
    document.getElementById("modal-add").classList.remove("show");
  });
  document.getElementById("btn-add-confirm").addEventListener("click", function () {
    var domainEl = document.getElementById("add-domain");
    Validator.clearAllErrors(document.getElementById("form-add"));
    var domain = domainEl.value.trim().toLowerCase();
    if (!domain) {
      Validator.showError(domainEl, "请输入域名");
      return;
    }
    if (!Validator.isDomain(domain)) {
      Validator.showError(domainEl, "域名格式不正确");
      return;
    }

    var btn = this;
    btn.disabled = true;
    Api.post("/api/dns", {
      domain: domain,
      remark: document.getElementById("add-remark").value.trim()
    }).then(function () {
      Toast.success("添加成功");
      document.getElementById("modal-add").classList.remove("show");
      loadList();
    }).catch(function (err) {
      Toast.error(err.message || "添加失败");
    }).then(function () {
      btn.disabled = false;
    });
  });

  // ========== 批量导入 ==========
  document.getElementById("btn-batch").addEventListener("click", function () {
    document.getElementById("modal-batch").classList.add("show");
    document.getElementById("batch-domains").value = "";
  });
  document.getElementById("modal-batch-close").addEventListener("click", function () {
    document.getElementById("modal-batch").classList.remove("show");
  });
  document.getElementById("btn-batch-cancel").addEventListener("click", function () {
    document.getElementById("modal-batch").classList.remove("show");
  });
  document.getElementById("btn-batch-confirm").addEventListener("click", function () {
    var ta = document.getElementById("batch-domains");
    var raw = ta.value.trim();
    if (!raw) {
      Toast.error("请输入域名列表");
      return;
    }
    var btn = this;
    btn.disabled = true;
    btn.textContent = "导入中...";

    Api.post("/api/dns/batch", { domains: raw }).then(function (resp) {
      Toast.success(resp.message || "导入完成");
      document.getElementById("modal-batch").classList.remove("show");
      loadList();
    }).catch(function (err) {
      Toast.error(err.message || "导入失败");
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "导入";
    });
  });

  // ========== 解析/同步检查 ==========
  document.getElementById("btn-resolve").addEventListener("click", function () {
    document.getElementById("modal-resolve").classList.add("show");
    document.getElementById("resolve-domain").value = "";
    document.getElementById("resolve-result").innerHTML = "";
    Validator.clearAllErrors(document.getElementById("modal-resolve"));
  });
  document.getElementById("modal-resolve-close").addEventListener("click", function () {
    document.getElementById("modal-resolve").classList.remove("show");
  });
  document.getElementById("btn-resolve-close").addEventListener("click", function () {
    document.getElementById("modal-resolve").classList.remove("show");
  });
  document.getElementById("btn-resolve-test").addEventListener("click", function () {
    var domainEl = document.getElementById("resolve-domain");
    var domain = domainEl.value.trim().toLowerCase();
    if (!domain || !Validator.isDomain(domain)) {
      Toast.error("请输入合法域名");
      return;
    }
    var btn = this;
    btn.disabled = true;
    btn.textContent = "测试中...";

    Api.post("/api/dns/resolve", { domain: domain }).then(function (resp) {
      var d = resp.data || {};
      var addrText = (d.addresses && d.addresses.length) ? d.addresses.join(", ") : "未解析到地址";
      var blockedBadge = d.enforced
        ? '<span class="badge badge-danger">解析地址已记录到同步状态</span>'
        : (d.blocked
          ? '<span class="badge badge-warning">已配置但当前未解析/未同步</span>'
          : '<span class="badge badge-success">未配置拦截</span>');
      document.getElementById("resolve-result").innerHTML =
        '<div class="panel" style="margin:0"><div class="panel-body">' +
        "<p><strong>域名：</strong>" + escHtml(d.domain) + "</p>" +
        "<p class='mt-1'><strong>解析结果：</strong><span class='font-mono'>" + escHtml(addrText) + "</span></p>" +
        "<p class='mt-1'><strong>状态：</strong>" + blockedBadge + "</p>" +
        "</div></div>";
    }).catch(function (err) {
      Toast.error(err.message || "测试失败");
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "测试";
    });
  });

  // ========== 搜索 ==========
  document.getElementById("btn-search").addEventListener("click", function () {
    keyword = document.getElementById("search-input").value.trim();
    currentPage = 1;
    loadList();
  });

  // ========== 对外操作 ==========
  window.DnsPage = {
    toggle: function (id, enabled) {
      Api.put("/api/dns/" + id, { enabled: !!enabled }).then(function () {
        Toast.success(enabled ? "已启用" : "已禁用");
        loadList();
      }).catch(function (err) {
        Toast.error(err.message || "操作失败");
      });
    },
    remove: function (id, domain) {
      if (!confirm("确定删除域名「" + domain + "」？")) return;
      Api.del("/api/dns/" + id).then(function () {
        Toast.success("删除成功");
        loadList();
      }).catch(function (err) {
        Toast.error(err.message || "删除失败");
      });
    }
  };

  document.getElementById("dns-tbody").addEventListener("click", function (event) {
    var button = event.target.closest("button[data-dns-action]");
    if (!button || !this.contains(button)) return;
    var id = Number(button.dataset.id);
    if (!Number.isSafeInteger(id) || id < 1) return;
    if (button.dataset.dnsAction === "toggle") window.DnsPage.toggle(id, Number(button.dataset.enabled));
    else if (button.dataset.dnsAction === "remove") window.DnsPage.remove(id, button.dataset.domain || "");
  });

  loadList();
})();

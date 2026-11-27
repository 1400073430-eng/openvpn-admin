/**
 * VPN 客户端管理页面
 * 列表 / 搜索 / 分页 / 新增 / 下载 ovpn / 吊销 / 编辑 / 删除
 */
(function () {
  "use strict";

  Layout.init("VPN 客户端", "clients");
  var content = Layout.content();

  // 页面结构
  content.innerHTML =
    // 同步已有证书提示条（有未导入证书时显示）
    '<div id="sync-banner" class="panel hidden" style="border-left:4px solid var(--accent)">' +
      '<div class="panel-body" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">' +
        '<div><strong>发现服务器上已有证书未纳入平台</strong>' +
        '<div class="text-secondary mt-1" id="sync-banner-text"></div></div>' +
        '<button class="btn btn-primary btn-sm" id="btn-sync-banner">一键同步</button>' +
      '</div>' +
    '</div>' +
    '<div class="panel">' +
      '<div class="panel-header">' +
        '<h2 class="panel-title">客户端列表</h2>' +
        '<div class="search-bar">' +
          '<input class="form-input" id="search-input" placeholder="搜索名称或备注..." maxlength="64">' +
          '<button class="btn btn-secondary btn-sm" id="btn-search">搜索</button>' +
          '<button class="btn btn-secondary btn-sm" id="btn-sync" title="扫描 EasyRSA 已有证书并导入">同步已有证书</button>' +
          '<button class="btn btn-primary btn-sm" id="btn-add">+ 新增客户端</button>' +
        '</div>' +
      '</div>' +
      '<div class="panel-body no-padding">' +
        '<div class="table-wrapper"><table class="table">' +
          '<thead><tr><th>名称</th><th>备注</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>' +
          '<tbody id="clients-tbody"><tr><td colspan="5" class="text-center text-secondary">加载中...</td></tr></tbody>' +
        '</table></div>' +
        '<div id="pager"></div>' +
      '</div>' +
    '</div>' +
    // 新增弹窗
    '<div class="modal-overlay" id="modal-add">' +
      '<div class="modal">' +
        '<div class="modal-header"><h3 class="modal-title">新增 VPN 客户端</h3>' +
        '<button class="modal-close" id="modal-add-close">×</button></div>' +
        '<div class="modal-body">' +
          '<form id="form-add">' +
            '<div class="form-group">' +
              '<label class="form-label">客户端名称 *</label>' +
              '<input class="form-input" id="add-name" placeholder="字母数字下划线中划线，2-32位" maxlength="32">' +
              '<div class="form-error"></div>' +
            '</div>' +
            '<div class="form-group">' +
              '<label class="form-label">备注</label>' +
              '<input class="form-input" id="add-remark" placeholder="可选，最多200字" maxlength="200">' +
            '</div>' +
          '</form>' +
        '</div>' +
        '<div class="modal-footer">' +
          '<button class="btn btn-ghost" id="btn-add-cancel">取消</button>' +
          '<button class="btn btn-primary" id="btn-add-confirm">创建</button>' +
        '</div>' +
      '</div>' +
    '</div>' +
    // 编辑弹窗
    '<div class="modal-overlay" id="modal-edit">' +
      '<div class="modal">' +
        '<div class="modal-header"><h3 class="modal-title">编辑备注</h3>' +
        '<button class="modal-close" id="modal-edit-close">×</button></div>' +
        '<div class="modal-body">' +
          '<div class="form-group">' +
            '<label class="form-label">备注</label>' +
            '<input class="form-input" id="edit-remark" maxlength="200">' +
          '</div>' +
        '</div>' +
        '<div class="modal-footer">' +
          '<button class="btn btn-ghost" id="btn-edit-cancel">取消</button>' +
          '<button class="btn btn-primary" id="btn-edit-confirm">保存</button>' +
        '</div>' +
      '</div>' +
    '</div>';

  var currentPage = 1;
  var pageSize = 10;
  var keyword = "";
  var editTargetName = "";

  // ========== 同步服务器已有证书 ==========
  // 接口使用 /api/easyrsa/*，与 /api/clients/{name} 无路径冲突
  function checkExisting() {
    Api.get("/api/easyrsa/scan").then(function (resp) {
      var d = resp.data || {};
      var missing = d.missing || [];
      var banner = document.getElementById("sync-banner");
      if (missing.length > 0) {
        var names = missing.map(function (c) { return escHtml(c.name); }).join("、");
        document.getElementById("sync-banner-text").textContent =
          "共 " + missing.length + " 个：" + names + "。可一键导入平台管理。";
        banner.classList.remove("hidden");
      } else {
        banner.classList.add("hidden");
      }
    }).catch(function () {
      // 扫描失败不打扰用户
    });
  }

  function doSync() {
    if (!confirm("将扫描服务器 EasyRSA 已有证书并导入平台，已存在的不会覆盖。继续？")) return;
    Toast.info("正在同步...");
    Api.post("/api/easyrsa/import", {}).then(function (resp) {
      Toast.success(resp.message || "同步完成");
      loadList();
      checkExisting();
    }).catch(function (err) {
      Toast.error(err.message || "同步失败");
    });
  }

  // 静默自动导入（仅列表为空时后端才会真正导入）
  function autoImportIfEmpty() {
    Api.post("/api/easyrsa/auto-import", {}).then(function (resp) {
      var d = resp.data || {};
      if (d.imported && d.imported.length) {
        Toast.success("已自动导入 " + d.imported.length + " 个服务器已有客户端");
        loadList();
      }
      checkExisting();
    }).catch(function () {});
  }

  document.getElementById("btn-sync").addEventListener("click", doSync);
  document.getElementById("btn-sync-banner").addEventListener("click", doSync);

  // ========== 加载列表 ==========
  function loadList() {
    var tbody = document.getElementById("clients-tbody");
    ListView.loading(tbody, 5);

    var url = ListView.url("/api/clients", currentPage, pageSize, keyword);

    Api.get(url).then(function (resp) {
      var d = resp.data || {};
      var list = d.list || [];
      if (!list.length) {
        ListView.empty(tbody, 5, "👤", "暂无客户端");
      } else {
        var html = "";
        list.forEach(function (item) {
          var statusBadge = item.status === "active"
            ? '<span class="badge badge-success">启用</span>'
            : '<span class="badge badge-danger">已吊销</span>';
          var ops = '<div class="actions">';
          if (item.status === "active") {
            ops += '<button type="button" class="btn btn-secondary btn-sm" data-client-action="download" data-name="' + escHtml(item.name) + '">下载</button>';
            ops += '<button type="button" class="btn btn-danger-outline btn-sm" data-client-action="revoke" data-name="' + escHtml(item.name) + '">吊销</button>';
          }
          ops += '<button type="button" class="btn btn-ghost btn-sm" data-client-action="edit" data-name="' + escHtml(item.name) + '" data-remark="' + escHtml(item.remark || "") + '">编辑</button>';
          ops += '<button type="button" class="btn btn-ghost btn-sm" data-client-action="remove" data-name="' + escHtml(item.name) + '">删除</button>';
          ops += "</div>";

          html += "<tr>" +
            "<td><strong>" + escHtml(item.name) + "</strong></td>" +
            '<td class="text-secondary">' + escHtml(item.remark || "-") + "</td>" +
            "<td>" + statusBadge + "</td>" +
            '<td class="text-secondary">' + escHtml(item.created_at) + "</td>" +
            "<td>" + ops + "</td>" +
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
      ListView.error(tbody, 5, err);
    });
  }

  // ========== 新增 ==========
  function openAdd() {
    document.getElementById("modal-add").classList.add("show");
    document.getElementById("add-name").value = "";
    document.getElementById("add-remark").value = "";
    Validator.clearAllErrors(document.getElementById("form-add"));
  }
  function closeAdd() {
    document.getElementById("modal-add").classList.remove("show");
  }

  document.getElementById("btn-add").addEventListener("click", openAdd);
  document.getElementById("modal-add-close").addEventListener("click", closeAdd);
  document.getElementById("btn-add-cancel").addEventListener("click", closeAdd);

  document.getElementById("btn-add-confirm").addEventListener("click", function () {
    var nameEl = document.getElementById("add-name");
    var remarkEl = document.getElementById("add-remark");
    Validator.clearAllErrors(document.getElementById("form-add"));

    var name = nameEl.value.trim();
    var valid = true;
    if (!name) {
      Validator.showError(nameEl, "请输入客户端名称");
      valid = false;
    } else if (!Validator.isClientName(name)) {
      Validator.showError(nameEl, "名称需为 2-32 位字母数字下划线中划线");
      valid = false;
    }
    if (!valid) return;

    this.disabled = true;
    var btn = this;
    btn.textContent = "创建中...";

    Api.post("/api/clients", {
      name: name,
      remark: remarkEl.value.trim()
    }).then(function () {
      Toast.success("客户端创建成功");
      closeAdd();
      loadList();
    }).catch(function (err) {
      Toast.error(err.message || "创建失败");
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "创建";
    });
  });

  // ========== 搜索 ==========
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

  // ========== 对外操作 ==========
  window.ClientsPage = {
    download: function (name) {
      Toast.info("正在生成配置文件...");
      Api.download("/api/clients/" + encodeURIComponent(name) + "/ovpn", name + ".ovpn");
    },

    revoke: function (name) {
      if (!confirm("确定吊销证书「" + name + "」？\n吊销后该客户端将无法连接 VPN。")) return;
      Api.post("/api/clients/" + encodeURIComponent(name) + "/revoke", {}).then(function () {
        Toast.success("证书已吊销");
        loadList();
      }).catch(function (err) {
        Toast.error(err.message || "吊销失败");
      });
    },

    edit: function (name, remark) {
      editTargetName = name;
      document.getElementById("edit-remark").value = remark;
      document.getElementById("modal-edit").classList.add("show");
    },

    remove: function (name) {
      if (!confirm("确定删除客户端「" + name + "」？\n仅删除管理记录，证书文件保留。")) return;
      Api.del("/api/clients/" + encodeURIComponent(name)).then(function () {
        Toast.success("客户端已删除");
        loadList();
      }).catch(function (err) {
        Toast.error(err.message || "删除失败");
      });
    }
  };

  // 使用事件委托和 data-* 属性，避免把服务端数据拼入 inline JavaScript。
  document.getElementById("clients-tbody").addEventListener("click", function (event) {
    var button = event.target.closest("button[data-client-action]");
    if (!button || !this.contains(button)) return;
    var action = button.dataset.clientAction;
    var name = button.dataset.name || "";
    if (!name) return;
    if (action === "download") window.ClientsPage.download(name);
    else if (action === "revoke") window.ClientsPage.revoke(name);
    else if (action === "edit") window.ClientsPage.edit(name, button.dataset.remark || "");
    else if (action === "remove") window.ClientsPage.remove(name);
  });

  // 编辑弹窗
  document.getElementById("modal-edit-close").addEventListener("click", function () {
    document.getElementById("modal-edit").classList.remove("show");
  });
  document.getElementById("btn-edit-cancel").addEventListener("click", function () {
    document.getElementById("modal-edit").classList.remove("show");
  });
  document.getElementById("btn-edit-confirm").addEventListener("click", function () {
    if (!editTargetName) return;
    var remark = document.getElementById("edit-remark").value.trim();
    var btn = this;
    btn.disabled = true;

    Api.put("/api/clients/" + encodeURIComponent(editTargetName), { remark: remark })
      .then(function () {
        Toast.success("备注已更新");
        document.getElementById("modal-edit").classList.remove("show");
        loadList();
      }).catch(function (err) {
        Toast.error(err.message || "更新失败");
      }).then(function () {
        btn.disabled = false;
      });
  });

  // 初始化：先自动补齐（空库时导入 EasyRSA 已有证书），再拉列表
  autoImportIfEmpty();
  loadList();
})();

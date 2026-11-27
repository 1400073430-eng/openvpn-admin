/**
 * 公共工具模块
 * - API 请求封装（自动带 JWT）
 * - Toast 提示
 * - 表单校验
 * - 登录态管理
 */
(function () {
  "use strict";

  // ==================== 配置 ====================
  var API_BASE = "";  // 同源，相对路径
  // 认证凭据由后端以 HttpOnly Cookie 管理；前端不保存 JWT。
  var USER_KEY = "ovpn_user";

  // ==================== Token 管理 ====================
  window.Auth = {
    getUser: function () {
      return sessionStorage.getItem(USER_KEY) || "";
    },
    setUser: function (user) {
      sessionStorage.setItem(USER_KEY, user);
    },
    clear: function () {
      sessionStorage.removeItem(USER_KEY);
    },
    isLoggedIn: function () {
      return !!this.getUser();
    },
    requireLogin: function () {
      if (!this.isLoggedIn()) {
        window.location.href = "/index.html";
        return false;
      }
      return true;
    },
    logout: function () {
      var self = this;
      return Api.post("/api/auth/logout").then(function () {
        self.clear();
        window.location.href = "/index.html";
      }).catch(function (err) {
        if (!self.isLoggedIn()) {
          window.location.href = "/index.html";
          return;
        }
        Toast.error("退出未完成，请重试：" + (err.message || "网络错误"));
      });
    }
  };

  // ==================== API 封装 ====================
  window.Api = {
    /**
     * 发起请求
     * @param {string} method  GET/POST/PUT/DELETE
     * @param {string} url     接口路径
     * @param {object} data    请求体（POST/PUT）
     * @returns {Promise}
     */
    request: function (method, url, data) {
      return new Promise(function (resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open(method, API_BASE + url, true);
        // Bound stalled requests so the UI can recover.
        xhr.timeout = 300000;
        xhr.setRequestHeader("Content-Type", "application/json");

        // HttpOnly Cookie 会由同源 XHR 自动携带，不在 JavaScript 中读取 JWT。

        xhr.onreadystatechange = function () {
          if (xhr.readyState !== 4) return;

          if (xhr.status === 401 && url.split("?")[0] !== "/api/auth/login") {
            Auth.clear();
            if (!window.location.pathname.endsWith("index.html") && window.location.pathname !== "/") {
              setTimeout(function () {
                window.location.href = "/index.html";
              }, 1200);
            }
            reject(new Error("登录已过期，请重新登录"));
            return;
          }

          try {
            var resp = JSON.parse(xhr.responseText);
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve(resp);
            } else {
              // FastAPI 422 等
              var msg = resp.detail || resp.message || "请求失败";
              if (Array.isArray(msg)) {
                msg = msg.map(function (m) { return m.msg || m; }).join("; ");
              }
              reject(new Error(typeof msg === "string" ? msg : "请求失败"));
            }
          } catch (e) {
            // 非 JSON（如下载文件）
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve({ code: 200, data: xhr.response || xhr.responseText });
            } else {
              reject(new Error("请求失败"));
            }
          }
        };

        xhr.onerror = function () {
          reject(new Error("网络错误，请检查连接"));
        };

        xhr.ontimeout = function () {
          reject(new Error("请求超时，请稍后重试"));
        };

        xhr.send(data ? JSON.stringify(data) : null);
      });
    },

    get: function (url) {
      return this.request("GET", url);
    },
    post: function (url, data) {
      return this.request("POST", url, data);
    },
    put: function (url, data) {
      return this.request("PUT", url, data);
    },
    del: function (url) {
      return this.request("DELETE", url);
    },

    /**
     * 下载文件（带鉴权）。失败时解析后端错误信息并展示。
     */
    download: function (url, filename) {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", API_BASE + url, true);
      // HttpOnly Cookie 会由同源请求自动携带。
      xhr.responseType = "blob";
      xhr.onload = function () {
        if (xhr.status === 200) {
          var blob = xhr.response;
          // 防止把 JSON 错误当成文件保存
          if (blob && blob.type && blob.type.indexOf("application/json") !== -1) {
            var reader = new FileReader();
            reader.onload = function () {
              try {
                var resp = JSON.parse(reader.result);
                Toast.error(resp.detail || resp.message || "下载失败");
              } catch (e) {
                Toast.error("下载失败");
              }
            };
            reader.readAsText(blob);
            return;
          }
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = filename || "download";
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(a.href);
          Toast.success("配置文件已下载");
        } else if (xhr.status === 401) {
          Toast.error("登录已过期，请重新登录");
          Auth.clear();
          window.location.href = "/index.html";
        } else {
          // 尝试读取错误详情（FastAPI detail 或统一 message）
          var fail = function (msg) {
            Toast.error(msg || ("下载失败 (HTTP " + xhr.status + ")"));
          };
          try {
            var r2 = new FileReader();
            r2.onload = function () {
              try {
                var resp = JSON.parse(r2.result);
                fail(resp.detail || resp.message);
              } catch (e) {
                fail();
              }
            };
            r2.readAsText(xhr.response);
          } catch (e) {
            fail();
          }
        }
      };
      xhr.onerror = function () {
        Toast.error("网络错误，请检查与服务器的连接");
      };
      xhr.send();
    }
  };

  // ==================== Toast ====================
  window.Toast = {
    _container: null,

    _ensureContainer: function () {
      if (!this._container) {
        this._container = document.createElement("div");
        this._container.className = "toast-container";
        document.body.appendChild(this._container);
      }
      return this._container;
    },

    _show: function (msg, type, duration) {
      var c = this._ensureContainer();
      var el = document.createElement("div");
      el.className = "toast toast-" + type;
      el.textContent = msg;
      c.appendChild(el);
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, duration || 3000);
    },

    success: function (msg, duration) {
      this._show(msg, "success", duration);
    },
    error: function (msg, duration) {
      this._show(msg, "error", duration || 4000);
    },
    info: function (msg, duration) {
      this._show(msg, "info", duration);
    }
  };

  // ==================== 表单校验 ====================
  window.Validator = {
    /**
     * 域名格式校验
     */
    isDomain: function (v) {
      if (!v) return false;
      v = v.trim().toLowerCase();
      if (v.length > 253) return false;
      return /^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$/.test(v);
    },

    /**
     * 客户端名称校验
     */
    isClientName: function (v) {
      if (!v) return false;
      return /^[a-zA-Z0-9_\-]{2,32}$/.test(v.trim());
    },

    /**
     * 非空校验
     */
    notEmpty: function (v) {
      return !!(v && v.trim());
    },

    /**
     * 密码校验（至少8位）
     */
    isPassword: function (v) {
      return !!(v && v.length >= 8 && v.length <= 128);
    },

    /**
     * 显示字段错误
     */
    showError: function (inputEl, msg) {
      var group = inputEl.closest(".form-group") || inputEl.parentNode;
      var err = group.querySelector(".form-error");
      if (!err) {
        err = document.createElement("div");
        err.className = "form-error";
        group.appendChild(err);
      }
      err.textContent = msg;
      inputEl.style.borderColor = "var(--danger)";
    },

    clearError: function (inputEl) {
      var group = inputEl.closest(".form-group") || inputEl.parentNode;
      var err = group.querySelector(".form-error");
      if (err) err.textContent = "";
      inputEl.style.borderColor = "";
    },

    clearAllErrors: function (form) {
      if (!form) return;
      form.querySelectorAll(".form-error").forEach(function (e) {
        e.textContent = "";
      });
      form.querySelectorAll("input, textarea, select").forEach(function (i) {
        i.style.borderColor = "";
      });
    }
  };

  // ==================== 列表页通用状态 ====================
  window.ListView = {
    url: function (path, page, pageSize, keyword) {
      var url = path + "?page=" + page + "&page_size=" + pageSize;
      return keyword ? url + "&keyword=" + encodeURIComponent(keyword) : url;
    },
    loading: function (tbody, columns) {
      tbody.innerHTML = '<tr><td colspan="' + columns + '" class="text-center text-secondary">加载中...</td></tr>';
    },
    empty: function (tbody, columns, icon, message) {
      tbody.innerHTML = '<tr><td colspan="' + columns + '"><div class="empty-state"><div class="empty-icon">' +
        escHtml(icon) + '</div><div class="empty-text">' + escHtml(message) + '</div></div></td></tr>';
    },
    error: function (tbody, columns, err) {
      tbody.innerHTML = '<tr><td colspan="' + columns + '" class="text-center text-danger">' +
        escHtml((err && err.message) || "加载失败") + '</td></tr>';
    }
  };

  // ==================== 分页渲染 ====================
  window.Pager = {
    /**
     * 渲染分页控件
     * @param {object} opts
     *   el: 容器 DOM
     *   total: 总条数
     *   page: 当前页
     *   pageSize: 每页条数
     *   onChange: function(page)
     */
    render: function (opts) {
      var el = opts.el;
      if (!el) return;
      var total = opts.total || 0;
      var page = opts.page || 1;
      var pageSize = opts.pageSize || 10;
      var totalPages = Math.max(1, Math.ceil(total / pageSize));

      var html = '<div class="pagination">';
      html += '<span>共 ' + total + ' 条，第 ' + page + ' / ' + totalPages + ' 页</span>';
      html += '<div class="pagination-controls">';
      html += '<button class="page-btn" data-page="prev" ' + (page <= 1 ? "disabled" : "") + ">上一页</button>";

      // 页码按钮（最多显示 5 个）
      var start = Math.max(1, page - 2);
      var end = Math.min(totalPages, start + 4);
      if (end - start < 4) start = Math.max(1, end - 4);
      for (var i = start; i <= end; i++) {
        html += '<button class="page-btn ' + (i === page ? "active" : "") + '" data-page="' + i + '">' + i + "</button>";
      }

      html += '<button class="page-btn" data-page="next" ' + (page >= totalPages ? "disabled" : "") + ">下一页</button>";
      html += "</div></div>";

      el.innerHTML = html;

      // 绑定事件
      el.querySelectorAll(".page-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var v = this.getAttribute("data-page");
          if (v === "prev" && page > 1) {
            opts.onChange(page - 1);
          } else if (v === "next" && page < totalPages) {
            opts.onChange(page + 1);
          } else if (v !== "prev" && v !== "next") {
            opts.onChange(parseInt(v, 10));
          }
        });
      });
    }
  };

  // ==================== 布局初始化 ====================
  window.Layout = {
    /**
     * 初始化管理后台布局（侧边栏 + 顶栏）
     * @param {string} title 页面标题
     * @param {string} activeKey 当前导航 key
     */
    init: function (title, activeKey) {
      if (!Auth.requireLogin()) return;

      var user = Auth.getUser() || "admin";
      var navItems = [
        { key: "dashboard", icon: "◫", label: "仪表盘", href: "/pages/dashboard.html" },
        { key: "clients", icon: "☰", label: "VPN客户端", href: "/pages/clients.html" },
        { key: "connections", icon: "◉", label: "在线连接", href: "/pages/connections.html" },
        { key: "dns", icon: "◎", label: "域名黑名单", href: "/pages/dns.html" },
        { key: "audit", icon: "☰", label: "审计日志", href: "/pages/audit.html" },
        { key: "settings", icon: "⚙", label: "系统设置", href: "/pages/settings.html" }
      ];

      var navHtml = "";
      navItems.forEach(function (item) {
        var cls = item.key === activeKey ? "nav-link active" : "nav-link";
        navHtml += '<li class="nav-item"><a class="' + cls + '" href="' + item.href + '">' +
          '<span class="nav-icon">' + item.icon + "</span><span>" + item.label + "</span></a></li>";
      });

      var html =
        '<div class="app-layout">' +
        '<aside class="sidebar">' +
        '<div class="sidebar-logo"><div class="logo-icon">V</div><div class="logo-text">VPN 管理平台</div></div>' +
        '<ul class="nav-menu">' + navHtml + "</ul>" +
        '<div class="sidebar-footer">OpenVPN 管理平台</div>' +
        "</aside>" +
        '<div class="main-content">' +
        '<header class="topbar">' +
        '<h1 class="topbar-title">' + title + "</h1>" +
        '<div class="topbar-user">' +
        '<span>' + user + "</span>" +
        '<div class="avatar">' + user.charAt(0).toUpperCase() + "</div>" +
        '<button class="btn btn-ghost btn-sm" id="btn-logout">退出</button>' +
        "</div></header>" +
        '<main class="content-area" id="content-area"></main>' +
        "</div></div>";

      document.body.innerHTML = html;

      // 退出登录
      var logoutBtn = document.getElementById("btn-logout");
      if (logoutBtn) {
        logoutBtn.addEventListener("click", function () {
          if (confirm("确定退出登录？")) {
            Auth.logout();
          }
        });
      }
    },

    /**
     * 获取内容区容器
     */
    content: function () {
      return document.getElementById("content-area");
    }
  };

  // ==================== 时间格式化 ====================
  window.fmtTime = function (s) {
    if (!s) return "-";
    return String(s);
  };

  // ==================== HTML 转义（防 XSS） ====================
  window.escHtml = function (s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

})();

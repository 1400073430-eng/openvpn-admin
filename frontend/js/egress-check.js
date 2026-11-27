/* Manual browser egress comparison. Never claims to test ChatGPT routing. */
(function () {
  "use strict";
  function normalize(ip, version) {
    if (typeof ip !== "string" || ip.length > 45) return null;
    if (version === 4) {
      if (!/^(0|[1-9]\d{0,2})(\.(0|[1-9]\d{0,2})){3}$/.test(ip)) return null;
      return ip.split('.').every(function (v) { return Number(v) <= 255; }) ? ip : null;
    }
    if (!/^[0-9a-f:]+$/i.test(ip) || ip.indexOf(':') < 0) return null;
    try { return new URL('http://[' + ip + ']/').hostname.toLowerCase(); }
    catch (err) { return null; }
  }
  function compare(browser, server, version) {
    var local = normalize(browser, version);
    var remote = normalize(server, version);
    if (!local) return '无法判断：浏览器查询失败、网络不可用或请求被拦截。';
    if (!remote) return '无法判断：浏览器存在 IPv' + version + ' 出口，但服务器出口查询未成功；需进一步核实。';
    if (local !== remote) return '疑似绕过或分流：浏览器出口与服务器不同；请检查 VPN 路由及分流规则。';
    return '出口匹配：仅代表本次查询路径匹配，不保证 ChatGPT 等其他网站使用相同路由。';
  }
  window.EgressCheck = {normalize: normalize, compare: compare};
  var content = document.getElementById('content-area');
  if (!content) return;
  var panel = document.createElement('div');
  panel.className = 'panel';
  panel.innerHTML = '<div class="panel-header"><h2 class="panel-title">本机 VPN 出口检查</h2></div>' +
    '<div class="panel-body"><p>请先连接 VPN。点击后浏览器将向 ipify.org 查询本机 IPv4、IPv6，查询服务会收到出口 IP。服务器对照结果可能缓存 5 分钟。</p>' +
    '<button type="button" class="btn btn-secondary mt-2" id="btn-browser-egress">检查本机出口</button>' +
    '<div id="browser-egress-result" class="mt-2">尚未检测。本功能不直接检测 ChatGPT 的实际连接。</div></div>';
  content.appendChild(panel);
  function lookup(version) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 8000);
    var host = version === 4 ? 'api.ipify.org' : 'api6.ipify.org';
    return fetch('https://' + host + '?format=json', {
      signal: controller.signal, credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer'
    }).then(function (response) {
      if (!response.ok) throw new Error('查询失败');
      return response.json();
    }).then(function (data) { return normalize(data.ip, version) ? data.ip : null; })
      .catch(function () { return null; })
      .finally(function () { clearTimeout(timer); });
  }
  document.getElementById('btn-browser-egress').addEventListener('click', function () {
    var btn = this;
    var output = document.getElementById('browser-egress-result');
    btn.disabled = true;
    output.textContent = '检测中，请稍候…';
    Promise.all([lookup(4), lookup(6), Api.post('/api/settings/node-check', {}).catch(function () { return null; })])
      .then(function (values) {
        var results = values[2] && values[2].data && values[2].data.results || [];
        output.textContent = '';
        [4, 6].forEach(function (version, index) {
          var server = results.find(function (r) { return r.version === version && r.status === 'ok'; });
          var row = document.createElement('p');
          row.textContent = 'IPv' + version + ' 浏览器：' + (values[index] || '查询未成功') +
            '；服务器：' + (server && server.ip || '查询未成功') + '。' + compare(values[index], server && server.ip, version);
          output.appendChild(row);
        });
      }).catch(function () { output.textContent = '检测未完成，无法判断。'; })
      .finally(function () { btn.disabled = false; });
  });
})();

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const elements = {};
let probe = {ready: false, upgrade_supported: false, addresses: [], default_routes: [], provider_note: '需到服务商确认'};
function el(id) {
  return elements[id] || (elements[id] = {innerHTML: '', textContent: '', value: '', disabled: false,
    addEventListener: function (name, fn) { this[name] = fn; }});
}
const sandbox = {
  Layout: {init() {}, content: () => el('content')},
  document: {getElementById: el},
  Api: {get: async url => ({data: url.endsWith('/ipv6') ? probe : {}})},
  Toast: {error() {}, success() {}},
  escHtml: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/js/settings.js'), 'utf8'), sandbox);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await tick();
  await elements['btn-ipv6-probe'].click();
  await tick();
  assert.strictEqual(elements['btn-ipv6-script'].disabled, true);
  assert(elements['ipv6-result'].innerHTML.includes('尚未检测到'));
  probe = {ready: true, upgrade_supported: true, addresses: [{interface: 'ens3', address: '<img src=x>'}],
    default_routes: [{}], egress_ipv6: '2606:4700::1234'};
  elements['btn-ipv6-probe'].click();
  await tick();
  assert.strictEqual(elements['btn-ipv6-script'].disabled, false);
  assert(elements['ipv6-result'].innerHTML.includes('&lt;img src=x&gt;'));
  assert(!elements['ipv6-result'].innerHTML.includes('<img'));
  sandbox.Api.get = async () => { throw new Error('检测超时'); };
  elements['btn-ipv6-probe'].click();
  await tick();
  assert.strictEqual(elements['btn-ipv6-script'].disabled, true);
  assert.strictEqual(elements['btn-ipv6-probe'].disabled, false);
  assert.strictEqual(elements['ipv6-result'].textContent, '检测超时');
  console.log('PASS: IPv6 UI no-IPv6 gating, ready gating, HTML escaping, error recovery');
})().catch(err => { console.error(err); process.exitCode = 1; });

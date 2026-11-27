const fs = require('fs'), vm = require('vm'), assert = require('assert');
const elements = {};
function el(id) {
  return elements[id] || (elements[id] = {value: '', innerHTML: '', textContent: '', disabled: false,
    addEventListener(name, callback) { this[name] = callback; }});
}
let cleared = 0;
const urls = [];
const sandbox = {
  Layout: {init() {}, content() { return el('content'); }}, document: {getElementById: el},
  Api: {get: async () => ({data: {}}), post: async url => { urls.push(url); return {}; }},
  Validator: {clearAllErrors() {}, isPassword() { return true; }, showError() { throw Error('unexpected validation'); }},
  Toast: {success() {}, error(message) { throw Error(message); }}, escHtml: String,
  Auth: {clear() { cleared++; }, logout() { throw Error('must not call revoked logout endpoint'); }},
  window: {location: {href: ''}}, setTimeout(callback) { callback(); }
};
vm.runInNewContext(fs.readFileSync('frontend/js/settings.js', 'utf8'), sandbox);
(async () => {
  el('pwd-old').value = 'old-password'; el('pwd-new').value = 'new-password'; el('pwd-confirm').value = 'new-password';
  el('btn-save-password').click();
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(cleared, 1);
  assert.strictEqual(sandbox.window.location.href, '/index.html');
  assert.deepStrictEqual(urls, ['/api/auth/change-password']);
  console.log('PASS: password change redirects without redundant expired-session logout');
})().catch(error => { console.error(error); process.exitCode = 1; });

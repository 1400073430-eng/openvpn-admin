const fs = require('fs'), vm = require('vm'), assert = require('assert');
let status = 401, payload = {detail: '用户名或密码错误'};
const values = {};
class XHR {
  open() {} setRequestHeader() {}
  send() { this.status = status; this.responseText = JSON.stringify(payload); this.readyState = 4; this.onreadystatechange(); }
}
const sandbox = {document: {}, XMLHttpRequest: XHR, location: {pathname: '/index.html', href: ''},
  sessionStorage: {getItem: key => values[key], setItem(key, val) { values[key] = val; }, removeItem(key) { delete values[key]; }},
  setTimeout(callback) { callback(); }};
sandbox.window = sandbox;
vm.runInNewContext(fs.readFileSync('frontend/js/api.js', 'utf8'), sandbox);
(async () => {
  await assert.rejects(sandbox.Api.post('/api/auth/login', {}), /用户名或密码错误/);
  sandbox.Auth.setUser('admin');
  const errors = [];
  sandbox.Toast.error = message => errors.push(message);
  sandbox.Api.post = async () => { throw Error('网络中断'); };
  await sandbox.Auth.logout();
  assert.strictEqual(sandbox.Auth.getUser(), 'admin');
  assert.strictEqual(sandbox.location.href, '');
  assert(errors[0].includes('退出未完成'));
  sandbox.Api.post = async () => ({});
  await sandbox.Auth.logout();
  assert.strictEqual(sandbox.Auth.getUser(), '');
  assert.strictEqual(sandbox.location.href, '/index.html');
  console.log('PASS: wrong-password detail and truthful logout failure/success');
})().catch(error => { console.error(error); process.exitCode = 1; });

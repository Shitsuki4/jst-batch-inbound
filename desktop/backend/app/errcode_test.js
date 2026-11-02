'use strict';
// 页面端错误码：本机服务连不上、返回无法识别、HTTP 错误都带码，且都不算权限问题；
// 所有页面都引入 errcode.js；设置页诊断分区和更换采购单对话框接的是已有的接口。
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const web = path.join(__dirname, 'web');
const src = fs.readFileSync(path.join(web, 'errcode.js'), 'utf8');

function load(fetchImpl) {
  const g = { location: { origin: 'http://127.0.0.1:8765', href: 'http://127.0.0.1:8765/' }, fetch: fetchImpl, console, URL };
  g.window = g;
  vm.runInNewContext(src, g);
  return g;
}

function response(status, body, isJson) {
  return {
    ok: status < 400, status,
    headers: { get: () => (isJson === false ? 'text/html' : 'application/json') },
    json: () => (isJson === false ? Promise.reject(new SyntaxError('bad')) : Promise.resolve(body)),
    clone() { return this; }
  };
}

(async () => {
  // 连不上本机服务
  let g = load(() => Promise.reject(new TypeError('Failed to fetch')));
  await assert.rejects(g.fetch('/api/state'), (e) => {
    assert.strictEqual(e.error_code, 'E-NET-1001');
    assert.ok(/^\[E-NET-1001\] /.test(e.message), e.message);
    assert.ok(!e.message.includes('权限'));
    return true;
  });
  assert.strictEqual(g.JstErr.isPermission({ error_code: 'E-NET-1001' }), false);

  // 服务器 500 且没带码 / 接口不存在 / 返回不是 JSON
  for (const [status, body, isJson, code] of [
    [500, { ok: false, msg: 'Internal error' }, true, 'E-NET-1004'],
    [404, { ok: false, msg: 'not found' }, true, 'E-REQ-1404']
  ]) {
    g = load(() => Promise.resolve(response(status, body, isJson)));
    const r = await (await g.fetch('/api/po/create', { method: 'POST' })).json();
    assert.strictEqual(r.ok, false, code);
    assert.strictEqual(r.error_code, code);
    assert.ok(String(r.msg).startsWith('[' + code + ']'), r.msg);
    assert.strictEqual(g.JstErr.isPermission(r), false, code);
  }

  g = load(() => Promise.resolve(response(200, null, false)));
  await assert.rejects((await g.fetch('/api/state')).json(), (e) => e.error_code === 'E-NET-1003' && /^\[E-NET-1003\] /.test(e.message));

  // 服务端已带码的不改；只有 E-JST-1401 是权限
  g = load(() => Promise.resolve(response(200, { ok: false, msg: '[E-NET-2002] 请求聚水潭超时', error_code: 'E-NET-2002' })));
  const r = await (await g.fetch('/api/po/create')).json();
  assert.strictEqual(r.msg, '[E-NET-2002] 请求聚水潭超时');
  assert.strictEqual(g.JstErr.isPermission(r), false);
  assert.strictEqual(g.JstErr.isPermission({ error_code: 'E-JST-1401' }), true);
  for (const code of ['E-NET-2001', 'E-NET-2002', 'E-JST-1001', 'E-JST-1301', 'E-PO-1001', 'E-APP-9001', 'E-NET-1001']) {
    assert.strictEqual(g.JstErr.isPermission({ error_code: code }), false, code);
  }
  // 本地输入检查报错补 E-UI-1001，已带码的不重复加
  assert.strictEqual(g.JstErr.ensure('请输入采购单号'), '[E-UI-1001] 请输入采购单号');
  assert.strictEqual(g.JstErr.ensure('[E-PO-2008] 请输入'), '[E-PO-2008] 请输入');

  // 每个页面都先引入 errcode.js
  for (const f of fs.readdirSync(web).filter((n) => n.endsWith('.html'))) {
    const html = fs.readFileSync(path.join(web, f), 'utf8');
    if (!/<script/i.test(html)) continue;
    assert.ok(html.includes('errcode.js'), f + ' 没有引入 errcode.js');
  }

  // 设置页「诊断与日志」和更换采购单对话框接的接口都存在
  const index = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  assert.ok(index.includes('/diag-settings.js'));
  assert.ok(index.includes('id="btn-rematch-po"'));
  const diag = fs.readFileSync(path.join(web, 'diag-settings.js'), 'utf8');
  const app = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
  const server = fs.readFileSync(path.join(__dirname, 'server.py'), 'utf8');
  for (const [api, method] of [['/api/diag/export', 'post'], ['/api/diag/open-logs', 'post'], ['/api/diag/logs', 'get'], ['/api/diag/info', 'get']]) {
    assert.ok(diag.includes(api), api);
    assert.ok(method === 'get' ? server.includes('path == "' + api + '"') : server.includes('def _post' + api.replace(/[\/-]/g, '_') + '('), api + ' 在服务端不存在');
  }
  for (const api of ['/api/po/rematch/preview', '/api/po/rematch/confirm']) {
    assert.ok(app.includes(api), api);
    assert.ok(server.includes('def _post' + api.replace(/[\/-]/g, '_') + '('), api);
  }
  console.log('errcode tests passed');
})().catch((e) => { console.error(e); process.exit(1); });

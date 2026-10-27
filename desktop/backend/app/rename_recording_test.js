// node desktop/backend/app/rename_recording_test.js
// Regression: 改单号后录像停止且不按新单号恢复。改的是正在录像的物流单时询问“是否按新单号重新录制？”：
// 是 → 发普通 tracking（电脑端保存当前片段、按新单号开新片段）；否 → 不发任何事件，当前录像原样继续。
'use strict';
const assert = require('assert');
const dispatched = [], listeners = {};
global.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
global.document = { addEventListener() {}, querySelector() { return null; } };
global.location = { href: 'http://127.0.0.1/' };
global.window = {
  fetch: async () => ({ ok: true, json: async () => ({ ok: true }) }),
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  dispatchEvent(ev) { dispatched.push(ev); },
  confirm() { throw Error('ask callback should be used'); },
};
window.parent = window;
require('./web/native-receipt.js');
const L = window.NativeReceiptLink;
const KEY = '普通:7:42';
const video = { enabled: true, active: true, ready: true, test: false, key: KEY, tracking: 'OLD-1' };

(async () => {
  // 判定：只有正在录这张物流单时才问
  assert.strictEqual(L.renameNeedsConfirm(video, KEY), true);
  assert.strictEqual(L.renameNeedsConfirm({ ...video, active: false }, KEY), false, '未在录像不问');
  assert.strictEqual(L.renameNeedsConfirm({ ...video, enabled: false }, KEY), false, '自动录像关闭不问');
  assert.strictEqual(L.renameNeedsConfirm({ ...video, test: true }, KEY), false, '测试录像不问');
  assert.strictEqual(L.renameNeedsConfirm(video, '普通:7:43'), false, '改的是别的物流单不问');
  assert.strictEqual(L.renameNeedsConfirm(undefined, KEY), false, '网页版/无桌面录像不问');
  assert.ok(L.renameQuestion('OLD-1', 'NEW-1').includes('OLD-1 → NEW-1'));
  assert.ok(L.renameQuestion('OLD-1', 'NEW-1').includes('是否按新单号重新录制'));

  window.__nativeVideo = video;
  let asked = 0;
  // 否：当前录像继续，不发任何事件
  assert.strictEqual(await L.afterRename({ key: KEY, from: 'OLD-1', to: 'NEW-1' }, async () => { asked++; return false; }), 'continue');
  assert.strictEqual(dispatched.length, 0, '选“否”不能停录或切段');
  // 是：按新单号发普通 tracking（与切换单号同一路径）
  assert.strictEqual(await L.afterRename({ key: KEY, from: 'OLD-1', to: ' NEW-1 ' }, async () => { asked++; return true; }), 'restart');
  assert.strictEqual(dispatched.length, 1);
  assert.deepStrictEqual([dispatched[0].type, dispatched[0].detail], ['native-receipt', { kind: 'tracking', tracking_no: 'NEW-1', key: KEY }]);
  assert.strictEqual(asked, 2);
  // 不需要问的情形：不弹窗、不发事件
  const never = async () => { throw Error('should not ask'); };
  assert.strictEqual(await L.afterRename({ key: '普通:7:43', from: 'A', to: 'B' }, never), 'none');
  assert.strictEqual(await L.afterRename({ key: KEY, from: 'OLD-1', to: 'OLD-1' }, never), 'none', '单号未变不问');
  window.__nativeVideo = { ...video, active: false };
  assert.strictEqual(await L.afterRename({ key: KEY, from: 'OLD-1', to: 'NEW-2' }, never), 'none', '已停录不擅自开录');
  assert.strictEqual(dispatched.length, 1);
  console.log('rename-recording ok');
})().catch(e => { console.error(e); process.exit(1); });

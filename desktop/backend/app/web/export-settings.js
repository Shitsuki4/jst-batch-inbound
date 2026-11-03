/* 设置 → 数据与备份 → 导出保存目录。排版和「视频举证 → 录像保存目录」一致：
   左边标签，右边输入框 + 「选择目录」，下面一行说明目录结构。导出文件存在：根目录\日期\类别\文件。 */
(function () {
  'use strict';
  var pane = document.querySelector('[data-settings-pane="data"]');
  if (!pane) return;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  var native = !!(window.NativeDesk && window.NativeDesk.available);
  function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || 'GET', headers: { 'Content-Type': 'application/json' } };
    if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
    return fetch(path, init).then(function (r) { return r.json(); });
  }
  var card = document.createElement('div');
  card.className = 'card';
  card.id = 'export-dir-card';
  card.innerHTML =
    '<div class="card-hd">导出保存目录</div>' +
    '<div class="form-row"><label for="export-directory">导出保存目录</label><div class="input-action">' +
    '<input class="input" id="export-directory" type="text" aria-label="导出保存目录" autocomplete="off" spellcheck="false">' +
    '<button class="btn btn-ghost" id="export-choose-directory" type="button">选择目录</button></div></div>' +
    '<p class="hint form-hint">新导出：日期 / 类别 / 文件（如 2026年11月3日 / 收货明细 / 收货明细_….xlsx），和录像目录一样按日期分层。收货明细、联合收货、无采购单、操作记录、诊断包等导出文件都存在这里。改目录不搬动以前导出的文件。</p>' +
    '<p class="export-dir-status" id="export-dir-status" role="status"></p>' +
    '<div class="export-dir-actions"><button class="btn btn-primary" id="export-dir-save" type="button">保存导出目录</button>' +
    '<button class="btn btn-ghost" id="export-dir-default" type="button">恢复默认</button>' +
    '<button class="btn btn-ghost" id="export-dir-open" type="button">打开导出目录</button></div>';
  pane.insertBefore(card, pane.firstChild);
  var $ = function (id) { return document.getElementById(id); };
  var info = null;
  if (!native) {
    // 普通浏览器里没有系统选择文件夹窗口，只能手填路径
    $('export-choose-directory').disabled = true;
    $('export-choose-directory').title = '请在桌面程序里选择；这里可以直接填完整路径';
  }
  function show(msg, bad) {
    var el = $('export-dir-status');
    el.className = 'export-dir-status' + (bad ? ' err' : '');
    el.textContent = msg;
  }
  function paint(j, note) {
    info = j;
    $('export-directory').value = j.directory || '';
    var text = (j.custom ? '当前使用自定义目录：' : '当前使用默认目录：') + (j.directory || '');
    if (!j.writable) return show((j.error || '导出目录不可用') + '。不改的话导出会失败。', true);
    show(text + (note ? '。' + note : ''));
  }
  function fail(r) { show(r.msg || '保存失败', true); }
  function load() {
    return api('/api/export-dir').then(function (r) { if (r.ok) paint(r); else fail(r); }).catch(function (e) { show(netErrorText(e), true); });
  }
  function netErrorText() { return '[E-NET-1001] 没连上本机收货服务'; }
  function save(dir) {
    var btns = ['export-dir-save', 'export-dir-default'].map($);
    btns.forEach(function (b) { b.disabled = true; });
    return api('/api/export-dir', { method: 'POST', body: { directory: dir } }).then(function (r) {
      if (r.ok) paint(r, r.note || '已保存'); else fail(r);
    }).catch(function (e) { show(netErrorText(e), true); }).then(function () { btns.forEach(function (b) { b.disabled = false; }); });
  }
  $('export-dir-save').onclick = function () { save(($('export-directory').value || '').trim()); };
  $('export-dir-default').onclick = function () { save(''); };
  $('export-dir-open').onclick = function () {
    api('/api/open-folder', { method: 'POST', body: {} }).then(function (r) { if (!r.ok) fail(r); }).catch(function (e) { show(netErrorText(e), true); });
  };
  $('export-choose-directory').onclick = function () {
    if (!native) return;
    NativeDesk.call('chooseExportDirectory', { initial: ($('export-directory').value || (info && info.directory) || '') }).then(function (r) {
      if (r && r.directory) { $('export-directory').value = r.directory; show('已选择，点「保存导出目录」后生效：' + r.directory); }
    }).catch(function (e) { show((e && e.message) || String(e), true); });
  };
  document.addEventListener('click', function (ev) {
    var b = ev.target && ev.target.closest && ev.target.closest('[data-settings-section="data"]');
    if (b) load();
  });
  load();
})();

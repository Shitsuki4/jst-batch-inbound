/* 操作记录：GET /api/oplog/list|meta|export，保留天数经 POST /api/settings 保存 */
(function () {
  'use strict';
  var root = document.getElementById('oplog-view');
  if (!root) return;
  var PAGE = 50, FETCH_LIMIT = 5000;
  var rows = [], total = 0, page = 0, meta = null, busy = false, scope = { source: '', sid: '' };
  function $(id) { return document.getElementById('ol-' + id); }
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  async function getJSON(url) {
    var r = await fetch(url, { cache: 'no-store' });
    var j = await r.json();
    if (!j.ok) throw Error(j.msg || '请求失败');
    return j;
  }
  function filters() {
    // 采购单/物流单号共用一个输入框：后端 q 对采购单号、外部单号、物流单号、编码、说明模糊匹配
    return {
      start: $('start').value, end: $('end').value, q: $('po').value.trim(),
      sku: $('sku').value.trim(), device: $('device').value, op: $('op').value,
      source: scope.source, sid: scope.sid
    };
  }
  function qs(f, extra) {
    var p = new URLSearchParams();
    Object.keys(f).forEach(function (k) { if (f[k] !== '' && f[k] != null) p.set(k, f[k]); });
    Object.keys(extra || {}).forEach(function (k) { p.set(k, extra[k]); });
    return p.toString();
  }
  function fillMeta() {
    var op = $('op'), dev = $('device'), keepOp = op.value, keepDev = dev.value;
    op.innerHTML = '<option value="">全部操作类型</option>' + Object.keys(meta.ops || {}).map(function (k) {
      return '<option value="' + esc(k) + '">' + esc(meta.ops[k]) + '</option>';
    }).join('');
    var kinds = meta.device_kinds || {};
    dev.innerHTML = '<option value="">全部设备</option>' + Object.keys(kinds).map(function (k) {
      return '<option value="' + esc(k) + '">' + esc(kinds[k]) + '</option>';
    }).join('') + (meta.devices || []).filter(function (d) { return d.device_id || d.device_name; }).map(function (d) {
      var v = d.device_id || d.device_name;
      return '<option value="' + esc(v) + '">PDA：' + esc(d.device_name || '未命名') + (d.device_id ? ' [' + esc(d.device_id) + ']' : '') + '</option>';
    }).join('');
    op.value = keepOp; dev.value = keepDev;
    var ret = $('retention');
    ret.min = meta.min_retention_days || 7; ret.max = meta.max_retention_days || 3650;
    if (document.activeElement !== ret) ret.value = meta.retention_days || '';
    $('retention-hint').textContent = '可设 ' + ret.min + '–' + ret.max + ' 天，超期记录自动清理。';
  }
  function qtyText(r) {
    if (r.qty_before == null && r.qty_after == null) return '';
    var d = r.qty_change, sign = d > 0 ? '+' + d : String(d);
    return esc(r.qty_before == null ? '—' : r.qty_before) + ' → ' + esc(r.qty_after == null ? '—' : r.qty_after) + (d != null ? ' <small>(' + esc(sign) + ')</small>' : '');
  }
  function noText(r) {
    var parts = [];
    if (r.po_id || r.outer_po_id) parts.push('采购：' + esc(r.po_id || '') + (r.outer_po_id ? ' / ' + esc(r.outer_po_id) : ''));
    if (r.tracking_no) parts.push('物流：' + esc(r.tracking_no));
    return parts.join('<br>');
  }
  function render() {
    var pages = Math.max(1, Math.ceil(rows.length / PAGE));
    if (page >= pages) page = pages - 1;
    var slice = rows.slice(page * PAGE, page * PAGE + PAGE);
    $('rows').innerHTML = slice.length ? slice.map(function (r) {
      var bad = r.result && r.result !== 'ok' && r.result !== 'success';
      return '<tr><td class="mono">' + esc(r.at_text) + '</td><td>' + esc(r.device_label) + '</td><td>' + esc(r.op_label)
        + (bad ? ' <span class="chip bad">' + esc({ fail: '失败', pending: '待确认' }[r.result] || r.result) + '</span>' : '') + '<br><small>' + esc(r.module_label) + '</small></td><td class="mono">' + noText(r)
        + '</td><td class="mono">' + esc(r.sku || r.code || '') + '</td><td>' + qtyText(r) + '</td><td>' + esc(r.summary) + '</td></tr>';
    }).join('') : '<tr><td colspan="7" class="empty">当前筛选无记录</td></tr>';
    var more = total > rows.length ? '（共 ' + total + ' 条，仅载入最近 ' + rows.length + ' 条，请缩小筛选范围；导出同样上限）' : '（共 ' + total + ' 条）';
    $('pageinfo').textContent = '第 ' + (page + 1) + ' / ' + pages + ' 页 ' + more;
    $('prev').disabled = page <= 0; $('next').disabled = page >= pages - 1;
    $('scope').hidden = !scope.sid;
    $('scope-text').textContent = scope.sid ? '仅显示收货单 #' + scope.sid + (scope.source ? '（' + ((meta && meta.modules && meta.modules[scope.source]) || scope.source) + '）' : '') + ' 的记录' : '';
    $('export').href = '/api/oplog/export?' + qs(filters());
  }
  async function run(fn) {
    if (busy) return; busy = true; $('msg').textContent = '加载中…';
    try { await fn(); $('msg').textContent = ''; }
    catch (e) { $('msg').textContent = '加载失败：' + (e.message || '网络错误'); }
    finally { busy = false; }
  }
  async function loadMeta() { meta = await getJSON('/api/oplog/meta'); fillMeta(); }
  async function load(resetPage) {
    if (!meta) await loadMeta();
    var j = await getJSON('/api/oplog/list?' + qs(filters(), { limit: FETCH_LIMIT }));
    rows = j.rows || []; total = j.total || 0;
    if (resetPage) page = 0;
    render();
  }
  async function saveRetention() {
    var v = $('retention').value.trim(), msg = $('retention-msg');
    var n = Number(v), lo = Number($('retention').min), hi = Number($('retention').max);
    if (!/^\d+$/.test(v) || n < lo || n > hi) { msg.textContent = '请输入 ' + lo + '–' + hi + ' 之间的整数'; return; }
    $('retention-save').disabled = true;
    try {
      var r = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ oplog_retention_days: String(n) }) });
      var d = await r.json();
      if (!r.ok || !d.ok) throw Error(d.msg || '保存失败');
      msg.textContent = '已保存：保留 ' + n + ' 天';
      if (meta) meta.retention_days = n;
    } catch (e) { msg.textContent = '未保存：' + e.message; }
    finally { $('retention-save').disabled = false; }
  }
  $('search').onclick = function () { run(function () { return load(true); }); };
  $('reset').onclick = function () {
    ['start', 'end', 'po', 'sku'].forEach(function (k) { $(k).value = ''; });
    $('device').value = ''; $('op').value = ''; scope = { source: '', sid: '' };
    run(function () { return load(true); });
  };
  $('clear-scope').onclick = function () { scope = { source: '', sid: '' }; run(function () { return load(true); }); };
  $('prev').onclick = function () { if (page > 0) { page--; render(); } };
  $('next').onclick = function () { page++; render(); };
  $('retention-save').onclick = saveRetention;
  root.addEventListener('keydown', function (e) { if (e.key === 'Enter' && e.target.matches('input.ol-filter')) $('search').click(); });
  ['start', 'end', 'device', 'op'].forEach(function (k) { $(k).addEventListener('change', function () { $('export').href = '/api/oplog/export?' + qs(filters()); }); });
  document.addEventListener('app:tab', function (e) { if (e.detail === 'oplog') run(function () { return loadMeta().then(function () { return load(false); }); }); });

  /* 外部入口：openOpLog({source:'receipt', sid:123}) 切到本页并按单过滤 */
  window.openOpLog = function (opt) {
    opt = opt || {};
    scope = { source: opt.source ? String(opt.source) : '', sid: opt.sid ? String(opt.sid) : '' };
    ['start', 'end', 'po', 'sku'].forEach(function (k) { $(k).value = ''; });
    $('device').value = ''; $('op').value = '';
    var tab = document.querySelector('.tab[data-tab="oplog"]');
    if (tab) tab.click();
    if (!document.querySelector('.view.active[data-view="oplog"]')) return false; // 切换被拦截（例如数量未保存）
    run(function () { return load(true); });
    return true;
  };
}());

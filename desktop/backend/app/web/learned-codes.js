/* 设置 →「商品资料」分区内的「已学习条码」卡片：列表、搜索、删除、导出 CSV。
   必须在 settings-workspace.js 之后加载：它已把设置卡片分好区，这里只往「商品资料」分区里追加一张卡片，
   不作为顶层卡片插入（否则会打乱 settings-workspace 按顺序分区的结果）。
   学习条码只在商品资料查不到时兜底；按当前登录公司 + 货主 + 仓储共享。 */
(function () {
  'use strict';
  var pane = document.querySelector('[data-settings-pane="catalog"]');
  if (!pane || document.getElementById('learned-codes-card')) return;

  var STATE = {
    active: { text: '使用中', cls: 'lc-active' },
    superseded: { text: '已被商品资料覆盖', cls: 'lc-superseded' },
    conflict: { text: '冲突已停用', cls: 'lc-conflict' }
  };
  var rows = [], loaded = false, busy = false, timer = null;

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function $(id) { return document.getElementById(id); }
  async function api(path, body) {
    var r = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    var j = await r.json();
    if (!j.ok) throw Error(j.msg || '请求失败');
    return j;
  }

  var card = document.createElement('div');
  card.className = 'card';
  card.id = 'learned-codes-card';
  card.innerHTML =
    '<div class="card-hd">已学习条码</div>' +
    '<p class="hint">商品资料始终优先，学习条码仅在商品资料查不到时兜底；按当前公司 + 货主 + 仓储共享。' +
    '商品资料出现此码后学习记录自动让位（已被商品资料覆盖 / 冲突已停用），之后不会自动恢复。</p>' +
    '<div class="row">' +
    '<input class="input" id="lc-q" type="search" placeholder="搜索条码 / 商品编码 / 名称" aria-label="搜索已学习条码">' +
    '<button type="button" class="btn btn-ghost" id="lc-refresh">刷新</button>' +
    '<button type="button" class="btn btn-ghost" id="lc-delete" disabled>删除选中</button>' +
    '<a class="btn btn-ghost" id="lc-export" href="/api/learned/export" download="learned-codes.csv">导出 CSV</a>' +
    '</div>' +
    '<p class="hint" id="lc-msg" role="status"></p>' +
    '<div class="lc-table-wrap"><table class="lc-table"><thead><tr>' +
    '<th><input type="checkbox" id="lc-all" aria-label="全选"></th><th>条码</th><th>商品</th>' +
    '<th>货主 / 仓储</th><th>状态</th><th>使用</th><th>学习时间</th>' +
    '</tr></thead><tbody id="lc-body"></tbody></table></div>';
  pane.appendChild(card);

  var style = document.createElement('style');
  style.textContent =
    '#learned-codes-card .lc-table-wrap{max-height:420px;overflow:auto;margin-top:8px}' +
    '#learned-codes-card .lc-table{width:100%;border-collapse:collapse;font-size:13px}' +
    '#learned-codes-card .lc-table th,#learned-codes-card .lc-table td{padding:6px 8px;border-bottom:1px solid #e6edf2;text-align:left;vertical-align:top;overflow-wrap:anywhere}' +
    '#learned-codes-card .lc-table th{position:sticky;top:0;background:#f6f8fb;color:#5d7287;font-weight:600}' +
    '#learned-codes-card .lc-state{display:inline-block;padding:0 6px;border-radius:8px;font-size:12px;line-height:18px;white-space:nowrap;border:1px solid}' +
    '#learned-codes-card .lc-active{background:#e7f5f3;color:#087c84;border-color:#b6e0db}' +
    '#learned-codes-card .lc-superseded{background:#f1f3f6;color:#66788a;border-color:#d8dfe6}' +
    '#learned-codes-card .lc-conflict{background:#fdf0e8;color:#b45330;border-color:#f0cdb8}' +
    '#learned-codes-card .lc-sub{color:#8193a4;font-size:12px}';
  document.head.appendChild(style);

  function selected() {
    return Array.prototype.map.call(card.querySelectorAll('[data-lc-id]:checked'), function (b) { return Number(b.dataset.lcId); });
  }
  function sync() {
    var n = selected().length;
    $('lc-delete').disabled = busy || !n;
    $('lc-delete').textContent = n ? '删除选中（' + n + '）' : '删除选中';
    $('lc-all').checked = !!rows.length && n === rows.length;
  }
  function render() {
    $('lc-body').innerHTML = rows.length ? rows.map(function (r) {
      var st = STATE[r.status] || { text: r.status_text || r.status, cls: 'lc-superseded' };
      var title = r.status === 'conflict' && r.catalog_sku ? ' title="商品资料对应：' + esc(r.catalog_sku) + '"' : '';
      return '<tr><td><input type="checkbox" data-lc-id="' + esc(r.id) + '" aria-label="选择 ' + esc(r.code) + '"></td>' +
        '<td>' + esc(r.code) + '</td>' +
        '<td>' + esc(r.name || '') + '<div class="lc-sub">' + esc(r.sku_id) + (r.spec ? ' · ' + esc(r.spec) : '') + '</div></td>' +
        '<td class="lc-sub">' + esc(r.owner) + ' / ' + esc(r.auth) + '</td>' +
        '<td><span class="lc-state ' + st.cls + '"' + title + '>' + esc(st.text) + '</span></td>' +
        '<td class="lc-sub">' + esc(r.hits || 0) + ' 次' + (r.last_used_at ? '<br>' + esc(r.last_used_at) : '') + '</td>' +
        '<td class="lc-sub">' + esc(r.created_at || '') + '</td></tr>';
    }).join('') : '<tr><td colspan="7" class="lc-sub">暂无已学习条码</td></tr>';
    sync();
  }
  async function load() {
    if (busy) return;
    busy = true;
    try {
      var j = await api('/api/learned/list?q=' + encodeURIComponent($('lc-q').value.trim()));
      rows = j.rows || [];
      loaded = true;
      $('lc-msg').textContent = j.logged === false ? '未登录ERP，登录后显示当前公司的学习条码。' : '共 ' + rows.length + ' 条';
    } catch (e) {
      rows = [];
      $('lc-msg').textContent = e.message;
    } finally {
      busy = false;
      render();
    }
  }
  async function remove() {
    var ids = selected();
    if (!ids.length || busy) return;
    if (!await JstDialog.confirm('删除选中的 ' + ids.length + ' 条学习条码？删除后再扫到这些码会按「商品资料查不到」处理。')) return;
    busy = true;
    try {
      var j = await api('/api/learned/delete', { ids: ids });
      $('lc-msg').textContent = j.msg || ('已删除 ' + j.deleted + ' 条');
    } catch (e) {
      $('lc-msg').textContent = e.message;
    } finally {
      busy = false;
    }
    await load();
  }

  $('lc-refresh').onclick = load;
  $('lc-delete').onclick = remove;
  $('lc-q').oninput = function () { clearTimeout(timer); timer = setTimeout(load, 250); };
  $('lc-all').onchange = function () {
    var on = this.checked;
    card.querySelectorAll('[data-lc-id]').forEach(function (b) { b.checked = on; });
    sync();
  };
  $('lc-body').onchange = sync;
  // 进入「商品资料」分区时才读取，避免启动时多一次请求
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-settings-section="catalog"]');
    if (b) load();
  });
  if (!pane.hidden) load();
  window.LearnedCodes = { reload: load, isLoaded: function () { return loaded; } };
})();

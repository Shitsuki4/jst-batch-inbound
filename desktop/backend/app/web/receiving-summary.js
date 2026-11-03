// 收货对单公共组件（4.1.8）。
// 无采购单匹配、暂存对单、结单对单、联合收货汇总都用这一套，样式和「有采购单收货」一致：
//   上方是「本轮收货进度」卡（已齐 / 短收 / 超收 / 未收），下方是商品表（商品信息 | 应收 | 实收 | 差额），
//   商品行直接用 ReceivingRoundUI.body 生成，和有采购单收货工作区是同一份标记和配色。
// 只管显示，不改任何数据和流程；未关联采购单时只显示实收，不虚构应收或差异。
(function (root) {
  'use strict';
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function n(v) { return v == null || v === '' || !isFinite(Number(v)) ? null : Number(v); }
  function num(v) { v = n(v); return v == null ? '待核实' : String(Math.round(v * 10000) / 10000); }

  // 把各收货方式的行统一成「有采购单收货」本轮商品行的字段
  function row(r, linked) {
    var recv = n(r.recv != null ? r.recv : r.qty) || 0;
    var extra = !!(r.extra || r.kind === 'extra');
    var plan = linked && !extra ? n(r.plan) : null;
    var diff = plan == null ? null : Math.round((recv - plan) * 10000) / 10000;
    var reason = r.reason;
    if (!reason) {
      if (!linked) reason = '实收 ' + num(recv);
      else if (extra) reason = '采购单外 ' + num(recv);
      else if (plan == null) reason = '待核实';
      else if (diff === 0) reason = '收齐';
      else reason = diff < 0 ? '少 ' + num(-diff) : '多 ' + num(diff);
    }
    return {
      poi_id: r.poi_id || r.sku_id || r.name, sku_id: r.sku_id, name: r.name, spec: r.spec,
      sku_codes: r.sku_codes || (r.sku_code ? [r.sku_code] : []), auxiliary_codes: r.auxiliary_codes,
      ordered: r.ordered, erp_received: r.erp_received,
      plan: linked ? (extra ? 0 : plan) : null, recv: recv, extra: extra,
      done: linked && !extra && plan != null && diff === 0,
      over: linked && !extra && plan != null && diff > 0,
      reason: reason, note: r.note, sources: r.sources
    };
  }
  function rows(list, linked) { return (list || []).map(function (r) { return row(r, linked); }); }
  function status(r) { return root.ReceivingRoundUI ? ReceivingRoundUI.status(r) : (r.done ? 'done' : r.over || r.extra ? 'over' : r.recv > 0 ? 'short' : 'none'); }

  // 本轮收货进度卡：和有采购单收货右上角的进度卡同一套样式
  function progress(list, opts) {
    opts = opts || {};
    var linked = !!opts.linked, plan = 0, recv = 0, known = linked;
    var c = { done: 0, short: 0, over: 0, none: 0 };
    list.forEach(function (r) {
      recv += r.recv || 0;
      if (linked && !r.extra) { if (r.plan == null) known = false; else plan += r.plan; }
      if (linked) c[status(r)]++;
    });
    recv = Math.round(recv * 10000) / 10000; plan = Math.round(plan * 10000) / 10000;
    // 向下取整：还有短收/未收时不显示 100%（例如 379/380 不能显示成 100%）
    var pct = known && plan > 0 ? Math.min(100, Math.floor(recv / plan * 100)) : 0;
    if (pct === 100 && (c.short || c.none)) pct = 99;
    var legend = linked
      ? [['已齐', 'c-ok', c.done], ['短收', 'c-warn', c.short], ['超收', 'c-danger', c.over], ['未收', 'c-none', c.none]]
      : [['商品', 'c-ok', list.length], ['物流单', 'c-none', opts.batches == null ? '—' : opts.batches]];
    return '<div class="card progress-card rs-progress">'
      + '<div class="pc-hd">' + esc(opts.title || (linked ? '本轮收货进度' : '实收合计（未关联采购单）')) + '</div>'
      + '<div class="pc-big"><b>' + num(recv) + '</b><span>' + (linked ? '/ ' + (known ? num(plan) : '待核实') + ' 件' : '件') + '</span>'
      + '<em>' + (linked && known ? pct + '%' : '') + '</em></div>'
      + (linked ? '<div class="pc-bar"><i class="' + (known && recv > plan ? 'over' : '') + '" style="width:' + pct + '%"></i></div>' : '')
      + '<div class="pc-legend' + (linked ? '' : ' rs-legend-2') + '">' + legend.map(function (x) { return '<div><span>' + x[0] + '</span><b class="' + x[1] + '">' + esc(x[2]) + '</b></div>'; }).join('') + '</div>'
      + '</div>';
  }

  // 商品表。opts.linked：是否有应收；opts.extraCol：{title, cell(row)} 附加一列（如联合收货的采购来源）；
  // opts.onlyDiff：只列没收齐的；opts.empty：没有行时的提示。
  function table(list, opts) {
    opts = opts || {};
    var linked = !!opts.linked;
    var shown = opts.onlyDiff ? list.filter(function (r) { return !r.done; }) : list;
    if (!shown.length) return '<p class="muted rs-empty">' + esc(opts.empty || (opts.onlyDiff ? '没有差异：全部收齐。' : '还没有实收商品。')) + '</p>';
    var head = '<th>商品信息</th>' + (linked ? '<th>' + esc(opts.planTitle || '应收') + '</th>' : '')
      + '<th>' + esc(opts.recvTitle || '实收') + '</th>' + (linked ? '<th>差额</th>' : '')
      + (opts.extraCol ? '<th>' + esc(opts.extraCol.title) + '</th>' : '');
    var body;
    if (linked && root.ReceivingRoundUI && !opts.extraCol && !shown.some(function (r) { return r.note; })) {
      body = ReceivingRoundUI.body({ rows: shown }, !!opts.reportMode);
    } else {
      body = shown.map(function (r) {
        var st = linked ? status(r) : 'none';
        var cls = linked ? (r.done ? 'pi-done' : r.over || r.extra ? 'pi-over' : r.recv > 0 ? 'pi-part' : '') : '';
        var prod = root.ReceivingRoundUI && ReceivingRoundUI.product ? ReceivingRoundUI.product(r, !!opts.reportMode)
          : '<div class="pi-name">' + esc(r.name || '未提供商品名') + '</div><div class="pi-meta">' + esc(r.sku_id || '') + '</div>';
        if (r.note) prod += '<div class="rs-note">' + esc(r.note) + '</div>';
        return '<tr class="' + cls + '" data-poi="' + esc(r.poi_id) + '"><td class="pi-product">' + prod + '</td>'
          + (linked ? '<td class="pi-plan" data-label="应收">' + (r.extra ? '—' : num(r.plan)) + '</td>' : '')
          + '<td class="pi-recv" data-label="实收">' + num(r.recv) + '</td>'
          + (linked ? '<td class="pi-mark" data-label="差额"><span class="round-mark ' + (r.done ? 'done' : r.over || r.extra ? 'over' : '') + ' st-' + st + '">' + esc(r.reason) + '</span></td>' : '')
          + (opts.extraCol ? '<td class="rs-extra">' + opts.extraCol.cell(r) + '</td>' : '') + '</tr>';
      }).join('');
    }
    return '<div class="table-wrap rs-table-wrap"><table class="tbl round-table rs-table' + (linked ? '' : ' rs-recv-only') + '"><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>';
  }

  // 进度卡 + 商品表，各收货方式的对单区都用它
  function panel(list, opts) {
    opts = opts || {};
    var r = list.length && list[0].reason !== undefined && list[0].done !== undefined ? list : rows(list, opts.linked);
    return '<div class="rs-panel">' + (opts.noProgress ? '' : progress(r, opts)) + table(r, opts) + '</div>';
  }

  // 非有采购单收货页面里，商品编码 / 条码点一下也能复制（有采购单收货页由 app.js 处理）
  if (root.document && !root.__rsCopyBound) {
    root.__rsCopyBound = true;
    root.document.addEventListener('click', function (e) {
      var b = e.target && e.target.closest ? e.target.closest('.rs-panel .copy-code, .rs-table .copy-code') : null;
      if (!b) return;
      e.preventDefault();
      var t = b.getAttribute('data-copy') || '';
      var done = function () { b.classList.add('copied'); setTimeout(function () { b.classList.remove('copied'); }, 900); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, function () {});
    });
  }

  root.ReceivingSummary = { row: row, rows: rows, progress: progress, table: table, panel: panel, num: num, status: status };
})(typeof window !== 'undefined' ? window : globalThis);

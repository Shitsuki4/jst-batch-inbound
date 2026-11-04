(function () {
  'use strict';
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function num(v) { if (v == null || !isFinite(Number(v))) return '待核实'; return String(Math.round(Number(v) * 10000) / 10000); }
  function copy(code, label) { return '<span class="pi-code-token"><span>' + esc(code) + '</span> <button type="button" class="copy-code" data-copy="' + esc(code) + '" aria-label="复制' + esc(label + ' ' + code) + '">复制</button></span>'; }
  function unique(list) { return list.filter(function (s, i) { return s && list.indexOf(s) === i; }); }
  // 商品名称和规格（款式名 / 商品简称）常常是同一段文字，或一个包含另一个，例如
  // 「发之食谱洗发水530ml无花果（清爽蓬松）」+「发之食谱洗发水530ml无花果」。内容重复时只显示一次：
  // 规格已包含在名称里就不再显示；规格包含整个名称时用规格当名称。比较时忽略空白、全半角和大小写。
  // 只有规格带来名称里没有的信息时，才作为副标题显示。
  function textKey(v) { var s = String(v == null ? '' : v); if (s.normalize) s = s.normalize('NFKC'); return s.replace(/\s+/g, '').toLowerCase(); }
  function nameSpec(name, spec) {
    var n = String(name == null ? '' : name).trim(), s = String(spec == null ? '' : spec).trim();
    var kn = textKey(n), ks = textKey(s);
    if (!ks || kn.indexOf(ks) >= 0) return {name: n, spec: ''};
    if (!kn || ks.indexOf(kn) >= 0) return {name: s, spec: ''};
    return {name: n, spec: s};
  }
  function fullName(row) { var ns = nameSpec(row && row.name, row && row.spec); return ns.name + (ns.spec ? ' ' + ns.spec : ''); }
  // 收货工作区用两行紧凑版：第一行商品名（+ 规格），第二行灰字「编码 · 条码 · 采购/入库」，点编码即复制。
  // 差异报告（reportMode）仍用完整版，打印 / 核对时信息不省略。
  // 紧凑版里编码本身就是复制按钮（不额外占宽度），鼠标移上去显示下划线和「点击复制」
  function chip(code, label) { return '<button type="button" class="copy-code pi-chip" data-copy="' + esc(code) + '" title="点击复制' + esc(label) + '" aria-label="复制' + esc(label + ' ' + code) + '">' + esc(code) + '</button>'; }
  function compact(row) {
    var barcodes = unique(row.sku_codes || (row.sku_code ? [row.sku_code] : []));
    var aux = unique(row.auxiliary_codes || String(row.aux_codes || '').split(/[,，;；\s]+/)).filter(function (s) { return barcodes.indexOf(s) < 0; });
    var meta = [];
    meta.push(row.sku_id ? chip(row.sku_id, '商品编码') : '编码未提供');
    if (barcodes.length) meta.push(barcodes.map(function (c) { return chip(c, '条码'); }).join(' '));
    if (aux.length) meta.push('辅助 ' + aux.map(function (c) { return chip(c, '辅助码'); }).join(' '));
    if (!barcodes.length && !aux.length) meta.push('条码未提供');
    // ERP 采购 / 入库数只在有值时占位；没值时放进悬停提示，不挤占第二行
    if (row.ordered != null) meta.push('<span class="pi-erp-inline">采购 ' + num(row.ordered) + ' / 入库 ' + num(row.erp_received) + '</span>');
    var ns = nameSpec(row.name, row.spec);
    return '<div class="pi-compact"' + (row.ordered != null ? ' title="' + esc('采购 ' + num(row.ordered) + ' / 入库 ' + num(row.erp_received)) + '"' : '') + '><div class="pi-name" title="' + esc(fullName(row)) + '">' + esc(ns.name || '未提供商品名')
      + (ns.spec ? '<span class="pi-spec-inline">' + esc(ns.spec) + '</span>' : '')
      + (row.extra ? ' <span class="tag-new">本轮计划外商品</span>' : '') + '</div>'
      + '<div class="pi-meta">' + meta.join('<span class="pi-dot" aria-hidden="true"> · </span>') + '</div></div>';
  }
  function product(row, reportMode) {
    if (!reportMode) return compact(row);
    var barcodes = unique(row.sku_codes || (row.sku_code ? [row.sku_code] : []));
    var aux = unique(row.auxiliary_codes || String(row.aux_codes || '').split(/[,，;；\s]+/)).filter(function (s) { return barcodes.indexOf(s) < 0; });
    var html = '<div class="pi-code"><span class="pi-label">编码</span> ' + (row.sku_id ? copy(row.sku_id, '商品编码') : '未提供') + '</div>';
    var ns = nameSpec(row.name, row.spec);
    html += '<div class="pi-name"><span class="pi-label">商品</span> ' + esc(ns.name || '未提供商品名')
      + (ns.spec ? '<span class="pi-spec-inline">' + esc(ns.spec) + '</span>' : '') + '</div>';
    if (barcodes.length) html += '<div class="pi-barcode">69码 / 条码 ' + barcodes.map(function (c) { return copy(c, '条码'); }).join(' · ') + '</div>';
    if (aux.length) html += '<div class="pi-barcode">辅助码 ' + aux.map(function (c) { return copy(c, '辅助码'); }).join(' · ') + '</div>';
    if (!barcodes.length && !aux.length) html += '<div class="pi-barcode">69码 / 辅助码：未提供</div>';
    html += '<div class="pi-erp">采购 ' + num(row.ordered) + ' / 入库 ' + num(row.erp_received) + '</div>';
    if (row.extra) html += '<span class="tag-new">本轮计划外商品</span>';
    return html;
  }
  function data(payload) {
    if (payload && payload.receiving_round) return payload.receiving_round;
    return {rows:[],progress:{plan:null,recv:0},warnings:['本轮数量尚未加载，请刷新；旧版服务端请同步升级。']};
  }
  // Presentation only: never infer quantities or turn unknown counts into zero.
  // Extra items take priority even when a legacy record has an unknown target.
  // Material standard palette: Green 200 / Yellow 300 / Red 200 / Grey 300.
  function appearance(row) {
    if (row.extra) return {kind:'extra',label:'采购单外',fill:'#e0e0e0',ink:'#374151',accent:'#616161'};
    if (row.plan == null) return {kind:'unknown',label:'待核实',fill:'#e0e0e0',ink:'#374151',accent:'#616161'};
    if (row.done) return {kind:'complete',label:'收齐',fill:'#a5d6a7',ink:'#14532d',accent:'#2e7d32'};
    return {kind:'difference',label:'少收 / 多收',fill:'#fff176',ink:'#713f12',accent:'#f9a825'};
  }
  // 四色状态：已齐 done（绿）/ 短收 short（橙）/ 超收 over（红）/ 未收 none（灰，含待核实）。
  function status(row) {
    if (row.done) return 'done';
    if (row.over || row.extra) return 'over';
    if (row.plan == null) return 'none';
    return Number(row.recv) > 0 ? 'short' : 'none';
  }
  function body(round, reportMode) {
    return (round.rows || []).map(function (r) {
      var cls = r.done ? 'pi-done' : r.over ? 'pi-over' : r.recv > 0 ? 'pi-part' : '';
      if (reportMode) cls += ' report-row report-' + appearance(r).kind;
      return '<tr class="' + cls + '" data-poi="' + esc(r.poi_id) + '"'
        + (r.original_index != null ? ' data-idx="' + esc(r.original_index) + '"' : '') + '>'
        + '<td class="pi-product">' + product(r, reportMode) + '</td>'
        + '<td class="pi-plan" data-label="本轮应收">' + num(r.plan) + '</td>'
        + '<td class="pi-recv" data-label="本轮已收">' + num(r.recv) + '</td>'
        + '<td class="pi-mark" data-label="差额"><span class="round-mark ' + (r.done ? 'done' : r.over ? 'over' : '') + ' st-' + status(r) + '">' + esc(r.reason || '待核实') + '</span></td></tr>';
    }).join('');
  }
  function notes(round) { return (round.warnings || []).map(function (s) { return '<div class="round-warning" role="status">' + esc(s) + '</div>'; }).join(''); }
  function progress(round) { var p = round.progress || {}; return '本轮已收 ' + num(p.recv) + ' / 应收 ' + num(p.plan) + '（所有物流单合计）'; }
  function report(round) {
    return '<div class="round-report"><p class="round-progress">' + esc(progress(round)) + '</p>' + notes(round)
      + '<div class="table-wrap"><table class="tbl round-table"><thead><tr><th>商品信息</th><th>本轮应收</th><th>本轮已收</th><th>差额</th></tr></thead><tbody>'
      + body(round, true) + '</tbody></table></div></div>';
  }
  window.ReceivingRoundUI = {data:data,body:body,notes:notes,report:report,progress:progress,num:num,appearance:appearance,status:status,product:product,nameSpec:nameSpec,fullName:fullName};
  // 其他脚本（当前物流单表格、配对表、差异单、各收货页）共用同一套去重规则
  window.ProductText = {nameSpec:nameSpec, fullName:fullName, key:textKey};
}());

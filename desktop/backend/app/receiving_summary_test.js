// node desktop/backend/app/receiving_summary_test.js
// 4.1.8：无采购单匹配 / 暂存对单 / 结单对单 / 联合收货汇总用同一套对单组件，和有采购单收货一致。
'use strict';
const fs = require('fs'), path = require('path'), assert = require('assert');
global.window = global;
require('./web/receiving-round.js');
require('./web/receiving-summary.js');
const RS = window.ReceivingSummary, RR = window.ReceivingRoundUI;

const linked = [
  {sku_id: 'A', name: '散粉', spec: '4#', plan: 10, recv: 10, kind: 'good', reason: '收齐'},
  {sku_id: 'B', name: '粉底', plan: 8, recv: 5, kind: 'diff'},
  {sku_id: 'C', name: '眉笔', plan: 2, recv: 3, kind: 'diff'},
  {sku_id: 'D', name: '赠品', plan: null, recv: 1, kind: 'extra'},
  {sku_id: 'E', name: '口红', plan: 4, recv: 0, kind: 'diff'}
];
const rows = RS.rows(linked, true);
assert.deepStrictEqual(rows.map(r => RR.status(r)), ['done', 'short', 'over', 'over', 'none']);
assert.deepStrictEqual(rows.map(r => r.reason), ['收齐', '少 3', '多 1', '采购单外 1', '少 4']);

const html = RS.panel(linked, {linked: true});
// 和有采购单收货同一套：进度卡 + round-table，商品行就是 ReceivingRoundUI.body 的输出
assert.ok(html.includes('progress-card') && html.includes('pc-legend') && html.includes('round-table'), html);
assert.ok(html.includes(RR.body({rows: rows}, false)), '商品行必须和有采购单收货工作区同一份标记');
assert.ok(/<b>19<\/b><span>\/ 24 件<\/span><em>79%<\/em>/.test(html), '进度：实收 19 / 应收 24（采购单外不计入应收）');
assert.ok(/已齐<\/span><b class="c-ok">1<.*短收<\/span><b class="c-warn">1<.*超收<\/span><b class="c-danger">2<.*未收<\/span><b class="c-none">1</.test(html));

// 未关联采购单：只显示实收，不虚构应收或差异
const un = RS.panel([{sku_id: 'A', name: '散粉', recv: 3}, {sku_id: 'B', name: '粉底', qty: 2}], {linked: false, batches: 2});
assert.ok(!un.includes('应收') && !un.includes('差额') && !un.includes('pc-bar'), un);
assert.ok(un.includes('<b>5</b>') && un.includes('rs-recv-only'));

// 待核实的应收不当成 0
const unk = RS.panel([{sku_id: 'A', name: 'x', plan: null, recv: 2, kind: 'unknown', reason: '待核实'}], {linked: true});
assert.ok(unk.includes('/ 待核实 件'), unk);

// 附加列（联合收货的采购来源）和商品名下的说明
const joint = RS.panel([{sku_id: 'A', name: '<b>x</b>', plan: 3, recv: 3, sources: [{po_id: 'P1', plan: 3}], note: '原实收商品：y'}],
  {linked: true, extraCol: {title: '采购来源', cell: r => r.sources.map(p => p.po_id).join('')}});
assert.ok(joint.includes('<th>采购来源</th>') && joint.includes('>P1</td>') && joint.includes('原实收商品：y'));
assert.ok(!joint.includes('<b>x</b>'), '商品名要转义');

// 三个页面都加载了公共组件，对单区都走 ReceivingSummary
for (const page of ['unplanned.html', 'express.html', 'joint.html']) {
  const s = fs.readFileSync(path.join(__dirname, 'web', page), 'utf8');
  assert.ok(s.indexOf('/receiving-round.js') > 0 && s.indexOf('/receiving-round.js') < s.indexOf('/receiving-summary.js'), page);
}
for (const f of ['unplanned-report.js', 'unplanned-link.js', 'joint.js']) {
  assert.ok(fs.readFileSync(path.join(__dirname, 'web', f), 'utf8').includes('ReceivingSummary.') || fs.readFileSync(path.join(__dirname, 'web', f), 'utf8').includes('RS.panel'), f);
}
console.log('receiving-summary ok');

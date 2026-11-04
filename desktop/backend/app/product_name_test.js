// node desktop/backend/app/product_name_test.js
// 4.1.10：商品名称和规格（款式名 / 商品简称）内容重复时只显示一次；当前物流单号表格的商品列不再被挤成逐字换行，
// 也不再显示「已在其他物流单号收满」那一段。
'use strict';
const fs = require('fs'), path = require('path'), assert = require('assert');
global.window = global;
require('./web/receiving-round.js');
const PT = window.ProductText, RR = window.ReceivingRoundUI;
assert.ok(PT && typeof PT.nameSpec === 'function', 'receiving-round.js 要导出 window.ProductText');

// 1. 去重规则
const A = '发之食谱洗发水530ml苹果姜（修护滋养）', F = '发之食谱洗发水530ml无花果（清爽蓬松）';
assert.deepStrictEqual(PT.nameSpec(A, A), {name: A, spec: ''}, '规格和名称完全相同');
assert.deepStrictEqual(PT.nameSpec(F, '发之食谱洗发水530ml无花果'), {name: F, spec: ''}, '规格是名称的一部分（截图里的计划外商品）');
assert.deepStrictEqual(PT.nameSpec(A, ' 发之食谱洗发水530ML苹果姜(修护滋养) '), {name: A, spec: ''}, '忽略空白、全半角括号和大小写');
assert.deepStrictEqual(PT.nameSpec('洗发水', '洗发水 530ml 苹果姜'), {name: '洗发水 530ml 苹果姜', spec: ''}, '规格包含整个名称时用规格当名称');
assert.deepStrictEqual(PT.nameSpec('散粉', '4#'), {name: '散粉', spec: '4#'}, '规格不同才作为副标题');
assert.deepStrictEqual(PT.nameSpec('散粉', ''), {name: '散粉', spec: ''});
assert.deepStrictEqual(PT.nameSpec('', '4#'), {name: '4#', spec: ''});
assert.deepStrictEqual(PT.nameSpec(null, undefined), {name: '', spec: ''});
assert.strictEqual(PT.fullName({name: '散粉', spec: '4#'}), '散粉 4#');
assert.strictEqual(PT.fullName({name: A, spec: A}), A);

// 2. 左侧采购单明细（紧凑行）和对单汇总表：名字不再叠两遍，悬停显示全名
const extraRow = {sku_id: 'X', name: F, spec: '发之食谱洗发水530ml无花果', recv: 6, extra: true};
const compact = RR.body({rows: [extraRow]}, false);
assert.strictEqual(compact.split('发之食谱洗发水530ml无花果').length - 1, 2, '名称正文一次 + title 一次：' + compact);
assert.ok(!compact.includes('pi-spec-inline'), compact);
assert.ok(compact.includes('title="' + F + '"'), compact);
const report = RR.body({rows: [extraRow]}, true);
assert.strictEqual(report.split('发之食谱洗发水530ml无花果').length - 1, 1, '对单汇总里名称只出现一次：' + report);
const diffSpec = RR.body({rows: [{sku_id: 'Y', name: '散粉', spec: '4#', plan: 2, recv: 1}]}, false);
assert.ok(diffSpec.includes('<span class="pi-spec-inline">4#</span>'), diffSpec);

// 3. 当前物流单号表格：商品 | 数量 | 操作，编码 / 条码进商品单元格，不再有固定百分比列宽
const web = path.join(__dirname, 'web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const head = html.slice(html.indexOf('<table class="tbl ln-table">'), html.indexOf('id="line-body"'));
assert.ok(head.includes('ln-qty-col') && head.includes('ln-act-col'), head);
assert.strictEqual((head.match(/<th\b/g) || []).length, 3, '当前物流单号表格只剩 3 列：' + head);
assert.ok(!/style="width:\d+%/.test(head), '不能再用百分比列宽：' + head);

const app = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
const render = app.slice(app.indexOf('function renderLines()'), app.indexOf('tb.innerHTML = html;', app.indexOf('function renderLines()')));
assert.ok(render.includes('!l.done_elsewhere'), '旧数据里的 done_elsewhere 行也要过滤掉');
assert.ok(!render.includes('已在其他物流单号收满'), '「已在其他物流单号收满」那一段整个去掉');
assert.ok(!render.includes('tr-done-elsewhere'), render);
assert.ok(render.includes('lineProduct(l, bad)') && render.includes('colspan="3"'), render);
assert.ok(/function lineProduct[\s\S]*?nsOf\(l\)|function lineNameSpec[\s\S]*?nsOf\(l\)/.test(app), 'lineProduct 要走名称 / 规格去重');

const css = fs.readFileSync(path.join(web, 'style.css'), 'utf8');
assert.ok(/\.ln-product\{min-width:200px\}/.test(css), '商品列最小 200px');
const nameRule = (css.match(/\.ln-name\{[^}]*\}/) || [''])[0];
assert.ok(nameRule.includes('-webkit-line-clamp:2') && nameRule.includes('overflow:hidden'), '长名称最多两行：' + nameRule);
assert.ok(!nameRule.includes('break-all'), '不能逐字断行：' + nameRule);
assert.ok(!/tr-done-elsewhere|\.de-tag|\.de-qty/.test(css), '收满沉底行的样式已删除');
assert.ok(/\.ln-table th\.ln-qty-col[^{]*\{width:1%;white-space:nowrap\}/.test(css), '数量 / 操作列按内容收紧');

// 4. 其他用到规格的地方都走同一套去重
for (const f of ['purchase-pairs.js', 'difference-report.js', 'unplanned.js']) {
  const src = fs.readFileSync(path.join(web, f), 'utf8');
  assert.ok(src.includes('ProductText.nameSpec'), f + ' 要用 ProductText.nameSpec');
}
console.log('product_name_test: ok');

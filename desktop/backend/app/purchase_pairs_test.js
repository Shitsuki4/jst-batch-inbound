// node desktop/backend/app/purchase_pairs_test.js
// Regression: receipt #170 (PO 1338121) — every short item has a same-brand,
// same-volume substitute with the exact missing quantity and must sit beside it.
'use strict';
global.window = {};
require('./web/purchase-pairs.js');
const P = window.PurchasePairs, assert = require('assert');
const rows = [
  {sku_id:'fzsp001',name:'发之食谱洗发水530ml无花果',plan:6,recv:3},
  {sku_id:'OLY195',name:'欧莱雅洗发水玻尿酸空气感440ml（新款）',plan:4,recv:0},
  {sku_id:'OLY46',name:'欧莱雅生姜洗发水440ml',plan:4,recv:2},
  {sku_id:'OLY307',name:'欧莱雅洗发水生姜660ml（强韧丰盈）',plan:6,recv:0},
  {sku_id:'DF154',name:'多芬小金盖发膜密集修护260g',plan:10,recv:9},
  {sku_id:'PT36',name:'潘婷洗发水3分钟茉莉山茶香470g（绿）',spec:'潘婷洗发水3分钟茉莉山茶香470g(绿)',plan:3,recv:2},
  {sku_id:'DF021',name:'多芬发膜小金盖水润修护260g',recv:1,extra:true},
  {sku_id:'PT71',name:'潘婷洗发水净澈蓬松白桃乌龙香470g（粉色）',recv:1,extra:true},
  {sku_id:'OLY003',name:'欧莱雅洗发水玻尿酸空气感660ml（新款）',recv:6,extra:true},
  {sku_id:'fzsp004',name:'发之食谱护发素530ml无花果（清爽蓬松）',spec:'发之食谱护发素530ml无花果',recv:3,extra:true},
  {sku_id:'OLY47',name:'欧莱雅护发素生姜440ml（强韧柔顺）',spec:'欧莱雅生姜润发乳440ml',recv:2,extra:true},
  {sku_id:'OLY196',name:'欧莱雅护发素玻尿酸空气感440ml（新款）',recv:4,extra:true},
];
const expected = 'DF154>DF021 OLY195>OLY196 OLY307>OLY003 OLY46>OLY47 PT36>PT71 fzsp001>fzsp004';
for (let k = 0; k < 50; k++) {
  const shuffled = k ? [...rows].sort(() => Math.random() - .5) : rows;
  const got = P.model(shuffled).pairs.filter(g => g.left.length && g.right.length)
    .map(g => g.left[0].sku_id + '>' + g.right.map(r => r.sku_id).join('+')).sort().join(' ');
  assert.strictEqual(got, expected);
}
const sim = (a, b, s1, s2) => P.similarity({sku_id:s1,name:a}, {sku_id:s2,name:b});
assert.strictEqual(sim('欧莱雅洗发水生姜440ml','欧莱雅沐浴露生姜440ml','OLY1','OLY2'), 0, 'different family');
assert.strictEqual(sim('欧莱雅洗发水生姜440ml','欧莱雅洗发水生姜660ml','OLY1','OLY2'), 0, 'different volume');
assert.strictEqual(sim('欧莱雅洗发水生姜440ml','潘婷洗发水生姜440ml','OLY1','PT2'), 0, 'different brand');
assert.strictEqual(sim('多芬发膜260g','潘婷发膜260g','DF1','PT2'), 0, 'different brand');
const pairsOf = list => P.model(list).pairs.map(g => (g.left.map(r => r.sku_id).join('+') || '-') + '>' + (g.right.map(r => r.sku_id).join('+') || '-')).join(' ');
// Quantities not exact: each short row keeps its own best counterpart instead of a mixed combo.
assert.strictEqual(pairsOf([
  {sku_id:'OLY195',name:'欧莱雅洗发水玻尿酸空气感440ml',plan:4,recv:0},
  {sku_id:'OLY46',name:'欧莱雅生姜洗发水440ml',plan:4,recv:2},
  {sku_id:'OLY196',name:'欧莱雅护发素玻尿酸空气感440ml',recv:3,extra:true},
  {sku_id:'OLY47',name:'欧莱雅护发素生姜440ml',recv:1,extra:true}]), 'OLY195>OLY196 OLY46>OLY47');
// One manufacturer (P&G GS1 6903148) or one code family (DF) is not one brand.
assert.strictEqual(P.similarity({sku_id:'H2768',name:'潘婷洗发水3分钟奇迹洗发水470ml（紫）',sku_code:'6903148000001'},{sku_id:'hfs41',name:'海飞丝洗发水去屑止痒470ml（专研）',sku_code:'6903148000002'}), 0);
assert.strictEqual(P.similarity({sku_id:'DF145',name:'清扬洗发水男士清爽控油500g'},{sku_id:'DF196',name:'力士洗发水赋活炫亮 500g'}), 0);
// Packaging / origin noise before the brand is ignored.
assert.ok(P.similarity({sku_id:'X1',name:'盒装-蒂佳婷面膜蓝pro新款（一盒5片）'},{sku_id:'X2',name:'蒂佳婷面膜绿pro新款（一盒5片）'}) >= .58);
// Leading tags do not hide the brand.
assert.strictEqual(pairsOf([
  {sku_id:'OLY1',name:'【新款】欧莱雅洗发水生姜440ml',plan:2,recv:0},
  {sku_id:'OLY9',name:'欧莱雅护发素生姜440ml',recv:2,extra:true}]), 'OLY1>OLY9');
// One short row beside several colour variants (merged left cell).
const mask = [
  {sku_id:'DF1',name:'多芬发膜260g（蓝色）',plan:7,recv:0},
  {sku_id:'DF2',name:'多芬发膜260g（粉色）',recv:3,extra:true},
  {sku_id:'DF3',name:'多芬发膜260g（黄色）',recv:4,extra:true}];
assert.strictEqual(pairsOf(mask), 'DF1>DF2+DF3');
assert.ok(/rowspan="2" class="pair-cell diff"/.test(P.table(mask, true)), 'left cell spans both variants');
// Overshooting total still sits together.
assert.strictEqual(pairsOf([mask[0], mask[1], {...mask[2], recv:5}]), 'DF1>DF2+DF3');
// Chinese / English names of the same brand pair; different brands still never do.
const simN = (a, b) => P.similarity({sku_id:'A1',name:a}, {sku_id:'B2',name:b});
assert.ok(simN('LOREAL洗发水生姜440ml','欧莱雅护发素生姜440ml') >= .58);
assert.ok(simN('Olay沐浴露水感透光730ml','玉兰油OLAY沐浴露烟酰胺730ml') >= .58);
assert.ok(simN('LG沐浴露紫500g','安宝笛沐浴露红500g') >= .58);
assert.strictEqual(simN('Dove洗发水500g','力士洗发水500g'), 0);
// Brand unconfirmed: exact quantity + same type + same volume, unique on both sides -> paired.
const guessPairs = list => P.model(list).pairs.map(g => (g.left.map(r => r.sku_id).join('+') || '-') + '>' + (g.right.map(r => r.sku_id + (r.guess ? '?' : '')).join('+') || '-')).join(' ');
assert.strictEqual(guessPairs([{sku_id:'A1',name:'溪木源面霜50g滋润版',plan:5,recv:2},{sku_id:'B9',name:'山茶花面霜50g',recv:3,extra:true}]), 'A1>B9?');
assert.strictEqual(guessPairs([{sku_id:'A1',name:'溪木源面霜50g',plan:5,recv:2},{sku_id:'B9',name:'山茶花面霜50g',recv:2,extra:true}]), 'A1>- ->B9', 'quantity must match');
assert.strictEqual(guessPairs([{sku_id:'DF145',name:'清扬洗发水控油500g',plan:4,recv:0},{sku_id:'DF196',name:'力士洗发水炫亮500g',recv:4,extra:true}]), 'DF145>- ->DF196', 'known different brands');
assert.strictEqual(guessPairs([{sku_id:'A1',name:'甲洗发水500ml',plan:4,recv:0},{sku_id:'B1',name:'乙洗发水500ml',recv:4,extra:true},{sku_id:'C1',name:'丙洗发水500ml',recv:4,extra:true}]), 'A1>- ->B1 ->C1', 'tie stays unpaired');
assert.strictEqual(guessPairs([{sku_id:'H10',name:'甲洗发水500ml',plan:4,recv:0},{sku_id:'H11',name:'乙洗发水500ml',recv:4,extra:true},{sku_id:'K90',name:'丙洗发水500ml',recv:4,extra:true}]), 'H10>H11? ->K90', 'adjacent code breaks tie');
console.log('purchase-pairs ok');

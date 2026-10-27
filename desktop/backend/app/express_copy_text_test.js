// 快递单登记导出「明细」与无采购单登记「复制明细文字」格式一致：用同一份期望文本校验前端 detailText。
// 对应后端 express_test.py：['123','2026/10/17 20:46:53','商品编码 商品名称 数量\nA 商品A 7\nB 商品B 8']
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const ctx={window:{},document:{fonts:{ready:Promise.resolve()}}};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,'web','unplanned-report.js'),'utf8'),ctx);
const R=ctx.window.UnplannedReport;
// 一张物流单的明细行（同 SKU 已在后端按物流单累加）
const text=R.detailText({rows:[{sku_id:'A',name:'商品A',recv:7},{sku_id:'Z',name:'零',recv:0},{sku_id:'B',name:'商品B',recv:8}]});
assert.strictEqual(text,'商品编码 商品名称 数量\nA 商品A 7\nB 商品B 8');
assert.strictEqual(R.detailText({rows:[{sku_id:'C',name:'名称  带\u3000空白',recv:1}]}),'商品编码 商品名称 数量\nC 名称 带 空白 1');
// 快递单页面复用 unplanned.js：接口切到 /api/express，左栏为当前物流单号明细，复制文字取本单 detail。
const page=fs.readFileSync(path.join(__dirname,'web','unplanned.js'),'utf8'),html=fs.readFileSync(path.join(__dirname,'web','express.html'),'utf8');
assert.ok(page.includes("API=EXPRESS?'/api/express':'/api/unplanned'")&&!page.includes("'/api/unplanned/"));
assert.ok(page.includes('const text=EXPRESS?waybillText():R.detailText(state);'));
assert.ok(html.includes('data-desk="express"')&&html.includes('当前物流单号收货明细')&&!html.includes('后补采购单')&&!html.includes('id="po"'));
console.log('express-copy-text ok');

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
// 快递单登记：复制全部明细时商品名称去掉全部空白（半角 / 全角空格、制表符、NBSP），列之间仍是一个半角空格；无采购单（上一行）不变。
assert.strictEqual(R.detailText({rows:[{sku_id:'C',name:' 名称  带\u3000空\t白\u00a0 ',recv:1},{sku_id:'D 1',name:'AKF 散粉',recv:2}]},{stripNames:true}),'商品编码 商品名称 数量\nC 名称带空白 1\nD 1 AKF散粉 2');
assert.strictEqual(R.stripName('a b\u3000c\td\u00a0e'),'abcde');
// 快递单页面复用 unplanned.js：接口切到 /api/express；左栏与无采购单一样是批次汇总，复制本单文字取后端本单 detail（名称已去空白）。
const page=fs.readFileSync(path.join(__dirname,'web','unplanned.js'),'utf8'),html=fs.readFileSync(path.join(__dirname,'web','express.html'),'utf8');
assert.ok(page.includes("API=EXPRESS?'/api/express':'/api/unplanned'")&&!page.includes("'/api/unplanned/"));
assert.ok(page.includes('await copyText(EXPRESS?waybillText():R.detailText(state));')&&page.includes('R.detailText(state,{stripNames:true})'));
assert.ok(page.includes("EXPRESS?'':'；采购查询独立")&&page.includes("EXPRESS?'快递单登记'"));
assert.ok(html.includes('data-desk="express"')&&html.includes('批次商品汇总 · 全部物流实收')&&html.includes('id="details"')&&html.includes('id="desk-batch-search"')&&html.includes('id="desk-batches-title"')&&!html.includes('后补采购单')&&!html.includes('id="po"'));
console.log('express-copy-text ok');

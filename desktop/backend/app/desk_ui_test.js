// 4.1.2 收货页公共界面纯逻辑：重复扫码查找 / 提示文字、删除整批输入确认、录像状态显示（不编造数值）。
const assert=require('assert'),path=require('path');
const U=require(path.join(__dirname,'web','desk-ui.js'));
// 重复扫码：忽略空白与大小写
const batches=[{id:1,tracking_no:'SF1234567890',first_scan_at:'2026-10-17 09:41:22'},{id:2,tracking_no:'yt 0001',first_scan_at:''}];
assert.strictEqual(U.findDuplicate(batches,' sf1234567890 ').id,1);
assert.strictEqual(U.findDuplicate(batches,'YT0001').id,2);
assert.strictEqual(U.findDuplicate(batches,'SF999'),null);
assert.strictEqual(U.findDuplicate(batches,''),null);
assert.strictEqual(U.findDuplicate(null,'x'),null);
assert.strictEqual(U.dupToast(batches[0]),'已扫过 · 09:41');
assert.strictEqual(U.dupToast(batches[1]),'已扫过');
assert.strictEqual(U.hm('2026/10/17 7:05:00'),'07:05');
// 删除整批：必须输入完整批次号或准确的物流单数
assert.strictEqual(U.deleteConfirmOk('','WR20261017-01',5),false);
assert.strictEqual(U.deleteConfirmOk('WR20261017','WR20261017-01',5),false);
assert.strictEqual(U.deleteConfirmOk(' WR20261017-01 ','WR20261017-01',5),true);
assert.strictEqual(U.deleteConfirmOk('5','WR20261017-01',5),true);
assert.strictEqual(U.deleteConfirmOk('6','WR20261017-01',5),false);
assert.strictEqual(U.deleteConfirmOk('05','WR20261017-01',5),true);
assert.strictEqual(U.deleteConfirmOk('0','WR20261017-01',0),true);
assert.strictEqual(U.deleteConfirmOk('5',null,null),false);
// 录像状态：只显示载荷里有的内容
assert.strictEqual(U.recView(undefined).cls,'off');
assert.strictEqual(U.recView({enabled:false}).text,'录像未启用');
let v=U.recView({enabled:true,ready:true,active:true,tracking:'SF1',status:'录制中'});
assert.strictEqual(v.cls,'on');assert.strictEqual(v.text,'● 录制中 · 单号 SF1');assert.ok(!/GB|\d\d:\d\d/.test(v.text),'没有时长 / 大小数据时不显示');
v=U.recView({enabled:true,active:true,tracking:'SF1'},133000);assert.strictEqual(v.text,'● 录制中 02:13 · 单号 SF1');
v=U.recView({enabled:true,active:true,elapsedMs:61000,segmentBytes:858993459});assert.strictEqual(v.text,'● 录制中 01:01 · 本段 0.8GB');
v=U.recView({enabled:true,ready:true,active:false,status:'待扫物流单'});assert.strictEqual(v.cls,'stop');assert.strictEqual(v.text,'● 未录制');
assert.strictEqual(U.recView({enabled:true,active:true,warning:'磁盘空间不足'}).cls,'warn');
assert.strictEqual(U.mmss(3725000),'1:02:05');
console.log('desk_ui_test ok');

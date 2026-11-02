'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const web = path.join(__dirname, 'web');
const app = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
function section(start, end) { return app.slice(app.indexOf(start), app.indexOf(end)); }

(async () => {
  const sent = [];
  let transportError = null;
  const response = {ok:true, json:async()=>({ok:true,dry:true}), clone(){return this;}};
  const context = vm.createContext({
    URL, console, document:{addEventListener(){},querySelector(){return null;}},
    location:{href:'http://127.0.0.1:61561/'},
  });
  context.window = context;
  context.parent = context;
  context.addEventListener = () => {};
  context.fetch = async (url, opts) => {
    sent.push({url, opts});
    if (transportError) throw transportError;
    return response;
  };
  context.__nativeVideo = {enabled:true,ready:false,active:false,key:''};
  vm.runInContext(fs.readFileSync(path.join(web, 'native-receipt.js'), 'utf8'), context);
  const body = {code:'6900000000001',qty:0,sid:7,batch_id:11};
  const options = {method:'POST',body:JSON.stringify(body)};
  await assert.rejects(context.fetch('/api/item/scan', options), error =>
    error.code === 'NATIVE_RECORDING_BLOCKED' && error.requestSent === false);
  assert.strictEqual(sent.length, 0, 'a recording block must never reach the backend');

  vm.runInContext('var stateEpoch=0,writesPending=0;' + section('  function api(', '  function esc('), context);
  let result = await context.api('/api/item/scan', {method:'POST',body});
  assert.strictEqual(result.code, 'NATIVE_RECORDING_BLOCKED');
  assert.strictEqual(result.ok, false);
  assert.match(result.msg, /录像尚未就绪/);
  assert.strictEqual(context.writesPending, 0);
  assert.strictEqual(sent.length, 0);

  context.__nativeVideo = {enabled:true,ready:true,active:true,key:'普通:7:11'};
  assert.strictEqual((await context.api('/api/item/scan', {method:'POST',body})).ok, true);
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(context.writesPending, 0);

  context.NativeDesk = {available:true,call:async()=>{throw Error('请先保存录像磁盘设置');}};
  result = await context.api('/api/tracking/scan', {method:'POST',body:{tracking_no:'EXISTING',sid:7}});
  assert.strictEqual(result.code, 'NATIVE_RECORDING_BLOCKED');
  assert.match(result.msg, /磁盘设置/);
  assert.strictEqual(sent.length, 1, 'failed native validation must not create a tracking batch');
  context.NativeDesk = {available:false};

  context.__nativeVideo = {enabled:false};
  transportError = TypeError('Failed to fetch');
  await assert.rejects(context.api('/api/item/scan', {method:'POST',body}), /Failed to fetch/);
  assert.strictEqual(context.writesPending, 0, 'transport failures must release the write counter');

  const elements = Object.fromEntries(['#pb-name','#pb-code','#pending-qty','#pending-bar'].map(id=>[id,{}]));
  context.$ = selector => elements[selector];
  context.pendingQty = () => 7;
  context.takePending = () => {const p=context.pending;context.pending=null;return p;};
  context.postConfirm = async () => ({ok:false,code:'NATIVE_RECORDING_BLOCKED',msg:'录像尚未就绪'});
  context.handleFail = () => {};
  context.refocus = () => {};
  vm.runInContext(section('  function restorePending(', '  function pendingFocus(') +
    section('  function confirmPendingNow(', '  function cancelPending(') +
    section('  function confirmPendingThen(', '  function clearPending('), context);
  const pending = {op_id:'keep-this-operation-id',code:'6900000000001',label:'SKU-TEST',batch_id:11};
  context.pending = pending;
  await context.confirmPendingNow();
  assert.strictEqual(context.pending, pending);
  assert.strictEqual(elements['#pending-qty'].value, '7');
  assert.strictEqual(elements['#pending-bar'].hidden, false);
  let next = 0;
  await context.confirmPendingThen(() => {next++;});
  assert.strictEqual(next, 0, 'a blocked confirmation must not consume the next scan');
  assert.strictEqual(context.pending.op_id, 'keep-this-operation-id');
  console.log('recording_gate_test: blocked scans, ready scans, validation, real network errors and pending quantities PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});

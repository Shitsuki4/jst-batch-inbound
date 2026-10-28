(function(){
'use strict';
// 快递单登记（express.html）复用本页逻辑：接口 /api/express，不含后补采购单 / 入库区域（对应元素用不挂载的占位节点）。
const EXPRESS=document.body.dataset.desk==='express',SOURCE=EXPRESS?'快递单':'无采购单',API=EXPRESS?'/api/express':'/api/unplanned',stubs={};
const $=id=>document.getElementById(id)||(EXPRESS?(stubs[id]||(stubs[id]=document.createElement(id==='warehouse'?'select':'div'))):null),R=UnplannedReport,e=R.esc;let state=null,bid=Number(new URLSearchParams(location.search).get("bid"))||0,busy=false,editing=null,scanCode='',pending=null,historyOpen=true,historySeq=0,historyKind='purchase';
const QTY_MSG='数量必须是大于0的整数（商品数量不能有小数），且不超过10亿',QTY_MSG0='数量必须是整数（商品数量不能有小数），不能为负数且不超过10亿';
function wholeQty(v,allowZero){const s=String(v??'').trim();if(!/^\d+$/.test(s)||Number(s)>1e9||(!allowZero&&Number(s)===0))throw Error(allowZero?QTY_MSG0:QTY_MSG);return Number(s);}
function guardQtyInput(el,allowZero){if(!el)return;const check=()=>{const s=el.value.trim(),bad=el.validity&&el.validity.badInput;el.setCustomValidity(!bad&&(s===''||/^\d+$/.test(s)&&Number(s)<=1e9&&(allowZero||Number(s)>0))?'':(allowZero?QTY_MSG0:QTY_MSG));};el.addEventListener('input',check);el.addEventListener('change',check);}
function message(s,error=false){$('message').textContent=s;$('message').classList.toggle('error',error);}
async function api(url,body){const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const data=await r.json();if(!r.ok||!data.ok)throw Error(data.msg||'请求失败');return data;}
async function run(fn){if(busy)return message('正在加载或保存，请稍候再试');busy=true;$('new').disabled=true;window.ReceivingDesk?.setBusy(true);try{await fn()}catch(err){message(err.message,true);window.ReceivingDesk?.feedback?.(err.message,'err')}finally{busy=false;$('new').disabled=false;window.ReceivingDesk?.setBusy(false)}}
function body(extra){return {sid:state?.session.id,revision:state?.meta.revision,batch_id:bid,...extra};}
async function action(action,extra={}){return api(API+'/action',body({action,...extra}));}
 $('copy-previous').onclick=()=>run(async()=>{if(pending||document.querySelector('dialog[open]'))throw Error('请先确认或取消当前商品');if(!bid)throw Error('请先扫描目标物流单');const result=await ReceiptCopy.run(action,'本收货批次不再提示');if(result){state=result;bid=result.current_batch;render();await history();message('已从 '+result.copy_source+' 复制 '+R.num(result.copy_total)+' 件；未生成入库单');}else message('已取消复制，未登记数量');$('code').focus();});
async function history(){if(!historyOpen)return;const seq=++historySeq;const d=await api(API+'/history?kind='+historyKind+'&q='+encodeURIComponent($(historyKind==='tracking'?'tracking-query':'query').value));if(!historyOpen||seq!==historySeq)return;$('history').innerHTML=d.rows.map(r=>'<div class="history-row"><button data-open="'+r.id+'">'+e(r.input_no)+'</button><span>'+(EXPRESS?'快递单登记'+(r.note?' · '+e(r.note):'')+'<br>'+e(r.started_at):e(r.po_id||'未关联采购单')+' / '+e(r.outer_po_id||'')+' / '+e(r.supplier||'')+'<br>'+e(r.note)+' · '+e(r.started_at))+'</span><span>'+r.tracking_count+' 单 / '+R.num(r.total)+' 件　'+(r.state==='closed'?'已结单 / 实收只读':r.locked?'已提交 / 查看原单':'可继续收货')+'</span></div>').join('')||'<p>暂无符合条件的记录</p>';}
async function load(id){if(pending||$('editor').open)throw Error('请先确认或取消当前商品，再打开 / 刷新批次');state=await api(API+'/state?sid='+id);render();window.dispatchEvent(new CustomEvent('native-receipt',{detail:{kind:'stop'}}));await history();}
function render(){window.purchaseUnplannedSid=state?.session.id;if(!state)return;renderContext();$('work').hidden=false;$('title').textContent=state.session.input_no+(EXPRESS?' · 快递单登记批次':' · 无采购单收货批次');$('meta').textContent=state.batches.length+' 个物流单 / '+state.sku_count+' 个 SKU / 实收 '+R.num(state.total)+' 件';$('note').value=state.meta.note;$('po').value=state.session.po_id||'';$('export').href=API+'/export?sid='+state.session.id;const list=state.batches;if(!list.some(b=>b.id===bid))bid=list.length?list[list.length-1].id:0;$('batches').innerHTML=list.map(b=>'<option value="'+b.id+'">'+e(b.tracking_no)+' · '+R.num(b.total)+' 件</option>').join('');$('batches').value=bid||'';document.querySelectorAll('[data-edit]').forEach(b=>b.disabled=state.locked);$('inbound').disabled=!state.can_create;$('recheck').disabled=state.locked||!state.linked;$('warehouses').disabled=state.locked||!state.linked;
let status=!state.linked?'未关联采购单：只登记实收，无应收和差异。':!state.checked?'实收明细已变化或上次核对未完成，请重新核对采购单。':state.can_create?'SKU 和数量全部一致，可预览并生成待入库单。':'存在差异或入库提醒，请核对下方明细。';if(state.locked)status='已提交或已生成待入库单，整批锁定。请查看/继续原单，不可重复创建。';$('check-status').innerHTML='<div class="status '+(state.can_create?'good':state.linked?'diff':'')+'">'+e(status)+(state.problems.length?'<br>'+state.problems.map(e).join('<br>'):'')+'</div>';
$('jobs').innerHTML=state.jobs.map(j=>'<p>入库记录 #'+j.id+'　单号：'+e(j.io_id||'结果待核实')+'　'+e(j.status==='complete'?'已生成并核对数量（未自动确认入库）':j.status)+' '+e(j.error||'')+(j.status!=='complete'&&j.status!=='rejected'?' <button data-resume="'+j.id+'">核对 / 继续原单</button>':'')+'</p>').join('');renderLines();renderReport();}
function renderLines(){window.ReceivingDesk?.render(state,bid);const b=state.batches.find(b=>b.id===bid);$('lines').innerHTML=b?'<table><thead><tr><th>商品 / SKU</th><th>规格</th><th>实收</th><th>操作</th></tr></thead><tbody>'+b.lines.map(r=>'<tr><td>'+e(r.name)+(window.BarcodeLearn?BarcodeLearn.tag(r):'')+'<div class="sku">'+e(r.sku_id)+'</div></td><td>'+e(r.spec)+'</td><td class="num">'+R.num(r.qty)+'</td><td><button data-line="'+r.id+'" '+(state.locked?'disabled':'')+'>修改</button> <button data-delete="'+r.id+'" '+(state.locked?'disabled':'')+'>删除</button></td></tr>').join('')+'</tbody></table>':'<p>请先扫描物流单号。</p>';ReceiptLifecycle.paint(state);}
function currentWaybill(){return (state&&state.waybills||[]).find(w=>w.id===bid)||null;}
function waybillText(){const w=currentWaybill();if(!w)throw Error('请先扫描或选择物流单号');if(!w.detail)throw Error('当前物流单号还没有实收商品，无可复制的明细');return w.detail;}
function renderReport(){window.purchaseUnplannedSid=EXPRESS?null:state?.session.id;$('report').innerHTML=R.render(state,$('details').checked);}
async function mutate(a,extra={}){if(pending&&a!=='scan')throw Error('请先确认或取消当前商品数量');const data=await action(a,extra);if(data.selection_required){return false;}state=data;if(data.current_batch)bid=data.current_batch;render();$('candidates').innerHTML='';await history();return true;}
function openEditor(row){if(pending)throw Error('请先确认或取消当前商品数量');if(!bid)throw Error('请先选择物流单');editing=row||null;const f=$('edit-form');$('editor-title').textContent=row?'修改实收商品':'手动登记商品';for(const k of ['sku_id','name','sku_code','spec','qty'])f.elements[k].value=row?row[k]:(k==='qty'?1:'');$('editor').showModal();}
$('new').onclick=()=>run(async()=>{if(pending||$('editor').open)throw Error('请先确认或取消当前商品');state=await action('new',{note:$('new-note').value,tracking_no:$('first-tracking').value,owner_co_id:$('desk-owner').value,auth_co_id:$('desk-auth').value});bid=state.current_batch||0;render();await history();message('已新建批次，请扫描物流单号');$('code').focus()});$('search').onclick=()=>{historyOpen=true;run(history)};$('query').onkeydown=ev=>{if(ev.key==='Enter')$('search').click()};$('history').onclick=ev=>{const b=ev.target.closest('[data-open]');if(b)run(async()=>{if(pending||$('editor').open)throw Error('请先确认或取消当前商品');bid=0;await load(Number(b.dataset.open));$('warehouse').innerHTML='<option value="">请选择入库仓库</option>';message('已打开历史收货批次')})};$('reload').onclick=()=>run(()=>load(state.session.id));$('save-note').onclick=()=>run(async()=>{await mutate('note',{note:$('note').value});message('备注已保存')});$('scan-tracking').onclick=()=>run(async()=>{if(pending)throw Error('请先确认或取消商品数量');const trackingNo=$('tracking').value.trim();await mutate('tracking',{tracking_no:$('tracking').value});$('tracking').value='';message('已进入物流单，可扫描商品');window.ReceivingDesk?.feedback?.('物流单号 '+trackingNo+'，请扫商品','tracking');ReceivingDesk.setMode('item',true,true)});$('tracking').onkeydown=ev=>{if(ev.key==='Enter')$('scan-tracking').click()};$('batches').onchange=()=>{if(busy||pending||$('editor').open){$('batches').value=bid;return message('请先确认或取消商品数量',true);}bid=Number($('batches').value);renderLines();if(EXPRESS)renderReport();if(state.session.state==='closed'||state.locked)return;window.dispatchEvent(new CustomEvent('native-receipt',{detail:{kind:'tracking',tracking_no:state.batches.find(b=>b.id===bid).tracking_no,key:SOURCE+':'+state.session.id+':'+bid}}))};$('rename').onclick=()=>run(async()=>{const b=state.batches.find(b=>b.id===bid);if(!b)throw Error('请选择物流单');const no=await JstDialog.prompt('修改物流单号',b.tracking_no);if(no!==null){const from=b.tracking_no,key=SOURCE+':'+state.session.id+':'+bid;await mutate('rename',{tracking_no:no});message('物流单号已修改');await window.NativeReceiptLink?.afterRename({key,from,to:state.batches.find(x=>x.id===bid)?.tracking_no??no});}});$('delete-batch').onclick=()=>run(async()=>{if(await JstDialog.confirm('删除此物流单及其全部实收明细？',{danger:true,ok:'删除'})){await mutate('delete_batch');message('已删除物流单')}});
function choose(row){$('step').value=1;pending=row;$('pending').hidden=false;$('pending-name').textContent=row.name+' · '+row.sku_id+' · 请确认本次数量';$('candidates').innerHTML='';$('step').focus();$('step').select();}
function clearPending(){pending=null;$('pending').hidden=true;$('candidates').innerHTML='';}
$('scan').onclick=()=>run(async()=>{if(!bid)throw Error('请先扫描物流单号');if(pending||ProductPicker.isOpen())throw Error('请先确认或取消当前商品');scanCode=$('code').value.trim();if(!scanCode)throw Error('请扫描商品码');const d=await action('resolve',{code:scanCode});ReceivingDesk.candidates(d.candidates,scanCode,choose,d.hint);});
$('confirm-qty').onclick=()=>run(async()=>{if(!pending)return;const savedName=pending.name||pending.sku_id,savedQty=wholeQty($('step').value,false);if(!await mutate('scan',{code:scanCode,sku_id:pending.sku_id,qty:savedQty}))throw Error('商品资料已变化，请取消后重新识别');clearPending();$('code').value='';$('step').value=1;message('实收数量已保存');window.ReceivingDesk?.feedback?.('✓ '+savedName+' +'+savedQty,'ok');$('code').focus()});
$('cancel-qty').onclick=()=>{if(busy)return;clearPending();$('code').focus()};$('step').onkeydown=ev=>{if(ev.key==='Enter'&&pending){ev.preventDefault();$('confirm-qty').click()}};
$('code').onkeydown=ev=>{if(ev.key==='Enter')$('scan').click()};$('candidates').onclick=ev=>{const b=ev.target.closest('[data-candidate]');if(b)choose(window.unplannedCandidates.find(r=>r.sku_id===b.dataset.candidate))};
$('manual').onclick=()=>run(()=>openEditor());$('cancel-edit').onclick=()=>{if(!busy)$('editor').close()};$('lines').onclick=ev=>{const edit=ev.target.closest('[data-line]'),del=ev.target.closest('[data-delete]');if(edit)run(()=>openEditor(state.batches.find(b=>b.id===bid).lines.find(r=>r.id===Number(edit.dataset.line))));if(del)run(async()=>{if(await JstDialog.confirm('删除此商品实收明细？',{danger:true,ok:'删除'})){await mutate('delete_line',{line_id:Number(del.dataset.delete)});message('已删除商品')}})};$('edit-form').onsubmit=ev=>{ev.preventDefault();run(async()=>{const v=Object.fromEntries(new FormData(ev.target));v.qty=wholeQty(v.qty,true);await mutate(editing?'edit':'manual',{...v,line_id:editing?.id});$('editor').close();message('商品明细已保存，已关联采购单时需重新核对')})};
$('match').onclick=()=>run(()=>reconcile('match'));$('recheck').onclick=()=>run(()=>reconcile('refresh'));$('warehouses').onclick=()=>run(async()=>{const d=await api(API+'/options?sid='+state.session.id);$('warehouse').innerHTML='<option value="">请选择入库仓库</option>'+d.warehouses.map(w=>'<option value="'+e(w.id)+'">'+e(w.name)+'</option>').join('');message('请选择入库仓库')});$('inbound').onclick=()=>run(async()=>{const wh_id=$('warehouse').value;if(!wh_id)throw Error('请先读取并选择入库仓库');try{const p=await action('preview',{wh_id});if(!await JstDialog.confirm('采购单：'+p.snapshot.po_id+'\n仓库：'+p.snapshot.warehouse.name+'\n全部物流单：'+p.snapshot.batches.length+' 个\n实收总数量：'+p.total_qty+'\n\n确认生成一张待入库单？不会自动确认库存。'))return;const d=await action('create',{wh_id,fingerprint:p.fingerprint,confirmed:true});message('已生成待入库单 '+d.job.io_id+'，未自动确认入库');}finally{await load(state.session.id)}});$('jobs').onclick=ev=>{const b=ev.target.closest('[data-resume]');if(!b)return;run(async()=>{const j=state.jobs.find(j=>j.id===Number(b.dataset.resume));let io_id='';if(!j.io_id){io_id=await JstDialog.prompt('先在ERP核查原单，再输入找到的入库单号；不会新建第二张单。');if(!io_id)return}if(!await JstDialog.confirm('确认核对并继续原入库单？'))return;try{const d=await action('resume',{job_id:j.id,io_id,confirmed:true});message('原单 '+d.job.io_id+' 已完成数量核对')}finally{await load(state.session.id)}})};
$('details').onchange=renderReport;async function getImage(){await load(state.session.id);return R.image(state,$('details').checked)}function download(img){const a=document.createElement('a');a.href=img.dataUrl;a.download=state.session.input_no+'-收货明细.png';a.click()}
$('save-image').onclick=()=>run(async()=>{download(await getImage());message('图片已生成并开始下载')});async function copyText(text){let ok=false;try{if(window.JstNative&&window.JstNative.copyText){window.JstNative.copyText(text);ok=true}else if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);ok=true}}catch(err){}if(!ok){const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.top='-1000px';document.body.appendChild(t);t.select();try{ok=document.execCommand('copy')}catch(err){}t.remove()}if(ok){$('fallback').innerHTML='';message('已复制明细文字（'+(text.split('\n').length-1)+' 个商品），可直接粘贴')}else{$('fallback').innerHTML='<p>自动复制未完成，请手动全选复制以下文字：</p><textarea readonly>'+e(text)+'</textarea>';throw Error('复制明细文字失败，请手动复制')}}$('copy-text').onclick=()=>run(async()=>{if(!state)throw Error('请先开始或打开收货批次');await copyText(EXPRESS?waybillText():R.detailText(state));});if(EXPRESS)$('copy-all-text').onclick=()=>run(async()=>{if(!state)throw Error('请先开始或打开收货批次');await copyText(R.detailText(state,{stripNames:true}));});$('copy').onclick=()=>run(async()=>{const img=await getImage();try{const cap=await api('/api/report/clipboard/status');if(!cap.supported)throw Error('请在运行助手的Windows电脑端复制');await api('/api/report/clipboard',{token:cap.token,text:R.text(state),png:img.dataUrl});$('fallback').innerHTML='';message('已复制全部物流单号文字＋收货明细图片，请粘贴检查后发送')}catch(err){$('fallback').innerHTML='<p>图文复制未完成，可保存图片并手动复制以下单号：</p><textarea readonly>'+e(R.text(state))+'</textarea><a download="收货明细.png" href="'+e(img.dataUrl)+'">保存明细图片</a>';throw err}});
$('close-history').onclick=()=>{historyOpen=false;historySeq++;$('history').innerHTML='';};
$('exit-work').onclick=()=>{if(busy||pending||$('editor').open)return message('请先完成当前数量确认 / 保存',true);state=null;bid=0;$('work').hidden=true;window.ReceivingDesk?.clear();window.dispatchEvent(new CustomEvent('native-receipt',{detail:{kind:'stop'}}));message('已退出，全部已登记明细保留；可从历史继续收货');};
function requireIdle(){if(pending||document.querySelector('dialog[open]'))throw Error('请先确认或取消当前商品数量 / 关闭弹窗');}
async function reconcile(kind){
 requireIdle();
 if(!state)throw Error('请先打开收货批次');
 const entered=$('po').value.trim();
 if(kind==='match'&&!entered)throw Error('请输入采购单号或外部单号');
 $('warehouse').innerHTML='<option value="">请选择入库仓库</option>';
 try{
  if(kind==='match'){
   const candidates=await action('match_search',{po_id:entered});
   const result=await UnplannedLink.open(action,candidates);
   state=result||await api(API+'/state?sid='+state.session.id);
   render();await history();
   if(!result){$('po').value=entered;message('已取消采购关联，原关联与实收保持不变；下次入库前需重新核对');return;}
  }else await mutate('refresh');
 }catch(err){
  // A failed remote lookup invalidates verification on the server too.
  // Keep receipts and the entered number, but never leave an old green status visible.
  try{state=await api(API+'/state?sid='+state.session.id);}
  catch(_){state={...state,checked:false,can_create:false};}
  render();if(kind==='match')$('po').value=entered;
  throw err;
 }
 message(kind==='refresh'?'已按最新采购单重新核对；未自动入库':state.can_create?'采购单匹配通过；未自动入库':'采购单已关联，请查看核对差异；未自动入库');
}
function showSavedReport(){const closed=state.session.state==='closed';$('saved-report-heading').textContent=closed?'已结单 · 全部物流汇总':'实收已暂存 · 继续对单';$('saved-report-note').textContent=closed?'结单只结束扫码，未自动入库。实收只读；可在下方继续后补采购单、核对或处理原入库单。':'暂存不结单、不自动建采购单、不确认库存；仅展示已保存的本批次全部实收。';$('saved-report-continue').textContent=closed?'返回查看批次':'继续收货';$('saved-report-content').innerHTML='<p>批次 '+e(state.session.input_no)+' · 实收 '+R.num(state.total)+' 件</p>'+R.render(state,$('details').checked);$('saved-report-dialog').showModal();}
$('pause-report').onclick=()=>run(()=>{requireIdle();showSavedReport();message(state.session.state==='closed'?'已结单，正在查看汇总':'已登记明细均已保存；本次暂存不结单、不确认库存');});
$('saved-report-close').onclick=()=>{$('saved-report-dialog').close();};$('saved-report-continue').onclick=()=>{$('saved-report-dialog').close();if(state.session.state!=='closed')$('code').focus();};
ReceiptLifecycle.bind({run,idle:requireIdle,getState:()=>state,action,apply:async d=>{state=d;render();await history();},clear:async()=>{state=null;bid=0;window.purchaseUnplannedSid=null;$('work').hidden=true;ReceivingDesk.clear();await history();},report:showSavedReport,notify:message});
$('search').onclick=()=>{historyKind='purchase';historyOpen=true;run(history);};
$('search-tracking').onclick=()=>{historyKind='tracking';historyOpen=true;run(history);};$('tracking-query').onkeydown=ev=>{if(ev.key==='Enter')$('search-tracking').click();};
$('first-tracking').onkeydown=ev=>{if(ev.key==='Enter')$('new').click()};

let contextLists={owners:[],wms:[]},company='';
function contextOptions(id,rows,value){
 const values=[{id:company,name:'当前登录公司'},...rows].filter(r=>r.id&&String(r.id)!=='2147483647');
 if(value&&!values.some(r=>String(r.id)===String(value)))values.push({id:value,name:'已选范围'});
 const unique=[...new Map(values.map(r=>[String(r.id),r])).values()];
 $(id).innerHTML=unique.map(r=>'<option value="'+e(r.id)+'">'+e(r.name||r.id)+'（'+e(r.id)+'）</option>').join('');
 $(id).value=value||company;
}
async function loadContext(refresh=false){
 const [partners,st]=await Promise.all([api('/api/partners'+(refresh?'?refresh=1':'')),fetch('/api/state').then(async r=>{const s=await r.json();if(!r.ok||!s.logged)throw Error('请先登录后读取商品库范围');return s;})]);
 contextLists=partners;company=String(st.user?.coid||'');
 contextOptions('desk-owner',partners.owners||[],refresh?$('desk-owner').value:partners.pick_owner);
 contextOptions('desk-auth',partners.wms||[],refresh?$('desk-auth').value:partners.pick_auth);
 if(partners.err)$('desk-context-hint').textContent='货主清单未完整读取，请刷新或确认当前范围。扫码只使用所选范围商品库。';
}
function renderContext(){
 const catalog=state.catalog_scope||{owner:state.session.owner_co_id,auth:state.session.auth_co_id};
 $('desk-active-context').textContent='扫码商品库：货主 '+catalog.owner+' / 分仓 '+catalog.auth+(state.linked?'；采购关联：货主 '+state.session.owner_co_id+' / 分仓 '+state.session.auth_co_id:(EXPRESS?'':'；采购查询独立，不受此范围限制'));
 $('desk-repair-context').hidden=!!(state.locked||state.linked||state.batches.some(b=>b.lines.length));
}
$('desk-refresh-context').onclick=()=>run(()=>loadContext(true));
$('desk-repair-context').onclick=()=>run(async()=>{
 if(pending)throw Error('请先确认或取消当前商品');
 await loadContext();
 contextOptions('context-owner',contextLists.owners||[],state.session.owner_co_id);
 contextOptions('context-auth',contextLists.wms||[],state.session.auth_co_id);
 $('context-editor').showModal();
});
$('context-cancel').onclick=()=>{if(!busy)$('context-editor').close();};
$('context-editor').addEventListener('cancel',ev=>{if(busy)ev.preventDefault();});
$('context-form').onsubmit=ev=>{ev.preventDefault();run(async()=>{
 await mutate('context',{owner_co_id:$('context-owner').value,auth_co_id:$('context-auth').value});
 $('context-editor').close();message('已保存商品库范围，请在同一商品扫码栏重新扫描；未改动实收。');
});};
const initialSid=new URLSearchParams(location.search).get('sid');
run(async()=>{await loadContext();if(initialSid)await load(Number(initialSid));else await history();});
$('editor').addEventListener('cancel',ev=>{if(busy)ev.preventDefault();});
setInterval(()=>fetch('/api/heartbeat').catch(()=>{}),15000);
guardQtyInput($('step'),false);guardQtyInput($('edit-form')?.elements.qty,true);
})();

/* Shared presentation only. All writes remain in the existing mode-specific services. */
(function(){'use strict';
 const $=id=>document.getElementById(id), type=document.body.dataset.desk;
 if(!type)return;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let mode='tracking',state=null,bid=0,busy=false;
 const blocked=()=>busy||!$('pending').hidden||!!document.querySelector('dialog[open]');
 function say(text){$('message').textContent=text;}
 function setMode(next,focus=true,force=false){
  if(state?.session.state==='closed'||state?.locked){if(focus)say('此批次实收已锁定，可切换物流查看明细，不会启动录像');return false;}
  if(!force&&blocked()){say('请先确认或取消当前商品数量，等待保存完成');return false;}
  mode=next;const tracking=mode==='tracking';
  $('tracking').hidden=!tracking;$('code').hidden=tracking;
  $(type==='joint'?'enter-tracking':'scan-tracking').hidden=!tracking;$('scan').hidden=tracking;
  $('desk-mode-tracking').classList.toggle('active',tracking);$('desk-mode-item').classList.toggle('active',!tracking);
  if(focus)$(tracking?'tracking':'code').focus();return true;
 }
 $('desk-mode-tracking').onclick=()=>setMode('tracking');$('desk-mode-item').onclick=()=>setMode('item');
 $('code').addEventListener('focus',()=>setMode('item',false,true));$('tracking').addEventListener('focus',()=>setMode('tracking',false,true));
 document.addEventListener('keydown',ev=>{
  if(document.querySelector('dialog[open]'))return;
  if(ev.key==='F1'||ev.key==='F2'){
   ev.preventDefault();ev.stopImmediatePropagation();
   if(!state){if(ev.key==='F1')$(type==='joint'?'desk-po-input':'first-tracking').focus();return;}
   setMode(ev.key==='F1'?'tracking':'item');
  }else if(ev.key==='Escape'&&!$('pending').hidden){ev.preventDefault();ev.stopImmediatePropagation();$('cancel-qty').click();}
 },true);
 $('desk-batches').onclick=ev=>{const b=ev.target.closest('[data-select-batch]');if(!b)return;if(blocked())return say('请先完成当前商品数量确认 / 保存');$('batches').value=b.dataset.selectBatch;$('batches').dispatchEvent(new Event('change'));setMode('item');};
 $('lines').addEventListener('dblclick',ev=>{const td=ev.target.closest('td.num,td.qty');if(!td)return;const b=td.closest('tr').querySelector(type==='joint'?'[data-edit]':'[data-line]');if(b&&!b.disabled)b.click();});
 // 快递单登记：一批可能有几百个物流单号。列表固定高度、框内滚动，最新扫描的在最上面；
 // 按物流单 id 复用按钮节点，每次扫描只改动变化的那几行，不整表重画；顶部搜索框按单号筛选 / 定位。
 const nodes=new Map();let listSid=null,listBid=0;
 function batchStatus(s,b){return s.session.state==='closed'?'已结单 / 实收只读':s.locked?'已提交 / 锁定':b.finished?'本包已扫完':'可继续补扫';}
 function searchText(){const el=$('desk-batch-search');return el?el.value.replace(/\s+/g,'').toLowerCase():'';}
 function applyFilter(){const q=searchText();let shown=0,first=null;for(const n of nodes.values()){const hit=!q||n.dataset.no.includes(q);n.hidden=!hit;if(hit){shown++;if(!first||Number(n.dataset.selectBatch)>Number(first.dataset.selectBatch))first=n;}}
  const count=$('desk-package-count');if(count)count.textContent=q?'匹配 '+shown+' 单':'';
  if(q&&first)reveal(first);return first;}
 function reveal(n){const box=$('desk-batches');if(!n||n.hidden)return;const top=n.offsetTop-box.offsetTop,bottom=top+n.offsetHeight;if(top<box.scrollTop)box.scrollTop=top-4;else if(bottom>box.scrollTop+box.clientHeight)box.scrollTop=bottom-box.clientHeight+4;}
 function renderLongList(s,batches,total,changed){
  const box=$('desk-batches');
  if(listSid!==s.session.id){box.textContent='';nodes.clear();listSid=s.session.id;}
  const title=$('desk-batches-title');if(title)title.textContent='本批次物流单号（'+batches.length+'）';
  if(!batches.length){box.innerHTML='<div class="desk-empty">请先扫描一个物流单号</div>';nodes.clear();applyFilter();return;}
  const empty=box.querySelector('.desk-empty');if(empty)empty.remove();
  const order=batches.slice().sort((a,b)=>b.id-a.id),seen=new Set(),q=searchText();let prev=null,added=false;
  for(const b of order){
   seen.add(b.id);let n=nodes.get(b.id);
   if(!n){n=document.createElement('button');n.type='button';n.className='desk-batch';n.dataset.selectBatch=b.id;n.setAttribute('role','listitem');n._sig='';nodes.set(b.id,n);added=true;}
   const active=b.id===bid,sig=b.tracking_no+'\u0001'+total(b)+'\u0001'+batchStatus(s,b)+'\u0001'+active;
   if(n._sig!==sig){n._sig=sig;n.innerHTML='<b>'+esc(b.tracking_no)+'</b><span>'+total(b)+' 件 · '+batchStatus(s,b)+'</span>';n.classList.toggle('active',active);n.setAttribute('aria-pressed',String(active));n.dataset.no=String(b.tracking_no).replace(/\s+/g,'').toLowerCase();n.hidden=!!q&&!n.dataset.no.includes(q);}
   const want=prev?prev.nextSibling:box.firstChild;if(n!==want)box.insertBefore(n,want);prev=n;
  }
  for(const [id,n] of nodes)if(!seen.has(id)){n.remove();nodes.delete(id);}
  if(q&&added)applyFilter();
  if(changed||listBid!==bid||added){listBid=bid;const a=nodes.get(bid);if(a&&!a.hidden)reveal(a);else if(!q)box.scrollTop=0;}
 }
 if(type==='express'&&$('desk-batch-search')){
  const input=$('desk-batch-search');
  input.addEventListener('input',()=>applyFilter());
  input.addEventListener('keydown',ev=>{
   if(ev.key==='Enter'){ev.preventDefault();const first=applyFilter();if(first&&searchText()){first.click();}else say('本批次没有找到这个物流单号');}
   else if(ev.key==='Escape'){ev.preventDefault();ev.stopPropagation();input.value='';applyFilter();const a=nodes.get(bid);if(a)reveal(a);}
  });
 }
 function render(s,id){
  const changed=state?.session.id!==s.session.id||bid!==id;state=s;bid=id;
  if(window.parent!==window)parent.ReceivingWorkspace?.updateStatus(type,s);
  $('start').hidden=true;
  const batches=s.batches||[],total=b=>b.total??(b.lines||[]).reduce((n,r)=>n+Number(r.qty||0),0);
  if(type==='express')renderLongList(s,batches,total,changed);else{
  $('desk-package-count').textContent=batches.length+' 个物流单';
  $('desk-batches').innerHTML=batches.length?batches.map(b=>'<button type="button" class="desk-batch'+(b.id===bid?' active':'')+'" data-select-batch="'+b.id+'" aria-pressed="'+(b.id===bid)+'"><b>'+esc(b.tracking_no)+'</b><span>'+total(b)+' 件 · '+(s.session.state==='closed'?'已结单 / 实收只读':s.locked?'已提交 / 锁定':b.finished?'本包已扫完':'可继续补扫')+'</span></button>').join(''):'<div class="desk-empty">请先扫描一个物流单号</div>';}
  const current=batches.find(b=>b.id===bid);$('desk-current-tracking').textContent=current?current.tracking_no+' · '+total(current)+' 件':'尚未选中物流单';
  if($('desk-receipt-total'))$('desk-receipt-total').textContent=batches.length+' 个物流单 / '+s.sku_count+' 种商品 / 实收 '+s.total+' 件';
  const url='/'+type+'.html?sid='+encodeURIComponent(s.session.id)+'&bid='+encodeURIComponent(bid);
  sessionStorage.setItem('receiving:return:'+type,url);history.replaceState(null,'',url+(window.parent!==window||new URLSearchParams(location.search).has('embed')?'&embed=1':''));
  if(changed)setMode(batches.length?'item':'tracking',false,true);
 }
 function clear(){state=null;bid=0;if(window.parent!==window)parent.ReceivingWorkspace?.updateStatus(type,null);$('start').hidden=false;sessionStorage.removeItem('receiving:return:'+type);history.replaceState(null,'','/'+type+'.html'+(window.parent!==window||new URLSearchParams(location.search).has('embed')?'?embed=1':''));}
 function activate(){if(blocked())return;if(state){setMode(window.__nativeVideo?.enabled?'tracking':mode,true,true);}else $(type==='joint'?'desk-po-input':'first-tracking').focus();}
 function candidates(rows,code,onChoose,hint){
  $('candidates').textContent='';
  if(rows.length===1)return onChoose(rows[0]);
  const miss=()=>say(hint||'当前货主 / 分仓商品库未找到此码，请检查商品库同步状态或手动登记。未登记任何数量。');
  if(!rows.length){
   // 商品资料查不到：可学习此码，学习后自动重新识别；跳过则保持原提示
   if(window.BarcodeLearn&&state?.session?.id&&!window.BarcodeLearn.isOpen()){
    say('商品资料未找到此码，可在弹框中学习匹配，或点「跳过」。');
    window.BarcodeLearn.open({kind:type,sid:state.session.id,code}).then(row=>{
     if(row){$('code').value=code;setTimeout(()=>$('scan').click(),0);}
     else{miss();$('code').focus();$('code').select();}
    });
    return;
   }
   miss();return;
  }
  window.ProductPicker.open({rows,code,owner:state?.catalog_scope?.owner??state?.session.owner_co_id,auth:state?.catalog_scope?.auth??state?.session.auth_co_id,onChoose,onCancel:()=>{$('code').focus();$('code').select();}});
 }
 window.ReceivingDesk={render,clear,setBusy:v=>{busy=v;},blocked,setMode,activate,candidates};
 if(type==='joint'){
  const value=()=>[...new Set($('purchase-nos').value.split(/[,，\s]+/).filter(Boolean))];
  function chips(nos){$('purchase-nos').value=nos.join('\n');$('desk-po-chips').innerHTML=nos.map((n,i)=>'<span class="desk-po-chip">'+esc(n)+' <button type="button" data-remove-po="'+i+'" aria-label="移除采购单 '+esc(n)+'">×</button></span>').join('');}
  function add(){const entered=$('desk-po-input').value.trim();if(!entered)return;chips([...new Set(value().concat(entered.split(/[,，\s]+/).filter(Boolean)))]);$('desk-po-input').value='';$('desk-po-input').focus();}
  $('desk-po-add').onclick=add;$('desk-po-input').onkeydown=ev=>{if(ev.key==='Enter'){ev.preventDefault();add();}};
  $('desk-po-chips').onclick=ev=>{const b=ev.target.closest('[data-remove-po]');if(b)chips(value().filter((_,i)=>i!==Number(b.dataset.removePo)));};
 }
})();

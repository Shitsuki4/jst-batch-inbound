/* 收货页公共界面（4.1.2）：顶部固定扫码条、录像状态、重复扫码定位、删除整批确认、汇总紧凑行。
   只做展示与确认，不改任何业务写入；录像状态只读取桌面宿主推送的 window.__nativeVideo，不编造数值。 */
(function(root){'use strict';
 // ---------- 纯函数（Node 测试可直接 require） ----------
 const norm=s=>String(s??'').replace(/\s+/g,'').toLowerCase();
 function findDuplicate(batches,no){const k=norm(no);if(!k)return null;return (batches||[]).find(b=>norm(b.tracking_no)===k)||null;}
 function hm(text){const m=/(\d{1,2}):(\d{2})(?::\d{2})?\s*$/.exec(String(text||'').trim());return m?m[1].padStart(2,'0')+':'+m[2]:'';}
 function dupToast(batch){const t=hm(batch&&(batch.first_scan_at||batch.created_at));return t?'已扫过 · '+t:'已扫过';}
 function deleteConfirmOk(typed,sessionNo,count){const v=String(typed??'').trim();if(!v)return false;return v===String(sessionNo??'').trim()||(count!=null&&count!==''&&/^\d+$/.test(v)&&Number(v)===Number(count));}
 function mmss(ms){if(!(ms>=0))return '';const s=Math.floor(ms/1000),h=Math.floor(s/3600),m=Math.floor(s%3600/60),r=s%60;return (h?h+':'+String(m).padStart(2,'0'):String(m).padStart(2,'0'))+':'+String(r).padStart(2,'0');}
 function gb(bytes){const n=Number(bytes);return n>0?(n/1073741824).toFixed(1)+'GB':'';}
 // 录像状态视图：只使用载荷里真实存在的字段；elapsedMs 仅在本页亲眼看到“开始录制”时才有。
 function recView(v,elapsedMs){
  if(!v||typeof v!=='object')return {cls:'off',text:'录像状态：请在桌面版中使用',title:'普通浏览器不连接录像'};
  if(!v.enabled)return {cls:'off',text:'录像未启用',title:v.status||''};
  const warn=v.warning?String(v.warning):'';
  if(v.active){
   const parts=['● 录制中'];const t=v.elapsedMs!=null?mmss(Number(v.elapsedMs)):(elapsedMs!=null?mmss(elapsedMs):'');if(t)parts.push(t);
   const size=gb(v.segmentBytes??v.bytes);const tail=size?'本段 '+size:(v.tracking?'单号 '+v.tracking:'');
   return {cls:warn?'warn':'on',text:parts.join(' ')+(tail?' · '+tail:''),title:[v.status,warn].filter(Boolean).join(' · ')};
  }
  if(v.ready===false&&!v.status)return {cls:'off',text:'录像准备中…',title:''};
  return {cls:'stop',text:'● 未录制',title:[v.status,warn].filter(Boolean).join(' · ')};
 }
 const api={norm,findDuplicate,hm,dupToast,deleteConfirmOk,mmss,gb,recView};
 if(typeof module!=='undefined'&&module.exports){module.exports=api;}
 if(typeof document==='undefined'||!document.body)return;
 const type=document.body.dataset.desk;root.DeskUI=api;if(!type)return;

 const $=id=>document.getElementById(id);
 const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
 const host=(()=>{try{return root.parent&&root.parent!==root&&root.parent.document?root.parent:root;}catch(e){return root;}})();
 let state=null,bid=0;

 // ---------- 1. 顶部固定扫码条 ----------
 const scanner=document.querySelector('.desk-scanner');
 const bar=el('div','desk-topbar');
 const cur=el('div','desk-top-current');const curLabel=el('small',null,'当前物流单号');const curNo=el('strong','desk-top-no','—');cur.append(curLabel,curNo);
 const cnt=el('div','desk-top-count');const cntLabel=el('small',null,'本单件数');const cntNo=el('strong',null,'—');cnt.append(cntLabel,cntNo);
 const rec=el('div','desk-rec off','录像状态读取中');rec.setAttribute('role','status');rec.setAttribute('aria-live','polite');
 const help=el('button','btn btn-sm btn-ghost desk-help-btn','?');help.type='button';help.title='显示 / 隐藏说明文字';help.setAttribute('aria-label','显示说明文字');
 help.onclick=()=>{document.body.classList.toggle('desk-help-open');refocus();};
 if(scanner){
  const row=scanner.querySelector('.desk-scan-row');
  bar.append(cur,cnt,rec,help);
  scanner.classList.add('desk-sticky');
  scanner.insertBefore(bar,scanner.firstChild);
  if(row)row.classList.add('desk-top-scan');
 }
 function refocus(){if(document.querySelector('dialog[open]'))return;try{root.ReceivingDesk?.activate?.();}catch(e){}}

 // ---------- 3. 删除整批收进「更多」+ 输入确认 ----------
 const del=$('delete-session');
 if(del&&del.parentElement){
  const wrap=el('div','desk-more');const btn=el('button','btn btn-ghost desk-more-btn','更多 ▾');btn.type='button';btn.setAttribute('aria-haspopup','menu');btn.setAttribute('aria-expanded','false');
  const menu=el('div','desk-more-menu');menu.setAttribute('role','menu');menu.hidden=true;
  del.parentElement.insertBefore(wrap,del);wrap.append(btn,menu);
  for(const id of ['reload','exit-work','delete-session']){const b=$(id);if(b){b.classList.add('desk-menu-item');b.setAttribute('role','menuitem');menu.append(b);}}
  const close=()=>{menu.hidden=true;btn.setAttribute('aria-expanded','false');};
  btn.onclick=ev=>{ev.stopPropagation();menu.hidden=!menu.hidden;btn.setAttribute('aria-expanded',String(!menu.hidden));};
  menu.addEventListener('click',()=>setTimeout(close,0));
  document.addEventListener('click',ev=>{if(!wrap.contains(ev.target))close();});
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&!menu.hidden){close();btn.focus();}});
 }
 function confirmDelete(s){
  return new Promise(resolve=>{
   const no=s.session.input_no,n=(s.batches||[]).length;
   const d=el('dialog','desk-delete-dialog');d.setAttribute('aria-labelledby','desk-delete-title');
   d.innerHTML='<h2 id="desk-delete-title">删除整批</h2><p class="desk-delete-what"></p><p class="muted">本操作不可撤销；不删除录像、不修改 ERP 采购单和库存。存在入库记录（含失败 / 待核实）时禁止删除。</p><label>请输入批次号 <b class="desk-delete-no"></b> 或物流单数 <b class="desk-delete-n"></b> 确认<input class="desk-delete-input" autocomplete="off"></label><div class="bar"><button type="button" class="btn btn-danger desk-delete-ok" disabled>确认删除整批</button><button type="button" class="btn btn-ghost desk-delete-cancel">取消</button></div>';
   d.querySelector('.desk-delete-what').textContent='批次 '+no+' · '+n+' 个物流单 · 实收 '+s.total+' 件';
   d.querySelector('.desk-delete-no').textContent=no;d.querySelector('.desk-delete-n').textContent=String(n);
   const input=d.querySelector('.desk-delete-input'),ok=d.querySelector('.desk-delete-ok');
   let done=false;const finish=v=>{if(done)return;done=true;try{d.close();}catch(e){}d.remove();resolve(v);setTimeout(refocus,0);};
   input.addEventListener('input',()=>{ok.disabled=!deleteConfirmOk(input.value,no,n);});
   input.addEventListener('keydown',ev=>{if(ev.key==='Enter'){ev.preventDefault();if(!ok.disabled)finish(true);}});
   ok.onclick=()=>{if(deleteConfirmOk(input.value,no,n))finish(true);};
   d.querySelector('.desk-delete-cancel').onclick=()=>finish(false);
   d.addEventListener('cancel',ev=>{ev.preventDefault();finish(false);});
   document.body.append(d);d.showModal();input.focus();
  });
 }
 api.confirmDelete=confirmDelete;

 // ---------- 4. 录像状态（真实数据） ----------
 let lastKey='',lastStatus='',flashTimer=0;
 function seen(){try{return host.__deskRecSeen||(host.__deskRecSeen={});}catch(e){return {};}}
 function paintRec(){
  const v=root.__nativeVideo,obs=seen();let elapsed=null;
  if(v&&v.enabled&&v.active){const key=(v.key||'')+'|'+(v.tracking||'');
   if(obs.key!==key){obs.key=key;obs.at=obs.wasIdle?Date.now():null;}
   if(obs.at)elapsed=Date.now()-obs.at;obs.wasIdle=false;
  }else if(v){obs.key='';obs.at=null;obs.wasIdle=true;}
  const view=recView(v,elapsed);
  rec.className='desk-rec '+view.cls+(rec.classList.contains('flash')?' flash':'');rec.textContent=view.text;rec.title=[view.title,rec.dataset.limit&&rec.dataset.limit!=='不限'?'单段上限 '+rec.dataset.limit:''].filter(Boolean).join(' · ');
  const status=v?String(v.status||''):'';const key=v?(v.key||'')+'|'+(v.tracking||''):'';
  if(v&&v.active&&key===lastKey&&status!==lastStatus&&/分段|切段|新段|续录|下一段|上限/.test(status))flash('录像已切到下一段：'+status);
  lastKey=key;lastStatus=status;
 }
 function flash(text){rec.classList.add('flash');toast(text,'','info');clearTimeout(flashTimer);flashTimer=setTimeout(()=>rec.classList.remove('flash'),2400);}
 // 单段上限 / 已自动分段次数来自桌面端 settings 命令（与「设置→视频举证」同一来源）；只在录像状态推送时读取，最多 3 秒一次。
 let segKey='',segCount=null,segBusy=false,segAt=0;
 async function readSegments(){
  const v=root.__nativeVideo;if(!v||!v.enabled||!v.active||!root.NativeDesk?.available||segBusy||Date.now()-segAt<3000)return;
  segBusy=true;segAt=Date.now();
  try{const s=await root.NativeDesk.call('settings');const key=(v.key||'')+'|'+(v.tracking||''),n=Number(s?.segmentRollovers);
   if(Number.isFinite(n)){if(key===segKey&&segCount!=null&&n>segCount)flash('录像已到单段上限，已自动切到下一段（本单第 '+(n+1)+' 段）');segKey=key;segCount=n;}
   if(s?.segmentLimit)rec.dataset.limit=String(s.segmentLimit);
  }catch(e){}finally{segBusy=false;}
 }
 const onVideo=()=>{paintRec();readSegments();};
 try{host.addEventListener('native:video',onVideo);}catch(e){}
 if(host!==root)root.addEventListener('native:video',onVideo);
 setInterval(paintRec,1000);paintRec();

 // ---------- 5. 重复扫码：定位 + 提示 ----------
 const toastBox=el('div','desk-toast');toastBox.setAttribute('role','alert');toastBox.hidden=true;document.body.append(toastBox);let toastTimer=0;
 function toast(main,sub,kind){toastBox.className='desk-toast '+(kind||'warn');toastBox.innerHTML='';toastBox.append(el('strong',null,main));if(sub)toastBox.append(el('span',null,sub));toastBox.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{toastBox.hidden=true;},3500);}
 function dupSound(){try{if(host.jstSound){host.jstSound(true);setTimeout(()=>host.jstSound(true),180);return;}const C=root.AudioContext||root.webkitAudioContext;if(!C)return;const c=dupSound.c||(dupSound.c=new C());c.resume();[0,0.16].forEach((t,i)=>{const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.value=i?440:660;g.gain.value=0.08;o.start(c.currentTime+t);o.stop(c.currentTime+t+0.11);});}catch(e){}}
 let pendingDup=null;
 function noteScan(){const input=$('tracking');if(!input||!state)return;const hit=findDuplicate(state.batches,input.value);pendingDup=hit?{id:hit.id,batch:hit,at:Date.now()}:null;}
 const trk=$('tracking');if(trk)trk.addEventListener('keydown',ev=>{if(ev.key==='Enter')noteScan();},true);
 for(const id of ['scan-tracking','enter-tracking']){const b=$(id);if(b)b.addEventListener('click',noteScan,true);}
 function locate(id){
  const box=$('desk-batches');if(!box)return null;const search=$('desk-batch-search');
  let row=box.querySelector('[data-select-batch="'+id+'"]');
  if(row&&row.hidden&&search){search.value='';search.dispatchEvent(new Event('input'));row=box.querySelector('[data-select-batch="'+id+'"]');}
  if(!row)return null;
  const top=row.offsetTop-box.offsetTop;if(top<box.scrollTop||top+row.offsetHeight>box.scrollTop+box.clientHeight)box.scrollTop=Math.max(0,top-box.clientHeight/3);
  box.querySelectorAll('.desk-dup').forEach(n=>n.classList.remove('desk-dup'));row.classList.add('desk-dup');try{row.scrollIntoView({block:'nearest'});}catch(e){}setTimeout(()=>row.classList.remove('desk-dup'),4000);return row;
 }
 function afterRender(){
  if(!pendingDup)return;const d=pendingDup;if(Date.now()-d.at>8000){pendingDup=null;return;}
  if(bid!==d.id)return;pendingDup=null;
  const b=(state.batches||[]).find(x=>x.id===d.id)||d.batch;
  locate(d.id);toast(dupToast(b),b.tracking_no+' 已在本批次，已定位到该行，未新建物流单','warn');dupSound();
 }


 // ---------- 2. 汇总表紧凑行：编码放在名称前（只调整显示顺序，不改数据） ----------
 const report=$('report');
 function compact(){if(!report||!report.closest('.desk-summary'))return;report.querySelectorAll('tbody tr td:first-child').forEach(td=>{const sku=td.querySelector(':scope>.sku');if(sku&&td.firstElementChild!==sku)td.insertBefore(sku,td.firstChild);});}
 if(report&&root.MutationObserver){new MutationObserver(compact).observe(report,{childList:true});compact();}
 // ---------- 渲染挂钩：顶部条 + 重复扫码 ----------
 function paintTop(){
  const b=state?(state.batches||[]).find(x=>x.id===bid):null;
  const total=b?(b.total??(b.lines||[]).reduce((n,r)=>n+Number(r.qty||0),0)):null;
  curNo.textContent=b?b.tracking_no:'—';cntNo.textContent=b?String(total):'—';
  bar.classList.toggle('empty',!b);
 }
 const RD=root.ReceivingDesk;
 if(RD&&RD.render){
  const render=RD.render,clear=RD.clear;
  RD.render=function(s,id){const r=render.apply(this,arguments);state=s;bid=id;paintTop();afterRender();return r;};
  RD.clear=function(){const r=clear.apply(this,arguments);state=null;bid=0;pendingDup=null;paintTop();return r;};
 }
 paintTop();
})(typeof window!=='undefined'?window:globalThis);

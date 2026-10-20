/* 远程管理页：只读。所有文本一律 textContent 渲染，不拼接 HTML。 */
(function(){'use strict';
 const $=id=>document.getElementById(id);
 const TYPES={po:'采购单收货',unplanned:'无采购单收货',joint:'联合收货'};
 const STATES={active:'收货中',paused:'挂起',closed:'已结单'};
 const INBOUND={complete:'已入库',pending:'待确认',unknown:'结果待核实',rejected:'已拒绝',reserved:'生成中'};
 const VSTATE={saved:'已保存',recording:'录制中',starting:'启动中',failed:'异常',stopped:'已停止'};
 let me=null,rOffset=0,vOffset=0,lastView='receipts';

 function el(tag,props,...kids){const e=document.createElement(tag);if(props)for(const [k,v] of Object.entries(props)){if(v==null)continue;if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v);}for(const k of kids){if(k==null||k===false)continue;e.append(k instanceof Node?k:document.createTextNode(String(k)));}return e;}
 function tag(text,kind){return el('span',{class:'tag'+(kind?' '+kind:''),text});}
 function num(v){if(v==null||v==='')return '';const n=Number(v);return Number.isFinite(n)?(Math.abs(n-Math.round(n))<1e-9?String(Math.round(n)):String(n)):String(v);}
 function fmtTime(v){if(!v)return '';const s=String(v);if(/^\d{4}-\d{2}-\d{2}T/.test(s)){const d=new Date(s);if(!isNaN(d))return d.toLocaleString('zh-CN',{hour12:false});}return s;}
 function fmtDur(sec){sec=Math.max(0,Math.round(Number(sec)||0));const h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s=sec%60;return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(s).padStart(2,'0');}
 function fmtSize(b){b=Number(b)||0;return b>1073741824?(b/1073741824).toFixed(2)+' GB':b>1048576?(b/1048576).toFixed(1)+' MB':Math.round(b/1024)+' KB';}
 function day(offset){const d=new Date();d.setDate(d.getDate()+offset);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
 function msg(id,text,error){const m=$(id);m.textContent=text||'';m.classList.toggle('error',!!error);}

 async function api(path,opts){
  const r=await fetch(path,Object.assign({credentials:'same-origin',cache:'no-store'},opts||{}));
  let d={};try{d=await r.json();}catch{d={ok:false,msg:'服务器响应无效（'+r.status+'）'};}
  if(r.status===401&&d.login){showLogin(d.msg&&me?'会话已过期，请重新登录':'');throw new Error(d.msg||'未登录');}
  if(!r.ok||!d.ok)throw new Error(d.msg||('请求失败 '+r.status));
  return d;
 }

 function showLogin(text){me=null;$('who').hidden=true;$('app-view').hidden=true;$('detail-view').hidden=true;$('login-view').hidden=false;msg('login-msg',text||'',!!text);stopVideo();}
 function showApp(){ $('login-view').hidden=true;$('who').hidden=false;$('who-name').textContent=me.user;$('detail-view').hidden=true;$('app-view').hidden=false;}

 $('login-form').addEventListener('submit',async ev=>{
  ev.preventDefault();const btn=$('login-btn');btn.disabled=true;msg('login-msg','正在登录…');
  try{await api('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('login-user').value,password:$('login-pass').value})});
   $('login-pass').value='';await boot();}
  catch(e){msg('login-msg',e.message,true);}finally{btn.disabled=false;}
 });
 $('logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':me?me.csrf:''},body:'{}'});}catch{}showLogin('已退出登录');});

 document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>tab(b.dataset.tab)));
 function tab(name){lastView=name;document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===name);b.setAttribute('aria-selected',String(b.dataset.tab===name));});document.querySelectorAll('[data-pane]').forEach(p=>p.hidden=p.dataset.pane!==name);if(name==='videos'&&!$('v-list').childElementCount)loadVideos(true);}

 // ---------------- 收货历史
 $('r-from').value=day(-6);$('r-to').value=day(0);
 $('r-form').addEventListener('submit',ev=>{ev.preventDefault();loadReceipts(true);});
 $('r-more').addEventListener('click',()=>loadReceipts(false));
 async function loadReceipts(reset){
  if(reset){rOffset=0;$('r-list').replaceChildren();}
  msg('r-msg','查询中…');
  const p=new URLSearchParams({type:$('r-type').value,q:$('r-q').value.trim(),from:$('r-from').value,to:$('r-to').value,offset:String(rOffset),limit:'50'});
  try{const d=await api('/api/receipts?'+p);rOffset+=d.rows.length;
   for(const r of d.rows)$('r-list').append(receiptItem(r));
   if(!$('r-list').childElementCount)$('r-list').append(el('div',{class:'empty',text:'没有符合条件的收货记录'}));
   $('r-more').hidden=!d.more;msg('r-msg','已显示 '+rOffset+' 条');}
  catch(e){msg('r-msg',e.message,true);}
 }
 function inboundTag(list){if(!list||!list.length)return tag('未生成入库单');const live=list.find(j=>j.status!=='rejected')||list[0];return tag(INBOUND[live.status]||live.status,live.status==='complete'?'ok':live.status==='rejected'?'bad':'warn');}
 function receiptItem(r){
  const title=r.type==='joint'?((r.po_ids&&r.po_ids.length)?'联合 '+r.po_ids.join(' / '):(r.input_no||('#'+r.id))):(r.po_id||r.input_no||('#'+r.id));
  const sub=[r.outer_po_id&&('外部 '+r.outer_po_id),r.supplier,r.note,r.mother&&('母单 '+r.mother),(r.trackings||[]).length&&('物流 '+r.trackings.join('、')+(r.tracking_count>r.trackings.length?' 等'+r.tracking_count+'个':''))].filter(Boolean).join(' · ');
  return el('button',{type:'button',class:'item',onclick:()=>openReceipt(r.type,r.id)},
   el('div',{class:'l1'},el('span',{text:title}),el('span',{text:'实收 '+num(r.total)})),
   el('div',{class:'l2'},tag(TYPES[r.type]),tag(STATES[r.state]||r.state||'',r.state==='closed'?'ok':'warn'),inboundTag(r.inbound),' ',fmtTime(r.started_at)),
   sub?el('div',{class:'l2',text:sub}):null);
 }
 function kv(pairs){const dl=el('dl',{class:'kv'});for(const [k,v] of pairs){if(v==null||v==='')continue;dl.append(el('dt',{text:k}),el('dd',{text:String(v)}));}return dl;}
 function table(head,rows){const t=el('table');t.append(el('thead',null,el('tr',null,...head.map(h=>el('th',{text:h[0]})))));const tb=el('tbody');for(const r of rows)tb.append(el('tr',null,...head.map(h=>el('td',{class:h[2]?'num':null,text:h[1](r)??''}))));t.append(tb);return el('div',{class:'table-wrap'},t);}
 async function openReceipt(type,id){
  openSheet('加载中…');
  try{const d=await api('/api/receipts/detail?'+new URLSearchParams({type,id:String(id)}));
   const h=d.head,body=$('detail-body');$('detail-title').textContent=TYPES[type]+' · '+(h.po_id||h.input_no||('#'+h.id));
   body.append(el('div',{class:'card'},el('h2',{text:'单据信息'}),kv([['类型',TYPES[type]],['收货单',h.input_no],['采购单号',h.po_id||(d.purchases||[]).map(p=>p.po_id).join(' / ')],['外部单号',h.outer_po_id||(d.purchases||[]).map(p=>p.outer_po_id).filter(Boolean).join(' / ')],['供应商',h.supplier||(d.purchases||[]).map(p=>p.supplier).filter(Boolean).join(' / ')],['仓库',h.wh_name],['母单物流',d.group&&d.group.mother],['备注',h.note],['状态',STATES[h.state]||h.state],['开始',h.started_at],['结束',h.ended_at]])));
   if(d.compare&&d.compare.length)body.append(el('div',{class:'card'},el('h2',{text:'对单（按商品汇总）'}),table([['商品编码',r=>r.sku_id],['名称',r=>[r.name,r.spec].filter(Boolean).join(' ')],['应收',r=>r.plan==null?'—':num(r.plan),1],['实收',r=>num(r.recv),1],['状态',r=>r.status]],d.compare)));
   for(const b of d.batches)body.append(el('div',{class:'card'},el('h2',{text:'物流 '+b.tracking_no+'（'+num(b.total)+'）'}),el('p',{class:'hint',text:'首次扫描 '+(b.first_scan_at||'')}),b.lines.length?table([['商品编码',r=>r.sku_id],['条码',r=>r.sku_code],['名称',r=>[r.name,r.spec].filter(Boolean).join(' ')],['数量',r=>num(r.qty),1],['',r=>r.matched?'':'采购单外']],b.lines):el('p',{class:'hint',text:'暂无明细'})));
   if(!d.batches.length)body.append(el('div',{class:'card'},el('p',{class:'hint',text:'该单还没有物流批次'})));
   if(d.stages&&d.stages.length)body.append(el('div',{class:'card'},el('h2',{text:'分配到采购单'}),table([['采购单',r=>r.po_id],['方式',r=>r.mode==='purchase_only'?'仅采购范围':'全部实收'],['数量',r=>num(r.total),1],['时间',r=>r.created]],d.stages)));
   body.append(el('div',{class:'card'},el('h2',{text:'入库单'}),d.inbound.length?table([['状态',r=>INBOUND[r.status]||r.status],['入库单号',r=>r.io_id||'—'],['创建',r=>r.created_at],['更新',r=>r.updated_at]],d.inbound):el('p',{class:'hint',text:'未生成入库单'})));
  }catch(e){$('detail-body').replaceChildren(el('div',{class:'card'},el('p',{class:'msg error',text:e.message})));}
 }

 // ---------------- 录像
 $('v-preset').addEventListener('change',()=>document.querySelectorAll('#v-form .custom').forEach(x=>x.hidden=$('v-preset').value!=='custom'));
 $('v-form').addEventListener('submit',ev=>{ev.preventDefault();loadVideos(true);});
 $('v-more').addEventListener('click',()=>loadVideos(false));
 function range(){const p=$('v-preset').value;if(p==='custom')return [$('v-from').value,$('v-to').value];if(p==='all')return ['',''];if(p==='today')return [day(0),day(0)];return [day(1-Number(p)),day(0)];}
 async function loadVideos(reset){
  if(reset){vOffset=0;$('v-list').replaceChildren();}
  msg('v-msg','查询中…');const [from,to]=range();
  try{const d=await api('/api/videos?'+new URLSearchParams({q:$('v-q').value.trim(),from,to,offset:String(vOffset),limit:'100'}));vOffset+=d.rows.length;
   for(const v of d.rows)$('v-list').append(videoItem(v));
   if(!$('v-list').childElementCount)$('v-list').append(el('div',{class:'empty',text:'没有符合条件的录像'}));
   $('v-more').hidden=!d.more;msg('v-msg','共 '+d.total+' 段，已显示 '+vOffset+' 段');}
  catch(e){msg('v-msg',e.message,true);}
 }
 function videoItem(v){
  return el('button',{type:'button',class:'item',onclick:()=>openVideo(v.id)},
   el('div',{class:'l1'},el('span',{text:v.tracking||'（无物流单号）'}),el('span',{text:fmtDur(v.duration)})),
   el('div',{class:'l2'},tag(VSTATE[v.state]||v.state||'未知',v.state==='saved'?'ok':v.state==='failed'?'bad':'warn'),v.protected?tag('售后保护','warn'):null,v.source?tag(v.source):null,' ',fmtTime(v.at),' · ',fmtSize(v.bytes)),
   el('div',{class:'l2',text:[v.purchase&&('采购 '+v.purchase),v.outer&&('外部 '+v.outer),v.last_sku&&('商品 '+v.last_sku),v.note&&('备注 '+v.note)].filter(Boolean).join(' · ')}));
 }
 function stopVideo(){document.querySelectorAll('video').forEach(v=>{try{v.pause();v.removeAttribute('src');v.load();}catch{}});}
 async function openVideo(id){
  openSheet('加载中…');
  try{const d=await api('/api/videos/detail?'+new URLSearchParams({id})),v=d.video,body=$('detail-body');
   $('detail-title').textContent='录像 · '+(v.tracking||v.id);
   const src='/media/'+encodeURIComponent(v.id)+'.mp4';
   const video=el('video',{controls:'',playsinline:'',preload:'metadata',src,controlslist:d.allow_download?null:'nodownload'});
   video.addEventListener('contextmenu',e=>{if(!d.allow_download)e.preventDefault();});
   video.addEventListener('error',()=>{const p=$('v-player-msg');if(p)p.textContent='播放失败：文件可能仍在录制、已被移动，或同时播放过多（最多'+3+'路）。';});
   body.append(el('div',{class:'card'},video,el('p',{class:'msg',id:'v-player-msg'}),
    d.allow_download?el('div',{class:'row'},el('a',{class:'btn ghost',href:src+'?download=1',download:(v.tracking||v.id)+'.mp4',text:'下载录像'})):el('p',{class:'hint small',text:'电脑端未允许下载，仅可在线播放。'})));
   if(v.cues&&v.cues.length){const ul=el('ul',{class:'cues'});for(const c of v.cues)ul.append(el('li',null,el('button',{type:'button',onclick:()=>{video.currentTime=c.offset;video.play().catch(()=>{});}},el('span',{class:'t',text:fmtDur(c.offset)}),el('span',{text:c.text}))));body.append(el('div',{class:'card'},el('h2',{text:'扫码时间点（点击跳转）'}),ul));}
   body.append(el('div',{class:'card'},el('h2',{text:'录像信息'}),kv([['物流单号',v.tracking],['采购单号',v.purchase],['外部单号',v.outer],['来源',v.source],['最近商品',v.last_sku],['备注',v.note],['状态',VSTATE[v.state]||v.state],['售后保护',v.protected?'是':'否'],['开始',fmtTime(v.at)],['结束',fmtTime(v.end)],['时长',fmtDur(v.duration)],['大小',fmtSize(v.bytes)]])));
  }catch(e){$('detail-body').replaceChildren(el('div',{class:'card'},el('p',{class:'msg error',text:e.message})));}
 }

 // ---------------- 详情页
 function openSheet(title){stopVideo();$('detail-body').replaceChildren();$('detail-title').textContent=title;$('app-view').hidden=true;$('detail-view').hidden=false;window.scrollTo(0,0);history.pushState({detail:true},'');}
 function closeSheet(){stopVideo();$('detail-view').hidden=true;$('detail-body').replaceChildren();if(me)$('app-view').hidden=false;}
 $('detail-back').addEventListener('click',()=>{if(history.state&&history.state.detail)history.back();else closeSheet();});
 window.addEventListener('popstate',()=>{if(!$('detail-view').hidden)closeSheet();});

 async function boot(){
  try{me=await api('/api/me');}catch(e){if(!me)showLogin('');return;}
  showApp();tab(lastView);loadReceipts(true);
 }
 boot();
})();

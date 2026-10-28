(function(){'use strict';
 const pending=new Map();let seq=0;
 const embedded=window.parent!==window, host=embedded?null:window.chrome?.webview;
 if(host)host.addEventListener('message',ev=>{const d=ev.data;if(d?.kind!=='receiving-reply')return;const p=pending.get(d.id);if(!p)return;pending.delete(d.id);clearTimeout(p.timer);d.ok?p.resolve(d.result):p.reject(Error(d.error||'原生操作未完成'));});
 window.NativeDesk={available:!!host,call(action,args={}){if(!host)return Promise.reject(Error('请从收货小助手桌面安装版启动，此功能不在普通浏览器中操作。'));return new Promise((resolve,reject)=>{const id='desk-'+(++seq)+'-'+Date.now();const timer=setTimeout(()=>{pending.delete(id);reject(Error('原生操作超时，请先核实当前状态，不要重复开始录像。'));},120000);pending.set(id,{resolve,reject,timer});host.postMessage({kind:'receiving-command',id,action,args});});}};
 if(embedded)window.NativeDesk={get available(){return !!parent.NativeDesk?.available;},call:(action,args={})=>parent.NativeDesk.call(action,args)};
 document.addEventListener('click',async ev=>{const b=ev.target.closest('[data-native-action]');if(!b)return;ev.preventDefault();if(b.disabled)return;
  const blocked=window.ReceivingApp?.isBusy()||window.ReceivingDesk?.blocked()||document.querySelector('#pending-bar:not([hidden]),#pending:not([hidden]),dialog[open],.qty-saving,.qty-save-error,.qty-unsaved');
  if(blocked&&['export','library','cleanup'].includes(b.dataset.nativeAction)){JstDialog.alert('请先确认或取消商品数量，并保存成功后再切换。');return;}
  b.disabled=true;try{await NativeDesk.call(b.dataset.nativeAction);}catch(e){JstDialog.alert(e.message);}finally{b.disabled=false;}
 });
 for(const type of ['joint','unplanned','express']){
  const url=sessionStorage.getItem('receiving:return:'+type);
  if(url&&new RegExp('^/'+type+'\\.html\\?sid=\\d+&bid=\\d+$').test(url))document.querySelectorAll('a[href="/'+type+'.html"]').forEach(a=>a.href=url);
 }
 document.addEventListener('click',ev=>{const a=ev.target.closest('a[href]');if(!a)return;const url=new URL(a.href,location.href);
  if(url.origin===location.origin&&url.searchParams.get('view')==='settings'&&document.body.dataset.desk)sessionStorage.setItem('receiving:last-work',location.pathname+location.search);
 },true);
})();

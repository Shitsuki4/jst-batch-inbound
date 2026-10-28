/* 公用确认 / 输入 / 提示弹窗：替代浏览器原生 confirm / prompt / alert。
   - 返回 Promise：confirm → true/false，prompt → 文字/null，alert → undefined；在 async 流程里 await 使用。
   - 用 <dialog>.showModal()：焦点进弹窗、背后页面不可操作，Esc = 取消，关闭后焦点回到打开前的位置。
   - 危险操作传 {danger:true, ok:'删除'}：按钮变红，默认焦点放在「取消」上，扫码枪的回车不会误删。
   - 同一时间只开一个，后来的排队。 */
(function(root){'use strict';
 if(root.JstDialog)return;
 const doc=root.document;let queue=Promise.resolve(),seq=0;
 function build(kind,text,opts){
  return new Promise(resolve=>{
   const back=doc.activeElement,id='jst-dlg-'+(++seq);
   const dlg=doc.createElement('dialog');dlg.className='jst-dlg'+(opts.danger?' danger':'');
   if(kind==='alert')dlg.setAttribute('role','alertdialog');
   dlg.setAttribute('aria-labelledby',id+'-t');dlg.setAttribute('aria-describedby',id+'-b');
   const hd=doc.createElement('div');hd.className='jst-dlg-hd';hd.id=id+'-t';
   hd.textContent=opts.title||(kind==='prompt'?'请输入':kind==='alert'?'提示':opts.danger?'确认删除':'请确认');
   const bd=doc.createElement('div');bd.className='jst-dlg-bd';bd.id=id+'-b';bd.textContent=String(text??'');
   let input=null;
   if(kind==='prompt'){input=doc.createElement('input');input.className='jst-dlg-input';input.autocomplete='off';input.value=opts.value??'';input.setAttribute('aria-labelledby',id+'-b');if(opts.placeholder)input.placeholder=opts.placeholder;}
   const ft=doc.createElement('div');ft.className='jst-dlg-ft';
   let cancel=null;
   if(kind!=='alert'){cancel=doc.createElement('button');cancel.type='button';cancel.className='btn btn-ghost';cancel.textContent=opts.cancel||'取消';ft.append(cancel);}
   const ok=doc.createElement('button');ok.type='button';ok.className='btn '+(opts.danger?'btn-danger-solid':'btn-primary');
   ok.textContent=opts.ok||(kind==='alert'?'知道了':opts.danger?'删除':'确定');ft.append(ok);
   dlg.append(hd,bd);if(input)dlg.append(input);dlg.append(ft);doc.body.append(dlg);
   let settled=false;
   function done(v){if(settled)return;settled=true;try{dlg.close();}catch(e){}dlg.remove();try{if(back&&back.isConnected&&back.focus)back.focus();}catch(e){}resolve(v);}
   const yes=()=>done(kind==='prompt'?input.value:kind==='alert'?undefined:true);
   const no=()=>done(kind==='prompt'?null:kind==='alert'?undefined:false);
   ok.onclick=yes;if(cancel)cancel.onclick=no;
   dlg.addEventListener('cancel',ev=>{ev.preventDefault();no();});
   dlg.addEventListener('keydown',ev=>{
    ev.stopPropagation();
    if(ev.key==='Escape'){ev.preventDefault();no();}
    else if(ev.key==='Enter'&&ev.target===input){ev.preventDefault();yes();}
    else if(ev.key==='Tab'){const f=[input,cancel,ok].filter(Boolean),i=f.indexOf(doc.activeElement);ev.preventDefault();f[(i+(ev.shiftKey?f.length-1:1))%f.length].focus();}
   });
   try{dlg.showModal();}catch(e){dlg.setAttribute('open','');}
   const first=input||(opts.danger&&cancel)||ok;first.focus();if(input)input.select();
  });
 }
 function open(kind,text,opts){const run=()=>build(kind,text,opts||{});const p=queue.then(run,run);queue=p.catch(()=>{});return p;}
 root.JstDialog={
  confirm:(text,opts)=>open('confirm',text,opts),
  prompt:(text,value,opts)=>open('prompt',text,Object.assign({value},opts||{})),
  alert:(text,opts)=>open('alert',text,opts),
  isOpen:()=>!!doc.querySelector('dialog.jst-dlg[open]')
 };
})(window);

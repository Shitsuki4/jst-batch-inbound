(()=>{
  const pendingEditor=()=>Boolean(document.querySelector('#pending-bar:not([hidden]),#pending:not([hidden]),dialog[open]'));
  document.addEventListener('click',e=>{if(e.target.closest?.('a[href]')&&pendingEditor()){e.preventDefault();e.stopImmediatePropagation();(window.JstDialog?JstDialog.alert:window.alert)('请先确认或取消当前商品，再切换模块。');}},true);
  const orig=window.fetch.bind(window),pending=new Map();
  let uiQueue=Promise.resolve(),linkFailed=false;
  const shared=window.parent!==window?parent.NativeReceiptLink:null;
  function linkWarning(message){linkFailed=true;if(window.__nativeVideo)window.__nativeVideo.ready=false;uiQueue=uiQueue.catch(()=>{}).then(()=>window.NativeDesk?.available?NativeDesk.call("stop"):undefined).catch(()=>{});(window.JstDialog?JstDialog.alert:window.alert)(message+"。本次业务结果已保存，请勿重复扫码或重复确认；先核对录像，再重新确认物流。");}
  window.addEventListener('native-receipt',ev=>{
    const detail=ev.detail;
    if(shared){parent.dispatchEvent(new parent.CustomEvent('native-receipt',{detail}));return;}
    uiQueue=uiQueue.catch(()=>{}).then(async()=>{
      const response=await orig('/api/native/ui',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(detail)});
      const result=await response.json();if(!result.ok)throw Error(result.msg||'录像事件失败');if(detail.kind==='tracking')linkFailed=false;
    }).catch(err=>{linkFailed=true;if(window.__nativeVideo)window.__nativeVideo.ready=false;(window.JstDialog?JstDialog.alert:window.alert)('录像联动失败：'+err.message+'。请重新确认物流单，核实录像状态。');});
  });
  // 改单号：只有“正在录像的就是这张物流单”时才询问。是 → 发普通 tracking，电脑端保存当前片段并按新单号开新片段；
  // 否 → 当前录像原样继续（不停录、不切段，文件仍按原单号）。后端改号本身不再停录。
  const renameNeedsConfirm=(video,key)=>!!(video&&video.enabled&&video.active&&!video.test&&key&&video.key===key);
  const renameQuestion=(from,to)=>'当前正在录像的物流单号已修改：\n'+from+' → '+to+'\n\n是否按新单号重新录制？\n· 是：保存当前片段，按新单号开始新片段\n· 否：当前录像继续，不中断（文件仍按原单号）';
  async function afterRename(info,ask){
    const to=String(info?.to||'').trim(),from=String(info?.from||'');
    if(!to||to===from||!renameNeedsConfirm(window.__nativeVideo,info?.key))return 'none';
    const yes=await (ask||(text=>window.JstDialog?JstDialog.confirm(text):window.confirm(text)))(renameQuestion(from,to));
    if(!yes)return 'continue';
    window.dispatchEvent(new CustomEvent('native-receipt',{detail:{kind:'tracking',tracking_no:to,key:info.key}}));
    return 'restart';
  }
  window.NativeReceiptLink={idle:()=>uiQueue,failed:()=>linkFailed,renameNeedsConfirm,renameQuestion,afterRename};
  window.fetch=async function(url,opts){
    let commitKey=null,scanAt=null,trackingSubmit=false;
    if(opts&&opts.body&&typeof opts.body==='string'){
      let b;try{b=JSON.parse(opts.body);}catch{}
      if(b){
        const path=new URL(String(url),location.href).pathname,code=b.code||b.sku_code;
        trackingSubmit=path==='/api/tracking/scan'||(['/api/unplanned/action','/api/joint/action','/api/express/action'].includes(path)&&['new','tracking'].includes(b.action)&&!!b.tracking_no);
        if(trackingSubmit&&window.NativeDesk?.available)await NativeDesk.call('validateTracking',{tracking:b.tracking_no});
        const scan=/\/api\/item\/(scan|resolve|choose)(?:[?#]|$)/.test(path)||['scan','resolve'].includes(b.action);
        const resolve=/\/api\/item\/resolve(?:[?#]|$)/.test(path)||b.action==='resolve'||b.peek||b.qty===0;
        const source=path.includes('/unplanned/')?'无采购单':path.includes('/joint/')?'联合':path.includes('/express/')?'快递单':'普通';
        if(code&&scan){
          const key=source+':'+b.sid+':'+b.batch_id+':'+code;
          // Resolve is a fresh scan, even after cancellation. Confirmation/retry
          // must retain that scan time instead of the later quantity-confirm time.
          if(resolve||!pending.has(key))pending.set(key,new Date().toISOString());
          scanAt=pending.get(key);b._native_scan_at=scanAt;
          opts={...opts,body:JSON.stringify(b)};if(!resolve)commitKey=key;
          if(pending.size>500)pending.delete(pending.keys().next().value);
        }
        if(scan||b.action==='copy_previous'){
          await (shared?shared.idle():uiQueue);
          const video=window.__nativeVideo,key=source+':'+b.sid+':'+b.batch_id;
          if(video?.enabled&&(linkFailed||shared?.failed()||!video.ready||video.key!==key)){
            throw Error('本物流录像尚未就绪，'+(b.action==='copy_previous'?'商品复制':'商品扫码')+'未提交。请等待“正在录制”，或明确关闭自动录像后无录像收货。');
          }
        }
      }
    }
    const response=await orig(url,opts);
    if(opts?.body){
      let result;try{result=await response.clone().json();}catch{}
      if(response.ok&&result?.ok){
        if(commitKey&&!result.selection_required&&!result.multi&&pending.get(commitKey)===scanAt)pending.delete(commitKey);
        if(result.native_warning)linkWarning(result.native_warning);
        else if(trackingSubmit&&!result.confirm_required)linkFailed=false;
      }
    }
    return response;
  };
})();

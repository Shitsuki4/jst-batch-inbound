/* All three desks use the same preview. No localStorage/global skip preference. */
(()=>{'use strict';
 let busy=false,dialog=null;
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function confirmCopy(p,label){return new Promise(resolve=>{
  dialog=document.createElement('dialog');dialog.className='receipt-copy-dialog';dialog.style.cssText='width:min(720px,90vw);max-height:85vh;border:1px solid #ccd6e1;border-radius:12px;padding:24px;color:#172b4d';
  dialog.innerHTML='<h2 style="margin-top:0">确认复制上一单商品</h2><p>来源物流单：<strong>'+esc(p.source_tracking)+'</strong><br>复制到当前物流单：<strong>'+esc(p.target_tracking)+'</strong></p><p>仅登记本地实收，不生成入库单、不改变库存。请核对套装和数量。</p><div style="max-height:38vh;overflow:auto"><table style="width:100%;text-align:left"><thead><tr><th>商品 / 编码</th><th>规格</th><th>数量</th></tr></thead><tbody>'+p.lines.map(r=>'<tr><td>'+esc(r.name)+'<div>'+esc(r.sku_id)+(r.sku_code&&r.sku_code!==r.sku_id?' / '+esc(r.sku_code):'')+'</div></td><td>'+esc(r.spec)+'</td><td>'+esc(r.qty)+'</td></tr>').join('')+'</tbody></table></div><p>共 '+p.lines.length+' 种商品 / '+esc(p.total)+' 件</p><label style="display:flex;align-items:center;gap:8px;margin:16px 0"><input type="checkbox" id="copy-dont-prompt" style="width:auto;display:inline-block;margin:0;flex:0 0 auto"> '+esc(label)+'</label><p style="font-size:13px;color:#607080">只跳过本范围的确认窗，不跳过防重复校验。换采购单 / 收货批次会再次提示；当前物流已有商品时不允许复制。</p><div style="display:flex;gap:12px;justify-content:flex-end"><button type="button" class="btn btn-ghost" id="copy-cancel">取消</button><button type="button" class="btn btn-primary" id="copy-confirm">确认复制 '+esc(p.total)+' 件</button></div>';
  const done=value=>{if(!dialog)return;dialog.close();dialog.remove();dialog=null;resolve(value);};
  dialog.addEventListener('cancel',ev=>{ev.preventDefault();done(null);});
  dialog.querySelector('#copy-cancel').onclick=()=>done(null);
  dialog.querySelector('#copy-confirm').onclick=()=>done({dont_prompt:dialog.querySelector('#copy-dont-prompt').checked});
  document.body.append(dialog);dialog.showModal();dialog.querySelector('#copy-cancel').focus();
 });}
 // Also protects the short preview/commit gaps when the modal is not open.
 for(const name of ['click','keydown','submit','change'])document.addEventListener(name,ev=>{if(busy&&(!dialog||!dialog.contains(ev.target))){ev.preventDefault();ev.stopImmediatePropagation();}},true);
 window.ReceiptCopy={isBusy:()=>busy,async run(request,label='本采购单不再提示'){
  if(busy)throw Error('正在复制，请勿重复操作');busy=true;
  try{
   const result=await request('copy_preview',{}),p=result.copy_preview;
   if(!p)throw Error('无法获取上一单预览，未复制');
   const decision=p.skip_confirmation?{dont_prompt:false}:await confirmCopy(p,label);
   if(!decision)return null;
   return await request('copy_previous',{fingerprint:p.fingerprint,confirmed:true,...decision});
  }finally{busy=false;}
 }};
})();

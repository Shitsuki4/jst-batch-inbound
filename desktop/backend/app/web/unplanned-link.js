(function(){
'use strict';
const $=id=>document.getElementById(id),e=UnplannedReport.esc,n=UnplannedReport.num;
let active=null,working=false,preview=null,invoke=null;
function controls(){
 $('po-link-cancel').disabled=working;
 $('po-link-confirm').disabled=working||!preview||!!preview.blockers.length||(preview.cross_scope&&!$('po-link-scope-confirm').checked);
 document.querySelectorAll('[data-po-choice]').forEach(b=>b.disabled=working);
}
function finish(result){if(working)return;$('po-link-dialog').close();const done=active;active=null;preview=null;invoke=null;if(done)done(result);}
async function choose(selection){
 if(working)return;working=true;preview=null;controls();$('po-link-error').textContent='正在重新读取目标采购单并核对实收…';
 $('po-link-preview').innerHTML='';$('po-link-scope-label').hidden=true;$('po-link-scope-confirm').checked=false;
 try{
  const data=await invoke('match_preview',{selection});preview=data.link_preview;
  const p=preview,src=p.catalog_scope,old=p.original;
  $('po-link-preview').innerHTML='<p><b>扫码商品库（保持不变）：</b>货主 '+e(src.owner)+' / 分仓 '+e(src.auth)+'</p>'+
   '<p>原采购关联：'+e(old.po_id||'未关联')+' · 货主 '+e(old.owner)+' / 分仓 '+e(old.auth)+'</p>'+
   '<p><b>目标采购单：'+e(p.head.po_id)+'</b> · 外部单号 '+e(p.head.outer_po_id||'—')+'<br>货主 '+e(p.owner)+' / 分仓 '+e(p.auth)+' · '+e(p.head.supplier||'')+'</p>'+
   '<p>本批次实收 '+n(p.received_total)+' 件。只核对，不改实收、不自动入库；同码不同商品不会自动合并。</p>'+
   '<div class="scroll"><table><thead><tr><th>商品编码</th><th>原实收商品 / 规格 / 条码</th><th>目标采购商品 / 规格 / 条码</th><th>实收 / 采购</th><th>核对</th></tr></thead><tbody>'+p.rows.map(r=>
    '<tr><td>'+e(r.sku_id)+'</td><td>'+e(r.source.name||'—')+'<br>'+e(r.source.spec||'')+'<br>'+e(r.source.sku_code||'')+'</td><td>'+e(r.target.name||'—')+'<br>'+e(r.target.spec||'')+'<br>'+e(r.target.sku_code||'')+'</td><td>'+n(r.received)+' / '+n(r.planned)+'</td><td>'+e(r.issues.join('；')||(r.received===r.planned?'编码数量一致':'数量有差异'))+'</td></tr>').join('')+'</tbody></table></div>';
  $('po-link-scope-label').hidden=!p.cross_scope;
  $('po-link-error').textContent=p.blockers.length?'跨范围商品未核对通过：'+p.blockers.join('；')+'。请核对原实收或目标采购资料后重新查询。':'';
 }catch(err){$('po-link-error').textContent=err.message;}
 finally{working=false;controls();}
}
$('po-link-cancel').onclick=()=>finish(null);
$('po-link-dialog').addEventListener('cancel',ev=>{ev.preventDefault();finish(null);});
$('po-link-scope-confirm').onchange=controls;
$('po-link-confirm').onclick=async()=>{
 if(working||!preview||$('po-link-confirm').disabled)return;
 working=true;controls();
 try{
  const result=await invoke('match_commit',{selection:preview.selection,fingerprint:preview.fingerprint,confirmed:true,confirm_scope:$('po-link-scope-confirm').checked});
  working=false;finish(result);
 }catch(err){preview=null;$('po-link-error').textContent=err.message+' 请重新选择采购单预览。';}
 finally{working=false;controls();}
};
window.UnplannedLink={open(action,data){
 if(active)throw Error('请先关闭当前采购关联预览');
 invoke=action;preview=null;working=false;
 $('po-link-error').textContent='';$('po-link-preview').innerHTML='';$('po-link-scope-label').hidden=true;$('po-link-scope-confirm').checked=false;
 $('po-link-warnings').textContent=(data.warnings||[]).join('；');
 $('po-link-choices').innerHTML=data.link_candidates.map((c,i)=>'<button type="button" data-po-choice="'+i+'">采购单 '+e(c.head.po_id)+' / 外部单号 '+e(c.head.outer_po_id||'—')+' · 货主 '+e(c.owner)+' / 分仓 '+e(c.auth)+'</button>').join('');
 $('po-link-choices').onclick=ev=>{const b=ev.target.closest('[data-po-choice]');if(b)choose(data.link_candidates[Number(b.dataset.poChoice)].selection);};
 const result=new Promise(resolve=>{active=resolve;});controls();$('po-link-dialog').showModal();
 if(data.link_candidates.length===1)choose(data.link_candidates[0].selection);
 return result;
}};
})();

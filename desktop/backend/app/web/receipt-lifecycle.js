/* Shared local close/delete controls. No ERP or media deletion. */
(function(){'use strict';
 const $=id=>document.getElementById(id);
 function paint(state){
  const closed=state.session.state==='closed',locked=closed||!!state.locked;
  for(const id of ['tracking','code','enter-tracking','scan-tracking','scan','step','qty','confirm-qty','rename','copy-previous','delete-batch','manual','finish-package','note','save-note','desk-repair-context','desk-mode-tracking','desk-mode-item'])if($(id))$(id).disabled=locked;
  $('lines').querySelectorAll('button').forEach(b=>b.disabled=locked);
  $('close-session').disabled=closed;
  $('delete-session').disabled=!!(state.jobs?.length||state.stages?.some(s=>s.jobs.length));
  $('lifecycle-status').textContent=closed?'已结单 · '+(state.session.ended_at||'')+' · 实收只读。后补采购单、核对、入库仍需分别确认；本次结单未自动入库。':'收货中 · 结单仅结束本批次扫码，不自动入库。';
 }
 function bind({run,idle,getState,action,apply,clear,report,notify}){
  $('close-session').onclick=()=>run(async()=>{
   idle();const s=getState();if(!s||s.session.state==='closed')return;
   const text='批次：'+s.session.input_no+'\n物流单：'+s.batches.length+' 个\n实收：'+s.total+' 件\n\n结单后不能继续扫码或修改本批次实收。\n不会自动入库；后补采购单、核对与原入库单流程保留。\n确认结单并查看全部物流的汇总？';
   if(!confirm(text))return;
   const result=await action('close_session',{confirmed:true,expected_session_no:s.session.input_no});
   await apply(result);window.dispatchEvent(new CustomEvent('native-receipt',{detail:{kind:'stop'}}));report();notify('已结单，实收已锁定；未自动生成入库单或确认库存');
  });
  $('delete-session').onclick=()=>run(async()=>{
   idle();const s=getState();if(!s)return;
   if(window.DeskUI?.confirmDelete){if(!await window.DeskUI.confirmDelete(s)){notify('已取消，未删除');return;}}
   else{
   if(!confirm('删除整个本地收货批次？\n批次：'+s.session.input_no+'\n包含 '+s.batches.length+' 个物流单，实收 '+s.total+' 件。\n\n本操作不可撤销，不删除录像、不修改ERP采购单和库存。\n存在入库记录（含失败 / 待核实记录）时禁止删除。'))return;
   if(prompt('再次确认：请输入要删除的完整批次号', '')!==s.session.input_no){notify('批次号未确认，未删除');return;}
   }
   const result=await action('delete_session',{confirmed:true,expected_session_no:s.session.input_no});
   if(!result.deleted)throw Error('未确认删除结果，请刷新核对，勿重复操作');
   await clear();window.dispatchEvent(new CustomEvent('native-receipt',{detail:{kind:'stop'}}));notify(result.message);
  });
 }
 window.ReceiptLifecycle={paint,bind};
})();

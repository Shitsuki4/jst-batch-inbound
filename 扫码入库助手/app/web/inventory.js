/* 盘点是独立模块，不复用收货累计数量，也不读取商品缓存中的库存。 */
(function () {
  'use strict';
  var root = document.getElementById('inventory-view'), busy = false, rows = [], selected = null, preview = null;
  var identity = '';
  function $(id) { return document.getElementById('iv-' + id); }
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  async function api(path, body) {
    var r = await fetch('/api/inventory/' + path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    var j = await r.json(); if (!j.ok) throw new Error(j.msg || '接口调用失败'); return j;
  }
  function updateScope() { if($("scope")) $("scope").textContent="当前操作："+scopeLabel(); }
  function sound(error) { if($("sound") && $("sound").checked && window.jstSound) window.jstSound(error); }
  function message(s, error) { updateScope(); if(error) sound(true); $('message').textContent = s; $('message').className = error ? 'inbound-warning' : 'hint'; }
  function setBusy(v) { busy = v; root.querySelectorAll('fieldset').forEach(function(e){ e.disabled = v; }); $('history-refresh').disabled = v; root.querySelectorAll('[data-verify]').forEach(function(e){e.disabled=v;}); }
  async function task(fn) { if (busy) return; setBusy(true); try { await fn(); } catch(e) { message(e.message || '网络请求失败，请核对操作记录。', true); } finally { setBusy(false); if(root.dataset.nextScan === "1") {delete root.dataset.nextScan;$("code").focus();} } }
  function discardPreview() { preview=null; $('preview').hidden=true; $('preview').innerHTML=''; }
  function clearRows() { updateScope(); rows=[]; selected=null; discardPreview(); $('editor').hidden=true; $('results').innerHTML='<p class="empty">请查询实时库存。</p>'; }
  function params() { return new URLSearchParams({owner:$('owner').value,auth:$('auth').value,wh_id:$('wh').value,location:$('location').value,code:$('code').value.trim(),code_type:$('code-type').value}); }
  function scopeLabel() { return ['owner','auth','wh','location'].map(function(id){ var e=$(id); return e.options[e.selectedIndex] ? e.options[e.selectedIndex].text : ''; }).join(' / '); }
  function options(id, data, label, previous) { $(id).innerHTML='<option value="">'+esc(label)+'</option>'+data.map(function(r){ return '<option value="'+esc(r.id)+'">'+esc(r.name)+' ('+esc(r.id)+')</option>'; }).join(''); $(id).value=previous || ''; }
  async function loadConfig() {
    clearRows(); options('wh', [], '请选择库存仓库', '');$('location').innerHTML='<option value="all">全部位置（含暂存位）</option>';
    if (!$('owner').value || !$('auth').value) return;
    var r=await api('config?'+params()); options('wh', r.warehouses, '请选择库存仓库', '');$('location').innerHTML=(r.locations||[{id:'all',name:'全部位置（含暂存位）'}]).map(function(l){return '<option value="'+esc(l.id)+'">'+esc(l.name)+'</option>';}).join('');
    message('请选择具体仓库。当前数量规则：'+(r.decimal ? '最多4位小数' : '整数')+'。', false);
  }
  async function history() {
    var r=await api('history');
    var labels={verified:'当前数量已核对',unknown:'结果待核实，禁止重发',sending:'提交中／待核实',rejected:'ERP明确拒绝',stale:'已失效，未写入'};
    $('history').innerHTML=r.rows.length ? r.rows.map(function(j){var s=j.snapshot;
      return '<div class="inbound-job"><b>'+esc(s.sku_id)+' · '+esc(labels[j.status] || j.status)+'</b> <span class="chip">'+esc(s.old_qty)+' → '+esc(s.target_qty)+'</span>'
        +'<p>货主 '+esc(s.owner)+' / 仓库公司 '+esc(s.auth)+' / '+esc(s.warehouse)+' · 仓位 '+esc(s.position || s.bin || '暂存位')+' · 明细 '+esc(s.pack_item_id)+'</p>'
        +'<small>'+esc(new Date(j.created*1000).toLocaleString())+' · '+esc(s.mode==='delta' ? '增减 '+s.value : '直接设为 '+s.value)+'</small>'
        +(j.error ? '<p>'+esc(j.error)+'</p>' : '')
        +(['sending','unknown'].includes(j.status) ? '<button class="btn btn-ghost" data-verify="'+esc(j.token)+'">仅回读核实（不重发）</button>':'')+'</div>';
    }).join('') : '<p class="empty">暂无盘点操作记录。</p>';
  }
  async function boot() {
    clearRows(); message('正在读取货主和仓库公司…');
    var oldOwner=$('owner').value,oldAuth=$('auth').value;
    var p=await api('partners');
    if(identity !== p.identity) {oldOwner='';oldAuth='';identity=p.identity;}
    options('owner',p.owners,'请选择货主',oldOwner); options('auth',p.wms,'请选择仓库公司',oldAuth);
    options('wh', [], '先选择货主及仓库公司','');
    if(oldOwner && oldAuth) await loadConfig(); else message('请明确选择货主及仓库公司，不使用所有商家。');
    await history();
  }
  async function lookup() {
    clearRows();
    if (!$('owner').value || !$('auth').value || !$('wh').value || !$('code').value.trim()) throw new Error('请选齐货主、仓库公司、库存仓库并输入编码。');
    message('正在查询实时库存…'); var r=await api('lookup?'+params()); rows=r.rows;
    $('results').innerHTML=rows.length ? '<table class="tbl"><thead><tr><th>选择</th><th>商品</th><th>所在仓库 / 仓位 / 批次</th><th>当前数量</th><th>冻结</th><th>库存明细ID</th></tr></thead><tbody>'
      +rows.map(function(r,i){return '<tr><td><input type="radio" name="iv-row" aria-label="选择库存明细 '+esc(r.pack_item_id)+'" value="'+i+'"'+(r.editable?'':' disabled')+'></td><td><b>'+esc(r.sku_id)+'</b><br>'+esc(r.name)+'<br><small>'+esc(r.properties_value)+'</small></td>'
        +'<td>'+esc(r.wh_id)+' / '+esc(r.bin || r.pack_id || r.pack_type || '暂存位')+'<br><small>箱 '+esc(r.pack_id || r.sys_pack_id)+' · 批次 '+esc(r.pb_id || '—')+'</small>'+(r.editable?'':'<br>该仓位不可修改')+'</td><td><b>'+esc(r.qty)+'</b></td><td>'+esc(r.freeze_qty)+'</td><td>'+esc(r.pack_item_id)+'</td></tr>';}).join('')+'</tbody></table>'
      : '<p class="empty">未找到该仓库的精确编码库存明细。零库存记录可能被 ERP 隐藏；此处不创建新的库存明细。</p>';
    message(rows.length ? '已读取 '+rows.length+' 条库存明细，请明确选择一条后操作。'+(rows.length>1?'同商品多仓位不自动合并。':'') : '查询完成，无匹配明细。');
  }
  function choose(i) {
    discardPreview(); selected=rows[i]; if(!selected || !selected.editable) return;
    $('editor').hidden=false; if(!($('continuous') && $('continuous').checked && $('mode').value==='delta')) $('value').value='';
    $('selected').textContent=scopeLabel()+' · '+selected.sku_id+' · '+(selected.bin || selected.pack_id || selected.pack_type || '暂存位')+' · 库存明细 '+selected.pack_item_id+' · 当前数量 '+selected.qty;
    $('value').focus();
  }
  async function makePreview() {
    discardPreview(); if(!selected) throw new Error('请先选择库存明细');
    var b=Object.fromEntries(params()); b.pack_item_id=String(selected.pack_item_id);b.expected_qty=selected.qty;b.mode=$('mode').value;b.value=$('value').value.trim();
    var r=await api('preview',b);preview=r.job; var s=preview.snapshot;
    $('preview').innerHTML='<h3>确认盘点</h3><p>'+esc(scopeLabel())+'</p><p>商品 <b>'+esc(s.sku_id)+'</b> · 仓位 '+esc(s.position || s.bin || '暂存位')+' · 明细 '+esc(s.pack_item_id)+'</p>'
      +'<p class="inventory-change">'+esc(s.old_qty)+' → <b>'+esc(s.target_qty)+'</b></p><p>'+esc(s.mode==='delta'?'增减 '+s.value:'直接改为 '+s.value)+'。确认后将直接修改 ERP 库存。</p>'
      +'<button type="button" class="btn btn-primary" id="iv-confirm">确认修改这条库存</button> <button type="button" class="btn btn-ghost" id="iv-cancel">取消</button>';
    $('preview').hidden=false; message('请核对货主、仓库、商品及目标数量后确认。');
  }
  async function submit() {
    if(!preview) throw new Error('请重新预览'); var token=preview.token;discardPreview();
    message('正在提交并回读核对，请勿重复操作或关闭程序…');
    try {
      var r=await api('submit',{token:token,confirmed:true}); var j=r.job;
      clearRows(); if(j.status==='verified'){sound(false);if($('continuous') && $('continuous').checked){$('code').value='';if($('mode').value!=='delta')$('value').value='';root.dataset.nextScan='1';}} message(j.status==='verified'?'已回读确认目标数量。请查询下一件商品。':(j.error || '操作未完成，请查看记录'),j.status!=='verified');
    } catch(e) {
      clearRows();message('请求中断或未完成：'+e.message+'。请查看记录；结果不明时不要重新增减。',true);
    }
    await history();
  }
  $('owner').onchange=$('auth').onchange=function(){task(loadConfig);};
  $('location').onchange=$('wh').onchange=function(){clearRows();updateScope();$('code').focus();};$('code').oninput=$('code-type').onchange=clearRows;
  $('mode').onchange=function(){discardPreview();$('value').value='';};$('value').oninput=discardPreview;
  $('plus').onclick=function(){discardPreview();$('mode').value='delta';$('value').value='1';$('value').focus();};
  $('minus').onclick=function(){discardPreview();$('mode').value='delta';$('value').value='-1';$('value').focus();};
  $('search').onsubmit=function(e){e.preventDefault();task(lookup);};
  $('edit').onsubmit=function(e){e.preventDefault();task(makePreview);};
  $('refresh').onclick=function(){task(function(){return boot();});};
  $('history-refresh').onclick=function(){task(history);};
  $('results').onchange=function(e){if(e.target.name==='iv-row')choose(+e.target.value);};
  $('preview').onclick=function(e){if(e.target.id==='iv-confirm')task(submit);if(e.target.id==='iv-cancel')discardPreview();};
  $('history').onclick=function(e){var el=e.target.closest('[data-verify]');if(el) task(async function(){var r=await api('verify',{token:el.dataset.verify});message(r.job.status==='verified'?'回读数量已核对。':r.job.error,r.job.status!=='verified');await history();});};
  window.bootInventory=function(){task(boot);};
  document.addEventListener('click',function(e){var el=e.target.closest('[data-tab="inventory"]');if(el)task(function(){return boot();});});
}());

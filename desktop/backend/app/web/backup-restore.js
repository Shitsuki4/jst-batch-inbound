// 备份恢复：放在「安全备份」卡片内部。预览 → 输入「恢复」确认 → 恢复前自动备份 → 重启后生效，失败自动回滚。
(function(){'use strict';
 var root=document.getElementById('backup-restore');if(!root)return;
 var busy=false,selected=null,lastBlockers=[];
 function $(id){return document.getElementById(id);}
 function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
 function native(){return !!(window.NativeDesk&&NativeDesk.available);}
 async function api(p,b){var r=await fetch('/api/'+p,b===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});var j=await r.json();if(!j.ok)throw Error(j.msg||'操作失败');return j;}
 function msg(t,bad){var m=$('restore-msg');m.textContent=t||'';m.style.color=bad?'var(--danger)':'';}
 function size(n){n=Number(n)||0;return n>1048576?(n/1048576).toFixed(1)+' MB':(n/1024).toFixed(0)+' KB';}
 function param(s){return s.id?'id='+encodeURIComponent(s.id):'path='+encodeURIComponent(s.path);}
 async function run(fn){if(busy)return;busy=true;paint();try{await fn();}catch(e){msg(e.message,true);}finally{busy=false;paint();}}
 function paint(){var c=$('restore-confirm'),go=$('restore-go');
  $('restore-selected').textContent=selected?('已选择：'+selected.label):'尚未选择备份';
  go.disabled=busy||!selected||!selected.previewed||c.value.trim()!=='恢复'||lastBlockers.length>0;
  root.querySelectorAll('button[data-restore-preview],#restore-file,#restore-zip,#restore-refresh,#restore-cancel').forEach(function(b){b.disabled=busy;});
 }
 root.innerHTML='<div class="card-hd" style="margin-top:14px">备份恢复</div>'+
  '<p class="hint">恢复会用所选备份<b>替换当前收货数据</b>。恢复前会自动再做一份「恢复前自动备份」；替换在重启时进行，任何一步失败都会自动回滚到恢复前的数据。正在录像、有未完成入库或PDA服务运行时不能恢复。</p>'+
  '<div class="row"><button class="btn btn-ghost" id="restore-refresh" type="button">刷新备份列表</button><button class="btn btn-ghost" id="restore-file" type="button">从文件夹恢复</button><button class="btn btn-ghost" id="restore-zip" type="button">从压缩包恢复</button></div>'+
  '<div id="restore-pending" class="inbound-warning" hidden></div>'+
  '<div id="restore-blockers" class="inbound-warning" hidden></div>'+
  '<div class="table-wrap" id="restore-list"><p class="empty">点击「刷新备份列表」查看可恢复的备份。</p></div>'+
  '<div id="restore-preview"></div>'+
  '<p id="restore-selected" class="hint"></p>'+
  '<div class="row"><input class="input" id="restore-confirm" autocomplete="off" placeholder="输入「恢复」两字确认" aria-label="输入恢复确认"><button class="btn btn-primary danger" id="restore-go" type="button" disabled>恢复所选备份</button></div>'+
  '<p id="restore-msg" class="hint" role="status" aria-live="polite"></p>';
 function blockers(list){lastBlockers=list||[];var b=$('restore-blockers');b.hidden=!lastBlockers.length;b.textContent=lastBlockers.length?'当前不能恢复：'+lastBlockers.join('；'):'';}
 function pending(on){var p=$('restore-pending');p.hidden=!on;p.innerHTML=on?'已有一份恢复等待重启生效。'+(native()?'':'请关闭后重新打开收货小助手完成恢复。')+' <button class="btn btn-ghost" id="restore-cancel" type="button">取消待恢复</button>':'';}
 async function load(){var j=await api('restore/list');pending(j.pending);blockers(j.blockers);
  $('restore-list').innerHTML=j.backups.length?'<table><thead><tr><th>时间</th><th>类型</th><th>版本</th><th>大小</th><th>完整性</th><th></th></tr></thead><tbody>'+j.backups.map(function(b){var id=b.directory.split(/[\\/]/).pop();return '<tr><td>'+esc(b.time)+'</td><td>'+esc(b.reason_label)+'</td><td>'+esc(b.version)+'</td><td>'+size(b.size)+'</td><td>'+(b.complete?'完整':'<span style="color:var(--danger)">未完成</span>')+'</td><td><button class="btn btn-ghost" type="button" data-restore-preview="'+esc(id)+'" data-label="'+esc(b.time+' · '+b.reason_label)+'"'+(b.complete?'':' disabled')+'>预览</button></td></tr>';}).join('')+'</tbody></table>':'<p class="empty">暂无备份。</p>';
 }
 function countsTable(dbs){var rows=[];Object.keys(dbs||{}).forEach(function(n){var d=dbs[n];var names={};Object.keys(d.backup||{}).concat(Object.keys(d.current||{})).forEach(function(t){names[t]=1;});
   if(d.kept_current){rows.push('<tr><td>'+esc(n)+'</td><td colspan="3">备份中没有此库，保留当前数据</td></tr>');return;}
   if(!d.in_backup){rows.push('<tr><td>'+esc(n)+'</td><td colspan="3" style="color:var(--danger)">备份中没有此库，恢复后当前数据将被移走</td></tr>');return;}
   Object.keys(names).sort().forEach(function(t){var b=d.backup&&d.backup[t],c=d.current&&d.current[t];if(!b&&!c)return;rows.push('<tr><td>'+esc(n)+' / '+esc(t)+'</td><td>'+(b==null?'—':b)+'</td><td>'+(c==null?'—':c)+'</td><td>'+(b!=null&&c!=null&&b<c?'<span style="color:var(--danger)">少 '+(c-b)+'</span>':'')+'</td></tr>');});});
  return rows.length?'<div class="table-wrap"><table><thead><tr><th>数据表</th><th>备份行数</th><th>当前行数</th><th>差异</th></tr></thead><tbody>'+rows.join('')+'</tbody></table></div>':'';}
 async function preview(sel){selected=null;$('restore-preview').innerHTML='';$('restore-confirm').value='';msg('正在校验并预览…');
  var j=await api('restore/preview?'+param(sel));blockers(j.blockers);var h='';
  if(!j.verify.ok){$('restore-preview').innerHTML='<p class="inbound-warning">备份校验未通过，不能恢复：'+esc(j.verify.errors.join('；'))+'</p>';msg('');return;}
  if(j.preview){var miss=j.preview.missing_inbound_io_ids||[];
   if(miss.length)h+='<div class="inbound-warning" style="color:var(--danger);border-color:var(--danger)"><b>警告：恢复后将丢失 '+miss.length+' 条已生成的入库记录</b>（ERP入库单号：'+miss.map(esc).join('、')+'）。这些单据在聚水潭中仍然存在，恢复后本软件不再知道它们已入库，<b>有重复入库的风险</b>。恢复后请先在聚水潭核对，不要重复提交。</div>';
   h+=countsTable(j.preview.dbs);}
  else h+='<p class="hint">压缩包会在恢复时完整校验；暂不显示行数对比。</p>';
  $('restore-preview').innerHTML=h;sel.previewed=true;selected=sel;msg('请核对上方内容；确认无误后输入「恢复」并点击恢复。');
 }
 async function choose(kind){if(!native()){msg('从文件恢复仅在桌面安装版中可用。',true);return;}var r=await NativeDesk.call('chooseBackup',{kind:kind});if(!r||!r.path)return;await preview({path:r.path,label:r.path});}
 async function restore(){var sel=selected;if(!sel)return;var confirmText=$('restore-confirm').value.trim();if(confirmText!=='恢复')throw Error('请输入「恢复」两字确认');
  if(!await JstDialog.confirm('确认用「'+sel.label+'」替换当前收货数据？\n恢复前会自动备份当前数据，失败会自动回滚。'))return;
  msg('正在做恢复前自动备份并校验所选备份，请勿关闭程序…');
  if(native()){var r=await NativeDesk.call('restoreBackup',sel.id?{id:sel.id,confirm:confirmText}:{path:sel.path,confirm:confirmText});msg(r&&r.message||'已准备恢复，程序将自动重新打开。');return;}
  var j=await api('restore/stage',sel.id?{id:sel.id,confirm:confirmText,recording:false}:{path:sel.path,confirm:confirmText,recording:false});
  msg('已完成'+(j.pre_restore_label||'恢复前自动备份')+'并准备恢复。请关闭后重新打开收货小助手完成恢复。');selected=null;$('restore-confirm').value='';await load();
 }
 async function result(){var j=await api('restore/result');var r=j.result;if(!r)return;var box=document.createElement('div');box.className='inbound-warning';box.setAttribute('role','alert');
  var text=r.ok?'备份恢复已完成：已恢复 '+(r.restored||[]).join('、')+(r.kept_current&&r.kept_current.length?'；保留当前 '+r.kept_current.join('、'):'')+'。恢复前的数据已另存（'+(r.rollback||'')+'）。请在聚水潭核对最近的入库单，避免重复入库。'
   :r.status==='recovered'?'上次恢复中断，已自动回滚到恢复前的数据。':r.status==='rejected'?'备份校验未通过，未恢复，当前数据未改动：'+(r.error||''):'恢复失败，已自动回滚到恢复前的数据：'+(r.error||'');
  if(!r.ok)box.style.color='var(--danger)';box.innerHTML=esc(text)+' <button class="btn btn-ghost" type="button">知道了</button>';box.querySelector('button').onclick=function(){box.remove();};
  var main=document.querySelector('main')||document.body;main.insertBefore(box,main.firstChild);}
 root.addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;
  if(b.id==='restore-refresh')run(load);else if(b.id==='restore-file')run(function(){return choose('folder');});else if(b.id==='restore-zip')run(function(){return choose('zip');});
  else if(b.id==='restore-go')run(restore);else if(b.id==='restore-cancel')run(async function(){await api('restore/cancel',{});msg('已取消待恢复，当前数据不变。');await load();});
  else if(b.dataset.restorePreview)run(function(){return preview({id:b.dataset.restorePreview,label:b.dataset.label});});});
 $('restore-confirm').addEventListener('input',paint);
 document.addEventListener('click',function(e){if(e.target.closest('[data-tab="settings"]'))run(load);});
 paint();result().catch(function(){});
}());

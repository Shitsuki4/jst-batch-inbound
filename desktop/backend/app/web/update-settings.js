/* 设置 → 软件更新。只调用本机 /api/update/*（联网在后台线程）；安装交给 NativeDesk.applyUpdate（原生端以真实录像状态调用 prepare 并复核 SHA256）。
   顶部横幅：发现新版本时显示，每个版本关闭一次后不再出现。 */
(function(){'use strict';
 const pane=document.querySelector('[data-settings-pane="update"]');
 const $=id=>document.getElementById(id);
 const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const DISMISS_KEY='receiving:update-banner-dismissed';
 const native=()=>!!(window.NativeDesk&&NativeDesk.available);
 let last=null,timer=null,installing=false,refreshPane=null;
 async function get(){const r=await fetch('/api/update/status',{cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.msg||'读取更新状态失败');return d;}
 async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.msg||'操作失败');return d;}
 const fmtSize=n=>!n?'':n>=1048576?(n/1048576).toFixed(1)+' MB':(n/1024).toFixed(0)+' KB';
 const fmtTime=t=>t?new Date(t*1000).toLocaleString():'从未';

 if(pane){
  pane.innerHTML=`<div class="card"><div class="card-hd">软件更新</div>
  <div id="update-state" class="video-state" role="status">正在读取更新状态…</div>
  <div class="form-row"><label for="update-channel">下载渠道</label><select class="select" id="update-channel" aria-label="下载渠道"><option value="github">GitHub 原链接</option><option value="proxy">加速站 gh-proxy.com</option></select></div>
  <p class="hint">国内网络访问 GitHub 较慢或失败时，请切换到加速站。两个渠道下载同一个发布文件，下载后都会按 SHA256SUMS.txt 校验。</p>
  <div class="form-row"><label>自动检查</label><label class="setting-switch"><input type="checkbox" id="update-auto"> 每天自动检查一次新版本（只检查，不自动下载）</label></div>
  <div class="form-row"><label>当前版本</label><span id="update-current">—</span></div>
  <div class="form-row"><label>最新版本</label><span id="update-latest">—</span></div>
  <details class="settings-advanced" id="update-notes-box" hidden><summary>更新说明</summary><pre id="update-notes" style="white-space:pre-wrap;font:inherit;margin:10px 0 0;max-height:320px;overflow:auto"></pre></details>
  <div id="update-progress-box" hidden style="margin:12px 0"><div style="height:10px;background:#e3eaf0;border-radius:6px;overflow:hidden"><div id="update-progress" style="height:100%;width:0;background:#0f8a8f;transition:width .3s"></div></div><p class="hint" id="update-progress-text"></p></div>
  <div class="row"><button class="btn btn-ghost" id="update-check">检查更新</button><button class="btn btn-primary" id="update-install" disabled>下载并安装</button><button class="btn btn-ghost" id="update-cancel" hidden>取消下载</button><button class="btn btn-ghost" id="update-release">打开发布页</button></div>
  <p class="hint" id="update-message" role="status"></p>
  <p class="hint">正在录像或入库中时不能安装；安装前会安全退出程序。便携版会解压到新文件夹，旧文件夹中的数据不会自动迁移。</p></div>`;

  const msg=(t,cls)=>{const m=$('update-message');m.textContent=t||'';m.className='hint'+(cls?' '+cls:'');};
  function paint(d){
   last=d;const dl=d.download||{},latest=d.latest;
   $('update-channel').value=d.channel||'github';$('update-auto').checked=!!d.auto_check;
   $('update-current').textContent=(d.current_version||'未知')+' · '+(d.install_mode_label||'');
   $('update-latest').textContent=latest?(latest.version+(latest.published_at?' · 发布于 '+latest.published_at.slice(0,10):'')+(d.asset&&d.asset.size?' · '+fmtSize(d.asset.size):'')):(d.checking?'检查中…':'尚未获取');
   $('update-notes-box').hidden=!(latest&&latest.notes);$('update-notes').textContent=latest&&latest.notes||'';
   let state;
   if(d.checking)state='正在检查新版本…';
   else if(d.check_error)state='检查失败：'+d.check_error+'（上次成功检查：'+fmtTime(d.last_checked)+'）';
   else if(d.update_available)state='发现新版本 '+latest.version+'，可以下载并安装';
   else state=latest?'已是最新版本（上次检查：'+fmtTime(d.last_checked)+'）':'尚未检查';
   if(d.install_mode!=='installed'&&d.install_mode!=='portable')state+=' · 开发 / 源码运行，不能自动安装';
   $('update-state').textContent=state;
   const busy=dl.state==='downloading'||dl.state==='verifying';
   $('update-progress-box').hidden=!(busy||dl.state==='verified'||dl.state==='failed'||dl.state==='cancelled');
   const pct=dl.total?Math.min(100,Math.floor(dl.received*100/dl.total)):(dl.state==='verified'?100:0);
   $('update-progress').style.width=pct+'%';
   $('update-progress-text').textContent=dl.state==='downloading'?'正在下载 '+(dl.name||'')+' · '+pct+'%（'+fmtSize(dl.received)+' / '+fmtSize(dl.total)+'）'+(dl.attempt?' · 第 '+(dl.attempt+1)+' 次尝试':''):dl.state==='verifying'?'正在校验 SHA256…':dl.state==='verified'?'已下载并通过 SHA256 校验：'+(dl.name||''):dl.state==='failed'?'下载失败：'+(dl.error||''):dl.state==='cancelled'?(dl.error||'已取消下载'):'';
   $('update-progress-text').className='hint'+(dl.state==='failed'?' err':dl.state==='verified'?' ok':'');
   if(dl.state==='failed'){const other=dl.suggest_channel||(d.channel==='proxy'?'github':'proxy');msg('下载失败。可尝试切换到「'+(other==='proxy'?'加速站 gh-proxy.com':'GitHub 原链接')+'」后重试。','err');}
   $('update-install').disabled=installing||busy||d.checking||!d.update_available||(d.install_mode!=='installed'&&d.install_mode!=='portable');
   $('update-install').textContent=dl.state==='verified'&&latest&&dl.version===latest.version?'安装更新':'下载并安装';
   $('update-cancel').hidden=!busy;$('update-check').disabled=d.checking||busy;
   banner(d);
   if(busy||d.checking)schedule(1000);
  }
  function schedule(ms){clearTimeout(timer);timer=setTimeout(refresh,ms);}
  async function refresh(){timer=null;try{const d=await get();paint(d);if(installing)continueInstall(d);}catch(e){$('update-state').textContent='读取失败：'+e.message;}}
  refreshPane=refresh;
  async function continueInstall(d){
   const dl=d.download||{};
   if(dl.state==='downloading'||dl.state==='verifying')return;
   if(dl.state!=='verified'){installing=false;paint(d);return;}
   installing=false;
   if(!native()){msg('已下载并校验。自动安装需要从收货小助手桌面版启动；也可点「打开发布页」手动下载。','err');paint(d);return;}
   try{msg('正在准备安装，程序将安全退出并启动更新…');await NativeDesk.call('applyUpdate',{});msg('即将退出并安装更新…','ok');}
   catch(e){msg('无法安装：'+e.message,'err');paint(d);}
  }
  $('update-channel').onchange=async e=>{try{paint(await post('/api/update/settings',{channel:e.target.value}));msg('已切换下载渠道','ok');}catch(err){msg(err.message,'err');refresh();}};
  $('update-auto').onchange=async e=>{try{paint(await post('/api/update/settings',{auto_check:e.target.checked}));msg(e.target.checked?'已开启每日自动检查':'已关闭自动检查','ok');}catch(err){msg(err.message,'err');refresh();}};
  $('update-check').onclick=async()=>{msg('');try{paint(await post('/api/update/check'));schedule(800);}catch(e){msg(e.message,'err');}};
  $('update-install').onclick=async()=>{
   if(!last||!last.update_available)return;
   if(!confirm('下载并安装 '+last.latest.version+'？\n安装前程序会安全退出；正在录像或入库中时会被拒绝。'))return;
   installing=true;msg('');
   try{const dl=last.download||{};if(!(dl.state==='verified'&&dl.version===last.latest.version))paint(await post('/api/update/download',{channel:$('update-channel').value}));await refresh();if(installing)schedule(1000);}
   catch(e){installing=false;msg(e.message,'err');refresh();}
  };
  $('update-cancel').onclick=async()=>{installing=false;try{paint(await post('/api/update/cancel'));}catch(e){msg(e.message,'err');}};
  $('update-release').onclick=async()=>{
   const links=last&&last.links&&last.links.release,ch=$('update-channel').value;
   const url=links&&links[ch]||'https://github.com/Shitsuki4/jst-batch-inbound/releases';
   if(native()){try{await NativeDesk.call('openUrl',{url});}catch(e){msg(e.message,'err');}}else window.open(url,'_blank','noopener');
  };
 }

 /* 顶部横幅：新版本出现时提示；关闭后同一版本不再提示。 */
 function banner(d){
  const v=d&&d.update_available&&d.latest&&d.latest.version;let el=$('update-banner');
  let dismissed='';try{dismissed=localStorage.getItem(DISMISS_KEY)||'';}catch{}
  if(!v||dismissed===v){if(el)el.remove();return;}
  if(el&&el.dataset.version===v)return;
  if(el)el.remove();
  el=document.createElement('div');el.id='update-banner';el.dataset.version=v;el.setAttribute('role','status');
  el.style.cssText='display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:9px 16px;background:#e7f5f3;color:#0b5f66;border-bottom:1px solid #c5e4e0;font-size:13px';
  el.innerHTML='<span>发现新版本 <b>'+esc(v)+'</b>（当前 '+esc(d.current_version||'未知')+'）</span><span style="display:flex;gap:8px"><button class="btn btn-primary btn-sm" data-update-open>查看更新</button><button class="btn btn-ghost btn-sm" data-update-dismiss aria-label="关闭更新提示">不再提示此版本</button></span>';
  el.querySelector('[data-update-open]').onclick=()=>{if(window.ReceivingApp&&ReceivingApp.navigate)ReceivingApp.navigate('settings');if(window.SettingsWorkspace)SettingsWorkspace.show('update');};
  el.querySelector('[data-update-dismiss]').onclick=()=>{try{localStorage.setItem(DISMISS_KEY,v);}catch{}el.remove();};
  document.body.insertBefore(el,document.body.firstChild);
 }

 /* 首次读取 + 每 30 分钟刷新一次（后台每天最多联网检查一次，这里只读本机状态）。 */
 const tick=()=>{if(pane)refreshPane();else get().then(banner,()=>{});};
 tick();setInterval(()=>{if(!timer||!pane)tick();},30*60*1000);
})();

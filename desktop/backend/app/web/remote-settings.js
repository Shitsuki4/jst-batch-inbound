/* 设置 → 远程管理页（IPv6 直连 / cpolar 穿透）。只调用本机 /api/remote/*；密码只提交给本机服务，本机仅保存加盐哈希。 */
(function(){'use strict';
 const pane=document.querySelector('[data-settings-pane="remote"]');if(!pane)return;
 const $=id=>document.getElementById(id);
 pane.innerHTML=`<div class="card"><div class="card-hd">远程管理页（只读）</div>
 <div id="remote-state" class="video-state" role="status">正在读取远程管理页状态…</div>
 <p class="hint">开启后，手机或其他电脑可以用浏览器<b>只读</b>查看收货历史和录像（在线播放），访问的人什么都不用装。远程页面不能收货、入库、删除、修改设置，也不能访问剪贴板和 PDA。默认关闭。</p>
 <div class="form-row"><label>启用</label><label class="setting-switch"><input type="checkbox" id="remote-enabled"> 启用远程管理页</label></div>
 <div class="form-row"><label>访问方式</label><label class="setting-switch"><input type="checkbox" id="remote-tunnel"> cpolar 穿透（推荐：没有公网 IPv6 也能用）</label></div>
 <div class="form-row"><label for="remote-tunnel-port">cpolar 入口端口</label><input class="input" id="remote-tunnel-port" type="number" min="1024" max="65535" step="1" placeholder="18480"></div>
 <div class="form-row"><label></label><label class="setting-switch"><input type="checkbox" id="remote-ipv6"> IPv6 直连（需要公网 IPv6 和放行入站）</label></div>
 <div class="form-row"><label for="remote-port">IPv6 直连端口</label><input class="input" id="remote-port" type="number" min="1024" max="65535" step="1" placeholder="18443"></div>
 <p class="hint">建议使用 10000 以上的高位端口（默认 18443）。国内运营商通常封锁 80 / 443 / 8080 入站。</p>
 <div class="form-row"><label for="remote-user">管理员用户名</label><input class="input" id="remote-user" maxlength="32" autocomplete="off" spellcheck="false"></div>
 <div class="form-row"><label for="remote-pass">管理员密码</label><input class="input" id="remote-pass" type="password" maxlength="128" autocomplete="new-password"></div>
 <div class="form-row"><label for="remote-pass2">确认密码</label><input class="input" id="remote-pass2" type="password" maxlength="128" autocomplete="new-password"></div>
 <p class="hint">密码至少10位；本机只保存加盐 scrypt 哈希，不保存明文。修改用户名或密码会让已登录的远程会话全部失效。</p>
 <div class="form-row"><label>录像下载</label><label class="setting-switch"><input type="checkbox" id="remote-download"> 允许下载录像（默认关闭：只能在线播放）</label></div>
 <div class="row"><button class="btn btn-primary" id="remote-save">保存远程管理页设置</button><button class="btn btn-ghost" id="remote-refresh">刷新状态 / 地址</button><span id="remote-msg" role="status"></span></div>
 </div>
 <div class="card"><div class="card-hd">cpolar 穿透（免费版）</div>
 <div id="remote-tunnel-state" class="video-state" role="status">—</div>
 <div class="form-row"><label>cpolar 本地地址</label><code id="remote-tunnel-addr">127.0.0.1:18480</code><button class="btn btn-ghost btn-sm" id="remote-copy-tunnel" data-copy="127.0.0.1:18480">复制</button></div>
 <p class="hint">1）在这台收货电脑上安装 cpolar（Windows 版装好后是系统服务，开机自动运行），用自己的免费账号登录。2）浏览器打开 http://127.0.0.1:9200 进入 cpolar，点“隧道管理 → 创建隧道”：协议选 <b>http</b>，本地地址填上面这串，域名类型选“随机域名”，地区不要选 China NAS。3）到“状态 → 在线隧道列表”复制 <b>https://</b> 开头的公网地址发给要看的人。</p>
 <p class="hint">免费版的公网地址是随机的，24 小时内会变：打不开时回到 cpolar 的“在线隧道列表”（或 dashboard.cpolar.com/status）复制新地址。地址变了以后，每台手机都要重新登录一次。免费版带宽只有 1Mbps，在线看录像会比较卡。</p>
 <p class="hint">只穿透这个入口，不要把本机收货服务的端口（${location.port||'8765'}）或 cpolar 自己的 9200 端口穿透出去：前者是完整的收货界面，没有远程登录保护。这个入口只接受本机连接，局域网和外网都直接连不上，也不用改防火墙。</p>
 </div>
 <div class="card"><div class="card-hd">IPv6 直连访问地址</div>
 <div id="remote-urls"><p class="hint">读取中…</p></div>
 <p class="hint">IPv6 地址可能随路由器重新拨号或隐私地址轮换而变化，访问不了时回来刷新这里。地址带方括号，端口写在方括号外。</p>
 <div class="form-row"><label>证书 SHA-256 指纹</label><code id="remote-fp" style="overflow-wrap:anywhere">—</code></div>
 <p class="hint">证书为本机自签名，浏览器首次打开会提示“不安全/证书无效”。请在警告页查看证书详情，确认 SHA-256 指纹与上面一致后再继续访问；不一致请不要登录。</p>
 </div>
 <div class="card"><div class="card-hd">放行入站连接</div>
 <p class="hint">1）Windows 防火墙：点击下方按钮，在弹出的管理员确认窗口中点“是”，为当前端口添加 TCP 入站放行规则（所有网络配置文件）。也可复制命令到“以管理员身份运行”的命令提示符执行。</p>
 <div class="row"><button class="btn btn-ghost" id="remote-firewall">添加 Windows 防火墙放行规则（需管理员确认）</button><button class="btn btn-ghost" id="remote-copy-cmd" data-copy="">复制</button></div>
 <code id="remote-cmd" style="display:block;overflow-wrap:anywhere;font-size:12px;margin:6px 0">—</code>
 <p class="hint">2）光猫 / 路由器：需允许 IPv6 入站（常见为“IPv6 防火墙 / IPv6 会话过滤”需关闭或为本机该端口添加放行；部分光猫需改为桥接由路由器拨号）。3）手机需在支持 IPv6 的网络下访问（移动数据一般支持；部分 Wi‑Fi 不支持）。</p>
 <p class="hint">安全提示：这是直接暴露在公网的入口。请使用长密码，不用时关闭；登录与观看录像都会记录在 data\\logs\\remote-access.log。</p>
 </div>`;
 let state=null,busy=false;
 function msg(text,error){if(error&&text&&window.JstErr)text=JstErr.ensure(text);const m=$('remote-msg');m.textContent=text||'';m.classList.toggle('error',!!error);}
 function paint(s){
  state=s;
  const modes=[s.tunnel_running?'cpolar 入口 '+(s.cpolar_local_address||''):'',s.ipv6_running?'IPv6 端口 '+s.port:''].filter(Boolean).join(' · ');
  $('remote-state').textContent=s.running?('运行中 · '+modes+(s.allow_download?' · 允许下载':' · 仅在线播放')+(s.error?' · '+s.error:'')):(s.enabled?('已启用但未运行'+(s.error?'：'+s.error:'')):'已关闭（默认）');
  $('remote-tunnel').checked=!!s.tunnel_enabled;$('remote-ipv6').checked=s.ipv6_enabled!==false;$('remote-tunnel-port').value=s.tunnel_port||18480;
  const ta=s.cpolar_local_address||'127.0.0.1:18480';$('remote-tunnel-addr').textContent=ta;$('remote-copy-tunnel').dataset.copy=ta;
  $('remote-tunnel-state').textContent=s.tunnel_running?('cpolar 入口运行中 · '+ta+'（只接受本机连接）'):(s.tunnel_enabled?('cpolar 入口未运行'+(s.tunnel_error?'：'+s.tunnel_error:(s.enabled?'':'（远程管理页未启用）'))):'未开启');
  $('remote-enabled').checked=!!s.enabled;$('remote-port').value=s.port||18443;$('remote-user').value=s.username||'';$('remote-download').checked=!!s.allow_download;
  $('remote-pass').placeholder=s.has_password?'已设置，留空表示不修改':'请设置至少10位的密码';$('remote-pass2').placeholder=$('remote-pass').placeholder;
  $('remote-fp').textContent=s.fingerprint||'（首次启用时生成）';
  $('remote-cmd').textContent=s.firewall_command||'';$('remote-copy-cmd').dataset.copy=s.firewall_command||'';
  const box=$('remote-urls');box.replaceChildren();
  if(!s.addresses||!s.addresses.length){const p=document.createElement('p');p.className='hint error';p.textContent='未检测到本机公网 IPv6 地址（2000::/3）。请确认光猫/路由器已开启 IPv6 并为本机分配了全局地址。';box.append(p);}
  else for(const url of s.urls){const row=document.createElement('div');row.className='row';const code=document.createElement('code');code.textContent=url;code.style.overflowWrap='anywhere';const b=document.createElement('button');b.className='btn btn-ghost btn-sm';b.textContent='复制';b.dataset.copy=url;row.append(code,b);box.append(row);}
  if(!s.running&&s.enabled&&s.error)msg(s.error,true);
 }
 async function load(){try{const r=await fetch('/api/remote/status',{cache:'no-store'});const d=await r.json();if(!d.ok)throw Error(d.msg||'读取失败');paint(d);}catch(e){$('remote-state').textContent='读取失败：'+e.message;}}
 async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.msg||'操作失败');return d;}
 $('remote-save').onclick=async()=>{
  if(busy)return;const pass=$('remote-pass').value,pass2=$('remote-pass2').value;
  if(pass!==pass2)return msg('两次输入的密码不一致',true);
  const enabled=$('remote-enabled').checked;
  const tunnel=$('remote-tunnel').checked,ipv6=$('remote-ipv6').checked;
  if(enabled&&!tunnel&&!ipv6)return msg('请至少选一种访问方式：cpolar 穿透或 IPv6 直连',true);
  if(enabled&&!(state&&state.enabled)&&!await JstDialog.confirm('启用后，别人可以从外网打开只读管理页（必须输入管理员用户名和密码）'+(tunnel?'：cpolar 免费地址是公开的网址，请用长密码。':'。')+'确定启用？'))return;
  if($('remote-download').checked&&!(state&&state.allow_download)&&!await JstDialog.confirm('允许下载后，登录者可以把录像文件保存到自己的设备。确定允许？'))return;
  busy=true;$('remote-save').disabled=true;msg('正在保存…');
  try{const body={enabled,ipv6_enabled:ipv6,tunnel_enabled:tunnel,tunnel_port:Number($('remote-tunnel-port').value||18480),port:Number($('remote-port').value||18443),username:$('remote-user').value.trim(),allow_download:$('remote-download').checked};if(pass)body.password=pass;
   const d=await post('/api/remote/config',body);$('remote-pass').value='';$('remote-pass2').value='';paint(d);
   msg(d.running?(d.tunnel_running&&!d.ipv6_running?'已保存并启动；请按下方步骤在 cpolar 里创建隧道':'已保存并启动；IPv6 直连请按下方步骤放行防火墙'):(d.enabled?'已保存，但监听未启动：'+(d.error||''):'已保存，远程管理页已关闭'),!d.running&&d.enabled);}
  catch(e){msg('未保存：'+e.message,true);}finally{busy=false;$('remote-save').disabled=false;}
 };
 $('remote-refresh').onclick=()=>{msg('');load();};
 $('remote-firewall').onclick=async()=>{try{const d=await post('/api/remote/firewall',{});msg(d.msg||'已请求添加规则');}catch(e){msg(e.message,true);}};
 document.querySelectorAll('[data-settings-section="remote"]').forEach(b=>b.addEventListener('click',load));
 if(!pane.hidden)load();
})();

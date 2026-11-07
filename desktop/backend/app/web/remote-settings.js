/* 设置 → 远程管理页（内置 cpolar / 内置 Tailscale / IPv6 直连）。cpolar Authtoken 和 Tailscale 授权密钥只提交给本机服务，用 Windows DPAPI 加密保存，状态接口不会返回。只调用本机 /api/remote/*；密码只提交给本机服务，本机仅保存加盐哈希。 */
(function(){'use strict';
 const pane=document.querySelector('[data-settings-pane="remote"]');if(!pane)return;
 const $=id=>document.getElementById(id);
 pane.innerHTML=`<div class="card"><div class="card-hd">远程管理页（只读）</div>
 <div id="remote-state" class="video-state" role="status">正在读取远程管理页状态…</div>
 <p class="hint">开启后，手机或其他电脑可以用浏览器<b>只读</b>查看收货历史和录像（在线播放），访问的人什么都不用装。远程页面不能收货、入库、删除、修改设置，也不能访问剪贴板和 PDA。默认关闭。</p>
 <div class="form-row"><label>启用</label><label class="setting-switch"><input type="checkbox" id="remote-enabled"> 启用远程管理页</label></div>
 <div class="form-row"><label>访问方式</label><label class="setting-switch"><input type="checkbox" id="remote-tunnel"> cpolar 穿透（内置，填 Authtoken 即可；免费地址会变）</label></div>
 <div class="form-row"><label></label><label class="setting-switch"><input type="checkbox" id="remote-ts"> Tailscale（内置；看的设备装 Tailscale 并登录同一账号，地址固定）</label></div>
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
 <div class="card"><div class="card-hd">cpolar 穿透（内置）</div>
 <div id="remote-tunnel-state" class="video-state" role="status">—</div>
 <div class="form-row"><label for="remote-cpolar-token">cpolar Authtoken</label><input class="input" id="remote-cpolar-token" type="password" maxlength="200" autocomplete="off" spellcheck="false" placeholder="在 dashboard.cpolar.com 的“验证”页复制"><button class="btn btn-ghost btn-sm" id="remote-cpolar-clear">清除</button></div>
 <div class="form-row"><label>公网地址</label><code id="remote-cpolar-url" style="overflow-wrap:anywhere">—</code><button class="btn btn-ghost btn-sm" id="remote-copy-cpolar" data-copy="">复制</button><button class="btn btn-ghost btn-sm" id="remote-cpolar-restart">重新连接</button></div>
 <p class="hint">用法：注册 cpolar 免费账号 → 把 Authtoken 粘到上面 → 勾选“cpolar 穿透”并保存。小助手会自己运行 cpolar，把公网地址显示在这里，访问的人用浏览器打开 <b>https://</b> 地址、登录就能看。不用另外安装 cpolar，也不用打开 cpolar 的 9200 页面建隧道。</p>
 <p class="hint">cpolar 客户端是 cpolar 公司的闭源软件，安装包里不带：第一次启用时小助手从 cpolar 官网下载固定版本（<span id="remote-cpolar-ver">—</span>），核对 SHA-256 后才运行，存在数据目录 tools\\cpolar 里。电脑要能访问 www.cpolar.com。</p>
 <p class="hint">免费版的地址 24 小时内会变：这里每几秒自动读取一次最新地址，变了以后把新地址重新发给要看的人，手机要重新登录一次。免费版带宽只有 1Mbps，在线看录像会卡；同一个免费账号同一时间只能在一台电脑上用。小助手开着、远程管理页启用时 cpolar 才运行，开机自启跟着小助手走；如果这台电脑以前单独装过 cpolar 并建过同一个隧道，请把那条隧道停掉，免得一个账号两处在线。</p>
 <div class="form-row"><label>本机入口</label><code id="remote-tunnel-addr">127.0.0.1:18480</code><button class="btn btn-ghost btn-sm" id="remote-copy-tunnel" data-copy="127.0.0.1:18480">复制</button></div>
 <p class="hint">不填 Authtoken 时，也可以照旧用自己安装的 cpolar 把上面这个本机入口穿透出去。不要把本机收货服务的端口（${location.port||'8765'}）穿透出去：那是完整的收货界面，没有远程登录保护。</p>
 </div>
 <div class="card"><div class="card-hd">Tailscale（内置）</div>
 <div id="remote-ts-state" class="video-state" role="status">—</div>
 <div class="form-row"><label for="remote-ts-key">授权密钥（可选）</label><input class="input" id="remote-ts-key" type="password" maxlength="220" autocomplete="off" spellcheck="false" placeholder="tskey-auth-…，留空则用下方登录链接授权"></div>
 <div class="form-row"><label for="remote-ts-name">设备名</label><input class="input" id="remote-ts-name" maxlength="63" spellcheck="false" placeholder="receiving-assistant"></div>
 <div class="form-row"><label for="remote-ts-port">本机入口端口</label><input class="input" id="remote-ts-port" type="number" min="1024" max="65535" step="1" placeholder="18481"></div>
 <div id="remote-ts-login" class="row" hidden><span>还没登录：</span><a id="remote-ts-auth" href="#" target="_blank" rel="noopener noreferrer">打开 Tailscale 登录链接</a><button class="btn btn-ghost btn-sm" id="remote-copy-ts-auth" data-copy="">复制链接</button></div>
 <div id="remote-ts-urls"></div>
 <div class="form-row"><label>证书 SHA-256</label><code id="remote-ts-fp" style="overflow-wrap:anywhere">—</code></div>
 <div class="row"><button class="btn btn-ghost" id="remote-ts-logout">退出 Tailscale 登录</button></div>
 <p class="hint">用法：勾选“Tailscale”并保存，然后点上面的登录链接用你的 Tailscale 账号授权（或者先在 login.tailscale.com/admin/settings/keys 生成授权密钥填进来，登录成功后小助手会删掉保存的密钥）。要看的手机或电脑装 Tailscale、登录同一个账号，浏览器打开上面的地址就行，地址不会变，也没有 cpolar 免费版的带宽限制。</p>
 <p class="hint">Tailscale 已经内置在小助手里（用 Tailscale 开源代码编译的 ra-tailnet.exe，不装 Tailscale 客户端、不建虚拟网卡、不用管理员权限），只在 tailnet 里开放这一个 HTTPS 入口，收货服务和这台电脑的其他端口都不会出现在 tailnet 里。tailnet 没开 HTTPS 证书时，浏览器会提示证书无效，核对上面的指纹一致再继续。</p>
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
  const modes=[s.tunnel_running?'cpolar 入口 '+(s.cpolar_local_address||''):'',s.tailscale_running?'Tailscale':'',s.ipv6_running?'IPv6 端口 '+s.port:''].filter(Boolean).join(' · ');
  $('remote-state').textContent=s.running?('运行中 · '+modes+(s.allow_download?' · 允许下载':' · 仅在线播放')+(s.error?' · '+s.error:'')):(s.enabled?('已启用但未运行'+(s.error?'：'+s.error:'')):'已关闭（默认）');
  $('remote-tunnel').checked=!!s.tunnel_enabled;$('remote-ipv6').checked=s.ipv6_enabled!==false;$('remote-tunnel-port').value=s.tunnel_port||18480;
  const ta=s.cpolar_local_address||'127.0.0.1:18480';$('remote-tunnel-addr').textContent=ta;$('remote-copy-tunnel').dataset.copy=ta;
  $('remote-tunnel-state').textContent=s.tunnel_running?('cpolar 入口运行中 · '+ta+'（只接受本机连接）'):(s.tunnel_enabled?('cpolar 入口未运行'+(s.tunnel_error?'：'+s.tunnel_error:(s.enabled?'':'（远程管理页未启用）'))):'未开启');
  const cp=s.cpolar||{};const ver=$('remote-cpolar-ver');ver.textContent=(cp.version||'')+(cp.sha256?'，SHA-256 '+cp.sha256.slice(0,16)+'…':'');
  $('remote-cpolar-token').placeholder=cp.builtin?'已保存，留空表示不修改':'在 dashboard.cpolar.com 的“验证”页复制';
  const curl=cp.public_url||'';$('remote-cpolar-url').textContent=curl||(cp.builtin?'（连上后显示）':'（填 Authtoken 后由小助手自动获取）');$('remote-copy-cpolar').dataset.copy=curl;
  const dl=cp.download||{};const cst={off:'未运行',downloading:'正在从 cpolar 官网下载客户端'+(dl.total?'（'+Math.round(100*dl.done/dl.total)+'%）':'…'),starting:'正在连接 cpolar…',online:'已连接'+(cp.url_since?' · 地址获取于 '+new Date(cp.url_since*1000).toLocaleString():''),error:'出错'};
  if(cp.builtin&&s.tunnel_running)$('remote-tunnel-state').textContent='内置 cpolar：'+(cst[cp.state]||cp.state)+(cp.error?'：'+(cp.error_code?'['+cp.error_code+'] ':'')+cp.error:'');
  const ts=s.tailscale||{};$('remote-ts').checked=!!s.tailscale_enabled;$('remote-ts-name').value=s.tailscale_hostname||'receiving-assistant';$('remote-ts-port').value=s.tailscale_port||18481;
  $('remote-ts-key').placeholder=ts.authkey_saved?'已保存，登录成功后自动删除':'tskey-auth-…，留空则用下方登录链接授权';
  const tst={off:'未运行',starting:'正在启动…',needs_login:'需要登录：点下面的链接授权',needs_approval:'等待 Tailscale 管理员批准这台设备',online:'已连接'+(ts.tailnet?' · '+ts.tailnet:''),error:'出错'};
  $('remote-ts-state').textContent=!s.tailscale_enabled?'未开启':(!s.tailscale_running?('未运行'+(s.tailscale_error?'：'+s.tailscale_error:(s.enabled?'':'（远程管理页未启用）'))):((tst[ts.state]||ts.state)+(ts.error?'：'+(ts.error_code?'['+ts.error_code+'] ':'')+ts.error:'')));
  const au=ts.auth_url||'';$('remote-ts-login').hidden=!au;$('remote-ts-auth').href=au||'#';$('remote-copy-ts-auth').dataset.copy=au;
  const tb=$('remote-ts-urls');tb.replaceChildren();for(const url of ts.urls||[]){const row=document.createElement('div');row.className='row';const code=document.createElement('code');code.textContent=url;code.style.overflowWrap='anywhere';const b=document.createElement('button');b.className='btn btn-ghost btn-sm';b.textContent='复制';b.dataset.copy=url;row.append(code,b);tb.append(row);}
  $('remote-ts-fp').textContent=ts.cert_mode==='tailscale'?'用设备名地址访问时是 Tailscale 签发的正式证书；用 100.x 地址访问时为自签名：'+(ts.cert_sha256||''):(ts.cert_sha256||'（连接后显示）');
  $('remote-ts-logout').disabled=!ts.logged_in_before;
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
  const tunnel=$('remote-tunnel').checked,ipv6=$('remote-ipv6').checked,tse=$('remote-ts').checked;
  if(enabled&&!tunnel&&!ipv6&&!tse)return msg('请至少选一种访问方式：cpolar 穿透、Tailscale 或 IPv6 直连',true);
  if(enabled&&!(state&&state.enabled)&&!await JstDialog.confirm('启用后，别人可以从外网打开只读管理页（必须输入管理员用户名和密码）'+(tunnel?'：cpolar 免费地址是公开的网址，请用长密码。':'。')+'确定启用？'))return;
  if($('remote-download').checked&&!(state&&state.allow_download)&&!await JstDialog.confirm('允许下载后，登录者可以把录像文件保存到自己的设备。确定允许？'))return;
  busy=true;$('remote-save').disabled=true;msg('正在保存…');
  try{const body={enabled,ipv6_enabled:ipv6,tunnel_enabled:tunnel,tunnel_port:Number($('remote-tunnel-port').value||18480),port:Number($('remote-port').value||18443),username:$('remote-user').value.trim(),allow_download:$('remote-download').checked,tailscale_enabled:tse,tailscale_hostname:$('remote-ts-name').value.trim().toLowerCase()||'receiving-assistant',tailscale_port:Number($('remote-ts-port').value||18481)};if(pass)body.password=pass;
   const ctok=$('remote-cpolar-token').value.trim(),tkey=$('remote-ts-key').value.trim();if(ctok)body.cpolar_token=ctok;if(tkey)body.tailscale_authkey=tkey;
   const d=await post('/api/remote/config',body);$('remote-pass').value='';$('remote-pass2').value='';$('remote-cpolar-token').value='';$('remote-ts-key').value='';paint(d);
   msg(d.running?(d.ipv6_running?'已保存并启动；IPv6 直连请按下方步骤放行防火墙':(d.cpolar&&d.cpolar.builtin||d.tailscale_running?'已保存并启动；公网 / Tailscale 地址连上后显示在下方':'已保存并启动；没填 cpolar Authtoken，需要自己用 cpolar 穿透本机入口')):(d.enabled?'已保存，但监听未启动：'+(d.error||''):'已保存，远程管理页已关闭'),!d.running&&d.enabled);}
  catch(e){msg('未保存：'+e.message,true);}finally{busy=false;$('remote-save').disabled=false;}
 };
 $('remote-refresh').onclick=()=>{msg('');load();};
 $('remote-cpolar-clear').onclick=async()=>{if(!(state&&state.cpolar&&state.cpolar.builtin))return msg('没有保存 cpolar Authtoken');if(!await JstDialog.confirm('清除后小助手不再运行内置 cpolar，原来的公网地址失效。确定清除？'))return;try{const d=await post('/api/remote/config',{cpolar_token_clear:true});paint(d);msg('已清除 cpolar Authtoken');}catch(e){msg(e.message,true);}};
 $('remote-cpolar-restart').onclick=async()=>{try{const d=await post('/api/remote/cpolar_restart',{});paint(d);msg(d.msg||'正在重新连接');}catch(e){msg(e.message,true);}};
 $('remote-ts-logout').onclick=async()=>{if(!await JstDialog.confirm('退出后这台电脑会从你的 Tailscale 网络里注销，Tailscale 地址失效，需要重新登录。确定退出？'))return;try{const d=await post('/api/remote/tailscale_logout',{});paint(d);msg(d.msg||'已退出');}catch(e){msg(e.message,true);}};
 setInterval(()=>{if(!pane.hidden&&!busy&&document.visibilityState==='visible')load();},4000);
 $('remote-firewall').onclick=async()=>{try{const d=await post('/api/remote/firewall',{});msg(d.msg||'已请求添加规则');}catch(e){msg(e.message,true);}};
 document.querySelectorAll('[data-settings-section="remote"]').forEach(b=>b.addEventListener('click',load));
 if(!pane.hidden)load();
})();

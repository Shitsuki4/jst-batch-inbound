/* Seven settings sections (远程管理页 pane is filled by remote-settings.js, 软件更新 by update-settings.js). Business settings are PATCH-like writes to their own keys;
   video settings are saved by the native host. Never claim a cross-store transaction. */
(function(){'use strict';
 const $=id=>document.getElementById(id),root=document.querySelector('[data-view="settings"]');if(!root)return;
 const cards=[...root.children],groups=[['receiving','收货与扫码'],['video','视频举证'],['catalog','商品资料'],['data','数据与备份'],['devices','PDA 与设备'],['update','软件更新'],['remote','远程管理页']];
 root.innerHTML='<div class="settings-heading"><div><h2>设置</h2><p>业务与视频分别保存；切换分区保留草稿，离开前会提醒。</p></div><button class="btn btn-ghost" id="settings-return">返回收货工作台</button></div><div class="settings-workspace"><aside class="settings-nav" aria-label="设置分区">'+groups.map(([id,name])=>'<button type="button" data-settings-section="'+id+'">'+name+'<span></span></button>').join('')+'</aside><div class="settings-content">'+groups.map(([id])=>'<section data-settings-pane="'+id+'" hidden></section>').join('')+'</div></div>';
 const pane=id=>root.querySelector('[data-settings-pane="'+id+'"]');
 cards.forEach((c,i)=>pane(i===0?'receiving':i===1?'catalog':i<4?'data':'devices').append(c));
 cards[0].querySelector('.card-hd').textContent='收货与扫码';$('btn-save-settings').textContent='保存收货设置';
 const catalog=cards[1],catalogTitle=catalog.querySelector('.card-hd');catalogTitle.textContent='商品资料与缓存';
 ['st-catalog-auto','st-catalog-online'].reverse().forEach(id=>catalogTitle.after($(id).closest('.form-row')));
 const saveCatalog=document.createElement('div');saveCatalog.className='row';saveCatalog.innerHTML='<button id="save-catalog-settings" class="btn btn-primary">保存商品资料设置</button><span id="catalog-save-msg" role="status"></span>';catalog.append(saveCatalog);saveCatalog.append($('btn-clear-cache'));

 const videoKeys=['showPreviewOnStartup','camera','resolution','frameRate','captureFormat','rotation','mirror','quality','directory','engine','overlayTime','overlayTracking','overlayPurchase','overlayOuter','overlaySku','overlayQuantity','photoEnabled','notesEnabled','protectionEnabled','zoomEnabled','soundStart','soundStop','soundSaved','soundDuplicate','soundError','soundVolume','cleanupDays','warningGb','stopGb','hotkeysEnabled','hotkeyPreview','hotkeyStop','hotkeyContinue','trackingRuleEnabled','trackingPrefixes','trackingMinLength','trackingMaxLength'];
 const vid=key=>'video-'+key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase());
 const check=(key,label)=>`<label class="setting-switch"><input type="checkbox" id="${vid(key)}"> ${label}</label>`;
 const row=(label,html)=>`<div class="form-row"><label>${label}</label>${html}</div>`;
 const input=(key,label,type='text',attrs='')=>row(label,`<input class="input" id="${vid(key)}" type="${type}" aria-label="${label}" ${attrs}>`);
 const select=(key,label,items,numeric=false)=>row(label,`<select class="select" id="${vid(key)}" aria-label="${label}" ${numeric?'data-number="true"':''}>${items.map(([v,t])=>`<option value="${v}">${t}</option>`).join('')}</select>`);
 const detail=(label,html)=>`<details class="settings-advanced"><summary>${label}</summary>${html}</details>`;
 pane('video').innerHTML=`<div class="card"><div class="card-hd">视频举证</div><div id="video-state" class="video-state" role="status">正在读取桌面录像状态…</div><p id="video-alerts" class="hint" role="status"></p><p class="hint">只采集视频，不使用麦克风；本机保存，不上传、不自动删除。</p>
 ${row('启动监控窗口',check('showPreviewOnStartup','启动软件时打开监控窗口'))}
 ${row('扫码自动录像',check('enabled','扫到物流后开始，换物流切为新片段'))}
 ${input('camera','摄像头','text','list="video-camera-options" autocomplete="off"')}<datalist id="video-camera-options"></datalist><button class="btn btn-ghost" id="video-enumerate">枚举设备</button>
 <div class="row"><button class="btn btn-ghost" id="video-modes">检测设备可用档位</button><select class="select" id="video-mode-choice" aria-label="设备可用录像档位" disabled><option value="">请先检测设备可用档位</option></select></div><p class="hint" id="video-modes-result">按设备报告列出分辨率、帧率、格式组合，含设备支持的2K、4K等高档位；不凭空增加。高分辨率可能降低帧率，请先做30秒测试。</p>
 ${select('resolution','分辨率',[['640x480','640×480'],['1280x720','1280×720'],['1920x1080','1920×1080']])}
 ${select('frameRate','帧率',[15,24,25,30,60].map(n=>[n,n+' fps']),true)}
 <p class="hint" id="video-effective">以设备报告的兼容模式和录像实际参数为准，不支持不会假报成功。</p>
 ${input('directory','录像保存目录')}<button class="btn btn-ghost" id="video-choose-directory">选择目录</button>
 <p class="hint">新录像：日期 / 采购-外部单号 / 物流 / 物流.mp4；续录不覆盖，多采购单份媒体、多关联检索。改目录不搬动历史录像。</p>
 ${detail('摄像头高级 · 格式 / 旋转 / 镜像 / 硬件控制',
 select('captureFormat','输入格式',[['auto','自动协商'],['mjpeg','MJPEG'],['yuyv422','YUYV422'],['nv12','NV12']])+
 select('rotation','画面旋转',[0,90,180,270].map(n=>[n,n+'°']),true)+row('画面镜像',check('mirror','水平镜像（影响录像）'))+
 input('quality','视频质量 CRF','number','min="15" max="35" step="1"')+
 '<p class="hint">CRF 15–35，数值越小文件越大。硬件参数由相机驱动保存，不纳入视频设置草稿，点击应用立即生效；需停录操作。</p><button class="btn btn-ghost" id="video-controls">查询硬件能力</button><div id="video-hardware">曝光、对焦、硬件变焦：尚未查询，不开放修改。</div>')}
 ${detail('录像叠字 · 逐项选择', '<div class="video-check-grid">'+[['overlayTime','时间'],['overlayTracking','物流单号'],['overlayPurchase','采购单'],['overlayOuter','外部单号'],['overlaySku','最近确认商品'],['overlayQuantity','最近确认数量']].map(x=>check(...x)).join('')+'</div><p class="hint">商品确认只更新叠字，不切段；完整关联信息同时写入本机索引。</p>')}
 ${detail('浮窗工具 · 按需显示', '<div class="video-check-grid">'+[['photoEnabled','拍照'],['notesEnabled','本段备注'],['protectionEnabled','售后保护'],['zoomEnabled','局部放大']].map(x=>check(...x)).join('')+'</div><p class="hint">拍照保存当前480×270预览图，不是全分辨率照片。局部放大仅影响预览，不裁录像。关闭保护功能不会解除已保护片段。</p>')}
 ${detail('全过程提示音 · 各项可关闭', '<div class="video-sounds">'+[['soundStart','开始','start'],['soundStop','停止','stop'],['soundSaved','保存成功','saved'],['soundDuplicate','重复物流','duplicate'],['soundError','异常 / 磁盘提醒','error']].map(([k,l,c])=>'<div>'+check(k,l)+`<button class="btn btn-ghost" data-sound-cue="${c}">试听</button></div>`).join('')+'</div>'+input('soundVolume','提示音音量','number','min="0" max="100" step="1"')+'<p class="hint">试听使用已保存的音量；只由扬声器播放，不录入录像。普通商品扫码音在“收货与扫码”中独立设置。</p>')}
 ${detail('磁盘阈值与清理提醒 · 在软件内自定义',input('warningGb','剩余空间预警 GB','number','min="0.001" max="1000000" step="any" placeholder="由你填写"')+input('stopGb','剩余空间停止 GB','number','min="0.001" max="1000000" step="any" placeholder="由你填写"')+'<p class="hint">必须满足：预警 GB &gt; 停止 GB &gt; 0。预警只提醒；达到停止值正常收尾并验证文件，不开始新录像。未配置不能开始录像。</p>'+select('cleanupDays','超龄清理提醒',[[0,'不按天数提醒'],[90,'90天以上'],[180,'180天以上']],true)+'<button class="btn btn-ghost" data-native-action="cleanup">预览清理候选</button><p class="hint">只提醒，不自动删除；进入列表后默认不勾选，删除前再次确认。在录、异常、售后保护录像排除。</p>')}
 ${detail('全局录像快捷键 · 自定义组合键',row('启用快捷键',check('hotkeysEnabled','只注册以下指定组合键'))+[['hotkeyPreview','显示 / 隐藏浮窗'],['hotkeyStop','结束本单录像'],['hotkeyContinue','继续本单新一段']].map(([key,label])=>input(key,label,'text','data-hotkey="true" autocomplete="off" placeholder="按组合键录入；Backspace清空"')).join('')+'<p class="hint">至少包含 Ctrl / Alt / Win 之一，可搭配 Shift 与字母、数字或F1–F24。留空停用该动作；冲突时保留旧设置。无键盘钩子，不拦截普通扫码。</p><p id="video-hotkey-status" role="status"></p>')}
 ${detail('物流格式规则 · 默认不限',row('可选规则',check('trackingRuleEnabled','启用前缀 / 长度校验（即使自动录像关闭也生效）'))+input('trackingPrefixes','允许的物流前缀','text','placeholder="逗号分隔，空白不限"')+input('trackingMinLength','最小长度','number','min="1" max="200" step="1" placeholder="空白不限"')+input('trackingMaxLength','最大长度','number','min="1" max="200" step="1" placeholder="空白不限"')+'<p class="hint">只校验物流，不限制商品条码；不截断、不补位、不改原单号。</p>')}
 ${detail('高级 · 内置录像引擎','<p class="hint">安装版自带FFmpeg / ffprobe，无需额外安装；非必要不修改路径。</p>'+input('engine','FFmpeg程序路径'))}
 <div class="row video-save-row"><button class="btn btn-primary" id="video-save">保存视频设置</button><span id="video-message" role="status"></span></div><p class="hint">录制中不可改设置；设置分区之间独立保存。启用自动录像前请保存阈值并先测试相机。</p></div>
 <div class="card"><div class="card-hd">监控预览与测试</div><p class="hint">测试不写收货记录，最长30秒后自动收尾；测试与收货都不主动弹出浮窗。切单有短暂间隔，看到“正在录制”后再拆包。</p><div class="row"><button class="btn btn-primary" data-native-action="preview">显示监控浮窗</button><button class="btn btn-ghost" id="video-test-start">开始测试录像（不写收货）</button><button class="btn btn-ghost" id="video-test-stop">结束测试并验证文件</button></div><div class="row"><button class="btn btn-ghost" data-native-action="stop">结束本单录像</button><button class="btn btn-ghost" data-native-action="continue">继续本单新一段</button><button class="btn btn-ghost" data-native-action="library">检索 / 归档 / 清理</button><button class="btn btn-ghost" data-native-action="currentDirectory">打开本单目录</button><button class="btn btn-ghost" data-native-action="directory">打开录像根目录</button><button class="btn btn-ghost" id="video-diagnostics">复制录像诊断</button></div></div>`;

 const fields={receiving:{default_id_type:'st-id-type',owner_ids:'st-owners',scan_step:'st-step',sku_lookup:'st-sku-lookup',scan_ignore_disabled:'st-scan-ignore-disabled',sound:'st-sound'},catalog:{catalog_auto:'st-catalog-auto',catalog_online:'st-catalog-online'},video:Object.fromEntries(videoKeys.map(k=>[k,vid(k)]))};
 const baseline={},saving=new Set();let section='receiving',videoState={},videoLoaded=false;
 function readField(el){if(el.type==='checkbox')return el.checked;if(el.type==='number'||el.dataset.number)return el.value===''?null:Number(el.value);return el.value;}
 function writeField(el,value){if(el.type==='checkbox')el.checked=!!value;else {if(el.tagName==='SELECT'&&value!=null&&![...el.options].some(o=>o.value===String(value)))el.add(new Option(String(value),String(value)));el.value=value??'';}}
 function values(id){return Object.fromEntries(Object.entries(fields[id]||{}).map(([key,el])=>[key,readField($(el))]));}
 function dirty(id){return baseline[id]&&JSON.stringify(baseline[id])!==JSON.stringify(values(id));}
 function dirtyIDs(){return Object.keys(fields).filter(dirty);}
 function badges(){root.querySelectorAll('[data-settings-section]').forEach(b=>b.querySelector('span').textContent=dirty(b.dataset.settingsSection)?'未保存':'');}
 function capture(id){baseline[id]=values(id);badges();}
 function notice(id,text,error=false){const el=$(id);el.textContent=text;el.classList.toggle('error',error);}
 function beforeLeave(){if(saving.size){alert('设置正在保存 / 操作中，请完成后再离开。');return false;}const ids=dirtyIDs();if(!ids.length)return true;if(!confirm(ids.map(id=>groups.find(x=>x[0]===id)[1]).join('、')+' 尚未保存。离开并放弃这些草稿？'))return false;ids.forEach(id=>{for(const [key,el] of Object.entries(fields[id]))writeField($(el),baseline[id][key]);});if(ids.includes('video'))paintVideo(videoState);badges();return true;}
 function show(id){if(!groups.some(g=>g[0]===id))id='receiving';section=id;root.querySelectorAll('[data-settings-pane]').forEach(p=>p.hidden=p.dataset.settingsPane!==id);root.querySelectorAll('[data-settings-section]').forEach(b=>{b.classList.toggle('active',b.dataset.settingsSection===id);b.setAttribute('aria-current',b.dataset.settingsSection===id?'page':'false');});if(id==='video'&&!videoLoaded)loadVideo();}
 root.querySelectorAll('[data-settings-section]').forEach(b=>b.onclick=()=>show(b.dataset.settingsSection));
 root.addEventListener('input',badges);root.addEventListener('change',badges);
 async function save(id){if(saving.size||!baseline[id])return;const data=values(id),message=id==='receiving'?'st-msg':'catalog-save-msg',button=$(id==='receiving'?'btn-save-settings':'save-catalog-settings');if(id==='receiving'&&(!Number.isFinite(Number(data.scan_step))||Number(data.scan_step)<=0)){notice(message,'扫码数量必须大于 0',true);return;}saving.add(id);button.disabled=true;try{const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.msg||'保存失败');baseline[id]=data;badges();notice(message,'已保存本分区，其他设置保持不变');document.dispatchEvent(new CustomEvent('settings:saved',{detail:data}));}catch(e){notice(message,'未保存：'+e.message,true);}finally{saving.delete(id);button.disabled=false;}}
 $('save-catalog-settings').onclick=()=>save('catalog');
 function businessLoaded(){['receiving','catalog'].forEach(id=>{capture(id);for(const el of Object.values(fields[id]))$(el).disabled=false;});}
 ['receiving','catalog'].forEach(id=>{for(const el of Object.values(fields[id]))$(el).disabled=!window.receivingSettingsLoaded;});
 document.addEventListener('settings:loaded',businessLoaded);if(window.receivingSettingsLoaded)businessLoaded();
 function paintVideo(s){videoState={...videoState,...s};$('video-state').textContent=(NativeDesk.available?'':'仅桌面版可操作 · ')+(s.status||videoState.status||'自动录像未启用')+(videoState.tracking?' · 物流 '+videoState.tracking:'');$('video-enabled').checked=!!videoState.enabled;
  const lock=!!videoState.active||saving.has('video')||!videoLoaded;Object.values(fields.video).forEach(id=>$(id).disabled=lock);
  ['video-save','video-choose-directory','video-enumerate','video-modes','video-controls'].forEach(id=>$(id).disabled=lock);
  ['video-resolution','video-frame-rate','video-capture-format'].forEach(id=>$(id).disabled=true);
  if(detectedModes.length){if(detectedCamera!==$('video-camera').value||detectedEngine!==$('video-engine').value)invalidateModes();else{const i=detectedModes.findIndex(m=>`${m.width}x${m.height}`===$('video-resolution').value&&m.fps===Number($('video-frame-rate').value)&&($('video-capture-format').value==='auto'||m.format===$('video-capture-format').value));$('video-mode-choice').value=i<0?'':String(i);}}
  $('video-mode-choice').disabled=lock||!detectedModes.length;
  $('video-enabled').disabled=!videoLoaded||saving.has('video');$('video-test-start').disabled=lock;$('video-test-stop').disabled=!videoState.test||saving.has('video');
  $('video-alerts').textContent=[videoState.settingsWarning,videoState.warning,videoState.archiveHint,videoState.cleanupHint].filter(Boolean).join(' · ');
  $('video-hotkey-status').textContent=videoState.hotkeyStatus||'未注册';$('video-effective').textContent='实际录像模式：'+(videoState.effectiveMode||'尚未开始，以设备兼容模式为准');
  $('video-hardware').querySelectorAll('input,button').forEach(el=>el.disabled=lock||el.dataset.unsupported==='true');
  root.querySelectorAll('[data-sound-cue]').forEach(el=>el.disabled=!videoLoaded||saving.size>0);
 }
 async function loadVideo(){try{const s=await NativeDesk.call('settings');videoLoaded=true;if(!dirty('video')){for(const [key,id] of Object.entries(fields.video))writeField($(id),s[key]);capture('video');}paintVideo(s);}catch(e){notice('video-message',e.message,true);paintVideo({status:e.message});}}
 async function videoAction(action,args={}){if(saving.size)throw Error('已有操作进行中，请等待完成');saving.add('video');paintVideo(videoState);try{const result=await NativeDesk.call(action,args);notice('video-message',result?.message||'操作完成');return result;}catch(e){notice('video-message',e.message,true);throw e;}finally{saving.delete('video');paintVideo(videoState);}}
 $('video-save').onclick=async()=>{try{const data=values('video');for(const key of ['warningGb','stopGb'])if(data[key]===null)throw Error('请展开“磁盘阈值与清理提醒”，在软件内填写预警和停止GB');await videoAction('saveSettings',data);baseline.video=data;badges();await loadVideo();}catch(e){notice('video-message',e.message,true);}};
 $('video-enumerate').onclick=async()=>{try{const r=await videoAction('cameras');$('video-camera-options').replaceChildren(...r.cameras.map(name=>{const o=document.createElement('option');o.value=name;return o;}));if(r.cameras.length===1&&$('video-camera').value!==r.cameras[0]){$('video-camera').value=r.cameras[0];invalidateModes();badges();}notice('video-message','识别到 '+r.cameras.length+' 个视频设备');}catch{}};
 $('video-choose-directory').onclick=async()=>{try{const r=await videoAction('chooseDirectory');if(r.directory){$('video-directory').value=r.directory;badges();}}catch{}};
 $('video-enabled').onchange=async()=>{const next=$('video-enabled').checked;if(!next&&videoState.active&&!confirm('关闭自动录像会结束并保存当前录像；之后可无录像收货。确定关闭？')){$('video-enabled').checked=true;return;}try{await videoAction('auto',{enabled:next,confirmed:true});await loadVideo();}catch{$('video-enabled').checked=!!videoState.enabled;}};
 $('video-test-start').onclick=async()=>{if(dirty('video'))return notice('video-message','请先保存视频设置，再按保存后的设备测试',true);try{await videoAction('testStart');await loadVideo();}catch{}};
 $('video-test-stop').onclick=async()=>{try{await videoAction('testStop');await loadVideo();}catch{}};

 root.querySelectorAll('[data-hotkey]').forEach(el=>el.addEventListener('keydown',ev=>{
  if(ev.key==='Tab')return;ev.preventDefault();
  if((ev.key==='Backspace'||ev.key==='Delete')&&!ev.ctrlKey&&!ev.altKey&&!ev.metaKey){el.value='';badges();return;}
  if(['Control','Alt','Shift','Meta'].includes(ev.key))return;
  const key=ev.key.toUpperCase();if(!(ev.ctrlKey||ev.altKey||ev.metaKey)||!/^([A-Z0-9]|F([1-9]|1\d|2[0-4]))$/.test(key)){notice('video-message','请使用Ctrl、Alt或Win组合键，不接收普通扫码字符',true);return;}
  el.value=[ev.ctrlKey?'Ctrl':'',ev.altKey?'Alt':'',ev.shiftKey?'Shift':'',ev.metaKey?'Win':'',key].filter(Boolean).join('+');badges();
 }));
 root.querySelectorAll('[data-sound-cue]').forEach(el=>el.onclick=async()=>{try{await videoAction('soundTest',{cue:el.dataset.soundCue});}catch{}});
 let detectedModes=[],detectedCamera='',detectedEngine='',modeRequest=0;
 function invalidateModes(){modeRequest++;detectedModes=[];detectedCamera='';detectedEngine='';$('video-mode-choice').replaceChildren(new Option('请重新检测此设备的可用档位',''));$('video-mode-choice').disabled=true;$('video-modes-result').textContent='设备或引擎已变化，旧档位已失效；保存 / 开始录像时会重新核验，不自动降档。';}
 $('video-modes').onclick=async()=>{const camera=$('video-camera').value,engine=$('video-engine').value;invalidateModes();const request=modeRequest;try{const r=await videoAction('cameraModes',{camera,engine});if(request!==modeRequest||camera!==$('video-camera').value||engine!==$('video-engine').value)return;detectedModes=r.modes;detectedCamera=camera;detectedEngine=engine;const choice=$('video-mode-choice');choice.replaceChildren(new Option('请选择设备报告的完整档位',''),...r.modes.map((m,i)=>new Option(m.label,String(i))));const i=r.modes.findIndex(m=>`${m.width}x${m.height}`===$('video-resolution').value&&m.fps===Number($('video-frame-rate').value)&&($('video-capture-format').value==='auto'||m.format===$('video-capture-format').value));choice.value=i<0?'':String(i);$('video-modes-result').textContent='设备报告 '+r.modes.length+' 个录像组合。选择后保存；实际流参数与所选档位不一致时不报就绪。';paintVideo(videoState);}catch{}};
 $('video-mode-choice').onchange=()=>{if(detectedCamera!==$('video-camera').value||detectedEngine!==$('video-engine').value)return invalidateModes();const value=$('video-mode-choice').value;if(value==='')return;const m=detectedModes[Number(value)];if(!m)return;writeField($('video-resolution'),`${m.width}x${m.height}`);writeField($('video-frame-rate'),m.fps);writeField($('video-capture-format'),m.format);badges();notice('video-message','已选择 '+m.label+'，请保存后测试。');};
 function hardware(controls){
  const host=$('video-hardware');host.replaceChildren();
  for(const c of controls){const line=document.createElement('div');line.className='video-hardware-row';
   const text=document.createElement('span');text.textContent=c.label+'：'+(c.supported?`${c.min}～${c.max}，步长${c.step}`:c.message||'不支持');line.append(text);
   const num=document.createElement('input');num.type='number';num.className='input';num.min=c.min;num.max=c.max;num.step=c.step;num.value=c.value;num.setAttribute('aria-label',c.label+'数值');
   const auto=document.createElement('input');auto.type='checkbox';auto.checked=c.automatic;auto.setAttribute('aria-label',c.label+'自动');const label=document.createElement('label');label.className='setting-switch';label.append(auto,document.createTextNode('自动'));
   const button=document.createElement('button');button.className='btn btn-ghost';button.textContent='应用'+c.label;
   const update=()=>{num.dataset.unsupported=String(!c.supported||!c.manualSupported||auto.checked);auto.dataset.unsupported=String(!c.supported||!c.autoSupported||!c.manualSupported);button.dataset.unsupported=String(!c.supported);paintVideo(videoState);};
   auto.onchange=update;button.onclick=async()=>{try{const r=await videoAction('setCameraControl',{camera:$('video-camera').value,control:c.key,automatic:auto.checked,value:Number(num.value)});hardware(r.controls);notice('video-message',c.label+'已由驱动回读确认；硬件参数不属于视频设置草稿');}catch{}};
   line.append(num,label,button);host.append(line);update();
  }
 }
 $('video-controls').onclick=async()=>{try{const r=await videoAction('cameraControls',{camera:$('video-camera').value});hardware(r.controls);}catch{}};
 $('video-camera').addEventListener('input',()=>{$('video-hardware').textContent='相机已变化，请重新查询硬件能力';invalidateModes();});
 $('video-engine').addEventListener('input',invalidateModes);
 $('video-diagnostics').onclick=async()=>{try{await videoAction('diagnostics');}catch{}};
 window.addEventListener('native:video',ev=>{if(videoLoaded)paintVideo(ev.detail);const brief=$('native-video-brief');if(brief)brief.textContent=(ev.detail.enabled?'已启用 · ':'已关闭 · ')+(ev.detail.status||'')+([ev.detail.warning,ev.detail.cleanupHint,ev.detail.settingsWarning].filter(Boolean).length?' · '+[ev.detail.warning,ev.detail.cleanupHint,ev.detail.settingsWarning].filter(Boolean).join(' · '):'');});
 $('video-settings-shortcut').onclick=()=>{ReceivingApp.navigate('settings');if(root.classList.contains('active'))show('video');};
 $('settings-return').onclick=()=>{const url=sessionStorage.getItem('receiving:last-work')||'/?view=home';if(/^\/(joint|unplanned)\.html\?sid=\d+&bid=\d+$/.test(url)){if(beforeLeave())location.href=url;}else ReceivingApp.navigate('home');};
 document.addEventListener('click',ev=>{const a=ev.target.closest('a[href]');if(a&&root.classList.contains('active')&&!a.hasAttribute('download')&&!a.getAttribute('href').startsWith('/api/')&&!beforeLeave()){ev.preventDefault();ev.stopImmediatePropagation();}},true);
 window.addEventListener('beforeunload',ev=>{if(dirtyIDs().length||saving.size){ev.preventDefault();ev.returnValue='';}});
 const exp=document.querySelector('[data-view="export"]');if(exp){const card=document.createElement('div');card.className='card';card.innerHTML='<div class="card-hd">精细导出 · 秒级扫描记录</div><p class="hint">时间、采购单、外部单号、物流单号、商品编码组合模糊搜索；可导出扫描流水（保留扫码与确认时间）或实收汇总。旧版数据没有的时间留空，不虚构。</p><div class="row"><button class="btn btn-primary" data-native-action="export">打开精细导出</button><button class="btn btn-ghost" data-native-action="library">录像文件与状态</button></div>';exp.prepend(card);}
 window.SettingsWorkspace={save,beforeLeave,show,hasUnsaved:()=>dirtyIDs().length>0||saving.size>0};
 show(new URLSearchParams(location.search).get('section')||'receiving');
})();

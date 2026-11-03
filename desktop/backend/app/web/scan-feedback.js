/* 扫码反馈（四种收货模式共用）：同一套提示音 + 扫码框旁边的提示条。
   有采购单收货（app.js）、无采购单 / 多采购单合并 / 快递单登记（receiving-desk.js）都走这里，
   换页面后声音和提示位置不变。只负责呈现，不做任何写操作。 */
(function(root){'use strict';
 if(root.ScanFeedback)return;
 // 频率 / 时长与原 app.js 一致：成功高音短响，物流单略低，警告 / 失败低音长响
 const SOUNDS={ok:[1318,0.06],tracking:[1046,0.07],warn:[200,0.14],err:[180,0.16]};
 let ctx=null,muted=null,mutedAt=0;
 // 「设置 → 提示音」关闭时不响。有采购单页由 app.js 直接传入；其它页按需读取，最多 30 秒一次。
 function refreshMuted(){
  if(Date.now()-mutedAt<30000||typeof fetch!=='function')return;mutedAt=Date.now();
  fetch('/api/settings').then(r=>r.ok?r.json():null).then(s=>{if(s&&typeof s==='object')muted=s.sound==='0';}).catch(()=>{});
 }
 function tone(freq,dur){
  try{
   const C=root.AudioContext||root.webkitAudioContext;if(!C)return;
   ctx=ctx||new C();if(ctx.resume)ctx.resume();
   const o=ctx.createOscillator(),g=ctx.createGain();
   o.type='square';o.frequency.value=freq;
   g.gain.setValueAtTime(0.0001,ctx.currentTime);
   g.gain.exponentialRampToValueAtTime(0.14,ctx.currentTime+0.01);
   g.gain.exponentialRampToValueAtTime(0.0001,ctx.currentTime+dur);
   o.connect(g);g.connect(ctx.destination);
   o.start();o.stop(ctx.currentTime+dur+0.02);
  }catch(e){/* 忽略 */}
 }
 function beep(kind){refreshMuted();if(muted)return;const s=SOUNDS[kind]||SOUNDS.ok;tone(s[0],s[1]);}
 // el 为扫码框下方的提示条；kind = ok / tracking / warn / err
 function flash(el,text,kind){if(!el)return;if(kind==='err'&&text&&window.JstErr)text=JstErr.ensure(text);el.textContent=text||'';el.className=(el.dataset.base||'flash')+(kind?' '+kind:'');}
 function show(el,text,kind){flash(el,text,kind);beep(kind);}
 // 数量框里打进来的是不是扫码枪的条码：扫码枪逐字快速输入并自带回车，人手改数量慢且只有数字。
 // text 为这一串输入，gaps 为相邻按键的间隔（毫秒）。含非数字、超过数量上限的位数（10 位以上），
 // 或 4 位以上且几乎每个间隔都在 50ms 内，都按扫码处理。
 function looksLikeScan(text,gaps){
  text=String(text||'').trim();if(!text)return false;
  if(/[^0-9]/.test(text))return true;
  if(text.length>=11)return true;
  gaps=gaps||[];if(text.length<4||gaps.length<text.length-1)return false;
  const fast=gaps.filter(g=>g<50).length;
  return fast>=Math.max(3,gaps.length-1);
 }
 function fmt(v){v=Number(v);return isFinite(v)?String(Math.round(v*10000)/10000):'待核实';}
 // 扫码后本轮实收超过应收：返回提示文字（服务端给 over_receipt 时）
 function overText(o){if(!o)return '';return '超收：'+(o.name||o.sku_id||'')+' 本轮已收 '+fmt(o.recv)+' / 应收 '+fmt(o.plan)+'，多 '+fmt(o.over);}
 root.ScanFeedback={SOUNDS,tone,beep,flash,show,looksLikeScan,overText,setMuted:v=>{muted=!!v;mutedAt=Date.now();}};
})(window);

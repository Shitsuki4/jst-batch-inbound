/* 2.0.12: one-paste Windows CF_HTML + local PNG. No ERP writes or message sending. */
(function () {
  'use strict';
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];}); }
  function snapshot(payload, options) {
    var p={session:payload.session || {},receiving_round:ReceivingRoundUI.data(payload)};
    if(options && options.fullTable) {p.full_table=options.fullTable;p.purchase_total=payload.live_purchase_total || [];}
    return p;
  }
  function totalsPayload(p) {
    var rs=p.purchase_total || [], known=rs.every(function(r){return r.plan != null;});
    return {session:p.session,isPurchaseTotal:true,receiving_round:{rows:rs,
      warnings:['采购获取时间：'+(p.session.po_refreshed_at || '尚未核实')+'；实收仅为本条记录各物流明细合计，不含其他收货轮次。'],
      progress:{plan:known?rs.reduce(function(n,r){return n+r.plan;},0):null,recv:rs.reduce(function(n,r){return n+r.recv;},0)}}};
  }
  function progress(p) {return p.isPurchaseTotal?'采购总量 '+ReceivingRoundUI.num(p.receiving_round.progress.plan)+' / 实收 '+ReceivingRoundUI.num(p.receiving_round.progress.recv):ReceivingRoundUI.progress(p.receiving_round);}
  function rows(payload, only) { return (payload.receiving_round.rows || []).filter(function(r){return !only || PurchasePairs.normalize(r).kind !== 'good';}); }
  function countText(payload, only) {
    var all=payload.receiving_round.rows || [], selected=rows(payload,true), unknown=selected.filter(function(r){return PurchasePairs.normalize(r).kind === 'unknown';}).length;
    return (only?'仅显示差异':'显示全部')+' · 当前显示 '+(only?selected.length:all.length)+' / 全部 '+all.length+' 项 · 已收齐 '+(all.length-selected.length)+' 项 / 差异及待核实 '+selected.length+' 项'+(unknown?'（待核实 '+unknown+' 项）':'')+(all.length && all.length===selected.length?'；没有已收齐商品，切换后商品范围相同':'');
  }
  function table(payload, only) {
    if (!only && payload.full_table) return '<p class="diff-count">'+esc(countText(totalsPayload(payload),false))+'</p>'+payload.full_table;
    var r=payload.receiving_round, visible=rows(payload,only);
    // Full view is a real full SKU table, not an unchanged difference table with
    // completed rows appended below the fold. Keep the same complete pairing
    // context for filtering, manual layout and exported images.
    var html=only?PurchasePairs.table(r.rows || [],true):ReceivingRoundUI.report(Object.assign({},r,{warnings:[]}));
    if (!visible.length) html=html.replace('<tbody></tbody>','<tbody><tr><td colspan="4" class="report-empty">'+((r.rows||[]).length?'全部已收满，无差异':'暂无商品明细，无法校验')+'</td></tr></tbody>');
    return '<p class="diff-count">'+esc(countText(payload,only))+'</p>'+html;
  }
  function eSid(p){return esc(p.session.id||0);}
  function render(payload, options) {
    var p=snapshot(payload,options), only=!!(options && options.onlyDiff);
    return '<section class="difference-report" data-report="'+esc(JSON.stringify(p))+'">'
      +'<button type="button" class="btn btn-ghost btn-sm" data-watch-source="receipt" data-watch-sid="'+eSid(p)+'">标记待改单 / 采购单跟踪</button>'
      +'<div class="diff-toolbar"><label><input type="checkbox" class="diff-only"'+(only?' checked':'')+'> 仅显示差异（含待核实）</label>'
      +'<div class="diff-copy-buttons"><button type="button" class="btn btn-ghost btn-sm" data-report-copy="all">复制完整结果（含已收满）</button>'
      +'<button type="button" class="btn btn-primary btn-sm" data-report-copy="diff">仅复制差异</button></div></div>'
      +(inboundIDs(p).length?'<p class="diff-order-ids">入库单号：'+esc(inboundIDs(p).join('、'))+'</p>':'')
      +'<p class="hint diff-copy-hint">电脑端点击一次复制，再到微信粘贴一次：单号文字＋结果图片。文字和图片均含已有入库单号。</p>'
      +'<p class="diff-copy-status" role="status" aria-live="polite"></p><div class="diff-copy-fallback"></div>'
      +'<div class="diff-legend" aria-label="行颜色说明"><span class="complete"><i></i>收齐</span><span class="difference"><i></i>少收 / 多收</span><span class="extra"><i></i>采购单外</span><span class="unknown"><i></i>待核实</span></div>'
      +'<div class="diff-warnings">'+ReceivingRoundUI.notes(p.receiving_round)+'</div>'
      +'<div class="diff-report-view">'+table(p,only)+'</div></section>';
  }
  function inboundIDs(p) { return (p.session.inbound_io_ids||[]).map(function(v){return String(v==null?'':v).trim();}).filter(function(v,i,a){return v && a.indexOf(v)===i;}); }
  function identifiers(p) {
    var ids=inboundIDs(p);
    return '采购单：'+(p.session.po_id||'未提供')+'\n外部单号：'+(p.session.outer_po_id||'未提供')+(ids.length?'\n入库单号：'+ids.join('、'):'');
  }
  function label(p,only) { if(p.isPurchaseTotal)return '采购总量对照（实时采购 / 本条记录实收）'; return '差异校验结果 · '+(only?'仅差异（含待核实）':'完整结果（含已收满）'); }
  function productLines(r,total) {
    var list=['编码：'+(r.sku_id||'未提供'),'商品：'+(r.name||'未提供商品名')];
    if(r.spec && r.spec!==r.name)list.push('规格：'+r.spec);
    var bars=r.sku_codes || (r.sku_code?[r.sku_code]:[]),aux=r.auxiliary_codes || [];
    if(bars.length)list.push('69码 / 条码：'+bars.join(' · '));
    if(aux.length)list.push('辅助码：'+aux.join(' · '));
    if(!bars.length&&!aux.length)list.push('69码 / 辅助码：未提供');
    if(!total)list.push('采购 '+ReceivingRoundUI.num(r.ordered)+' / 入库 '+ReceivingRoundUI.num(r.erp_received));
    if(r.extra)list.push(total?'采购单外商品':'本轮计划外商品');
    return list;
  }
  function text(p,only) {
    if(!only && p.purchase_total)p=totalsPayload(p);
    var r=p.receiving_round, selected=rows(p,only);
    var out=[identifiers(p),label(p,only),'收货轮次：#'+(p.session.id||r.id||'—'),'开始时间：'+(p.session.started_at||r.started_at||'未提供'),
      progress(p),countText(p,only)].concat(r.warnings||[]);
    selected.forEach(function(row){out.push(productLines(row,p.isPurchaseTotal).join('；')+(p.isPurchaseTotal?'；采购总量 ':'；本轮应收 ')+ReceivingRoundUI.num(row.plan)+(p.isPurchaseTotal?' / 实收 ':' / 本轮已收 ')+ReceivingRoundUI.num(row.recv)+' / '+(row.reason||'待核实'));});
    if(!selected.length)out.push((r.rows||[]).length?'全部已收满，无差异':'暂无商品明细，无法校验');
    return out.join('\n');
  }
  // Draw from the complete report data, never the scroll-clipped dialog DOM.
  function image(p,only) {
    if(!only && p.purchase_total)return legacyImage(totalsPayload(p),false);
    return PurchasePairs.image(p.receiving_round.rows || [],{title:label(p,only),only:only,headers:[identifiers(p),'收货轮次：#'+(p.session.id||'—'),'开始时间：'+(p.session.started_at||'未提供'),ReceivingRoundUI.progress(p.receiving_round),countText(p,only)].concat(p.receiving_round.warnings||[])});
  }
  function legacyImage(p,only) {
    return Promise.resolve(document.fonts && document.fonts.ready).then(function(){
      var width=1200, canvas=document.createElement('canvas'), ctx=canvas.getContext('2d');
      if(!ctx)throw new Error('当前浏览器无法生成图片');
      var ops=[], y=34, margin=32, font='"Microsoft YaHei", "PingFang SC", sans-serif';
      function wrap(value,max,size,bold){
        ctx.font=(bold?'600 ':'')+size+'px '+font;
        var lines=[],current='';
        Array.from(String(value==null?'':value)).forEach(function(c){
          if(c==='\n'){lines.push(current);current='';return;}
          if(current && ctx.measureText(current+c).width>max){lines.push(current);current=c;}else current+=c;
        });lines.push(current);return lines;
      }
      function put(value,x,top,max,size,color,bold){var lines=wrap(value,max,size,bold),step=Math.ceil(size*1.55);lines.forEach(function(line,i){ops.push({type:'text',value:line,x:x,y:top+i*step,size:size,color:color||'#24354a',bold:bold});});return lines.length*step;}
      function line(value,size,color,bold){y+=put(value,margin,y,width-margin*2,size,color,bold)+8;}
      line(label(p,only),34,'#173d68',true);
      line(identifiers(p),28,'#24354a',true);
      line('收货轮次 #'+(p.session.id||p.receiving_round.id||'—')+'   开始时间：'+(p.session.started_at||p.receiving_round.started_at||'未提供'),23,'#334155');
      line(progress(p)+(p.isPurchaseTotal?'':'（以上为全轮合计）'),26,'#1f6feb',true);
      line(countText(p,only),23,'#334155');
      line('颜色：收齐 · 绿色 / 少收、多收 · 黄色 / 采购单外、待核实 · 灰色',23,'#334155');
      (p.receiving_round.warnings||[]).forEach(function(w){line('核对提醒：'+w,23,'#855b12');});
      var cols=[32,698,850,1002], widths=[666,152,152,166], headerY=y;
      ops.push({type:'rect',x:32,y:y,w:1136,h:58,color:'#edf2f8'});
      (p.isPurchaseTotal?['商品信息','采购总量','实收','差额']:['商品信息','本轮应收','本轮已收','差额']).forEach(function(h,i){put(h,cols[i]+12,headerY+10,widths[i]-24,24,'#334155',true);});y+=58;
      var selected=rows(p,only);
      selected.forEach(function(r){
        var appearance=ReceivingRoundUI.appearance(r), productWidth=widths[0]-28;
        // Use the same measured font and line-height for layout and painting.
        var prod=productLines(r,p.isPurchaseTotal).map(function(value,i){return {value:value,size:i<2?28:24,bold:i<2,color:i<2?'#24354a':'#334155'};}), height=28;
        prod.forEach(function(v){height+=wrap(v.value,productWidth,v.size,v.bold).length*Math.ceil(v.size*1.55);});
        var nums=[ReceivingRoundUI.num(r.plan),ReceivingRoundUI.num(r.recv),r.reason||'待核实'];
        nums.forEach(function(v,i){var size=i===2?26:32;height=Math.max(height,wrap(v,widths[i+1]-24,size,true).length*Math.ceil(size*1.55)+28);});
        ops.push({type:'rect',x:32,y:y,w:1136,h:height,color:appearance.fill});
        ops.push({type:'rect',x:32,y:y,w:4,h:height,color:appearance.accent});
        var py=y+14;prod.forEach(function(v){py+=put(v.value,46,py,productWidth,v.size,v.color,v.bold);});
        nums.forEach(function(v,i){var size=i===2?26:32,block=wrap(v,widths[i+1]-24,size,true).length*Math.ceil(size*1.55);
          put(v,cols[i+1]+12,y+Math.max(14,(height-block)/2),widths[i+1]-24,size,i===1?'#154080':i===2?appearance.ink:'#24354a',true);});
        y+=height;ops.push({type:'rect',x:32,y:y-1,w:1136,h:1,color:'#dde5ee'});
      });
      if(!selected.length){y+=18;line((p.receiving_round.rows||[]).length?'全部已收满，无差异':'暂无商品明细，无法校验',22,'#334155');}
      y+=32;
      if(y>30000)throw new Error('结果超过单张图片高度上限，未截断或复制；请使用仅复制差异，或导出明细核对');
      canvas.width=width;canvas.height=Math.ceil(y);ctx=canvas.getContext('2d');ctx.fillStyle='#ffffff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.textBaseline='top';
      ops.forEach(function(o){ctx.fillStyle=o.color;if(o.type==='rect')ctx.fillRect(o.x,o.y,o.w,o.h);else{ctx.font=(o.bold?'600 ':'')+o.size+'px '+font;ctx.fillText(o.value,o.x,o.y);}});
      return new Promise(function(resolve,reject){canvas.toBlob(function(blob){if(!blob)return reject(new Error('图片生成失败，未复制'));var reader=new FileReader();reader.onerror=function(){reject(new Error('图片读取失败'));};reader.onload=function(){resolve({blob:blob,dataUrl:reader.result,width:canvas.width,height:canvas.height});};reader.readAsDataURL(blob);},'image/png');});
    });
  }
  function nativeCapability() {
    if(!/^(localhost|127\.0\.0\.1|\[::1\])$/i.test(location.hostname))return Promise.resolve({supported:false});
    return fetch('/api/report/clipboard/status',{cache:'no-store'}).then(function(response){
      if(response.status===404)return {supported:false};
      return response.json().then(function(value){if(!response.ok || !value.ok)throw new Error(value.msg || '本机复制服务不可用');return value;});
    });
  }
  function copyResult(box,p,only){
    var buttons=box.querySelectorAll('[data-report-copy]'),status=box.querySelector('.diff-copy-status'),fallback=box.querySelector('.diff-copy-fallback');
    if(box.dataset.copyBusy==='1')return;
    box.dataset.copyBusy='1';Array.prototype.forEach.call(buttons,function(b){b.disabled=true;});
    status.textContent='正在生成'+(only?'差异':'完整')+'结果图片…';fallback.innerHTML='';
    var result=image(p,only);
    function fail(error){
      status.textContent='未完成复制：'+(error && error.message || '剪贴板不可用')+'。';
      return result.then(function(img){
        var filename=String(p.session.po_id||'采购单').replace(/[\\/:*?"<>|\r\n]/g,'_')+'-'+(only?'差异':'完整')+'结果.png';
        fallback.innerHTML='<p>本次图文复制未成功，可重试；以下保存和手动复制仅为备用，不代表一次粘贴已完成。</p><a class="btn btn-ghost btn-sm" download="'+esc(filename)+'" href="'+esc(img.dataUrl)+'">保存结果图片</a>'
          +'<label>单号文字<textarea readonly class="diff-manual-text">'+esc(identifiers(p))+'</textarea></label>'
          +'<details><summary>预览待复制图片</summary><img alt="'+esc(label(p,only))+'" src="'+esc(img.dataUrl)+'"></details>';
      },function(e){status.textContent='未完成复制：'+e.message;});
    }
    Promise.all([nativeCapability(),result]).then(function(values){
      var capability=values[0],img=values[1];
      if(capability.supported){
        return fetch('/api/report/clipboard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:capability.token,text:identifiers(p),png:img.dataUrl})})
          .then(function(response){return response.json().then(function(value){if(!response.ok || !value.ok)throw new Error(value.msg || '本机图文复制失败');return value;});})
          .then(function(){status.textContent='已复制单号文字＋'+(only?'差异':'完整')+'图片，请到本机微信粘贴一次，检查后发送。';});
      }
      // Compatibility only. A browser's alternate MIME representations do NOT prove WeChat delivery.
      if(!window.isSecureContext || !navigator.clipboard || !navigator.clipboard.write || !window.ClipboardItem)throw new Error('当前设备不支持图文剪贴板，请在运行助手的Windows电脑端复制');
      return navigator.clipboard.write([new ClipboardItem({'image/png':img.blob,'text/plain':new Blob([text(p,only)],{type:'text/plain'}),'text/html':new Blob(['<div><p>'+esc(identifiers(p)).split(String.fromCharCode(10)).join('<br>')+'</p><img src="'+img.dataUrl+'"></div>'],{type:'text/html'})})])
        .then(function(){status.textContent='已复制兼容图文；当前并非本机Windows复制，请核对粘贴结果，不能保证微信同时接收文字和图片。';});
    }).catch(fail).then(function(){box.dataset.copyBusy='0';Array.prototype.forEach.call(buttons,function(b){b.disabled=false;});},function(){box.dataset.copyBusy='0';Array.prototype.forEach.call(buttons,function(b){b.disabled=false;});status.textContent='未完成复制，请重试';});
  }
  document.addEventListener('change',function(e){if(!e.target.matches('.diff-only'))return;var box=e.target.closest('.difference-report'),p=JSON.parse(box.dataset.report);box.querySelector('.diff-report-view').innerHTML=table(p,e.target.checked);});
  document.addEventListener('click',function(e){var b=e.target.closest('[data-report-copy]');if(!b)return;e.preventDefault();var box=b.closest('.difference-report');copyResult(box,JSON.parse(box.dataset.report),b.dataset.reportCopy==='diff');});
  window.DifferenceReport={render:render,rows:rows,text:text,image:image,identifiers:identifiers};
}());

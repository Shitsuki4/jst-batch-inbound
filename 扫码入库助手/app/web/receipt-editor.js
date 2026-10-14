/* Local receipt metadata editor: no catalog creation, no quantity changes. */
(function () {
  'use strict';
  var box, current, busy = false, previous;
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function button(line, locked) {
    if (line.matched || !line.id) return '';
    return '<button type="button" class="btn btn-ghost btn-sm js-edit-receipt" data-line="' + esc(JSON.stringify(line)) + '"' + (locked || line.inbound_job_id ? ' disabled' : '') + '>编辑资料</button>';
  }
  function close() { if (busy) return; box.hidden = true; current = null; if (previous && previous.isConnected) previous.focus(); }
  function open(line) {
    if (window.receiptEditAllowed && !window.receiptEditAllowed()) return;
    previous = document.activeElement; current = line;
    if (!box) {
      box = document.createElement('div'); box.className = 'receipt-editor-mask'; box.hidden = true;
      document.body.appendChild(box);
      box.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
        if (e.key === 'Tab') {
          var els = Array.from(box.querySelectorAll('input,button')).filter(function(x) { return !x.disabled; });
          var first = els[0], last = els[els.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      });
    }
    var fields = [['sku_id','商品编码',200],['name','商品名称',300],['sku_code','条码（69码）',200],['spec','规格',300]];
    box.innerHTML = '<form class="receipt-editor-card" role="dialog" aria-modal="true" aria-labelledby="re-title">'
      + '<h3 id="re-title">编辑本地收货商品资料</h3><p>仅修改这一物流子批次的收货记录，不创建商品资料、不修改采购单。实收数量 <b>' + esc(line.qty) + '</b> 保持不变。</p>'
      + fields.map(function(f) { return '<label>' + f[1] + '<input class="input" name="' + f[0] + '" maxlength="' + f[2] + '" value="' + esc(line[f[0]]) + '" autocomplete="off"' + (f[0] === 'sku_id' || f[0] === 'name' ? ' required' : '') + '></label>'; }).join('')
      + '<p class="inbound-warning">商品资料搜不到也可保留收货明细；生成入库时必须查到最终编码。与采购单编码一致时会重新匹配，重复编码不会自动合并。</p>'
      + '<p id="re-error" role="alert"></p><div class="row"><button type="button" class="btn btn-ghost re-cancel">取消</button><button type="submit" class="btn btn-primary">保存资料</button></div></form>';
    box.hidden = false; box.querySelector('.re-cancel').onclick = close;
    box.querySelector('form').onsubmit = async function(e) {
      e.preventDefault(); if (busy) return; busy = true;
      var values = {}; fields.forEach(function(f) { values[f[0]] = box.querySelector('[name="' + f[0] + '"]').value.trim(); });
      box.querySelectorAll('button,input').forEach(function(x) { x.disabled = true; });
      try {
        var res = await fetch('/api/line/edit', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({line_id:line.id,sid:line.session_id,batch_id:line.batch_id,expected:line,values:values})});
        var data = await res.json(); if (!data.ok) throw Error(data.msg || '保存失败');
        busy = false; close(); document.dispatchEvent(new CustomEvent('receipt-line-updated', {detail:data}));
      } catch(err) {
        box.querySelector('#re-error').textContent = (err.message || '网络异常') + '。若网络中断，请取消并刷新核对，不要重复添加商品。';
      } finally { busy = false; box.querySelectorAll('button,input').forEach(function(x) { x.disabled = false; }); }
    };
    box.querySelector('input').focus();
  }
  window.ReceiptEditor = {button:button,isOpen:function() { return !!box && !box.hidden; }};
  document.addEventListener('click', function(e) {
    var b = e.target.closest && e.target.closest('.js-edit-receipt');
    if (b && !b.disabled) { try { open(JSON.parse(b.dataset.line)); } catch(_) {} }
  });
}());

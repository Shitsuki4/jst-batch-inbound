/* Shared modal barcode disambiguation UI for all receiving modes. */
(function () {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let dialog, active = false;
  function open(options) {
    if (active) return;
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.className = 'product-picker';
      dialog.setAttribute('aria-labelledby', 'product-picker-title');
      document.body.appendChild(dialog);
    }
    active = true;
    const rows = options.rows || [], previous = document.activeElement;
    dialog.innerHTML = '<div class="modal-hd"><span id="product-picker-title">请选择商品（条码重复）</span><button type="button" class="modal-x" aria-label="关闭商品选择">×</button></div>'
      + '<div class="modal-bd"><p>条码 / 编码 <b>' + esc(options.code) + '</b> 命中 <b>' + rows.length + '</b> 个商品，请选择实际收货的商品。未选择前不会登记数量。</p>'
      + rows.map((r, i) => '<button type="button" class="cand" data-product="' + i + '"><span class="cand-pic"><span class="cand-pic-ph">加载图片…</span></span><span class="cand-main"><span class="cand-title">' + esc(r.name || r.sku_id) + '</span><span class="cand-meta">商品编码：<b>' + esc(r.sku_id) + '</b></span><span class="cand-meta">条码：<b>' + esc(r.sku_code || '—') + '</b></span><span class="cand-meta">规格：' + esc(r.spec || '—') + (r.match_via ? ' · ' + esc(r.match_via) + '命中' : '') + '</span></span></button>').join('')
      + '</div><div class="modal-ft"><span class="hint">方向键选择 · Enter 确认 · Esc 取消</span><button type="button" class="btn btn-ghost" data-cancel>取消</button></div>';
    function finish(row) {
      if (!active) return;
      active = false; dialog.close();
      if (row) options.onChoose(row);
      else if (options.onCancel) options.onCancel();
      else previous?.focus();
    }
    dialog.onclick = ev => {
      const choice = ev.target.closest('[data-product]');
      if (choice) finish(rows[Number(choice.dataset.product)]);
      else if (ev.target.closest('[data-cancel],.modal-x')) finish(null);
    };
    dialog.oncancel = ev => { ev.preventDefault(); finish(null); };
    dialog.onkeydown = ev => {
      if (['F1', 'F2'].includes(ev.key)) { ev.preventDefault(); ev.stopPropagation(); }
      if (!['ArrowDown', 'ArrowUp'].includes(ev.key)) return;
      ev.preventDefault();
      const buttons = [...dialog.querySelectorAll('[data-product]')];
      const i = buttons.indexOf(document.activeElement);
      buttons[(i + (ev.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus();
    };
    dialog.showModal(); dialog.querySelector('[data-product]')?.focus();
    dialog.querySelectorAll('[data-product]').forEach((button, i) => {
      const pic = button.querySelector('.cand-pic'), row = rows[i];
      const show = url => {
        if (!url || !/^https?:\/\//i.test(url)) { pic.textContent = '暂无图片'; return; }
        const img = document.createElement('img'); img.alt = row.name || row.sku_id;
        img.onerror = () => { pic.textContent = '暂无图片'; }; img.src = url; pic.replaceChildren(img);
      };
      if (row.pic) show(row.pic);
      else fetch('/api/item/pic?code=' + encodeURIComponent(row.sku_id) + '&owner=' + encodeURIComponent(options.owner || '') + '&auth=' + encodeURIComponent(options.auth || ''))
        .then(r => r.json()).then(r => show(r.pic)).catch(() => show(''));
    });
  }
  window.ProductPicker = {open, isOpen: () => active};
})();

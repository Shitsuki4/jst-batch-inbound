(function () {
  'use strict';
  // Embedded workbench uses the parent's persistent navigation; no duplicate header/entry.
  if (window.parent !== window && document.getElementById('board')) return;
  const frame = document.getElementById('purchase-frame');
  let entry = document.querySelector('.purchase-entry');
  if (!entry) {
    entry = document.createElement('a'); entry.className = 'purchase-entry tab'; entry.href = '/#purchase';
    entry.innerHTML = '采购单 <span class="purchase-badge yellow" hidden></span><span class="purchase-badge red" hidden></span>';
    const place = document.querySelector('nav.tabs, .tabs, header');
    if (place) place.appendChild(entry);
  }
  function paint(d) {
    document.querySelectorAll('.purchase-entry').forEach(el => {
      for (const key of ['yellow', 'red']) {
        const dot = el.querySelector('.' + key); if (!dot) continue;
        const count = Math.max(0, Number(d[key]) || 0);
        dot.textContent = count; dot.hidden = !count;
        dot.title = (key === 'yellow' ? '已改单' : '已匹配') + '待完成 ' + count + ' 单';
        dot.setAttribute('aria-label', dot.title);
      }
    });
  }
  async function badges() { try { const d = await (await fetch('/api/purchase-watch/list')).json(); if (d.ok) paint(d); } catch (_) {} }
  badges(); setInterval(badges, 15000);
  if (frame) {
    document.addEventListener('app:tab', ev => {
      if (ev.detail === 'purchase') {
        if (!frame.getAttribute('src')) frame.src = '/purchase-watch.html?embedded=1';
        if (!/^#purchase(?:\/\d+)?$/.test(location.hash)) history.replaceState(null, '', '#purchase');
        frame.contentWindow.postMessage({type: 'purchase-refresh'}, location.origin);
        badges();
      } else if (location.hash.startsWith('#purchase')) history.replaceState(null, '', location.pathname + location.search);
    });
    function openHash() {
      if (/^#purchase(?:\/\d+)?$/.test(location.hash)) entry.click();
    }
    window.addEventListener('hashchange', openHash); setTimeout(openHash, 0);
    window.addEventListener('message', ev => {
      if (ev.origin !== location.origin || ev.source !== frame.contentWindow) return;
      if (ev.data && ev.data.type === 'purchase-badges') paint(ev.data);
    });
  }
  if (document.getElementById('check-status')) {
    const b = document.createElement('button'); b.textContent = '标记待改单 / 打开采购单跟踪'; b.dataset.watchSource = 'unplanned';
    document.getElementById('check-status').after(b);
  }
  document.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-watch-source]'); if (!b) return;
    const sid = b.dataset.watchSid || window.purchaseUnplannedSid;
    if (!sid) { alert('请先打开一个收货批次并关联采购单'); return; }
    b.disabled = true;
    try {
      const d = await (await fetch('/api/purchase-watch/action', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'register', source: b.dataset.watchSource, sid: Number(sid)})})).json();
      if (!d.ok) throw Error(d.msg);
      if (frame) { location.hash = 'purchase/' + d.record.id; entry.click(); badges(); }
      else location.href = '/#purchase/' + d.record.id;
    } catch (e) { alert(e.message); } finally { b.disabled = false; }
  });
})();

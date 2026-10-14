/* Keep each receiving mode inside the same application shell and retain its work. */
(function () {
  'use strict';
  const modes = ['joint', 'unplanned'];
  const summaries = {}, requested = {};
  const labels = {joint: '多采购单合并收货', unplanned: '无采购单收货'};
  const frames = () => [...document.querySelectorAll('iframe[data-receiving-frame]')];
  function updateStatus(name, state) {
    summaries[name] = labels[name] + (state ? ' · ' + state.session.input_no + ' · ' + state.batches.length + ' 个物流单 / 实收 ' + state.total + ' 件' : ' · 尚未创建批次');
    if (document.querySelector('.view.active')?.dataset.view === name) document.getElementById('receiving-mode-status').textContent = summaries[name];
  }
  function busy() {
    return frames().some(f => f.hasAttribute('src') && (!f.dataset.ready ||
      f.contentWindow.ReceivingDesk?.blocked()));
  }
  function show(name) {
    if (document.getElementById('app').hidden) return;
    document.getElementById('status-box').hidden = modes.includes(name);
    document.getElementById('receiving-mode-status').hidden = !modes.includes(name);
    document.getElementById('receiving-mode-status').textContent = summaries[name] || labels[name] || '';
    if (!modes.includes(name)) return;
    const f = document.getElementById(name + '-frame');
    if (!f.hasAttribute('src')) {
      let url = new URL('/' + name + '.html', location.origin);
      const remembered = requested[name] || sessionStorage.getItem('receiving:return:' + name);
      if (remembered && new RegExp('^/' + name + '\\.html\\?sid=\\d+&bid=\\d+$').test(remembered)) url = new URL(remembered, location.origin);
      const query = new URLSearchParams(location.search);
      if (!requested[name] && query.get('view') === name && /^\d+$/.test(query.get('sid') || '')) {
        url.searchParams.set('sid', query.get('sid'));
        url.searchParams.set('bid', /^\d+$/.test(query.get('bid') || '') ? query.get('bid') : '0');
      }
      url.searchParams.set('embed', '1');
      f.onload = () => {
        f.dataset.ready = '1';
        f.contentWindow.ReceivingDesk?.activate();
      };
      f.src = url.pathname + url.search;
      delete requested[name];
    } else f.contentWindow.ReceivingDesk?.activate();
  }
  function beforeSwitch(from, to) {
    if (from === to) return;
    if (['home', ...modes].includes(from)) {
      if (window.__nativeVideo) window.__nativeVideo.ready = false;
      window.dispatchEvent(new CustomEvent('native-receipt', {detail: {kind: 'stop'}}));
    }
    if (to === 'settings' && modes.includes(from)) sessionStorage.setItem('receiving:last-work', '/?view=' + from);
  }
  function reset() {
    for (const f of frames()) { f.removeAttribute('src'); delete f.dataset.ready; }
    for (const name of modes) { sessionStorage.removeItem('receiving:return:' + name); delete summaries[name]; delete requested[name]; }
  }
  function openLink(url) {
    const name = modes.find(m => url.pathname === '/' + m + '.html');
    if (!name) return false;
    if (!window.ReceivingApp?.canNavigate()) return true;
    const sid = url.searchParams.get('sid'), bid = url.searchParams.get('bid');
    if (/^\d+$/.test(sid || '')) {
      requested[name] = '/' + name + '.html?sid=' + sid + '&bid=' + (/^\d+$/.test(bid || '') ? bid : '0');
      const f = document.getElementById(name + '-frame');
      f.removeAttribute('src'); delete f.dataset.ready;
    }
    window.ReceivingApp.navigate(name);
    return true;
  }
  window.ReceivingWorkspace = {busy, show, beforeSwitch, reset, updateStatus, openLink};
  document.addEventListener('app:tab', e => show(e.detail));
  document.addEventListener('click', e => {
    const a = e.target.closest('a[href]');
    if (!a || a.hasAttribute('download')) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;
    if (modes.some(m => url.pathname === '/' + m + '.html')) { e.preventDefault(); openLink(url); }
  });
})();

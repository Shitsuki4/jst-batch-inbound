(function () {
  'use strict';
  if (window.parent !== window) {
    document.documentElement.classList.add('receiving-embedded');
    Object.defineProperty(window, '__nativeVideo', {configurable: true,
      get: () => parent.__nativeVideo, set: value => { parent.__nativeVideo = value; }});
    document.addEventListener('click', e => {
      const a = e.target.closest('a[href]');
      if (!a || a.hasAttribute('download')) return;
      const url = new URL(a.href, location.href);
      if (url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
      const mode = url.pathname === '/joint.html' ? 'joint' : url.pathname === '/unplanned.html' ? 'unplanned' : url.pathname === '/express.html' ? 'express' : url.searchParams.get('view') || 'home';
      e.preventDefault(); if (!parent.ReceivingWorkspace?.openLink(url)) parent.ReceivingApp?.navigate(mode);
    });
  } else if (!new URLSearchParams(location.search).has('embed')) {
    const mode = location.pathname.includes('joint') ? 'joint' : location.pathname.includes('express') ? 'express' : 'unplanned';
    const q = new URLSearchParams(location.search); q.set('view', mode);
    location.replace('/?' + q);
  }
})();

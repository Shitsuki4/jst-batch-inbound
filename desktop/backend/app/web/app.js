/* 扫码入库助手 —— 原生前端，零框架，零构建 */
(function () {
  'use strict';

  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };
  var stateEpoch = 0, writesPending = 0, syncRunning = false;
  var poRefreshing = false, poRefreshNotice = null;
  var pauseReportBusy = false, finishBusy = false, qtySaveErrors = {}, diffContext = null;
  var S = {};                 // 服务端状态快照
  var mode = 'tracking';      // po 已由服务端控制；tracking / item
  var suppressRefocus = 0;
  var pending = null;         // 扫码后待确认数量：{code, batch_id, label}

  $('#btn-inbound-current').onclick = function () {
    confirmPendingThen(function () { if (S.session) window.openInbound(S.session.id); });
  };
  document.addEventListener('inbound-updated', function () { refreshAfterMutation(); refreshDiffDialog(); });
  function inboundButton(id) {
    return '<button class="btn btn-primary btn-sm" data-inbound-sid="' + id + '">按实收生成入库单 / 记录</button>';
  }

  /* -------------------- 基础工具 -------------------- */
  function api(path, opts) {
    opts = opts || {};
    var writing = (opts.method || 'GET') !== 'GET';
    if (writing) { stateEpoch++; writesPending++; }
    return fetch(path, {
      method: opts.method || 'GET',
      headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) { return r.json(); }).then(function (r) {
      if (writing) { writesPending--; stateEpoch++; }
      return r;
    }, function (e) { if (writing) { writesPending--; stateEpoch++; } throw e; });
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function num(v) { var f = parseFloat(v); return isNaN(f) ? 0 : f; }
  function fmtNum(v) { var f = parseFloat(v) || 0; return Math.abs(f - Math.round(f)) < 1e-6 ? String(Math.round(f)) : String(f); }
  function today() { var d = new Date(); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function pad(n) { return n < 10 ? '0' + n : '' + n; }

  var toastTimer = null;
  function toast(msg, type) {
    var t = $('#toast');
    t.textContent = msg;
    t.style.background = type === 'err' ? '#b3261e' : (type === 'warn' ? '#8a5a00' : '#1b2733');
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 2200);
  }

  var actx = null;
  function beep(freq, dur) {
    if (S.settings && S.settings.sound === '0') return;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      var o = actx.createOscillator(), g = actx.createGain();
      o.type = 'square'; o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, actx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.14, actx.currentTime + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime + dur);
      o.connect(g); g.connect(actx.destination);
      o.start(); o.stop(actx.currentTime + dur + 0.02);
    } catch (e) { /* 忽略 */ }
  }

  function flash(msg, kind) {
    var el = $('#scan-flash');
    el.textContent = msg || '';
    el.className = 'flash' + (kind ? ' ' + kind : '');
  }

  /* -------------------- 弹窗 -------------------- */
  var modalEsc = null;
  function openModal(title, html, buttons, onEsc) {
    diffContext = null;
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = html;
    var ft = $('#modal-foot');
    ft.innerHTML = '';
    (buttons || []).forEach(function (b) {
      var el = document.createElement('button');
      el.className = 'btn ' + (b.cls || '');
      el.textContent = b.text;
      el.onclick = function () { b.fn && b.fn(); };
      ft.appendChild(el);
    });
    $('#modal').hidden = false;
    modalEsc = onEsc || null;
  }
  function closeModal() { if (pauseReportBusy || finishBusy) return false; if(document.querySelector('#modal .qty-saving,#modal .qty-save-error,#modal .qty-unsaved')){toast('请先保存数量（按Enter或移开焦点），失败输入已保留','warn');return false;} $('#modal').hidden = true; modalEsc = null; diffContext = null; return true; }
  $('#modal-x').onclick = closeModal;

  function permAlert(hint) {
    var h = '<div class="perm-msg" style="text-align:center;padding:8px 0 2px">'
      + '<div style="font-size:17px;font-weight:700;color:#d93a3a">当前账号无此操作权限</div>'
      + (hint ? '<div style="margin-top:8px;color:#6b7a8d;font-size:13px">' + esc(hint) + '</div>' : '')
      + '</div>';
    openModal('操作提示', h, [{ text: '知道了', cls: 'btn-primary', fn: closeModal }], closeModal);
  }

  function handleFail(r) {
    if (!r) return permAlert();
    if (r.msg === '当前账号无此操作权限') { permAlert(r.hint); return true; }
    toast(r.msg || '操作失败', 'err');
    beep(180, 0.16);
    return false;
  }

  /* 二次确认弹窗（删除类操作统一走这里） */
  function confirmBox(title, msg, yesText, fn) {
    suppressRefocus = Date.now() + 6000;
    openModal(title, '<div style="padding:6px 0;text-align:center;line-height:1.7">' + msg + '</div>',
      [{ text: '取消', cls: 'btn-ghost', fn: function () { closeModal(); refocus(); } },
      { text: yesText || '确认', cls: 'btn-danger', fn: function () { closeModal(); fn(); } }],
      function () { closeModal(); refocus(); });
  }

  /* -------------------- 登录 -------------------- */
  // v_d_144 是阿里云盾给的客户端随机串，格式固定为「毫秒时间戳_32位十六进制」。
  // 在本机页面里拿不到时，按同样格式生成一个，避免服务端因缺字段拒绝。
  function synthVd144() {
    var hex = '';
    for (var i = 0; i < 32; i++) hex += '0123456789abcdef'[Math.floor(Math.random() * 16)];
    return Date.now() + '_' + hex;
  }

  function collectFingerprint(cb) {
    var got = { jd3: window.jdDeviceId || '', vd144: window.vipMarketClentId || '' };
    function done() {
      got.jd3 = window.jdDeviceId || '';
      got.vd144 = window.vipMarketClentId || synthVd144();
      cb(got);
    }
    if (got.jd3 && got.vd144) return done();
    var urls = ['https://gias.jd.com/js/td.js', 'https://g.alicdn.com/sj/securesdk/0.0.3/securesdk_v2.js'];
    urls.forEach(function (u) {
      var s = document.createElement('script');
      s.src = u; s.async = true;
      s.onerror = function () { };
      document.head.appendChild(s);
    });
    var waited = 0;
    (function poll() {
      got.jd3 = window.jdDeviceId || '';
      got.vd144 = window.vipMarketClentId || '';
      if ((got.jd3 && got.vd144) || waited > 2600) return done();
      waited += 160;
      setTimeout(poll, 160);
    })();
  }

  /* 登录成功后统一走这里 */
  function enterApp(user, warning) {
    $("#lg-password").value = "";
    $("#sm-code").value = "";
    $('#login').style.display = 'none';
    $('#app').hidden = false;
    $('#who').textContent = (user.uname || user.coid) + (user.coname ? ' · ' + user.coname : '');
    loadSettingsView();
    reload();
    window.ReceivingWorkspace?.show(document.querySelector(".view.active")?.dataset.view);
    if (warning) toast(warning, "warn");
  }

  function lgTip(msg, bad) {
    $('#lg-tip').innerHTML = msg || '';
    $('#lg-tip').className = 'login-tip' + (bad ? ' bad' : '');
    msg && bad && beep(180, 0.16);
  }

  /* ---- 常用账号：登录成功后服务端会记下账号名（只记账号、不存密码），
     下次打开直接点一下填进输入框，省得手输 ---- */
  var authRestoring = false;
  function loginPreferences() { return {remember_account: $('#lg-remember').checked, keep_signed_in: $('#lg-keep').checked}; }
  function loadSavedAccounts(initial) {
    return api('/api/accounts').then(function (r) {
      var box = $('#lg-accounts');
      if (!box || !r.ok) return;
      if (initial && r.preferences) {
        $('#lg-remember').checked = r.preferences.remember_account;
        $('#lg-keep').checked = r.preferences.keep_signed_in;
      }
      var rows = r.rows || [];
      box.hidden = !rows.length || !$('#lg-remember').checked;
      box.innerHTML = rows.length ? '<div class="lga-label">常用账号（点击填入）</div><div class="lga-list">' + rows.map(function(a) {
        return '<span class="lga-chip" data-name="' + esc(a.name) + '">' + esc(a.name) + '<i class="lga-x" data-del="' + esc(a.name) + '" title="不再记住该账号">×</i></span>';
      }).join('') + '</div>' : '';
      if (initial && $('#lg-remember').checked && rows.length && !$('#lg-account').value) {
        $('#lg-account').value = rows[0].name;
        if (rows[0].kind === 'sms') $('#sm-mobile').value = rows[0].name;
      }
    }).catch(function () {});
  }
  var savedAccountsReady = loadSavedAccounts(true);
  ['#lg-remember', '#lg-keep'].forEach(function(id) {
    $(id).onchange = function() {
      api('/api/login/preferences', {method:'POST', body:loginPreferences()}).then(function(r) {
        if (!r.ok) lgTip('登录偏好未保存，请重试', true);
        loadSavedAccounts(false);
      }).catch(function() { lgTip('登录偏好未保存，请重试', true); });
    };
  });

  document.addEventListener('click', function (e) {
    var del = e.target && e.target.closest ? e.target.closest('.lga-x') : null;
    if (del) {
      e.stopPropagation();
      if ($('#lg-account').value === del.dataset.del) $('#lg-account').value = '';
      if ($('#sm-mobile').value === del.dataset.del) $('#sm-mobile').value = '';
      api('/api/account/del', { method: 'POST', body: { name: del.dataset.del } })
        .then(function () { loadSavedAccounts(); });
      return;
    }
    var chip = e.target && e.target.closest ? e.target.closest('.lga-chip') : null;
    if (chip) {
      $('#lg-account').value = chip.dataset.name || '';
      $('#lg-password').focus();
    }
  });

  /* ---- tab 切换 ---- */
  $('#login-tabs').addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.ltab') : null;
    if (!b) return;
    var t = b.getAttribute('data-lt');
    $$('#login-tabs .ltab').forEach(function (x) { x.classList.toggle('active', x === b); });
    ['pw', 'sms', 'qr', 'official'].forEach(function (k) {
      var p = $('#lg-' + k);
      if (p) p.classList.toggle('active', k === t);
    });
    lgTip('');
    if (t === 'pw') $('#lg-account').focus();
    if (t === 'sms') $('#sm-mobile').focus();
  });

  /* ---- 一、账号密码（含图形验证码） ---- */
  function loadCaptcha() {
    var acc = $('#lg-account').value.trim();
    if (!acc) { lgTip('请先填写账号，再取图形验证码', true); $('#lg-account').focus(); return; }
    var img = $('#lg-captcha-img');
    img.src = '/api/captcha?account=' + encodeURIComponent(acc) + '&t=' + Date.now();
  }

  $('#lg-captcha-img').onclick = loadCaptcha;

  $('#lg-pw').onsubmit = function (e) {
    e.preventDefault();
    if (authRestoring) return;
    var acc = $('#lg-account').value.trim();
    var pwd = $('#lg-password').value;
    if (!acc || !pwd) { lgTip('请输入账号和密码', true); return; }
    var vcode = '';
    if (!$('#lg-captcha-row').hidden) {
      vcode = $('#lg-captcha').value.trim();
      if (!vcode) { lgTip('请输入图形验证码', true); $('#lg-captcha').focus(); return; }
    }
    var btn = $('#lg-submit');
    btn.disabled = true; btn.textContent = '登录中…';
    lgTip('');
    collectFingerprint(function (fp) {
      api('/api/login', {
        method: 'POST',
        body: { ...loginPreferences(), account: acc, password: pwd, verify_code: vcode, jd3: fp.jd3, vd144: fp.vd144 }
      }).then(function (r) {
        btn.disabled = false; btn.textContent = '登 录';
        if (r.ok && r.need_captcha) {
          // 服务端点名要图形验证码：拉图、展开输入框、清空旧值
          $('#lg-captcha-row').hidden = false;
          $('#lg-captcha').value = '';
          loadCaptcha();
          lgTip(r.msg || '请输入图形验证码后再登录', true);
          $('#lg-captcha').focus();
          return;
        }
        if (!r.ok) { lgTip(r.msg || '登录失败', true); return; }
        enterApp(r.user, r.warning);
      }).catch(function () {
        btn.disabled = false; btn.textContent = '登 录';
        lgTip('网络异常，请重试', true);
      });
    });
  };

  /* ---- 二、短信验证码 ---- */
  var smTimer = null;
  function tickSend(sec) {
    var b = $('#sm-send');
    clearInterval(smTimer);
    if (sec <= 0) { b.disabled = false; b.textContent = '重新获取'; return; }
    b.disabled = true;
    b.textContent = sec + 's';
    smTimer = setInterval(function () {
      sec--;
      if (sec <= 0) { clearInterval(smTimer); b.disabled = false; b.textContent = '重新获取'; }
      else b.textContent = sec + 's';
    }, 1000);
  }

  $('#sm-send').onclick = function () {
    if (authRestoring) return;
    var mobile = $('#sm-mobile').value.trim();
    if (!/^1\d{10}$/.test(mobile)) { lgTip('请输入正确的 11 位手机号', true); $('#sm-mobile').focus(); return; }
    var suffix = $('#sm-suffix-row').hidden ? '' : $('#sm-suffix').value.trim();
    if (!$('#sm-suffix-row').hidden && suffix.length !== 2) {
      lgTip('请输入手机号后两位', true); $('#sm-suffix').focus(); return;
    }
    var b = $('#sm-send');
    b.disabled = true; b.textContent = '发送中…';
    lgTip('');
    api('/api/sms/send', { method: 'POST', body: { mobile: mobile, suffix: suffix } })
      .then(function (r) {
        if (!r.ok) { b.disabled = false; b.textContent = '获取验证码'; lgTip(r.msg, true); return; }
        if (r.need_suffix) {
          // 该手机号绑了多个账号，要补尾号再发一次
          $('#sm-suffix-row').hidden = false;
          b.disabled = false; b.textContent = '获取验证码';
          lgTip(r.msg || ('该手机号绑定了多个账号，请输入手机号后两位（' + (r.prefix || '') + '）'), true);
          $('#sm-suffix').focus();
          return;
        }
        lgTip(r.msg || '验证码已发送');
        tickSend(60);
        $('#sm-code').focus();
      })
      .catch(function () {
        b.disabled = false; b.textContent = '获取验证码';
        lgTip('网络异常，请重试', true);
      });
  };

  $('#lg-sms').onsubmit = function (e) {
    e.preventDefault();
    var mobile = $('#sm-mobile').value.trim();
    var code = $('#sm-code').value.trim();
    if (!mobile) { lgTip('请输入手机号', true); $('#sm-mobile').focus(); return; }
    if (!code) { lgTip('请输入短信验证码', true); $('#sm-code').focus(); return; }
    var suffix = $('#sm-suffix-row').hidden ? '' : $('#sm-suffix').value.trim();
    var btn = $('#sm-submit');
    btn.disabled = true; btn.textContent = '登录中…';
    lgTip('');
    api('/api/sms/login', { method: 'POST', body: { ...loginPreferences(), mobile: mobile, code: code, suffix: suffix } })
      .then(function (r) {
        btn.disabled = false; btn.textContent = '登 录';
        if (!r.ok) { lgTip(r.msg || '登录失败', true); return; }
        enterApp(r.user, r.warning);
      })
      .catch(function () {
        btn.disabled = false; btn.textContent = '登 录';
        lgTip('网络异常，请重试', true);
      });
  };

  /* ---- 三、扫码登录（微信 / 钉钉，走聚水潭官方 OAuth） ---- */
  // 串行轮询官方二维码/登录状态；世代保护避免切换提供商时旧图像覆盖新界面。
  var qrTimer = null, qrEpoch = 0, qrKind = null, openingOfficial = false;
  function clearQrImage() { $('#qr-image').hidden = true; $('#qr-image').removeAttribute('src'); }
  function stopQrPoll() { clearTimeout(qrTimer); qrTimer = null; ++qrEpoch; }
  function qrWatch() {
    stopQrPoll();
    var epoch = qrEpoch, kind = qrKind, delay = 500;
    function poll() {
      if (epoch !== qrEpoch) return;
      api('/api/qr/poll').then(function(r) {
        if (epoch !== qrEpoch) return;
        if (r && r.logged) { stopQrPoll(); clearQrImage(); enterApp(r.user, r.warning); return; }
        if (kind !== 'official') {
          if (r && r.qr_kind === kind && r.qr_image && r.qr_image.indexOf('data:image/png;base64,') === 0) {
            if ($('#qr-image').getAttribute('src') !== r.qr_image) $('#qr-image').src = r.qr_image;
            $('#qr-image').hidden = false; delay = 2000;
            $('#qr-status').textContent = kind === 'weixin' ? '请使用微信扫码确认' : '请使用钉钉扫码确认';
          } else { delay = 750; clearQrImage(); $('#qr-status').textContent = (r && (r.pending || r.msg)) || '正在加载官方二维码…'; }
        } else if (r && r.pending) lgTip(r.pending, true);
      }).catch(function() {
        if (epoch === qrEpoch) { clearQrImage(); lgTip('登录连接暂不可用，正在重试；也可重新打开登录', true); }
      }).then(function() { if (epoch === qrEpoch) qrTimer = setTimeout(poll, delay); });
    }
    poll();
  }
  function openOfficial(kind, refresh) {
    if (openingOfficial) return;
    openingOfficial = true; stopQrPoll(); clearQrImage(); qrKind = kind;
    var epoch = qrEpoch;
    $('.qr-choices').hidden = kind !== 'official';
    $('#qr-view').hidden = kind === 'official';
    $('#qr-status').textContent = '正在加载官方二维码…';
    lgTip(kind === 'official' ? '正在打开官方登录窗口…' : '');
    api('/api/qr/open', {method:'POST', body:{...loginPreferences(), kind:kind, refresh:!!refresh}}).then(function(r) {
      openingOfficial = false;
      if (epoch !== qrEpoch) return;
      if (!r.ok) { $('#qr-status').textContent = r.msg || '打开失败，请重试'; lgTip(r.msg || '打开失败', true); return; }
      if (kind === 'official') lgTip(r.tip || '请在官方窗口登录并保持打开，成功后自动返回');
      qrWatch();
    }).catch(function() { openingOfficial = false; if (epoch === qrEpoch) lgTip('打开登录失败，请重试', true); });
  }
  $('.qr-choices').addEventListener('click', function(e) {
    var b = e.target.closest('.qr-choice');
    if (b) openOfficial(b.getAttribute('data-kind'));
  });
  $('#official-login').onclick = function() { openOfficial('official'); };
  $('#qr-refresh').onclick = function() { if (qrKind && qrKind !== 'official') openOfficial(qrKind, true); };
  $('#qr-back').onclick = function() {
    if (openingOfficial) return;
    stopQrPoll(); clearQrImage(); qrKind = null;
    $('#qr-view').hidden = true; $('.qr-choices').hidden = false; lgTip('');
    openingOfficial = true;
    api('/api/qr/cancel', {method:'POST'}).then(function() { openingOfficial = false; }, function() { openingOfficial = false; });
  };
  $('#qr-finish').onclick = function() { if (qrKind === 'official') qrWatch(); else openOfficial('official'); };

  $('#lg-account').focus();

  $('#btn-logout').onclick = function () {
    if (!navigationReady()) return;
    if (window.SettingsWorkspace && document.querySelector('.view.active')?.dataset.view === 'settings' && !SettingsWorkspace.beforeLeave()) return;
    api('/api/logout', { method: 'POST' }).then(function (r) { if (!r.ok) return; window.ReceivingWorkspace?.reset(); location.href = '/'; });
  };

  /* -------------------- 状态刷新 -------------------- */
  function reload(cb, bid) {
    // bid 指定时用该物流单号作为「当前单号」（删除别的单号后不至于跳走）
    var epoch = ++stateEpoch;
    return api('/api/state' + (bid ? '?batch_id=' + bid : '')).then(function (st) {
      if (epoch !== stateEpoch) return;
      S = st;
      renderAll();
      cb && cb();
    }).catch(function () { toast('与本地服务通信失败', 'err'); });
  }

  function renderAll() {
    var has = !!(S.session && S.session.state === 'active');
    $('#idle-card').hidden = has;
    $('#work-area').hidden = !has;
    if (has) {
      renderStatus();
      renderPoItems();
      renderLines();
      setMode(S.batches && S.batches.length ? 'item' : 'tracking', true);
      refocus();
    } else {
      $('#status-box').innerHTML = '<span class="st-empty">尚未创建收货批次</span>';
      setMode('po');
      clearPending();
    }
    renderPaused();
  }

  /* 挂起中的批次：退出批次后停在这里，点一下即可继续收货 */
  function renderPaused() {
    var wrap = $('#paused-wrap'), list = S.paused || [];
    if (!wrap) return;
    wrap.hidden = !list.length;
    $('#paused-count').textContent = list.length;
    if (!list.length) { $('#paused-list').innerHTML = ''; return; }
    $('#paused-list').innerHTML = list.map(function (p) {
      var pr = p.round_progress || { plan: null, recv: (p.progress || {}).recv || 0 };
      return '<div class="paused-item" data-id="' + p.id + '">'
        + '<div class="pi-main">'
        + '<div class="pi-title">采购单 ' + esc(p.po_id) + '</div>'
        + '<div class="pi-meta"><span>供应商：' + esc(p.supplier || '—') + '</span>'
        + '<span>' + (p.batches || 0) + ' 个物流单号</span>'
        + '<span>本轮已收 ' + fmtNum(pr.recv) + ' / ' + ReceivingRoundUI.num(pr.plan) + '</span>'
        + '<span>挂起于 ' + esc(p.paused_at || '—') + '</span></div>'
        + '</div>'
        + '<div class="pi-acts">'
        + '<button class="btn btn-ghost btn-sm js-paused-report" data-id="' + p.id + '">查看差异</button>'
        + '<button class="btn btn-primary btn-sm js-resume" data-id="' + p.id + '">继续收货</button>'
        + '<button class="btn btn-ghost btn-sm danger js-del-session" data-id="' + p.id + '">删除</button>'
        + '</div></div>';
    }).join('');
  }

  function renderStatus() {
    var s = S.session, b = S.batches || [], p = ReceivingRoundUI.data(S).progress || {};
    var plan = p.plan || 0, recv = p.recv || 0;
    var pct = plan > 0 ? Math.min(100, Math.round(recv / plan * 100)) : 0;
    var over = p.plan != null && recv > plan;
    var html = ''
      + cell('po', '当前采购单号', s.po_id + (s.outer_po_id ? '  （外部单号 ' + esc(s.outer_po_id) + '）' : ''))
      + cell('', '已录入物流单号', b.length + ' 个')
      + cell('', '已扫商品行', (S.lines || []).length + ' 行')
      + '<div class="st-cell st-prog">'
      + '  <div class="k">本轮已收 ' + fmtNum(recv) + ' / 应收 ' + ReceivingRoundUI.num(p.plan) + (p.plan == null ? '' : '（' + pct + '%）') + '</div>'
      + '  <div class="prog-bar"><div class="prog-inner' + (over ? ' over' : '') + '" style="width:' + pct + '%"></div></div>'
      + '</div>';
    var box = $('#status-box');
    box.innerHTML = html;
    if (!box.querySelector('.btn-exit')) {
      // 退出批次：随时可走，不丢数据
      var b1 = document.createElement('button');
      b1.className = 'btn btn-ghost btn-lg btn-exit';
      b1.textContent = '退出批次';
      b1.style.flex = 'none';
      b1.onclick = askExit;
      box.appendChild(b1);
      var br = document.createElement('button');
      br.className = 'btn btn-primary btn-lg btn-pause-report';
      br.textContent = '暂存并对单';
      br.style.flex = 'none';
      br.onclick = askPauseReport;
      box.appendChild(br);
      var b2 = document.createElement('button');
      b2.className = 'btn btn-danger btn-lg btn-finish';
      b2.textContent = '结单';
      b2.style.flex = 'none';
      b2.onclick = askFinish;
      box.appendChild(b2);
    }
    function cell(k, kk, vv) {
      return '<div class="st-cell ' + k + '"><div class="k">' + kk + '</div><div class="v">' + esc(vv) + '</div></div>';
    }
  }

  /* -------------------- 商家 / 分仓选择 -------------------- */
  // 一张采购单归属哪个货主、从哪个分仓收货，决定了查询时用的 co_id 上下文。
  // 默认「自动」轮换；跨仓收货时在这里指定，可以少一轮试探。
  function fillSelect(sel, list, cur, autoText) {
    var html = '<option value="">' + autoText + '</option>';
    (list || []).forEach(function (o) {
      // 有中文名就显示「名称（id）」方便核对；名称缺失或与 id 相同时只显示一次，
      // 免得出现「2147483647（2147483647）」这种没信息量的重复。
      var label = String(o.name || '') === String(o.id) ? String(o.id) : o.name + '（' + o.id + '）';
      html += '<option value="' + esc(o.id) + '"' + (String(cur) === String(o.id) ? ' selected' : '') + '>'
        + esc(label) + '</option>';
    });
    if (cur && html.indexOf('value="' + esc(cur) + '"') < 0) {
      html += '<option value="' + esc(cur) + '" selected>' + esc(cur) + '（已手动指定）</option>';
    }
    sel.innerHTML = html;
    sel.value = cur || '';
  }

  function loadPartners(refresh) {
    return api('/api/partners' + (refresh ? '?refresh=1' : '')).then(function (r) {
      if (!r.ok) return;
      fillSelect($('#pick-owner'), r.owners, r.pick_owner, '自动识别');
      fillSelect($('#pick-auth'), r.wms, r.pick_auth, '跟随货主 / 自动');
      if (!refresh) return;
      var n = (r.owners || []).length + (r.wms || []).length;
      if (!n) {
        // 刷新失败要如实说，别让用户以为"刷了但没变化"
        toast('刷新失败：' + (r.err_msg || '接口未返回清单') + '（可先确认已登录）', 'warn');
      } else {
        // 名称=id 说明接口没给中文名，这个要单独提示，否则用户只会看到一串数字
        var nameless = (r.wms || []).filter(function (o) { return String(o.name) === String(o.id); }).length;
        toast('已刷新：货主 ' + (r.owners || []).length + ' 个 / 分仓 ' + (r.wms || []).length + ' 个'
          + (nameless ? '（其中 ' + nameless + ' 个分仓接口未返回名称）' : ''), 'ok');
      }
    }).catch(function () { toast('刷新失败：请求出错', 'warn'); });
  }

  function savePick() {
    api('/api/settings', {
      method: 'POST',
      body: { pick_owner: $('#pick-owner').value, pick_auth: $('#pick-auth').value }
    });
  }
  $('#pick-owner').onchange = savePick;
  $('#pick-auth').onchange = savePick;
  $('#btn-refresh-partners').onclick = function () { loadPartners(true); };

  /* -------------------- 创建收货批次 / 退出 / 继续 -------------------- */
  function activate(newRoundFrom) {
    var no = $('#po-input').value.trim();
    if (!no) { $('#idle-msg').className = 'hint err'; $('#idle-msg').textContent = '请输入采购单号'; $('#po-input').focus(); return; }
    var btn = $('#btn-activate'), tip = $('#idle-msg');
    btn.disabled = true; btn.textContent = '查询中…';
    tip.className = 'hint'; tip.textContent = '正在查询聚水潭采购单…';
    api('/api/po/create', {
      method: 'POST',
      body: {
        no: no, id_type: $('#id-type').value, new_round_from: typeof newRoundFrom === 'number' ? newRoundFrom : null,
        owner_co_id: $('#pick-owner').value, authorize_co_id: $('#pick-auth').value
      }
    })
      .then(function (r) {
        btn.disabled = false; btn.textContent = '创建收货批次';
        if (!r.ok) { if (!handleFail(r)) { tip.className = 'hint err'; tip.textContent = r.msg; } return; }
        if (r.require_new_round_confirmation) {
          openModal('开始新一轮收货', '<p>' + esc(r.msg) + '</p>', [
            {text:'取消',cls:'btn-ghost',fn:closeModal},
            {text:'已核对，开始新一轮',cls:'btn-primary',fn:function(){closeModal();activate(r.previous_round_id);}}
          ]);
          return;
        }
        $('#po-input').value = '';
        tip.textContent = '';
        var how = r.reopened ? '已新建下一轮收货'  : (r.resumed ? '已回到挂起批次' : '已创建收货批次');
        // 「自动识别」时告诉用户这个号被当成了什么类型命中（如按外部单号匹配到采购单）
        var viaType = '';
        if ($('#id-type').value === 'auto' && r.id_type_label) {
          viaType = r.id_type === 'po_id' ? '' : ('（识别为' + r.id_type_label + '）');
        }
        toast(how + '：' + r.session.po_id + viaType, 'ok');
        beep(880, 0.08);
        reload();
      })
      .catch(function () { btn.disabled = false; btn.textContent = '创建收货批次'; permAlert(); });
  }
  $('#btn-activate').onclick = activate;
  $('#po-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); activate(); }
  });

  /* 退出批次：把当前收货中的单挂起，人立刻空闲，可去创建别的批次 */
  function askExit() {
    suppressRefocus = Date.now() + 4000;
    var s = S.session, n = (S.batches || []).length;
    openModal('退出批次', '<div style="text-align:center;padding:6px 0 2px">'
      + '<div style="font-size:15px">即将退出 <b style="color:#1f6feb">' + esc(s.po_id) + '</b> 的收货</div>'
      + '<div style="color:#6b7a8d;font-size:13px;margin-top:8px">'
      + '已扫的 ' + n + ' 个物流单号、全部明细都会保留。<br>'
      + '退出后你可以先去创建/处理别的采购单批次，回头在「挂起中的批次」里点一下就能接着收。</div>'
      + '</div>',
      [{ text: '继续收货', cls: 'btn-ghost', fn: function () { closeModal(); refocus(); } },
      { text: '退出批次', cls: 'btn-primary', fn: doExit }],
      function () { closeModal(); refocus(); });
  }

  function doExit() {
    closeModal();
    api('/api/session/pause', { method: 'POST', body: { sid: S.session.id } }).then(function (r) {
      if (!r.ok) return handleFail(r);
      toast(r.msg || '已退出批次', 'ok');
      beep(660, 0.08);
      reload();
    }).catch(function () { permAlert(); });
  }

  // 待确认扫码不是已保存明细；先明确确认，不能悄悄清掉或带着失败继续挂起。
  function pauseReportReady(sid, operation) {
    operation = operation || '暂存并对单';
    var focused = document.activeElement;
    if (focused && focused.matches && focused.matches('#line-body .qty-input')) focused.blur();
    if (!S.session || S.session.id !== sid) { toast('当前批次已变化，请重新选择', 'warn'); return false; }
    if (pending) { toast('还有待确认的扫码数量，请先确认数量，再' + operation, 'warn'); return false; }
    if (writesPending || poRefreshing || (window.ReceiptEditor && ReceiptEditor.isOpen())) {
      toast('正在保存或刷新，请完成后再' + operation, 'warn'); return false;
    }
    var dirty = $$('#line-body .qty-input').some(function (inp) {
      var line = (S.lines || []).filter(function (l) { return l.id === +inp.dataset.id; })[0];
      return line && num(inp.value) !== num(line.qty);
    });
    if (dirty || Object.keys(qtySaveErrors).some(function (id) { return qtySaveErrors[id] === sid; })) {
      toast('有数量尚未保存成功，请重新保存失败的数量，或刷新核对已保存明细', 'warn'); return false;
    }
    return true;
  }

  function askPauseReport() {
    var s = S.session;
    if (pauseReportBusy || !s || !pauseReportReady(s.id)) return;
    suppressRefocus = Date.now() + 4000;
    openModal('暂存并对单', '<p>将暂存采购单 <b>' + esc(s.po_id) + '</b> 本轮全部 '
      + (S.batches || []).length + ' 个物流单号及明细，并打开与结单相同的差异对照。</p>'
      + '<p><b>本轮不会结单</b>，稍后可从「挂起中的批次」查看差异或继续收货。</p>'
      + '<p class="hint">按本地已保存的采购资料对单，不刷新或修改 ERP，也不确认库存。</p>',
      [{ text: '取消', cls: 'btn-ghost', fn: closeModal },
       { text: '暂存并查看差异', cls: 'btn-primary', fn: function () { doPauseReport(s.id); } }], closeModal);
  }

  function doPauseReport(sid) {
    if (pauseReportBusy || !pauseReportReady(sid)) return;
    pauseReportBusy = true;
    $$('#modal-foot button').forEach(function (b) { b.disabled = true; });
    api('/api/session/pause-report', { method: 'POST', body: { sid: sid } }).then(function (r) {
      pauseReportBusy = false;
      $$('#modal-foot button').forEach(function (b) { b.disabled = false; });
      if (!r.ok) return handleFail(r);
      toast(r.msg || '明细已暂存，本轮未结单', 'ok');
      showDiff(r, false, true);
      reload();
    }).catch(function () {
      pauseReportBusy = false;
      $$('#modal-foot button').forEach(function (b) { b.disabled = false; });
      toast('暂存结果未确认，请重试或刷新查看挂起批次；明细不会被清空', 'warn');
    });
  }

  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('.js-paused-report') : null;
    if (!el || el.disabled) return;
    el.disabled = true;
    // 不带 refresh：离线也能继续对本地差异，不假称已刷新 ERP。
    api('/api/session/detail?sid=' + encodeURIComponent(el.dataset.id)).then(function (r) {
      el.disabled = false;
      if (!r.ok) return handleFail(r);
      showDiff(r, r.session.state === 'closed', r.session.state === 'paused');
    }).catch(function () { el.disabled = false; permAlert(); });
  });

  function resumeBatch(sid) {
    api('/api/session/resume', { method: 'POST', body: { sid: sid, refresh: true } }).then(function (r) {
      if (!r.ok) return handleFail(r);
      toast(r.msg || '已回到该批次', 'ok');
      beep(880, 0.08);
      reload();
    }).catch(function () { permAlert(); });
  }

  /* 点击挂起批次里的「继续收货」 */
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('.js-resume') : null;
    if (el) { resumeBatch(+el.dataset.id); }
  });

  /* -------------------- 模式切换 -------------------- */
  function setMode(m, silent) {
    mode = m;
    var inp = $('#scan-input');
    inp.classList.remove('focus-tracking', 'focus-item');
    if (m === 'tracking') { inp.placeholder = '请扫描物流单号，支持子母件 / 独立单号'; inp.classList.add('focus-tracking'); }
    else if (m === 'item') { inp.placeholder = '请扫描商品 69 码 / 辅助条码 / 商品编码'; inp.classList.add('focus-item'); }
    $$('.mode-btn').forEach(function (b) { b.classList.toggle('active', b.dataset.mode === m); });
    if (!silent) refocus();
  }
  $('#mode-tracking').onclick = function () { setMode('tracking'); };
  $('#mode-item').onclick = function () { setMode('item'); };

  document.addEventListener('keydown', function (e) {
    if (window.ProductPicker?.isOpen() || document.querySelector('.view.active')?.dataset.view !== 'home') return;
    if (e.key === 'F1') { e.preventDefault(); if (S.session) setMode('tracking'); }
    if (e.key === 'F2') { e.preventDefault(); if (S.session) setMode('item'); }
    if (e.key === 'Escape' && !$('#modal').hidden && modalEsc) modalEsc();
  });

  /* -------------------- 扫码主流程 -------------------- */
  // 扫码枪扫完会自带回车。若当前有「待确认数量」，且光标不在数量框（说明用户
  // 没去改数字），这一回车按默认 1 确认；光标在数量框时由数量框自己处理。
  $('#scan-input').addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    var v = this.value.trim();
    this.value = '';
    if (!v) return;
    if (pending && mode === 'item') {
      // 直接把这一笔按默认 1 入账，再处理刚扫到的新编码
      submitScan(v);
      return;
    }
    submitScan(v);
  });

  /* 待确认数量框：回车=确认，Esc=取消本笔 */
  (function () {
    var q = $('#pending-qty');
    if (!q) return;
    q.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); confirmPending(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelPending(); }
    });
    // 数量框失焦不自动确认（避免误触）。只有当用户没在操作别的控件时才把光标拉回来，
    // 否则点表格里的按钮会被不停抢焦点。
    q.addEventListener('blur', function () {
      if (!pending) return;
      if (Date.now() < suppressRefocus || (window.getSelection() && !window.getSelection().isCollapsed)) return;
      var a = document.activeElement;
      if (a && a !== document.body) return;
      setTimeout(function () { if (pending && document.activeElement === document.body) pendingFocus(); }, 0);
    });
  })();

  function confirmPending() {
    if (!pending) return;
    var p = pending, q = Math.max(0, num($('#pending-qty').value));
    api(p.choose ? '/api/item/choose' : '/api/item/scan', {
      method: 'POST',
      body: p.choose
        ? { item: p.choose, code: p.code, batch_id: p.batch_id, sid: S.session && S.session.id, qty: q }
        : { code: p.code, batch_id: p.batch_id, sid: S.session && S.session.id, qty: q,
            expected_sku_id: p.expected_sku_id, expected_matched: p.expected_matched }
    }).then(function (r) {
      clearPending();
      if (!r.ok) { handleFail(r); refocus(); return; }
      S.lines = r.lines; if (r.items) S.items = r.items; S.progress = r.progress;
      renderLines(); renderPoItems(); renderStatus();
      var sid = (r.item && r.item.sku_id) || (p.choose && p.choose.sku_id);
      if (sid) flashPoItem(sid);
      var nm = (r.item && r.item.name) ? r.item.name : p.label;
      if (r.unmatched) {
        flash((r.resolved ? '⚠ 采购单无此商品：' : '⚠ 未识别商品已记入差异：') + nm + '  →  ' + fmtNum(q), 'warn'); beep(200, 0.14);
      } else {
        flash(nm + '  →  ' + fmtNum(q), 'ok'); beep(1318, 0.06);
      }
      refocus();
    }).catch(function () { clearPending(); permAlert(); refocus(); });
  }

  function cancelPending() {
    // 取消本笔：不入账，回到扫码框
    var label = pending ? pending.label : '';
    clearPending();
    if (label) flash('已取消：' + label, 'warn');
    refocus();
  }

  $('#btn-pending-ok').onclick = function () { confirmPending(); };
  $('#btn-pending-cancel').onclick = function () { cancelPending(); };

  function submitScan(code) {
    if (!code) return;
    if (mode === 'tracking') return scanTracking(code);
    return scanItem(code);
  }

  function scanTracking(code) {
    // 扫到别的采购单号不再死拦：提示一下，由用户决定是切单还是继续。
    // （以前这里直接拒绝，导致一张单没收完、别的单完全进不来。）
    var a = S.session;
    if (a && (code === a.po_id || code === a.input_no || (a.outer_po_id && code === a.outer_po_id))) {
      // 扫到自己单号 → 当作确认，无事发生
      flash('仍在收货：' + a.po_id, 'warn');
      beep(660, 0.06);
      refocus();
      return;
    }
    api('/api/tracking/scan', { method: 'POST', body: { tracking_no: code, sid: a && a.id } }).then(function (r) {
      if (!r.ok) {
        if (r.msg === '当前账号无此操作权限') return handleFail(r);
        // 常见错误（如结单后误扫）用轻提示，别弹「无权限」吓人
        toast(r.msg || '操作失败', 'err');
        beep(180, 0.16);
        refocus();
        return;
      }
      S.current_batch = r.batch.id;
      S.lines = r.lines || [];
      S.batches = r.batches || [];
      renderPoItems(); renderLines(); renderStatus();
      setMode('item', true);
      flash((r.created ? '新建物流单号：' : '继续补扫：') + code, 'ok');
      toast(r.created ? '已新建物流单号 ' + code + '，之前的单号仍在上方列表'
        : '已切回 ' + code + '，继续补扫', 'ok');
      beep(1046, 0.07);
      refocus();
    }).catch(function () { permAlert(); });
  }

  var VIA_TAG = {
    aux: '（辅助码匹配）', sku_id: '（商品编码匹配）',
    i_id: '（款式编码匹配）', api: '（商品资料接口匹配）'
  };
  function viaTag(v) { return VIA_TAG[v] || ''; }

  function scanItem(code) {
    // 若扫到的其实是另一个已存在的物流单号，直接切过去，避免误判为未知商品
    var hit = (S.batches || []).filter(function (b) { return b.tracking_no === code; })[0];
    if (hit) {
      confirmPendingThen(function () {
        switchTracking(hit.id, code);
      });
      return;
    }
    // 先确认上一笔待确认的数量（默认 1），再解析这一笔
    confirmPendingThen(function () { resolveItem(code); });
  }

  /* 把上一笔待确认的按当前数量入账，成功后再执行 next */
  function confirmPendingThen(next) {
    if (!pending) { next(); return; }
    var p = pending, q = Math.max(0, num($('#pending-qty').value));
    pending = null;
    api(p.choose ? '/api/item/choose' : '/api/item/scan', {
      method: 'POST',
      body: p.choose
        ? { item: p.choose, code: p.code, batch_id: p.batch_id, sid: S.session && S.session.id, qty: q }
        : { code: p.code, batch_id: p.batch_id, sid: S.session && S.session.id, qty: q }
    }).then(function (r) {
      clearPending();
      if (r.ok && r.lines) {
        S.lines = r.lines;
        if (r.items) S.items = r.items;
        S.progress = r.progress;
        renderLines(); renderPoItems(); renderStatus();
        var sid = (r.item && r.item.sku_id) || (p.choose && p.choose.sku_id);
        if (sid) flashPoItem(sid);
        flash((r.item && r.item.name ? r.item.name : p.label) + '  →  ' + fmtNum(r.added || q), 'ok');
        beep(1318, 0.06);
      } else if (!r.ok) {
        handleFail(r);
      }
      next();
    }).catch(function () { clearPending(); permAlert(); next(); });
  }

  function clearPending() {
    pending = null;
    var bar = $('#pending-bar');
    if (bar) bar.hidden = true;
  }

  function showPending(res, code, label, chooseItem) {
    pending = { code: code, batch_id: res.batch_id || S.current_batch, label: label || code,
                choose: chooseItem || null, expected_sku_id: res.item && res.item.sku_id,
                expected_matched: !res.unmatched };
    $('#pb-name').textContent = label || code;
    $('#pb-code').textContent = code;
    var q = $('#pending-qty');
    q.value = '1';
    $('#pending-bar').hidden = false;
    pendingFocus();
  }

  function pendingFocus() {
    var q = $('#pending-qty');
    if (!q) return;
    try { q.focus(); q.select(); } catch (e) { }
  }

  function switchTracking(bid, code) {
    S.current_batch = bid;
    api('/api/state?batch_id=' + bid).then(function (st) {
      S = st; renderLines(); renderPoItems(); renderStatus();
      setMode('item', true);
      window.dispatchEvent(new CustomEvent('native-receipt',{detail:{kind:'tracking',tracking_no:code,key:'普通:'+S.session.id+':'+bid}}));
      flash('切换到物流单号：' + code + '，继续补扫', 'warn'); beep(660, 0.07); refocus();
    });
  }

  /* 解析一个商品编码：命中唯一商品则进入「待确认数量」；多候选则弹选择 */
  function resolveItem(code) {
    api('/api/item/scan', {
      method: 'POST',
      body: { code: code, batch_id: S.current_batch, sid: S.session && S.session.id, qty: 0 }
    }).then(function (r) {
      if (!r.ok) { handleFail(r); return; }
      if (r.multi) return showCandidates(r);
      var name = (r.item && r.item.name) ? r.item.name : code;
      showPending(r, code, name);
      flash((r.unmatched ? (r.resolved ? '⚠ 采购单无此商品：' : '⚠ 本地未识别，仍可收货并编辑资料：') : '')
        + name + (r.unmatched && !r.resolved ? '　确认后可编辑资料；资料不存在不能入库' : '　数量默认 1，回车确认'), r.unmatched ? 'warn' : 'ok');
      if (r.unmatched) beep(200, 0.14); else beep(880, 0.05);
    }).catch(function () { permAlert(); });
  }

  /* 多候选选择：一个码命中多个商品（如 A 的条码 = B 的辅助码、或编码互挂）时，
     用「大图 + 名称 + 编码 + 条码 + 命中方式」让人工分辨，杜绝收错货。
     图片来自商品资料接口（pic 字段），异步补位：先弹框，图到了再淡入。 */
  function showCandidates(r) {
    suppressRefocus = Date.now() + 3000;
    ProductPicker.open({rows:r.candidates, code:r.code,
      owner:S.session?.owner_co_id, auth:S.session?.auth_co_id,
      onCancel:refocus,
      onChoose:function(it) {
        showPending({batch_id:S.current_batch}, r.code, it.name, {
          poi_id:it.poi_id || '', sku_id:it.sku_id, i_id:it.i_id || '',
          sku_code:it.sku_code || '', name:it.name, spec:it.spec || '', outside_purchase:!!it.outside_purchase
        });
        flash('已选：' + it.name + '　数量默认 1，回车确认', 'ok');
        beep(880, .05);
      }
    });
  }

  $('#btn-skip-tracking').onclick = function () {
    var no = 'NOSHIP-' + new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
    scanTracking(no);
  };

  /* -------------------- 左栏：采购单明细（应收 / 已收） -------------------- */
  // 左栏是「这张采购单该收什么」，右栏是「当前这个物流单号实际收了多少」。
  // 同SKU只显示一次，按首次出现顺序；本地累计实收不可按重复采购行相加。
  function renderPoItems() {
    var round = ReceivingRoundUI.data(S), rows = round.rows || [];
    var tb = $('#poitem-body'), cnt = $('#item-count');
    if (cnt) cnt.textContent = rows.length;
    var refreshButton = $('#po-live-refresh');
    if (refreshButton) refreshButton.disabled = poRefreshing;
    renderPoRefreshStatus();
    var empty = $('#poitem-empty');
    if (empty) empty.hidden = rows.length > 0;
    if (tb) tb.innerHTML = ReceivingRoundUI.body(round);
    var note = $('#po-round-notice');
    if (note) note.innerHTML = ReceivingRoundUI.notes(round);
  }

  function renderPoRefreshStatus() {
    var el = $('#po-live-status'), s = S.session || {};
    if (!el) return;
    var note = poRefreshNotice && poRefreshNotice.sid === s.id ? poRefreshNotice : null;
    el.className = note && note.error ? 'hint inbound-warning' : 'hint';
    el.textContent = (poRefreshing ? '正在查询聚水潭；暂显示旧快照。' : '')
      + (note && note.error ? '刷新失败，当前显示旧快照：' + note.error + '。' : '')
      + '聚水潭最后成功获取：' + (s.po_refreshed_at || '尚未核实')
      + '；后台同步仅更新本地收货，重新查询请点刷新。';
  }

  $('#po-live-refresh').onclick = function () {
    if (!S.session || pending || window.ReceiptCopy?.isBusy() || writesPending || poRefreshing) { toast('请先完成当前输入再刷新', 'warn'); return; }
    var b = this, sid = S.session.id; b.disabled = true; poRefreshing = true; ++stateEpoch;
    poRefreshNotice = null; renderPoRefreshStatus();
    api('/api/session/detail?sid=' + sid + '&refresh=1').then(function (r) {
      if (!r.ok) throw Error(r.msg || '采购状态刷新失败');
      if (S.session && S.session.id === sid) {
        return reload(function () { toast('已重新获取聚水潭采购状态', 'ok'); }, S.current_batch);
      }
    }).catch(function (e) {
      poRefreshNotice = { sid: sid, error: e.message || '网络错误' };
      if (S.session && S.session.id === sid) toast('采购状态刷新失败，保留旧数据', 'err');
    }).then(function () { poRefreshing = false; b.disabled = false; renderPoRefreshStatus(); });
  };

  // 扫到的商品在左栏高亮一下，方便核对
  function flashPoItem(skuId) {
    if (!skuId) return;
    var rows = $$('#poitem-body tr');
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var hit = r.hasAttribute('data-idx') ? (S.items || [])[+r.dataset.idx] : null;
      if (hit && String(hit.sku_id) === String(skuId)) {
        r.classList.add('pi-hit');
        (function (el) {
          setTimeout(function () { el.classList.remove('pi-hit'); }, 1400);
          try { el.scrollIntoView({ block: 'nearest' }); } catch (e) { }
        })(r);
        return;
      }
    }
  }

  /* -------------------- 右栏①：物流单号折叠列表 --------------------
     一个收货批次里可能扫进多个物流单号：已扫完的折叠成一行（单号 + 种数/件数），
     当前正在收的展开高亮。点折叠行 → 切回该单号继续补扫；右侧 ✕ 删整单（二次确认）、
     「改」编辑单号。这样扫第二个单号时，上一个不会消失。 */
  function renderBatchList() {
    var bs = S.batches || [], cur = S.current_batch;
    var wrap = $('#batch-list'), empty = $('#batch-empty'), cnt = $('#batch-count');
    if (!wrap) return;
    if (cnt) cnt.textContent = bs.length;
    if (empty) empty.hidden = bs.length > 0;
    wrap.innerHTML = bs.map(function (b) {
      var active = b.id === cur;
      return '<div class="bl-item' + (active ? ' active' : '') + '" data-id="' + b.id + '">'
        + '<div class="bl-main js-bl-switch" data-id="' + b.id + '">'
        + '<span class="bl-caret">' + (active ? '▾' : '▸') + '</span>'
        + '<span class="bl-no">' + esc(b.tracking_no) + '</span>'
        + (b.inbound_job_id ? '<span class="chip">入库 ' + esc(b.inbound_io_id || '待核实') + ' · 已锁定' + (b.inbound_pending_qty > 0 ? ' · 另有 ' + fmtNum(b.inbound_pending_qty) + ' 件未提交' : '') + '</span>' : '')
        + (active ? '<span class="bl-tag">收货中</span>' : '')
        + '</div>'
        + '<div class="bl-meta">' + esc(b.first_scan_at) + '　' + (b.kinds || 0) + ' 种 / '
        + fmtNum(b.total || 0) + ' 件</div>'
        + '<div class="bl-acts">'
        + '<button class="btn btn-ghost btn-sm js-bl-switch" data-id="' + b.id + '"'
        + (active ? ' disabled' : '') + '>' + (active ? '当前' : '继续补扫') + '</button>'
        + '<button class="btn btn-ghost btn-sm js-bl-rename" data-id="' + b.id + '" data-no="'
        + esc(b.tracking_no) + '">改单号</button>'
        + '<button class="btn btn-ghost btn-sm js-bl-copy" data-id="' + b.id + '"' + (!active || b.inbound_job_id ? ' disabled' : '') + '>复制上一单商品</button>'
        + '<button class="btn btn-ghost btn-sm danger js-bl-del" data-id="' + b.id + '" data-no="'
        + esc(b.tracking_no) + '" data-kinds="' + (b.kinds || 0) + '">删除</button>'
        + '</div></div>';
    }).join('');
  }

  /* -------------------- 右栏②：当前物流单号明细 -------------------- */
  // 明细已由服务端排好：采购单里有的在上（按采购单顺序），采购单没有的沉底。
  // 这里再插一条分隔行，把两类视觉上隔开。
  function renderLines() {
    var lines = S.lines || [];
    var tb = $('#line-body');
    $('#line-empty').hidden = lines.length > 0;
    var cur = (S.batches || []).filter(function (b) { return b.id === S.current_batch; })[0];
    var ct = $('#cur-tracking');
    if (ct) ct.textContent = cur ? cur.tracking_no : '—';
    var cbn = $('#cur-batch-name');
    if (cbn) cbn.textContent = '物流单号：' + (cur ? cur.tracking_no : '—');

    var html = '', sep = false, dsep = false;
    lines.forEach(function (l) {
      // 已在别的物流单号收满的商品：灰色只读行，沉在明细表最底部
      if (l.done_elsewhere) {
        if (!dsep) {
          dsep = true;
          html += '<tr class="line-sep"><td colspan="5">'
            + '<span class="line-sep-txt">以下商品已在其他物流单号收满（无需再扫）</span></td></tr>';
        }
        html += '<tr class="tr-done-elsewhere" data-done="1">'
          + '<td>' + esc(l.name)
          + (l.spec ? '<div class="pi-spec">' + esc(l.spec) + '</div>' : '') + '</td>'
          + '<td class="sku">' + esc(l.sku_id) + '</td>'
          + '<td class="sku">' + esc(l.sku_code) + '</td>'
          + '<td class="de-qty">' + fmtNum(l.qty) + '</td>'
          + '<td style="text-align:right"><span class="de-tag">✓ 已收满'
          + (l.from_batch ? ' · ' + esc(l.from_batch) : '') + '</span></td>'
          + '</tr>';
        return;
      }
      var bad = !l.matched;
      if (bad && !sep) {
        sep = true;
        html += '<tr class="line-sep"><td colspan="5">'
          + '<span class="line-sep-txt">以下为采购单里没有的商品</span></td></tr>';
      }
      html += '<tr class="' + (bad ? 'tr-bad' : '') + '" data-id="' + l.id + '">'
        + '<td>' + esc(l.name)
        + (bad ? '<span class="tag-new">采购单无此商品</span>' : '')
        + (l.spec ? '<div class="pi-spec">' + esc(l.spec) + '</div>' : '') + '</td>'
        + '<td class="sku">' + esc(l.sku_id) + '</td>'
        + '<td class="sku">' + esc(l.sku_code) + '</td>'
        + '<td><div class="qty-ctl">'
        + '<button data-act="dec" data-id="' + l.id + '">−</button>'
        + '<input class="qty-input" data-id="' + l.id + '" value="' + fmtNum(l.qty) + '">'
        + '<button data-act="inc" data-id="' + l.id + '">＋</button>'
        + '</div></td>'
        + '<td style="text-align:right">' + window.ReceiptEditor.button(l) + '<button class="icon-btn" data-act="del" data-id="' + l.id + '">✕</button></td>'
        + '</tr>';
    });
    tb.innerHTML = html;
    if (cur && cur.inbound_job_id) {
      tb.querySelectorAll('input, button').forEach(function (el) { el.disabled = true; });
      if (cbn) cbn.textContent += '（已关联入库单，补收请新建物流子批次）';
    }
    renderBatchList();
    bindLineCtl();
  }

  /* 物流单号列表：切换 / 编辑 / 删除（事件委托） */
  document.addEventListener('click', function (e) {
    var el;
    if (!e.target || !e.target.closest) return;
    if ((el = e.target.closest('.js-bl-copy'))) {
      copyPreviousBatch(+el.dataset.id);return;
    }
    if ((el = e.target.closest('.js-bl-switch'))) {
      var id = +el.dataset.id;
      if (id === S.current_batch) return;
      confirmPendingThen(function () { switchTracking(id, trackingNoOf(id)); });
      return;
    }
    if ((el = e.target.closest('.js-bl-del'))) {
      askDelBatch(+el.dataset.id, el.dataset.no, +el.dataset.kinds || 0);
      return;
    }
    if ((el = e.target.closest('.js-bl-rename'))) {
      askRenameBatch(+el.dataset.id, el.dataset.no);
      return;
    }
  });

  function trackingNoOf(bid) {
    var b = (S.batches || []).filter(function (x) { return x.id === bid; })[0];
    return b ? b.tracking_no : '';
  }

  /* 整单删除物流单号：二次确认（删除动作不可逆，必须弹窗确认） */
  function askDelBatch(bid, no, kinds) {
    confirmBox('删除物流单', '即将删除物流单号 <b>' + esc(no) + '</b> 及其 ' + kinds
      + ' 条收货明细（' + kinds + ' 种商品），<br>对应数量会从采购单实收中扣减，删除后无法恢复。',
      '删除该物流单', function () {
        var isCur = (S.current_batch === bid);
        api('/api/batch/del', { method: 'POST', body: { batch_id: bid, sid: S.session && S.session.id } })
          .then(function (r) {
            if (!r.ok) return handleFail(r);
            toast(r.msg || '已删除该物流单', 'ok');
            refreshAfterMutation(isCur ? 0 : S.current_batch);
          }).catch(function () { permAlert(); });
      });
  }

  /* 编辑物流单号：该单号下明细不动，只改单号 */
  function askRenameBatch(bid, no) {
    suppressRefocus = Date.now() + 120000;
    openModal('修改物流单号',
      '<div style="padding:4px 0">'
      + '<div style="color:#6b7a8d;font-size:13px;margin-bottom:10px">原单号：<b class="mono">' + esc(no) + '</b>，'
      + '该单号下的收货明细保持不变，只改单号本身。</div>'
      + '<input id="rename-input" class="input input-xl" value="' + esc(no) + '" autocomplete="off" spellcheck="false">'
      + '</div>',
      [{ text: '取消', cls: 'btn-ghost', fn: function () { closeModal(); refocus(); } },
      { text: '保存', cls: 'btn-primary', fn: function () {
        var v = $('#rename-input').value.trim();
        if (!v) { toast('物流单号不能为空', 'err'); return; }
        if (v === no) { closeModal(); refocus(); return; }
        api('/api/batch/rename', { method: 'POST', body: { batch_id: bid, tracking_no: v } })
          .then(function (r) {
            closeModal();
            if (!r.ok) { handleFail(r); return; }
            toast(r.msg || '已修改', 'ok');
            refreshAfterMutation((S.current_batch && S.current_batch !== bid) ? S.current_batch : 0);
          }).catch(function () { closeModal(); permAlert(); });
      } }],
      function () { closeModal(); refocus(); });
    var inp = $('#rename-input');
    if (inp) { inp.focus(); inp.select(); inp.onkeydown = function (e) {
      if (e.key === 'Enter') { e.preventDefault(); $$('#modal-foot .btn-primary').forEach(function (b) { b.click(); }); }
    }; }
  }

  function bindLineCtl() {
    $$('#line-body button[data-act]').forEach(function (b) {
      b.onclick = function () {
        var id = +b.dataset.id;
        var line = (S.lines || []).filter(function (l) { return l.id === id; })[0];
        if (!line) return;
        if (b.dataset.act === 'del') { delLine(id); return; }
        var step = Math.max(0.01, num(S.settings && S.settings.scan_step) || 1);
        setQty(id, Math.max(0, num(line.qty) + (b.dataset.act === 'inc' ? step : -step)));
      };
    });
    $$('#line-body .qty-input').forEach(function (inp) {
      inp.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } };
      inp.onblur = function () {
        var id = +inp.dataset.id;
        var line = (S.lines || []).filter(function (l) { return l.id === id; })[0];
        if (line && num(inp.value) !== num(line.qty)) setQty(id, Math.max(0, num(inp.value)));
      };
    });
  }

  document.addEventListener('receipt-line-updated', function () {
    toast('收货资料已保存，数量未改变；入库前仍须核验商品资料', 'ok');
    refreshAfterMutation(S.current_batch);
  });

  function afterLineChange(r, silent) {
    if (!r.ok) { handleFail(r); return; }
    S.lines = r.lines; S.progress = r.progress;
    if (r.items) S.items = r.items;
    renderLines(); renderPoItems(); renderStatus();
    if (!silent) refocus();
  }
  function setQty(id, q) {
    suppressRefocus = Date.now() + 400;
    var sid = S.session && S.session.id;
    api('/api/line/set', { method: 'POST', body: { line_id: id, qty: q, batch_id: S.current_batch } })
      .then(function (r) {
        if (r.ok) delete qtySaveErrors[id]; else qtySaveErrors[id] = sid;
        afterLineChange(r);
      }).catch(function () { qtySaveErrors[id] = sid; toast('数量保存结果未确认，请重试或刷新核对', 'warn'); });
  }
  function delLine(id) {
    suppressRefocus = Date.now() + 400;
    api('/api/line/del', { method: 'POST', body: { line_id: id, batch_id: S.current_batch } })
      .then(function (r) { if (r.ok) delete qtySaveErrors[id]; afterLineChange(r); });
  }

  /* -------------------- 结单 -------------------- */
  function askFinish() {
    if (finishBusy || !S.session || !pauseReportReady(S.session.id, '结单')) return;
    suppressRefocus = Date.now() + 4000;
    var s = S.session, n = (S.batches || []).length;
    openModal('结单', '<div style="text-align:center;padding:6px 0 2px">'
      + '<div style="font-size:15px">即将对 <b style="color:#1f6feb">' + esc(s.po_id) + '</b> 结单</div>'
      + '<div style="color:#6b7a8d;font-size:13px;margin-top:8px">'
      + '系统会把该采购单下 <b>' + n + '</b> 个物流单号的实收合并，<br>与本轮应收做一次完整比对后给出差异报告。<br>'
      + '结单后本轮不可再扫码；如需继续收货，请先核对旧轮入库状态，再创建新一轮。</div>'
      + '</div>',
      [{ text: '取消', cls: 'btn-ghost', fn: function () { closeModal(); refocus(); } },
      { text: '结单并校验差异', cls: 'btn-danger', fn: function () { doFinish(s.id); } }],
      function () { closeModal(); refocus(); });
  }

  function doFinish(sid) {
    if (finishBusy || !pauseReportReady(sid, '结单')) return;
    finishBusy = true;
    $$('#modal-foot button').forEach(function (b) { b.disabled = true; });
    api('/api/session/close', { method: 'POST', body: { sid: sid } }).then(function (r) {
      finishBusy = false;
      $$('#modal-foot button').forEach(function (b) { b.disabled = false; });
      if (!r.ok) return handleFail(r);
      showDiff(r, true);
      reload();
    }).catch(function () {
      finishBusy = false;
      $$('#modal-foot button').forEach(function (b) { b.disabled = false; });
      toast('结单结果未确认，请刷新记录核实；已保存明细不会清空，不要重复入库', 'warn');
    });
  }

  function refreshDiffDialog() {
    var context = diffContext;
    if (!context || $('#modal').hidden) return;
    api('/api/session/detail?sid=' + encodeURIComponent(context.sid)).then(function (r) {
      if (!r.ok || diffContext !== context || $('#modal').hidden) return;
      var filter = $('#modal .diff-only');
      showDiff(r, r.session.state === 'closed', r.session.state === 'paused', filter ? filter.checked : true);
    }).catch(function () { toast('入库记录刷新失败，请重新打开此轮对单核实单号', 'warn'); });
  }

  function showDiff(r, finished, paused, onlyDiff) {
    suppressRefocus = Date.now() + 4000;
    var round = ReceivingRoundUI.data(r);
    var head = '<p>采购单 <b>' + esc(r.session.po_id) + '</b> · 收货轮次 #' + esc(r.session.id)
      + (r.session.outer_po_id ? ' · 外部单号 ' + esc(r.session.outer_po_id) : '') + '</p>'
      + (paused ? '<p class="hint">明细已暂存，本轮未结单。此报告按本地已保存的采购资料对照，未实时刷新 ERP，不代表已确认入库。可关闭后稍后对单，或继续收货（同一轮次）。</p>' : '')
      + DifferenceReport.render(r, {onlyDiff:onlyDiff !== false})
      + (paused ? '' : '<p class="hint">仅结算本轮本地收货，不代表已在聚水潭确认入库。新一轮请先核对旧轮入库状态。</p>');
    if (round.legacy) head += '<details><summary>旧版采购总量对照（历史口径，非本轮差额）</summary>' + diffTable(r.diff || []) + '</details>';
    var btns = [{ text: '知道了', cls: 'btn-primary', fn: function () { closeModal(); refocus(); } }];
    if (finished || paused) {
      // Entry is bound to this report's round, never S.session or the filtered rows.
      btns.unshift({ text: '导出该单明细', cls: '', fn: function () { exportNow('po', r.session.po_id); } },
        { text: '生成入库单', cls: 'btn-primary report-inbound', fn: function () { window.openInbound(r.session.id); } });
    }
    if (paused) {
      btns[btns.length - 1].text = '关闭，稍后对单';
      btns.unshift({ text: '继续收货', cls: 'btn-ghost', fn: function () { closeModal(); resumeBatch(r.session.id); } });
    }
    openModal(paused ? '明细已暂存 · 继续对单（未结单）' : (finished ? '收货已完成 · 差异校验结果' : '差异校验结果'), head, btns, function () { closeModal(); refocus(); });
    diffContext = { sid: r.session.id };
  }

  /* -------------------- 查询（模糊匹配 / 可修改 / 可删除） -------------------- */
  var queryOpen={po:false,tk:false},querySeq={po:0,tk:0};
  function closeQuery(kind){queryOpen[kind]=false;querySeq[kind]++;$('#q-'+kind+'-out').innerHTML='';$('#btn-close-'+kind).hidden=true;}
  ['po','tk'].forEach(function(k){$('#btn-close-'+k).onclick=function(){if(document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved'))return toast('数量正在保存或保存失败，请保存成功后再关闭','warn');closeQuery(k);};});
  var lastQ = null;   // 记住最后一次查询，便于改动后自动刷新

  function refreshAfterMutation(keepBid) {
    if(document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved'))return;
    if (queryOpen.po) runPoQuery();
    if (queryOpen.tk) runTkQuery();
    loadRecents();
    refreshOpenSessionDetail();
    // 统一全量刷新 /api/state：挂起列表、收货区、状态条一起更新。
    // 之前只刷查询结果，导致「退出批次后删除批次」提示成功但列表里还留着旧条目。
    // keepBid 未指定 → 保留当前正在收的物流单号；传 0 → 让服务端重新挑（删掉的就是当前那张）。
    var keep = (keepBid === null || keepBid === undefined)
      ? ((!$('#work-area').hidden && S.current_batch) ? S.current_batch : 0)
      : keepBid;
    reload(null, keep);
  }

  // 历史记录的明细表：数量可直接改、整行可删（收货中与已结束的记录通用）
  function linesTable(lines) {
    var rows = lines.length ? lines.map(function (l) {
      return '<tr class="' + (l.matched ? '' : 'tr-bad') + '">'
        + '<td class="sku">' + esc(l.sku_id) + '</td>'
        + '<td>' + esc(l.name) + (l.matched ? '' : ' <span class="tag-new">采购单无此商品</span>')
        + (l.spec ? '<div style="color:#6b7a8d;font-size:12px">' + esc(l.spec) + '</div>' : '') + '</td>'
        + '<td class="sku">' + esc(l.sku_code) + '</td>'
        + '<td style="width:104px"><input class="qty-input js-rec-qty" data-id="' + l.id + '" value="'
        + fmtNum(l.qty) + '"' + (l.inbound_job_id ? ' disabled' : '') + '></td>'
        + '<td style="text-align:right">' + window.ReceiptEditor.button(l) + '<button class="icon-btn js-del-line" data-id="' + l.id + '">✕</button></td>'
        + '</tr>';
    }).join('') : '<tr><td colspan="5"><div class="empty">无明细</div></td></tr>';
    return '<div class="table-wrap"><table class="tbl"><thead><tr>'
      + '<th>商品编码</th><th>商品名称</th><th>条码</th><th>数量</th><th></th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  function viewBatch(bid) {
    suppressRefocus = Date.now() + 8000;
    api('/api/batch/lines?batch_id=' + bid).then(function (r) {
      if (!r.ok) return handleFail(r);
      openModal('子批次明细', linesTable(r.lines),
        [{ text: '关闭', cls: 'btn-primary', fn: function () { if(closeModal()===false)return; refocus(); refreshAfterMutation(); } }],
        function () { if(closeModal()===false)return; refocus(); refreshAfterMutation(); });
    }).catch(function () { permAlert(); });
  }

  // 采购单查询结果里的单个「物流单号」分组小表
  function groupTable(g) {
    var rows = (g.lines || []).length ? g.lines.map(function (l) {
      return '<tr class="' + (l.matched ? '' : 'tr-bad') + '">'
        + '<td class="sku">' + esc(l.sku_id) + '</td>'
        + '<td>' + esc(l.name) + (l.matched ? '' : ' <span class="tag-new">采购单无此商品</span>')
        + (l.spec ? '<div style="color:#6b7a8d;font-size:12px">' + esc(l.spec) + '</div>' : '') + '</td>'
        + '<td style="width:104px"><input class="qty-input js-rec-qty" data-id="' + l.id + '" value="'
        + fmtNum(l.qty) + '"' + (g.inbound_job_id ? ' disabled' : '') + '></td>'
        + '<td style="text-align:right">' + window.ReceiptEditor.button(l) + '<button class="icon-btn js-del-line" data-id="' + l.id + '">✕</button></td>'
        + '</tr>';
    }).join('') : '<tr><td colspan="4"><div class="empty">该物流单号暂无明细</div></td></tr>';
    return '<div class="grp">'
      + '<div class="grp-hd"><span class="chip">' + esc(g.tracking_no) + '</span>'
      + (g.inbound_job_id ? '<span class="chip">入库 ' + esc(g.inbound_io_id || '待核实') + ' · 已锁定' + (g.inbound_pending_qty > 0 ? ' · 另有 ' + fmtNum(g.inbound_pending_qty) + ' 件未提交' : '') + '</span>' : '')
      + '<span class="grp-meta">' + esc(g.first_scan_at) + '　' + (g.kinds || 0) + ' 种 / '
      + fmtNum(g.total) + ' 件</span>'
      + '<button class="icon-btn js-edit-batch" data-id="' + g.id + '" data-no="' + esc(g.tracking_no)
      + '" title="修改物流单号">✎</button>'
      + '<button class="icon-btn js-del-batch" data-id="' + g.id + '" title="删除该物流单号">✕</button>'
      + '</div>'
      + '<table class="tbl"><thead><tr><th>商品编码</th><th>商品名称</th><th>数量</th><th></th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table></div>';
  }

  // 汇总差异表：实收合计 vs 采购单应收
  function diffTable(rows) {
    if (!rows.length) {
      return '<div style="padding:14px 2px;color:#17864a;font-weight:600">✓ 实收与采购单完全一致，无差异</div>';
    }
    return '<div class="table-wrap"><table class="tbl"><thead><tr>'
      + '<th>商品编码</th><th>商品名称</th><th>应收</th><th>实收</th><th>差异</th><th>说明</th>'
      + '</tr></thead><tbody>'
      + rows.map(function (d) {
        return '<tr class="tr-bad"><td class="sku">' + esc(d.sku_id) + '</td><td>' + esc(d.name) + '</td>'
          + '<td>' + fmtNum(d.plan) + '</td><td>' + fmtNum(d.recv) + '</td>'
          + '<td class="bad-rec">' + (d.diff > 0 ? '+' : '') + fmtNum(d.diff) + '</td>'
          + '<td class="bad-rec">' + esc(d.reason) + '</td></tr>';
      }).join('')
      + '</tbody></table></div>';
  }

  // 采购单维度实收汇总：每个商品应收 / 实收（全单合计）/ 差，一致的也列出，作为完整对照
  function poSummaryTable(rows) {
    if (!rows || !rows.length) {
      return '<div class="empty">该采购单没有商品明细</div>';
    }
    return '<div class="table-wrap"><table class="tbl"><thead><tr>'
      + '<th>商品编码</th><th>商品名称</th><th>应收</th><th>实收</th><th>差异</th>'
      + '</tr></thead><tbody>'
      + rows.map(function (d) {
        var cls = d.extra ? 'tr-bad' : (d.done ? 'tr-ok' : (d.over ? 'tr-bad' : ''));
        var diffTxt = d.diff === 0 ? '0'
          : ((d.diff > 0 ? '+' : '') + fmtNum(d.diff));
        var diffCls = d.diff === 0 ? '' : ' class="bad-rec"';
        return '<tr class="' + cls + '">'
          + '<td class="sku">' + esc(d.sku_id || d.sku_code) + '</td>'
          + '<td>' + esc(d.name || '')
          + (d.extra ? ' <span class="tag-new">采购单无此商品</span>' : '')
          + (d.spec ? '<div style="color:#6b7a8d;font-size:12px">' + esc(d.spec) + '</div>' : '')
          + '</td>'
          + '<td>' + fmtNum(d.plan) + '</td>'
          + '<td>' + fmtNum(d.recv) + '</td>'
          + '<td' + diffCls + '>' + diffTxt + '</td>'
          + '</tr>';
      }).join('')
      + '</tbody></table></div>';
  }

  function purchaseTotalTable(rows) {
    return '<div class="table-wrap purchase-total-table"><table class="tbl"><thead><tr><th>商品编码</th><th>商品名称</th><th>采购总量</th><th>实收</th><th>差异</th></tr></thead><tbody>'
      + ((rows || []).length ? rows.map(function (d) {
        var status = d.extra ? '采购单外' : (d.plan == null ? '待核实' : (d.done ? '收齐' : (d.diff > 0 ? '多收' : '少收')));
        return '<tr data-sku="' + esc(d.sku_id) + '" class="' + (d.extra || d.plan == null ? 'total-unknown' : (d.done ? 'total-done' : 'total-diff')) + '">'
          + '<td class="sku">' + esc(d.sku_id || d.sku_code || '待核实') + '</td><td>' + esc(d.name || '')
          + (d.extra ? ' <span class="tag-new">采购单无此商品</span>' : '')
          + (d.spec ? '<div class="hint">' + esc(d.spec) + '</div>' : '') + '</td>'
          + '<td class="total-plan">' + ReceivingRoundUI.num(d.plan) + '</td><td class="total-recv">' + ReceivingRoundUI.num(d.recv) + '</td>'
          + '<td class="total-delta"><span class="total-delta-value">' + (d.diff == null ? '—' : (d.diff > 0 ? '+' : '') + ReceivingRoundUI.num(d.diff))
          + '</span><span class="total-status">' + esc(status) + '</span></td></tr>';
      }).join('') : '<tr><td colspan="5" class="empty">该采购单没有商品明细</td></tr>') + '</tbody></table></div>';
  }
  function recordReport(r, only) {
    var stamp = (r.session || {}).po_refreshed_at || '尚未核实';
    return DifferenceReport.render(r, {onlyDiff:only, fullTable:
      '<p class="hint live-total-note">采购总量对照 · 最近成功获取采购明细：' + esc(stamp)
      + '。采购明细随刷新更新；实收仅合计上方/下方本条记录的各物流明细，不混入其他收货轮次。</p>'
      + purchaseTotalTable(r.live_purchase_total || [])});
  }
  function initialPurchaseTable(r) {
    var snap = r.initial_purchase || {};
    return '<details class="initial-purchase"><summary>初始采购单快照</summary><p class="hint">'
      + esc(snap.notice || '尚无初始快照') + (snap.captured_at ? ' 保存时间：' + esc(snap.captured_at) + '。' : '')
      + '采购编码、名称、数量固定；实收随本条记录的各物流明细更新。实收单外商品单独列出，不改变原采购快照。</p>'
      + (snap.available ? purchaseTotalTable(snap.rows) : '<div class="empty">初始采购明细缺失，无法还原首次总量</div>') + '</details>';
  }

  function purchaseBalanceTable(rows, session) {
    return '<details class="erp-ledger"><summary>ERP余额与入库关联明细（独立账目）</summary><p class="hint">获取时间：'
      + esc((session || {}).po_refreshed_at || '尚未核实')
      + '。剩余应收 = 采购总量 − 聚水潭已入库；本地累计实收不重复扣减，已关联草稿不代表已确认入库。</p>'
      + '<table class="tbl"><thead><tr><th>商品编码</th><th>采购总量</th><th>聚水潭已入库</th><th>剩余应收</th><th>本地待生成</th><th>本地已关联</th></tr></thead><tbody>'
      + (rows || []).map(function (r) { return '<tr><td>' + esc(r.sku_id) + '</td>'
        + ['ordered', 'erp_received', 'remaining', 'local_pending', 'local_linked'].map(function (key) {
          return '<td>' + (r[key] == null ? '待核实' : fmtNum(r[key])) + '</td>';
        }).join('') + '</tr>'; }).join('') + '</tbody></table></details>';
  }

  function extraHistoryHTML(rows){return (rows||[]).map(function(r){return '<div class="q-block"><strong>'+esc(r.label)+'</strong><p>'+esc(r.description)+'</p><a class="btn btn-primary" href="/'+(r.source==='joint'?'joint':'unplanned')+'.html?sid='+Number(r.id)+'">查看明细 / 继续收货'+(r.source==='joint'?' / 汇总对单':'')+'</a></div>';}).join('');}
  function runPoQuery() {
    if(document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved'))return toast('请先保存当前数量，失败时可按Enter重试','warn');
    lastQ = 'po';
    var no = $('#q-po').value.trim();
    if (!no) return toast('请输入采购单号等关键字', 'warn');
    queryOpen.po=true;var request=++querySeq.po;$('#btn-close-po').hidden=false;
    Promise.all([api('/api/query/po?no=' + encodeURIComponent(no) + '&refresh=1'),api('/api/receipts/extra-history?q='+encodeURIComponent(no))]).then(function (results) {var r=results[0],extra=extraHistoryHTML(results[1].rows);
      if(!queryOpen.po||request!==querySeq.po||document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved'))return;
      if(!results[1].ok)extra='<p class="error">其他收货来源查询失败：'+esc(results[1].msg)+'</p>';
      if (!r.ok){$('#q-po-out').innerHTML=extra+'<p class="error">普通收货查询失败：'+esc(r.msg||'请检查登录与权限后重试')+'</p>';return;}
      var box = $('#q-po-out');
      if (!r.found) { box.innerHTML = extra || '<div class="empty">' + esc(r.hint || '未找到记录') + '</div>'; return; }
      // 点进去就展开：上面是各物流单号明细，下面是汇总差异，不需要再点「查看明细」
      box.innerHTML = r.records.map(function (rec) {
        var s = rec.session, prog = rec.progress || { plan: 0, recv: 0 };
        var stTag = s.state === 'active' ? '收货中' : (s.state === 'paused' ? '已挂起' : '已结束');
        var stCls = s.state === 'active' ? '' : (s.state === 'paused' ? '' : ' bad');
        var h = '<div class="q-block"><div class="q-title">采购单 ' + esc(s.po_id)
          + '<span class="chip' + stCls + '">' + stTag + '</span></div>'
          + '<div class="q-kv"><span>外部单号：' + esc(s.outer_po_id || '—') + '</span>'
          + '<span>供应商：' + esc(s.supplier || '—') + '</span>'
          + '<span>收货开始：' + esc(s.started_at) + '</span>'
          + '<span>结束：' + esc(s.ended_at || (s.state === 'paused' ? '挂起于 ' + (s.paused_at || '—') : '未结束')) + '</span></div>';

        h += purchaseBalanceTable(rec.purchase_balance, s);
        h += '<div class="q-sub">① 该轮收货核对 · #' + esc(s.id) + '</div>';
        h += recordReport(rec, false);
        h += '<div class="q-sub">② 各物流单号明细（' + rec.groups.length + ' 个单号，数量可直接修改）</div>';
        h += rec.groups.length ? rec.groups.map(groupTable).join('') : '<div class="empty">尚未录入任何物流单号</div>';
        h += initialPurchaseTable(rec);

        h += '<div class="q-actions">' + inboundButton(s.id)
          + '<button class="btn btn-ghost btn-sm js-export-po" data-po="' + esc(s.po_id) + '">导出该单明细</button>';
        if (s.state === 'paused') {
          h += '<button class="btn btn-primary btn-sm js-resume" data-id="' + s.id + '">继续收货</button>';
        }
        h += '<button class="btn btn-ghost btn-sm danger js-del-session" data-id="' + s.id + '">删除整单收货记录</button>'
          + '</div></div>';
        return h;
      }).join('')+extra;
    }).catch(function () { if(queryOpen.po&&request===querySeq.po)permAlert(); });
  }
  $('#btn-q-po').onclick = runPoQuery;
  $('#q-po').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); runPoQuery(); }
  });

  function runTkQuery() {
    if(document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved'))return toast('请先保存当前数量，失败时可按Enter重试','warn');
    lastQ = 'tk';
    var no = $('#q-tk').value.trim();
    if (!no) return toast('请输入物流单号关键字', 'warn');
    queryOpen.tk=true;var request=++querySeq.tk;$('#btn-close-tk').hidden=false;
    Promise.all([api('/api/query/tracking?no=' + encodeURIComponent(no)),api('/api/receipts/extra-history?q='+encodeURIComponent(no))]).then(function (results) {var r=results[0],extra=extraHistoryHTML(results[1].rows);
      if(!queryOpen.tk||request!==querySeq.tk||document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved'))return;
      if(!results[1].ok)extra='<p class="error">其他收货来源查询失败：'+esc(results[1].msg)+'</p>';
      if (!r.ok){$('#q-tk-out').innerHTML=extra+'<p class="error">普通收货查询失败：'+esc(r.msg||'请检查登录与权限后重试')+'</p>';return;}
      var box = $('#q-tk-out');
      if (!r.found) { box.innerHTML = extra || '<div class="empty">未找到匹配的物流单号收货记录</div>'; return; }
      box.innerHTML = r.batches.map(function (b) {
        return '<div class="q-block"><div class="q-title">物流单号 ' + esc(b.tracking_no)
          + '<span class="chip">采购单 ' + esc(b.po_id) + '</span></div>'
          + '<div class="q-kv"><span>首次扫描：' + esc(b.first_scan_at) + '</span>'
          + '<span>供应商：' + esc(b.supplier || '—') + '</span>'
          + '<span>所属采购单状态：' + (b.state === 'active' ? '收货中' : '已结束') + '</span></div>'
          + '<div class="q-sub">该批次商品明细（数量可直接修改）</div>'
          + linesTable(b.lines)
          + '<div class="q-actions">'
          + '<button class="btn btn-ghost btn-sm danger js-del-batch" data-id="' + b.id + '">删除该子批次</button>'
          + '</div></div>';
      }).join('')+extra;
    }).catch(function () { if(queryOpen.tk&&request===querySeq.tk)permAlert(); });
  }
  $('#btn-q-tk').onclick = runTkQuery;
  $('#q-tk').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); runTkQuery(); }
  });

  /* ---------- 历史记录的删除 / 改量（事件委托，动态内容通用） ---------- */
  document.addEventListener('click', function (e) {
    var el, id;
    if (!e.target || !e.target.closest) return;
    if ((el = e.target.closest('[data-tab]'))) { switchTab(el.dataset.tab); return; }
    if ((el = e.target.closest('.js-del-line'))) {
      id = +el.dataset.id;
      confirmBox('删除商品明细', '该商品将从本批次中移除，<br>删除后无法恢复。', '删除该行', function () {
        api('/api/line/del', { method: 'POST', body: { line_id: id } }).then(function (r) {
          if (!r.ok) return handleFail(r);
          toast('已删除该明细行', 'ok'); refreshAfterMutation();
        });
      });
      return;
    }
    if ((el = e.target.closest('.js-edit-batch'))) {
      askRenameBatch(+el.dataset.id, el.dataset.no || '');
      return;
    }
    if ((el = e.target.closest('.js-del-batch'))) {
      id = +el.dataset.id;
      confirmBox('删除物流单', '将删除该物流单号及其全部收货明细，<br>对应数量会从采购单实收中扣减。', '删除物流单', function () {
        api('/api/batch/del', { method: 'POST', body: { batch_id: id } }).then(function (r) {
          if (!r.ok) return handleFail(r);
          toast(r.msg || '已删除该物流单', 'ok');
          refreshAfterMutation(id === S.current_batch ? 0 : S.current_batch);
        });
      });
      return;
    }
    if ((el = e.target.closest('.js-del-session'))) {
      id = +el.dataset.id;
      confirmBox('删除采购单记录', '将删除该采购单的全部收货记录（含所有子批次与明细），<br>此操作无法恢复。', '删除整单', function () {
        api('/api/session/del', { method: 'POST', body: { sid: id } }).then(function (r) {
          if (!r.ok) return handleFail(r);
          toast(r.msg || '已删除', 'ok');
          refreshAfterMutation((S.session && S.session.id === id) ? 0 : S.current_batch);
        });
      });
      return;
    }
    if ((el = e.target.closest('.js-view-batch'))) { viewBatch(+el.dataset.id); return; }
    if ((el = e.target.closest('.js-export-po'))) { exportNow('po', el.dataset.po); }
  });

  document.addEventListener('change', function (e) {
    var inp = e.target && e.target.closest ? e.target.closest('.js-rec-qty') : null;
    if (!inp) return;
    if(inp.classList.contains('qty-saving'))return;
    var q=Number(inp.value);
    if(inp.value.trim()===''||!Number.isFinite(q)||q<0){inp.classList.add('qty-save-error');return toast('请输入有效非负数量','warn');}
    inp.classList.remove('qty-save-error','qty-unsaved');inp.classList.add('qty-saving');inp.disabled=true;
    api('/api/line/set', {method:'POST',body:{line_id:+inp.dataset.id,qty:q}}).then(function(r){
      if(!r.ok){inp.classList.add('qty-save-error');toast(r.msg||'保存失败，按Enter重试','warn');return;}
      inp.classList.remove('qty-saving');toast('数量已更新','ok');
      if(!document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved'))refreshAfterMutation();
    }).catch(function(){inp.classList.add('qty-save-error');toast('数量保存失败，输入已保留；请重试','warn');}).finally(function(){inp.classList.remove('qty-saving');inp.disabled=false;});
  });

  document.addEventListener('input',function(ev){if(ev.target.matches('.js-rec-qty'))ev.target.classList.add('qty-unsaved');});
  document.addEventListener('keydown',function(ev){if(ev.key==='Enter'&&ev.target.matches('.js-rec-qty.qty-save-error,.js-rec-qty.qty-unsaved')){ev.preventDefault();ev.target.dispatchEvent(new Event('change',{bubbles:true}));}});
  function loadRecents() {
    api('/api/recents').then(function (r) {
      if (!r.ok) return;
      $('#recent-out').innerHTML = r.rows.length
        ? r.rows.map(function (s) {
          var stTag = s.state === 'active' ? '收货中' : (s.state === 'paused' ? '已挂起' : '已结束');
          var stCls = s.state === 'closed' ? ' bad' : '';
          var pr = s.round_progress || { plan: null, recv: (s.progress || {}).recv || 0 };
          return '<div class="q-block"><div class="q-title">采购单 ' + esc(s.po_id)
            + '<span class="chip' + stCls + '">' + stTag + '</span></div>'
            + '<div class="q-kv"><span>外部单号：' + esc(s.outer_po_id || '—') + '</span>'
            + '<span>供应商：' + esc(s.supplier || '—') + '</span>'
            + '<span>' + esc(s.started_at) + ' ~ ' + esc(s.ended_at || (s.state === 'paused' ? '挂起' : '进行中')) + '</span>'
            + '<span>' + (s.batches || 0) + ' 个物流单号 · 本轮已收 ' + fmtNum(pr.recv) + ' / ' + ReceivingRoundUI.num(pr.plan) + '</span></div>'
            + '<div class="q-actions">'
            + inboundButton(s.id)
            + '<button class="btn btn-ghost btn-sm" data-watch-source="receipt" data-watch-sid="'+s.id+'">标记待改单</button>'
            + '<button class="btn btn-ghost btn-sm js-open-session" data-id="' + s.id + '">查看明细与差异</button>'
            + (s.state === 'paused' ? '<button class="btn btn-primary btn-sm js-resume" data-id="' + s.id + '">继续收货</button>' : '')
            + '<button class="btn btn-ghost btn-sm danger js-del-session" data-id="' + s.id + '">删除记录</button>'
            + '</div></div>';
        }).join('')
        : '<div class="empty">暂无记录</div>';
    });
  }

  /* 从最近记录点进一张单：弹窗里同样两段式（分物流单号明细 + 汇总差异） */
  function openSessionDetail(sid) {
    if(document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved'))return toast('请先保存数量','warn');
    suppressRefocus = Date.now() + 8000;
    api('/api/session/detail?sid=' + sid + '&refresh=1').then(function (r) {
      if (!r.ok) return handleFail(r);
      var s = r.session, prog = r.progress || { plan: 0, recv: 0 };
      var stTag = s.state === 'active' ? '收货中' : (s.state === 'paused' ? '已挂起' : '已结束');
      var h = '<div class="q-kv" style="margin-bottom:8px">'
        + '<span>采购单 <b>' + esc(s.po_id) + '</b> · ' + stTag + '</span>'
        + '<span>外部单号：' + esc(s.outer_po_id || '—') + '</span>'
        + '<span>供应商：' + esc(s.supplier || '—') + '</span>'
        + '<span>' + esc(s.started_at) + ' ~ ' + esc(s.ended_at || '进行中') + '</span></div>'
        + purchaseBalanceTable(r.purchase_balance, s)
        + '<div class="q-sub">① 该轮收货核对 · #' + esc(s.id) + '</div>'
        + '<div data-record-report>' + recordReport(r, true) + '</div>'
        + '<div class="q-sub">② 各物流单号明细（' + r.groups.length + ' 个，数量可直接修改）</div>'
        + '<div data-record-groups>' + (r.groups.length ? r.groups.map(groupTable).join('') : '<div class="empty">尚未录入任何物流单号</div>') + '</div>'
        + '<div data-initial-snapshot>' + initialPurchaseTable(r) + '</div>'
        + '<div class="q-actions">' + inboundButton(s.id) + '</div>';
      openModal('采购单 ' + s.po_id + ' · 收货明细与差异', '<section class="record-detail" data-sid="' + sid + '">'
        + '<button class="btn btn-ghost btn-sm js-record-refresh" data-id="' + sid + '">刷新实时采购明细</button>'
        + '<p class="hint record-refresh-status" role="status"></p>' + h + '</section>',
        [{ text: '关闭', cls: 'btn-primary', fn: function () { if(closeModal()===false)return; refocus(); refreshAfterMutation(); } }],
        function () { if(closeModal()===false)return; refocus(); refreshAfterMutation(); });
    }).catch(function () { permAlert(); });
  }
  var recordRefreshEpoch = 0;
  function refreshOpenSessionDetail(live) {
    var box = $('#modal:not([hidden]) .record-detail');
    if (!box || document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved')) return;
    var epoch = ++recordRefreshEpoch, sid = box.dataset.sid;
    var status = box.querySelector('.record-refresh-status');
    if (live) status.textContent = '正在获取实时采购明细…';
    api('/api/session/detail?sid=' + sid + (live ? '&refresh=1' : '')).then(function (r) {
      if (epoch !== recordRefreshEpoch || !box.isConnected || $('#modal').hidden || document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved')) return;
      if (!r.ok) { status.textContent = '刷新失败：' + (r.msg || '请重试') + '；下方仍为上次成功获取的数据。'; return; }
      var only = box.querySelector('.diff-only').checked;
      var expanded = box.querySelector('.initial-purchase').open;
      box.querySelector('[data-record-report]').innerHTML = recordReport(r, only);
      box.querySelector('[data-initial-snapshot]').innerHTML = initialPurchaseTable(r);
      box.querySelector('.initial-purchase').open = expanded;
      // Do not discard a quantity the user is currently typing in another row.
      if (!box.querySelector('[data-record-groups]').contains(document.activeElement)) {
        box.querySelector('[data-record-groups]').innerHTML = r.groups.length ? r.groups.map(groupTable).join('') : '<div class="empty">尚未录入任何物流单号</div>';
      }
      status.textContent = live ? '实时采购明细已刷新；初始快照保持不变。' : '实收已按当前物流明细更新；采购数据仍使用上次成功获取的明细，可点击刷新。';
    }).catch(function () {
      if (epoch === recordRefreshEpoch && box.isConnected) status.textContent = '刷新失败，请重试；下方仍为上次成功获取的数据。';
    });
  }
  document.addEventListener('click', function (e) {
    if (e.target.closest('.js-record-refresh')) refreshOpenSessionDetail(true);
  });
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('.js-open-session') : null;
    if (el) { openSessionDetail(+el.dataset.id); }
  });

  /* -------------------- 导出 -------------------- */
  function syncExportUI() {
    var m = $('input[name=exp-mode]:checked').value;
    $('#exp-po-row').hidden = m !== 'po';
    $('#exp-tk-row').hidden = m !== 'tracking';
    $('#exp-range-row').hidden = m !== 'range';
  }
  $$('input[name=exp-mode]').forEach(function (r) { r.onchange = syncExportUI; });

  function exportNow(mode, value) {
    var fmt = $('input[name=exp-fmt]:checked').value;
    var qs = '/api/export?mode=' + mode + '&value=' + encodeURIComponent(value || getExportValue())
      + '&start=' + encodeURIComponent($('#exp-from').value)
      + '&end=' + encodeURIComponent($('#exp-to').value)
      + '&format=' + fmt;
    api(qs).then(function (r) {
      if (!r.ok) { $('#exp-msg').className = 'hint err'; $('#exp-msg').textContent = r.msg || '导出失败'; return; }
      $('#exp-msg').className = 'hint ok';
      $('#exp-msg').textContent = '已导出 ' + r.rows + ' 行 → ' + r.path;
      $('#exp-preview').innerHTML = r.preview.rows.map(function (row) {
        return '<tr>' + row.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>';
      }).join('');
      var a = document.createElement('a');
      a.href = r.url; a.download = r.name;
      document.body.appendChild(a); a.click(); a.remove();
      toast('已导出 ' + r.rows + ' 行', 'ok');
    }).catch(function () { permAlert(); });
  }
  function getExportValue() {
    var m = $('input[name=exp-mode]:checked').value;
    if (m === 'po') return $('#exp-po').value.trim();
    if (m === 'tracking') return $('#exp-tk').value.trim();
    return '';
  }
  $('#btn-export').onclick = function () {
    var m = $('input[name=exp-mode]:checked').value;
    if (m === 'po' && !$('#exp-po').value.trim()) return toast('请填写采购单号', 'warn');
    if (m === 'tracking' && !$('#exp-tk').value.trim()) return toast('请填写物流单号', 'warn');
    if (m === 'range' && (!$('#exp-from').value || !$('#exp-to').value)) return toast('请选择时间范围', 'warn');
    exportNow(m);
  };
  $('#btn-open-folder').onclick = function () { api('/api/open-folder', { method: 'POST' }); };

  /* -------------------- 设置 -------------------- */
  function loadSettingsView() {
    api('/api/settings').then(function (s) {
      var t = s.default_id_type || 'auto';
      if (!$('#st-id-type').querySelector('option[value="' + t + '"]')) t = 'auto';
      $('#st-id-type').value = t;
      $('#id-type').value = t;
      $('#st-owners').value = s.owner_ids || '';
      $('#st-step').value = s.scan_step || '1';
      $('#st-sku-lookup').value = s.sku_lookup || '1';
      $('#st-scan-ignore-disabled').value = s.scan_ignore_disabled === '0' ? '0' : '1';
      $('#st-catalog-auto').value = s.catalog_auto || '1';
      $('#st-catalog-online').value = s.catalog_online || '0';
      $('#st-sound').value = s.sound || '1';
      window.receivingSettingsLoaded = true;
      document.dispatchEvent(new Event('settings:loaded'));
    });
    loadPartners();
    $('#exp-from').value = today();
    $('#exp-to').value = today();
    syncExportUI();
    loadRecents();
    setInterval(loadRecents, 30000);
  }
  $('#btn-save-settings').onclick = function () { window.SettingsWorkspace && SettingsWorkspace.save('receiving'); };

  $('#btn-clear-cache').onclick = function () {
    var button = $('#btn-clear-cache'), target = $('#catalog-save-msg') || $('#st-msg');
    button.disabled = true;
    api('/api/cache/clear', { method: 'POST' }).then(function (r) {
      target.className = r.ok ? 'hint ok' : 'hint err';
      target.textContent = r.ok ? (r.msg || '已清空') : (r.msg || '清空失败');
    }).catch(function () { target.className = 'hint err'; target.textContent = '清空失败，请检查本地服务后重试'; })
      .then(function () { button.disabled = false; });
  };

  /* -------------------- Tab -------------------- */
  $$('.tab[data-tab]').forEach(function (t) { t.onclick = function () { switchTab(t.dataset.tab); }; });
  function navigationReady() {
    if (window.ReceivingWorkspace?.busy() || window.ProductPicker?.isOpen() || pending || window.ReceiptCopy?.isBusy() || writesPending || poRefreshing || pauseReportBusy || finishBusy || (window.ReceiptEditor && ReceiptEditor.isOpen()) || document.querySelector('.qty-saving,.qty-save-error,.qty-unsaved')) {
      toast('请先确认或取消商品数量，并等待保存完成后再切换模块', 'warn'); return false;
    }
    return true;
  }
  function switchTab(name) {
    if (!document.querySelector('.view[data-view="' + name + '"]')) return;
    var current = document.querySelector('.view.active');
    if (current && current.dataset.view !== name) {
      if (!navigationReady()) return;
      if (current.dataset.view === 'settings' && window.SettingsWorkspace && !SettingsWorkspace.beforeLeave()) return;
      window.ReceivingWorkspace?.beforeSwitch(current.dataset.view, name);
      if (current.dataset.view === 'home' && name === 'settings') sessionStorage.setItem('receiving:last-work', '/?view=home');
    }
    $$('.tab').forEach(function (t) { t.classList.toggle('active', t.dataset.tab === name); });
    $$('.view').forEach(function (v) { v.classList.toggle('active', v.dataset.view === name); });
    document.dispatchEvent(new CustomEvent('app:tab', { detail: name }));
    if (name === 'query') loadRecents();
    if (S.session) refocus();
  }

  async function copyPreviousBatch(bid) {
    if (!S.session || bid !== S.current_batch) return toast('请先切换到目标物流单', 'warn');
    if (ReceivingApp.isBusy() || window.ReceiptEditor?.isOpen() || document.querySelector('dialog[open],.qty-unsaved,.qty-saving,.qty-save-error')) return toast('请先完成当前商品或数量操作', 'warn');
    const sid=S.session.id;stateEpoch++;
    try {
      const result=await ReceiptCopy.run(async (action,extra)=>{
        if (sid!==S.session?.id || bid!==S.current_batch) throw Error('当前单据已变化，请重新操作');
        const r=await api('/api/batch/copy',{method:'POST',body:{sid,batch_id:bid,action,...extra}});
        if(!r.ok)throw Error(r.msg||'复制失败');return r;
      });
      if(result){refreshAfterMutation();toast('已从 '+result.copy_source+' 复制 '+fmtNum(result.copy_total)+' 件；仅登记实收，未入库','ok');}
    }catch(err){toast(err.message,'err');}finally{stateEpoch++;}
  }

  window.ReceivingApp = { navigate: switchTab, canNavigate: navigationReady,
    isBusy: function () { return !!(pending || window.ReceiptCopy?.isBusy() || writesPending || poRefreshing || pauseReportBusy || finishBusy || window.ProductPicker?.isOpen() || window.ReceivingWorkspace?.busy()); } };
  document.addEventListener('settings:saved', function (ev) {
    S.settings = Object.assign(S.settings || {}, ev.detail);
    if (ev.detail.default_id_type) $('#id-type').value = ev.detail.default_id_type;
  });
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest('a[href]');
    if (a && !a.hasAttribute('download') && !a.getAttribute('href').startsWith('/api/') && !navigationReady()) { ev.preventDefault(); ev.stopImmediatePropagation(); }
  }, true);
  var initialView = new URLSearchParams(location.search).get('view');
  if (initialView) switchTab(initialView);

  /* -------------------- 焦点保持（适配扫码枪） -------------------- */
  function currentScanInput() {
    if ($('#app').hidden) return $('#lg-account');
    if ($('.view.active') && $('.view.active').dataset.view !== 'home') return null;
    // 有待确认数量时，焦点保持在数量框（全选），回车即确认
    if (pending && !$('#pending-bar').hidden) return $('#pending-qty');
    if (!$('#work-area').hidden) return $('#scan-input');
    if (!$('#idle-card').hidden && $('.view.active').dataset.view === 'home') return $('#po-input');
    return null;
  }
  var pointerSelecting = false;
  function textSelected() { var s=window.getSelection(); return !!(s && !s.isCollapsed); }
  function refocus() {
    if (pointerSelecting || textSelected()) return;
    if (window.ProductPicker?.isOpen() || document.querySelector('.inbound-dialog[open]')) return;
    var el = currentScanInput();
    if (!el || $('#modal').hidden === false || window.ReceiptEditor.isOpen()) return;
    try {
      el.focus();
      // 数量框：聚焦即全选，方便直接改数字；扫码框：选中便于覆盖
      el.select && el.select();
    } catch (e) { }
  }
  setInterval(function () {
    if (Date.now() < suppressRefocus) return;
    var a = document.activeElement;
    if (!a || a === document.body) refocus();
  }, 500);
  document.addEventListener('pointerdown', function () { pointerSelecting = true; });
  document.addEventListener('pointerup', function () { pointerSelecting = false; suppressRefocus = Date.now() + 1200; });
  document.addEventListener('pointercancel', function () { pointerSelecting = false; });
  // Never focus on mousedown: it collapses native selection / interrupts copy.
  window.addEventListener('focus', refocus);
  if (!S.settings) S.settings = { sound: '1' };

  /* -------------------- 心跳（窗口关闭即退出进程） -------------------- */
  setInterval(function () { fetch('/api/heartbeat').catch(function () { }); }, 8000);

  /* -------------------- 启动 -------------------- */
  async function restoreLogin() {
    if (authRestoring) return;
    authRestoring = true;
    var controls = [...document.querySelectorAll('#login button, #login input')].map(function(b) { return {element:b, disabled:b.disabled}; });
    controls.forEach(function(b) { b.element.disabled = true; });
    try {
      await savedAccountsReady;
      var st = await fetch('/api/state').then(function(r) { return r.json(); });
      if (!st.logged && $('#lg-keep').checked) {
        lgTip('正在验证已保存的登录…');
        var restored = await api('/api/login/restore', {method:'POST'});
        if (restored.restored) st = await fetch('/api/state').then(function(r) { return r.json(); });
        else lgTip(restored.msg || '请登录，成功后将按所选偏好保存。');
        $('#lg-restore').hidden = !!restored.restored;
      }
      if (st.logged) enterApp(st.user);
      else ($('#lg-account').value ? $('#lg-password') : $('#lg-account')).focus();
    } catch (err) { lgTip('恢复登录失败，请检查本地服务 / 网络，或手动登录。', true); $('#lg-restore').hidden = false; }
    finally { authRestoring = false; controls.forEach(function(b) { b.element.disabled = b.disabled; }); if (!$('#login').hidden) ($('#lg-account').value ? $('#lg-password') : $('#lg-account')).focus(); }
  }
  $('#lg-restore').onclick = restoreLogin;
  restoreLogin();
  function canSync() {
    var el = document.activeElement;
    return !document.hidden && !$('#app').hidden && !writesPending && !pending && !poRefreshing && !window.ReceiptCopy?.isBusy()
      && !window.ProductPicker?.isOpen() && $('#modal').hidden && !window.ReceiptEditor.isOpen()
      && !document.querySelector('.inbound-dialog[open]') && !pointerSelecting && !textSelected()
      && !(el && (el.tagName === 'SELECT' || el.tagName === 'TEXTAREA'
        || (el.tagName === 'INPUT' && (el.value || el.type === 'number'))));
  }
  function syncReceipt() {
    if (syncRunning || !canSync()) return;
    syncRunning = true;
    var epoch = stateEpoch, bid = S.current_batch;
    api('/api/state' + (bid ? '?batch_id=' + bid : '')).then(function (st) {
      if (epoch !== stateEpoch || !canSync() || !st.logged) return;
      if (JSON.stringify(st) === JSON.stringify(S)) return;
      var sameSession = S.session && st.session && S.session.id === st.session.id;
      S = st;
      if (sameSession && st.session.state === 'active') {
        renderStatus(); renderPoItems(); renderLines(); renderPaused();
      } else renderAll();
    }).catch(function () {}).then(function () { syncRunning = false; });
  }
  setInterval(syncReceipt, 2000);
  window.addEventListener('focus', syncReceipt);
  document.addEventListener('visibilitychange', syncReceipt);
})();

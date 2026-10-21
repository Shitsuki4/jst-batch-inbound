/* 未知条码学习：扫到商品资料查不到的码时，人工搜索选择商品 → 二次确认 → 记住「条码 → 商品」。
   服务端会再次校验（商品资料优先、禁用商品、货主范围），前端只负责交互。
   另：全局 fetch 包装，把响应里的 learned_notice（学习匹配让位 / 冲突等一次性提示）显示为横幅。 */
(function () {
  'use strict';
  if (window.BarcodeLearn) return;
  var esc = function (v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };

  /* ---------- 一次性提示横幅 ---------- */
  var shown = {};
  function notice(text) {
    text = String(text || '').trim();
    if (!text || shown[text]) return;
    shown[text] = 1;
    var box = document.getElementById('bl-notices');
    if (!box) {
      box = document.createElement('div');
      box.id = 'bl-notices';
      box.className = 'bl-notices';
      box.setAttribute('role', 'status');
      box.setAttribute('aria-live', 'polite');
      document.body.appendChild(box);
    }
    var item = document.createElement('div');
    item.className = 'bl-notice';
    item.innerHTML = '<span class="bl-notice-text"></span><button type="button" class="bl-notice-close" aria-label="关闭提示">知道了</button>';
    item.querySelector('.bl-notice-text').textContent = text;
    item.querySelector('button').onclick = function () { item.remove(); };
    box.appendChild(item);
  }
  if (window.fetch && !window.fetch.__blWrapped) {
    var raw = window.fetch.bind(window);
    var wrapped = function () {
      return raw.apply(null, arguments).then(function (res) {
        try {
          var ct = res.headers && res.headers.get('content-type') || '';
          if (ct.indexOf('json') >= 0) {
            res.clone().json().then(function (d) {
              if (d && d.learned_notice) notice(d.learned_notice);
            }).catch(function () {});
          }
        } catch (e) { }
        return res;
      });
    };
    wrapped.__blWrapped = true;
    window.fetch = wrapped;
  }

  /* ---------- 学习对话框 ---------- */
  var active = null;
  function getJSON(url, body) {
    return fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined)
      .then(function (r) { return r.json(); })
      .then(function (d) { if (!d || !d.ok) throw Error((d && d.msg) || '请求失败'); return d; });
  }

  /* open({kind:'purchase'|'pda'|'unplanned'|'joint', sid, code}) → Promise<learned row | null>
     null = 用户跳过 / 关闭，调用方继续原有未识别流程。 */
  function open(opt) {
    if (active) return Promise.resolve(null);
    return new Promise(function (resolve) {
      var dlg = document.createElement('dialog');
      dlg.className = 'inbound-dialog bl-dialog';
      dlg.innerHTML =
        '<div class="bl-head"><h2>学习未知条码</h2><button type="button" class="btn btn-ghost" data-bl="skip">跳过</button></div>'
        + '<p class="bl-code">扫描条码：<b></b></p>'
        + '<p class="hint">商品资料查不到此码。可搜索并选择对应商品，确认后记住此码（仅当前货主 / 公司；以后商品资料能识别时优先用商品资料）。不确定请点「跳过」走原未识别流程。</p>'
        + '<section data-step="search"><form class="bl-search"><input class="input" autocomplete="off" placeholder="商品编码 / 款号 / 名称 / 规格"><button class="btn btn-primary">搜索</button></form>'
        + '<p class="bl-msg" role="status"></p><div class="bl-rows"></div></section>'
        + '<section data-step="confirm" hidden><div class="bl-confirm"></div><p class="bl-msg" role="status"></p>'
        + '<div class="bl-actions"><button type="button" class="btn btn-primary" data-bl="learn">确认学习并重新识别</button> '
        + '<button type="button" class="btn btn-ghost" data-bl="back">返回重选</button> '
        + '<button type="button" class="btn btn-ghost" data-bl="skip">跳过</button></div></section>';
      document.body.appendChild(dlg);
      dlg.querySelector('.bl-code b').textContent = opt.code;
      var q = dlg.querySelector('.bl-search input');
      var stepS = dlg.querySelector('[data-step="search"]'), stepC = dlg.querySelector('[data-step="confirm"]');
      var rows = [], chosen = null, busy = false, done = false;
      active = dlg;

      function finish(val) {
        if (done) return;
        done = true; active = null;
        try { dlg.close(); } catch (e) { }
        dlg.remove();
        resolve(val);
      }
      function say(step, text, err) {
        var m = step.querySelector('.bl-msg');
        m.textContent = text || '';
        m.className = 'bl-msg' + (err ? ' inbound-warning' : '');
      }
      function search() {
        if (busy) return;
        busy = true; say(stepS, '正在搜索…');
        getJSON('/api/learned/search?kind=' + encodeURIComponent(opt.kind) + '&sid=' + encodeURIComponent(opt.sid || '') + '&q=' + encodeURIComponent(q.value.trim()))
          .then(function (d) {
            rows = d.rows || [];
            say(stepS, rows.length ? (q.value.trim() ? '找到 ' + rows.length + ' 个商品' : '当前单据内商品（可输入关键字搜索商品资料）')
              : (d.scoped && !d.catalog_ready ? '未找到；当前货主商品库尚未同步，可先同步商品库' : '未找到商品，请换关键字'), !rows.length);
            dlg.querySelector('.bl-rows').innerHTML = rows.map(function (r, i) {
              var off = r.enabled === false;
              return '<button type="button" class="bl-row' + (off ? ' bl-off' : '') + '" data-i="' + i + '"' + (off ? ' disabled' : '') + '>'
                + '<b>' + esc(r.name || r.sku_id) + '</b>'
                + '<span>编码 ' + esc(r.sku_id) + (r.i_id ? ' · 款号 ' + esc(r.i_id) : '') + (r.sku_code && r.sku_code !== r.sku_id ? ' · 条码 ' + esc(r.sku_code) : '') + '</span>'
                + (r.spec ? '<span>' + esc(r.spec) + '</span>' : '')
                + '<em>' + (r.in_po ? '当前单据内' : '商品资料') + (off ? ' · 已禁用，不可学习' : '') + '</em></button>';
            }).join('');
          })
          .catch(function (e) { say(stepS, e.message || '搜索失败', true); })
          .then(function () { busy = false; });
      }
      function confirmStep(r) {
        chosen = r;
        stepS.hidden = true; stepC.hidden = false; say(stepC, '');
        dlg.querySelector('.bl-confirm').innerHTML =
          '<p>请核对实物后确认：</p><table class="bl-table"><tr><th>扫描条码</th><td class="bl-big">' + esc(opt.code) + '</td></tr>'
          + '<tr><th>对应商品</th><td><b>' + esc(r.name || '') + '</b><br>编码 ' + esc(r.sku_id) + (r.i_id ? ' · 款号 ' + esc(r.i_id) : '')
          + (r.spec ? '<br>' + esc(r.spec) : '') + '</td></tr></table>'
          + '<p class="hint">学习后此码在当前货主下按该商品识别，并在明细上标「学习匹配」。</p>';
        dlg.querySelector('[data-bl="learn"]').focus();
      }
      function learn() {
        if (busy || !chosen) return;
        busy = true; say(stepC, '正在保存…');
        getJSON('/api/learned/learn', { kind: opt.kind, sid: opt.sid, code: opt.code, sku_id: chosen.sku_id })
          .then(function (d) { notice(d.msg || '已学习'); finish(d.learned || { code: opt.code, sku_id: chosen.sku_id }); })
          .catch(function (e) { busy = false; say(stepC, e.message || '学习失败', true); });
      }

      dlg.querySelector('.bl-search').onsubmit = function (e) { e.preventDefault(); search(); };
      dlg.addEventListener('click', function (e) {
        var b = e.target.closest('button');
        if (!b || b.disabled) return;
        if (b.dataset.i != null) return confirmStep(rows[+b.dataset.i]);
        var act = b.dataset.bl;
        if (act === 'skip') finish(null);
        else if (act === 'back') { stepC.hidden = true; stepS.hidden = false; q.focus(); }
        else if (act === 'learn') learn();
      });
      dlg.addEventListener('cancel', function (e) { e.preventDefault(); finish(null); });
      // 弹框期间不让页面的扫码热键 / 回车处理吃掉输入
      dlg.addEventListener('keydown', function (e) { e.stopPropagation(); });
      if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
      q.focus();
      search();
    });
  }

  function tag(line) {
    return line && line.match_src === 'learned' ? '<span class="bl-tag" title="此码由人工学习匹配，商品资料查不到时才使用">学习匹配</span>' : '';
  }

  window.BarcodeLearn = { open: open, isOpen: function () { return !!active; }, notice: notice, tag: tag };
})();

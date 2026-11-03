/* 设置 → 诊断与日志：导出诊断包、打开日志文件夹、查看最近的报错（按错误码 / 日志编号 / 关键字筛选）。
   只读本机日志，不改任何收货数据。日志里不含密码、令牌、Cookie。 */
(function () {
  'use strict';
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function size(n) { n = Number(n) || 0; return n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n >= 1024 ? Math.round(n / 1024) + ' KB' : n + ' B'; }
  function json(path, opts) {
    return fetch(path, opts).then(function (r) { return r.json(); }, function () {
      return { ok: false, msg: '[E-NET-1001] 没连上本机收货服务', error_code: 'E-NET-1001' };
    });
  }
  function post(path, body) { return json(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); }
  function msgOf(r, fallback) { var m = (r && r.msg) || fallback; return window.JstErr ? window.JstErr.ensure(m) : m; }

  function mount() {
    var pane = document.querySelector('[data-settings-pane="diagnostic"]');
    if (!pane) return false;
    if (pane.querySelector('#diag-card')) return true;
    pane.innerHTML = '<div class="card" id="diag-card"><div class="card-hd">诊断与日志</div>'
      + '<p class="hint">每条报错都带错误码和日志编号，并记入本机诊断日志。遇到问题时导出诊断包发给维护人员：只含日志、版本与环境信息和错误码表，不含数据库和密码。</p>'
      + '<div class="row"><button class="btn btn-primary" type="button" id="diag-export">导出诊断包</button>'
      + '<button class="btn btn-ghost" type="button" id="diag-open">打开日志文件夹</button>'
      + '<button class="btn btn-ghost" type="button" id="diag-refresh">刷新</button></div>'
      + '<p class="hint" id="diag-status" role="status"></p>'
      + '<p class="hint" id="diag-where"></p></div>'
      + '<div class="card"><div class="card-hd">最近的报错</div>'
      + '<div class="row"><input class="input" id="diag-q" placeholder="按错误码（如 E-NET-2001）、日志编号或关键字筛选" autocomplete="off">'
      + '<button class="btn btn-secondary" type="button" id="diag-search">筛选</button></div>'
      + '<div id="diag-list"><p class="hint">正在读取…</p></div></div>';
    var st = pane.querySelector('#diag-status');
    pane.querySelector('#diag-export').onclick = function () {
      st.textContent = '正在导出诊断包…';
      post('/api/diag/export', { open: true }).then(function (r) {
        st.textContent = r.ok ? '已导出：' + r.path + (r.opened ? '' : '（在「导出」文件夹里）') : msgOf(r, '导出失败');
      });
    };
    pane.querySelector('#diag-open').onclick = function () {
      post('/api/diag/open-logs').then(function (r) { st.textContent = r.ok ? '日志文件夹：' + r.folder : msgOf(r, '打开失败'); });
    };
    pane.querySelector('#diag-refresh').onclick = load;
    pane.querySelector('#diag-search').onclick = load;
    pane.querySelector('#diag-q').onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); load(); } };
    load();
    return true;
  }

  function load() {
    var pane = document.querySelector('[data-settings-pane="diagnostic"]');
    if (!pane) return;
    // 弹窗里的报错大多是 WARN 级（断网、无权限、业务校验），默认列 WARN 和 ERROR；
    // 按错误码或日志编号查时不限级别，弹窗里给的日志编号一定能查到。
    var q = (pane.querySelector('#diag-q').value || '').trim(), qs = 'limit=100';
    if (/^\[?E-[A-Z]+-\d{4}\]?$/i.test(q)) qs += '&code=' + encodeURIComponent(q.replace(/[\[\]]/g, '').toUpperCase());
    else if (/^[0-9a-f]{6,}$/i.test(q)) qs += '&rid=' + encodeURIComponent(q.toLowerCase());
    else { qs += '&level=WARN,ERROR'; if (q) qs += '&q=' + encodeURIComponent(q); }
    json('/api/diag/info').then(function (r) {
      if (!r.ok) return;
      pane.querySelector('#diag-where').innerHTML = '日志位置：' + esc(r.log_dir) + '<br>单个文件满 ' + size(r.max_bytes)
        + ' 换新文件，最多保留 ' + esc(r.backups) + ' 个旧文件。诊断包保存在：' + esc(r.export_dir)
        + ((r.files || []).length ? '<br>现有日志：' + r.files.map(function (f) { return esc(f.name) + '（' + size(f.size) + '）'; }).join('、') : '');
    });
    json('/api/diag/logs?' + qs).then(function (r) {
      var box = pane.querySelector('#diag-list');
      if (!r.ok) { box.innerHTML = '<p class="hint">' + esc(msgOf(r, '读取日志失败')) + '</p>'; return; }
      var rows = r.entries || [];
      if (!rows.length) { box.innerHTML = '<p class="hint">' + (q ? '没有符合条件的报错。' : '最近没有报错。') + '</p>'; return; }
      box.innerHTML = '<table class="tbl"><thead><tr><th>时间</th><th>级别</th><th>错误码</th><th>内容</th><th>日志编号</th></tr></thead><tbody>'
        + rows.map(function (e) {
          var extra = [e.po ? '采购单 ' + e.po : '', e.tracking ? '物流单 ' + e.tracking : '', e.op || ''].filter(Boolean).join(' · ');
          return '<tr><td>' + esc(e.ts) + '</td><td>' + esc(e.level) + '</td><td><b>' + esc(e.code) + '</b></td><td>' + esc(e.msg) + (extra ? '<div class="hint">' + esc(extra) + '</div>' : '')
            + '</td><td>' + esc(e.rid) + '</td></tr>';
        }).join('') + '</tbody></table>';
    });
  }

  window.JstDiagSettings = { load: load };
  if (!mount()) {
    var tries = 0, t = setInterval(function () { if (mount() || ++tries > 50) clearInterval(t); }, 200);
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-settings-section="diagnostic"]');
    if (b) setTimeout(function () { if (mount()) load(); }, 0);
  });
})();

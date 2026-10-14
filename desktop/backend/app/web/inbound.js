/* 独立的入库预览/生成窗口：数量来自服务端快照，不从表格 DOM 推算。 */
(function () {
  'use strict';
  var dialog = document.createElement('dialog');
  dialog.className = 'inbound-dialog';
  dialog.setAttribute('aria-label', '按实收生成入库单');
  document.body.appendChild(dialog);
  var sid = 0, busy = false, selectedWh = '', options = null, mode = '';
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  }); }
  function request(url, body) {
    return fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) } : {}).then(function (r) { return r.json(); });
  }
  function message(msg, error) {
    var box = dialog.querySelector('.inbound-message');
    box.textContent = msg;
    box.className = 'inbound-message' + (error ? ' error' : ' success');
  }
  function setBusy(value) {
    busy = value;
    dialog.querySelectorAll('button, input, select').forEach(function (el) { el.disabled = value; });
  }
  function close() { if (!busy) dialog.close(); }
  dialog.addEventListener('cancel', function (e) { if (busy) e.preventDefault(); });
  function heading() {
    dialog.innerHTML = '<div class="inbound-heading"><h2>按实收生成入库单</h2>'
      + '<button type="button" class="btn btn-ghost inbound-close">关闭</button></div>'
      + '<p class="inbound-message" role="status" aria-live="polite"></p>'
      + '<div class="inbound-content">正在读取实收明细…</div>';
    dialog.querySelector('.inbound-close').onclick = close;
  }
  function chooseMode() {
    heading();
    dialog.querySelector('.inbound-content').innerHTML = '<div class="inbound-step">01 / 选择入库方式</div>'
      + '<p class="inbound-intro">按真实收到的数量生成，你决定哪些编码加入本次入库单。</p>'
      + '<div class="inbound-mode-grid">'
      + '<button type="button" class="inbound-mode-card" data-mode="purchase_only"><span class="mode-icon">01</span><strong>仅采购单内编码</strong><span>按实收生成入库单，采购单没有的编码不加入。</span><small>单外明细保留在本地，可稍后另行生成。</small><b>选择此方式 →</b></button>'
      + '<button type="button" class="inbound-mode-card" data-mode="all_received"><span class="mode-icon">02</span><strong>全部实收编码</strong><span>按实收生成入库单，采购单没有的编码也加入。</span><small>编码仍须存在于当前货主的商品资料中。</small><b>选择此方式 →</b></button></div>'
      + '<p class="inbound-safe">只生成「待入库」草稿，不自动确认库存；两种方式都不会修改采购单、价格或供应商。</p>';
    dialog.querySelectorAll('[data-mode]').forEach(function (el) {
      el.onclick = function () { mode = el.dataset.mode; selectedWh = ''; load(); };
    });
  }
  function load(note, error) {
    heading();
    setBusy(true);
    return request('/api/inbound/options?sid=' + sid).then(function (o) {
      options = o;
      if (!o.ok) selectedWh = '';
      return request('/api/inbound/preview?sid=' + sid + '&mode=' + encodeURIComponent(mode) + (selectedWh ? '&wh_id=' + encodeURIComponent(selectedWh) : ''));
    }).then(function (r) {
      setBusy(false);
      if (!r.ok) { dialog.querySelector('.inbound-content').textContent = ''; message(r.msg, true); return; }
      render(r);
      if (note) message(note, error);
    }).catch(function () { setBusy(false); message('读取失败，请检查本地服务后重新打开。', true); });
  }
  function render(r) {
    var snap = r.snapshot, jobs = r.jobs || [];
    var unresolved = jobs.some(function (j) { return j.status !== 'complete' && j.status !== 'rejected'; });
    var h = '<p>采购单 <b>' + esc(snap.po_id) + '</b> · 本次待生成 <b>'
      + snap.batches.length + '</b> 个物流单号 / <b>' + snap.items.length + '</b> 种商品 / 实收 <b>'
      + esc(r.total_qty) + '</b> 件</p>'
      + '<p class="inbound-note">仅包含尚未提交的实收明细；同商品按实收合并，零数量不生成。'
      + '生成聚水潭「待入库」单，<b>不会自动确认入库</b>。部分生成后，该物流批次保留编辑锁；排除的单外明细仍未提交，可用「全部实收编码」另行生成，不重复已处理商品。补收请新建物流子批次。</p>';
    h += '<div class="inbound-validation"><span>02 / 核对明细与仓库</span><b>'
      + (mode === 'purchase_only' ? '仅采购单内编码' : '全部实收编码')
      + '</b><button class="btn btn-ghost inbound-change-mode">重新选择方式</button></div>';
    if ((snap.excluded_items || []).length) {
      h += '<details class="inbound-excluded" open><summary>本次不加入：' + snap.excluded_items.length + ' 条单外明细 / ' + esc(r.excluded_qty) + ' 件（本地保留，未提交）</summary>'
        + '<div class="inbound-table"><table class="tbl"><thead><tr><th>物流单号</th><th>商品编码</th><th>商品名称</th><th>实收</th></tr></thead><tbody>'
        + snap.excluded_items.map(function (x) { return '<tr><td>' + esc(x.tracking_no) + '</td><td>' + esc(x.sku_id) + '</td><td>' + esc(x.name) + '</td><td>' + esc(x.qty) + '</td></tr>'; }).join('') + '</tbody></table></div></details>';
    }
    if (options && options.ok) {
      h += '<div class="inbound-scope"><p>原收货货主：<b>' + esc(options.owner_name) + '</b>（' + esc(options.owner)
        + '） · 仓库公司：<b>' + esc(options.auth_name) + '</b>（' + esc(options.auth) + '）</p>'
        + '<label>入库仓库 <select class="select inbound-warehouse"><option value="">请选择仓库（主仓／进货／次品／附属仓）</option>'
        + options.warehouses.map(function (w) { return '<option value="' + esc(w.id) + '"' + (selectedWh === w.id ? ' selected' : '') + '>' + esc(w.name) + '</option>'; }).join('')
        + '</select></label><p class="hint">货主及仓库公司沿用此收货批次，不能被当前界面其它选择覆盖。选仓后重新计算确认快照。</p></div>';
    } else h += '<p class="inbound-warning">' + esc(options && options.msg || '仓库清单读取失败') + '。仍可查看已有记录。</p>';
    if (snap.batches.length) h += '<p>物流单号：' + snap.batches.map(function (b) { return esc(b.tracking_no); }).join('、') + '</p>';
    if (r.warnings.length) h += '<div class="inbound-warning">' + r.warnings.map(esc).join('<br>') + '</div>';
    if (snap.items.length) {
      h += '<div class="inbound-table"><table class="tbl"><thead><tr><th>商品编码</th><th>商品名称</th><th>本次实收</th></tr></thead><tbody>'
        + snap.items.map(function (p) { return '<tr><td>' + esc(p.sku_id)
          + (p.outside_purchase ? '<br><span class="chip">采购单外商品 · 添加商品</span>' : '') + '</td><td>' + esc(p.name)
          + '</td><td>' + esc(p.qty) + '</td></tr>'; }).join('') + '</tbody></table></div>';
      if (!unresolved && snap.warehouse) h += '<button class="btn btn-primary inbound-create">确认按以上实收生成待入库单</button>';
    } else h += '<p class="empty">暂无可生成的新实收；已生成记录见下方。</p>';
    if (unresolved) h += '<p class="inbound-warning">有尚未核对完成的记录，必须先处理原单，不能再次开单。</p>';
    if (jobs.length) h += '<h3>入库生成记录</h3>' + jobs.map(function (j) {
      var completed = j.status === 'complete', rejected = j.status === 'rejected';
      return '<div class="inbound-job"><b>' + (j.io_id ? '入库单 ' + esc(j.io_id) : (rejected ? '创建被拒绝' : '创建结果待核实'))
        + '</b><span class="chip">' + (completed ? '实收核对完成' : (rejected ? '未生成，可重新预览' : (j.io_id ? '待继续完成' : '禁止重复开单'))) + '</span>'
        + '<div>' + esc(j.created_at) + (j.warehouse ? ' · 入库仓库：' + esc(j.warehouse.name) : ' · 旧版记录：沿用原单仓库') + '</div>'
        + '<div>本单方式：' + (j.validate_purchase_details === false ? '全部实收编码' : '采购单内编码') + '</div>'
        + (j.error ? '<p class="inbound-warning">' + esc(j.error) + '</p>' : '')
        + (!completed && !rejected && !j.io_id ? '<p>请先在聚水潭按该采购单核查；仅可关联原空草稿或数量完全一致的原单，不能另建一张。</p>'
          + '<label>原入库单号 <input class="input inbound-bind-id" data-job="' + j.id + '" inputmode="numeric" placeholder="聚水潭 io_id"></label>' : '')
        + (!completed && !rejected ? '<button class="btn btn-ghost inbound-resume" data-job="' + j.id + '" data-bind="' + (!j.io_id ? '1' : '0') + '">'
          + (j.io_id ? '继续完成原单（不重建）' : '确认关联原单并核对') + '</button>' : '') + '</div>';
    }).join('');
    dialog.querySelector('.inbound-content').innerHTML = h;
    dialog.querySelector('.inbound-change-mode').onclick = chooseMode;
    var select = dialog.querySelector('.inbound-warehouse');
    if (select) select.onchange = function () { selectedWh = select.value; load(); };
    var create = dialog.querySelector('.inbound-create');
    if (create) create.onclick = function () {
      submit('/api/inbound/create', { sid: sid, wh_id: snap.warehouse.id, fingerprint: r.fingerprint, mode: mode, confirmed: true });
    };
    dialog.querySelectorAll('.inbound-resume').forEach(function (el) {
      el.onclick = function () {
        var id = el.dataset.job, io = '';
        if (el.dataset.bind === '1') {
          io = dialog.querySelector('.inbound-bind-id[data-job="' + id + '"]').value.trim();
          if (!/^\d+$/.test(io)) { message('请先输入经聚水潭核实的原入库单号。', true); return; }
        }
        submit('/api/inbound/resume', { job_id: +id, io_id: io, confirmed: true });
      };
    });
  }
  function submit(url, body) {
    if (busy) return;
    setBusy(true);
    message('正在生成/核对原入库单，请勿关闭程序或重复操作…', false);
    request(url, body).then(function (r) {
      setBusy(false);
      document.dispatchEvent(new CustomEvent('inbound-updated'));
      return load(r.msg || '操作完成', !r.ok);
    }).catch(function () {
      setBusy(false);
      load('请求中断，结果可能已写入。请先查看下方原单记录，不要另建入库单。', true);
    });
  }
  window.openInbound = function (sessionId) {
    if (busy) return;
    sid = +sessionId;
    selectedWh = ''; options = null; mode = '';
    if (!dialog.open) dialog.showModal();
    chooseMode();
  };
  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-inbound-sid]');
    if (el) window.openInbound(el.dataset.inboundSid);
  });
}());

/* 本地商品库同步状态；只渲染纯文本，不在扫码输入事件中触发网络查询。 */
(function () {
  'use strict';
  var pending = false, button = document.getElementById('btn-catalog-sync');
  function date(ts) { return ts ? new Date(ts * 1000).toLocaleString() : '尚未完成'; }
  function refresh() {
    if (pending) return;
    pending = true;
    fetch('/api/catalog/status').then(function (r) { return r.json(); }).then(function (r) {
      if (!r.ok) throw new Error(r.msg || '状态读取失败');
      button.disabled = r.sync.running || !r.logged || !r.enabled;
      document.getElementById('catalog-status').textContent = (r.enabled ? '' : '自动同步已关闭。')
        + r.sync.message + (r.sync.running ? ' · 本货主已拉取 ' + r.sync.loaded + ' 条' : '');
      var tbody = document.getElementById('catalog-rows');
      tbody.textContent = '';
      r.rows.forEach(function (row) {
        var tr = document.createElement('tr');
        [row.label + '（' + row.owner + ' / ' + row.auth + '）', row.count, date(row.success),
          row.error || (row.stale ? '旧快照仍可用，等待更新' : '已缓存')].forEach(function (v) {
          var td = document.createElement('td'); td.textContent = v; tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      if (!r.rows.length) {
        var tr = document.createElement('tr'), td = document.createElement('td');
        td.colSpan = 4; td.textContent = '暂无全量快照。首次登录后自动拉取；完成前仍可匹配本地采购明细。';
        tr.appendChild(td); tbody.appendChild(tr);
      }
    }).catch(function (e) {
      document.getElementById('catalog-status').textContent = '无法读取同步状态：' + e.message;
    }).finally(function () { pending = false; });
  }
  button.onclick = function () {
    button.disabled = true;
    fetch('/api/catalog/sync', {method: 'POST', headers: {'Content-Type':'application/json'}, body:'{}'})
      .then(function(r) {return r.json();})
      .then(function(r) {document.getElementById('catalog-message').textContent = r.msg || '请求已提交'; refresh();})
      .catch(function() {document.getElementById('catalog-message').textContent = '提交失败，请刷新状态后重试'; button.disabled = false;});
  };
  document.getElementById('btn-catalog-status').onclick = refresh;
  document.addEventListener('click', function(e) {if (e.target.closest('[data-tab="settings"]')) refresh();});
  setInterval(function() {if (document.querySelector('.view.active[data-view="settings"]')) refresh();}, 3000);
  refresh();
}());

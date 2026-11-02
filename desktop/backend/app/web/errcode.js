/* 统一错误码（页面端）。必须作为每个页面的第一个脚本加载。
 * - 本机服务的报错自带 [E-XXX-NNNN] 和日志编号 request_id（见 errors.py）。
 * - 这里补上页面自己发现的错误：连不上本机服务（E-NET-1001）、返回的不是 JSON（E-NET-1003）、
 *   HTTP 错误但没带错误码（E-NET-1004 / E-REQ-1404），让任何页面、任何旧的 fetch 写法都能显示错误码。
 * - 只有 error_code === 'E-JST-1401'（聚水潭明确拒绝权限）才算权限问题。
 */
(function (global) {
  'use strict';
  if (global.JstErr && global.JstErr.installed) return;
  var CODE_RE = /\[(E-[A-Z]{2,5}-\d{4})\]/;
  var TITLES = {
    'E-NET-1001': '没连上本机收货服务',
    'E-NET-1002': '本机收货服务响应超时',
    'E-NET-1003': '本机收货服务返回的数据无法识别',
    'E-NET-1004': '本机收货服务返回了 HTTP 错误',
    'E-REQ-1404': '接口不存在',
    'E-UI-1001': '页面输入没有通过检查'
  };
  var PERMISSION = 'E-JST-1401';

  function codeOf(x) {
    if (!x) return '';
    if (typeof x === 'object') {
      if (x.error_code) return String(x.error_code);
      x = x.msg != null ? x.msg : (x.message != null ? x.message : '');
    }
    var m = CODE_RE.exec(String(x));
    return m ? m[1] : '';
  }
  function withCode(code, msg) {
    msg = String(msg == null ? '' : msg).trim() || TITLES[code] || '';
    return CODE_RE.test(msg) ? msg : '[' + code + '] ' + msg;
  }
  function isPermission(r) { return codeOf(r) === PERMISSION; }
  function logHint(r) {
    var rid = r && (r.request_id || r.rid);
    return rid ? '日志编号 ' + rid + '（设置 → 诊断与日志 → 导出诊断包，可按此编号查到这次报错）' : '';
  }
  // 一条报错的完整文字：[错误码] 原因 + 建议 + 日志编号
  function describe(r, fallbackCode) {
    r = r || {};
    var code = codeOf(r) || fallbackCode || 'E-APP-9001';
    var parts = [withCode(code, r.msg != null ? r.msg : r.message)];
    if (r.hint) parts.push(String(r.hint));
    var hint = logHint(r);
    if (hint) parts.push(hint);
    return parts.join('\n');
  }
  function netError(detail) {
    var e = new TypeError(withCode('E-NET-1001', '没连上本机收货服务' + (detail ? '（' + detail + '）' : '')));
    e.error_code = 'E-NET-1001';
    e.transport = true;
    return e;
  }
  function sameOrigin(url) {
    try {
      var u = new URL(typeof url === 'string' ? url : (url && url.url) || '', global.location.href);
      return u.origin === global.location.origin;
    } catch (e) { return false; }
  }
  function patchResponse(res) {
    if (!res || res.__jstErrPatched) return res;
    var origJson = res.json.bind(res);
    try {
      Object.defineProperty(res, '__jstErrPatched', { value: true });
      res.json = function () {
        return origJson().then(function (d) {
          if (d && typeof d === 'object' && d.ok === false && !d.error_code) {
            var code = codeOf(d) || (res.status === 404 ? 'E-REQ-1404' : res.status >= 500 ? 'E-NET-1004' : '');
            if (code) { d.error_code = code; d.msg = withCode(code, d.msg); }
          }
          return d;
        }, function () {
          var code = res.status === 404 ? 'E-REQ-1404' : 'E-NET-1003';
          var e = new Error(withCode(code, (TITLES[code] || '') + '（HTTP ' + res.status + '）'));
          e.error_code = code;
          throw e;
        });
      };
    } catch (e) { /* 只读对象时保持原样 */ }
    return res;
  }
  var origFetch = global.fetch && global.fetch.bind(global);
  if (origFetch) {
    global.fetch = function (input, init) {
      if (!sameOrigin(input)) return origFetch(input, init);
      return origFetch(input, init).then(patchResponse, function (e) {
        // 主动取消（AbortError）和已带码的错误保持原样，其余都是连不上本机服务
        if (e && (e.name === 'AbortError' || e.error_code)) throw e;
        throw netError(e && e.message);
      });
    };
  }
  // 弹窗 / 提示里没有错误码的本地校验报错，统一补 E-UI-1001
  function ensure(msg, code) { return withCode(code || codeOf(msg) || 'E-UI-1001', msg); }

  global.JstErr = {
    installed: true,
    PERMISSION: PERMISSION,
    codeOf: codeOf,
    withCode: withCode,
    ensure: ensure,
    isPermission: isPermission,
    describe: describe,
    logHint: logHint,
    netError: netError
  };
})(typeof window !== 'undefined' ? window : globalThis);

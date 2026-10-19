# -*- coding: utf-8 -*-
"""
聚水潭 ERP 登录/查询及 WebForms 回调客户端 —— 仅依赖 Python 标准库。

覆盖：
  1. 登录            POST api.erp321.com/erp/webapi/UserApi/WebLogin/Passport
  2. 采购单列表       www.erp321.com/app/scm/purchase/purchasemode.aspx  → LoadDataToJSON
  3. 采购单明细       www.erp321.com/app/scm/purchase/purchaseitem.aspx   → 页面内嵌 #_jt_data

对外异常约定：
  LoginError  登录失败（前端展示友好文案）
  BizError    其余任何失败（前端统一展示「当前账号无此操作权限」，内部日志记录细节）
"""
import json
from codes import item_auxiliary
import os
import re
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")

LOGIN_URL = "https://api.erp321.com/erp/webapi/UserApi/WebLogin/Passport"
LOGIN_REFERER = "https://www.erp321.com/login.aspx"
WWW = "https://www.erp321.com"

# 图形验证码图片。服务端要求验证码时会在 Passport 返回 code=10003，
# 这里的 id 就是登录账号，t 用毫秒时间戳防止缓存。
VALIDCODE_URL = WWW + "/ValidCode.aspx"

# 短信验证码登录。注意这两个接口挂在 usercenter 下、不带 /erp 前缀，
# 但同属 api.erp321.com（前端 axios 的 baseURL 就是 //api.erp321.com）。
UC_SMS_SEND = "https://api.erp321.com/usercenter/AuthApi/WebLogin/SendVerificationV2"
UC_SMS_LOGIN = "https://api.erp321.com/usercenter/AuthApi/WebLogin/VerificationV2"

# Passport 的特殊业务码：10003 需要图形验证码；10005 需要去网页重置密码；
# 301109 账号注销中。
CODE_NEED_CAPTCHA = 10003
PO_LIST_PATH = "/app/scm/purchase/purchasemode.aspx"
PO_DETAIL_PATH = "/app/scm/purchase/purchaseitem.aspx"

# 新架构（商品资料）JSON API 根地址。两个 host 别混：
#   api.erp321.com    路径带 /erp，body 需要额外带 isOtherStore
#   apiweb.erp321.com 路径不带 /erp
API_ROOTS = {
    "api":    "https://api.erp321.com/erp/webapi/",
    "apiweb": "https://apiweb.erp321.com/webapi/",
}
# 商品资料列表只回填 queryFlds 里声明的列，按需索取以压小响应体
# pic：商品主图 URL，多候选弹窗要用大图供人工分辨
SKU_QUERY_FLDS = ["i_id", "sku_id", "name", "properties_value", "sku_code", "enabled", "unit", "pic"]

_SSL = ssl.create_default_context()

# ---------------------------------------------------------------- 异常

class LoginError(Exception):
    """登录失败，允许展示具体原因（账号密码错误 / 需要安全验证）"""

class CaptchaError(LoginError):
    """服务端要求图形验证码才能继续登录。前端要拉 ValidCode.aspx 并让用户填。"""
    need_captcha = True

class BizError(Exception):
    """其它一切失败。前端一律提示「当前账号无此操作权限」"""

class CallbackRejected(BizError):
    """WebForms 明确返回 IsSuccess=false，区别于写请求网络超时。"""

class ItemLookupError(Exception):
    """商品资料接口不可用（网络 / 无权限 / 返回异常）。
    与「确认查无此商品」区分开：前者不该写入负缓存。"""

class ItemAmbiguousError(ItemLookupError):
    def __init__(self, items):
        super().__init__("该编码对应多个商品，请选择")
        self.items = items

# ---------------------------------------------------------------- 工具

def _num(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(str(v).replace(",", ""))
    except Exception:
        return default

def _s(v):
    return "" if v is None else str(v).strip()

def _eq(a, b):
    """宽松相等：忽略首尾空白与大小写（商品编码常带大小写差异）"""
    return _s(a).lower() == _s(b).lower()

def _diag(msg):
    """把接口层面的异常/异常结构打到 stderr（即启动控制台），并追加到诊断文件，
    便于现场取证。刻意不抛异常：诊断输出绝不能影响主流程。"""
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        sys.stderr.write("[jst] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass
    try:
        if getattr(sys, "frozen", False):
            # 打包后 __file__ 在只读的临时目录，诊断日志改放 exe 同目录
            base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base = os.path.abspath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), ".."))
        path = os.path.join(base, "data", "diag.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# 分仓 / 货主接口的字段名在不同网关版本下不统一。把候选集中在这里，
# 避免再出现「id 认得出、name 认不出 → 界面显示一串数字」这种漏字段的事故。
# 实测 GetWmsCoIds 返回 wms_co_id / wms_co_name（2026-09-30 抓取确证）。
_WMS_ID_KEYS = ("wms_co_id", "co_id", "id", "wmsId", "wms_id")
_WMS_NAME_KEYS = ("wms_co_name", "co_name", "name", "short_name", "wms_name", "wh_name")

def _wms_id(row, fallback=""):
    for k in _WMS_ID_KEYS:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return fallback

def _wms_name(row):
    for k in _WMS_NAME_KEYS:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return ""

_OWNER_ID_KEYS = ("consignor_co_id", "owner_co_id", "co_id", "id")
_OWNER_NAME_KEYS = ("consignor_co_name", "owner_co_name", "co_name", "name", "short_co_name")

def _owner_id(row, fallback=""):
    for k in _OWNER_ID_KEYS:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return fallback

def _owner_name(row):
    for k in _OWNER_NAME_KEYS:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return ""

# ---------------------------------------------------------------- 客户端

class JstClient(object):
    # Serializes cookie writers (absorb/apply) so two concurrent responses cannot lose
    # each other's update; class-level so test fakes that skip __init__ still have it.
    _cookie_lock = threading.Lock()

    def __init__(self, logger=None, timeout=25):
        # name -> 原始（已 URL 编码）取值。写入一律"复制后整体替换"（copy-on-write），
        # 读取方拿到的是某一时刻的完整快照，不会遇到 dictionary changed size during iteration。
        self.cookies = {}
        self.uid = ""
        self.coid = ""
        self.uname = ""
        self.coname = ""
        self.timeout = timeout
        self.log = logger or (lambda *a, **k: None)
        # VIEWSTATE 缓存：同一页面短时间内复用，省一次 GET
        self._vs_cache = {}
        # 商品资料反查缓存：code+货主 -> (时间戳, 结果|None)，10 分钟
        self._sku_cache = {}

    # ---------------- 基础 HTTP ----------------
    def _cookie_header(self):
        ck = self.cookies   # 取一次引用即为快照：写方只整体替换、从不原地修改
        return "; ".join("%s=%s" % (k, v) for k, v in list(ck.items()) if v not in (None, ""))

    def _request(self, url, body=None, extra_headers=None, referer=None, is_json=False, timeout=None):
        data = None
        headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, */*; q=0.01" if is_json else "text/html,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        ck = self._cookie_header()
        if ck:
            headers["Cookie"] = ck
        if referer:
            headers["Referer"] = referer
        headers["Origin"] = WWW
        if body is not None:
            if is_json:
                data = body.encode("utf-8")
                headers["Content-Type"] = "application/json"
            else:
                data = body.encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=(timeout or self.timeout), context=_SSL) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise BizError("HTTP %s %s" % (e.code, url))
        except Exception as e:
            raise BizError("NET %s %s" % (e, url))
        try:
            return raw.decode("utf-8", "replace")
        except Exception:
            return raw.decode("gbk", "replace")

    def _raw(self, url, body=None, referer=None, timeout=None, extra_headers=None,
             binary=False, is_json=True):
        """
        底层请求。返回 (payload, meta)。
        payload 为 bytes（binary=True）或 str；meta 含 content-type 与 Set-Cookie 原始行。
        只在需要读响应头 / 二进制体（验证码图、既写 Set-Cookie 又写 body 的登录接口）时用。
        """
        data = None
        headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, */*; q=0.01" if is_json else "text/html,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        ck = self._cookie_header()
        if ck:
            headers["Cookie"] = ck
        if referer:
            headers["Referer"] = referer
        headers["Origin"] = WWW
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            else:
                data = body.encode("utf-8") if isinstance(body, str) else body
                headers["Content-Type"] = "application/x-www-form-urlencoded"
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=(timeout or self.timeout), context=_SSL) as r:
                raw = r.read()
                meta = {"ctype": r.headers.get("Content-Type", ""),
                        "set_cookie": list(r.headers.get_all("Set-Cookie") or [])}
        except urllib.error.HTTPError as e:
            raise BizError("HTTP %s %s" % (e.code, url))
        except Exception as e:
            raise BizError("NET %s %s" % (e, url))
        if binary:
            return raw, meta
        try:
            return raw.decode("utf-8", "replace"), meta
        except Exception:
            return raw.decode("gbk", "replace"), meta

    def _absorb_set_cookie(self, line):
        """把一行 Set-Cookie 收进当前会话（只取 name=value）"""
        part = (line or "").split(";")[0].strip()
        if "=" not in part:
            return
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or k.lower() in ("path", "expires", "max-age", "domain",
                                  "secure", "httponly", "samesite", "priority"):
            return
        with self._cookie_lock:
            ck = dict(self.cookies)
            if v.lower() in ("", "deleted", "expired"):
                ck.pop(k, None)
            else:
                ck[k] = v
            self.cookies = ck

    def _json_login_post(self, url, payload, timeout=None):
        """POST 登录系接口，返回 (dict, meta)；HTTP 层异常转成 BizError。"""
        text, meta = self._raw(url, payload, referer=LOGIN_REFERER, timeout=timeout)
        try:
            j = json.loads(text)
        except Exception:
            raise BizError("登录服务返回异常，请稍后重试")
        return j, meta

    def _apply_session(self, ck):
        """写入会话 cookie 并解析用户信息"""
        ck = {str(k): ("" if v is None else str(v)) for k, v in (ck or {}).items()}
        if not ck.get("u_id") or not ck.get("u_co_id"):
            raise LoginError("验证码/账号校验后未返回完整ERP登录身份，请使用下方官方网页登录并完成安全验证（不会误报登录成功）")
        new = dict(ck)
        new["u_lastLoginType"] = ck.get("u_lastLoginType") or self.cookies.get("u_lastLoginType") or "ap"
        with self._cookie_lock:
            self.cookies = new
        self._vs_cache = {}
        self._sku_cache = {}
        try:
            self.uid = urllib.parse.unquote(self.cookies.get("u_id", ""))
            self.coid = urllib.parse.unquote(self.cookies.get("u_co_id", ""))
            self.uname = urllib.parse.unquote(self.cookies.get("u_name", ""))
            self.coname = urllib.parse.unquote(self.cookies.get("u_co_name", ""))
        except Exception:
            pass
        return {"uid": self.uid, "coid": self.coid, "uname": self.uname, "coname": self.coname}

    # ---------------- 登录 ----------------
    def login(self, account, password, verify_code="", jd3="", vd144=""):
        """账号密码登录。服务端要验证码时抛 CaptchaError（前端据此拉图）。"""
        d = {"account": account, "password": password,
             "j_d_3": jd3 or "", "v_d_144": vd144 or "", "isApp": False}
        if verify_code:
            d["verifyCode"] = verify_code
        payload = {"data": d}
        try:
            j, meta = self._json_login_post(LOGIN_URL, payload)
        except BizError:
            raise LoginError("网络异常，请检查网络后重试")

        code = j.get("code")
        msg = (j.get("msg") or "").strip()

        if code == 0:
            data = j.get("data") or {}
            if data.get("hasRisk") or data.get("idaasHasRisk"):
                raise LoginError("该账号触发了安全验证（滑块 / 二次验证），"
                                 "请先在聚水潭网页端用浏览器登录一次，再回到本工具登录")
            ck = j.get("cookie") or {}
            if not ck:
                # 少数环境不走 body 的 cookie 对象，退回 Set-Cookie
                tmp = {}
                for line in meta.get("set_cookie") or []:
                    part = line.split(";")[0].strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        tmp[k.strip()] = v.strip()
                ck = tmp
            ck["u_lastLoginType"] = "ap"
            ck["u_isTPWS"] = str(data.get("isTPWS", 2))
            return self._apply_session(ck)

        if code == CODE_NEED_CAPTCHA:
            raise CaptchaError(msg or "请输入图形验证码后再登录")

        # 登录失败时服务端会给出**具体原因**（账号禁用 / 未开通 / 密码错误…）。
        # 以前统一吞成「账号或密码错误」，导致真实原因查不出来 —— 这里原样透出。
        raise LoginError(msg or "账号或密码错误（服务端未返回原因，code=%s）" % code)

    def validcode_image(self, account):
        """取图形验证码图片。返回 (bytes, content_type)。
        顺便吸收 ValidCode.aspx 种下的会话 cookie —— 校验验证码时要原样带回去。"""
        url = "%s?channel=login&id=%s&t=%d" % (
            VALIDCODE_URL, urllib.parse.quote((account or "").strip()), int(time.time() * 1000))
        try:
            raw, meta = self._raw(url, referer=LOGIN_REFERER, timeout=12, binary=True)
        except BizError as e:
            raise LoginError("图形验证码加载失败（%s）" % e)
        for line in meta.get("set_cookie") or []:
            self._absorb_set_cookie(line)
        if not raw or len(raw) < 64:
            raise LoginError("图形验证码为空，请重试")
        ctype = (meta.get("ctype") or "").split(";")[0].strip() or "image/gif"
        return raw, ctype

    def sms_send(self, mobile, suffix=""):
        """
        下发短信验证码。
        第一次不带 suffix 调用；若返回 need_suffix，说明该手机号命中了登录账号，
        需要用户补「手机号后两位」后再调一次（这正是官方的「验证已绑手机号」步骤）。
        返回 {'cid','prefix','need_suffix'}。

        注：官方前端这一步**不**带 j_d_3 / v_d_144（只有校验那一步才带），
        实测补上也不会改变结果，保持与原版一致。
        """
        mobile = (mobile or "").strip()
        if not mobile:
            raise LoginError("请输入手机号")
        d = {"mobile": mobile}
        if suffix:
            d["suffix"] = str(suffix).strip()
        try:
            j, meta = self._json_login_post(UC_SMS_SEND, {"data": d}, timeout=15)
            for line in meta.get("set_cookie") or []:
                self._absorb_set_cookie(line)
        except BizError:
            raise LoginError("短信验证码下发失败，请检查网络后重试")
        code = j.get("code")
        msg = (j.get("msg") or "").strip()
        if code != 0:
            # 301046 服务端统一说成「账号已禁用」，实测手机号没绑账号、号码不合法都是这个码
            if code == 301046:
                msg = "%s（多半是该手机号没有绑定聚水潭账号）" % msg
            raise LoginError(msg or "验证码发送失败")
        data = j.get("data") or {}
        return {
            "cid": _s(data.get("cId")),
            "prefix": _s(data.get("prefix")),
            "need_suffix": bool(data.get("isLoginId")),
        }

    def sms_login(self, mobile, code, cid, suffix="", jd3="", vd144=""):
        """短信验证码登录。cid 是下发那一步拿到的，必须原样带回。"""
        d = {"mobile": (mobile or "").strip(), "verificationCode": (code or "").strip(),
             "cid": _s(cid), "j_d_3": jd3 or "", "v_d_144": vd144 or ""}
        # Official mobile flow omits suffix; include it only for the account-tail flow.
        if _s(suffix).strip():
            d["suffix"] = _s(suffix).strip()
        try:
            j, meta = self._json_login_post(UC_SMS_LOGIN, {"data": d}, timeout=20)
        except BizError:
            raise LoginError("网络异常，请检查网络后重试")
        if j.get("code") != 0:
            # Do not blame the OTP when the service returned an unspecified rejection.
            # Only expose a bounded numeric status, never response bodies or credentials.
            status = j.get("code")
            status_text = str(status)
            status_text = status_text if re.fullmatch(r"-?\d{1,9}", status_text) else "未提供"
            message = (j.get("msg") or "").strip() or "验证码登录校验未通过"
            raise LoginError("%s（服务端错误码：%s）" % (message, status_text))
        data = j.get("data") or {}
        if not data.get("isBinding"):
            raise LoginError("该手机号尚未绑定聚水潭账号，请先用账号密码登录一次并完成绑定")
        if data.get("hasRisk") or data.get("idaasHasRisk"):
            raise LoginError("需要完成官方安全验证，请点击下方官方网页登录，在官方窗口内完成验证")
        # A previous account must not satisfy identity checks for a new SMS login.
        ck = {k: v for k, v in list(self.cookies.items())
              if k not in ("u_id", "u_co_id", "u_name", "u_co_name")}
        for line in meta.get("set_cookie") or []:
            part = line.split(";", 1)[0].strip()
            if "=" in part:
                k, v = part.split("=", 1)
                if v and v.lower() not in ("deleted", "expired"):
                    ck[k.strip()] = v.strip()
                else:
                    ck.pop(k.strip(), None)
        returned = j.get("cookie") or {}
        if isinstance(returned, dict):
            ck.update(returned)
        ck["u_lastLoginType"] = "vc"
        ck["u_isTPWS"] = str(data.get("isTPWS", 2))
        return self._apply_session(ck)

    def adopt_cookies(self, ck):
        """接纳从浏览器取回的会话 cookie（第三方扫码登录走的这条路）。"""
        info = self._apply_session(ck)
        if not info["uid"]:
            raise LoginError("浏览器 Cookie 里没有登录身份（u_id），可能还没有扫码成功")
        return info

    # ---------------- WebForms ----------------
    def _viewstate(self, page_url):
        """GET 页面取 __VIEWSTATE（结果按 URL 缓存 60 秒）"""
        now = time.time()
        hit = self._vs_cache.get(page_url)
        if hit and now - hit[2] < 60:
            return hit[0], hit[1]
        html = self._request(page_url, referer=page_url)
        if "login" in html[:4000].lower() and "__VIEWSTATE" not in html:
            raise BizError("登录态失效")
        vs = (re.search(r'name="__VIEWSTATE"[^>]*value="([^"]*)"', html) or [None, ""])[1]
        vsg = (re.search(r'name="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', html) or [None, ""])[1]
        if not vs:
            raise BizError("未取到页面状态（%s）" % page_url.split("?")[0])
        self._vs_cache[page_url] = (vs, vsg, now)
        return vs, vsg

    def callback(self, page_url, method, args, fields=None, page_size=None, call_control=None):
        """ACall1 回调。读方法不传 CallControl，写方法必须传 —— 后端按此校验。"""
        vs, vsg = self._viewstate(page_url)
        param = {"Method": method, "Args": args}
        if call_control is not None:
            param["CallControl"] = call_control
        form = {"__VIEWSTATE": vs, "__VIEWSTATEGENERATOR": vsg}
        if fields:
            form.update(fields)
        if page_size:
            form["_jt_page_size"] = str(page_size)
        form["__CALLBACKID"] = "JTable1"
        form["__CALLBACKPARAM"] = json.dumps(param, ensure_ascii=False)
        body = urllib.parse.urlencode(form, quote_via=urllib.parse.quote)
        url = "%s&ts___=%d&am___=%s" % (page_url, int(time.time() * 1000), method)
        text = self._request(url, body, referer=page_url,
                             extra_headers={"X-Requested-With": "XMLHttpRequest"})
        try:
            outer = json.loads(re.sub(r"^\d+\|", "", text, count=1))
        except Exception:
            if "login" in text[:500].lower() or len(text) < 200:
                raise BizError("登录态失效")
            raise BizError("响应解析失败")
        if not outer.get("IsSuccess"):
            msg = outer.get("ExceptionMessage") or outer.get("Message") or ""
            raise CallbackRejected(msg or "接口调用失败")
        rv = outer.get("ReturnValue")
        if isinstance(rv, str):
            try:
                return json.loads(rv)
            except Exception:
                return rv
        return rv

    # ---------------- 采购单 ----------------
    def _list_url(self, owner, auth):
        return ("%s%s?is_easy=&owner_co_id=%s&authorize_co_id=%s&_c=jst-epaas&epaas=true"
                % (WWW, PO_LIST_PATH, owner, auth))

    _LIST_FIELDS = {
        "remarkOpt": "", "labelsOpt": "", "priceOptSource": "", "queryType": "",
        "dateRange_temp_id": "po_date", "po_date": "", "status": "", "delivery_status": "",
        "goods_status": "", "group": "", "filter_sku_id_temp_id": "包含商品", "sku_id": "",
        "remark_select": "-1", "remark": "", "purchaser_name_v": "", "purchaser_name": "",
        "lc_id_v": "", "lc_id": "", "l_id": "", "payment_method": "", "item_type": "",
        "wmslabels": "", "nowmslabels": "", "is_1688_order": "", "confirm_name_v": "",
        "confirm_name": "", "contract_exist": "", "filter_lwh_id_binding_temp_id": "手动锁定",
        "lwh_id_binding": "", "manual_lwh_id_binding": "",
    }

    def search_po(self, keyword, owner, auth, id_type="po_id", page_size=25):
        """按单号查采购单列表。返回 (rows, dp)。"""
        page_url = self._list_url(owner, auth)
        filters = [
            {"k": "po_id", "v": str(keyword), "c": "@="},
            {"k": "poid_type", "v": id_type, "c": "@="},
            {"k": "remark_select", "v": "-1", "c": "@="},
            {"k": "contract_exist", "v": "", "c": "@="},
            {"k": "show_type", "v": "purchase", "c": "="},
        ]
        fields = dict(self._LIST_FIELDS)
        fields["owner_co_id"] = str(owner)
        fields["authorize_co_id"] = str(auth)
        res = self.callback(page_url, "LoadDataToJSON",
                            ["1", json.dumps(filters, ensure_ascii=False), "{}"],
                            fields=fields, page_size=page_size)
        res = res or {}
        return res.get("datas") or [], res.get("dp") or {}

    def fetch_detail(self, po_id, owner, auth):
        """取采购单明细（服务端渲染，数据内嵌 #_jt_data）。"""
        url = ("%s%s?po_id=%s&p_co_id=%s&p_owner_co_id=%s&all_data=true&archive=false"
               "&owner_co_id=%s&authorize_co_id=%s"
               % (WWW, PO_DETAIL_PATH, po_id, self.coid or owner, owner, owner, auth))
        html = self._request(url, referer=url)
        return self._parse_jt_data(html)

    @staticmethod
    def _parse_jt_data(html):
        m = re.search(r'<div id="_jt_data"[^>]*>([\s\S]*?)</div>', html, re.I)
        if not m:
            if "parent.location" in html and "login" in html[:2000].lower():
                raise BizError("登录态失效")
            raise BizError("明细页未返回数据")
        raw = m.group(1).strip()
        if not raw:
            return {"dp": None, "datas": []}
        try:
            return json.loads(raw)
        except Exception:
            try:
                return json.loads(raw.replace("&quot;", '"').replace("&amp;", "&"))
            except Exception:
                raise BizError("明细数据解析失败")


    # ---------------- 商品资料（新架构 JSON API） ----------------
    def json_api(self, host, path, data=None, page=None, qs="", timeout=None):
        """
        调用 addr.erp321.com 系现代接口。
        host: "api"（路径带 /erp，需 isOtherStore） 或 "apiweb"（路径不带 /erp）
        """
        body = {"ip": "", "uid": self.uid, "coid": self.coid}
        if page:
            body["page"] = page
        body["data"] = data
        if host == "api":
            body["isOtherStore"] = False
        url = API_ROOTS[host] + path + (("?" + qs) if qs else "")
        text = self._request(url, json.dumps(body, ensure_ascii=False),
                             referer="https://src.erp321.com/", is_json=True, timeout=timeout)
        try:
            j = json.loads(text)
        except Exception:
            raise BizError("商品资料接口返回异常")
        if j.get("code") != 0:
            raise BizError("SKU_API code=%s %s" % (j.get("code"), (j.get("msg") or "").strip()))
        return j.get("data")

    def clone_for_catalog(self):
        """后台只读客户端持有登录快照，不与扫码/登录请求共享 cookie 字典。"""
        clone = JstClient(timeout=15)
        clone.cookies = dict(self.cookies)
        clone.uid, clone.coid = self.uid, self.coid
        clone.uname, clone.coname = self.uname, self.coname
        return clone

    def catalog_page(self, owner, auth, page, size=200):
        from urllib.parse import urlencode
        fields = list(dict.fromkeys(SKU_QUERY_FLDS + ["_sku_codes", "owner_co_id"]))
        return self.json_api("apiweb", "ItemApi/ItemSku/GetPageListV2",
            data={"sku_type": 1, "queryFlds": fields, "orderBy": ""},
            page={"currentPage": page, "pageSize": size, "pageAction": 1},
            qs=urlencode({"owner_co_id": owner, "authorize_co_id": auth}), timeout=15)

    def item_detail(self, code, owner="", auth="", timeout=6):
        """
        ItemApi/ItemSku/GetEditOrCreateItemSkuInfoV2 —— 按商品编码取完整档案。
        注意：data 必须是字符串本身（传对象会报 10015）；
              货主/仓储过滤只能写在 URL query 上，写在 data 里会被静默忽略。
        """
        qs = "owner_co_id=%s&authorize_co_id=%s" % (owner or self.coid, auth or self.coid)
        try:
            d = self.json_api("api", "ItemApi/ItemSku/GetEditOrCreateItemSkuInfoV2",
                              data=code, qs=qs, timeout=timeout)
        except BizError:
            raise ItemLookupError("detail unavailable")
        b = (d or {}).get("basicItemSkuInfo") or {}
        return self._to_item({
            "sku_id": b.get("sku_id"), "i_id": b.get("i_id"), "name": b.get("name"),
            "sku_code": b.get("sku_code"), "spec": b.get("properties_value"),
            "enabled": b.get("enabled"), "pic": b.get("pic"),
        }, "detail")

    def item_search(self, field, value, owner="", limit=200, timeout=6, auth=""):
        """GetPageListV2 —— 按商品编码 / 条码 检索；货主走 URL query。"""
        qs = urllib.parse.urlencode({"owner_co_id": owner or self.coid, "authorize_co_id": auth or self.coid})
        try:
            rows = self.json_api(
                "apiweb", "ItemApi/ItemSku/GetPageListV2",
                data={"sku_type": 1, "queryFlds": SKU_QUERY_FLDS, "orderBy": "", field: value},
                page={"currentPage": 1, "pageSize": limit, "pageAction": 1},
                qs=qs, timeout=timeout)
        except BizError:
            raise ItemLookupError("list unavailable")
        out = []
        for r in (rows if isinstance(rows, list) else []):
            if not isinstance(r, dict):
                continue
            if not _eq(r.get(field), value):
                continue                       # 过滤条件被后端忽略时，避免误取首页任意行
            it = self._to_item(r, "list")
            if it:
                out.append(it)
        return out

    @staticmethod
    def _to_item(r, src):
        sid = _s(r.get("sku_id"))
        if not sid:
            return None
        return {"sku_id": sid, "i_id": _s(r.get("i_id")), "name": _s(r.get("name")),
                "sku_code": _s(r.get("sku_code")), "spec": _s(r.get("spec") or r.get("properties_value")),
                "enabled": _s(r.get("enabled")), "pic": _s(r.get("pic")), "src": src,
                "aux_codes": ",".join(item_auxiliary(r))}

    def lookup_item(self, code, owner="", auth=""):
        """Explicit online fallback; collect distinct SKU candidates, never take rows[0] on a collision."""
        code = (code or "").strip()
        if not code: return None
        key = (str(self.coid), str(auth or self.coid), code.lower(), str(owner or ""))
        hit = self._sku_cache.get(key)
        if hit and time.time() - hit[0] < 600:
            rows = hit[1] if isinstance(hit[1], list) else ([hit[1]] if hit[1] else [])
        else:
            result, err = {}, None
            for field in ("sku_code", "sku_id"):
                try:
                    for it in self.item_search(field, code, owner, auth=auth): result[it["sku_id"]] = it
                except Exception as e: err = e
            try:
                it = self.item_detail(code, owner, auth)
                if it: result[it["sku_id"]] = it
            except Exception as e: err = e
            if not result and err: raise ItemLookupError(str(err))
            rows = list(result.values())
            if len(self._sku_cache) > 800: self._sku_cache.clear()
            self._sku_cache[key] = (time.time(), rows)
        if len(rows) > 1: raise ItemAmbiguousError(rows)
        return rows[0] if rows else None

    # ---------------- 货主 / 分仓 ----------------
    def partners(self, timeout=10):
        """可代销的货主清单（含自有）。失败返回 []。

        实测返回 `[{consignor_co_id, consignor_co_name}]`；也兼容 {id: name} 字典形态。
        名称缺失时退回 id（界面会显示成数字），所以字段名要写全。
        """
        try:
            rows = self.json_api("api", "ItemApi/PartnerRelation/GetTPWPartnerOwnersV2",
                                 data={"isExcludeSelf": False, "isExcludeOwner": False},
                                 timeout=timeout)
        except BizError as e:
            _diag("partners 接口失败: %s" % e)
            return []
        out, seen = [], set()

        def push(oid, name):
            oid = _s(oid)
            if not oid or oid in seen:
                return
            seen.add(oid)
            out.append({"id": oid, "name": _s(name) or oid})

        if isinstance(rows, dict):
            for k, v in rows.items():
                if isinstance(v, dict):
                    push(_owner_id(v, k), _owner_name(v))
                else:
                    push(k, v)
        elif isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict):
                    push(_owner_id(r), _owner_name(r))
        if out and all(x["name"] == x["id"] for x in out):
            _diag("partners 未解析出名称，原始返回：%s"
                  % json.dumps(rows, ensure_ascii=False, default=str)[:800])
        return out

    def wms_raw(self, timeout=10):
        """分仓接口的**原始 data**（不做任何解析），仅供 /api/debug/wms-raw 取证。"""
        return self.json_api("api", "ItemApi/PartnerRelation/GetWmsCoIds",
                             data="AddAllWms", timeout=timeout)

    def owners_raw(self, timeout=10):
        """货主接口的**原始 data**（不做任何解析），仅供诊断取证。"""
        return self.json_api("api", "ItemApi/PartnerRelation/GetTPWPartnerOwnersV2",
                             data={"isExcludeSelf": False, "isExcludeOwner": False},
                             timeout=timeout)

    def wms_list(self, timeout=10):
        """仓储（分仓）清单。注意 data 是字符串常量。

        **实测真实返回**（2026-09-30 抓取，`GetWmsCoIds`，data="AddAllWms"）：
            [{"wms_co_id": 2147483647, "wms_co_name": "--所有仓储--",
              "istpwPartner": true, "isWmsMain": true},
             {"wms_co_id": 15179484, "wms_co_name": "东莞至臻", ...}, ...]
        字段名是 **wms_co_id / wms_co_name**（不是 co_id / co_name）。
        之前名称候选漏了 `wms_co_name`，id 却认得 `wms_co_id`，
        结果就是「id 取到了、名称取不到 → 回退成 id」，下拉框显示一串数字。

        另外兼容对象数组 / 扁平字典 {id: name} / 二维数组 [[id, name]] 三种形态，
        任何一种都必须带上名称，否则界面只能显示数字。
        """
        try:
            rows = self.json_api("api", "ItemApi/PartnerRelation/GetWmsCoIds",
                                 data="AddAllWms", timeout=timeout)
        except BizError as e:
            _diag("wms_list 接口失败: %s" % e)
            return []
        out, seen = [], set()

        def push(i, n):
            i = _s(i)
            n = _s(n)
            if not i or i in seen:
                return
            seen.add(i)
            out.append({"id": i, "name": n or i})

        if isinstance(rows, dict):
            # 扁平字典形态：{id: name}
            for k, v in rows.items():
                if isinstance(v, dict):
                    push(_wms_id(v, k), _wms_name(v))
                else:
                    push(k, v)
        elif isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict):
                    push(_wms_id(r), _wms_name(r))
                elif isinstance(r, (list, tuple)) and len(r) >= 2:
                    push(r[0], r[1])
        # 取证：全部分仓名都等于 id，说明接口没给出名称字段。把原始结构打到 stderr，
        # 方便从启动控制台一眼看出真实字段名（否则只能看到界面上一串数字，无从下手）。
        if out and all(x["name"] == x["id"] for x in out):
            _diag("wms_list 未解析出名称，原始返回：%s"
                  % json.dumps(rows, ensure_ascii=False, default=str)[:800])
        return out


# ---------------------------------------------------------------- 高层封装

def normalize_po_row(row):
    return {
        "po_id":         _s(row.get("po_id")),
        "outer_po_id":   _s(row.get("temp_outer_po_id") or row.get("outer_po_id")),
        "status":        _s(row.get("status")),
        "po_date":       _s(row.get("po_date")),
        "supplier":      _s(row.get("seller") or row.get("seller_name")),
        "wh_name":       _s(row.get("wms_co_name")),
        "owner_co_id":   _s(row.get("owner_co_id")),
        "wms_co_id":     _s(row.get("wms_co_id") or row.get("co_id")),
        "receive_status":_s(row.get("receive_status")),
        "qty_total":     _num(row.get("qty")),
    }

def normalize_po_item(row):
    return {
        "poi_id":      _s(row.get("poi_id")),
        "sku_id":      _s(row.get("sku_id")),
        "i_id":        _s(row.get("i_id")),
        "sku_code":    _s(row.get("sku_code")),                 # 条码 / 69码
        "aux_codes":   ",".join(item_auxiliary(row)),
        "name":        _s(row.get("name")),
        "spec":        _s(row.get("properties_value")),
        "qty":         _num(row.get("qty")),
        "order_qty":   _num(row.get("orderQty")),
        "in_qty":      _num(row.get("in_qty")),
        # Missing ERP counters must not be silently presented as a verified zero.
        "in_qty_known": row.get("in_qty") not in (None, "") and 0 <= _num(row.get("in_qty"), -1) < float("inf"),
        "qty_known": row.get("qty") not in (None, "") and 0 <= _num(row.get("qty"), -1) < float("inf"),
    }

# 匹配来源的中文名（多候选弹窗里要标明每个候选是怎么命中的）
VIA_LABELS = {
    "barcode": "条码", "aux": "辅助码", "sku_id": "商品编码",
    "i_id": "款式编码", "api": "商品资料接口",
}


def match_item(items, code):
    """
    在采购单明细里定位商品。前三级（条码 / 辅助条码 / 商品编码）**合并收集**：
    同一个码可能既是 A 商品的条码、又是 B 商品的辅助码（或商品编码）——
    例如 SFJ32 与 OL146 互挂的情况。只要命中了**不同**商品，一律返回 multi
    交给人工选择，绝不按级别优先级静默取先命中的那个（收错货比多弹一次窗严重得多）。

    单个商品多级命中（如条码与辅助码同值）不算冲突，via 取其中优先级最高的一级。
    前三级都没命中时才按款式编码（i_id）兜底；同款式多规格仍会 multi。

    返回：{'mode':'unique','item':x,'via':..} / {'mode':'multi','items':[..],'via':..} / {'mode':'none'}
    multi 时每个候选带 match_via（如「条码、辅助码」）说明它是怎么命中的。
    """
    code = (code or "").strip()
    if not code:
        return {"mode": "none"}

    buckets, order = {}, []

    def put(it, via):
        k = str(it.get("poi_id") or it.get("sku_id"))
        b = buckets.get(k)
        if b is None:
            buckets[k] = {"item": it, "vias": [via]}
            order.append(k)
        elif via not in b["vias"]:
            b["vias"].append(via)

    # 1) 条码 / 69码  2) 辅助条码（一商品可挂多个）  3) 商品编码
    for it in items:
        if _eq(it.get("sku_code"), code):
            put(it, "barcode")
    for it in items:
        for c in item_auxiliary(it):
            if c and _eq(c, code):
                put(it, "aux")
                break
    for it in items:
        if _eq(it.get("sku_id"), code):
            put(it, "sku_id")

    if buckets:
        if len(buckets) == 1:
            b = buckets[order[0]]
            return {"mode": "unique", "item": b["item"], "via": b["vias"][0]}
        items_out = []
        for k in order:
            b = buckets[k]
            it = dict(b["item"])
            it["match_via"] = "、".join(VIA_LABELS.get(v, v) for v in b["vias"])
            items_out.append(it)
        return {"mode": "multi", "items": items_out, "via": "multi"}

    # 4) 款式编码兜底
    hits = [it for it in items if _eq(it.get("i_id"), code)]
    if not hits:
        return {"mode": "none"}
    uniq = _dedup(hits)
    if len(uniq) == 1:
        return {"mode": "unique", "item": uniq[0], "via": "i_id"}
    return {"mode": "multi", "items": uniq, "via": "i_id"}


def match_item_by_any(items, keys):
    """
    用商品资料接口解析出的多个候选值（商品编码 / 条码 / 款式编码）
    依次回查采购单明细，命中即返回（via 标记为 'api'）。
    """
    for field, val in keys:
        val = (val or "").strip()
        if not val:
            continue
        r = match_item(items, val)
        if r["mode"] != "none":
            r["via"] = "api"
            r["src_field"] = field
            return r
    return {"mode": "none"}


def _dedup(items):
    """同一商品可能多行命中，按 poi_id / sku_id 去重"""
    seen, uniq = set(), []
    for it in items:
        k = it.get("poi_id") or it.get("sku_id")
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq

# -*- coding: utf-8 -*-
"""
轻量扫码入库客户端 —— 本地服务端。

本地服务使用 http.server + sqlite3；PDA HTTPS 证书使用 cryptography。
前端为原生 HTML/JS，浏览器用 Edge --app 无边框窗口打开，跨进程零冗余。
"""
import hashlib
import html
import logging
from logging.handlers import RotatingFileHandler
import traceback
import ipaddress
import sqlite3
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _frozen():
    """是否运行在 PyInstaller 打包出的 exe 里。"""
    return getattr(sys, "frozen", False)


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if _frozen():
    # 只读资源（web 前端）被 PyInstaller 解到临时目录：_MEIPASS。
    # 可写数据（data/、导出/、扫码登录配置）放 exe 同目录，方便备份、重装不丢。
    BUNDLE = getattr(sys, "_MEIPASS", HERE)
    WEB = os.path.join(BUNDLE, "web")
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    ROOT = EXE_DIR
else:
    WEB = os.path.join(HERE, "web")
# 环境变量优先，其次 exe 同目录（打包）/ 项目根（源码运行）
DATA = os.environ.get("JST_DB_DIR") or os.path.join(ROOT, "data")
EXPORT_DIR = os.path.join(ROOT, "导出")

_LOG = logging.getLogger("inbound.lifecycle")
_LOG.setLevel(logging.INFO)
_LOG.propagate = False
_LOG.addHandler(logging.NullHandler())
if __name__ == "__main__":
    try:
        os.makedirs(os.path.join(DATA, "logs"), exist_ok=True)
        _handler = RotatingFileHandler(os.path.join(DATA, "logs", "service.log"), maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
        _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _LOG.addHandler(_handler)
    except OSError:
        # A diagnostic log failure must not kill a working receiving service.
        pass

def _exception_log(kind, typ, tb):
    # No exception values or local variables: they can contain ERP response secrets.
    _LOG.error("%s: %s\n%s", kind, typ.__name__, "".join(traceback.format_tb(tb)))

_previous_excepthook = sys.excepthook
def _uncaught(typ, value, tb):
    _exception_log("主线程未处理异常", typ, tb)
    _previous_excepthook(typ, value, tb)
sys.excepthook = _uncaught
if hasattr(threading, "excepthook"):
    _previous_thread_hook = threading.excepthook
    def _thread_uncaught(args):
        _exception_log("后台线程未处理异常", args.exc_type, args.exc_traceback)
        _previous_thread_hook(args)
    threading.excepthook = _thread_uncaught
_LOG.info("进程启动 pid=%s；正在初始化本地数据", os.getpid())

sys.path.insert(0, HERE)
from jst import (JstClient, BizError, LoginError, CaptchaError,  # noqa: E402
                 normalize_po_row, normalize_po_item, match_item, match_item_by_any)
from store import Store, now_str, validate_qty, plan_by_key  # noqa: E402
from catalog import CatalogDB, CatalogSync, scope_key
from login_memory import LoginMemory
from inbound import InboundService, InboundError  # noqa: E402
from unplanned import UnplannedService
from joint_receipt import JointService
from codes import enabled_scan_candidates
from purchase_watch import PurchaseWatch
from inventory import InventoryService
from backup import BackupManager, VERSION
from operations import Operations
from pda import PdaHost
from download import InstallerHost
from remote_access import RemoteHost
from updater import UpdateHost, inbound_busy_reason
import xlsx  # noqa: E402
import edgecookie  # noqa: E402
import cdp  # noqa: E402
import qrview
from report_clipboard import ReportClipboard, MAX_REQUEST_BYTES

# Local packaging diagnostic: temporary certificate only, no DB or ERP requests.
if __name__ == "__main__" and "--self-test" in sys.argv:
    import tempfile
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory(prefix="jst-tls-check-") as d:
        diagnostic = PdaHost(SimpleNamespace(DATA=d))
        diagnostic.certificate("127.0.0.1")
        import qrcode
        qr = qrcode.QRCode(); qr.add_data("packaging-check"); qr.make(fit=True)
        print(json.dumps({"ok": True, "version": VERSION, "tls_certificate": bool(diagnostic.pin), "qr": bool(qr.get_matrix())}))
    sys.exit(0)

# Acquire before backups/databases/workers, not only at HTTP bind time.
_SERVICE_LEASE = None
if __name__ == "__main__":
    from service_runtime import ServiceLease
    _SERVICE_LEASE = ServiceLease(DATA)
    if not _SERVICE_LEASE.owned:
        existing_port = _SERVICE_LEASE.existing_port()
        _LOG.info("检测到同一数据目录服务已运行；复用现有页面 port=%s", existing_port)
        if existing_port and os.environ.get("JST_NO_BROWSER") != "1":
            webbrowser.open("http://127.0.0.1:%d/" % existing_port)
        _SERVICE_LEASE.close()
        sys.exit(0)

REPORT_CLIPBOARD = ReportClipboard(os.path.join(DATA, "report-clipboard"))

PORT = int(os.environ.get("JST_PORT", "8765"))
PERM_MSG = "当前账号无此操作权限"
ID_TYPES = ["po_id", "outer_po_id", "source_o_id", "plat_so_id"]
ID_TYPE_LABELS = {"po_id": "采购单号", "outer_po_id": "外部单号",
                  "source_o_id": "来源单号", "plat_so_id": "线上采购单号"}

state_lock = threading.RLock()
BACKUPS = BackupManager(DATA, state_lock)
BACKUPS.before_upgrade()
STORE = Store(os.path.join(DATA, "jst.db"))
# 自动更新：检查 / 下载全部在后台线程；"入库任务生成中"只读检查三个收货库，不取 state_lock。
UPDATER = UpdateHost(DATA, busy=lambda: inbound_busy_reason(
    [os.path.join(DATA, n) for n in ("jst.db", "unplanned.db", "joint_receipts.db")]))
JST = JstClient()
LOGIN_MEMORY = LoginMemory(DATA, STORE)
QR_LOGIN_OPTIONS = {}
INBOUND = InboundService(STORE, JST)
INVENTORY = InventoryService(STORE, JST)
OPERATIONS = Operations(STORE, JST, INVENTORY)
CATALOG = CatalogDB(os.path.join(DATA, "catalog.db"))
LAST_HEARTBEAT = 0.0      # 0 = 从未收到过心跳
_started = time.time()

# 短信验证码：下发与校验必须成对，cid 由下发那一步给出，存内存不外传
SMS_STATE = {"cid": "", "mobile": ""}

# 扫码登录：用**独立**的 Edge 配置目录。
# 主 Profile 被常驻 Edge 独占锁住，Cookie 库读不出来；独立目录由本程序起、由本程序关。
# 取会话不走 SQLite：cookie 落盘是异步批提交（约 30s），且会话 cookie 根本不落盘，
# 所以开 --remote-debugging-port 用 CDP 从活着的浏览器里直接拿（cdp.py）。
QR_LOCK = threading.RLock()
QR_PROFILE = os.path.join(DATA, "edge-login")
QR_PROC = None
QR_CDP_PORT = 0
QR_ACTIVE_PROFILE = ""
QR_KIND = "official"
QR_STARTED = 0.0
QR_URL = "https://www.erp321.com/login.aspx"
QR_HOSTS = ("erp321.com", "jushuitan.com")


def note_beat():
    global LAST_HEARTBEAT
    LAST_HEARTBEAT = time.monotonic()


def log(msg):
    _LOG.info(msg)
    try:
        sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), msg))
        sys.stderr.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 业务

def _ctx_candidates(pick_owner="", pick_auth=""):
    """货主上下文候选：用户在界面显式选的 → 上次成功的 → 用户配置的 → 通用兜底"""
    out, seen = [], set()

    def add(o, a):
        k = (str(o), str(a))
        if k not in seen and k[0] not in ("", "0"):
            seen.add(k)
            out.append(k)

    if pick_owner:
        add(pick_owner, pick_auth or JST.coid)
    pref_o = STORE.get_kv("ctx_owner")
    pref_a = STORE.get_kv("ctx_auth")
    if pref_o:
        add(pref_o, pref_a or JST.coid)
    for o in re.split(r"[,\s]+", (STORE.get_kv("owner_ids", "") or "").strip()):
        if o:
            add(o, JST.coid)
    add("2147483647", JST.coid)
    add(JST.coid, JST.coid)
    return out


def _remember_ctx(o, a):
    STORE.set_many({"ctx_owner": str(o), "ctx_auth": str(a)})


def _find_purchase_in_scope(no, owner, auth):
    """Read-only exact lookup for an existing receipt. Never rotate its company scope."""
    owner, auth = str(owner or '').strip(), str(auth or '').strip()
    if not JST.uid or not JST.coid:
        raise BizError('采购单查询需要登录，请重新登录聚水潭后再核对')
    if owner in ('', '0', '2147483647') or auth in ('', '0', '2147483647'):
        raise BizError('当前收货批次缺少具体货主 / 分仓范围，请核对批次，不能自动跨范围关联')
    errors = []
    for kind in ('po_id', 'outer_po_id'):
        try:
            raw, _ = JST.search_po(no, owner, auth, kind)
            if not isinstance(raw, list) or any(not isinstance(r, dict) for r in raw):
                raise BizError('采购列表响应格式异常')
        except (BizError, OSError, ValueError) as exc:
            errors.append(str(exc) or type(exc).__name__)
            continue
        if len(raw) >= 25:
            raise BizError('采购单查询结果过多，无法确认唯一单据；请使用完整采购单号核对')
        matches = {}
        for source in raw:
            row = normalize_po_row(source)
            if not row['po_id'] or no not in (row['po_id'], row['outer_po_id']):
                continue
            if ((row['owner_co_id'] and row['owner_co_id'] != owner)
                    or (row['wms_co_id'] and row['wms_co_id'] != auth)):
                raise BizError('采购单返回的货主 / 分仓与当前收货批次范围不一致，未关联')
            matches[row['po_id']] = row
        if len(matches) > 1:
            raise BizError('该外部单号对应多张采购单，请输入具体采购单号后再核对')
        if matches:
            return next(iter(matches.values())), dict(owner=owner, auth=auth, id_type=kind)
    if errors:
        reason = '；'.join(dict.fromkeys(errors))[:200]
        raise BizError('采购单查询未完成：'+reason+'。请检查登录状态、采购查询权限及网络后重试；未更换原关联。')
    raise BizError('当前批次范围（货主 '+owner+' / 分仓 '+auth+'）未找到精确匹配的采购单。'
                   '支持采购单号或外部单号；请确认补开的采购单已保存，且属于该货主 / 分仓。')


def find_purchase_order(no, pick_owner="", pick_auth="", prefer="", *, strict_scope=False):
    """按单号查采购单；优先用户所选货主/分仓，再自动轮换其余上下文与单号类型。

    prefer：界面显式选定的单号类型（po_id/outer_po_id/...）。
    传「auto」或不传时自动轮换全部四种类型——即「自动识别」：
    采购单号和外部单号不用人为区分，扫到哪个算哪个，命中后在结果里带回实际命中的类型。
    """
    no = (no or "").strip()
    if not no:
        raise BizError("单号为空")
    if strict_scope:
        return _find_purchase_in_scope(no, pick_owner, pick_auth)
    preferred = prefer if prefer in ID_TYPES else STORE.get_kv("default_id_type", "auto")
    if preferred not in ID_TYPES:
        preferred = ""
    order = ([preferred] if preferred else []) + [t for t in ID_TYPES if t != preferred]
    last_err = None
    for owner, auth in _ctx_candidates(pick_owner, pick_auth):
        for t in order:
            try:
                rows, _ = JST.search_po(no, owner, auth, t)
            except BizError as e:
                last_err = e
                continue
            if not rows:
                continue
            rows = [normalize_po_row(r) for r in rows if str(r.get("po_id", "")).strip()]
            if not rows:
                continue
            row = rows[0] if len(rows) == 1 else next(
                (r for r in rows if str(r["po_id"]) == no or r["outer_po_id"] == no), rows[0])
            _remember_ctx(row.get("owner_co_id") or owner, row.get("wms_co_id") or auth)
            return row, {"owner": row.get("owner_co_id") or owner,
                         "auth": row.get("wms_co_id") or auth, "id_type": t}
    log("find_purchase_order miss: %s (%s)" % (no, last_err))
    raise BizError("PO_NOT_FOUND")


def fetch_items(row, ctx):
    raw = JST.fetch_detail(row["po_id"], ctx["owner"], ctx["auth"])
    items = [normalize_po_item(r) for r in (raw.get("datas") or [])]
    items = [it for it in items if it["sku_id"] or it["name"]]
    if not items:
        raise BizError("采购单无明细")
    return items


def refresh_purchase_session(sid):
    """Explicit read-only refresh, using the session's original exact company scope.

    Never activate a session, discard receipts, or substitute another PO on failure.
    Caller holds state_lock. Background two-second sync never calls this function.
    """
    s = STORE.get_session(sid)
    if not s or not JST.uid:
        raise BizError("请先登录聚水潭并选择收货单，旧数据未更新")
    owner, auth = s.get("owner_co_id") or "", s.get("auth_co_id") or ""
    if not owner or not auth:
        raise BizError("旧收货单缺少货主或仓库公司，请在电脑重新查询采购单")
    raw, _ = JST.search_po(s["po_id"], owner, auth, "po_id")
    exact = [r for r in raw if str(r.get("po_id", "")).strip() == str(s["po_id"])]
    if len(exact) != 1:
        raise BizError("未找到唯一的原采购单，旧数据未更新，请核对权限和采购单状态")
    row = normalize_po_row(exact[0])
    if ((row.get("owner_co_id") and row["owner_co_id"] != owner)
            or (row.get("wms_co_id") and row["wms_co_id"] != auth)):
        raise BizError("采购单公司上下文不一致，已停止刷新")
    items = fetch_items(row, {"owner": owner, "auth": auth})
    # Both remote calls completed before changing the local snapshot.
    with STORE.db:
        STORE.db.execute(
            "UPDATE session SET supplier=?,wh_name=?,status=?,po_date=?,outer_po_id=?,items_json=? WHERE id=?",
            (row["supplier"], row["wh_name"], row["status"], row["po_date"],
             row["outer_po_id"], json.dumps(items, ensure_ascii=False), sid))
        STORE.db.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)",
            ("po_refreshed_at:" + str(sid), time.strftime("%Y-%m-%d %H:%M:%S")))
    return STORE.get_session(sid)


PARTNERS_TTL = 12 * 3600
# 清单缓存结构版本号。分仓/货主字段解析规则一变，就把它 +1，让所有旧缓存立即失效。
# 否则用户会遇到「代码明明修好了，界面还是老样子」——因为 12 小时内的旧缓存还在供数据
# （分仓名一度全是数字，就是这么"刷不掉"的）。v2：补 wms_co_name 字段。
PARTNERS_CACHE_VER = 2


def _partners_cache_ok(d):
    """缓存是否可用：版本号一致，且分仓/货主里没有「名称=id」的退化数据。"""
    try:
        if not isinstance(d, dict) or d.get("ver") != PARTNERS_CACHE_VER:
            return False
        for key in ("wms", "owners"):
            for x in (d.get(key) or []):
                if str(x.get("name")) == str(x.get("id")):
                    return False        # 有没解析出名称的条目 → 视为坏缓存，重取
        return bool(d.get("wms") or d.get("owners"))
    except Exception:
        return False


# 每次登录/切换账号清缓存时 +1。慢请求在登录前发出、登录后才返回时，
# 用它判断结果已过期，不能把旧账号的货主/分仓清单写回缓存。
PARTNERS_GEN = 0


def invalidate_partners():
    global PARTNERS_GEN
    PARTNERS_GEN += 1
    STORE.set_many({"partners_json": "", "partners_ts": "0"})


def load_partners(refresh=False):
    """货主与分仓清单。接口结果缓存 12 小时；取不到时允许返回空列表，不影响收货主流程。

    注意 refresh=True（用户点了「刷新清单」）时必须**信任本次结果**：
    以前刷新失败会静默回落到旧缓存，导致用户反复点刷新看到的还是那份坏数据
    （分仓名全是数字就是这么"刷不掉"的）。现在刷新场景下成功就用新的，
    失败则带 err 标记返回，让界面明确提示刷新失败，而不是拿旧数据糊弄。
    """
    cached = STORE.get_kv("partners_json", "")
    empty = {"owners": [], "wms": [], "err": True}
    if not JST.uid and not cached:
        return empty                # 未登录不打扰接口，避免白等一个超时
    ts = float(STORE.get_kv("partners_ts", "0") or 0)
    if cached and not refresh and time.time() - ts < PARTNERS_TTL:
        try:
            d = json.loads(cached)
            if _partners_cache_ok(d):
                return d
            log("partners 缓存过期(版本/字段升级)，重新拉取")
        except Exception:
            pass
    gen, ident = PARTNERS_GEN, (JST.uid, JST.coid)
    try:
        data = {"owners": JST.partners(), "wms": JST.wms_list(), "ts": now_str(),
                "ver": PARTNERS_CACHE_VER}
        if not data["owners"] and not data["wms"]:
            raise BizError("empty partner list")
        if gen != PARTNERS_GEN or ident != (JST.uid, JST.coid):
            # 拉取期间登录身份变了：这份清单属于旧账号，既不缓存也不返回
            log("partners 拉取期间账号已切换，丢弃旧结果")
            return {"owners": [], "wms": [], "err": True, "err_msg": "登录账号已切换，请重新读取清单"}
        STORE.set_many({"partners_json": json.dumps(data, ensure_ascii=False),
                        "partners_ts": str(time.time())})
        return data
    except Exception as e:
        log("partners fail: %s" % e)
        if refresh:
            # 用户主动刷新却失败：不要用旧缓存掩盖问题，如实报错并附上原因
            return {"owners": [], "wms": [], "err": True, "err_msg": str(e)[:120]}
        if cached:
            try:
                d = json.loads(cached)
                if _partners_cache_ok(d):
                    return d
            except Exception:
                pass
        return {"owners": [], "wms": [], "err": True, "err_msg": str(e)[:120]}


def catalog_context(owner="", auth=""):
    company = str(JST.coid or STORE.get_kv("catalog_company", ""))
    return scope_key(company, owner, auth)


def catalog_provider():
    # 只在复制身份和本地配置时短暂拿锁；分页下载、写独立库不占收货请求的锁。
    with state_lock:
        if not JST.uid or not JST.coid or STORE.get_kv("catalog_auto", "1") == "0":
            return None
        client = JST.clone_for_catalog()
        identity = (JST.uid, JST.coid)
        if STORE.get_kv("catalog_company", "") != str(JST.coid):
            STORE.set_kv("catalog_company", str(JST.coid))
        targets = [(str(s.get("owner_co_id") or JST.coid), str(s.get("auth_co_id") or JST.coid),
                    "收货货主 " + str(s.get("owner_co_id") or JST.coid)) for s in STORE.open_sessions()]
        targets.append((STORE.get_kv("pick_owner", "") or str(JST.coid),
                        STORE.get_kv("pick_auth", "") or str(JST.coid), "当前所选货主"))
        for service in (globals().get('UNPLANNED'), globals().get('JOINT')):
            instance = getattr(service, 'instance', None)
            if instance is not None:
                targets += [(str(s.get('owner_co_id') or JST.coid),
                             str(s.get('auth_co_id') or JST.coid), '收货商品库')
                            for s in instance.store.open_sessions()]
                if isinstance(instance, UnplannedService):
                    targets += [(scope['owner'], scope['auth'], '独立扫码商品库')
                                for s in instance.store.open_sessions()
                                for scope in [instance.catalog_scope(s['id'])]]
        targets += [(o, str(JST.coid), "配置货主 " + o) for o in
                    re.split(r"[,，\s]+", STORE.get_kv("owner_ids", "")) if o]
    return client, targets, lambda: (JST.uid, JST.coid) == identity


CATALOG_SYNC = CatalogSync(CATALOG, catalog_provider)


def catalog_scan_match(base, candidates):
    # 共用条码/款式码不得取第一条。仅按已解析出的精确 SKU 回到原采购明细。
    sku_ids = {str(it.get("sku_id") or "").strip().lower() for it in candidates}
    matched = [it for it in base if str(it.get("sku_id") or "").strip().lower() in sku_ids]
    if len(matched) == 1: return {"mode": "one", "item": matched[0], "via": "catalog"}
    if matched: return {"mode": "multi", "items": matched, "via": "catalog"}
    return {"mode": "none"}


def lookup_item_by_code(code, owner="", auth="", all_matches=False):
    """本地资料优先；默认不联网。可在设置中显式打开缺码实时查询。"""
    code = (code or "").strip()
    if not code or STORE.get_kv("sku_lookup", "1") == "0": return None
    scope = catalog_context(owner, auth)
    rows = CATALOG.lookup(scope, code)
    if rows: return rows if all_matches else (rows[0] if len(rows) == 1 else None)
    if scope:
        found, info = CATALOG.point_get(scope, code)
        if found:
            return info if all_matches or not isinstance(info, list) else (info[0] if len(info) == 1 else None)
    # 旧版离线缓存仅在尚无公司身份的本地迁移期沿用；不得跨公司复用旧 code 主键。
    if not scope and not JST.uid:
        hit = STORE.sku_cache_get(code)
        if hit and hit["hit"] and str(hit.get("owner_co_id") or "") in ("", str(owner)):
            return dict(hit, src="legacy-cache")
    if not JST.uid or STORE.get_kv("catalog_online", "0") != "1": return None
    try:
        info = JST.lookup_item(code, owner, auth or JST.coid)
    except Exception as e:
        from jst import ItemAmbiguousError
        if isinstance(e, ItemAmbiguousError):
            CATALOG.point_put(scope, code, e.items)
            return e.items if all_matches else None
        log("item lookup fail %s: %s" % (code, e))
        return None
    CATALOG.point_put(scope, code, info)
    return info


def scan_enabled_candidates(rows, owner="", auth=""):
    if STORE.get_kv("scan_ignore_disabled", "1") == "0": return rows
    # Status comes from the current scoped catalog, never from names or purchase presence.
    scope = catalog_context(owner, auth)
    enriched = []
    for it in rows:
        row = dict(it)
        sku = str(row.get("sku_id") or "").strip().lower()
        matches = [v for v in CATALOG.lookup(scope, sku)
                   if str(v.get("sku_id") or "").strip().lower() == sku]
        if len(matches) == 1:
            row["enabled"] = matches[0].get("enabled")
        enriched.append(row)
    return enabled_scan_candidates(enriched, True)


def receipt_candidates(session, code):
    base = json.loads(session.get("items_json") or "[]")
    direct = match_item(base, code)
    hits = direct.get("items", []) if direct["mode"] == "multi" else ([direct["item"]] if direct.get("item") else [])
    catalog = []
    if STORE.get_kv("sku_lookup", "1") != "0":
        catalog = CATALOG.lookup(catalog_context(session.get("owner_co_id", ""), session.get("auth_co_id", "")), code)
        if not catalog and not hits:
            info = lookup_item_by_code(code, session.get("owner_co_id", ""), session.get("auth_co_id", ""), all_matches=True)
            if info: catalog = info if isinstance(info, list) else [info]
    by_sku = {str(it.get("sku_id") or "").strip().lower(): it for it in base}
    result = {}
    for it in hits + catalog:
        key = str(it.get("sku_id") or "").strip().lower()
        if not key: continue
        purchase = by_sku.get(key)
        row = dict(it, **(purchase or {}))
        row.update(matched=bool(purchase), outside_purchase=not bool(purchase))
        if not purchase: row["poi_id"] = ""
        row.setdefault("match_via", "商品资料" if it in catalog else "采购明细")
        row["_scan_via"] = direct.get("via", "catalog") if it in hits else "catalog"
        result[key] = row
    return scan_enabled_candidates(list(result.values()), session.get("owner_co_id", ""), session.get("auth_co_id", ""))


def receipt_selection(session, body):
    """Re-resolve server-side; never trust candidate metadata supplied by the client."""
    selected = body.get("item") or {}
    code = str(body.get("code") or selected.get("sku_id") or "").strip()
    choices = receipt_candidates(session, code)
    found = [it for it in choices if str(it.get("sku_id")) == str(selected.get("sku_id"))
             and str(it.get("poi_id") or "") == str(selected.get("poi_id") or "")]
    if len(found) != 1: raise ValueError("商品资料或采购明细已变化，请重新扫码选择")
    return found[0]


def record_comparisons(sid):
    from purchase_snapshot import initial_view, live_view
    return {"live_purchase_total": live_view(STORE, sid), "initial_purchase": initial_view(STORE, sid)}


def session_payload(sid):
    s = STORE.get_session(sid)
    if not s:
        return None
    return {
        "id": s["id"], "po_id": s["po_id"], "outer_po_id": s["outer_po_id"],
        "input_no": s["input_no"], "supplier": s["supplier"], "wh_name": s["wh_name"],
        "status": s["status"], "po_date": s["po_date"], "state": s["state"],
        "owner_co_id": s.get("owner_co_id") or "",
        "auth_co_id": s.get("auth_co_id") or "",
        "started_at": s["started_at"], "ended_at": s["ended_at"],
        "paused_at": s.get("paused_at") or "",
        "po_refreshed_at": STORE.get_kv("po_refreshed_at:" + str(sid), ""),
        # Only known IDs recorded for this exact receiving round; never infer ERP confirmation.
        "inbound_io_ids": list(dict.fromkeys(str(j["io_id"]).strip() for j in STORE.inbound_jobs(sid)
                                             if j["status"] != "rejected" and str(j.get("io_id") or "").strip())),
    }


def full_state(sid=0):
    """当前收货上下文。sid 指定时用那张单（挂起单也能被拉出来操作），否则用 active。"""
    s = STORE.get_session(int(sid)) if sid else None
    s = s or STORE.active_session()
    openings = STORE.open_sessions()
    # 除当前展示的那张外，其余未结单都是「挂起中」，前端拿它渲染一键恢复列表
    paused = [{"id": x["id"], "po_id": x["po_id"], "outer_po_id": x["outer_po_id"],
               "supplier": x["supplier"], "paused_at": x.get("paused_at") or "",
               "started_at": x["started_at"], "state": x["state"],
               "batches": len(STORE.batches(x["id"])),
               "progress": STORE.progress(x["id"]),
               "round_progress": STORE.receiving_round(x["id"])["progress"]}
              for x in openings if not s or x["id"] != s["id"]]
    out = {
        "logged": bool(JST.uid),
        "user": {"uid": JST.uid, "coid": JST.coid, "uname": JST.uname, "coname": JST.coname},
        "session": session_payload(s["id"]) if s else None,
        "batches": STORE.batches(s["id"]) if s else [],
        "purchase_balance": STORE.purchase_balance(s["id"]) if s else [],
        "receiving_round": STORE.receiving_round(s["id"]) if s else None,
        "progress": STORE.progress(s["id"]) if s else {"plan": 0, "recv": 0},
        "paused": paused,
        "settings": {
            "default_id_type": STORE.get_kv("default_id_type", "auto"),
            "owner_ids": STORE.get_kv("owner_ids", ""),
            "scan_step": STORE.get_kv("scan_step", "1"),
            "sound": STORE.get_kv("sound", "1"),
            "sku_lookup": STORE.get_kv("sku_lookup", "1"),
            "scan_ignore_disabled": STORE.get_kv("scan_ignore_disabled", "1"),
            "inbound_validate_purchase": STORE.get_kv("inbound_validate_purchase", "1"),
            "catalog_auto": STORE.get_kv("catalog_auto", "1"),
            "catalog_online": STORE.get_kv("catalog_online", "0"),
            "pick_owner": STORE.get_kv("pick_owner", ""),
            "pick_auth": STORE.get_kv("pick_auth", ""),
        },
        "sku_cache": STORE.sku_cache_count(),
    }
    return out


def _pick_default_batch(sid):
    bs = STORE.batches(sid)
    return bs[-1]["id"] if bs else None


def po_items(sid, bid=0):
    """左栏「采购单明细」：采购单基线 + 每个商品的已收数量。

    排序即采购单原始顺序（基线来自 items_json，本来就按接口返回顺序存）。
    recv 取「全采购单各物流单号合计」（不只看当前子批次），progress 是行内小进度。
    """
    s = STORE.get_session(sid)
    if not s:
        return []
    try:
        base = json.loads(s["items_json"] or "[]")
    except Exception:
        base = []
    # 同一 SKU 在采购单出现多行时，实收按 SKU 合计（与差异表、purchase_balance、
    # 入库预览一致），再按采购单顺序逐行分摊：先填满前面的行，超收记在该 SKU 最后一行。
    # 这样每行 recv 之和 = SKU 实收合计，不会每行都显示总数。
    planned, _ = STORE.sku_comparison(sid, base)
    left = {k: p["recv"] for k, p in planned.items()}
    last_idx = {}
    for i, it in enumerate(base):
        last_idx["sku:" + str(it.get("sku_id"))] = i
    out = []
    for i, it in enumerate(base):
        key = "sku:" + str(it.get("sku_id"))
        plan = float(it.get("qty") or 0)
        if last_idx[key] == i:
            got = left[key]
        else:
            got = min(max(plan, 0.0), left[key])
        left[key] -= got
        sku = planned[key]
        out.append({
            "idx": i, "poi_id": it.get("poi_id"), "sku_id": it.get("sku_id"),
            "i_id": it.get("i_id"), "sku_code": it.get("sku_code"),
            "aux_codes": it.get("aux_codes"), "name": it.get("name"), "spec": it.get("spec"),
            "plan": plan, "recv": got,
            "done": abs(got - plan) < 1e-6,
            "over": got > plan + 1e-6,
            "sku_plan": sku["plan"], "sku_recv": sku["recv"],
        })
    return out


def po_order(sid):
    """{poi_id: 在采购单里的序号} —— 给明细表按采购单顺序排序用。"""
    s = STORE.get_session(sid)
    if not s:
        return {}
    try:
        base = json.loads(s["items_json"] or "[]")
    except Exception:
        base = []
    return {str(it.get("poi_id")): i for i, it in enumerate(base) if it.get("poi_id")}


def cur_lines(sid, bid):
    """当前物流单号明细，已按「采购单顺序优先、采购单没有的沉底」排好。

    额外：本采购单里「已经在别的物流单号收满」的商品，作为灰色只读行
    （done_elsewhere=True）追加到明细表最底部 —— 扫了下一个单号后，上一个
    单号已收满的商品不会凭空消失，而是自动沉到下面，方便一眼核对还差什么。
    """
    if not bid:
        return []
    lines = STORE.batch_lines(bid, order=po_order(sid))
    # 当前单号已出现的商品 key（含计划外）
    present = {str(l.get("key")) for l in lines}
    try:
        base = json.loads((STORE.get_session(sid) or {}).get("items_json") or "[]")
    except Exception:
        base = []
    planned, _ = STORE.sku_comparison(sid, base)   # 同一 SKU 多行按合计判断是否收满
    seq = 0
    for it in base:
        key = "sku:" + str(it.get("sku_id"))
        if key in present:
            continue                       # 本单号已经有这一行，不重复
        present.add(key)                   # 同一 SKU 多行只沉底展示一次
        plan, got = planned[key]["plan"], planned[key]["recv"]
        if plan <= 0 or abs(got - plan) > 1e-6:
            continue                       # 只在别的单号正好收满时才沉底展示
        lines.append({
            "id": 0, "batch_id": bid, "session_id": sid,
            "key": key, "poi_id": it.get("poi_id"), "sku_id": it.get("sku_id"),
            "i_id": it.get("i_id"), "sku_code": it.get("sku_code"),
            "name": it.get("name"), "spec": it.get("spec"),
            "qty": got, "matched": 1, "done_elsewhere": True,
            "from_batch": _done_batch_label(sid, key), "_seq": seq,
        })
        seq += 1
    return lines


def _done_batch_label(sid, key):
    """某商品是在哪个物流单号收满的（用于灰色行的「已在 XX 收满」提示）。"""
    try:
        row = STORE.db.execute(
            "SELECT b.tracking_no FROM line l JOIN batch b ON b.id=l.batch_id "
            "WHERE l.session_id=? AND l.key=? AND l.qty>0 ORDER BY l.id DESC LIMIT 1",
            (sid, key)).fetchone()
        return row["tracking_no"] if row else ""
    except Exception:
        return ""




def api_state(q=None):
    try:
        sid = int((q or {}).get("sid") or 0)
    except Exception:
        sid = 0
    st = full_state(sid)
    bid = st["batches"][-1]["id"] if st["batches"] else None
    if q and q.get("batch_id"):
        try:
            bid = int(q["batch_id"])
        except Exception:
            pass
    st["current_batch"] = bid
    st["lines"] = cur_lines(st["session"]["id"], bid) if st["session"] else []
    # 左栏采购单明细：供前端渲染「应收 / 已收 / 进度」
    st["items"] = po_items(st["session"]["id"]) if st["session"] else []
    return st


class _UnplannedLazy:
    # Opening the original app must not create an unused second database.
    # All requests are serialized by state_lock, as are regular receipt writes.
    def __init__(self): self.instance = None
    def __getattr__(self, name):
        if self.instance is None:
            self.instance = UnplannedService(os.path.join(DATA, "unplanned.db"), JST, find_purchase_order, lookup_item_by_code, scan_filter=scan_enabled_candidates)
            self.instance.inbound.cross_guard = lambda store, sid: cross_receipt_guard(store, sid)
        return getattr(self.instance, name)

UNPLANNED = _UnplannedLazy()

def purchase_sources():
    stores = {'receipt': STORE}
    if getattr(UNPLANNED, 'instance', UNPLANNED) is not None or os.path.exists(os.path.join(DATA,'unplanned.db')):
        stores['unplanned'] = UNPLANNED.store
    return stores

class _WatchLazy:
    def __init__(self): self.instance = None
    def __getattr__(self, name):
        with state_lock:
            if self.instance is None:
                self.instance = PurchaseWatch(os.path.join(DATA,'purchase_watch.db'), JST, purchase_sources, state_lock)
            return getattr(self.instance, name)
WATCH = _WatchLazy()
INBOUND.cross_guard = lambda store, sid: WATCH.cross_guard(store,sid)


class _JointLazy:
    def __init__(self): self.instance=None
    def __getattr__(self,name):
        if self.instance is None:
            self.instance=JointService(os.path.join(DATA,'joint_receipts.db'),JST,find_purchase_order,lookup_item_by_code,scan_filter=scan_enabled_candidates)
            self.instance.inbound.cross_guard=cross_receipt_guard
        return getattr(self.instance,name)
JOINT=_JointLazy()

def cross_receipt_guard(store,sid):
    WATCH.cross_guard(store,sid)
    if JOINT.instance is None and not os.path.exists(os.path.join(DATA,'joint_receipts.db')):return
    target=store.get_session(sid)
    from purchase_watch import identity
    for row in JOINT.store.db.execute("SELECT j.*,s.po_id,s.owner_co_id,s.auth_co_id,t.group_sid FROM inbound_job j JOIN session s ON s.id=j.session_id JOIN joint_stage t ON t.sid=s.id WHERE j.status<>'rejected'"):
        j=dict(row)
        if store is JOINT.store and j['session_id']==sid:continue
        if identity(j)!=identity(target):continue
        own=JOINT.store.db.execute('SELECT group_sid FROM joint_stage WHERE sid=?',(sid,)).fetchone() if store is JOINT.store else None
        if not own or own[0]!=j['group_sid'] or j['status']!='complete':
            raise ValueError('此采购单在其他来源 / 联合批次已有入库或待核实记录，请核对并继续原单，禁止重复开单')
INBOUND.cross_guard=cross_receipt_guard

def scan_step():
    """设置里的「每次累加数量」：必须是正整数；历史上存了非法值（小数/负数）时按 1 处理。"""
    try:
        return validate_qty(STORE.get_kv("scan_step", "1") or "1")
    except ValueError:
        return 1


CLIENT_OP_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def client_op(b, path, fields):
    """(op, previous) for an optional client_op_id; (None, None) when absent.

    One id per physical scan / confirmation, reused by the client on retry. The
    digest covers the business fields so a reused id with different content is
    refused instead of silently replayed."""
    op_id = b.get("client_op_id")
    if op_id in (None, ""):
        return None, None
    if not isinstance(op_id, str) or not CLIENT_OP_RE.match(op_id):
        raise ValueError("操作编号格式无效")
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
                            .encode("utf-8")).hexdigest()
    op = {"id": op_id, "path": path, "digest": digest}
    return op, STORE.client_op_get(op_id)


def _rollback_open_transaction(path=""):
    """Roll back anything a request left uncommitted on the shared STORE connection.

    Every successful store write commits itself (``with self.db:``), so an open
    transaction at the end of a request means a write failed part-way (for example
    a trigger RAISE(ABORT) after an earlier statement). Discard it."""
    try:
        if STORE.db.in_transaction:
            STORE.db.rollback()
            log("%s: 已回滚未提交的本地写入" % path)
    except Exception as e:
        log("%s: rollback failed: %s" % (path, e))


# ---------------------------------------------------------------- HTTP

class DesktopHTTPServer(ThreadingHTTPServer):
    # Windows may reject bursts above the stdlib's five pending connections.
    # WebView loads many assets while the native recorder polls this same port;
    # HTTP/1.0 intentionally closes responses to keep safe shutdown bounded.
    request_queue_size = 128


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "jst-scan/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---------- helpers ----------
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Native HTTP/1.0 closes after each response. Announce it explicitly:
        # .NET concurrent requests can otherwise reuse a closing socket (WinError 10053).
        if self.close_connection and not any(k.lower() == "connection" for k in (extra or {})):
            self.send_header("Connection", "close")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _ok(self, **kw):
        d = {"ok": True}
        d.update(kw)
        self._json(d)

    def _fail(self, msg, hint="", code=200):
        self._json({"ok": False, "msg": msg, "hint": hint}, code)

    def _body(self):
        if hasattr(self, "_desktop_body"):
            return self._desktop_body
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path):
        if path in ("", "/", "/index.html"):
            path = "/index.html"
        safe = os.path.normpath(path.lstrip("/"))
        if safe.startswith("..") or os.path.isabs(safe):
            return self._send(404, b"not found")
        p = os.path.join(WEB, safe)
        if not os.path.isfile(p):
            self._send(404, b"not found")
            return
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".woff2": "font/woff2",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}.get(os.path.splitext(p)[1], "application/octet-stream")
        with open(p, "rb") as f:
            self._send(200, f.read(), ctype)

    def _actor(self):
        return "电脑端 / ERP:" + str(JST.coid) + ":" + str(JST.uid)

    def _desktop_guard(self):
        """Loopback-only authority; reject DNS rebinding and cross-site requests."""
        port = self.server.server_address[1]
        hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port}
        valid = self.headers.get("Host", "").lower() in hosts
        origin = self.headers.get("Origin")
        if origin:
            valid = valid and origin.lower() in {"http://" + h for h in hosts}
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            valid = False
        if valid:
            return True
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if 0 < size <= 1024 * 1024:
                self.connection.settimeout(2)
                self.rfile.read(size)
        except (ValueError, OSError):
            pass
        self.close_connection = True
        self._fail("禁止非本机地址或跨站请求", code=403)
        return False

    def _clipboard_guard(self):
        # Never write this computer's clipboard on behalf of a LAN/PDA client.
        try:
            valid = ipaddress.ip_address(self.client_address[0]).is_loopback
            valid = valid and ipaddress.ip_address(self.server.server_address[0]).is_loopback
        except ValueError:
            valid = False
        if not valid:
            self.close_connection = True
            self._fail('图文剪贴板仅供本机电脑端使用', code=403)
        return valid

    def _copy_report_clipboard(self):
        if not self._clipboard_guard(): return
        self.close_connection = True  # malformed/oversize requests never leak into keep-alive
        if self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json':
            return self._fail('复制请求必须是JSON', code=400)
        try:
            size = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return self._fail('复制请求长度无效', code=400)
        if not 0 < size <= MAX_REQUEST_BYTES:
            return self._fail('复制内容为空或超过12MB，请仅复制差异', code=413)
        try:
            self.connection.settimeout(10)
            raw = self.rfile.read(size)
            if len(raw) != size:
                return self._fail('复制请求不完整', code=400)
            payload = json.loads(raw.decode('utf-8'))
            result = REPORT_CLIPBOARD.copy(payload)
            return self._ok(**result)
        except PermissionError as e:
            return self._fail(str(e), code=403)
        except (ValueError, UnicodeError) as e:
            return self._fail(str(e), code=400)
        except Exception:
            return self._fail('图文未完整复制，请确认剪贴板未被占用后重试', code=503)

    def _update_post(self, path):
        """自动更新接口：不取 state_lock；联网工作只在 UpdateHost 的后台线程里进行。"""
        try:
            if hasattr(self, "_desktop_body"):
                del self._desktop_body
            body = self._desktop_body = self._body()
            body = body if isinstance(body, dict) else {}
            if path == "/api/update/settings":
                return self._ok(**UPDATER.update_settings(body))
            if path == "/api/update/check":
                UPDATER.check_async(force=True)
                return self._ok(**UPDATER.status())
            if path == "/api/update/download":
                ch = body.get("channel")
                return self._ok(**UPDATER.start_download(ch if isinstance(ch, str) and ch else None))
            if path == "/api/update/cancel":
                return self._ok(**UPDATER.cancel_download())
            if path == "/api/update/prepare":
                info = UPDATER.prepare_install(recording=body.get("recording") is True)
                return self._ok(install=info, **UPDATER.status())
            return self._fail("接口不存在", code=404)
        except ValueError as e:
            return self._fail(str(e))
        except Exception as e:
            log("%s crash: %s" % (path, e))
            return self._fail("更新操作失败：%s" % (str(e)[:160] or e.__class__.__name__))

    # ---------- GET ----------
    # These GETs never touch STORE / JST and must stay responsive while a long POST
    # holds state_lock (heartbeat feeds the watchdog). Every other /api/* GET runs
    # under state_lock so it cannot interleave with a locked POST on the shared
    # SQLite connection or the shared JstClient session.
    LOCK_FREE_GETS = frozenset(("/api/heartbeat", "/api/pda/download/status",
                                "/api/report/clipboard/status", "/api/remote/status",
                                "/api/update/status"))

    def do_GET(self):
        if not self._desktop_guard(): return
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/") and path not in self.LOCK_FREE_GETS:
            with state_lock:
                return self._do_get()
        return self._do_get()

    def _do_get(self):
        global LAST_HEARTBEAT
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        q = {k: v[0] for k, v in qs.items()}

        if path.startswith('/api/purchase-watch/'):
            try:
                with state_lock:
                    if path == '/api/purchase-watch/list': return self._ok(**WATCH.listing())
                    if path == '/api/purchase-watch/options':
                        service = WATCH.inbound(int(q['id']),q['source'],int(q['sid']))
                        w=WATCH.get(int(q['id']))
                        if not any(g['source']==q['source'] and g['sid']==int(q['sid']) for g in WATCH.capture(w)['groups']): raise ValueError('收货来源不匹配')
                        return self._ok(warehouses=service.context(service.store.get_session(int(q['sid']))).warehouses())
                    return self._fail('接口不存在',code=404)
            except Exception as e: return self._fail(str(e))

        if path == '/api/receipts/extra-history':
            try:
                with state_lock:
                    text=q.get('q','');rows=[]
                    if getattr(UNPLANNED,'instance',UNPLANNED) is not None or os.path.exists(os.path.join(DATA,'unplanned.db')):
                        for r in UNPLANNED.history(text):rows.append(dict(source='unplanned',id=r['id'],label=r['input_no'],description='无采购单登记 · '+str(r.get('po_id') or '未补采购单')))
                    if JOINT.instance is not None or os.path.exists(os.path.join(DATA,'joint_receipts.db')):
                        for r in JOINT.history(text):rows.append(dict(source='joint',id=r['id'],label=r['input_no'],description='联合收货 · 母单 '+r['mother']+' · '+','.join(r['po_ids'])))
                    return self._ok(rows=rows)
            except Exception as e:return self._fail(str(e))
        if path.startswith('/api/joint/'):
            try:
                with state_lock:
                    sid=int(q.get('sid') or 0)
                    if path=='/api/joint/history':return self._ok(rows=JOINT.history(q.get('q',''),q.get('kind','all')))
                    if path=='/api/joint/state':return self._ok(**JOINT.state(sid))
                    if path=='/api/joint/options':
                        JOINT.group(sid)
                        return self._ok(warehouses=JOINT.inbound.context(JOINT.store.get_session(sid)).warehouses())
                    if path=='/api/joint/export':return self._send(200,JOINT.export(sid),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename=joint-receipt.csv'})
                    return self._fail('接口不存在',code=404)
            except Exception as e:return self._fail(str(e))

        if path.startswith('/api/unplanned/'):
            try:
                with state_lock:
                    sid = int(q.get('sid') or 0)
                    if path == '/api/unplanned/history': return self._ok(rows=UNPLANNED.history(q.get('q',''),q.get('kind','all')))
                    if path == '/api/unplanned/state': return self._ok(**UNPLANNED.state(sid))
                    if path == '/api/unplanned/options': return self._ok(warehouses=UNPLANNED.inbound.context(UNPLANNED.session(sid)).warehouses())
                    if path == '/api/unplanned/export': return self._send(200, UNPLANNED.export(sid), 'text/csv; charset=utf-8', {'Content-Disposition':'attachment; filename=unplanned-receipt.csv'})
                    return self._fail('接口不存在', code=404)
            except Exception as e:
                return self._fail(str(e))

        if path == "/api/pda/download/status":
            return self._ok(**DOWNLOAD.status())

        if path in ("/api/backup/status", "/api/pda/status", "/api/operations", "/api/operations/export"):
            try:
                with state_lock:
                    if path == "/api/backup/status": return self._ok(**BACKUPS.status())
                    if path == "/api/pda/status": return self._ok(**LAN.status())
                    if path == "/api/operations/export":
                        return self._send(200, OPERATIONS.export(), "text/csv; charset=utf-8", {"Content-Disposition": "attachment; filename=operations.csv"})
                    return self._ok(rows=OPERATIONS.rows())
            except Exception as e:
                return self._fail(str(e))

        if path == "/api/remote/status":
            return self._ok(**REMOTE.status())
        if path == "/api/update/status":
            return self._ok(**UPDATER.status())
        if path == "/api/heartbeat":
            note_beat()
            return self._ok(ts=time.time())

        if path == "/api/report/clipboard/status":
            if not self._clipboard_guard(): return
            return self._ok(**REPORT_CLIPBOARD.status())

        if path == "/api/state":
            return self._json(api_state(q))

        if path == "/api/inbound/options":
            try:
                with state_lock:
                    s = STORE.get_session(int(q.get("sid") or 0))
                    if not s:
                        return self._fail("找不到收货批次")
                    api = INBOUND.context(s)
                    partners = load_partners()
                    owner, auth = api.context["owner_co_id"], api.context["authorize_co_id"]
                    name = lambda key, value: next((str(r.get("name")) for r in partners.get(key, []) if str(r.get("id")) == str(value)), str(value))
                    return self._ok(owner=owner, auth=auth, owner_name=name("owners", owner), auth_name=name("wms", auth),
                                    warehouses=api.warehouses())
            except Exception as e:
                return self._fail(str(e))

        if path.startswith("/api/inventory/"):
            try:
                with state_lock:
                    if not JST.uid:
                        return self._fail("请先登录聚水潭")
                    if path == "/api/inventory/partners":
                        p = load_partners(refresh=True)
                        if p.get("err"):
                            return self._fail("货主／仓库公司读取失败，请检查网络和权限后重试")
                        clean = lambda rows: [r for r in rows if str(r.get("id", "")).isdigit() and str(r["id"]) not in ("0", "2147483647")]
                        owners, wms = clean(p.get("owners", [])), clean(p.get("wms", []))
                        for rows in (owners, wms):
                            if str(JST.coid).isdigit() and not any(str(r["id"]) == str(JST.coid) for r in rows):
                                rows.append({"id": str(JST.coid), "name": JST.coname or "当前公司"})
                        return self._ok(owners=owners, wms=wms, identity=str(JST.coid) + ":" + str(JST.uid))
                    if path == "/api/inventory/config":
                        return self._ok(**INVENTORY.config(q.get("owner"), q.get("auth")))
                    if path == "/api/inventory/lookup":
                        return self._ok(rows=INVENTORY.lookup(q.get("owner"), q.get("auth"), q.get("wh_id"), q.get("code"), q.get("code_type", "sku"), q.get("location", "all")))
                    if path == "/api/inventory/history":
                        return self._ok(rows=INVENTORY.history())
            except Exception as e:
                return self._fail(str(e))

        if path == "/api/inbound/preview":
            try:
                with state_lock:
                    return self._ok(**INBOUND.preview(int(q.get("sid") or 0), q.get("wh_id"), mode=q.get("mode")))
            except Exception as e:
                return self._fail(str(e))

        if path == "/api/diff":
            try:
                sid = int(q.get("sid") or 0)
                if not sid:
                    s = STORE.active_session()
                    if not s:
                        return self._fail(PERM_MSG)
                    sid = s["id"]
                return self._ok(session=session_payload(sid), rows=STORE.diff_report(sid),
                                receiving_round=STORE.receiving_round(sid))
            except Exception as e:
                log("diff fail: %s" % e)
                return self._fail(PERM_MSG)

        if path == "/api/query/po":
            try:
                no = (q.get("no") or "").strip()
                if not no:
                    return self._fail("请输入采购单号")
                ss = STORE.find_sessions(no)
                if not ss:
                    return self._ok(found=False, hint="本地无该采购单的收货记录")
                out = []
                for s in ss:
                    with state_lock:
                        if q.get("refresh") == "1":
                            with state_lock:
                                refresh_purchase_session(s["id"])
                        # 两段式：groups = 按物流单号分组的明细；diff = 汇总后的与采购单差异
                        out.append({"session": session_payload(s["id"]),
                                    "purchase_balance": STORE.purchase_balance(s["id"]),
                                    "receiving_round": STORE.receiving_round(s["id"]),
                                    "batches": STORE.batches(s["id"]),
                                    "groups": STORE.batches_with_lines(s["id"], order=po_order(s["id"])),
                                    "summary": STORE.received_summary(s["id"]),
                                    "po_summary": STORE.po_received_summary(s["id"]),
                                    **record_comparisons(s["id"]),
                                    "diff": STORE.diff_report(s["id"]),
                                    "progress": STORE.progress(s["id"])})
                return self._ok(found=True, records=out)
            except Exception as e:
                log("query po fail: %s" % e)
                return self._fail(PERM_MSG)

        if path == "/api/query/tracking":
            try:
                no = (q.get("no") or "").strip()
                if not no:
                    return self._fail("请输入物流单号")
                bs = STORE.find_batches(no)
                for b in bs:
                    b["lines"] = STORE.batch_lines(b["id"])
                return self._ok(found=bool(bs), batches=bs)
            except Exception as e:
                log("query tracking fail: %s" % e)
                return self._fail(PERM_MSG)

        if path == "/api/export":
            return self._export(q)

        if path.startswith("/files/"):
            name = os.path.basename(path[len("/files/"):])
            p = os.path.join(EXPORT_DIR, name)
            if not os.path.isfile(p):
                return self._send(404, b"not found")
            with open(p, "rb") as f:
                data = f.read()
            fn = urllib.parse.quote(name)
            return self._send(200, data,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                              if name.endswith(".xlsx") else "text/csv; charset=utf-8",
                              {"Content-Disposition": "attachment; filename*=UTF-8''%s" % fn})

        if path == "/api/recents":
            rows = STORE.recent_sessions(30)
            out = []
            for r in rows:
                p = session_payload(r["id"])
                p["batches"] = len(STORE.batches(r["id"]))
                p["progress"] = STORE.progress(r["id"])
                p["round_progress"] = STORE.receiving_round(r["id"])["progress"]
                out.append(p)
            return self._ok(rows=out)

        if path == "/api/session/detail":
            """点进一张收货单：两段式数据（分物流单号明细 + 汇总差异）"""
            try:
                sid = int(q.get("sid") or 0)
            except Exception:
                sid = 0
            if not sid:
                return self._fail("参数不完整")
            s = STORE.get_session(sid)
            if not s:
                return self._fail("找不到该收货单")
            with state_lock:
                if q.get("refresh") == "1":
                    try:
                        with state_lock:
                            refresh_purchase_session(sid)
                    except Exception:
                        return self._fail("采购单实时刷新失败，未使用旧数据冒充最新结果；请检查登录、网络和原公司权限后重试")
                return self._ok(session=session_payload(sid),
                                purchase_balance=STORE.purchase_balance(sid),
                                receiving_round=STORE.receiving_round(sid),
                                groups=STORE.batches_with_lines(sid, order=po_order(sid)),
                                summary=STORE.received_summary(sid),
                                po_summary=STORE.po_received_summary(sid),
                                **record_comparisons(sid),
                                diff=STORE.diff_report(sid),
                                progress=STORE.progress(sid),
                                items=po_items(sid))

        if path == "/api/batch/lines":
            """按子批次取明细（不限是否收货中，供历史记录查看）"""
            try:
                bid = int(q.get("batch_id") or 0)
            except Exception:
                bid = 0
            if not bid:
                return self._fail("参数不完整")
            return self._ok(lines=STORE.batch_lines(bid))

        if path == "/api/partners":
            data = load_partners(refresh=q.get("refresh") == "1")
            data["pick_owner"] = STORE.get_kv("pick_owner", "")
            data["pick_auth"] = STORE.get_kv("pick_auth", "")
            return self._ok(**data)

        if path == "/api/item/pic":
            """按商品编码/条码取商品主图 URL（多候选弹窗用）。
            结果随商品资料缓存落库（7 天）；接口失败一律静默返回空图，不影响选择。"""
            code = (q.get("code") or "").strip()
            if not code:
                return self._ok(pic="")
            try:
                info = lookup_item_by_code(code, (q.get("owner") or "").strip(), (q.get("auth") or "").strip())
                if info and info.get("pic"):
                    return self._ok(pic=info["pic"])
            except Exception as e:
                log("item pic fail %s: %s" % (code, e))
            return self._ok(pic="")

        if path == "/api/accounts":
            """常用登录账号（只记账号名，不存密码），登录页快速选择用"""
            return self._ok(rows=STORE.list_accounts(), preferences=LOGIN_MEMORY.preferences())

        if path == "/api/debug/wms-raw":
            # 现场取证：把分仓/货主接口的**原始返回**原样吐出来，用于排查"分仓显示数字"。
            # 这是个只读诊断入口，不写任何缓存，出问题让用户直接开这个页面看。
            out = {"uid": JST.uid, "coid": JST.coid}
            try:
                out["wms_raw"] = JST.wms_raw()
            except Exception as e:
                out["wms_err"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            try:
                out["owners_raw"] = JST.owners_raw()
            except Exception as e:
                out["owners_err"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            out["wms_parsed"] = JST.wms_list()
            out["owners_parsed"] = JST.partners()
            return self._ok(**out)

        if path == "/api/catalog/status":
            with state_lock:
                company = str(JST.coid or STORE.get_kv("catalog_company", ""))
                enabled = STORE.get_kv("catalog_auto", "1") != "0"
            return self._ok(sync=CATALOG_SYNC.status(), rows=CATALOG.status(company) if company else [],
                            logged=bool(JST.uid), enabled=enabled, interval_hours=6)

        if path == "/api/settings":
            return self._json(full_state()["settings"])

        if path == "/api/captcha":
            # 代理官方图形验证码。id 必须是登录账号，且 ValidCode.aspx 种下的
            # 会话 cookie 要由 JST 客户端带着去验 —— 不能让浏览器直接请求。
            acc = (q.get("account") or "").strip()
            if not acc:
                return self._send(400, b"missing account")
            try:
                raw, ctype = JST.validcode_image(acc)
            except LoginError as e:
                log("captcha fail: %s" % e)
                return self._send(502, str(e).encode("utf-8"), "text/plain; charset=utf-8")
            except Exception as e:
                log("captcha crash: %s" % e)
                return self._send(502, b"captcha unavailable")
            return self._send(200, raw, ctype)

        if path == "/api/qr/poll":
            # 扫码期间前端每 2 秒问一次；登录成功则自动采纳会话并关掉扫码窗口
            try:
                return self._json(qr_poll())
            except LoginError as e:
                return self._ok(logged=False, pending=str(e))
            except Exception as e:
                log("qr poll crash: %s" % e)
                return self._ok(logged=False)

        return self._static(path)

    def _export(self, q):
        mode = q.get("mode", "range")
        fmt = q.get("format", "csv")
        try:
            rows = STORE.export_rows(mode, q.get("value", ""), q.get("start", ""), q.get("end", ""))
        except Exception as e:
            log("export fail: %s" % e)
            return self._fail(PERM_MSG)
        if not rows:
            return self._fail("所选范围内没有可导出的收货记录", hint="请确认筛选条件")

        os.makedirs(EXPORT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        tag = {"po": "采购单%s" % q.get("value", ""), "tracking": "物流单%s" % q.get("value", ""),
               "range": "%s至%s" % (q.get("start", ""), q.get("end", ""))}.get(mode, "全部")
        base = "收货明细_%s_%s" % (tag, stamp)
        cols = ["采购单号", "对应物流单号", "扫描时间", "商品编码", "商品名称", "数量"]
        if fmt == "xlsx":
            name = safe_filename(base + ".xlsx")
            path = os.path.join(EXPORT_DIR, name)
            xlsx.write_xlsx(path, "收货明细", cols, rows)
        else:
            name = safe_filename(base + ".csv")
            path = os.path.join(EXPORT_DIR, name)
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(",".join(cols) + "\r\n")
                for r in rows:
                    f.write(",".join('"%s"' % str(x).replace('"', '""') for x in r) + "\r\n")
        return self._ok(name=name, rows=len(rows), url="/files/" + urllib.parse.quote(name),
                        path=path, preview={"cols": cols, "rows": rows[:5]})

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._desktop_guard(): return
        if path == "/api/report/clipboard":
            return self._copy_report_clipboard()
        if path.startswith("/api/update/"):
            return self._update_post(path)
        try:
            # Consume once even for no-argument routes; keep-alive must not treat
            # an unread JSON body as the next HTTP request.
            if hasattr(self, "_desktop_body"):
                del self._desktop_body
            self._desktop_body = self._body()
            fn = getattr(self, "_post_" + path.strip("/").replace("/", "_").replace("-", "_"), self._nf)
            with state_lock:
                try:
                    if path in ("/api/login", "/api/login/restore", "/api/sms/login", "/api/qr/finish", "/api/logout"):
                        LAN.invalidate()
                    return fn()
                finally:
                    # A handler that failed (or caught its own error) must never leave a
                    # half-written transaction on the shared connection for the next
                    # request's commit() to publish.
                    _rollback_open_transaction(path)
        except Exception as e:
            log("%s crash: %s" % (path, e))
            try:
                return self._fail(str(e) if "入库" in str(e) else PERM_MSG)
            except Exception:
                return None

    def _nf(self):
        self._send(404, b"not found")

    def _post_api_purchase_watch_action(self):
        try:
            b=self._body(); action=b.get('action')
            if action=='register':
                if b.get('source')=='unplanned': UNPLANNED.store
                w=WATCH.register(b.get('source'),int(b.get('sid') or 0))
                threading.Thread(target=WATCH.poll,daemon=True).start()
                return self._ok(record=w)
            if action=='poll':
                threading.Thread(target=WATCH.poll,daemon=True).start()
                return self._ok(msg='已安排后台只读核对')
            if action=='delete':
                return self._ok(**WATCH.delete(int(b['id']),b.get('confirmed')))
            if action=='archive':
                return self._ok(record=WATCH.archive(int(b['id']),b.get('note',''),b.get('confirmed')))
            if action in ('transition','auto'):
                return self._ok(record=WATCH.transition(int(b['id']),b.get('state','pending'),b.get('note',''),action=='auto'))
            service=WATCH.inbound(int(b['id']),b['source'],int(b['sid']))
            service.cross_guard=cross_receipt_guard
            if action=='preview':
                if not b.get('wh_id'): raise ValueError('请选择入库仓库')
                return self._ok(**service.preview(int(b['sid']),str(b['wh_id'])))
            if action=='create':
                if b.get('confirmed') is not True or b.get('keep_differences') is not True or not b.get('wh_id'):raise ValueError('请预览并单次明确确认保留差异按实收生成待入库单')
                job=service.create(int(b['sid']),str(b.get('fingerprint') or ''),str(b['wh_id']))
                return self._ok(job={k:v for k,v in job.items() if k!='snapshot_json'})
            if action=='resume':
                w=WATCH.get(int(b['id']))
                if not any(g['source']==b['source'] and g['sid']==int(b['sid']) for g in WATCH.capture(w)['groups']):raise ValueError('收货来源不匹配')
                job=service.store.get_inbound(int(b['job_id']))
                if b.get('confirmed') is not True or not job or job['session_id']!=int(b['sid']):raise ValueError('请确认继续此批次的原单')
                job=service.resume(job['id'],str(b.get('io_id') or ''))
                return self._ok(job={k:v for k,v in job.items() if k!='snapshot_json'})
            raise ValueError('操作不存在')
        except Exception as e:return self._fail(str(e))

    def _post_api_joint_action(self):
        try:
            b=self._body();action=b.get('action');sid=int(b.get('sid') or 0)
            if action in ('preview','create','resume'):
                JOINT.group(sid);stage=JOINT.stage(int(b.get('stage_sid') or 0))
                if stage['group_sid']!=sid or stage['cancelled']:raise ValueError('分配不属于当前批次或已取消')
                if action=='preview':
                    if not b.get('wh_id'):raise ValueError('请选择仓库')
                    return self._ok(**JOINT.inbound.preview(stage['sid'],str(b['wh_id'])))
                if b.get('confirmed') is not True:raise ValueError('请明确确认按已分配数量生成 / 继续待入库单，不自动确认库存')
                if action=='create':
                    if not b.get('wh_id'):raise ValueError('请选择仓库')
                    job=JOINT.inbound.create(stage['sid'],str(b.get('fingerprint') or ''),str(b['wh_id']),mode=stage['mode'])
                else:
                    job=JOINT.store.get_inbound(int(b.get('job_id') or 0))
                    if not job or job['session_id']!=stage['sid']:raise ValueError('入库记录不属于当前分配')
                    job=JOINT.inbound.resume(job['id'],str(b.get('io_id') or ''))
                return self._ok(job={k:v for k,v in job.items() if k!='snapshot_json'})
            return self._ok(**JOINT.mutate(action,b))
        except Exception as e:return self._fail(str(e))

    def _post_api_unplanned_action(self):
        try:
            b = self._body()
            action = b.get('action')
            sid = int(b.get('sid') or 0)
            if action == 'new':
                b.setdefault('owner_co_id', STORE.get_kv('pick_owner', '') or str(JST.coid))
                b.setdefault('auth_co_id', STORE.get_kv('pick_auth', '') or str(JST.coid))
            if action == 'preview':
                if not b.get('wh_id'): raise ValueError('请选择入库仓库')
                return self._ok(**UNPLANNED.inbound.preview(sid,str(b['wh_id'])))
            if action == 'create':
                if b.get('confirmed') is not True or not b.get('wh_id'): raise ValueError('请先预览、选择仓库并确认')
                job = UNPLANNED.inbound.create(sid,str(b.get('fingerprint') or ''),str(b['wh_id']))
                return self._ok(job={k:v for k,v in job.items() if k!='snapshot_json'})
            if action == 'resume':
                if b.get('confirmed') is not True: raise ValueError('请确认继续原单')
                job = UNPLANNED.store.get_inbound(int(b.get('job_id') or 0))
                if not job or job['session_id'] != sid: raise ValueError('入库记录不属于此批次')
                result = UNPLANNED.inbound.resume(job['id'],str(b.get('io_id') or '').strip())
                return self._ok(job={k:v for k,v in result.items() if k!='snapshot_json'})
            result = UNPLANNED.mutate(action,b)
            if action in ('new', 'context'):
                CATALOG_SYNC.event.set()
            if action == 'resolve' and not result.get('candidates'):
                result['hint'] = '当前货主 / 分仓商品库未匹配到此编码。请核对货主、分仓和条码；在设置中同步该货主商品库，或明确开启缺码实时查询。未登记任何数量。'
            return self._ok(**result)
        except Exception as e:
            return self._fail(str(e))

    def _post_api_inbound_create(self):
        b = self._body()
        if b.get("confirmed") is not True:
            return self._fail("请先预览并明确确认生成待入库单")
        if not str(b.get("wh_id") or "").strip():
            return self._fail("请先选择入库仓库并重新预览")
        try:
            job = INBOUND.create(int(b.get("sid") or 0), str(b.get("fingerprint") or ""), str(b["wh_id"]), mode=b.get("mode"))
            return self._ok(job={k: v for k, v in job.items() if k != "snapshot_json"},
                            msg="已按实收生成待入库单 %s（未自动确认入库）" % job["io_id"])
        except Exception as e:
            return self._fail(str(e))

    def _post_api_inbound_resume(self):
        b = self._body()
        if b.get("confirmed") is not True:
            return self._fail("请先确认继续原入库单")
        try:
            job = INBOUND.resume(int(b.get("job_id") or 0), str(b.get("io_id") or "").strip())
            return self._ok(job={k: v for k, v in job.items() if k != "snapshot_json"},
                            msg="入库单 %s 已完成实收数量核对（未自动确认入库）" % job["io_id"])
        except Exception as e:
            return self._fail(str(e))

    def _post_api_inventory_preview(self):
        try:
            b = dict(self._body())
            b["actor"] = self._actor()
            return self._ok(job=INVENTORY.preview(b))
        except Exception as e:
            return self._fail(str(e))

    def _post_api_inventory_submit(self):
        b = self._body()
        if b.get("confirmed") is not True:
            return self._fail("请先预览并明确确认库存修改")
        try:
            return self._ok(job=INVENTORY.submit(str(b.get("token") or "")))
        except Exception as e:
            return self._fail(str(e))

    def _post_api_inventory_verify(self):
        try:
            return self._ok(job=INVENTORY.verify(str(self._body().get("token") or "")))
        except Exception as e:
            return self._fail(str(e))

    def _post_api_backup_create(self):
        self._body()
        try:
            return self._ok(backup=BACKUPS.create("manual"), **BACKUPS.status())
        except Exception as e:
            return self._fail("备份失败：" + str(e))

    def _post_api_pda_download_start(self):
        b = self._body()
        if b.get("confirmed") is not True:
            return self._fail("请确认仅向可信局域网开放安装包下载10分钟")
        try:
            return self._ok(**DOWNLOAD.start(str(b.get("host") or ""), b.get("port", 8767)))
        except (ValueError, OSError) as e:
            return self._fail(str(e))

    def _post_api_pda_download_stop(self):
        DOWNLOAD.stop()
        return self._ok(**DOWNLOAD.status())

    def _post_api_pda_start(self):
        b = self._body()
        try:
            if b.get("confirmed") is not True: return self._fail("请确认仅向可信局域网启用PDA服务")
            return self._ok(**LAN.start(str(b.get("host") or ""), b.get("port", 8766)))
        except Exception as e:
            return self._fail(str(e))

    def _post_api_pda_stop(self):
        self._body()
        LAN.stop()
        return self._ok(**LAN.status())

    def _post_api_pda_pairing(self):
        self._body()
        try:
            return self._ok(**LAN.pairing())
        except Exception as e:
            return self._fail(str(e))

    def _post_api_pda_revoke(self):
        LAN.revoke(str(self._body().get("id") or ""))
        return self._ok(**LAN.status())

    def _post_api_remote_config(self):
        try:
            return self._ok(msg="远程管理页设置已保存", **REMOTE.update(self._body()))
        except ValueError as e:
            return self._fail(str(e))
        except OSError as e:
            return self._fail("远程管理页设置未保存：%s" % e)

    def _post_api_remote_firewall(self):
        self._body()
        try:
            return self._ok(**REMOTE.open_firewall())
        except (ValueError, OSError) as e:
            return self._fail(str(e))

    def _post_api_operations_note(self):
        b = self._body()
        try:
            OPERATIONS.note(str(b.get("kind") or ""), str(b.get("ref") or ""), b.get("text", ""), self._actor())
            return self._ok(msg="备注已追加；未解锁或重新执行任何操作")
        except Exception as e:
            return self._fail(str(e))

    def _post_api_catalog_sync(self):
        self._body()
        if not JST.uid: return self._fail("请先登录聚水潭")
        if STORE.get_kv("catalog_auto", "1") == "0": return self._fail("请先开启并保存商品资料自动同步设置")
        queued = CATALOG_SYNC.request()
        return self._ok(msg="已安排后台更新，扫码继续使用旧资料" if queued else "正在同步，请稍候")

    def _post_api_settings(self):
        b = self._body()
        allow = ("default_id_type", "owner_ids", "scan_step", "sound",
                 "sku_lookup", "pick_owner", "pick_auth", "catalog_auto", "catalog_online",
                 "inbound_validate_purchase", "scan_ignore_disabled")
        d = {}
        for k, v in b.items():
            if k not in allow:
                continue
            v = str(v)
            if k == "inbound_validate_purchase" and v not in ("0", "1"):
                return self._fail("校验采购单明细开关只能为0或1")
            if k == "scan_ignore_disabled" and v not in ("0", "1"):
                return self._fail("扫码忽略禁用商品开关只能为0或1")
            if k == "scan_step":
                try:
                    v = str(validate_qty(b[k]))
                except ValueError:
                    return self._fail("每次累加数量必须是正整数（商品数量不能有小数）")
            # 单号类型只收合法值；auto = 界面「自动识别」（四种类型轮换）
            if k == "default_id_type" and v not in ID_TYPES + ["auto"]:
                continue
            d[k] = v
        if d:
            STORE.set_many(d)
        CATALOG_SYNC.event.set()
        return self._ok(msg="设置已保存")

    def _post_api_login_restore(self):
        result = LOGIN_MEMORY.restore(JST, lambda: JstClient(timeout=10))
        if result['restored']:
            invalidate_partners()
        return self._ok(**result)

    def _post_api_login_preferences(self):
        b = self._body()
        prefs = {k: b.get(k) is True for k in ('remember_account', 'keep_signed_in')}
        STORE.set_many({'login_remember': '1' if prefs['remember_account'] else '0',
                        'login_keep': '1' if prefs['keep_signed_in'] else '0'})
        if not prefs['keep_signed_in']:
            LOGIN_MEMORY.forget()
        QR_LOGIN_OPTIONS.update(prefs)
        return self._ok(preferences=prefs)

    def _post_api_login(self):
        b = self._body()
        acc = (b.get("account") or "").strip()
        pwd = b.get("password") or ""
        if not acc or not pwd:
            return self._fail("请输入账号和密码")
        fp = {k: (v or "")[:12] for k, v in (("jd3", b.get("jd3", "")), ("vd144", b.get("vd144", "")))}
        code = (b.get("verify_code") or "").strip()
        log("login attempt account=%s fp=%s captcha=%s" % (acc, fp, bool(code)))
        try:
            info = JST.login(acc, pwd, code, b.get("jd3", ""), b.get("vd144", ""))
        except CaptchaError as e:
            log("login need captcha account=%s reason=%s" % (acc, e))
            return self._ok(need_captcha=True, msg=str(e) or "请输入图形验证码后再登录")
        except LoginError as e:
            log("login FAIL account=%s reason=%s" % (acc, e))
            return self._fail(str(e))
        except Exception as e:
            log("login crash: %s" % e)
            return self._fail("登录失败，请稍后重试")
        log("login OK uid=%s coid=%s" % (info.get("uid"), info.get("coid")))
        warning = LOGIN_MEMORY.completed(JST, b, acc, 'pw')
        invalidate_partners()
        return self._ok(user=info, warning=warning)

    # ---------------- 短信验证码登录 ----------------

    def _post_api_sms_send(self):
        b = self._body()
        mobile = (b.get("mobile") or "").strip()
        suffix = (b.get("suffix") or "").strip()
        if not mobile:
            return self._fail("请输入手机号")
        SMS_STATE.clear()  # new challenge invalidates older mobile/suffix/cid
        try:
            r = JST.sms_send(mobile, suffix)
        except LoginError as e:
            log("sms send fail: %s" % e)
            return self._fail(str(e))
        except Exception as e:
            log("sms send crash: %s" % e)
            return self._fail("验证码下发失败，请稍后重试")
        if r["need_suffix"]:
            # 命中了登录账号，需要先验证手机号后两位（对应用户在网页上的那一步）
            return self._ok(need_suffix=True, prefix=r["prefix"],
                            msg="需要验证账号绑定的手机号，请根据提示输入绑定手机号后两位")
        if not r["cid"]:
            return self._fail("服务端未返回验证码凭据，请稍后重试")
        SMS_STATE["cid"] = r["cid"]
        SMS_STATE["mobile"] = mobile
        SMS_STATE["suffix"] = suffix
        SMS_STATE["sent_at"] = time.time()
        log("sms sent to %s***" % mobile[:3])
        return self._ok(msg="验证码已发送，请注意查收")

    def _post_api_sms_login(self):
        b = self._body()
        mobile = (b.get("mobile") or "").strip()
        code = (b.get("code") or "").strip()
        suffix = (b.get("suffix") or "").strip()
        cid = SMS_STATE.get("cid", "")
        if not mobile or not code:
            return self._fail("请输入手机号和验证码")
        if (not cid or SMS_STATE.get("mobile", "") != mobile or
                SMS_STATE.get("suffix", "") != suffix or time.time() - SMS_STATE.get("sent_at", 0) > 600):
            return self._fail("请先获取验证码", hint="验证码凭据已失效，请重新点「获取验证码」")
        try:
            info = JST.sms_login(mobile, code, cid, suffix)
        except LoginError as e:
            log("sms login fail: %s" % e)
            return self._fail(str(e))
        except Exception as e:
            log("sms login crash: %s" % e)
            return self._fail("登录失败，请稍后重试")
        SMS_STATE["cid"] = ""
        log("sms login OK uid=%s" % info.get("uid"))
        warning = LOGIN_MEMORY.completed(JST, b, mobile, 'sms')
        invalidate_partners()
        return self._ok(user=info, warning=warning)

    # ---------------- 扫码登录（微信 / 钉钉） ----------------

    def _post_api_qr_open(self):
        b = self._body()
        QR_LOGIN_OPTIONS.clear()
        QR_LOGIN_OPTIONS.update({k: b.get(k, v) for k, v in LOGIN_MEMORY.preferences().items()})
        kind = (b.get("kind") or "weixin").strip()
        try:
            r = qr_launch(kind, refresh=b.get("refresh") is True)
        except BizError as e:
            return self._fail(str(e))
        except Exception as e:
            log("qr open crash: %s" % e)
            return self._fail("打开扫码登录窗口失败")
        tip = "请扫描本页显示的官方二维码；如需绑定或安全验证，请切换到官方网页登录。"
        if kind == "official":
            tip = "已打开独立官方窗口，请完成登录及所需安全验证，成功后自动返回。"
        return self._ok(opened=True, reused=r.get("reused"), kind=kind, tip=tip)

    def _post_api_qr_finish(self):
        try:
            r = qr_adopt()
        except LoginError as e:
            log("qr adopt fail: %s" % e)
            return self._fail(str(e), hint="也可以用账号密码或短信验证码登录")
        except Exception as e:
            log("qr adopt crash: %s" % e)
            return self._fail("读取官方登录状态失败，请重新打开登录窗口",
                              hint="请重新打开官方登录窗口并保持打开，无需管理员权限，不读取Cookie文件")
        return self._ok(user=r["user"], cookies=r["cookies"], msg="扫码登录成功", warning=r.get("warning", ""))

    def _post_api_qr_cancel(self):
        qr_close()
        return self._ok(msg="已关闭扫码登录窗口")

    def _post_api_logout(self):
        LOGIN_MEMORY.forget()
        QR_LOGIN_OPTIONS.clear()
        qr_close(wait=False)
        SMS_STATE["cid"] = ""
        self._reset()
        return self._ok()

    def _reset(self):
        JST.cookies = {}
        JST.uid = JST.coid = JST.uname = JST.coname = ""
        JST._vs_cache = {}
        JST._sku_cache = {}
        invalidate_partners()

    def _po_create(self):
        """创建收货批次：查一张采购单并把它置为当前收货。

        与旧版「激活」的关键差别：**不再因为已有单据收货中而拒绝**。
        同一张采购单只保留一张收货单（重复创建则复用并恢复），
        之前那张正在收的单会自动挂起，随时可从「挂起批次」列表点回来。
        """
        b = self._body()
        no = (b.get("no") or "").strip()
        if not no:
            return self._fail("请输入或扫描采购单号")
        # 界面上选的商家/分仓优先；没选就走自动轮换
        pick_owner = (b.get("owner_co_id") or STORE.get_kv("pick_owner", "") or "").strip()
        pick_auth = (b.get("authorize_co_id") or STORE.get_kv("pick_auth", "") or "").strip()
        # 单号类型：界面选了具体类型就优先按它试，选「自动识别」则四种类型轮换
        prefer = (b.get("id_type") or "").strip()
        log("po create no=%s type=%s owner=%s auth=%s"
            % (no, prefer or "-", pick_owner or "-", pick_auth or "-"))
        try:
            row, ctx = find_purchase_order(no, pick_owner, pick_auth, prefer=prefer)
            items = fetch_items(row, ctx)
        except BizError as e:
            detail = str(e)
            return self._fail(PERM_MSG,
                              hint=("未找到该采购单。请确认单号，或在「设置」里补充所属货主ID"
                                    if detail == "PO_NOT_FOUND" else ""))
        except Exception:
            return self._fail(PERM_MSG)

        existing = STORE.find_session_by_po(row["po_id"], ctx["owner"], ctx["auth"])
        was_closed = bool(existing and existing["state"] == "closed")
        if was_closed and str(b.get("new_round_from") or "") != str(existing["id"]):
            return self._ok(require_new_round_confirmation=True,
                previous_round_id=existing["id"], po_id=row["po_id"],
                msg="该采购单上一轮已结束。新一轮按当前ERP剩余量作为应收，历史实收不计入新一轮。请先核对上轮收货是否已在ERP处理完毕；未完成入库的旧货不要在新一轮重复扫描。")
        row["owner_co_id"] = ctx["owner"]
        row["auth_co_id"] = ctx["auth"]
        sid = STORE.open_session(no, row["po_id"], ctx["id_type"], row, items)
        STORE.set_kv("po_refreshed_at:" + str(sid), time.strftime("%Y-%m-%d %H:%M:%S"))
        STORE.activate_session(sid)
        return self._ok(session=session_payload(sid), head=row,
                        id_type=ctx["id_type"],
                        id_type_label=ID_TYPE_LABELS.get(ctx["id_type"], ctx["id_type"]),
                        resumed=bool(existing) and not was_closed,
                        reopened=was_closed,
                        items=[{k: v for k, v in it.items() if k != "aux_codes"} for it in items],
                        hint=("按外部单号 %s 匹配到采购单 %s" % (no, row["po_id"])
                              if row["po_id"] != no else ""))

    # 兼容旧前端/旧书签
    _post_api_po_activate = _po_create
    _post_api_po_create = _po_create

    def _post_api_session_pause(self):
        """退出批次：把当前收货中的单挂起，人立刻空闲，可去创建别的批次。
        数据全部保留，随时可以从「挂起批次」列表点回来接着扫。"""
        b = self._body()
        s = None
        try:
            sid = int(b.get("sid") or 0)
        except Exception:
            sid = 0
        s = STORE.get_session(sid) if sid else STORE.active_session()
        if not s:
            return self._fail("当前没有正在收货的批次")
        STORE.pause_session(s["id"])
        return self._ok(session=session_payload(s["id"]), msg="已退出批次 %s，可继续创建其他批次" % s["po_id"])

    def _post_api_session_pause_report(self):
        """保存并挂起本轮，返回本地差异快照；不结单、不刷新或写入 ERP。"""
        b = self._body()
        raw_sid = b.get("sid")
        # 必须显式指定目标，非法参数不能退回到另一个 active 批次。
        if isinstance(raw_sid, bool) or not str(raw_sid or "").isdigit():
            return self._fail("请指定有效的收货批次")
        sid = int(raw_sid)
        s = STORE.get_session(sid) if sid > 0 else None
        if not s:
            return self._fail("找不到该收货批次")
        if s["state"] not in ("active", "paused"):
            return self._fail("该采购单已结单，不能暂存；请在历史记录中查看差异")
        # do_POST 已持有 state_lock；报告和暂停与其他收货写入串行。
        rows = STORE.diff_report(sid)
        prog = STORE.progress(sid)
        batches = STORE.batches(sid)
        receiving_round = STORE.receiving_round(sid)
        STORE.pause_session(sid)
        return self._ok(session=session_payload(sid), diff=rows, progress=prog,
                        batches=batches, receiving_round=receiving_round,
                        msg="明细已暂存，本轮未结单，可继续对单或恢复收货")

    def _post_api_session_resume(self):
        """重新进入一张挂起的批次，接着上次继续扫。"""
        b = self._body()
        try:
            sid = int(b.get("sid") or 0)
        except Exception:
            sid = 0
        s = STORE.get_session(sid)
        if not s:
            return self._fail("找不到该收货批次")
        if s["state"] == "closed":
            return self._fail("该采购单已结单，不能继续扫码", hint="如需继续收货，请重新「创建收货批次」")
        if b.get("refresh") is True:
            refresh_purchase_session(sid)
        STORE.activate_session(sid)
        return self._ok(session=session_payload(sid), batches=STORE.batches(sid),
                        progress=STORE.progress(sid), msg="已回到 %s，可继续扫码" % s["po_id"])

    def _post_api_session_close(self):
        """结单：整单收尾，汇总全部物流单号后给出完整差异报告。"""
        try:
            b = self._body()
        except Exception:
            b = {}
        try:
            sid = int((b or {}).get("sid") or 0)
        except Exception:
            sid = 0
        s = STORE.get_session(sid) if sid else STORE.active_session()
        if not s:
            return self._fail("当前没有正在收货的采购单")
        rows = STORE.diff_report(s["id"])
        prog = STORE.progress(s["id"])
        batches = STORE.batches(s["id"])
        STORE.close_session(s["id"])
        return self._ok(diff=rows, progress=prog, batches=batches, session=session_payload(s["id"]),
                        receiving_round=STORE.receiving_round(s["id"]))

    def _replay_client_op(self, op, old, sid, bid=None):
        """Duplicate client_op_id: return the original result, never add again."""
        if old["path"] != op["path"] or old["digest"] != op["digest"]:
            return self._fail("同一操作编号的请求内容不一致，未重复入账；请刷新后核对实收")
        try:
            resp = json.loads(old["response_json"]) if old.get("response_json") else None
        except ValueError:
            resp = None
        if not isinstance(resp, dict):
            resp = {"ok": True, "line_id": old["line_id"], "qty": old["qty"], "added": old["added"]}
        resp["duplicate"] = True
        # 附上当前明细，避免界面回到旧快照；数量本身不再变化
        if sid:
            resp["items"] = po_items(sid)
            resp["progress"] = STORE.progress(sid)
            if bid:
                resp["lines"] = cur_lines(sid, bid)
        resp.pop("ok", None)
        return self._ok(**resp)

    def _ok_op(self, op, **kw):
        if op:
            STORE.client_op_finish(op["id"], dict(ok=True, **kw))
        return self._ok(**kw)

    def _cur_session(self, bid=0):
        """收货类操作的上下文单据：优先 body 里的 sid，其次当前 active。"""
        try:
            sid = int(bid or 0)
        except Exception:
            sid = 0
        s = STORE.get_session(sid) if sid else None
        return s or STORE.active_session()

    def _post_api_tracking_scan(self):
        b = self._body()
        s = self._cur_session(b.get("sid") or 0)
        if not s:
            return self._fail("请先创建收货批次")
        if s["state"] == "closed":
            return self._fail("该采购单已结单", hint="如需继续收货，请重新「创建收货批次」")
        if s["state"] == "paused":
            STORE.activate_session(s["id"])    # 挂着扫单号 = 顺手恢复
        no = (b.get("tracking_no") or "").strip()
        if not no:
            return self._fail("请输入或扫描物流单号")
        batch, created = STORE.ensure_batch(s["id"], no)
        return self._ok(batch=batch, created=created,
                        lines=STORE.batch_lines(batch["id"]),
                        batches=STORE.batches(s["id"]),
                        batch_id=batch["id"])

    def _post_api_item_scan(self):
        b = self._body()
        s = self._cur_session(b.get("sid") or 0)
        if not s:
            return self._fail("请先创建收货批次")
        if s["state"] == "closed":
            return self._fail("该采购单已结单", hint="如需继续收货，请重新「创建收货批次」")
        bid = b.get("batch_id")
        if not bid:
            bs = STORE.batches(s["id"])
            if not bs:
                return self._fail("请先扫描物流单号建立子批次")
            bid = bs[-1]["id"]
        # 指定批次必须属于本单，避免拿到别的单的 batch_id 串数据
        try:
            bid_int = int(bid)
        except Exception:
            return self._fail("物流子批次参数不合法")
        owner = None
        for x in STORE.batches(s["id"]):
            if x["id"] == bid_int:
                owner = x
                break
        if owner is None:
            return self._fail("物流子批次与当前采购单不匹配", hint="请重新扫描物流单号")
        bid = bid_int
        code = (b.get("code") or "").strip()
        if not code:
            return self._fail("无效条码")
        step = scan_step()
        # qty 显式传入时用它的值（前端「待确认数量」，默认 1）；
        # qty 传 0 = 只解析不通账（前端刚扫到编码、还没确认数量）；
        # 未传 qty（旧前端/直接调用）= 按设置里的每次累加数量，保持向后兼容。
        raw_qty = b.get("qty")
        dry = False
        if raw_qty is None or raw_qty == "":
            add = step
        else:
            try:
                add = validate_qty(raw_qty, allow_zero=True)
            except ValueError as e:
                return self._fail(str(e))
            if add == 0:
                dry = True
                add = step        # dry 时不会真的入账，这个值只用于占位
        op = None
        if not dry:
            try:
                op, old = client_op(b, "/api/item/scan", {"sid": s["id"], "batch_id": bid, "code": code, "qty": add})
            except ValueError as e:
                return self._fail(str(e))
            if old:
                return self._replay_client_op(op, old, s["id"], bid)
        try:
            candidates = receipt_candidates(s, code)
        except ValueError as e:
            return self._fail(str(e))
        info = candidates[0] if len(candidates) == 1 else None
        res = ({"mode": "multi", "items": candidates} if len(candidates) > 1 else
               {"mode": "one", "item": info, "via": info.get("_scan_via", "catalog")} if info and info["matched"] else {"mode": "none"})
        if b.get("expected_sku_id"):
            current_sku = str((res.get("item") or {}).get("sku_id") or (info or {}).get("sku_id") or code)
            if (res["mode"] == "multi" or current_sku != str(b["expected_sku_id"])
                    or ("expected_matched" in b and bool(b["expected_matched"]) != (res["mode"] != "none"))):
                return self._fail("商品资料或采购明细已更新，解析结果发生变化，请重新扫码核对后再确认")

        if res["mode"] == "multi":
            return self._ok(multi=True, code=code, resolved=bool(info), dry=dry,
                            candidates=[
                                {"poi_id": it.get("poi_id"), "sku_id": it.get("sku_id"),
                                 "i_id": it.get("i_id"),
                                 "sku_code": it.get("sku_code"), "name": it.get("name"),
                                 "spec": it.get("spec"), "qty": it.get("qty"),
                                 "match_via": it.get("match_via", ""), "matched": it.get("matched", True),
                                 "outside_purchase": it.get("outside_purchase", False)}
                                for it in res["items"]], batch_id=bid)

        if res["mode"] == "none":
            # 接口认出了这是个真商品、但采购单里没有 → 仍算差异，只不过名称用真实商品名
            key = ("RAW:" + info["sku_id"]) if info else ("RAW:" + code)
            payload = {"key": key, "sku_id": (info or {}).get("sku_id") or code,
                       "sku_code": (info or {}).get("sku_code") or code,
                       "name": (info or {}).get("name") or ("未知商品(%s)" % code),
                       "spec": (info or {}).get("spec") or "",
                       "matched": False, "poi_id": ""}
            if dry:
                return self._ok(dry=True, code=code, unmatched=True, resolved=bool(info),
                                item={"sku_id": payload["sku_id"], "name": payload["name"],
                                      "spec": payload["spec"]},
                                batch_id=bid, msg=("采购单无此商品，已识别，待确认数量" if info else
                                "本地资料未识别，仍可确认收货并编辑资料；入库前会核验编码，资料不存在不能入库"))
            lid, qty = STORE.add_qty(bid, s["id"], payload, add, op=op)
            return self._ok_op(op, unmatched=True, line_id=lid, qty=qty, code=code,
                            added=add, resolved=bool(info),
                            lines=cur_lines(s["id"], bid),
                            items=po_items(s["id"]),
                            item={"sku_id": payload["sku_id"], "name": payload["name"],
                                  "spec": payload["spec"]},
                            progress=STORE.progress(s["id"]),
                            msg=(("采购单无此商品（已识别为「%s」），已记入差异" % payload["name"])
                                 if info else "采购单无此商品，已记入差异"))

        it = res["item"]
        payload = {"key": it.get("sku_id"), "poi_id": it.get("poi_id"), "sku_id": it.get("sku_id"),
                   "i_id": it.get("i_id"), "sku_code": it.get("sku_code"), "name": it.get("name"),
                   "spec": it.get("spec"), "matched": True}
        if dry:
            return self._ok(dry=True, code=code, resolved=True, via=res.get("via", "barcode"),
                            item={k: payload[k] for k in ("sku_id", "name", "spec")},
                            batch_id=bid, msg="已识别商品，待确认数量")
        lid, qty = STORE.add_qty(bid, s["id"], payload, add, op=op)
        return self._ok_op(op, line_id=lid, qty=qty, added=add, via=res.get("via", "barcode"),
                        item={k: payload[k] for k in ("sku_id", "name", "spec")},
                        lines=cur_lines(s["id"], bid), items=po_items(s["id"]),
                        progress=STORE.progress(s["id"]))

    def _post_api_item_choose(self):
        """多候选时人工选定后继续追加"""
        b = self._body()
        s = self._cur_session(b.get("sid") or 0)
        if not s:
            return self._fail("请先创建收货批次")
        if s["state"] == "closed":
            return self._fail("该采购单已结单", hint="如需继续收货，请重新「创建收货批次」")
        bid = b.get("batch_id")
        if not any(str(x["id"]) == str(bid) for x in STORE.batches(s["id"])):
            return self._fail("物流子批次与当前采购单不匹配")
        bid = int(bid)
        # 与 item/scan 一致：显式 qty 优先（前端「待确认数量」）；缺省用设置里的累加数
        raw_qty = b.get("qty")
        try:
            add = scan_step() if raw_qty is None or raw_qty == "" else validate_qty(raw_qty)
        except ValueError as e:
            return self._fail(str(e))
        sel = b.get("item") if isinstance(b.get("item"), dict) else {}
        try:
            op, old = client_op(b, "/api/item/choose", {"sid": s["id"], "batch_id": bid, "qty": add,
                                "sku_id": sel.get("sku_id"), "poi_id": sel.get("poi_id"), "code": b.get("code")})
        except ValueError as e:
            return self._fail(str(e))
        if old:
            return self._replay_client_op(op, old, s["id"], bid)
        try:
            it = receipt_selection(s, b)
        except ValueError as e:
            return self._fail(str(e))
        matched = it["matched"]
        payload = {"key": it["sku_id"] if matched else "RAW:" + it["sku_id"],
                   "poi_id": it.get("poi_id", ""), "sku_id": it["sku_id"], "i_id": it.get("i_id", ""),
                   "sku_code": it.get("sku_code", ""), "name": it.get("name", ""),
                   "spec": it.get("spec", ""), "matched": matched}
        lid, qty = STORE.add_qty(bid, s["id"], payload, add, op=op)
        return self._ok_op(op, line_id=lid, qty=qty, added=add, unmatched=not matched, resolved=True,
                        item=it, lines=cur_lines(s["id"], bid), items=po_items(s["id"]),
                        progress=STORE.progress(s["id"]))

    def _post_api_line_edit(self):
        b = self._body()
        try:
            sid, bid, lid = int(b.get("sid") or 0), int(b.get("batch_id") or 0), int(b.get("line_id") or 0)
            line = STORE.edit_receipt_line(lid, sid, bid, b.get("values") or {}, b.get("expected"))
        except (ValueError, TypeError, sqlite3.Error) as e:
            return self._fail(str(e))
        return self._ok(line=line, lines=cur_lines(sid, bid), items=po_items(sid), progress=STORE.progress(sid),
                        msg="本地收货资料已保存，实收数量未改变；入库前仍须核验商品资料")

    def _post_api_line_set(self):
        """改数量。收货中的单据直接改；已挂起 / 已结束的同样允许改（历史纠正）。"""
        b = self._body()
        try:
            lid = int(b.get("line_id"))
            qty = validate_qty(b.get("qty"), allow_zero=True)
        except ValueError as e:
            return self._fail(str(e) if "数量" in str(e) else "数量修改失败")
        except Exception:
            return self._fail("数量修改失败")
        bid = b.get("batch_id")
        s = self._cur_session(b.get("sid") or 0)
        sid = s["id"] if s else 0
        try:
            op, old = client_op(b, "/api/line/set", {"line_id": lid, "qty": qty})
        except ValueError as e:
            return self._fail(str(e))
        if old:
            return self._replay_client_op(op, old, sid, bid)
        try:
            STORE.set_qty(lid, qty, op=op)
        except Exception as e:
            log("line set fail: %s" % e)
            return self._fail(str(e) if "入库" in str(e) else "数量修改失败")
        return self._ok_op(op, line_id=lid, qty=qty,
                           lines=cur_lines(sid, bid) if bid else [],
                           items=po_items(sid) if sid else [],
                           progress=STORE.progress(sid) if sid else None)

    def _post_api_line_del(self):
        b = self._body()
        try:
            STORE.del_line(int(b.get("line_id")))
        except Exception as e:
            log("line del fail: %s" % e)
            return self._fail(str(e) if "入库" in str(e) else "删除失败")
        bid = b.get("batch_id")
        s = self._cur_session(b.get("sid") or 0)
        sid = s["id"] if s else 0
        return self._ok(lines=cur_lines(sid, bid) if bid else [],
                        items=po_items(sid) if sid else [],
                        progress=STORE.progress(sid) if sid else None)

    def _post_api_batch_del(self):
        """删除整个子批次（含其全部明细行）"""
        b = self._body()
        try:
            bid = int(b.get("batch_id"))
        except Exception:
            return self._fail("参数不完整")
        try:
            n = STORE.del_batch(bid)
        except Exception as e:
            log("batch del fail: %s" % e)
            return self._fail(str(e) if "入库" in str(e) else "删除失败")
        s = self._cur_session(b.get("sid") or 0)
        bs = STORE.batches(s["id"]) if s else []
        return self._ok(deleted=n, batches=bs,
                        lines=STORE.batch_lines(bs[-1]["id"]) if bs else [],
                        progress=STORE.progress(s["id"]) if s else None,
                        msg="已删除物流单及其 %d 条明细" % n)

    def _post_api_batch_copy(self):
        try:
            from receipt_copy import copy_previous
            b = self._body(); sid = int(b.get('sid') or 0)
            def guard():
                s = STORE.get_session(sid)
                if not s or s['state'] == 'closed': raise ValueError('收货批次不存在或已结单')
                if s.get('id_type') in ('unplanned','joint','joint-allocation'): raise ValueError('请在对应的收货模块复制')
            def validate(s, rows):
                allowed = scan_enabled_candidates(rows, s.get('owner_co_id',''), s.get('auth_co_id',''))
                if {r['sku_id'] for r in allowed} != {r['sku_id'] for r in rows}:
                    raise ValueError('上一单含已停用商品，不能整单复制')
                base = {str(r.get('sku_id')):r for r in json.loads(s['items_json'] or '[]')}
                for r in rows:
                    current = base.get(str(r['sku_id']))
                    if bool(r['matched']) != bool(current) or (current and str(current.get('poi_id') or '') != str(r['poi_id'] or '')):
                        raise ValueError('采购明细已变化，请先核对上一单的商品匹配关系')
            result = copy_previous(STORE, b, account=[str(JST.uid),str(JST.coid)], guard=guard, validate=validate)
            return self._ok(**result)
        except Exception as e:
            return self._fail(str(e))

    def _post_api_batch_rename(self):
        """编辑物流单号：该单号下的收货明细保持不变，只改单号本身"""
        b = self._body()
        try:
            bid = int(b.get("batch_id") or 0)
        except Exception:
            return self._fail("参数不完整")
        no = (b.get("tracking_no") or "").strip()
        if not no:
            return self._fail("物流单号不能为空")
        try:
            STORE.rename_batch(bid, no)
        except ValueError as e:
            return self._fail(str(e))
        except Exception as e:
            log("batch rename fail: %s" % e)
            return self._fail(str(e) if "入库" in str(e) else "修改失败")
        r = STORE.db.execute("SELECT session_id FROM batch WHERE id=?", (bid,)).fetchone()
        sid = r["session_id"] if r else 0
        return self._ok(msg="物流单号已改为 %s" % no, batches=STORE.batches(sid) if sid else [])

    def _post_api_account_del(self):
        """删除一个记住的登录账号"""
        b = self._body()
        STORE.del_account((b.get("name") or "").strip())
        return self._ok(rows=STORE.list_accounts(), msg="已删除")

    def _post_api_session_del(self):
        """删除整张采购单的收货记录（含全部子批次与明细）"""
        b = self._body()
        try:
            sid = int(b.get("sid") or 0)
        except Exception:
            sid = 0
        if not sid:
            s = STORE.active_session()
            sid = s["id"] if s else 0
        if not sid:
            return self._fail("没有可删除的记录")
        try:
            stat = STORE.del_session(sid)
        except Exception as e:
            log("session del fail: %s" % e)
            return self._fail(str(e) if "入库" in str(e) else "删除失败")
        return self._ok(stat=stat, msg="已删除该采购单收货记录（%d 个子批次 / %d 条明细）"
                                       % (stat["batches"], stat["lines"]))

    def _post_api_cache_clear(self):
        n = STORE.sku_cache_clear()
        JST._sku_cache = {}
        return self._ok(cleared=n, msg="已清空商品缓存 %d 条" % n)

    def _post_api_open_folder(self):
        try:
            os.makedirs(EXPORT_DIR, exist_ok=True)
            os.startfile(EXPORT_DIR)
        except Exception:
            return self._fail("无法打开文件夹")
        return self._ok()


def safe_filename(s):
    return re.sub(r'[\\/:*?"<>|]+', "_", s)


# ---------------------------------------------------------------- 启动

LAN = PdaHost(sys.modules[__name__])
DOWNLOAD = InstallerHost(sys.modules[__name__], os.path.join(BUNDLE if _frozen() else HERE, "installer"))
# 远程管理页（IPv6 直连，只读）：独立监听器与独立路由，默认关闭，绝不复用上面的 Handler。
REMOTE = RemoteHost(DATA, receipt_db=os.path.join(DATA, "jst.db"))


def watchdog():
    """心跳仅用于诊断，绝不作为用户退出指令；后台节流/睡眠/断网不能结束服务。"""
    disconnected = False
    while True:
        time.sleep(5)
        if not LAST_HEARTBEAT:
            continue
        missing = time.monotonic() - LAST_HEARTBEAT > 30
        if missing and not disconnected:
            log("网页心跳暂停；服务保持运行，可重新打开页面继续收货")
        elif disconnected and not missing:
            log("网页心跳恢复；服务继续运行")
        disconnected = missing


def edge_exe():
    """定位 msedge.exe。找不到返回 None。"""
    drive = os.environ.get("SystemDrive", "C:")
    pf = os.environ.get("ProgramFiles") or os.path.join(drive + os.sep, "Program Files")
    pfx86 = os.environ.get("ProgramFiles(x86)") or os.path.join(drive + os.sep, "Program Files (x86)")
    for p in (os.path.join(pfx86, "Microsoft", "Edge", "Application", "msedge.exe"),
              os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""),
                           "Microsoft", "Edge", "Application", "msedge.exe")):
        if p and os.path.isfile(p):
            return p
    return None


def open_browser(port):
    url = "http://127.0.0.1:%d/" % port
    exe = edge_exe()
    if exe:
        try:
            import subprocess
            subprocess.Popen([exe, "--app=%s" % url, "--window-size=1366,900",
                              "--disable-features=Translate", "--no-first-run"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception as e:
            log("edge launch fail: %s" % e)
    webbrowser.open(url)


# ------------------------------------------------- 扫码登录（第三方 OAuth）

def qr_launch(kind="weixin", refresh=False):
    """Dedicated per-launch profile; use the browser's actual debug port, never Cookie DB."""
    global QR_PROC, QR_CDP_PORT, QR_ACTIVE_PROFILE, QR_KIND, QR_STARTED
    import tempfile
    import subprocess
    if kind not in ("weixin", "dingtalk", "official"):
        raise BizError("不支持的登录方式")
    exe = edge_exe()
    if not exe:
        raise BizError("没有找到 Microsoft Edge，请安装后打开官方登录窗口")
    with QR_LOCK:
        # Edge's launcher may exit while its browser process continues running.
        if QR_CDP_PORT and cdp.alive(QR_CDP_PORT, timeout=1):
            if QR_KIND == kind:
                if not refresh:
                    return {"opened": True, "reused": True, "cdp": True}
                if kind != "official":
                    try:
                        if qrview.refresh(QR_CDP_PORT):
                            QR_STARTED = time.monotonic()
                            return {"opened": True, "reused": True, "refreshed": True, "cdp": True}
                    except Exception:
                        pass  # A dead/redirected owned target falls back to a fresh login browser.
            qr_close()
        qr_close()
        QR_KIND = kind
        QR_STARTED = time.monotonic()
        os.makedirs(QR_PROFILE, exist_ok=True)
        QR_ACTIVE_PROFILE = tempfile.mkdtemp(prefix="session-", dir=QR_PROFILE)
        QR_CDP_PORT = 0
        QR_PROC = subprocess.Popen(
            [exe, "--app=%s" % QR_URL, "--user-data-dir=%s" % QR_ACTIVE_PROFILE,
             "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
             "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
             "--enable-automation", "--disable-extensions",
             "--disable-features=Translate,msFedCmService", "--window-size=1120,860"]
            + (["--headless=new"] if kind != "official" else []),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        active = os.path.join(QR_ACTIVE_PROFILE, "DevToolsActivePort")
        for _ in range(60):
            try:
                with open(active, encoding="utf-8") as f:
                    port = int(f.readline().strip())
                if 1024 <= port <= 65535 and cdp.alive(port, timeout=0.5):
                    QR_CDP_PORT = port
                    log("official login debug channel ready")
                    return {"opened": True, "reused": False, "cdp": True}
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        qr_close()
    raise BizError("官方登录窗口的连接未就绪（可能被浏览器策略禁用）。请关闭刚打开的登录窗口后重试；不会读取Cookie文件或要求管理员权限")


def _qr_live():
    return bool(QR_CDP_PORT and cdp.alive(QR_CDP_PORT, timeout=1))


def qr_take_cookies():
    if not QR_CDP_PORT:
        return None
    try:
        return cdp.cookies_for_host(QR_CDP_PORT, QR_HOSTS)
    except Exception:
        raise LoginError("官方登录窗口连接中断。请保持登录窗口打开；若已关闭，请重新打开并登录（不读取Cookie文件）")


def _adopt_browser(ck):
    info = JST.adopt_cookies(ck)
    warning = LOGIN_MEMORY.completed(JST, QR_LOGIN_OPTIONS, kind='qr')
    invalidate_partners()
    LAN.invalidate()
    log("official login OK uid=%s" % info.get("uid"))
    qr_close(wait=False)
    return {"user": info, "cookies": len(ck), "via": "cdp", "warning": warning}


def qr_poll():
    with state_lock, QR_LOCK:
        if QR_CDP_PORT and QR_STARTED and time.monotonic() - QR_STARTED > 600:
            qr_close()
            return {"logged": False, "pending": "登录等待已超时，请刷新二维码或重新打开官方网页登录。"}
        ck = qr_take_cookies()
        if not ck or not ck.get("u_id") or not ck.get("u_co_id"):
            view = qrview.snapshot(QR_CDP_PORT, QR_KIND) if QR_CDP_PORT and QR_KIND != "official" else {}
            return dict(logged=False, **view)
        result = _adopt_browser(ck)
        result["logged"] = True
        return result


def qr_close(wait=True):
    """Close only our exact CDP browser. Never kill users' other Edge processes."""
    global QR_PROC, QR_CDP_PORT
    with QR_LOCK:
        port, proc = QR_CDP_PORT, QR_PROC
        QR_CDP_PORT, QR_PROC = 0, None
    def close():
        if port:
            try:
                cdp.command(port, "Browser.close", timeout=2)
            except Exception:
                pass  # Browser.close often closes the socket before replying.
        elif proc and proc.poll() is None:
            # Only our exact launcher, when startup failed before a CDP port existed.
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
    if wait:
        close()
    else:
        threading.Thread(target=close, daemon=True, name="official-login-close").start()
    return bool(port or proc)


def qr_adopt():
    ck = qr_take_cookies()
    if not ck or not ck.get("u_id"):
        if not QR_CDP_PORT:
            raise LoginError("官方登录窗口还没有打开或已经关闭，请重新打开并完成登录；不会读取Cookie文件")
        raise LoginError("官方窗口尚未登录成功，请先完成验证码/扫码及安全验证，并保持窗口打开")
    return _adopt_browser(ck)


def pick_port(preferred, tries=20):
    """端口被占用时顺延，避免同一台机器多实例互相抢占"""
    for i in range(tries):
        p = preferred + i
        if len(str(p)) > 5:
            break
        s = socket.socket()
        s.settimeout(0.35)
        try:
            s.bind(("127.0.0.1", p))
        except OSError:
            continue
        finally:
            s.close()
        return p
    return preferred


# Native host contract: the port it passed in JST_NATIVE_PORT is busy -> exit with
# this code and the host retries with a new port (no silent fallback scanning, the
# host would otherwise talk to the wrong process).
EXIT_PORT_IN_USE = 75
NATIVE_MUTEX_NAME = "Local\\ReceivingAssistantService"
_NATIVE_MUTEX = None


class StrictHTTPServer(DesktopHTTPServer):
    # Windows SO_REUSEADDR lets a second socket bind a port that is already in use;
    # strict native binding must fail instead, so use exclusive address use there.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def hold_native_mutex(name=NATIVE_MUTEX_NAME):
    """Create and keep a named Windows mutex for the process lifetime so the native
    host can see that a receiving service is alive. No-op on other platforms."""
    global _NATIVE_MUTEX
    if os.name != "nt" or _NATIVE_MUTEX:
        return _NATIVE_MUTEX
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            log("创建服务互斥体失败 err=%s" % ctypes.get_last_error())
            return None
        _NATIVE_MUTEX = handle      # never closed: released by the OS when the process exits
    except Exception as e:
        log("创建服务互斥体失败: %s" % e)
    return _NATIVE_MUTEX


def create_http_server(port, native):
    """Native mode binds strictly to the given port or exits with EXIT_PORT_IN_USE;
    standalone mode keeps the old pick_port fallback."""
    if native:
        try:
            return StrictHTTPServer(("127.0.0.1", port), Handler), port
        except OSError as e:
            log("原生模式端口 %d 无法绑定（%s），退出码 %d 交由宿主换端口重试" % (port, e, EXIT_PORT_IN_USE))
            raise SystemExit(EXIT_PORT_IN_USE)
    port = pick_port(port)
    return DesktopHTTPServer(("127.0.0.1", port), Handler), port


def main():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    global PORT
    native = bool(os.environ.get("JST_NATIVE_PORT"))
    if native:
        hold_native_mutex()
    srv, PORT = create_http_server(PORT, native)
    srv.daemon_threads = False
    if _SERVICE_LEASE:
        _SERVICE_LEASE.publish(PORT)
    threading.Thread(target=watchdog, daemon=True).start()
    WATCH.start()
    CATALOG_SYNC.start()
    BACKUPS.start()
    UPDATER.start()
    REMOTE.service_port = PORT
    REMOTE.start_if_enabled()   # 默认关闭；仅在设置里启用后才监听 [::]
    log("版本 %s；服务已启动 http://127.0.0.1:%d" % (VERSION, PORT))
    log("关闭/最小化网页不会退出服务。退出前请等待保存或生成完成，再在此窗口按 Ctrl+C 或关闭此窗口。日志：data/logs/service.log")
    if os.environ.get("JST_NO_BROWSER") != "1":   # 回归测试时置 1，避免弹窗
        threading.Timer(0.6, lambda: open_browser(PORT)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("收到手动退出指令，等待正在处理的本机请求结束")
    finally:
        WATCH.stop.set()
        try:
            UPDATER.stop()
        except Exception:
            pass
        try:
            REMOTE.stop()
        except Exception:
            pass
        srv.server_close()
        log("本机服务已停止")
        if _SERVICE_LEASE:
            _SERVICE_LEASE.close()


if __name__ == "__main__":
    main()

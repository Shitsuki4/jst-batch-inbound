# -*- coding: utf-8 -*-
"""自动检查更新 / 下载 / SHA256 校验（只读访问 GitHub Release，不上传任何数据）。

设计要点：
- 版本号只认 ``desktop-vX.Y.Z`` 标签，按语义化版本比较；草稿和预发布一律忽略。
- 两个下载渠道：``github``（GitHub 原链接）与 ``proxy``（加速站 gh-proxy.com，格式与
  CI 写进 Release 说明的链接一致：``https://gh-proxy.com/<GitHub 原地址>``）。
- 检查更新：短超时，按所选渠道优先、另一渠道兜底；结果缓存到 data/update-cache.json，
  自动检查每天最多一次，且全部在后台线程里进行，绝不阻塞启动。
- 下载：存到 data/updates/<版本>/，``.part`` 断点续传、失败自动重试；完成后必须与
  同渠道下载的 SHA256SUMS.txt 一致（Release 接口若带 asset digest 也必须一致）才算可用。
- 安装版有两种安装包：离线完整安装程序（自带 WebView2 / FFmpeg，约 500 MiB）和精简的升级安装程序
  （不带这两样，约 90 MiB）。Release 说明里由 CI 写一段 ``runtime-requirements`` 注释，记下这一版
  固定的运行组件文件哈希；本机正在用的运行组件（宿主通过 JST_RUNTIME_* 环境变量告知）与之完全一致时
  才下载升级安装程序，否则（组件版本变了、本机缺组件、说明里没有这段）一律下载离线完整安装程序。
  升级安装程序自己安装前也会再核对一遍，不一致就拒绝安装。
- 真正安装（启动安装程序 / 解压便携包）由桌面宿主完成；这里只在 prepare_install()
  里复核文件哈希并拒绝“入库任务进行中”的情况，录像中由宿主拒绝。
"""
import hashlib
import json
import os
import re
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.request

REPO = "Shitsuki4/jst-batch-inbound"
TAG_PREFIX = "desktop-v"
PROXY_PREFIX = "https://gh-proxy.com/"
CHANNELS = ("github", "proxy")
CHANNEL_LABELS = {"github": "GitHub 原链接", "proxy": "加速站 gh-proxy.com"}
DEFAULT_CHANNEL = "github"
CHECK_INTERVAL = 24 * 3600          # 自动检查：每天最多一次（成功后）
RETRY_AFTER_FAILURE = 3600          # 自动检查失败后至少隔 1 小时再试
CHECK_TIMEOUT = 8                   # 单个检查请求超时（秒）
DOWNLOAD_TIMEOUT = 30               # 下载单次读超时（秒）
DOWNLOAD_ATTEMPTS = 4
MAX_NOTES = 20000
USER_AGENT = "ReceivingAssistant-Updater"

_VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,6})$")
_TAG_RE = re.compile(r"^desktop-v(\d{1,4}\.\d{1,4}\.\d{1,6})$")
_ASSET_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_ORIGINS = ("https://github.com/", "https://api.github.com/")
_PROXY_BLOCK = re.compile(r"<!-- gh-proxy-links:start -->.*?<!-- gh-proxy-links:end -->\s*", re.S)
_RUNTIME_BLOCK = re.compile(r"<!-- runtime-requirements:\s*(\{.*?\})\s*-->\s*", re.S)
# 升级安装程序要求本机已有、且与这一版固定版本完全一致的运行组件文件（文件名 -> 运行时路径来源见 detect_runtime）
RUNTIME_FILES = ("ffmpeg.exe", "ffprobe.exe", "msedgewebview2.exe")


# ---------------------------------------------------------------- 纯函数（有单元测试）

def parse_version(text):
    """'3.0.3' / 'v3.0.3' / 'desktop-v3.0.3' -> (3, 0, 3)；预发布后缀、非法格式返回 None。"""
    s = str(text or "").strip()
    if s.startswith(TAG_PREFIX):
        s = s[len(TAG_PREFIX):]
    elif s[:1] in ("v", "V"):
        s = s[1:]
    m = _VERSION_RE.match(s)
    return tuple(int(x) for x in m.groups()) if m else None


def compare_versions(a, b):
    """语义化比较：a<b 返回 -1，相等 0，a>b 1。任一不可解析时抛 ValueError。"""
    va, vb = parse_version(a), parse_version(b)
    if va is None or vb is None:
        raise ValueError("版本号格式无效：%s / %s" % (a, b))
    return (va > vb) - (va < vb)


def is_newer(latest, current):
    try:
        return compare_versions(latest, current) > 0
    except ValueError:
        return False


def channel_url(channel, url):
    """按渠道生成实际请求地址。只接受 GitHub 原地址，加速站 = 前缀 + 原地址。"""
    if channel not in CHANNELS:
        raise ValueError("未知更新渠道：%s" % channel)
    url = str(url or "")
    if not url.startswith(_ALLOWED_ORIGINS) or any(c in url for c in "\r\n\t "):
        raise ValueError("只允许 GitHub 地址：%s" % url)
    return PROXY_PREFIX + url if channel == "proxy" else url


def other_channel(channel):
    return "proxy" if channel == "github" else "github"


def release_download_url(tag, name):
    if not _TAG_RE.match(str(tag or "")):
        raise ValueError("Release 标签无效：%s" % tag)
    if not _ASSET_RE.match(str(name or "")):
        raise ValueError("文件名无效：%s" % name)
    return "https://github.com/%s/releases/download/%s/%s" % (REPO, tag, name)


def release_page_url(tag):
    if not _TAG_RE.match(str(tag or "")):
        raise ValueError("Release 标签无效：%s" % tag)
    return "https://github.com/%s/releases/tag/%s" % (REPO, tag)


def asset_names(version):
    if parse_version(version) is None:
        raise ValueError("版本号格式无效：%s" % version)
    return {
        "installer": "ReceivingAssistant-%s-Offline-Setup.exe" % version,
        "upgrade": "ReceivingAssistant-%s-Upgrade-Setup.exe" % version,
        "deps": "ReceivingAssistant-Deps-%s-Setup.exe" % version,
        "portable": "jst-batch-inbound-desktop-%s-win-x64.zip" % version,
        "sums": "SHA256SUMS.txt",
    }


def asset_kind_for_mode(mode):
    """安装版 -> 离线完整安装程序（运行组件一致时由 UpdateHost 换成升级安装程序）；便携版 -> 便携 ZIP；
    其他（开发 / 未知）不提供下载。"""
    return {"installed": "installer", "portable": "portable"}.get(mode)


ASSET_LABELS = {"installer": "离线完整安装程序", "upgrade": "升级安装程序", "portable": "便携版 ZIP"}


def parse_runtime_requirements(body):
    """读 Release 说明里 CI 写的 ``<!-- runtime-requirements: {...} -->``；缺失或格式不对返回 None。"""
    m = _RUNTIME_BLOCK.search(str(body or ""))
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return None
    sha = d.get("sha256") if isinstance(d, dict) else None
    if not isinstance(sha, dict):
        return None
    out = {}
    for name in RUNTIME_FILES:
        v = str(sha.get(name) or "").strip().lower()
        if not _HEX64.match(v):
            return None
        out[name] = v
    return {"sha256": out, "webview2": str(d.get("webview2") or "")[:40], "ffmpeg": str(d.get("ffmpeg") or "")[:40]}


def detect_runtime():
    """安装版宿主告知的、本机正在用的运行组件文件（安装目录 runtime\\ 或运行依赖安装程序的共享目录）。"""
    webview = os.environ.get("JST_RUNTIME_WEBVIEW2") or ""
    return {"ffmpeg.exe": os.environ.get("JST_RUNTIME_FFMPEG") or "",
            "ffprobe.exe": os.environ.get("JST_RUNTIME_FFPROBE") or "",
            "msedgewebview2.exe": os.path.join(webview, "msedgewebview2.exe") if webview else ""}


def runtime_mismatch(required, actual):
    """比较 Release 要求的运行组件哈希与本机实际哈希（None 表示文件缺失）。一致返回空字符串，否则返回原因。"""
    if not required:
        return "这一版的发布说明里没有运行组件版本信息"
    missing = [n for n in RUNTIME_FILES if not (actual or {}).get(n)]
    if missing:
        return "本机没有找到运行组件（%s）" % "、".join(missing)
    if any(actual[n] != required["sha256"][n] for n in RUNTIME_FILES):
        want = "、".join(x for x in ("WebView2 " + required.get("webview2", "") if required.get("webview2") else "",
                                      "FFmpeg " + required.get("ffmpeg", "") if required.get("ffmpeg") else "") if x)
        return "这一版的运行组件%s和本机的不一样" % ("（%s）" % want if want else "")
    return ""


def parse_sha256sums(text):
    """解析 SHA256SUMS.txt（兼容 BOM、CRLF、``*name`` 二进制标记、反斜杠路径）。"""
    out = {}
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", "replace")
    for raw in str(text or "").replace("\ufeff", "").splitlines():
        line = raw.strip()
        m = re.match(r"^([0-9A-Fa-f]{64})\s+\*?(.+?)$", line)
        if not m:
            continue
        out[m.group(2).strip().replace("\\", "/")] = m.group(1).lower()
    return out


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify_file(path, expected):
    """文件存在且 SHA256 与 expected（64 位十六进制）一致时返回实际哈希，否则抛 ValueError。"""
    expected = str(expected or "").strip().lower()
    if not _HEX64.match(expected):
        raise ValueError("缺少有效的 SHA256 校验值")
    if not os.path.isfile(path):
        raise ValueError("更新文件不存在")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError("SHA256 校验失败（期望 %s…，实际 %s…）" % (expected[:12], actual[:12]))
    return actual


def _digest(asset):
    d = str((asset or {}).get("digest") or "")
    if d.lower().startswith("sha256:") and _HEX64.match(d[7:].lower()):
        return d[7:].lower()
    return ""


def normalize_release(rel):
    """GitHub release JSON -> 精简结构；不是合法正式版时返回 None。"""
    if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
        return None
    tag = str(rel.get("tag_name") or "")
    m = _TAG_RE.match(tag)
    if not m:
        return None
    version = m.group(1)
    assets = {}
    for a in rel.get("assets") or []:
        name = str((a or {}).get("name") or "")
        if _ASSET_RE.match(name):
            assets[name] = {"size": int(a.get("size") or 0), "digest": _digest(a)}
    body = str(rel.get("body") or "")
    notes = _RUNTIME_BLOCK.sub("", _PROXY_BLOCK.sub("", body)).strip()
    return {"version": version, "tag": tag, "name": str(rel.get("name") or tag)[:200],
            "notes": notes[:MAX_NOTES], "published_at": str(rel.get("published_at") or ""),
            "html_url": release_page_url(tag), "assets": assets,
            "runtime": parse_runtime_requirements(body)}


def pick_release(releases):
    """从 release 列表中挑出版本最高的正式 desktop 版（忽略草稿、预发布、其他标签）。"""
    best = None
    for rel in releases or []:
        n = normalize_release(rel)
        if n and (best is None or parse_version(n["version"]) > parse_version(best["version"])):
            best = n
    return best


def refusal_reason(recording=False, inbound_busy=""):
    """统一的拒绝更新理由（空字符串 = 可以继续）。"""
    if recording:
        return "正在录像，请先结束本单录像后再更新"
    if inbound_busy:
        return str(inbound_busy)
    return ""


def detect_version():
    """当前程序版本：宿主传入的 JST_APP_VERSION（来自 csproj）> app_version 模块 > 源码树中的 csproj。"""
    v = os.environ.get("JST_APP_VERSION", "").strip()
    if parse_version(v):
        return v
    try:
        from app_version import VERSION as APP_VERSION  # 后端唯一版本源（#12 app_version.py）
        if parse_version(APP_VERSION):
            return APP_VERSION
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    csproj = os.path.join(here, "..", "..", "src", "Receiving.Native.csproj")
    try:
        with open(csproj, encoding="utf-8") as f:
            m = re.search(r"<Version>\s*([0-9.]+)\s*</Version>", f.read())
        if m and parse_version(m.group(1)):
            return m.group(1)
    except OSError:
        pass
    return ""


def detect_mode():
    mode = os.environ.get("JST_INSTALL_MODE", "").strip().lower()
    return mode if mode in ("installed", "portable") else "dev"


# ---------------------------------------------------------------- 网络

_SSL = ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http_open(url, timeout, headers=None, follow_redirects=True):
    """返回 (status, headers, response)。调用方负责关闭 response。"""
    req = urllib.request.Request(url, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})))
    handlers = [urllib.request.HTTPSHandler(context=_SSL)]
    if not follow_redirects:
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    try:
        resp = opener.open(req, timeout=timeout)
        return resp.status, resp.headers, resp
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e


def _read_json(fetch, url, timeout):
    status, _, resp = fetch(url, timeout, {"Accept": "application/vnd.github+json"})
    try:
        if status != 200:
            raise IOError("HTTP %s" % status)
        raw = resp.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise IOError("响应过大")
        return json.loads(raw.decode("utf-8"))
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _short_error(e):
    if isinstance(e, urllib.error.URLError) and getattr(e, "reason", None) is not None:
        e = e.reason
    text = str(e) or e.__class__.__name__
    if "timed out" in text.lower():
        return "连接超时"
    return text[:160]


# ---------------------------------------------------------------- 主体

class UpdateHost:
    def __init__(self, data_dir, current_version=None, install_mode=None, fetch=None,
                 busy=None, clock=time.time, sleep=time.sleep, runtime=None):
        """fetch(url, timeout, headers, follow_redirects=True) -> (status, headers, response)，默认 http_open。"""
        self.data_dir = data_dir
        self.dir = os.path.join(data_dir, "updates")
        self.settings_path = os.path.join(data_dir, "update-settings.json")
        self.cache_path = os.path.join(data_dir, "update-cache.json")
        self.current = current_version if current_version is not None else detect_version()
        self.mode = install_mode or detect_mode()
        self.fetch = fetch or http_open
        self.busy = busy or (lambda: "")
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.RLock()
        self.checking = False
        self.cancel = threading.Event()
        self.stop_event = threading.Event()
        self.dl = {"state": "idle"}
        self._dl_thread = None
        self.runtime = runtime if runtime is not None else detect_runtime()
        self._runtime_hash_cache = {}

    # ---------- 持久化
    def _read(self, path, default):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else default
        except (OSError, ValueError):
            return default

    def _write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def settings(self):
        d = self._read(self.settings_path, {})
        channel = d.get("channel") if d.get("channel") in CHANNELS else DEFAULT_CHANNEL
        auto = d.get("auto_check")
        return {"channel": channel, "auto_check": True if auto is None else bool(auto)}

    def update_settings(self, body):
        body = body if isinstance(body, dict) else {}
        s = self.settings()
        if "channel" in body:
            if body["channel"] not in CHANNELS:
                raise ValueError("更新渠道只能是 GitHub 原链接或加速站 gh-proxy.com")
            s["channel"] = body["channel"]
        if "auto_check" in body:
            if not isinstance(body["auto_check"], bool):
                raise ValueError("自动检查开关无效")
            s["auto_check"] = body["auto_check"]
        with self.lock:
            self._write(self.settings_path, s)
        return self.status()

    def cache(self):
        return self._read(self.cache_path, {})

    # ---------- 状态
    def latest(self):
        latest = self.cache().get("latest")
        return latest if isinstance(latest, dict) and parse_version(latest.get("version")) else None

    def update_available(self):
        latest = self.latest()
        return bool(latest and parse_version(self.current) and is_newer(latest["version"], self.current))

    def _runtime_hashes(self):
        """本机运行组件文件的 SHA256（按路径 + 大小 + 修改时间缓存，FFmpeg 很大，不重复计算）。缺失为 None。"""
        out = {}
        for name in RUNTIME_FILES:
            path = str(self.runtime.get(name) or "")
            try:
                st = os.stat(path) if path else None
            except OSError:
                st = None
            if st is None or not os.path.isfile(path):
                out[name] = None
                continue
            key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
            with self.lock:
                cached = self._runtime_hash_cache.get(name)
            if cached and cached[0] == key:
                out[name] = cached[1]
                continue
            try:
                digest = sha256_file(path)
            except OSError:
                out[name] = None
                continue
            with self.lock:
                self._runtime_hash_cache[name] = (key, digest)
            out[name] = digest
        return out

    def upgrade_mismatch(self, latest):
        """升级安装程序能不能用：空字符串 = 可以；否则是要改用离线完整安装程序的原因。"""
        return runtime_mismatch((latest or {}).get("runtime"), self._runtime_hashes())

    def _asset_info(self, latest):
        kind = asset_kind_for_mode(self.mode)
        if not latest or not kind:
            return None
        names = asset_names(latest["version"])
        assets = latest.get("assets", {})
        note = ""
        if kind == "installer" and names["upgrade"] in assets:
            reason = self.upgrade_mismatch(latest)
            if not reason:
                kind = "upgrade"
            else:
                note = reason + "，所以下载离线完整安装程序（自带运行组件）。"
        name = names[kind]
        meta = assets.get(name)
        return {"kind": kind, "name": name, "label": ASSET_LABELS[kind], "note": note,
                "size": (meta or {}).get("size", 0), "listed": meta is not None}

    def _links(self, latest):
        if not latest:
            return {}
        names = asset_names(latest["version"])
        out = {"release": {c: channel_url(c, latest["html_url"]) for c in CHANNELS}}
        for kind in ("installer", "upgrade", "portable", "deps"):
            url = release_download_url(latest["tag"], names[kind])
            out[kind] = {c: channel_url(c, url) for c in CHANNELS}
        return out

    def status(self):
        s = self.settings()
        c = self.cache()
        latest = self.latest()
        with self.lock:
            dl = dict(self.dl)
            checking = self.checking
        return {
            "current_version": self.current or "",
            "install_mode": self.mode,
            "install_mode_label": {"installed": "安装版", "portable": "便携版"}.get(self.mode, "开发 / 源码运行"),
            # 便携版安装前要在确认框里给出当前数据所在文件夹（新版本解压到新文件夹，数据不会自动迁移）
            "data_dir": os.path.abspath(self.data_dir),
            "channel": s["channel"], "auto_check": s["auto_check"],
            "channels": [{"id": k, "label": CHANNEL_LABELS[k]} for k in CHANNELS],
            "checking": checking,
            "last_checked": c.get("checked_at") or 0, "last_attempt": c.get("attempted_at") or 0,
            "check_source": c.get("source") or "", "check_error": c.get("error") or "",
            "latest": {k: v for k, v in latest.items() if k != "assets"} if latest else None,
            "update_available": self.update_available(),
            "asset": self._asset_info(latest), "links": self._links(latest),
            "download": dl,
        }

    # ---------- 检查
    def due(self, now=None):
        s = self.settings()
        if not s["auto_check"]:
            return False
        now = self.clock() if now is None else now
        c = self.cache()
        checked, attempted = float(c.get("checked_at") or 0), float(c.get("attempted_at") or 0)
        if checked > now or attempted > now:      # 系统时间被调回过：立即允许
            return True
        return now - checked >= CHECK_INTERVAL and now - attempted >= RETRY_AFTER_FAILURE

    def _fetch_latest(self, channel):
        base = "https://api.github.com/repos/%s/releases" % REPO
        data = _read_json(self.fetch, channel_url(channel, base + "/latest"), CHECK_TIMEOUT)
        rel = normalize_release(data)
        if rel:
            return rel
        # 最新 release 不是 desktop 正式版（例如其他组件的发布）：退回列表里挑最高的 desktop 版。
        data = _read_json(self.fetch, channel_url(channel, base + "?per_page=30"), CHECK_TIMEOUT)
        rel = pick_release(data if isinstance(data, list) else [])
        if not rel:
            raise IOError("没有找到正式版 desktop Release")
        return rel

    def _fetch_redirect_tag(self):
        """API 被限流 / 屏蔽时的兜底：读 github.com/<repo>/releases/latest 的跳转地址拿到标签（无说明、无 digest）。"""
        status, headers, resp = self.fetch("https://github.com/%s/releases/latest" % REPO, CHECK_TIMEOUT,
                                           {}, follow_redirects=False)
        try:
            loc = (headers or {}).get("Location", "") if status in (301, 302, 303, 307, 308) else ""
        finally:
            try:
                resp.close()
            except Exception:
                pass
        m = re.search(r"/releases/tag/(desktop-v\d+\.\d+\.\d+)$", loc or "")
        if not m:
            raise IOError("无法从发布页跳转地址识别版本")
        rel = normalize_release({"tag_name": m.group(1), "assets": [], "body": ""})
        names = asset_names(rel["version"])
        rel["assets"] = {n: {"size": 0, "digest": ""} for n in names.values()}
        rel["notes"] = "（GitHub 接口暂不可用，未能获取更新说明；可打开发布页查看。）"
        return rel

    def check_now(self, force=False):
        """同步检查；返回 status()。自动检查（force=False）遵守每天一次的节流。"""
        with self.lock:
            if self.checking:
                return self.status()
            if not force and not self.due():
                return self.status()
            self.checking = True
        try:
            channel = self.settings()["channel"]
            errors = []
            rel = source = None
            for ch in (channel, other_channel(channel)):
                try:
                    rel, source = self._fetch_latest(ch), ch
                    break
                except Exception as e:
                    errors.append("%s：%s" % (CHANNEL_LABELS[ch], _short_error(e)))
            if rel is None:
                try:
                    rel, source = self._fetch_redirect_tag(), "github-page"
                except Exception as e:
                    errors.append("发布页：%s" % _short_error(e))
            c = self.cache()
            now = self.clock()
            c["attempted_at"] = now
            if rel is not None:
                c.update({"checked_at": now, "latest": rel, "source": source, "error": ""})
                if self.mode == "installed" and rel.get("runtime"):
                    try:
                        self._runtime_hashes()      # 在后台线程里先算好，status() 直接用缓存
                    except Exception:
                        pass
            else:
                c["error"] = "检查更新失败（" + "；".join(errors) + "）"
            with self.lock:
                self._write(self.cache_path, c)
        finally:
            with self.lock:
                self.checking = False
        return self.status()

    def check_async(self, force=True):
        with self.lock:
            if self.checking:
                return False
        threading.Thread(target=self._safe_check, args=(force,), daemon=True, name="update-check").start()
        return True

    def _safe_check(self, force):
        try:
            self.check_now(force)
        except Exception:
            pass

    def start(self, first_delay=20, interval=3600):
        """启动后台自动检查：首次延迟 first_delay 秒，之后每 interval 秒看一次是否到期。"""
        def loop():
            if self.stop_event.wait(first_delay):
                return
            while True:
                try:
                    if self.due():
                        self.check_now(False)
                except Exception:
                    pass
                if self.stop_event.wait(interval):
                    return
        threading.Thread(target=loop, daemon=True, name="update-auto-check").start()

    # ---------- 下载
    def _set(self, **kw):
        with self.lock:
            self.dl.update(kw)

    def start_download(self, channel=None):
        latest = self.latest()
        if not latest or not self.update_available():
            raise ValueError("当前没有可下载的新版本，请先检查更新")
        info = self._asset_info(latest)
        if not info:
            raise ValueError("开发 / 源码运行模式不提供自动下载，请到发布页手动下载")
        if not info["listed"]:
            raise ValueError("该版本的 Release 中没有 %s，请到发布页确认" % info["name"])
        channel = channel or self.settings()["channel"]
        if channel not in CHANNELS:
            raise ValueError("未知更新渠道")
        with self.lock:
            if self.dl.get("state") in ("downloading", "verifying"):
                return self.status()
            self.cancel.clear()
            self.dl = {"state": "downloading", "version": latest["version"], "name": info["name"],
                       "kind": info["kind"], "channel": channel, "received": 0,
                       "total": info["size"], "error": "", "path": "", "sha256": "", "attempt": 0}
            self._dl_thread = threading.Thread(target=self._download_worker,
                                               args=(latest, info, channel), daemon=True, name="update-download")
            self._dl_thread.start()
        return self.status()

    def cancel_download(self):
        self.cancel.set()
        return self.status()

    def _cleanup_other_versions(self, keep):
        if not os.path.isdir(self.dir):
            return
        for name in os.listdir(self.dir):
            p = os.path.join(self.dir, name)
            if name != keep and parse_version(name) and os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)

    def _expected_hash(self, latest, info, channel):
        sums_url = channel_url(channel, release_download_url(latest["tag"], "SHA256SUMS.txt"))
        last = None
        for attempt in range(3):
            if self.cancel.is_set():
                raise InterruptedError()
            try:
                status, _, resp = self.fetch(sums_url, DOWNLOAD_TIMEOUT, {})
                try:
                    if status != 200:
                        raise IOError("HTTP %s" % status)
                    text = resp.read(256 * 1024)
                finally:
                    resp.close()
                sums = parse_sha256sums(text)
                if info["name"] not in sums:
                    raise ValueError("SHA256SUMS.txt 中没有 %s，无法校验，已停止下载" % info["name"])
                expected = sums[info["name"]]
                digest = (latest.get("assets", {}).get(info["name"]) or {}).get("digest") or ""
                if digest and digest != expected:
                    raise ValueError("SHA256SUMS.txt 与 GitHub 记录的文件摘要不一致，可能被篡改，已停止下载")
                return expected
            except ValueError:
                raise
            except Exception as e:
                last = e
                self.sleep(min(2 * (attempt + 1), 6))
        raise IOError("下载 SHA256SUMS.txt 失败：%s" % _short_error(last))

    def _download_worker(self, latest, info, channel):
        try:
            version = latest["version"]
            self._cleanup_other_versions(version)
            folder = os.path.join(self.dir, version)
            os.makedirs(folder, exist_ok=True)
            final = os.path.join(folder, info["name"])
            part = final + ".part"
            expected = self._expected_hash(latest, info, channel)
            if os.path.isfile(final):
                self._set(state="verifying")
                try:
                    verify_file(final, expected)
                    size = os.path.getsize(final)
                    self._set(state="verified", path=final, sha256=expected, received=size, total=size)
                    return
                except ValueError:
                    os.remove(final)
            url = channel_url(channel, release_download_url(latest["tag"], info["name"]))
            last = None
            for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
                if self.cancel.is_set():
                    raise InterruptedError()
                self._set(attempt=attempt, state="downloading")
                try:
                    self._download_once(url, part)
                    last = None
                    break
                except InterruptedError:
                    raise
                except Exception as e:
                    last = e
                    if attempt < DOWNLOAD_ATTEMPTS:
                        self.sleep(min(2 ** attempt, 15))
            if last is not None:
                raise IOError("下载失败：%s" % _short_error(last))
            self._set(state="verifying")
            try:
                verify_file(part, expected)
            except ValueError as e:
                try:
                    os.remove(part)
                except OSError:
                    pass
                raise ValueError("%s；已删除损坏的文件，请重试或切换下载渠道" % e)
            os.replace(part, final)
            size = os.path.getsize(final)
            self._set(state="verified", path=final, sha256=expected, received=size, total=size, error="")
        except InterruptedError:
            self._set(state="cancelled", error="已取消下载（已下载部分保留，下次继续）")
        except Exception as e:
            msg = str(e) if isinstance(e, (ValueError, IOError)) else _short_error(e)
            self._set(state="failed", error=msg, suggest_channel=other_channel(channel))

    def _download_once(self, url, part):
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        headers = {"Range": "bytes=%d-" % have} if have else {}
        status, hdrs, resp = self.fetch(url, DOWNLOAD_TIMEOUT, headers)
        try:
            if status == 416 and have:
                return                      # 已完整，交给校验
            if status == 200:
                have, mode = 0, "wb"        # 服务器不支持续传：从头开始
            elif status == 206 and have:
                mode = "ab"
            else:
                raise IOError("HTTP %s" % status)
            length = int((hdrs or {}).get("Content-Length") or 0)
            total = have + length if length else 0
            self._set(received=have, total=total or self.dl.get("total", 0))
            with open(part, mode) as f:
                while True:
                    if self.cancel.is_set():
                        raise InterruptedError()
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    have += len(chunk)
                    self._set(received=have)
            if length and have < total:
                raise IOError("连接中断（%d/%d 字节）" % (have, total))
        finally:
            try:
                resp.close()
            except Exception:
                pass

    # ---------- 安装前
    def prepare_install(self, recording=False):
        reason = refusal_reason(recording, self.busy())
        if reason:
            raise ValueError(reason)
        with self.lock:
            dl = dict(self.dl)
        if dl.get("state") != "verified" or not dl.get("path"):
            raise ValueError("更新文件尚未下载并校验完成")
        path = os.path.abspath(dl["path"])
        root = os.path.abspath(self.dir) + os.sep
        if not path.startswith(root):
            raise ValueError("更新文件不在更新缓存目录内，已拒绝")
        verify_file(path, dl["sha256"])          # 安装前再复核一次，防止下载后被替换
        return {"path": path, "sha256": dl["sha256"], "kind": dl["kind"], "version": dl["version"]}

    def stop(self):
        self.stop_event.set()
        self.cancel.set()


def inbound_busy_reason(db_paths):
    """任一收货库存在“正在生成”的入库任务（status='creating'）时返回拒绝理由。只读打开，不建库。"""
    import sqlite3
    for p in db_paths:
        if not os.path.isfile(p):
            continue
        try:
            con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True, timeout=2)
        except sqlite3.Error:
            continue
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_job'").fetchone():
                continue
            n = con.execute("SELECT COUNT(*) FROM inbound_job WHERE status='creating'").fetchone()[0]
            if n:
                return "有 %d 个入库任务正在生成 / 结果未确认，请先在入库页面完成或核对后再更新" % n
        except sqlite3.Error:
            continue
        finally:
            con.close()
    return ""

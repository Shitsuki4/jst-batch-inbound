"""内置内网穿透（4.1.11）：小助手自己启动和看管 cpolar 与 Tailscale，远程管理页填好 key 就能用。

* Tailscale：安装包、升级安装程序和便携版都自带 backend\\ra-tailnet.exe。它是用 Tailscale 开源的
  tsnet 库（BSD-3-Clause）在 CI 里从源码编译的（desktop/tailnet），在本进程里加入用户自己的 tailnet：
  不装 Tailscale 客户端、不建虚拟网卡、不要管理员权限，只把 tailnet 上的一个 HTTPS 端口转到本机
  127.0.0.1:18481（远程管理页的 Tailscale 入口），收货服务 8765 等别的端口不会出现在 tailnet 里。
* cpolar：官方客户端是闭源软件，服务条款里没有允许再分发，所以不打进安装包：第一次启用时由小助手
  从 cpolar 官网下载固定版本的 Windows 便携版 zip，核对 zip 和 cpolar.exe 的 SHA-256（写死在下面），
  对不上就删掉不用；以后每次启动前都再核对一遍。放在数据目录 tools\\cpolar\\<版本>\\。

两个子进程都放进 Windows 作业对象（关闭即结束）：小助手退出、崩溃或被卸载程序结束时，它们跟着退出，
不会留下孤儿进程。开机自启跟着小助手走：小助手启动且远程管理页已启用，就自动拉起已配置的穿透。

cpolar 免费版的公网地址 24 小时内会变：这里每 5 秒从 cpolar 本机接口读一次当前地址，变了就更新状态、
记进远程访问日志，设置页始终显示最新地址；cpolar 掉线或退出会自动重启（退避最长 60 秒）。
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

CPOLAR_VERSION = "3.3.12"
CPOLAR_URL = "https://www.cpolar.com/static/downloads/releases/3.3.12/cpolar-stable-windows-amd64.zip"
CPOLAR_ZIP_SHA256 = "92af567efb1b10038da742dff872da99a7cc277e22b804d55762e67bdeceb25e"
CPOLAR_EXE_SHA256 = "ae4a871c33f8cd3a6ad118247b6205219b2008425d018f56cb9feec8aca90cea"
CPOLAR_EXE = "cpolar.exe"
CPOLAR_MAX_ZIP = 64 * 1024 * 1024
TAILNET_EXE = "ra-tailnet.exe" if os.name == "nt" else "ra-tailnet"
DEFAULT_TAILNET_LOCAL_PORT = 18481   # Tailscale 入口：只绑 127.0.0.1，给 ra-tailnet 转发
DEFAULT_TAILNET_LISTEN = 443         # tailnet 上的 HTTPS 端口（只在 tailnet 内可达）
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
CPOLAR_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")
TS_AUTHKEY = re.compile(r"^tskey-[A-Za-z0-9_\-]{10,200}$")
URL_IN_LOG = re.compile(r"https://[A-Za-z0-9.\-]+\.cpolar\.(?:top|cn|io|com)\b")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- 密钥保存：Windows 用 DPAPI（只有当前 Windows 账户能解开） ----------
def protect(text):
    if not text:
        return ""
    raw = text.encode("utf-8")
    if os.name == "nt":
        import login_memory
        return "dpapi:" + base64.b64encode(login_memory._dpapi(raw)).decode("ascii")
    return "plain:" + base64.b64encode(raw).decode("ascii")   # 非 Windows 只用于测试


def unprotect(blob):
    if not isinstance(blob, str) or ":" not in blob:
        return ""
    kind, data = blob.split(":", 1)
    try:
        raw = base64.b64decode(data)
        if kind == "dpapi" and os.name == "nt":
            import login_memory
            raw = login_memory._dpapi(raw, decrypt=True)
        elif kind != "plain":
            return ""
        return raw.decode("utf-8")
    except Exception:
        return ""


# ---------- 子进程：Windows 作业对象，小助手退出就一起结束 ----------
_JOB = None


def _kill_on_close_job():
    global _JOB
    if os.name != "nt":
        return None
    if _JOB is None:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        _JOB = (k32, job)
    return _JOB


def spawn(cmd, env=None, cwd=None):
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=env, cwd=cwd, **kw)
    job = _kill_on_close_job()
    if job:
        import ctypes
        from ctypes import wintypes
        k32, h = job
        try:
            k32.AssignProcessToJobObject(wintypes.HANDLE(h), wintypes.HANDLE(int(p._handle)))
        except Exception:
            pass
    return p


def stop_process(p, timeout=5):
    if not p:
        return
    try:
        if p.stdin:
            p.stdin.close()   # ra-tailnet 看到 stdin 关闭会自己退出
    except Exception:
        pass
    try:
        p.wait(timeout=1.5)
        return
    except Exception:
        pass
    try:
        p.terminate()
        p.wait(timeout=timeout)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


class _Agent:
    """一个受看管的子进程：退出就按 5→10→20→40→60 秒退避重启，直到 stop()。"""
    name = ""
    launcher = ()      # 测试用：在命令前加解释器（比如 [sys.executable]）

    def __init__(self, log):
        self.log = log
        self.lock = threading.RLock()
        self.proc = None
        self.want = False
        self.gen = 0
        self.state = "off"
        self.error = ""
        self.error_code = ""
        self.since = 0.0
        self.restarts = 0

    def set_state(self, state, error="", code=""):
        with self.lock:
            changed = state != self.state or error != self.error
            self.state, self.error, self.error_code = state, error, code
            if changed:
                self.since = time.time()
                if error:
                    self.log("%s：%s %s" % (self.name, code, error))

    def fatal(self):
        """这次退出是否不该自动重启（比如 key 无效）。"""
        return False

    def start(self):
        with self.lock:
            if self.want:
                return
            self.want = True
            self.gen += 1
            gen = self.gen
        threading.Thread(target=self._run, args=(gen,), daemon=True, name="tunnel-" + self.name).start()

    def stop(self):
        with self.lock:
            self.want = False
            self.gen += 1
            p, self.proc = self.proc, None
        stop_process(p)
        self.set_state("off")

    def alive(self, gen):
        with self.lock:
            return self.want and gen == self.gen

    def _run(self, gen):
        delay = 5
        while self.alive(gen):
            try:
                cmd, env = self.prepare()
            except _Fatal as e:
                self.set_state("error", str(e), e.code)
                with self.lock:
                    if gen == self.gen:
                        self.want = False
                return
            except Exception as e:
                self.set_state("error", str(e), getattr(e, "code", "") or self.default_code)
                cmd = None
            if cmd and self.alive(gen):
                started = time.monotonic()
                try:
                    p = spawn(list(self.launcher) + cmd, env=env)
                except OSError as e:
                    self.set_state("error", "无法启动：%s" % e, self.default_code)
                else:
                    with self.lock:
                        if not self.alive(gen):
                            stop_process(p)
                            return
                        self.proc = p
                    self.on_started(p, gen)
                    for raw in iter(p.stdout.readline, b""):
                        if not self.alive(gen):
                            break
                        self.on_line(raw.decode("utf-8", "replace").strip(), gen)
                    try:
                        rc = p.wait(timeout=5)
                    except Exception:
                        rc = None
                    with self.lock:
                        if self.proc is p:
                            self.proc = None
                    if not self.alive(gen):
                        return
                    if self.fatal():
                        with self.lock:
                            if gen == self.gen:
                                self.want = False
                        return
                    if self.state != "error":
                        self.set_state("error", "%s 意外退出（代码 %s），%d 秒后重启" % (self.name, rc, delay), self.default_code)
                    if time.monotonic() - started > 300:
                        delay = 5
            self.restarts += 1
            end = time.monotonic() + delay
            while time.monotonic() < end and self.alive(gen):
                time.sleep(0.2)
            delay = min(delay * 2, 60)

    # 子类实现
    default_code = "E-RMT-1120"

    def prepare(self):
        raise NotImplementedError

    def on_started(self, p, gen):
        pass

    def on_line(self, line, gen):
        pass


class _Fatal(Exception):
    def __init__(self, msg, code):
        Exception.__init__(self, msg)
        self.code = code


class CpolarAgent(_Agent):
    name = "cpolar"
    default_code = "E-RMT-1113"

    def __init__(self, log, tools_dir, run_dir, spec=None):
        _Agent.__init__(self, log)
        self.tools_dir = tools_dir
        self.run_dir = run_dir
        self.spec = dict(version=CPOLAR_VERSION, url=CPOLAR_URL, zip_sha256=CPOLAR_ZIP_SHA256,
                         exe_sha256=CPOLAR_EXE_SHA256, exe=CPOLAR_EXE)
        self.spec.update(spec or {})
        self.token = ""
        self.local_port = 0
        self.region = ""
        self.inspect_port = 0
        self.public_url = ""
        self.http_url = ""
        self.last_url = ""
        self.url_since = 0.0
        self.auth_failed = False
        self.download = {"state": "", "done": 0, "total": 0}

    # ---------- 二进制：下载 + 校验 ----------
    def exe_path(self):
        return os.path.join(self.tools_dir, "cpolar", self.spec["version"], self.spec["exe"])

    def installed(self):
        p = self.exe_path()
        try:
            return os.path.isfile(p) and _sha256(p) == self.spec["exe_sha256"]
        except OSError:
            return False

    def ensure_binary(self):
        exe = self.exe_path()
        if os.path.isfile(exe):
            if _sha256(exe) == self.spec["exe_sha256"]:
                return exe
            self.log("cpolar：本地 %s 校验值不对，删掉重新下载" % exe)
            os.remove(exe)
        os.makedirs(os.path.dirname(exe), exist_ok=True)
        self.set_state("downloading")
        self.download = {"state": "downloading", "done": 0, "total": 0}
        fd, tmp = tempfile.mkstemp(prefix="cpolar-", suffix=".zip", dir=os.path.dirname(exe))
        os.close(fd)
        try:
            req = urllib.request.Request(self.spec["url"], headers={"User-Agent": "ReceivingAssistant"})
            h = hashlib.sha256()
            try:
                with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    self.download["total"] = total
                    while True:
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        self.download["done"] += len(chunk)
                        if self.download["done"] > CPOLAR_MAX_ZIP:
                            raise _Fatal("下载内容过大，已丢弃", "E-RMT-1111")
                        h.update(chunk)
                        f.write(chunk)
            except _Fatal:
                raise
            except Exception as e:
                err = Exception("从 cpolar 官网下载客户端失败：%s（地址 %s）" % (e, self.spec["url"]))
                err.code = "E-RMT-1110"
                raise err
            got = h.hexdigest()
            if got != self.spec["zip_sha256"]:
                raise _Fatal("下载的 cpolar 安装包校验值不对（得到 %s，应为 %s），已删除，没有运行" % (got[:16], self.spec["zip_sha256"][:16]), "E-RMT-1111")
            with zipfile.ZipFile(tmp) as z:
                names = [n for n in z.namelist() if os.path.basename(n).lower() == self.spec["exe"].lower()]
                if len(names) != 1:
                    raise _Fatal("cpolar 安装包里没找到 %s" % self.spec["exe"], "E-RMT-1111")
                part = exe + ".part"
                with z.open(names[0]) as src, open(part, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            if _sha256(part) != self.spec["exe_sha256"]:
                os.remove(part)
                raise _Fatal("解压出的 cpolar.exe 校验值不对，已删除，没有运行", "E-RMT-1111")
            os.replace(part, exe)
            if os.name != "nt":
                os.chmod(exe, 0o755)
            self.log("cpolar：已从官网下载 %s 并核对 SHA-256" % self.spec["version"])
            self.download["state"] = "done"
            return exe
        except Exception:
            self.download["state"] = "failed"
            raise
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ---------- 启动 ----------
    def configure(self, token, local_port, region=""):
        self.token, self.local_port, self.region = token, local_port, region

    def prepare(self):
        if not self.token:
            raise _Fatal("还没填 cpolar 的 Authtoken", "E-RMT-1112")
        exe = self.ensure_binary()
        self.auth_failed = False
        self.public_url = self.http_url = ""
        os.makedirs(self.run_dir, exist_ok=True)
        conf = os.path.join(self.run_dir, "cpolar.yml")
        with open(conf, "w", encoding="utf-8") as f:
            f.write("authtoken: %s\n" % self.token)
        self.inspect_port = _free_port()
        cmd = [exe, "http", "-config=" + conf, "-log=stdout", "-log-level=INFO", "-daemon=off", "-dashboard=off",
               "-inspect-addr=127.0.0.1:%d" % self.inspect_port]
        if self.region:
            cmd.append("-region=" + self.region)
        cmd.append("127.0.0.1:%d" % self.local_port)
        self.set_state("starting")
        return cmd, None

    def on_started(self, p, gen):
        threading.Thread(target=self._poll, args=(gen, p), daemon=True, name="cpolar-url").start()

    def on_line(self, line, gen):
        low = line.lower()
        if "auth failed" in low or "authtoken" in low and ("invalid" in low or "fail" in low):
            self.auth_failed = True
            self.set_state("error", "cpolar 拒绝了这个 Authtoken（请到 dashboard.cpolar.com 的“验证”页重新复制）", "E-RMT-1112")
            with self.lock:
                p = self.proc
            stop_process(p, timeout=2)
            return
        m = URL_IN_LOG.search(line)
        if m and not self.public_url:
            self._set_url(m.group(0), "")

    def fatal(self):
        return self.auth_failed

    def _poll(self, gen, p):
        while self.alive(gen) and p.poll() is None:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/tunnels" % self.inspect_port, timeout=3) as r:
                    data = json.loads(r.read(1024 * 1024).decode("utf-8", "replace"))
                https, http = pick_urls(data)
                if https or http:
                    self._set_url(https or http, http if https else "")
            except Exception:
                pass
            for _ in range(25 if self.public_url else 5):   # 拿到地址前每秒查一次，之后每 5 秒
                if not self.alive(gen):
                    return
                time.sleep(0.2)

    def _set_url(self, url, http):
        with self.lock:
            if url != self.public_url:
                old = self.last_url
                self.public_url, self.url_since, self.last_url = url, time.time(), url
                self.log("cpolar 公网地址%s：%s" % ("已变为" if old and old != url else "", url))
            self.http_url = http
        self.set_state("online")

    def stop(self):
        _Agent.stop(self)
        self.public_url = self.http_url = ""
        try:
            os.remove(os.path.join(self.run_dir, "cpolar.yml"))   # 不运行时不留明文 token
        except OSError:
            pass

    def status(self):
        return {"state": self.state, "error": self.error, "error_code": self.error_code,
                "public_url": self.public_url, "http_url": self.http_url,
                "url_since": int(self.url_since) if self.public_url else 0,
                "installed": self.installed() if self.state in ("off", "error") else os.path.isfile(self.exe_path()),
                "version": self.spec["version"], "download": dict(self.download),
                "source": self.spec["url"], "sha256": self.spec["exe_sha256"]}


def pick_urls(data):
    """从 cpolar 本机接口 /api/tunnels 的返回里挑出 https 和 http 公网地址。"""
    https = http = ""
    items = data.get("tunnels") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = list(items.values())
    for t in items if isinstance(items, list) else []:
        u = t.get("public_url") if isinstance(t, dict) else None
        if not isinstance(u, str) or len(u) > 200 or not re.match(r"^https?://[A-Za-z0-9.\-:]+/?$", u):
            continue
        if u.startswith("https://") and not https:
            https = u.rstrip("/")
        elif u.startswith("http://") and not http:
            http = u.rstrip("/")
    return https, http


def _free_port():
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


class TailnetAgent(_Agent):
    name = "Tailscale"
    default_code = "E-RMT-1121"

    def __init__(self, log, exe, state_dir):
        _Agent.__init__(self, log)
        self.exe = exe
        self.state_dir = state_dir
        self.authkey = ""
        self.hostname = "receiving-assistant"
        self.listen = DEFAULT_TAILNET_LISTEN
        self.local_port = DEFAULT_TAILNET_LOCAL_PORT
        self.info = {}
        self.on_logged_in = None

    def configure(self, authkey, hostname, listen, local_port):
        self.authkey, self.hostname, self.listen, self.local_port = authkey, hostname, listen, local_port

    def available(self):
        return bool(self.exe) and os.path.isfile(self.exe)

    def prepare(self):
        if not self.available():
            raise _Fatal("安装目录里缺少 %s，请重新安装小助手" % TAILNET_EXE, "E-RMT-1122")
        env = dict(os.environ)
        env.pop("TS_AUTHKEY", None)
        if self.authkey:
            env["TS_AUTHKEY"] = self.authkey
        self.set_state("starting")
        return [self.exe, "--state-dir", self.state_dir, "--hostname", self.hostname,
                "--listen", str(self.listen), "--target", "127.0.0.1:%d" % self.local_port], env

    def on_line(self, line, gen):
        try:
            d = json.loads(line)
        except ValueError:
            return
        if not isinstance(d, dict):
            return
        self.info = d
        st = d.get("state")
        if st == "Running":
            self.set_state("online")
            if self.authkey and self.on_logged_in:
                self.on_logged_in()      # 登录一次以后节点状态里就有身份了，不再保存 auth key
        elif st == "NeedsLogin":
            self.set_state("needs_login")
        elif st == "NeedsMachineAuth":
            self.set_state("needs_approval")
        elif st == "Error":
            self.set_state("error", str(d.get("error") or "未知错误")[:300], "E-RMT-1121")
        elif st:
            self.set_state("starting")

    def logout(self):
        """从 tailnet 注销这台电脑并删掉节点状态。"""
        self.stop()
        msg = ""
        if self.available() and os.path.isdir(self.state_dir):
            try:
                kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
                r = subprocess.run(list(self.launcher) + [self.exe, "--state-dir", self.state_dir, "--logout"], stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=30, **kw)
                if r.returncode != 0:
                    msg = "Tailscale 没能注销这台设备（可以到 login.tailscale.com/admin/machines 手动删除）"
            except Exception as e:
                msg = "Tailscale 没能注销这台设备：%s" % e
        shutil.rmtree(self.state_dir, ignore_errors=True)
        self.info = {}
        self.log("Tailscale：已退出登录并删除本机节点状态" + ("；" + msg if msg else ""))
        return msg

    def status(self):
        d = self.info or {}
        ips = [ip for ip in d.get("ips") or [] if isinstance(ip, str)]
        dns = d.get("dns_name") if isinstance(d.get("dns_name"), str) else ""
        suffix = "" if self.listen == 443 else ":%d" % self.listen
        urls = []
        if self.state == "online":
            if dns:
                urls.append("https://%s%s/" % (dns, suffix))
            urls += ["https://%s%s/" % (ip if ":" not in ip else "[%s]" % ip, suffix) for ip in ips if ":" not in ip]
        auth = d.get("auth_url") if isinstance(d.get("auth_url"), str) and d.get("auth_url", "").startswith("https://login.tailscale.com/") else ""
        return {"state": self.state, "error": self.error, "error_code": self.error_code,
                "available": self.available(), "auth_url": auth if self.state == "needs_login" else "",
                "ips": ips, "dns_name": dns, "tailnet": d.get("tailnet") or "", "urls": urls,
                "cert_mode": d.get("cert_mode") or "", "cert_sha256": d.get("cert_sha256") or "",
                "listen": self.listen, "logged_in_before": os.path.isfile(os.path.join(self.state_dir, "tailscaled.state")),
                "version": d.get("version") or ""}


def default_tailnet_exe():
    """ra-tailnet 和 Receiving.Service.exe 放在同一个 backend 目录；源码运行时可以用 JST_TAILNET_EXE 指定。"""
    env = os.environ.get("JST_TAILNET_EXE", "")
    if env:
        return env
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, TAILNET_EXE)

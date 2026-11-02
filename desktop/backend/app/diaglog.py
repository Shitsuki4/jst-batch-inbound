# -*- coding: utf-8 -*-
"""诊断日志：结构化、可轮转、不记敏感信息。

每行一条 JSON（diagnostic.jsonl），字段：
  ts 时间（本地时区 ISO）、level、code 错误码、op 操作（接口路径或动作名）、page 页面、
  rid 日志编号（报错弹窗里显示的那个）、msg、po 采购单号、outer_po 外部单号、tracking 物流单号、
  sid / batch_id、exc 异常类型、trace 简短堆栈（仅程序内部错误）、extra。

位置：<数据目录>/logs/diagnostic.jsonl，单文件满 2 MB 轮转，保留 5 个旧文件（最多约 12 MB）。
安装版数据目录是 %LOCALAPPDATA%\\ReceivingAssistant\\v3\\data，便携版是便携文件夹\\data。
永远不记密码、令牌、Cookie、聚水潭会话与签名：redact() 按字段名和内容两层过滤。
"""
import contextvars
import datetime
import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import uuid
import zipfile

MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 5
FILE_NAME = "diagnostic.jsonl"

_lock = threading.Lock()
_dir = None
_request_id = contextvars.ContextVar("jst_request_id", default="")

SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|pwd|token|secret|cookie|session|authorization|auth_?key|sign(ature)?|"
    r"viewstate|u_?id_?key|access_?key|api_?key|sms_?code|verify_?code|captcha|credential|private)", re.I)
# 内容层：即使混在普通文字里也遮掉
SENSITIVE_TEXT = [
    (re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9]{20,})"), "***"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    (re.compile(r"(?i)((?:password|passwd|pwd|token|secret|cookie|session|sign|signature|u_lid|u_id|u_co_id|"
                r"__viewstate|jt_?token|access_token|refresh_token|sms_code|code_key)\s*[=:]\s*)"
                r"(\"[^\"]*\"|'[^']*'|[^\s&;,\"']+)"), r"\1***"),
    (re.compile(r"(https?://[^\s?#\"']+)\?[^\s\"']*"), r"\1?…"),
]
REDACTED = "***"


def configure(data_dir):
    """设置日志目录（数据目录下的 logs）。测试里可以指向临时目录。"""
    global _dir
    _dir = os.path.join(data_dir, "logs") if data_dir else None
    return _dir


def log_dir():
    return _dir


def log_path():
    return os.path.join(_dir, FILE_NAME) if _dir else None


def new_request_id():
    rid = uuid.uuid4().hex[:10]
    _request_id.set(rid)
    return rid


def set_request_id(rid):
    _request_id.set(rid or "")


def request_id():
    return _request_id.get()


def redact_text(text):
    text = str(text)
    for rx, rep in SENSITIVE_TEXT:
        text = rx.sub(rep, text)
    return text


def redact(value, depth=0):
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if SENSITIVE_KEY.search(str(k)):
                out[str(k)] = REDACTED
            else:
                out[str(k)] = redact(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    s = redact_text(value)
    return s if len(s) <= 2000 else s[:2000] + "…"


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _rotate(path):
    try:
        if os.path.getsize(path) < MAX_BYTES:
            return
    except OSError:
        return
    base, ext = os.path.splitext(path)
    for i in range(BACKUPS, 0, -1):
        src = path if i == 1 else "%s.%d%s" % (base, i - 1, ext)
        dst = "%s.%d%s" % (base, i, ext)
        try:
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                os.replace(src, dst)
        except OSError:
            pass


def write(level, op, msg="", code="", page="", exc=None, **fields):
    """写一条日志；任何异常都吞掉，日志绝不影响业务。返回写入的记录（方便测试）。"""
    rec = {"ts": _now(), "level": str(level).upper(), "code": code or "", "op": str(op or ""),
           "page": str(page or ""), "rid": fields.pop("rid", None) or request_id(), "msg": str(msg or "")}
    for key in ("po", "outer_po", "tracking", "sid", "batch_id", "source", "status"):
        v = fields.pop(key, None)
        if v not in (None, "", 0):
            rec[key] = v
    if exc is not None:
        rec["exc"] = type(exc).__name__
        if fields.pop("with_trace", False):
            rec["trace"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-6:])
    fields.pop("with_trace", None)
    if fields:
        rec["extra"] = fields
    rec = redact(rec)
    path = log_path()
    if not path:
        return rec
    try:
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _lock:
            os.makedirs(_dir, exist_ok=True)
            _rotate(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
    return rec


def info(op, msg="", **fields):
    return write("INFO", op, msg, **fields)


def warn(op, msg="", **fields):
    return write("WARN", op, msg, **fields)


def error(op, msg="", **fields):
    return write("ERROR", op, msg, **fields)


def files():
    """日志目录里可以放进诊断包的文件（不含数据库与 Cookie）。"""
    if not _dir or not os.path.isdir(_dir):
        return []
    out = []
    for name in sorted(os.listdir(_dir)):
        low = name.lower()
        if low.endswith((".jsonl", ".log", ".txt")) or re.search(r"\.log\.\d+$", low):
            p = os.path.join(_dir, name)
            if os.path.isfile(p):
                out.append(p)
    return out


def tail(limit=200, code="", rid="", level="", text=""):
    """从最新的日志往回取记录（含桌面程序写的 native-diagnostic.jsonl）。"""
    limit = max(1, min(int(limit or 200), 2000))
    want = []
    paths = [p for p in files() if p.lower().endswith(".jsonl")]
    rows = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    rows.sort(key=lambda r: str(r.get("ts") or ""))
    for r in reversed(rows):
        if code and r.get("code") != code:
            continue
        if rid and r.get("rid") != rid:
            continue
        if level and str(r.get("level")).upper() != level.upper():
            continue
        if text and text not in json.dumps(r, ensure_ascii=False):
            continue
        want.append(r)
        if len(want) >= limit:
            break
    return want


def environment(extra=None):
    env = {
        "generated_at": _now(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "log_dir": _dir,
        "timezone": time.strftime("%z"),
    }
    if extra:
        env.update(extra)
    return redact(env)


def export_zip(dest_dir, env=None, codes=None, extra_files=None):
    """打包诊断信息：日志 + 版本与环境 + 错误码表。不包含数据库、Cookie、密码。"""
    os.makedirs(dest_dir, exist_ok=True)
    name = "收货小助手诊断包_%s.zip" % time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(dest_dir, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files() + list(extra_files or []):
            if not p or not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    data = f.read()
                z.writestr("logs/" + os.path.basename(p), redact_text(data))
            except OSError:
                continue
        z.writestr("environment.json", json.dumps(environment(env), ensure_ascii=False, indent=2))
        if codes is not None:
            z.writestr("error-codes.json", json.dumps(codes, ensure_ascii=False, indent=2))
        z.writestr("README.txt", "收货小助手诊断包\r\n"
                   "logs/diagnostic*.jsonl：本机收货服务的诊断日志（每行一条 JSON，按 rid 日志编号或 code 错误码搜索）\r\n"
                   "logs/native-diagnostic*.jsonl：桌面程序（窗口、录像、弹窗）的诊断日志\r\n"
                   "environment.json：版本与运行环境；error-codes.json：错误码表\r\n"
                   "不包含数据库、密码、Cookie 和聚水潭会话。\r\n")
    return path

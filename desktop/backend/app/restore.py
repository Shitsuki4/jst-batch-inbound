"""Backup listing / verification / staged restore. Pure module: no server wiring.

Flow: stage() copies a verified backup to data/restore-pending; apply_pending()
runs at the next startup (before any DB is opened), swapping DBs under a
journal so that a failure or crash is always rolled back.
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import time
import zipfile

FORMAT = 1
DB_NAMES = ("jst.db", "catalog.db", "unplanned.db", "purchase_watch.db", "joint_receipts.db", "learned_codes.db")
# 备份中没有这些库时（如旧版备份不含未知条码学习数据）保留当前数据，而不是把当前库移走。
KEEP_IF_ABSENT = ("catalog.db", "learned_codes.db")
# 操作记录库会进备份（审计用），但恢复时始终保留当前操作记录，不用备份覆盖。
KEEP_CURRENT_NAMES = ("operation_log.db",)
ALLOWED_NAMES = DB_NAMES + KEEP_CURRENT_NAMES
SIDECARS = ("-wal", "-shm", "-journal")
ZIP_WHITELIST = set(ALLOWED_NAMES) | {"manifest.json"}
REASON_LABELS = {"manual": "手动备份", "daily": "每日自动", "pre-upgrade": "升级前自动",
                 "pre-restore": "恢复前自动备份"}
PENDING = "restore-pending"
PENDING_TMP = "restore-pending.tmp"
JOURNAL = "restore-journal.json"
RESULT = "restore-result.json"

_FAULT = None  # test hook: callable(step_name) invoked after each file move


class RestoreError(Exception):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return  # Windows: directories cannot be opened
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_json_atomic(path, obj):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _load_manifest(backup_dir):
    try:
        return json.loads((Path(backup_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RestoreError("无法读取 manifest.json：%s" % e)


def list_backups(backups_dir):
    root = Path(backups_dir)
    out = []
    if not root.is_dir():
        return out
    for mp in sorted(root.glob("*/manifest.json"), reverse=True):
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        files = m.get("files") or []
        created = m.get("created") or 0
        reason = m.get("reason", "")
        out.append({
            "directory": str(mp.parent),
            "created": created,
            "time": datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S") if created else "",
            "reason": reason,
            "reason_label": REASON_LABELS.get(reason, reason or "未知"),
            "version": m.get("version", ""),
            "format": m.get("format", 0),
            "complete": bool(m.get("complete")),
            "size": sum(int(f.get("bytes") or 0) for f in files),
            "dbs": [f.get("name") for f in files],
        })
    return out


def verify(backup_dir):
    """Return {"ok", "errors", "manifest"}; never raises for bad backups."""
    d = Path(backup_dir)
    errors = []
    try:
        m = _load_manifest(d)
    except RestoreError as e:
        return {"ok": False, "errors": [str(e)], "manifest": None}
    fmt = m.get("format", 0)
    if not isinstance(fmt, int) or fmt > FORMAT:
        errors.append("备份格式 %s 高于本程序支持的 %d，请先升级程序" % (fmt, FORMAT))
        return {"ok": False, "errors": errors, "manifest": m}
    if not m.get("complete"):
        errors.append("备份未完成（manifest complete=false）")
    files = m.get("files") or []
    if not files:
        errors.append("备份不含任何数据库")
    seen = set()
    for f in files:
        name = f.get("name", "")
        if name not in ALLOWED_NAMES or name in seen:
            errors.append("非法或重复的文件名：%r" % name)
            continue
        seen.add(name)
        p = d / name
        if not p.is_file():
            errors.append("缺少文件：" + name)
            continue
        if _sha256(p) != f.get("sha256"):
            errors.append("校验和不一致：" + name)
            continue
        try:
            with closing(sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)) as c:
                r = c.execute("PRAGMA integrity_check").fetchone()[0]
            if r != "ok":
                errors.append("完整性检查失败：%s（%s）" % (name, r))
        except sqlite3.Error as e:
            errors.append("无法打开数据库 %s：%s" % (name, e))
    return {"ok": not errors, "errors": errors, "manifest": m}


def _counts(path):
    if not Path(path).is_file():
        return None
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: c.execute('SELECT COUNT(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0] for t in tables}


def _io_ids(path):
    if not Path(path).is_file():
        return set()
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as c:
            return {r[0] for r in c.execute("SELECT io_id FROM inbound_job WHERE io_id<>''")}
    except sqlite3.Error:
        return set()


def preview(backup_dir, data_dir):
    """Row counts (backup vs current) and inbound io_ids that a restore would drop."""
    b, d = Path(backup_dir), Path(data_dir)
    m = _load_manifest(b)
    names = [f.get("name") for f in m.get("files") or [] if f.get("name") in DB_NAMES]
    dbs = {}
    for n in DB_NAMES:
        in_backup = n in names
        if not in_backup and not (d / n).is_file():
            continue
        dbs[n] = {"in_backup": in_backup,
                  "kept_current": (not in_backup and n in KEEP_IF_ABSENT),
                  "backup": _counts(b / n) if in_backup else None,
                  "current": _counts(d / n)}
    missing = sorted(_io_ids(d / "jst.db") - _io_ids(b / "jst.db")) if "jst.db" in names else sorted(_io_ids(d / "jst.db"))
    return {"dbs": dbs, "missing_inbound_io_ids": missing}


def _safe_zip_members(zf):
    picked = {}
    for info in zf.infolist():
        raw = info.filename.replace("\\", "/")
        if info.is_dir():
            continue
        p = PurePosixPath(raw)
        if raw.startswith("/") or ":" in raw or ".." in p.parts or len(p.parts) > 2:
            raise RestoreError("压缩包含不安全路径：%r" % info.filename)
        if p.name not in ZIP_WHITELIST:
            raise RestoreError("压缩包含不允许的文件：%r" % info.filename)
        if p.name in picked:
            raise RestoreError("压缩包含重复文件：%r" % p.name)
        picked[p.name] = info
    if "manifest.json" not in picked:
        raise RestoreError("压缩包缺少 manifest.json")
    return picked


def _rmtree(path):
    if Path(path).exists():
        shutil.rmtree(path)


def stage(src, data_dir):
    """Copy a backup (folder / manifest.json / .zip) into data/restore-pending after verification."""
    src, data = Path(src), Path(data_dir)
    tmp, pending = data / PENDING_TMP, data / PENDING
    _rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        if src.is_file() and src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src) as zf:
                for name, info in _safe_zip_members(zf).items():
                    with zf.open(info) as fin, open(tmp / name, "wb") as fout:
                        shutil.copyfileobj(fin, fout)
        else:
            sdir = src.parent if src.is_file() and src.name == "manifest.json" else src
            if not sdir.is_dir():
                raise RestoreError("找不到备份：%s" % src)
            m = _load_manifest(sdir)
            for f in m.get("files") or []:
                name = f.get("name", "")
                if name not in ALLOWED_NAMES:
                    raise RestoreError("manifest 含非法文件名：%r" % name)
                shutil.copyfile(sdir / name, tmp / name)
            shutil.copyfile(sdir / "manifest.json", tmp / "manifest.json")
        for p in tmp.iterdir():
            with open(p, "rb+") as f:
                os.fsync(f.fileno())
        v = verify(tmp)
        if not v["ok"]:
            raise RestoreError("；".join(v["errors"]))
        _fsync_dir(tmp)
        _rmtree(pending)
        os.replace(tmp, pending)
        _fsync_dir(data)
        return v["manifest"]
    except Exception:
        _rmtree(tmp)
        raise


def _move(src, dst, step):
    os.replace(src, dst)
    if _FAULT:
        _FAULT(step)


def _rollback(data, j):
    """Undo a (possibly partial) apply described by journal j. Idempotent."""
    pending, rb = data / PENDING, Path(j["rollback"])
    pending.mkdir(exist_ok=True)
    for name in j["incoming"]:
        # Move-out of all current files precedes any move-in, so a file present in
        # data but absent from pending was moved in by us.
        if (data / name).exists() and not (pending / name).exists():
            os.replace(data / name, pending / name)
    if rb.is_dir():
        for p in rb.iterdir():
            os.replace(p, data / p.name)
        _fsync_dir(data)
        rb.rmdir()


def _park_pending(data):
    pending = data / PENDING
    if pending.exists():
        dest = data / ("restore-failed-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        os.replace(pending, dest)
        return str(dest)
    return ""


def apply_pending(data_dir):
    """Call at startup before opening any DB. Returns result dict or None if nothing to do."""
    data = Path(data_dir)
    jpath, pending = data / JOURNAL, data / PENDING
    if jpath.exists():
        j = json.loads(jpath.read_text(encoding="utf-8"))
        _rollback(data, j)
        parked = _park_pending(data)
        res = {"ok": False, "status": "recovered", "at": time.time(),
               "error": "上次恢复中断，已回滚到恢复前的数据", "parked": parked}
        _write_json_atomic(data / RESULT, res)
        jpath.unlink()
        return res
    if not pending.is_dir():
        return None
    v = verify(pending)
    if not v["ok"]:
        parked = _park_pending(data)
        res = {"ok": False, "status": "rejected", "at": time.time(),
               "error": "；".join(v["errors"]), "parked": parked}
        _write_json_atomic(data / RESULT, res)
        return res
    incoming = [f["name"] for f in v["manifest"]["files"] if f["name"] in DB_NAMES]
    outgoing = [n for n in DB_NAMES if not (n in KEEP_IF_ABSENT and n not in incoming)]
    rb = data / ("restore-rollback-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    j = {"state": "applying", "rollback": str(rb), "incoming": incoming, "outgoing": outgoing,
         "started": time.time()}
    _write_json_atomic(jpath, j)
    try:
        rb.mkdir()
        for n in outgoing:
            for suffix in ("",) + SIDECARS:
                p = data / (n + suffix)
                if p.exists():
                    _move(p, rb / p.name, "out:" + p.name)
        for n in incoming:
            _move(pending / n, data / n, "in:" + n)
        _fsync_dir(data)
    except Exception as e:
        _rollback(data, j)
        parked = _park_pending(data)
        res = {"ok": False, "status": "rolled_back", "at": time.time(), "error": str(e), "parked": parked}
        _write_json_atomic(data / RESULT, res)
        jpath.unlink()
        return res
    res = {"ok": True, "status": "applied", "at": time.time(), "restored": incoming,
           "kept_current": [n for n in DB_NAMES if n not in outgoing and (data / n).exists()],
           "rollback": str(rb), "version": v["manifest"].get("version", ""),
           "backup_created": v["manifest"].get("created")}
    jpath.unlink()  # commit point: from here on a crash must not roll back
    _fsync_dir(data)
    _write_json_atomic(data / RESULT, res)
    _rmtree(pending)
    return res

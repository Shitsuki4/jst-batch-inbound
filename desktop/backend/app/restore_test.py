"""restore.py unit tests: isolated temp dirs, no server, no JST session."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backup  # noqa: E402
import restore  # noqa: E402

FAILS = []


def check(name, cond):
    print("[%s] %s" % ("PASS" if cond else "FAIL", name))
    if not cond:
        FAILS.append(name)


def make_data(d, io_ids, catalog=True, marker="cur"):
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(d / "jst.db")) as c:
        c.execute("CREATE TABLE IF NOT EXISTS inbound_job (id INTEGER PRIMARY KEY, io_id TEXT NOT NULL DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("DELETE FROM inbound_job")
        c.executemany("INSERT INTO inbound_job(io_id) VALUES (?)", [(i,) for i in io_ids])
        c.execute("INSERT OR REPLACE INTO kv VALUES ('marker', ?)", (marker,))
        c.commit()
    if catalog:
        with closing(sqlite3.connect(d / "catalog.db")) as c:
            c.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
            c.execute("DELETE FROM t")
            c.execute("INSERT INTO t VALUES (?)", (marker,))
            c.commit()


def marker(d, db="jst.db"):
    with closing(sqlite3.connect(Path(d) / db)) as c:
        if db == "jst.db":
            return c.execute("SELECT v FROM kv WHERE k='marker'").fetchone()[0]
        return c.execute("SELECT v FROM t").fetchone()[0]


def take_backup(data):
    backup.BackupManager(data, threading.Lock()).create("manual")
    return sorted((Path(data) / "backups").glob("*/manifest.json"))[-1].parent


def main():
    root = Path(tempfile.mkdtemp(prefix="restore-test-"))

    # --- backup + list + verify + preview ---
    data = root / "data"
    make_data(data, ["IO1", "IO2"], marker="old")
    bdir = take_backup(data)
    m = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    check("manifest format=1", m.get("format") == 1)
    lst = restore.list_backups(data / "backups")
    check("list_backups label/dbs", lst and lst[0]["reason_label"] == "手动备份" and "jst.db" in lst[0]["dbs"] and lst[0]["size"] > 0)
    check("verify ok", restore.verify(bdir)["ok"])
    make_data(data, ["IO1", "IO2", "IO3"], marker="new")
    pv = restore.preview(bdir, data)
    check("preview missing io_ids", pv["missing_inbound_io_ids"] == ["IO3"])
    check("preview counts", pv["dbs"]["jst.db"]["backup"]["inbound_job"] == 2 and pv["dbs"]["jst.db"]["current"]["inbound_job"] == 3)

    # --- integrity failure (corrupt bytes, fix sha so only integrity_check catches it) ---
    bad = root / "bad"
    bad.mkdir()
    raw = bytearray((bdir / "jst.db").read_bytes())
    for i in range(100, min(len(raw), 4096)):
        raw[i] = 0xFF
    (bad / "jst.db").write_bytes(bytes(raw))
    bm = dict(m, files=[{"name": "jst.db", "bytes": len(raw), "sha256": restore._sha256(bad / "jst.db")}])
    (bad / "manifest.json").write_text(json.dumps(bm), encoding="utf-8")
    check("integrity failure rejected", not restore.verify(bad)["ok"])
    sha_bad = root / "shabad"
    sha_bad.mkdir()
    (sha_bad / "jst.db").write_bytes((bdir / "jst.db").read_bytes())
    (sha_bad / "manifest.json").write_text(json.dumps(dict(m, files=[{"name": "jst.db", "sha256": "0" * 64}])), encoding="utf-8")
    check("sha mismatch rejected", not restore.verify(sha_bad)["ok"])
    try:
        restore.stage(bad, root / "stage-bad")
        check("stage refuses corrupt backup", False)
    except restore.RestoreError:
        check("stage refuses corrupt backup", not (root / "stage-bad" / restore.PENDING).exists()
              and not (root / "stage-bad" / restore.PENDING_TMP).exists())

    # --- newer format refused ---
    newer = root / "newer"
    newer.mkdir()
    (newer / "jst.db").write_bytes((bdir / "jst.db").read_bytes())
    (newer / "manifest.json").write_text(json.dumps(dict(m, format=2)), encoding="utf-8")
    v = restore.verify(newer)
    check("newer format refused", not v["ok"] and "格式" in v["errors"][0])

    # --- zip-slip refused, good zip accepted ---
    zs = root / "slip.zip"
    with zipfile.ZipFile(zs, "w") as z:
        z.write(bdir / "manifest.json", "manifest.json")
        z.write(bdir / "jst.db", "../jst.db")
    try:
        restore.stage(zs, root / "zs-data")
        check("zip-slip refused", False)
    except restore.RestoreError:
        check("zip-slip refused", not (root / "jst.db").exists())
    zx = root / "extra.zip"
    with zipfile.ZipFile(zx, "w") as z:
        z.write(bdir / "manifest.json", "manifest.json")
        z.writestr("evil.exe", b"x")
    try:
        restore.stage(zx, root / "zx-data")
        check("non-whitelisted zip member refused", False)
    except restore.RestoreError:
        check("non-whitelisted zip member refused", True)
    gz = root / "good.zip"
    with zipfile.ZipFile(gz, "w") as z:
        for p in bdir.iterdir():
            z.write(p, "bk/" + p.name)
    restore.stage(gz, root / "gz-data")
    check("zip stage ok", (root / "gz-data" / restore.PENDING / "jst.db").is_file())

    # --- apply success (staged via manifest.json path), catalog kept when absent ---
    nocat = root / "nocat"
    nocat.mkdir()
    (nocat / "jst.db").write_bytes((bdir / "jst.db").read_bytes())
    (nocat / "manifest.json").write_text(json.dumps(dict(m, files=[f for f in m["files"] if f["name"] == "jst.db"])), encoding="utf-8")
    (data / "jst.db-wal").write_bytes(b"")
    restore.stage(nocat / "manifest.json", data)
    res = restore.apply_pending(data)
    check("apply success", res and res["ok"] and marker(data) == "old")
    check("catalog kept", marker(data, "catalog.db") == "new" and "catalog.db" in res["kept_current"])
    check("old db + wal in rollback dir", (Path(res["rollback"]) / "jst.db").is_file() and (Path(res["rollback"]) / "jst.db-wal").is_file())
    check("result written, pending/journal gone", (data / restore.RESULT).is_file() and not (data / restore.PENDING).exists()
          and not (data / restore.JOURNAL).exists())
    check("nothing to apply -> None", restore.apply_pending(data) is None)

    # --- injected failure -> rollback ---
    d2 = root / "d2"
    make_data(d2, ["A"], marker="old")
    b2 = take_backup(d2)
    make_data(d2, ["A", "B"], marker="new")
    restore.stage(b2, d2)
    restore._FAULT = lambda step: (_ for _ in ()).throw(OSError("injected")) if step == "in:jst.db" else None
    try:
        res = restore.apply_pending(d2)
    finally:
        restore._FAULT = None
    check("injected failure rolled back", res["status"] == "rolled_back" and marker(d2) == "new" and marker(d2, "catalog.db") == "new")
    check("no journal / rollback dir left", not (d2 / restore.JOURNAL).exists() and not list(d2.glob("restore-rollback-*"))
          and not (d2 / restore.PENDING).exists())

    # --- crash mid-apply leaves journal -> recovery on next call ---
    class Crash(BaseException):
        pass
    d3 = root / "d3"
    make_data(d3, ["X"], marker="old")
    b3 = take_backup(d3)
    make_data(d3, ["X", "Y"], marker="new")
    restore.stage(b3, d3)

    def crash(step):
        if step == "in:jst.db":
            raise Crash()
    restore._FAULT = crash
    try:
        restore.apply_pending(d3)
        check("crash simulated", False)
    except Crash:
        check("crash leaves journal", (d3 / restore.JOURNAL).exists())
    finally:
        restore._FAULT = None
    res = restore.apply_pending(d3)
    check("leftover journal recovered", res["status"] == "recovered" and marker(d3) == "new" and marker(d3, "catalog.db") == "new"
          and not (d3 / restore.JOURNAL).exists() and not list(d3.glob("restore-rollback-*")))

    import shutil
    shutil.rmtree(root, ignore_errors=True)
    print("restore_test: %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

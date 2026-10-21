"""SQLite online backups. Never include browser profiles or TLS/session secrets."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from datetime import datetime

from app_version import VERSION  # 与应用版本同源；升级后版本变化即触发一次升级前备份

class BackupManager:
    def __init__(self, data, lock):
        self.data = Path(data)
        self.root = self.data / "backups"
        self.lock = lock
        self.last_error = ""

    def manifests(self):
        if not self.root.exists():
            return []
        result = []
        for path in sorted(self.root.glob("*/manifest.json"), reverse=True):
            try:
                m = json.loads(path.read_text(encoding="utf-8"))
                m["directory"] = str(path.parent)
                result.append(m)
            except (OSError, ValueError):
                continue
        return result

    def status(self):
        rows = self.manifests()
        return {"version": VERSION, "directory": str(self.root), "last_error": self.last_error,
                "count": len(rows), "latest": rows[0] if rows else None,
                "recent": rows[:10], "automatic": "升级前及每天；不自动删除旧备份"}

    def create(self, reason="manual"):
        with self.lock:
            paths = [self.data / n for n in ("jst.db", "catalog.db", "unplanned.db", "purchase_watch.db", "joint_receipts.db", "operation_log.db") if (self.data / n).is_file()]
            if not paths:
                return None
            dest = self.root / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + reason)
            dest.mkdir(parents=True)
            m = {"format": 1, "version": VERSION, "reason": reason, "created": time.time(), "files": [], "complete": False}
            try:
                for path in paths:
                    target = dest / path.name
                    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as source:
                        with closing(sqlite3.connect(target)) as out:
                            source.backup(out, pages=256, sleep=0.02)
                            out.execute("PRAGMA journal_mode=DELETE")
                            if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                                raise RuntimeError("备份数据库完整性检查失败：" + path.name)
                    digest = hashlib.sha256()
                    with target.open("rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b""):
                            digest.update(chunk)
                    m["files"].append({"name": path.name, "bytes": target.stat().st_size, "sha256": digest.hexdigest()})
                m["complete"] = True
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
                m["error"] = self.last_error
                raise
            finally:
                (dest / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
            return m

    def before_upgrade(self):
        if (self.data / "jst.db").exists() and not any(m.get("version") == VERSION and m.get("complete") for m in self.manifests()):
            self.create("pre-upgrade")  # Fail closed: do not migrate an existing DB without backup.

    def daily(self):
        today = datetime.now().date()
        if not any(m.get("complete") and datetime.fromtimestamp(m["created"]).date() == today for m in self.manifests()):
            self.create("daily")

    def start(self):
        def run():
            while True:
                try:
                    self.daily()
                except Exception as e:
                    self.last_error = str(e)
                time.sleep(300)
        threading.Thread(target=run, daemon=True, name="sqlite-backup").start()

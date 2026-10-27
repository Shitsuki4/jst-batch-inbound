"""SQLite online backups. Never include browser profiles or TLS/session secrets."""
import contextlib
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
        self._create_lock = threading.Lock()
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
        """在线备份全部业务库。

        只在固定各库读快照时短暂持有业务锁（WAL 库开读事务，跨库一致）；复制、完整性检查、
        计算摘要都在锁外进行，备份大库不会卡住扫码。恢复前备份（pre-restore）例外：它本身就是
        写入屏障，保持持锁，保证备份即恢复前的最后状态。多个备份由 _create_lock 串行。"""
        # 锁顺序：_create_lock → 业务锁。调用方（手动备份请求）可能持有业务锁，等待时先释放它。
        if not self._create_lock.acquire(blocking=False):
            released = getattr(self.lock, "released", None)
            with (released("backup-wait") if released else contextlib.nullcontext()):
                self._create_lock.acquire()
        try:
            return self._create(reason)
        finally:
            self._create_lock.release()

    def _create(self, reason):
        paths = [self.data / n for n in ("jst.db", "catalog.db", "unplanned.db", "express.db", "purchase_watch.db", "joint_receipts.db", "learned_codes.db", "operation_log.db") if (self.data / n).is_file()]
        if not paths:
            return None
        dest = self.root / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + reason)
        dest.mkdir(parents=True)
        m = {"format": 1, "version": VERSION, "reason": reason, "created": time.time(), "files": [], "complete": False}
        sources = []
        try:
            with self.lock:
                for path in paths:
                    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
                    sources.append((path, source))
                    if source.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
                        # 固定读快照：之后的写入不进入本次备份，也不阻塞写入方。
                        source.execute("BEGIN")
                        source.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
                released = getattr(self.lock, "released", None)
                outside = released("backup") if released and reason != "pre-restore" else contextlib.nullcontext()
                with outside:
                    for path, source in sources:
                        target = dest / path.name
                        with closing(sqlite3.connect(target)) as out:
                            source.backup(out)
                            out.execute("PRAGMA journal_mode=DELETE")
                            if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                                raise RuntimeError("备份数据库完整性检查失败：" + path.name)
                        if source.in_transaction:
                            source.rollback()
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
            for _, source in sources:
                try:
                    source.close()
                except Exception:
                    pass
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

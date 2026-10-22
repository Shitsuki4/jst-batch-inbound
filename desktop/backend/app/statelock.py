# -*- coding: utf-8 -*-
"""全局业务锁 state_lock：只保护本地状态，远程调用 / 慢 I/O 一律在锁外进行。

并发模型（4.0.x 全局锁重构，logic review #8）
------------------------------------------------
* 所有修改本地收货状态的请求仍在 ``STATE_LOCK`` 内串行执行：共享的 STORE.db 等 SQLite
  连接只在持锁时使用（同一连接被两个线程同时使用会死锁，见 #18）。
* 访问聚水潭 / ERP（HTTP）、写导出文件、备份复制、控制登录浏览器等慢操作放在
  ``with remote():`` / ``with STATE_LOCK.released(...)`` 里：当前线程**完整释放**锁（含重入层数），
  操作结束后重新获取。释放期间别的请求（扫码、GET state、PDA）可以正常进行。
* 释放前必须没有未提交的事务：同一连接上的事务是共享的，别的线程一提交就会把我们写了一半的
  数据一起提交。若检测到打开的事务，本次**不释放**（退回旧的持锁行为，计入 ``stats['pinned']``
  并记日志），保证正确性优先。
* 重新获取锁之后，调用方必须重新读取 / 校验它依赖的本地状态（版本 / 指纹 / 存在性），
  发生并发修改时报冲突或重试，而不是用锁外拿到的旧快照覆盖。
* 同一对象上的长流程（入库生成 / 续作）用 ``keyed(key)`` 串行：等待 keyed 锁时不持有
  state_lock，锁顺序固定为 keyed → state_lock，不会反向等待。
* scan_audit 增量（操作记录 after 钩子、录像 item 事件）按「本请求释放锁期间别人写入的 id 区间」
  排除，避免把其他请求的扫码算到本请求名下（``foreign_ranges`` / ``audit_exclusion``）。
"""
import contextlib
import logging
import threading

_LOG = logging.getLogger("jst-scan")


class StateLock(object):
    """可重入的全局业务锁，支持在远程调用期间完整释放。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._tls = threading.local()
        self._db_sources = []        # callables -> iterable of sqlite3 connections (shared, guarded by this lock)
        self._audit_sources = []     # callables -> iterable of sqlite3 connections that have scan_audit
        self.stats = {"yields": 0, "pinned": 0}

    # ------------------------------------------------------------ RLock API
    def _depth(self):
        return getattr(self._tls, "depth", 0)

    def acquire(self, blocking=True, timeout=-1):
        ok = self._lock.acquire(blocking, timeout)
        if ok:
            self._tls.depth = self._depth() + 1
        return ok

    def release(self):
        depth = self._depth()
        if depth <= 0:
            raise RuntimeError("cannot release un-acquired state lock")
        self._tls.depth = depth - 1
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def mark(self):
        """当前线程累计释放次数；前后两次 mark 不同说明期间释放过锁，本地状态需重新校验。"""
        return getattr(self._tls, "yields", 0)

    def held(self):
        """当前线程是否持有锁。"""
        return self._depth() > 0

    # ------------------------------------------------------------ registry
    def register_db(self, source):
        """登记由本锁保护的共享连接（返回连接列表的函数）；释放前检查它们没有打开的事务。"""
        self._db_sources.append(source)

    def register_audit(self, source):
        """登记带 scan_audit 表的连接；释放期间别人写入的 scan_audit id 区间会被记下。"""
        self._audit_sources.append(source)

    @staticmethod
    def _collect(sources):
        out, seen = [], set()
        for src in sources:
            try:
                conns = src() or ()
            except Exception:
                continue
            for c in conns:
                if c is not None and id(c) not in seen:
                    seen.add(id(c))
                    out.append(c)
        return out

    def _audit_marks(self):
        marks = {}
        for c in self._collect(self._audit_sources):
            try:
                marks[id(c)] = (c, c.execute("SELECT COALESCE(MAX(id),0) FROM scan_audit").fetchone()[0])
            except Exception:
                pass
        return marks

    # ------------------------------------------------------------ release around slow work
    @contextlib.contextmanager
    def released(self, why="remote"):
        """完整释放当前线程持有的锁，块结束后按原层数重新获取。

        未持锁时什么也不做；有未提交事务时不释放（正确性优先），产出 False。"""
        depth = self._depth()
        if depth <= 0:
            yield False
            return
        busy = []
        for c in self._collect(self._db_sources):
            try:
                if c.in_transaction:
                    busy.append(c)
            except Exception:
                pass
        if busy:
            self.stats["pinned"] += 1
            _LOG.warning("state_lock 未释放（%s）：共享连接上有未提交事务，本次远程调用仍在锁内进行", why)
            yield False
            return
        marks = self._audit_marks()
        for _ in range(depth):
            self.release()
        self.stats["yields"] += 1
        self._tls.yields = getattr(self._tls, "yields", 0) + 1
        try:
            yield True
        finally:
            for _ in range(depth):
                self.acquire()
            self._note_foreign(marks)

    def _note_foreign(self, marks):
        ranges = getattr(self._tls, "foreign", None)
        if ranges is None:
            ranges = self._tls.foreign = {}
        for key, (conn, lo) in marks.items():
            try:
                hi = conn.execute("SELECT COALESCE(MAX(id),0) FROM scan_audit").fetchone()[0]
            except Exception:
                continue
            if hi > lo:
                ranges.setdefault(key, []).append((lo, hi))

    # ------------------------------------------------------------ scan_audit attribution
    def begin_request(self):
        """每个请求开始时清空上一请求留下的区间。"""
        self._tls.foreign = {}

    def foreign_ranges(self, conn):
        return list((getattr(self._tls, "foreign", None) or {}).get(id(conn), ()))

    def audit_exclusion(self, conn, column="id"):
        """(sql, params)：排除本请求释放锁期间其他请求写入的 scan_audit 行。"""
        sql, params = "", []
        for lo, hi in self.foreign_ranges(conn):
            sql += " AND NOT (%s>? AND %s<=?)" % (column, column)
            params += [lo, hi]
        return sql, params


STATE_LOCK = StateLock()


def remote(why="remote"):
    """访问聚水潭 / ERP 等远程服务时使用：在锁外执行。"""
    return STATE_LOCK.released(why)


def unlocked(why="slow-io"):
    """本地慢 I/O（写导出文件、备份复制、浏览器控制）时使用：在锁外执行。"""
    return STATE_LOCK.released(why)


# ---------------------------------------------------------------- keyed (per-object) locks
_KEYED = {}
_KEYED_GUARD = threading.Lock()


@contextlib.contextmanager
def keyed(key):
    """同一 key（如某收货批次的入库流程）的长流程串行执行；可重入。

    先尝试不阻塞获取；需要等待时先释放 state_lock 再等，拿到后重新获取 state_lock，
    锁顺序固定为 keyed → state_lock。"""
    with _KEYED_GUARD:
        lk = _KEYED.get(key)
        if lk is None:
            lk = _KEYED[key] = threading.RLock()
    if not lk.acquire(blocking=False):
        with STATE_LOCK.released("wait:%s" % (key,)) as freed:
            if not freed and STATE_LOCK.held():
                raise RuntimeError("该收货批次正在处理入库请求，请稍后重试")
            lk.acquire()
    try:
        yield
    finally:
        lk.release()


# ---------------------------------------------------------------- ERP client proxy
_LOCAL_METHODS = frozenset(("_parse_jt_data", "clone_for_catalog", "_cookie_header", "_list_url",
                            "_absorb_set_cookie", "_apply_session"))


class Unlocking(object):
    """包装聚水潭客户端：每次方法调用都在 state_lock 之外执行；属性读写透传给原客户端。"""
    __slots__ = ("_target",)

    def __init__(self, target):
        object.__setattr__(self, "_target", unwrap(target))

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if name.startswith("__") or name in _LOCAL_METHODS or not callable(value):
            return value

        def call(*args, **kwargs):
            with remote(name):
                return value(*args, **kwargs)
        call.__name__ = name
        return call

    def __setattr__(self, name, value):
        setattr(self._target, name, value)

    def __delattr__(self, name):
        delattr(self._target, name)

    def __repr__(self):
        return "Unlocking(%r)" % (self._target,)


def unwrap(client):
    while isinstance(client, Unlocking):
        client = object.__getattribute__(client, "_target")
    return client


def unlocking(client):
    return client if client is None or isinstance(client, Unlocking) else Unlocking(client)

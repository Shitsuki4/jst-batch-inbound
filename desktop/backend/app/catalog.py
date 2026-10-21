# -*- coding: utf-8 -*-
"""六小时商品资料快照：独立 SQLite/WAL，联网分页不占扫码锁，成功后整批切换。"""
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from codes import item_auxiliary
from pathlib import Path

INTERVAL = 6 * 3600
RETRY = 5 * 60


def scope_key(company, owner, auth):
    company, owner, auth = (str(v or '').strip() for v in (company, owner, auth))
    owner = company if owner in ('', '0') else owner
    auth = auth or company
    if not company or '2147483647' in (company, owner, auth):
        return ''
    return json.dumps([company, owner, auth], separators=(',', ':'))


def aliases(item):
    result = set()
    for k in ('sku_id', 'sku_code', 'i_id'):
        v = str(item.get(k) or '').strip().lower()
        if v: result.add(v)
    for v in item_auxiliary(item):
        if v.strip(): result.add(v.strip().lower())
    return result


class CatalogDB:
    def __init__(self, filename):
        self.filename = str(filename)
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS catalog_meta(scope TEXT PRIMARY KEY, label TEXT, success REAL DEFAULT 0,
                attempt REAL DEFAULT 0, count INTEGER DEFAULT 0, error TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS catalog_item(scope TEXT, sku TEXT, payload TEXT, PRIMARY KEY(scope,sku));
            CREATE TABLE IF NOT EXISTS catalog_code(scope TEXT, code TEXT, sku TEXT, PRIMARY KEY(scope,code,sku));
            CREATE TABLE IF NOT EXISTS catalog_point(scope TEXT, code TEXT, payload TEXT, ts REAL, PRIMARY KEY(scope,code));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.filename, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def status(self, company=''):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM catalog_meta ORDER BY label,scope')]
        return [dict(r, company=json.loads(r['scope'])[0], owner=json.loads(r['scope'])[1],
                     auth=json.loads(r['scope'])[2], stale=not (0 <= time.time()-r['success'] < INTERVAL))
                for r in rows if not company or json.loads(r['scope'])[0] == str(company)]

    def due(self, scope, now=None, force=False):
        now = time.time() if now is None else now
        with self.connect() as db:
            r = db.execute('SELECT * FROM catalog_meta WHERE scope=?', (scope,)).fetchone()
        if not r or force: return True
        if r['error']: return not (0 <= now-r['attempt'] < RETRY)
        return not (0 <= now-r['success'] < INTERVAL)

    def failed(self, scope, label, error):
        with self.connect() as db:
            db.execute('INSERT INTO catalog_meta(scope,label,attempt,error) VALUES(?,?,?,?) '
                       'ON CONFLICT(scope) DO UPDATE SET label=excluded.label,attempt=excluded.attempt,error=excluded.error',
                       (scope, label, time.time(), str(error)[:600]))

    def replace(self, scope, label, items):
        products, codes, seen = [], [], set()
        for it in items:
            sku = str(it.get('sku_id') or '').strip()
            # catalog_item 主键 (scope, sku) 区分大小写：只拒绝完全相同的编码；
            # 仅大小写不同的两个 SKU 是不同商品（扫码别名的小写匹配不受影响）。
            if not sku or sku in seen:
                raise ValueError('商品资料存在空编码或重复编码，保留旧快照')
            seen.add(sku)
            products.append((scope, sku, json.dumps(it, ensure_ascii=False)))
            codes.extend((scope, code, sku) for code in aliases(it))
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT count FROM catalog_meta WHERE scope=?', (scope,)).fetchone()
            if old and old['count'] and not products:
                raise ValueError('全量接口意外返回空库，保留旧快照，请核对货主和权限')
            db.execute('DELETE FROM catalog_item WHERE scope=?', (scope,))
            db.execute('DELETE FROM catalog_code WHERE scope=?', (scope,))
            db.execute('DELETE FROM catalog_point WHERE scope=?', (scope,))
            db.executemany('INSERT INTO catalog_item VALUES(?,?,?)', products)
            db.executemany('INSERT INTO catalog_code VALUES(?,?,?)', codes)
            db.execute('INSERT INTO catalog_meta(scope,label,success,attempt,count,error) VALUES(?,?,?,?,?,?) '
                       'ON CONFLICT(scope) DO UPDATE SET label=excluded.label,success=excluded.success,'
                       'attempt=excluded.attempt,count=excluded.count,error=excluded.error', (scope,label,now,now,len(products),''))

    def lookup(self, scope, code):
        if not scope: return []
        code = str(code or '').strip().lower()
        with self.connect() as db:
            rows = db.execute('SELECT i.payload FROM catalog_code c JOIN catalog_item i '
                              'ON i.scope=c.scope AND i.sku=c.sku WHERE c.scope=? AND c.code=? ORDER BY i.sku',
                              (scope,code)).fetchall()
        return [dict(json.loads(r['payload']), src='catalog') for r in rows]

    def search(self, scope, query, limit=50):
        """人工选择商品用：精确编码/条码优先，其次名称、编码、条码包含关键字。只读当前范围。"""
        if not scope: return []
        q = str(query or '').strip().lower()
        if not q: return []
        result, seen = [], set()
        for row in self.lookup(scope, q):
            if row.get('sku_id') not in seen:
                seen.add(row.get('sku_id')); result.append(row)
        like = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM catalog_item WHERE scope=? AND lower(payload) LIKE ? ESCAPE '\\' "
                              "ORDER BY sku LIMIT 2000", (scope, like)).fetchall()
        for r in rows:
            if len(result) >= limit: break
            it = json.loads(r['payload'])
            text = ' '.join(str(it.get(k) or '') for k in ('sku_id', 'i_id', 'sku_code', 'name', 'spec', 'aux_codes')).lower()
            if q in text and it.get('sku_id') not in seen:
                seen.add(it.get('sku_id')); result.append(dict(it, src='catalog'))
        return result[:limit]

    def point_get(self, scope, code):
        with self.connect() as db:
            r = db.execute('SELECT * FROM catalog_point WHERE scope=? AND code=?',
                           (scope,code.strip().lower())).fetchone()
        if r and 0 <= time.time()-r['ts'] < (INTERVAL if r['payload'] != 'null' else RETRY):
            return True, json.loads(r['payload'])
        return False, None

    def point_put(self, scope, code, item):
        if not scope: return
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO catalog_point VALUES(?,?,?,?)',
                       (scope, code.strip().lower(), json.dumps(item, ensure_ascii=False), time.time()))


def fetch_catalog(client, owner, auth, progress=lambda n: None, valid=lambda: True, page_size=200):
    """接口不提供可靠总数，一直读到空页；重复页/结构错误/分页上限均不得发布。"""
    result, seen = [], set()
    for page in range(1, 2502):
        if not valid(): raise RuntimeError('登录账号已变化，取消本轮同步')
        rows = client.catalog_page(owner, auth, page, page_size)
        if not isinstance(rows, list): raise ValueError('商品资料分页响应不是数组')
        if not rows: return result
        for row in rows:
            if not isinstance(row, dict): raise ValueError('商品资料行格式错误')
            sku = str(row.get('sku_id') or '').strip()   # exact match: SKU is case-sensitive
            if not sku or sku in seen: raise ValueError('分页出现重复/空商品编码，保留旧快照')
            row_owner = str(row.get('owner_co_id') if row.get('owner_co_id') is not None else '').strip()
            # 部分版本的自有数据用 0；不能把其它货主的数据静默装进当前范围。
            if row_owner and row_owner not in (str(owner), '0' if str(owner)==str(client.coid) else str(owner)):
                raise ValueError('商品资料返回货主不匹配，保留旧快照')
            seen.add(sku)
            it = client._to_item(row, 'catalog')
            if not it: raise ValueError('商品资料缺少编码')
            it['aux_codes'] = ','.join(item_auxiliary(row))
            it['unit'] = str(row.get('unit') or '')
            result.append(it)
        if len(result) > 500000: raise ValueError('商品资料超过安全分页上限')
        progress(len(result))
        time.sleep(.03)
    raise ValueError('商品分页超过上限，未覆盖旧快照')


class CatalogSync:
    def __init__(self, db, provider):
        self.db, self.provider = db, provider
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.stop_event = threading.Event()
        self.thread = None
        self.force = False
        self.running = False
        self.state = {'running': False, 'message': '等待登录后自动同步', 'loaded': 0}
        self.owners_cache = {}

    def status(self):
        with self.lock: return dict(self.state)

    def update(self, **kw):
        with self.lock: self.state.update(kw)

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(target=self.loop, name='catalog-sync', daemon=True)
            self.thread.start()

    def request(self):
        with self.lock:
            if self.running: return False
            self.force = True
        self.event.set()
        return True

    def loop(self):
        while not self.stop_event.is_set():
            self.event.clear()
            with self.lock:
                force, self.force = self.force, False
                self.running = True
            try: self.run_once(force)
            except Exception as e: self.update(running=False, message='同步失败：'+str(e)[:300])
            finally:
                with self.lock: self.running = False
            self.event.wait(60)

    def run_once(self, force=False):
        context = self.provider()
        if not context:
            self.update(running=False, message='等待登录；已有商品资料可继续本地使用')
            return
        client, targets, valid = context
        key = (str(client.coid), str(client.uid))
        now = time.time()
        cached = self.owners_cache.get(key)
        if force or not cached or now-cached[0] >= INTERVAL:
            self.update(running=True, message='正在读取可用货主列表', loaded=0)
            owners = client.partners()
            self.owners_cache[key] = (now if owners else now-INTERVAL+RETRY, owners)
        else: owners = cached[1]
        # 当前/未结单上下文优先；其余货主按登录仓储公司同步。
        targets = list(targets) + [(str(o['id']), str(client.coid), str(o['name'])) for o in owners]
        targets.append((str(client.coid), str(client.coid), '自有商品'))
        seen, errors = set(), 0
        for owner, auth, label in targets:
            if not valid(): break
            scope = scope_key(client.coid, owner, auth)
            if not scope or scope in seen: continue
            seen.add(scope)
            if not self.db.due(scope, force=force): continue
            _, owner, auth = json.loads(scope)
            self.update(running=True, message='正在同步 '+label, loaded=0)
            try:
                items = fetch_catalog(client, owner, auth,
                                      progress=lambda n: self.update(loaded=n), valid=valid)
                if not valid(): raise RuntimeError('账号变化，未发布本轮快照')
                self.db.replace(scope, label, items)
            except Exception as e:
                self.db.failed(scope, label, e)
                errors += 1
        self.update(running=False, message=('部分货主同步失败，保留旧数据，5 分钟后重试' if errors else
                    '本地商品库已就绪；每 6 小时自动更新（程序运行且已登录时）'))

# -*- coding: utf-8 -*-
"""未知条码学习：本机记住「条码 → 商品编码」的对应关系。

规则（用户确认）：
* 商品资料（本地商品库 / 采购明细 / ERP 实时查询）永远优先；学习匹配只在商品资料
  什么都查不到时兜底使用。
* 商品资料后来能识别这个条码时，一律以商品资料为准：
  - 对应同一商品 → 标记 superseded（已被商品资料覆盖），不再使用；
  - 对应不同商品 → 标记 conflict，提示一次后停止使用。
* 范围 = 聚水潭公司 + 货主 + 仓储公司（与商品库 scope_key 相同），不同公司之间绝不共用。

独立 SQLite 文件，不改动收货主库结构以外的任何东西。
"""
import csv
import io
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ACTIVE, SUPERSEDED, CONFLICT = 'active', 'superseded', 'conflict'
STATUS_TEXT = {ACTIVE: '使用中', SUPERSEDED: '已被商品资料覆盖（同一商品）',
               CONFLICT: '商品资料对应其他商品，已停用'}
MAX_CODE = 200

_local = threading.local()


def push_notice(text):
    """本次请求内产生的一次性提示，由 HTTP 层随响应带回（learned_notice）。"""
    if not hasattr(_local, 'notices'): _local.notices = []
    if text not in _local.notices: _local.notices.append(text)


def take_notices():
    notices = getattr(_local, 'notices', None) or []
    _local.notices = []
    return notices


def clean_code(code):
    code = str(code or '').strip()
    if not code: raise ValueError('条码不能为空')
    if len(code) > MAX_CODE or any(ord(c) < 32 for c in code):
        raise ValueError('条码格式无效或过长')
    return code


def now_text():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def scope_parts(scope):
    try:
        company, owner, auth = json.loads(scope)
        return str(company), str(owner), str(auth)
    except (ValueError, TypeError):
        raise ValueError('学习范围无效')


class LearnedCodes:
    def __init__(self, filename):
        self.filename = str(filename)
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS learned_code(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, company TEXT NOT NULL, owner TEXT NOT NULL, auth TEXT NOT NULL,
                code TEXT NOT NULL, raw_code TEXT NOT NULL,
                sku_id TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', catalog_sku TEXT NOT NULL DEFAULT '',
                learned_by TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL DEFAULT '', hits INTEGER NOT NULL DEFAULT 0,
                UNIQUE(scope, code));
            CREATE INDEX IF NOT EXISTS ix_learned_company ON learned_code(company);
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

    # ---------- 写入 ----------
    def learn(self, scope, code, item, learned_by='', source=''):
        if not scope: raise ValueError('请先登录聚水潭并确定货主，才能学习条码')
        company, owner, auth = scope_parts(scope)
        raw = clean_code(code)
        sku = str((item or {}).get('sku_id') or '').strip()
        if not sku: raise ValueError('请选择有商品编码的商品')
        keep = {k: (item.get(k) if item.get(k) is not None else '')
                for k in ('sku_id', 'i_id', 'sku_code', 'name', 'spec', 'pic', 'enabled', 'aux_codes')}
        keep['sku_id'] = sku
        stamp = now_text()
        with self.connect() as db:
            db.execute(
                'INSERT INTO learned_code(scope,company,owner,auth,code,raw_code,sku_id,payload,status,catalog_sku,'
                'learned_by,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(scope,code) DO UPDATE SET raw_code=excluded.raw_code,sku_id=excluded.sku_id,'
                'payload=excluded.payload,status=excluded.status,catalog_sku=\'\',learned_by=excluded.learned_by,'
                'source=excluded.source,updated_at=excluded.updated_at,hits=0,last_used_at=\'\'',
                (scope, company, owner, auth, raw.lower(), raw, sku, json.dumps(keep, ensure_ascii=False),
                 ACTIVE, '', str(learned_by or '')[:200], str(source or '')[:40], stamp, stamp))
            row = db.execute('SELECT * FROM learned_code WHERE scope=? AND code=?', (scope, raw.lower())).fetchone()
        return self.public(row)

    def touch(self, rid):
        with self.connect() as db:
            db.execute('UPDATE learned_code SET hits=hits+1,last_used_at=? WHERE id=?', (now_text(), rid))

    def reconcile(self, scope, code, found_rows):
        """商品资料已能识别此条码：学习匹配让位。冲突时返回（并登记）一次性提示。"""
        if not scope or not found_rows: return None
        key = str(code or '').strip().lower()
        if not key: return None
        skus = sorted({str(r.get('sku_id') or '').strip() for r in found_rows if str(r.get('sku_id') or '').strip()})
        if not skus: return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM learned_code WHERE scope=? AND code=? AND status=?',
                             (scope, key, ACTIVE)).fetchone()
            if not row: return None
            same = row['sku_id'] in skus
            status = SUPERSEDED if same else CONFLICT
            # 条件更新保证并发/重复扫码只有一次真正转换，因此冲突提示只出现一次。
            cur = db.execute('UPDATE learned_code SET status=?,catalog_sku=?,updated_at=? WHERE id=? AND status=?',
                             (status, ','.join(skus)[:400], now_text(), row['id'], ACTIVE))
            if cur.rowcount != 1 or same: return None
        name = json.loads(row['payload']).get('name') or ''
        notice = ('条码 %s 现已在商品资料中对应 %s，与之前学习的 %s%s 不同；已改用商品资料结果，'
                  '该学习匹配已停用（可在 设置 → 商品资料 → 未知条码学习 中查看或删除）。'
                  % (row['raw_code'], '、'.join(skus), row['sku_id'], ('（%s）' % name) if name else ''))
        push_notice(notice)
        return notice

    def delete(self, company, ids):
        ids = [int(i) for i in (ids or []) if str(i).strip().lstrip('-').isdigit()]
        if not company: raise ValueError('请先登录聚水潭')
        if not ids: raise ValueError('请选择要删除的学习条码')
        with self.connect() as db:
            n = 0
            for rid in ids:
                n += db.execute('DELETE FROM learned_code WHERE id=? AND company=?', (rid, str(company))).rowcount
        return n

    # ---------- 读取 ----------
    def active(self, scope, code):
        if not scope: return None
        key = str(code or '').strip().lower()
        if not key: return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM learned_code WHERE scope=? AND code=? AND status=?',
                             (scope, key, ACTIVE)).fetchone()
        return dict(row) if row else None

    def rows(self, company, query='', limit=2000):
        if not company: return []
        q = str(query or '').strip().lower()
        with self.connect() as db:
            rows = db.execute('SELECT * FROM learned_code WHERE company=? ORDER BY updated_at DESC,id DESC',
                              (str(company),)).fetchall()
        result = []
        for r in rows:
            item = self.public(r)
            if q and q not in ' '.join(str(item.get(k) or '') for k in
                                       ('code', 'sku_id', 'name', 'sku_code', 'spec', 'owner', 'auth')).lower():
                continue
            result.append(item)
            if len(result) >= limit: break
        return result

    @staticmethod
    def public(row):
        r = dict(row)
        p = json.loads(r.pop('payload') or '{}')
        return {'id': r['id'], 'code': r['raw_code'], 'sku_id': r['sku_id'], 'name': p.get('name', ''),
                'sku_code': p.get('sku_code', ''), 'spec': p.get('spec', ''), 'owner': r['owner'],
                'auth': r['auth'], 'company': r['company'], 'status': r['status'],
                'status_text': STATUS_TEXT.get(r['status'], r['status']), 'catalog_sku': r['catalog_sku'],
                'learned_by': r['learned_by'], 'source': r['source'], 'created_at': r['created_at'],
                'updated_at': r['updated_at'], 'last_used_at': r['last_used_at'], 'hits': r['hits']}

    def export_csv(self, company):
        out = io.StringIO()
        w = csv.writer(out)
        safe = lambda v: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v
        w.writerow(['条码', '商品编码', '商品名称', '商品条码', '规格', '货主', '仓储公司', '状态',
                    '商品资料对应编码', '学习时间', '最近使用', '使用次数', '来源'])
        for r in self.rows(company, limit=10 ** 7):
            w.writerow([safe(x) for x in (r['code'], r['sku_id'], r['name'], r['sku_code'], r['spec'], r['owner'],
                                          r['auth'], r['status_text'], r['catalog_sku'], r['created_at'],
                                          r['last_used_at'], r['hits'], r['source'])])
        return '\ufeff' + out.getvalue()

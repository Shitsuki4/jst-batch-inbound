"""Independent no-PO receipts. Reuses receipt schema, never ordinary receipt targets."""
import csv
import hashlib
import io
import json
import math
from decimal import Decimal
from store import Store, now_str
from inbound import InboundService, InboundError, json_text
from jst import normalize_po_item, match_item


def clean(value, limit=300):
    value = str(value or '').strip()
    if len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('内容过长或包含控制字符')
    return value


def qty(value):
    n = float(value)
    if not math.isfinite(n) or n < 0 or n > 100000000:
        raise ValueError('数量必须是有效非负数，且不超过一亿')
    return n


class StrictInbound(InboundService):
    def __init__(self, service, client):
        super().__init__(service.store, client)
        self.service = service

    def preview(self, sid, wh_id=None):
        self.service.refresh(sid)
        report = self.service.state(sid)
        if report['locked']:
            raise InboundError('该批次已提交，请查看或继续原入库单，不可重复生成')
        if not report['can_create']:
            raise InboundError('采购单与实收未通过核对，请处理差异后重新核对')
        s = report['session']
        peer = self.store.db.execute("SELECT j.id FROM inbound_job j JOIN session p ON p.id=j.session_id WHERE j.session_id<>? AND p.po_id=? AND p.owner_co_id=? AND p.auth_co_id=? AND j.status<>'rejected' LIMIT 1", (sid,s['po_id'],s['owner_co_id'],s['auth_co_id'])).fetchone()
        if peer: raise InboundError('此采购单已在另一个无采购单批次提交入库，请先核查原单')
        self.store.set_kv('inbound_validate_purchase', '1')
        result = super().preview(sid, wh_id)
        # Reject changed purchase data between preview and confirmation, including prices.
        result['snapshot']['purchase_digest'] = report['meta']['purchase_digest']
        result['snapshot']['receipt_revision'] = report['meta']['revision']
        result['fingerprint'] = hashlib.sha256(json_text(result['snapshot']).encode()).hexdigest()
        result['warnings'] = ['仅按整批实收创建待入库单；不自动确认库存，价格按采购单及ERP规则继承。']
        return result


class UnplannedService:
    def __init__(self, path, client, find_purchase, lookup, scan_filter=None):
        self.store = Store(path)
        self.client, self.find_purchase, self.lookup = client, find_purchase, lookup
        self.scan_filter = scan_filter or (lambda rows, owner, auth: rows)
        self.store.db.execute('CREATE TABLE IF NOT EXISTS unplanned_meta(sid INTEGER PRIMARY KEY, note TEXT NOT NULL DEFAULT "", revision INTEGER NOT NULL DEFAULT 0, checked_revision INTEGER NOT NULL DEFAULT -1, checked_at TEXT NOT NULL DEFAULT "", purchase_digest TEXT NOT NULL DEFAULT "")')
        self.store.db.commit()
        self.inbound = StrictInbound(self, client)

    def session(self, sid):
        s = self.store.get_session(int(sid))
        if not s: raise ValueError('找不到无采购单收货批次')
        return s

    def locked(self, sid):
        return any(j['status'] != 'rejected' for j in self.store.inbound_jobs(sid))

    def guard(self, sid, revision):
        self.session(sid)
        if self.locked(sid): raise ValueError('该批次已提交或结果待核实，明细已锁定，请继续原单')
        m = self.store.db.execute('SELECT revision FROM unplanned_meta WHERE sid=?', (sid,)).fetchone()
        if revision is None or int(revision) != m['revision']:
            raise ValueError('收货记录已变化，请刷新后重试')

    def touch(self, sid):
        self.store.db.execute('UPDATE unplanned_meta SET revision=revision+1,checked_revision=-1 WHERE sid=?', (sid,))
        self.store.db.commit()

    def new(self, note, tracking_no=""):
        stamp = now_str()
        with self.store.db:
            c = self.store.db.execute('INSERT INTO session(input_no,po_id,id_type,state,started_at,items_json,round_baseline_json,owner_co_id,auth_co_id) VALUES(?,?,?,?,?,?,?,?,?)', ('', '', 'unplanned', 'active', stamp, '[]', '[]', str(self.client.coid or ''), str(self.client.coid or '')))
            sid = c.lastrowid
            self.store.db.execute('UPDATE session SET input_no=? WHERE id=?', ('WP'+stamp[:10].replace('-','')+'-'+str(sid).zfill(4), sid))
            self.store.db.execute('INSERT INTO unplanned_meta(sid,note) VALUES(?,?)', (sid, clean(note)))
        result=self.state(sid)
        if tracking_no:
            batch,_=self.store.ensure_batch(sid,clean(tracking_no,200))
            result=self.state(sid);result['current_batch']=batch['id']
        return result

    def history(self, query=''):
        query = str(query or '').strip()
        result = []
        for row in self.store.db.execute('SELECT s.*,m.note FROM session s JOIN unplanned_meta m ON m.sid=s.id ORDER BY s.id DESC'):
            s = dict(row)
            bs = self.store.batches(s['id'])
            if query and not any(query.lower() in str(x).lower() for x in [s['input_no'],s['po_id'],s['note']]+[b['tracking_no'] for b in bs]): continue
            total = self.store.db.execute('SELECT COALESCE(SUM(qty),0) FROM line WHERE session_id=?', (s['id'],)).fetchone()[0]
            result.append({k:s[k] for k in ('id','input_no','po_id','note','started_at')} | {'total':total,'tracking_count':len(bs),'locked':self.locked(s['id'])})
        return result

    def state(self, sid):
        s = self.session(sid)
        meta = dict(self.store.db.execute('SELECT * FROM unplanned_meta WHERE sid=?',(sid,)).fetchone())
        batches = self.store.batches_with_lines(sid)
        actual = {}
        for b in batches:
            for line in b['lines']:
                if not qty(line['qty']): continue
                sku = line['sku_id']
                if sku not in actual: actual[sku] = dict(line, recv=Decimal(0))
                actual[sku]['recv'] += Decimal(str(line['qty']))
        purchase = {}
        for p in json.loads(s['items_json'] or '[]'):
            sku = p['sku_id']
            r = purchase.setdefault(sku, dict(p, plan=Decimal(0), count=0))
            r['plan'] += Decimal(str(p['qty']))
            r['count'] += 1
        rows, problems = [], []
        linked = bool(s['po_id'])
        for sku in list(purchase) + [sku for sku in actual if sku not in purchase]:
            a, p = actual.get(sku, {}), purchase.get(sku, {})
            recv, plan = a.get('recv',Decimal(0)), p.get('plan',Decimal(0))
            kind, reason = 'neutral', '仅实收'
            if linked:
                if not p: kind,reason = 'extra','采购单外'
                elif p['count'] > 1: kind,reason = 'unknown','采购单重复SKU'
                elif not p.get('qty_known',True): kind,reason = 'unknown','采购数量未知'
                elif recv == plan: kind,reason = 'good','一致'
                else: kind,reason = 'diff',('未收到 '+str(plan) if not recv else ('少收 ' if recv < plan else '多收 ')+str(abs(recv-plan)))
            source = a or p
            rows.append({'sku_id':sku,'sku_code':source.get('sku_code',''),'name':source.get('name',''),'spec':source.get('spec',''),'recv':float(recv),'plan':float(plan) if linked else None,'kind':kind,'reason':reason})
        for p in purchase.values():
            if not p.get('in_qty_known',False): problems.append('商品 '+p['sku_id']+' 的ERP已入库数量未知，不能确认可入库量')
            elif float(p.get('in_qty') or 0) > 0: problems.append('商品 '+p['sku_id']+' 已存在ERP入库数量，请核查补开的采购单')
        jobs = [{k:v for k,v in j.items() if k!='snapshot_json'} for j in self.store.inbound_jobs(sid)]
        locked = self.locked(sid)
        current = meta['checked_revision'] == meta['revision']
        return {'session':s,'meta':meta,'batches':batches,'rows':rows,'jobs':jobs,'locked':locked,'linked':linked,'checked':current,'total':sum(float(a['recv']) for a in actual.values()),'sku_count':len(actual),'problems':problems,'can_create':bool(linked and current and rows and actual and all(r['kind']=='good' for r in rows) and not problems and not locked)}

    def load_purchase(self, sid, po_id, owner, auth):
        raw = self.client.fetch_detail(po_id, owner, auth)
        values = raw.get('datas') or []
        items = [normalize_po_item(r) for r in values]
        if not items or any(not p['sku_id'] for p in items): raise ValueError('采购单明细为空或商品编码无效')
        for p in items: qty(p['qty'])
        digest = hashlib.sha256(json_text(values).encode()).hexdigest()
        with self.store.db:
            self.store.db.execute('UPDATE session SET po_id=?,owner_co_id=?,auth_co_id=?,items_json=? WHERE id=?', (po_id,owner,auth,json.dumps(items,ensure_ascii=False),sid))
            self.store.db.execute('UPDATE unplanned_meta SET checked_revision=revision,checked_at=?,purchase_digest=? WHERE sid=?', (now_str(),digest,sid))
            by_sku = {p['sku_id']:p for p in items}
            for row in self.store.db.execute('SELECT id,sku_id FROM line WHERE session_id=?',(sid,)).fetchall():
                p = by_sku.get(row['sku_id'],{})
                self.store.db.execute('UPDATE line SET matched=?,poi_id=? WHERE id=?',(int(bool(p)),p.get('poi_id',''),row['id']))

    def refresh(self, sid):
        s = self.session(sid)
        if self.locked(sid): raise ValueError('该批次已提交，请继续原单')
        if not s['po_id']: raise ValueError('请先关联补开的采购单')
        # Invalidate earlier verification even when the refresh fails.
        self.store.db.execute('UPDATE unplanned_meta SET checked_revision=-1 WHERE sid=?',(sid,)); self.store.db.commit()
        self.load_purchase(sid,s['po_id'],s['owner_co_id'],s['auth_co_id'])

    def batch(self, sid, bid):
        row = self.store.db.execute('SELECT * FROM batch WHERE id=? AND session_id=?',(bid,sid)).fetchone()
        if not row: raise ValueError('物流单与当前批次不匹配')
        return dict(row)

    def line(self, sid, bid, lid):
        self.batch(sid,bid)
        row = self.store.db.execute('SELECT * FROM line WHERE id=? AND session_id=? AND batch_id=?',(lid,sid,bid)).fetchone()
        if not row: raise ValueError('商品明细与当前物流单不匹配')
        return dict(row)

    def candidates(self, sid, code):
        s = self.session(sid)
        local = [dict(r) for r in self.store.db.execute('SELECT * FROM line WHERE session_id=?',(sid,))]
        base = json.loads(s['items_json'] or '[]') + local
        hit = match_item(base,code)
        rows = hit.get('items',[]) or ([hit['item']] if hit.get('item') else [])
        found = self.lookup(code,s.get('owner_co_id',''),s.get('auth_co_id',''),all_matches=True)
        if found: rows += found if isinstance(found,list) else [found]
        unique = {}
        for r in rows:
            if r.get('sku_id'):
                key = str(r['sku_id']).strip().lower()
                unique.setdefault(key,{k:r.get(k,'') for k in ('sku_id','sku_code','name','spec','i_id')})
                if 'enabled' in r: unique[key]['enabled'] = r['enabled']
        return self.scan_filter(list(unique.values()),s.get('owner_co_id',''),s.get('auth_co_id',''))

    def mutate(self, action, body):
        sid = int(body.get('sid') or 0)
        if action == 'new':
            no=clean(body.get('tracking_no'),200)
            if not no: raise ValueError('请先扫描或输入第一个物流单号')
            return self.new(body.get('note',''),no)
        self.guard(sid,body.get('revision'))
        bid = int(body.get('batch_id') or 0)
        if action == 'resolve': return dict(candidates=self.candidates(sid,clean(body.get('code'),200)),selection_required=True)
        if action == 'note':
            self.store.db.execute('UPDATE unplanned_meta SET note=? WHERE sid=?',(clean(body.get('note')),sid));self.store.db.commit()
        elif action == 'tracking':
            no = clean(body.get('tracking_no'),200)
            if not no: raise ValueError('请输入物流单号')
            batch, _ = self.store.ensure_batch(sid,no)
            bid = batch['id']
        elif action == 'rename':
            self.batch(sid,bid); no=clean(body.get('tracking_no'),200)
            if not no: raise ValueError('请输入物流单号')
            self.store.rename_batch(bid,no)
        elif action == 'delete_batch':
            self.batch(sid,bid); self.store.del_batch(bid)
        elif action in ('scan','manual'):
            self.batch(sid,bid)
            n = qty(body.get('qty',1))
            if not n: raise ValueError('新增数量必须大于零')
            if action == 'scan':
                code = clean(body.get('code'),200)
                rows = self.candidates(sid,code)
                chosen = clean(body.get('sku_id'),200)
                if chosen: rows = [r for r in rows if r['sku_id']==chosen]
                if len(rows)!=1: return {'candidates':rows,'selection_required':True,'code':code}
                p = rows[0]
            else:
                p = {k:clean(body.get(k),300) for k in ('sku_id','sku_code','name','spec')}
                if not p['sku_id'] or not p['name']: raise ValueError('商品编码和名称不能为空')
            p = dict(p,matched=False)
            old = self.store.db.execute('SELECT qty FROM line WHERE batch_id=? AND sku_id=?',(bid,p['sku_id'])).fetchone()
            qty(n + (float(old['qty']) if old else 0))
            self.store.add_qty(bid,sid,p,n)
        elif action in ('edit','delete_line'):
            lid=int(body.get('line_id') or 0);self.line(sid,bid,lid)
            if action=='delete_line': self.store.del_line(lid)
            else:
                p={k:clean(body.get(k),300) for k in ('sku_id','sku_code','name','spec')}
                if not p['sku_id'] or not p['name']: raise ValueError('商品编码和名称不能为空')
                n=qty(body.get('qty'))
                if self.store.db.execute('SELECT 1 FROM line WHERE batch_id=? AND sku_id=? AND id<>?',(bid,p['sku_id'],lid)).fetchone(): raise ValueError('该物流单已有此SKU，请先核对数量，不能覆盖合并')
                with self.store.db:
                    self.store.db.execute('UPDATE line SET key=?,sku_id=?,sku_code=?,name=?,spec=?,qty=?,matched=0,poi_id="",updated_at=? WHERE id=?',('sku:'+p['sku_id'],p['sku_id'],p['sku_code'],p['name'],p['spec'],n,now_str(),lid))
        elif action == 'match':
            no=clean(body.get('po_id'),200)
            if not no: raise ValueError('请输入补开的采购单号')
            self.store.db.execute('UPDATE unplanned_meta SET checked_revision=-1 WHERE sid=?',(sid,));self.store.db.commit()
            row,ctx=self.find_purchase(no)
            if str(row.get('po_id'))!=no and str(row.get('outer_po_id',''))!=no:
                raise ValueError('未找到精确匹配的采购单，请检查单号')
            self.load_purchase(sid,str(row['po_id']),str(ctx['owner']),str(ctx['auth']))
            return self.state(sid)
        elif action == 'refresh':
            self.refresh(sid);return self.state(sid)
        else: raise ValueError('不支持的操作')
        self.touch(sid)
        result=self.state(sid);result['current_batch']=bid
        return result

    def export(self, sid):
        state=self.state(sid)
        out=io.StringIO(newline=''); w=csv.writer(out)
        def safe(v):
            if isinstance(v,str) and v.startswith(('=','+','-','@',chr(9),chr(13))): return "'"+v
            return v
        def row(values): w.writerow([safe(v) for v in values])
        row(['无采购单收货批次',state['session']['input_no'],'采购单',state['session']['po_id']])
        row(['批次汇总','商品编码','名称','规格','实收']+(['应收','核对结果'] if state['linked'] else []))
        for r in state['rows']: row(['',r['sku_id'],r['name'],r['spec'],r['recv']]+([r['plan'],r['reason']] if state['linked'] else []))
        row([]);row(['物流单号','商品编码','名称','规格','实收'])
        for b in state['batches']:
            for r in b['lines']: row([b['tracking_no'],r['sku_id'],r['name'],r['spec'],r['qty']])
        return ('﻿'+out.getvalue()).encode('utf-8')

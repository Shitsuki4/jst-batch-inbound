"""Persistent PO watch. No ERP writes: polling uses a detached login snapshot.
Receipt baselines are read-only. Draft completion is never inventory confirmation.
"""
import copy
import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from decimal import Decimal
from inbound import InboundService, InboundError, PurchaseInAPI, json_text, quantity
from jst import normalize_po_item
from receiving_round import aggregate, load
from store import now_str

STATES = ('pending', 'modified', 'matched')
def digest(x): return hashlib.sha256(json_text(x).encode('utf-8')).hexdigest()
def identity(s): return tuple(str(s.get(k) or '') for k in ('po_id','owner_co_id','auth_co_id'))
def canonical_rows(rows, version=False):
    # UI row tokens/order are not purchase edits. ERP inbound counters must not
    # silently cancel a user's override of the same purchase/receipt revision.
    cleaned=[{k:v for k,v in row.items() if not k.startswith('__') and not (version and k in ('in_qty','in_qty_known'))} for row in rows]
    return sorted(cleaned,key=json_text)

def login(client): return (str(client.uid),str(client.coid),digest(client.cookies))
def modified(row):
    # Only full date-time values are eligible; date-only/absent is not a change.
    value = str(row.get('modified') or row.get('modify_date') or row.get('modified_at') or '').strip()
    try:
        if ':' not in value: return ''
        datetime.fromisoformat(value.replace('Z','+00:00'))
        return value
    except ValueError: return ''

class PurchaseWatch:
    def __init__(self, path, client, sources, lock):
        self.client, self.sources, self.lock = client, sources, lock
        self.db=sqlite3.connect(path,check_same_thread=False); self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE IF NOT EXISTS watch(
            id INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, watch_id INTEGER, at TEXT, action TEXT, note TEXT);
        ''');self.db.commit()
        self.poll_lock=threading.Lock();self.stop=threading.Event()
    def stores(self): return self.sources()
    def save(self,w):
        self.db.execute('UPDATE watch SET data=? WHERE id=?',(json_text(w),w['id']));self.db.commit()
    def get(self,wid,include_deleted=False):
        r=self.db.execute('SELECT data FROM watch WHERE id=?',(int(wid),)).fetchone()
        if not r: raise ValueError('采购跟踪记录不存在')
        w=json.loads(r['data'])
        if [str(self.client.uid),str(self.client.coid)] != w['account']: raise ValueError('请登录登记采购跟踪时的公司账号')
        if w.get('deleted') and not include_deleted: raise ValueError('此待改单跟踪已删除，请从收货记录重新标记')
        return w
    def audit(self,w,action,note=''):
        self.db.execute('INSERT INTO audit(watch_id,at,action,note) VALUES(?,?,?,?)',(w['id'],now_str(),action,str(note)[:500]));self.db.commit()
    def register(self,source,sid):
        store=self.stores().get(source)
        if not store: raise ValueError('收货来源不存在')
        s=store.get_session(int(sid))
        if not s or not s.get('po_id'): raise ValueError('请先关联采购单')
        if not self.client.uid or not self.client.coid: raise ValueError('请先登录聚水潭')
        key=identity(s)
        if not key[2] or '2147483647' in key[1:]: raise ValueError('采购单缺少明确的货主/仓库公司')
        ident=json_text([str(self.client.uid),str(self.client.coid),*key])
        old=self.db.execute('SELECT id FROM watch WHERE identity=?',(ident,)).fetchone()
        if old and not self.get(old['id'],include_deleted=True).get('deleted'): return self.get(old['id'])
        w=dict(account=[str(self.client.uid),str(self.client.coid)],po_id=key[0],owner_co_id=key[1],auth_co_id=key[2],state='pending',notice='',manual=None,modified='',version='',receipt='',checked_at='',error='',rows=[],problems=['首次核对尚未完成'],complete=False,inbound_complete=False,po_complete=False,sources=[],note='',created_at=now_str())
        w['tracking_token']=uuid.uuid4().hex
        if old: w['id']=old['id']
        else:
            c=self.db.execute('INSERT INTO watch(identity,data) VALUES(?,?)',(ident,'{}'));w['id']=c.lastrowid
        self.save(w);self.audit(w,'重新登记待改单' if old else '登记待改单')
        return w
    def listing(self):
        account=[str(self.client.uid),str(self.client.coid)]
        rows=[json.loads(r['data']) for r in self.db.execute('SELECT data FROM watch ORDER BY id DESC')]
        rows=[w for w in rows if w['account']==account and not w.get('deleted')]
        return dict(rows=rows,yellow=sum(w['notice']=='modified' and not w['complete'] for w in rows),red=sum(w['notice']=='matched' and not w['complete'] for w in rows),interval=60)
    def delete(self,wid,confirmed=False):
        if confirmed is not True: raise ValueError('请明确确认仅删除本地待改单跟踪')
        w=self.get(wid)
        if w['state']!='pending' or w['complete']: raise ValueError('仅未完成的待改单记录可以删除，请刷新状态后重试')
        # Tombstone only. Receipt lines, ERP POs and inbound/idempotency records stay intact.
        w.update(deleted=True,deleted_at=now_str(),tracking_token=uuid.uuid4().hex)
        self.save(w);self.audit(w,'删除待改单跟踪','仅移除本地跟踪，不删除采购单、收货数据或入库记录')
        return dict(id=w['id'],deleted=True)
    def archive(self,wid,note='',confirmed=False):
        w=self.get(wid)
        if confirmed is not True: raise ValueError('请确认仅完成本地跟踪，不代表ERP库存确认')
        if w['state']!='matched' or w['complete']: raise ValueError('仅未完成的已匹配记录可以标记完成')
        w.update(archive_override='closed',complete=True,completed_at=now_str(),completion_method='manual',
                 completion_note=str(note)[:500],completion_revision=[w.get('version',''),w.get('receipt','')],
                 completion_changed=False,tracking_token=uuid.uuid4().hex)
        self.save(w);self.audit(w,'手动完成本地跟踪',note)
        return w
    def transition(self,wid,state,note='',automatic=False):
        w=self.get(wid)
        if state not in STATES: raise ValueError('状态无效')
        reopening=bool(w.get('complete'))
        w['state']=state;w['note']=str(note)[:500]
        w['manual']=None if automatic else dict(version=w['version'],receipt=w['receipt'])
        if automatic: w['archive_override']=None
        elif reopening: w.update(archive_override='open',complete=False,reopened_at=now_str())
        if state!='pending': w['notice']=state
        w['tracking_token']=uuid.uuid4().hex
        self.save(w);self.audit(w,'恢复自动' if automatic else ('重新打开并转为 ' if reopening else '手动转为 ')+state,note)
        return w
    def capture(self,w):
        groups=[]; actual={};seen_tracking={};problems=[]; all_jobs=[]
        for source,store in self.stores().items():
            for r in store.db.execute('SELECT * FROM session WHERE po_id=? AND owner_co_id=? AND auth_co_id=? ORDER BY id',identity(w)):
                s=dict(r);batches=store.batches_with_lines(s['id']);jobs=store.inbound_jobs(s['id'])
                groups.append(dict(source=source,sid=s['id'],input_no=s['input_no'],started_at=s['started_at'],baseline=s.get('round_baseline_json'),batches=batches,jobs=jobs))
                for b in batches:
                    no=b['tracking_no']
                    if no in seen_tracking: problems.append('同采购单存在重复物流单号 '+no+'，请核实范围，禁止重复计数/开单')
                    seen_tracking[no]=True
                    for line in b['lines']:
                        sku=str(line['sku_id']);n=quantity(line['qty'])
                        if not n:continue
                        entry=actual.setdefault(sku,dict(line,recv=Decimal(0)));entry['recv']+=n
                all_jobs += [(source,j) for j in jobs if j['status']!='rejected']
        if not groups: problems.append('原收货记录已不存在，请核实')
        groups.sort(key=lambda g:(g['started_at'],g['source'],g['sid']))
        basis={}
        if groups:
            first=groups[0]
            if first['source']=='receipt':
                if first['baseline'] is None: problems.append('旧记录缺少起始应收基线，不能猜测本轮范围')
                else:
                    for sku,r in aggregate(load(first['baseline'])).items():
                        if r['erp_received'] is None:problems.append('起始已入库量未知：'+sku)
                        else:basis[sku]=r['erp_received']
            # A no-PO receipt starts with zero prior receipt; ERP nonzero must be accounted for by verified jobs.
        revision=digest([dict(source=g['source'],sid=g['sid'],baseline=g['baseline'],batches=[dict(id=b['id'],tracking_no=b['tracking_no'],lines=[{k:l[k] for k in ('id','sku_id','name','spec','qty')} for l in b['lines']]) for b in g['batches']]) for g in groups])
        return dict(groups=groups,actual=actual,basis=basis,problems=problems,jobs=all_jobs,receipt=revision)
    def read(self,w,cap,client):
        po,owner,auth=identity(w)
        candidates,_=client.search_po(po,owner,auth,'po_id')
        candidates=[r for r in candidates if str(r.get('po_id'))==po]
        if len(candidates)!=1:raise ValueError('未读到唯一的原采购单；保留上次可靠状态')
        head=candidates[0]
        # No cross-company fallback. All fields must confirm the exact original scope.
        if str(head.get('owner_co_id',''))!=owner or str(head.get('wms_co_id') or head.get('co_id') or '')!=auth:raise ValueError('采购单货主或仓库公司与原记录不一致')
        raw=client.fetch_detail(po,owner,auth).get('datas')
        if not isinstance(raw,list) or not raw:raise ValueError('采购单明细读取失败或为空')
        items=[normalize_po_item(r) for r in raw];plan=aggregate(items);rows=[];problems=list(cap['problems'])
        confirmed={};inbound_complete=bool(cap['actual']); seen_io=set()
        api=PurchaseInAPI(client,owner if owner!='0' else auth,auth)
        linked_lines=set()
        for source,j in cap['jobs']:
            snap=json.loads(j['snapshot_json'])
            for b in snap['batches']:
                for line in b['lines']:linked_lines.add((source,line['id']))
            if not j['io_id']: inbound_complete=False;continue
            if j['io_id'] in seen_io:problems.append('多个本地记录关联同一入库单，需人工核实范围');inbound_complete=False;continue
            seen_io.add(j['io_id'])
            record=api.row(j['io_id']);api.check_source(record,snap)
            if record.get('status')!='已入库':inbound_complete=False;continue
            desired={p['sku_id']:quantity(p['qty']) for p in snap['items']}
            api.verify(api.details(j['io_id']),desired)
            for sku,n in desired.items():confirmed[sku]=confirmed.get(sku,Decimal(0))+n
        for g in cap['groups']:
            for b in g['batches']:
                for line in b['lines']:
                    if quantity(line['qty']) and (g['source'],line['id']) not in linked_lines:inbound_complete=False
        for sku in list(plan)+[k for k in cap['actual'] if k not in plan]:
            p=plan.get(sku);a=cap['actual'].get(sku);meta=p or a;recv=a['recv'] if a else Decimal(0)
            target=max(p['ordered']-cap['basis'].get(sku,Decimal(0)),Decimal(0)) if p and p['ordered'] is not None else (None if p else Decimal(0))
            if not sku or (p and p['row_count']!=1):problems.append('采购单编码无效或重复SKU：'+sku)
            if p:
                if p['erp_received'] is None:problems.append('ERP已入库数量未知：'+sku)
                elif p['erp_received']!=cap['basis'].get(sku,Decimal(0))+confirmed.get(sku,Decimal(0)):
                    problems.append('ERP已入库量与起始基线及本地已确认单据不一致：'+sku+'；请核实外部/多轮入库范围')
            kind='unknown' if target is None else 'extra' if not p else 'good' if recv==target else 'diff'
            reason='待核实' if target is None else '采购单外' if not p else '一致' if recv==target else ('少收 ' if recv<target else '多收 ')+str(abs(recv-target))
            rows.append(dict(sku_id=sku,name=meta.get('name',''),spec=meta.get('spec',''),sku_code=meta.get('sku_code',''),plan=float(target) if target is not None else None,recv=float(recv),kind=kind,reason=reason))
        if any(r['plan'] is None for r in rows):problems.append('采购应收量未知')
        return dict(rows=rows,problems=problems,modified=modified(head),version=digest([modified(head),canonical_rows(raw,True)]),purchase_digest=digest([{k:head.get(k) for k in ('po_id','owner_co_id','wms_co_id','co_id','status')},modified(head),canonical_rows(raw)]),inbound_complete=inbound_complete and not problems,po_complete=head.get('status') in ('完成','已完成'),po_status=str(head.get('status','')),items=items)
    def apply(self,w,cap,result):
        old_mod=w['modified'];old_rev=w['receipt'];manual=w['manual']
        if manual and ((w['version'] and manual['version']!=result['version']) or (old_rev and manual['receipt']!=cap['receipt'])):manual=None
        actual_match=bool(cap['actual']) and bool(result['rows']) and not result['problems'] and all(r['kind']=='good' for r in result['rows'])
        if not manual and not result['problems']:
            if actual_match:w['state']='matched'
            elif old_mod and result['modified'] and old_mod!=result['modified']:w['state']='modified'
            elif old_rev and old_rev!=cap['receipt']:w['state']='pending'
        if w['state']!='pending':w['notice']=w['state']
        if manual and not w['version']: manual=dict(version=result['version'],receipt=cap['receipt'])
        w.update(result);w.update(receipt=cap['receipt'],manual=manual,checked_at=now_str(),error='',actual_match=actual_match)
        if not result['modified']:w['time_warning']='ERP未提供完整最后修改时间：不能自动判定已改单，可手动切换；SKU数量仍会核对'
        else:w['time_warning']=''
        w['sources']=[dict(source=g['source'],sid=g['sid'],input_no=g['input_no'],batches=len(g['batches']),jobs=[{k:v for k,v in j.items() if k!='snapshot_json'} for j in g['jobs']]) for g in cap['groups']]
        erp_complete=w['inbound_complete'] and w['po_complete']
        override=w.get('archive_override')
        w['complete']=True if override=='closed' else False if override=='open' else erp_complete
        if override=='closed':
            w['completion_changed']=w.get('completion_revision')!=[result['version'],cap['receipt']]
        elif w['complete']:
            if w.get('completion_method')!='erp': w['completed_at']=now_str()
            w['completion_method']='erp'

        self.save(w)
        return w
    def poll_one(self,wid):
        with self.lock:
            w=self.get(wid);cap=self.capture(w);token=login(self.client)
            client=self.client.clone_for_catalog() if hasattr(self.client,'clone_for_catalog') else copy.copy(self.client)
            client.cookies=copy.deepcopy(self.client.cookies)
        try: result=self.read(w,cap,client)
        except Exception as e:
            with self.lock:
                if token==login(self.client):
                    current=self.get(wid)
                    if current.get('tracking_token')!=w.get('tracking_token'):return
                    current['error']=str(e);self.save(current)
            return
        with self.lock:
            if token!=login(self.client):return
            current=self.get(wid)
            if current.get('tracking_token')!=w.get('tracking_token'):return
            if self.capture(current)['receipt']!=cap['receipt']:return
            previous=current['state'];self.apply(current,cap,result)
            if current['state']!=previous:self.audit(current,'自动 '+previous+' → '+current['state'])
    def poll(self):
        if not self.poll_lock.acquire(False):return
        try:
            with self.lock:
                if not self.client.uid or not self.client.coid:return
                ids=[w['id'] for w in self.listing()['rows']]
            for wid in ids:
                try:self.poll_one(wid)
                except Exception:pass  # Account switched/deleted; next cycle retries without overwriting reliable status.
        finally:self.poll_lock.release()
    def start(self):
        def worker():
            while not self.stop.is_set():
                self.poll();self.stop.wait(60)
        threading.Thread(target=worker,name='purchase-watch-60s',daemon=True).start()
    def cross_guard(self,store,sid):
        s=store.get_session(sid)
        if not s:return
        current={b['tracking_no'] for b in store.batches(sid)}
        for other in self.stores().values():
            for row in other.db.execute("SELECT j.*,s.po_id,s.owner_co_id,s.auth_co_id FROM inbound_job j JOIN session s ON s.id=j.session_id WHERE j.status<>'rejected'"):
                j=dict(row)
                if other is store and j['session_id']==sid:continue
                if identity(j)!=identity(s):continue
                snap=json.loads(j['snapshot_json']);nos={b['tracking_no'] for b in snap['batches']}
                if other is not store or current.intersection(nos) or j['status'] not in ('complete','rejected'):
                    raise InboundError('此采购单在其他来源/批次已有入库记录，请继续原单并核实，禁止跨来源重复生成')
    def inbound(self,wid,source,sid):return WatchInbound(self,wid,source,int(sid))

class WatchInbound(InboundService):
    def __init__(self,watch,wid,source,sid):
        self.watch,self.wid,self.source,self.sid=watch,int(wid),source,sid
        w=watch.get(self.wid)
        if not any(g['source']==source and g['sid']==sid for g in watch.capture(w)['groups']):raise InboundError('此批次不属于采购跟踪单')
        stores=watch.stores()
        if source not in stores:raise InboundError('来源无效')
        super().__init__(stores[source],watch.client);self.cross_guard=watch.cross_guard
    def preview(self,sid,wh_id=None):
        if sid!=self.sid:raise InboundError('收货批次不匹配')
        w=self.watch.get(self.wid);cap=self.watch.capture(w)
        if not any(g['source']==self.source and g['sid']==sid for g in cap['groups']):raise InboundError('此批次不属于采购跟踪单')
        if w['state'] not in ('modified','matched'):raise InboundError('请先转为已改单或已匹配')
        self.cross_guard(self.store,sid)
        # Synchronous revalidation is mandatory immediately before any explicit write.
        result=self.watch.read(w,cap,self.client)
        if result['problems']:raise InboundError('；'.join(result['problems']))
        if any(p.get('row_count',1)>1 for p in aggregate(result['items']).values()):raise InboundError('采购重复SKU不能确定成本来源')
        preview=super().preview(sid,wh_id,validate_override=False)
        snap=preview['snapshot'];by={p['sku_id']:p for p in result['items']}
        for item in snap['items']:item['outside_purchase']=item['sku_id'] not in by
        if not snap['items']:raise InboundError('全部实收已提交，请查看或继续原单，不可重复生成')
        permission=dict(watch_id=self.wid,source=self.source,receipt=cap['receipt'],purchase=result['purchase_digest'],state=w['state'])
        snap['purchase_watch_permission']=permission
        preview.update(fingerprint=digest(snap),differences=[r for r in result['rows'] if r['kind']!='good'],requires_difference_confirmation=any(r['kind']!='good' for r in result['rows']))
        preview['warnings']=['按此批次全部尚未提交物流单的真实SKU和数量生成待入库；不补少收、不删除单外、不替换商品、不修改采购单。','价格/成本/供应商均不写入；采购行由ERP继承，单外商品无采购价格可继承，请在ERP核实。','仅生成待入库草稿，不自动确认库存。']
        return preview
    def create(self,sid,fingerprint,wh_id=None):
        self.cross_guard(self.store,sid)
        return super().create(sid,fingerprint,wh_id)

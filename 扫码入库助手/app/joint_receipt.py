"""Joint receipts. Physical lines exist once; durable per-line allocations reserve quantities.
ERP drafts remain ONE purchase each. No inventory confirmation or price writes.
"""
import csv
import hashlib
import io
import json
from decimal import Decimal
from inbound import InboundService, InboundError, json_text, quantity
from jst import normalize_po_item
from receiving_round import aggregate
from store import now_str
from unplanned import UnplannedService, clean, qty


def digest(value):
    return hashlib.sha256(json_text(value).encode('utf-8')).hexdigest()


def canonical(rows):
    return sorted([{k:v for k,v in r.items() if not k.startswith('__')} for r in rows],key=json_text)


class JointInbound(InboundService):
    def __init__(self, service):
        super().__init__(service.store, service.client)
        self.service=service

    def resume(self,job_id,io_id=''):
        job=self.store.get_inbound(job_id)
        if not job: raise InboundError('入库记录不存在')
        stage=self.service.stage(job['session_id'])
        if stage['cancelled']: raise InboundError('此分配已取消')
        return super().resume(job_id,io_id)

    def preview(self,sid,wh_id=None,*,mode=None,**kwargs):
        stage=self.service.stage(sid)
        self.service.group(stage['group_sid'])
        if stage['cancelled']: raise InboundError('此分配已取消，请重新分配')
        if mode is not None and mode!=stage['mode']: raise InboundError('入库方式与已确认分配不一致')
        po=self.service.purchase(stage['group_sid'],stage['purchase_id'])
        raw=self.service.read_detail(po)
        items=[normalize_po_item(r) for r in raw]
        counts=aggregate(items)
        for r in counts.values():
            if not r['sku_id'] or r['row_count']!=1 or r['ordered'] is None or r['erp_received'] is None:
                raise InboundError('采购明细重复或数量未知，请先在ERP处理后刷新')
        # A staged allocation is immutable; changed source requires cancellation/reconfirmation.
        if digest(canonical(raw))!=stage['purchase_digest']:
            raise InboundError('采购单在分配确认后有变化，请刷新核对，取消尚未提交的分配后重新确认；已提交请继续原单')
        p=super().preview(sid,wh_id,mode=stage['mode'])
        p['snapshot']['joint_allocation']=[dict(r) for r in self.store.db.execute('SELECT source_line,qty FROM joint_allocation WHERE stage_sid=? ORDER BY id',(sid,))]
        p['snapshot']['joint_group']=stage['group_sid']
        p['snapshot']['purchase_digest']=stage['purchase_digest']
        p['fingerprint']=digest(p['snapshot'])
        return p


class JointService:
    def __init__(self,path,client,find_purchase,lookup,scan_filter=None):
        self.client=client;self.find_purchase=find_purchase
        self.receipt=UnplannedService(path,client,find_purchase,lookup,scan_filter)
        self.store=self.receipt.store
        self.store.db.executescript('''
        CREATE TABLE IF NOT EXISTS joint_group(sid INTEGER PRIMARY KEY,account TEXT NOT NULL,mother TEXT NOT NULL,expected INTEGER,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS joint_purchase(id INTEGER PRIMARY KEY,group_sid INTEGER NOT NULL,po_id TEXT NOT NULL,owner TEXT NOT NULL,auth TEXT NOT NULL,head_json TEXT NOT NULL,initial_json TEXT NOT NULL,live_json TEXT NOT NULL,read_at TEXT NOT NULL,UNIQUE(group_sid,po_id));
        CREATE TABLE IF NOT EXISTS joint_stage(sid INTEGER PRIMARY KEY,group_sid INTEGER NOT NULL,purchase_id INTEGER NOT NULL,mode TEXT NOT NULL,purchase_digest TEXT NOT NULL,cancelled INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS joint_allocation(id INTEGER PRIMARY KEY,group_sid INTEGER NOT NULL,stage_sid INTEGER NOT NULL,source_line INTEGER NOT NULL,qty TEXT NOT NULL,UNIQUE(stage_sid,source_line));
        CREATE TABLE IF NOT EXISTS joint_package(batch_id INTEGER PRIMARY KEY,finished INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS joint_audit(id INTEGER PRIMARY KEY,group_sid INTEGER,at TEXT,action TEXT,detail TEXT);
        ''');self.store.db.commit()
        self.inbound=JointInbound(self)

    def account(self):
        if not self.client.uid or not self.client.coid: raise ValueError('请先登录聚水潭')
        return json_text([str(self.client.uid),str(self.client.coid)])

    def group(self,sid):
        r=self.store.db.execute('SELECT * FROM joint_group WHERE sid=?',(int(sid),)).fetchone()
        if not r: raise ValueError('联合收货批次不存在')
        if r['account']!=self.account(): raise ValueError('请登录创建联合批次时的公司账号')
        return dict(r)

    def purchase(self,sid,pid):
        self.group(sid)
        r=self.store.db.execute('SELECT * FROM joint_purchase WHERE group_sid=? AND id=?',(sid,int(pid))).fetchone()
        if not r: raise ValueError('采购单不属于当前联合批次')
        return dict(r)

    def stage(self,sid):
        r=self.store.db.execute('SELECT * FROM joint_stage WHERE sid=?',(int(sid),)).fetchone()
        if not r: raise ValueError('分配记录不存在')
        self.group(r['group_sid'])
        return dict(r)

    def audit(self,sid,action,detail=''):
        self.store.db.execute('INSERT INTO joint_audit(group_sid,at,action,detail) VALUES(?,?,?,?)',(sid,now_str(),action,str(detail)[:4000]));self.store.db.commit()

    def guard(self,sid,revision):
        self.group(sid)
        current=self.store.db.execute('SELECT revision FROM unplanned_meta WHERE sid=?',(sid,)).fetchone()[0]
        if revision is None or int(revision)!=current: raise ValueError('联合批次已变化，请刷新后重试')

    def read_detail(self,po):
        raw=self.client.fetch_detail(po['po_id'],po['owner'],po['auth']).get('datas')
        if not isinstance(raw,list) or not raw: raise ValueError('采购单 '+po['po_id']+' 明细读取失败或为空')
        return raw

    def resolve_po(self,no):
        head,ctx=self.find_purchase(no)
        if no not in (str(head.get('po_id')),str(head.get('outer_po_id',''))): raise ValueError('未找到精确采购单：'+no)
        owner,auth=str(ctx.get('owner') or ''),str(ctx.get('auth') or '')
        if not owner or not auth or '2147483647' in (owner,auth): raise ValueError('请选择具体货主和仓库公司')
        if head.get('owner_co_id') is not None and str(head['owner_co_id'])!=owner: raise ValueError('货主身份不一致')
        if head.get('wms_co_id') is not None and str(head['wms_co_id'])!=auth: raise ValueError('仓库身份不一致')
        po=dict(po_id=str(head['po_id']),owner=owner,auth=auth,head=head)
        po['raw']=self.read_detail(po)
        return po

    def new(self,body):
        account=self.account();mother=clean(body.get('mother'),200)
        nos=list(dict.fromkeys(clean(x,200) for x in body.get('purchase_nos',[]) if clean(x,200)))
        if not mother or not nos or len(nos)>30: raise ValueError('请输入母单 / 整票号以及1至30张采购单')
        if body.get('confirmed') is not True: raise ValueError('请人工确认这些采购单和母单属于同一票货')
        expected=body.get('expected')
        if expected not in (None,''):
            if str(expected)!=str(int(expected)) or not 1<=int(expected)<=10000: raise ValueError('预计子件数应为1至10000的整数，或留空')
            expected=int(expected)
        else: expected=None
        if self.store.db.execute('SELECT 1 FROM joint_group WHERE account=? AND mother=?',(account,mother)).fetchone(): raise ValueError('此母单已有联合批次，请查询原批次继续，不能重复新建')
        pos=[self.resolve_po(no) for no in nos];pos=list({p['po_id']:p for p in pos}.values())
        if len({(p['owner'],p['auth']) for p in pos})!=1: raise ValueError('只能合并同货主、同仓库公司的采购单')
        s=self.receipt.new(body.get('note',''));sid=s['session']['id']
        with self.store.db:
            self.store.db.execute('UPDATE session SET input_no=?,id_type=?,owner_co_id=?,auth_co_id=? WHERE id=?',('LH'+now_str()[:10].replace('-','')+'-'+str(sid).zfill(4),'joint',pos[0]['owner'],pos[0]['auth'],sid))
            self.store.db.execute('UPDATE session SET items_json=? WHERE id=?',(json_text([normalize_po_item(r) for p in pos for r in p['raw']]),sid))
            self.store.db.execute('INSERT INTO joint_group VALUES(?,?,?,?,?)',(sid,account,mother,expected,now_str()))
            for p in pos:
                self.store.db.execute('INSERT INTO joint_purchase(group_sid,po_id,owner,auth,head_json,initial_json,live_json,read_at) VALUES(?,?,?,?,?,?,?,?)',(sid,p['po_id'],p['owner'],p['auth'],json_text(p['head']),json_text(p['raw']),json_text(p['raw']),now_str()))
        self.audit(sid,'创建联合批次','母单归属经人工确认')
        return self.state(sid)

    def refresh(self,sid):
        self.group(sid)
        pos=[dict(r) for r in self.store.db.execute('SELECT * FROM joint_purchase WHERE group_sid=?',(sid,))]
        reads=[(self.read_detail(p),p['id']) for p in pos]  # all-or-none, never turn a failed read into zero
        with self.store.db:
            for raw,pid in reads:self.store.db.execute('UPDATE joint_purchase SET live_json=?,read_at=? WHERE id=?',(json_text(raw),now_str(),pid))
        self.store.db.execute('UPDATE session SET items_json=? WHERE id=?',(json_text([normalize_po_item(r) for raw,pid in reads for r in raw]),sid));self.store.db.commit()
        self.receipt.touch(sid)
        return self.state(sid)

    def reserved(self,sid):
        out={}
        for r in self.store.db.execute('SELECT a.* FROM joint_allocation a JOIN joint_stage s ON s.sid=a.stage_sid WHERE a.group_sid=? AND s.cancelled=0',(sid,)):
            out[r['source_line']]=out.get(r['source_line'],Decimal(0))+quantity(r['qty'])
        return out

    def state(self,sid):
        g=self.group(sid);s=self.store.get_session(sid)
        meta=dict(self.store.db.execute('SELECT * FROM unplanned_meta WHERE sid=?',(sid,)).fetchone())
        pos=[];totals={};warnings=[]
        for row in self.store.db.execute('SELECT * FROM joint_purchase WHERE group_sid=? ORDER BY id',(sid,)):
            p=dict(row);initial=aggregate([normalize_po_item(r) for r in json.loads(p['initial_json'])]);live=aggregate([normalize_po_item(r) for r in json.loads(p['live_json'])]);p['items']=[]
            for sku,x in initial.items():
                n=max(x['ordered']-x['erp_received'],Decimal(0)) if x['ordered'] is not None and x['erp_received'] is not None else None
                if n is None: warnings.append(p['po_id']+' 部分起始数量未知，不能判定收齐')
                t=totals.setdefault(sku,dict(sku_id=x['sku_id'],name=x.get('name',''),spec=x.get('spec',''),plan=Decimal(0),recv=Decimal(0),sources=[]))
                t['plan']=t['plan']+n if t['plan'] is not None and n is not None else None
                t['sources'].append(dict(id=p['id'],po_id=p['po_id'],plan=float(n) if n is not None else None))
                p['items'].append(dict(sku_id=x['sku_id'],name=x.get('name',''),plan=float(n) if n is not None else None))
            p['changed']=canonical(json.loads(p['initial_json']))!=canonical(json.loads(p['live_json']))
            if p['changed']: warnings.append(p['po_id']+' 已较起始采购快照变化；汇总应收仍按本轮起始未入库量，入库前请核对最新明细')
            # New live SKUs are in purchase scope, but have no known starting target.
            for sku,x in live.items():
                if sku not in initial:
                    t=totals.setdefault(sku,dict(sku_id=x['sku_id'],name=x.get('name',''),spec=x.get('spec',''),plan=None,recv=Decimal(0),sources=[]));t['plan']=None;t['sources'].append(dict(id=p['id'],po_id=p['po_id'],plan=None))
            p['head']=json.loads(p.pop('head_json'));p['initial']=json.loads(p.pop('initial_json'));p['live']=json.loads(p.pop('live_json'));pos.append(p)
        reserved=self.reserved(sid);batches=self.store.batches_with_lines(sid)
        for b in batches:
            pack=self.store.db.execute('SELECT finished FROM joint_package WHERE batch_id=?',(b['id'],)).fetchone();b['finished']=bool(pack and pack[0])
            for line in b['lines']:
                r=quantity(line['qty']);line['reserved']=float(reserved.get(line['id'],0));line['available']=float(r-reserved.get(line['id'],0))
                if r<=0:continue
                t=totals.setdefault(line['sku_id'],dict(sku_id=line['sku_id'],name=line['name'],spec=line['spec'],plan=Decimal(0),recv=Decimal(0),sources=[]));t['recv']+=r
        rows=[];short=Decimal(0);over=Decimal(0);extra=Decimal(0)
        for t in totals.values():
            p,r=t['plan'],t['recv'];kind='unknown' if p is None else 'extra' if not t['sources'] else 'good' if r==p else 'diff'
            if kind=='extra':extra+=r
            elif p is not None:
                short+=max(p-r,Decimal(0));over+=max(r-p,Decimal(0))
            rows.append(dict(t,plan=float(p) if p is not None else None,recv=float(r),kind=kind,delta=float(r-p) if p is not None else None))
        stages=[]
        for row in self.store.db.execute('SELECT * FROM joint_stage WHERE group_sid=? AND cancelled=0 ORDER BY sid',(sid,)):
            st=dict(row);st['po_id']=self.store.get_session(st['sid'])['po_id'];st['jobs']=[{k:v for k,v in j.items() if k!='snapshot_json'} for j in self.store.inbound_jobs(st['sid'])];st['can_cancel']=not any(j['status']!='rejected' for j in st['jobs']);st['total']=float(sum((quantity(r['qty']) for r in self.store.db.execute('SELECT qty FROM joint_allocation WHERE stage_sid=?',(st['sid'],))),Decimal(0)));stages.append(st)
        return dict(session=s,group=g,meta=meta,purchases=pos,batches=batches,rows=rows,stages=stages,warnings=list(dict.fromkeys(warnings)),totals=dict(short=float(short),over=float(over),extra=float(extra)),total=sum(r['recv'] for r in rows))

    def history(self,query=''):
        account=self.account();out=[];q=str(query).lower().strip()
        for r in self.store.db.execute('SELECT s.id,s.input_no,s.started_at,g.mother FROM joint_group g JOIN session s ON s.id=g.sid WHERE g.account=? ORDER BY g.sid DESC',(account,)):
            d=dict(r);d['po_ids']=[p[0] for p in self.store.db.execute('SELECT po_id FROM joint_purchase WHERE group_sid=?',(d['id'],))];nos=[b['tracking_no'] for b in self.store.batches(d['id'])]
            if not q or any(q in str(v).lower() for v in [d['id'],d['input_no'],d['mother'],*d['po_ids'],*nos]):out.append(d)
        return out

    def cancel(self,sid,stage_sid):
        st=self.stage(stage_sid)
        if st['group_sid']!=sid: raise ValueError('分配不属于当前批次')
        if any(j['status']!='rejected' for j in self.store.inbound_jobs(stage_sid)): raise ValueError('已提交或结果待核实，不能取消数量占用；请继续原单')
        with self.store.db:
            self.store.db.execute('UPDATE joint_stage SET cancelled=1 WHERE sid=?',(stage_sid,))
        self.receipt.touch(sid);self.audit(sid,'取消未提交分配',str(stage_sid))

    def plan(self,sid,body):
        if body.get('confirmed') is not True: raise ValueError('请明确确认每个编码的采购单归属和数量')
        mode=body.get('mode')
        if mode not in ('purchase_only','all_received'): raise ValueError('请选择入库方式')
        allocations=body.get('allocations')
        if not isinstance(allocations,list) or not allocations: raise ValueError('请填写要分配的正数数量')
        report=self.state(sid);lines={r['id']:r for b in report['batches'] for r in b['lines']};purchases={p['id']:p for p in report['purchases']}
        used={};grouped={};live_sets={p['id']:{str(r.get('sku_id')) for r in p['live']} for p in report['purchases']}
        for a in allocations:
            lid,pid=int(a['line_id']),int(a['purchase_id']);n=quantity(a['qty'])
            if n<=0 or n>100000000:raise ValueError('分配数量应大于0且不超过一亿')
            if lid not in lines or pid not in purchases:raise ValueError('明细或采购单不属于当前批次')
            sku=lines[lid]['sku_id'];scope=[k for k,v in live_sets.items() if sku in v]
            if scope and pid not in scope:raise ValueError('组内编码必须分给包含该编码的采购单')
            if not scope and mode=='purchase_only':raise ValueError('仅采购范围内模式不能分配组外编码')
            used[lid]=used.get(lid,Decimal(0))+n
            if used[lid]>quantity(lines[lid]['available']):raise ValueError('分配之和超过此物流明细未占用的真实实收')
            bucket=grouped.setdefault(pid,{})
            bucket[lid]=bucket.get(lid,Decimal(0))+n
        # All remote reads before persisting; source version changes require a fresh report.
        for pid in grouped:
            p=self.purchase(sid,pid);raw=self.read_detail(p)
            if canonical(raw)!=canonical(purchases[pid]['live']):raise ValueError('采购单已变化，请刷新后重新分配')
            for x in aggregate([normalize_po_item(r) for r in raw]).values():
                if not x['sku_id'] or x['row_count']!=1 or x['ordered'] is None or x['erp_received'] is None:raise ValueError('采购单重复编码或数量未知，请处理后再分配')
        # Direct inserts in one local transaction. No helper with implicit commit inside this block.
        with self.store.db:
            for pid,quantities in grouped.items():
                p=purchases[pid];head=p['head']
                cur=self.store.db.execute('INSERT INTO session(input_no,po_id,id_type,supplier,owner_co_id,auth_co_id,state,started_at,items_json,round_baseline_json) VALUES(?,?,?,?,?,?,?,?,?,?)',(report['session']['input_no']+' / '+p['po_id'],p['po_id'],'joint-allocation',head.get('supplier_name') or head.get('supplier',''),p['owner'],p['auth'],'closed',now_str(),json_text([normalize_po_item(r) for r in p['live']]),json_text([normalize_po_item(r) for r in p['initial']])))
                stage=cur.lastrowid
                self.store.db.execute('INSERT INTO joint_stage(sid,group_sid,purchase_id,mode,purchase_digest,created) VALUES(?,?,?,?,?,?)',(stage,sid,pid,mode,digest(canonical(p['live'])),now_str()))
                bs={}
                for lid,n in quantities.items():
                    line=lines[lid];source=next(b for b in report['batches'] if b['id']==line['batch_id'])
                    if source['id'] not in bs:
                        c=self.store.db.execute('INSERT INTO batch(session_id,tracking_no,first_scan_at) VALUES(?,?,?)',(stage,source['tracking_no'],now_str()));bs[source['id']]=c.lastrowid
                    self.store.db.execute('INSERT INTO line(session_id,batch_id,key,sku_id,sku_code,name,spec,qty,matched,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(stage,bs[source['id']],'sku:'+line['sku_id'],line['sku_id'],line.get('sku_code',''),line['name'],line['spec'],float(n),int(line['sku_id'] in live_sets[pid]),now_str()))
                    self.store.db.execute('INSERT INTO joint_allocation(group_sid,stage_sid,source_line,qty) VALUES(?,?,?,?)',(sid,stage,lid,str(n)))
        self.receipt.touch(sid);self.audit(sid,'确认采购归属 / 占用实收',json_text(allocations))
        return self.state(sid)

    def mutate(self,action,body):
        if action=='new':return self.new(body)
        sid=int(body.get('sid') or 0);self.guard(sid,body.get('revision'));bid=int(body.get('batch_id') or 0)
        if action=='refresh':return self.refresh(sid)
        if action=='plan':return self.plan(sid,body)
        if action=='cancel':self.cancel(sid,int(body['stage_sid']));return self.state(sid)
        if action=='tracking':
            if body.get('confirmed') is not True:raise ValueError('请确认此物流单属于当前母单 / 整票')
            no=clean(body.get('tracking_no'),200)
            if not no:raise ValueError('请输入物流单号')
            other=self.store.db.execute('SELECT g.sid FROM batch b JOIN joint_group g ON g.sid=b.session_id WHERE b.tracking_no=? AND g.account=? AND g.sid<>?',(no,self.account(),sid)).fetchone()
            if other:raise ValueError('此物流单已登记在另一个联合批次，不能重复收货')
            b,_=self.store.ensure_batch(sid,no);self.receipt.touch(sid);r=self.state(sid);r['current_batch']=b['id'];return r
        if action=='finish_package':
            self.receipt.batch(sid,bid)
            with self.store.db:self.store.db.execute('INSERT OR REPLACE INTO joint_package VALUES(?,?)',(bid,int(bool(body.get('finished')))))
            self.receipt.touch(sid);return self.state(sid)
        if action in ('edit','delete_line','delete_batch','rename'):
            reserved=self.reserved(sid)
            if action in ('delete_batch','rename'):
                self.receipt.batch(sid,bid)
                if any(reserved.get(r['id'],0)>0 for r in self.store.batch_lines(bid)):raise ValueError('此物流含已分配 / 已提交明细，不能删除或改号')
            else:
                lid=int(body.get('line_id') or 0);old=self.receipt.line(sid,bid,lid);n=reserved.get(lid,Decimal(0))
                if n>0 and (action=='delete_line' or quantity(body.get('qty',0))<n or any(str(body.get(k,''))!=str(old.get(k,'')) for k in ('sku_id','name','spec','sku_code'))):raise ValueError('已占用明细不能改编码或删减到占用数量以下')
        if action not in ('scan','resolve','manual','edit','delete_line','delete_batch','rename','note'):raise ValueError('不支持的操作')
        result=self.receipt.mutate(action,body)
        if result.get('selection_required'):return result
        with self.store.db:self.store.db.execute('UPDATE joint_package SET finished=0 WHERE batch_id=?',(bid,))
        r=self.state(sid);r['current_batch']=bid;return r

    def export(self,sid):
        d=self.state(sid);out=io.StringIO(newline='');w=csv.writer(out)
        def row(v):w.writerow(["'"+x if isinstance(x,str) and x.startswith(('=','+','-','@','\t','\r')) else x for x in v])
        row(['联合批次',d['session']['input_no'],'母单',d['group']['mother']]);row(['编码','商品','本轮合计应收','实收','差异','采购来源'])
        for r in d['rows']:row([r['sku_id'],r['name'],r['plan'] if r['plan'] is not None else '待核实',r['recv'],r['delta'] if r['delta'] is not None else '待核实',' / '.join(p['po_id'] for p in r['sources'])])
        row([]);row(['物流单','编码','实收','已占用','未分配'])
        for b in d['batches']:
            for r in b['lines']:row([b['tracking_no'],r['sku_id'],r['qty'],r['reserved'],r['available']])
        return ('\ufeff'+out.getvalue()).encode('utf-8')

"""Trial-only additive audit and desktop bridge. No media capture in Python."""
import threading,json,datetime,urllib.parse,os,csv,io
CTX=threading.local()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
def migrate(store):
 d=store.db
 for n,f in [('receipt_action',lambda:getattr(CTX,'action','program')),('receipt_raw',lambda:getattr(CTX,'raw','')),('receipt_scan_at',lambda:getattr(CTX,'scan_at','')),('receipt_device',lambda:getattr(CTX,'device','电脑/未知'))]:d.create_function(n,0,f)
 d.create_function('receipt_now',0,now)
 d.executescript('''
 CREATE TABLE IF NOT EXISTS native_event(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS scan_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT,scan_at TEXT,action TEXT,raw_code TEXT,device TEXT,sid INTEGER,bid INTEGER,lid INTEGER,po TEXT,outer_no TEXT,tracking TEXT,sku TEXT,name TEXT,before_qty REAL,after_qty REAL);
 CREATE INDEX IF NOT EXISTS scan_audit_at ON scan_audit(at);
 CREATE INDEX IF NOT EXISTS scan_audit_line ON scan_audit(lid);
 ''')
 for action,ref,before,after in [('INSERT','NEW','0','NEW.qty'),('UPDATE','NEW','OLD.qty','NEW.qty'),('DELETE','OLD','OLD.qty','0')]:
  d.executescript(f'''CREATE TRIGGER IF NOT EXISTS native_audit_{action.lower()} AFTER {action} ON line
  WHEN COALESCE((SELECT id_type FROM session WHERE id={ref}.session_id),'')!='joint-allocation'
  BEGIN INSERT INTO scan_audit(at,scan_at,action,raw_code,device,sid,bid,lid,po,outer_no,tracking,sku,name,before_qty,after_qty)
  SELECT receipt_now(),receipt_scan_at(),CASE WHEN '{action}'='DELETE' THEN 'delete' ELSE receipt_action() END,receipt_raw(),receipt_device(),{ref}.session_id,{ref}.batch_id,{ref}.id,s.po_id,s.outer_po_id,b.tracking_no,{ref}.sku_id,{ref}.name,{before},{after}
  FROM session s JOIN batch b ON b.id={ref}.batch_id WHERE s.id={ref}.session_id; END;''')
 d.commit()
def emit(s,kind,payload):
 s.STORE.db.execute('INSERT INTO native_event(at,kind,payload) VALUES(?,?,?)',(now(),kind,json.dumps(payload,ensure_ascii=False)));s.STORE.db.commit()

def video_store(s,source):
 return {'普通':s.STORE,'无采购单':s.UNPLANNED.store,'联合':s.JOINT.store}[source]
def video_context(s,source,sid,bid):
 """Authoritative local context; no caller-supplied path, purchase or quantity."""
 store=video_store(s,source);d=store.db;sid=int(sid);bid=int(bid)
 row=d.execute('SELECT b.id,b.session_id,b.tracking_no,s.po_id,s.outer_po_id FROM batch b JOIN session s ON s.id=b.session_id WHERE b.id=? AND s.id=?',(bid,sid)).fetchone()
 if not row:raise ValueError('所选物流不存在或不属于该收货批次')
 pairs=[]
 if source=='联合' and d.execute("SELECT 1 FROM sqlite_master WHERE name='joint_purchase'").fetchone():
  for r in d.execute('SELECT po_id,head_json FROM joint_purchase WHERE group_sid=? ORDER BY po_id,id',(sid,)):
   head=json.loads(r['head_json'] or '{}');pairs.append({'po':str(r['po_id'] or ''),'outer':str(head.get('outer_po_id') or '')})
 elif row['po_id']:pairs=[{'po':str(row['po_id']),'outer':str(row['outer_po_id'] or '')}]
 recent=d.execute('SELECT sku,action,before_qty,after_qty FROM scan_audit WHERE sid=? AND bid=? ORDER BY id DESC LIMIT 1',(sid,bid)).fetchone()
 amount=None
 if recent:
  amount=recent['after_qty']-recent['before_qty'] if recent['action'] in ('scan','manual') else recent['after_qty']
 last=recent['sku'] if recent else ''
 if recent and recent['action']=='copy_previous':
  copied=d.execute('SELECT source_tracking,snapshot_json FROM receipt_copy_log WHERE target_bid=? AND sid=?',(bid,sid)).fetchone()
  if copied:
   last='复制上一单：'+copied['source_tracking'];amount=json.loads(copied['snapshot_json'])['total']
 return {'source':source,'sessionId':sid,'batchId':bid,'tracking':row['tracking_no'],'purchases':pairs,'lastSku':last, 'confirmedQuantity':amount}
def video_context_key(s,key):
 parts=str(key).split(':')
 if len(parts)!=3:raise ValueError('录像关联键无效')
 return video_context(s,*parts)
def emit_tracking(s,source,sid,bid,duplicate=False):
 c=video_context(s,source,sid,bid)
 emit(s,'tracking',{'id':c['batchId'],'session_id':c['sessionId'],'tracking_no':c['tracking'],'source':source,'duplicate':bool(duplicate),'context':c})

def export_store(store,q):
 mode=q.get('mode','events');d=store.db
 joint={}
 for p in (d.execute('SELECT group_sid,po_id,head_json FROM joint_purchase') if d.execute("SELECT 1 FROM sqlite_master WHERE name='joint_purchase'").fetchone() else []):
  h=json.loads(p['head_json']);joint.setdefault(p['group_sid'],[]).append((p['po_id'],h.get('outer_po_id','')))
 def matching(r):
  js=joint.get(r['sid'],[])
  return (not q.get('po') or q['po'].lower() in ' / '.join([r.get('po') or '']+[str(p[0]) for p in js]).lower()) and (not q.get('outer') or q['outer'].lower() in ' / '.join([r.get('outer_no') or '']+[str(p[1]) for p in js]).lower()) and all(not q.get(k) or q[k].lower() in str(r.get(col) or '').lower() for k,col in [('tracking','tracking'),('sku','sku')])
 params=[];where=[]
 for k,op in [('start','>='),('end','<=')]:
  if q.get(k):
   dt=datetime.datetime.fromisoformat(q[k]); dt=dt.astimezone(datetime.timezone.utc)
   if k=='end':dt+=datetime.timedelta(seconds=1);op='<'
   where.append('at '+op+' ?');params.append(dt.astimezone(datetime.timezone.utc).isoformat(timespec='milliseconds'))
 def local(v):
  return datetime.datetime.fromisoformat(v).astimezone().strftime('%Y-%m-%d %H:%M:%S') if v else ''
 if mode=='events':
  cols=['流水ID','扫码时间','确认/修改时间','操作','原始条码','商品编码','商品名称','数量变化','操作前','操作后','采购单号(当前关联)','外部单号','物流单号(发生时)','来源','批次ID']
  sql='SELECT a.*, COALESCE(s.po_id,a.po) AS linked_po,COALESCE(s.outer_po_id,a.outer_no) AS linked_outer FROM scan_audit a LEFT JOIN session s ON s.id=a.sid'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY a.id'
  rows=[]
  for v in d.execute(sql,params):
   r=dict(v);r['po']=r['linked_po'];r['outer_no']=r['linked_outer']
   if matching(r):rows.append([r['id'],local(r['scan_at']),local(r['at']),r['action'],r['raw_code'],r['sku'],r['name'],r['after_qty']-r['before_qty'],r['before_qty'],r['after_qty'],' / '.join([str(x[0]) for x in joint[r['sid']]]) if r['sid'] in joint else r['po'], ' / '.join(str(x[1]) for x in joint.get(r['sid'],[])) or r['outer_no'],r['tracking'],r['device'],r['sid']])
 else:
  cols=['采购单号','外部单号','物流单号','商品编码','名称','当前实收','首次商品扫码','最后商品扫码','最后修改','时间完整性']
  rows=[]
  for v in d.execute("SELECT s.id sid,s.po_id po,s.outer_po_id outer_no,b.tracking_no tracking,l.sku_id sku,l.name,l.qty,l.id lid FROM line l JOIN batch b ON b.id=l.batch_id JOIN session s ON s.id=l.session_id WHERE COALESCE(s.id_type,'')!='joint-allocation'"):
   r=dict(v)
   if not matching(r):continue
   ev=[dict(x) for x in d.execute('SELECT * FROM scan_audit WHERE lid=? ORDER BY id',(r['lid'],))]
   # Range selects lines having an operation in that range; qty remains current cumulative qty, not range subtotal.
   if where and not d.execute('SELECT 1 FROM scan_audit WHERE lid=? AND '+' AND '.join(where)+' LIMIT 1',[r['lid']]+params).fetchone():continue
   scans=[e['scan_at'] for e in ev if e['scan_at'] and e['action']=='scan']
   rows.append([' / '.join(str(x[0]) for x in joint.get(r['sid'],[])) or r['po'],' / '.join(str(x[1]) for x in joint.get(r['sid'],[])) or r['outer_no'],r['tracking'],r['sku'],r['name'],r['qty'],local(min(scans)) if scans else '',local(max(scans)) if scans else '',local(ev[-1]['at']) if ev else '', '仅新版流水时间；旧历史未补造'])
 return {'columns':cols,'rows':rows,'count':len(rows),'note':'时间范围按流水确认/修改时间；汇总数量为当前累计，并非范围内增量。旧版6列仍在原导出页。'}
def export_data(s,q):
 out=None
 for label,store in [('普通',s.STORE),('无采购单',s.UNPLANNED.store),('联合',s.JOINT.store)]:
  r=export_store(store,q)
  if out is None:out={'columns':r['columns']+['收货来源'],'rows':[],'note':r['note']}
  for row in r['rows']:out['rows'].append(row+[label])
 out['count']=len(out['rows'])
 return out
def install(s):
 migrate(s.STORE)
 H=s.Handler
 # Close each loopback response so idle keep-alive clients cannot stall safe server shutdown.
 H.protocol_version="HTTP/1.0"
 oldbody=H._body;oldok=H._ok;oldget=H.do_GET;oldpost=H.do_POST
 token=os.environ.get('JST_NATIVE_TOKEN','')
 def auth(h):return bool(token) and h.headers.get('X-Native-Token')==token
 def body(h):
  b=oldbody(h)
  if not hasattr(h,'_native_context') or h._native_context is not b:
   h._native_context=b;p=urllib.parse.urlparse(h.path).path;a=b.get('action','')
   CTX.raw=str(b.get('code') or b.get('sku_code') or '')[:200]
   CTX.action='copy_previous' if a=='copy_previous' else 'scan' if p in ('/api/item/scan','/api/item/resolve','/api/item/choose') or a in ('scan','resolve') else 'manual' if a=='manual' else 'edit'
   CTX.scan_at=now() if CTX.action=='scan' else '';CTX.device='PDA' if p.startswith('/api/pda/') else '电脑/本机'
   raw=b.get('_native_scan_at')
   if raw and CTX.action=='scan':
    try:
     dt=datetime.datetime.fromisoformat(raw.replace('Z','+00:00'))
     if abs((datetime.datetime.now(datetime.timezone.utc)-dt).total_seconds())<86400:CTX.scan_at=dt.astimezone(datetime.timezone.utc).isoformat(timespec='milliseconds')
    except (ValueError,TypeError):pass
  return b
 def ok(h,**kw):
  if h.command=='POST':
   p=urllib.parse.urlparse(h.path).path;b=getattr(h,'_desktop_body',{});a=b.get('action')
   try:
    if p=='/api/tracking/scan':
     batch=kw['batch'];emit_tracking(s,'普通',batch['session_id'],batch['id'],not kw.get('created',True))
    if p in ('/api/unplanned/action','/api/joint/action') and a in ('new','tracking') and kw.get('current_batch'):
     emit_tracking(s,'联合' if p=='/api/joint/action' else '无采购单',kw['session']['id'],kw['current_batch'])
    if p=='/api/unplanned/action' and a in ('match','refresh') and kw.get('session'):
     sid=kw['session']['id']
     contexts=[video_context(s,'无采购单',sid,row['id']) for row in kw.get('batches',[])]
     emit(s,'association',{'contexts':contexts})
    marker=getattr(h,'_video_audit',None)
    item_action=p.startswith('/api/item/') or p in ('/api/line/set','/api/line/del','/api/line/edit') or (p in ('/api/batch/copy','/api/unplanned/action','/api/joint/action') and a in ('copy_previous','scan','manual','edit','delete_line'))
    if marker and item_action and not kw.get('selection_required'):
     source,before=marker;d=video_store(s,source).db
     rows=d.execute('SELECT sid,bid,MAX(id) last_id FROM scan_audit WHERE id>? GROUP BY sid,bid ORDER BY last_id',(before,)).fetchall()
     for row in rows:emit(s,'item',{'context':video_context(s,source,row['sid'],row['bid'])})
    if p in ('/api/batch/del','/api/batch/rename') or (p in ('/api/unplanned/action','/api/joint/action') and a in ('rename','delete_batch')):emit(s,'stop',{'reason':'物流记录修改，请重新确认物流'})
    if p in ('/api/session/pause','/api/session/pause-report','/api/session/close','/api/logout'):emit(s,'stop',{'reason':p})
    if p in ('/api/unplanned/action','/api/joint/action') and a in ('close_session','delete_session'):emit(s,'stop',{'reason':'本地收货批次已结单 / 删除'})
   except Exception as e:
    # A committed business write must not be reported as failed (a retry could double-count).
    kw['native_warning']='收货结果已保留，但录像关联失败，请先停录并核对：'+str(e)
    if hasattr(s,'log'):s.log(kw['native_warning'])
  return oldok(h,**kw)
 def get(h):
  u=urllib.parse.urlparse(h.path)
  if u.path.startswith('/api/native/'):
   if not h._desktop_guard():return
   if not auth(h):return h._fail('native authority required',code=403)
   q={k:v[-1] for k,v in urllib.parse.parse_qs(u.query).items()}
   try:
    with s.state_lock:
     if u.path=='/api/native/events':
      after=int(q.get('after','0'));rs=s.STORE.db.execute('SELECT * FROM native_event WHERE id>? ORDER BY id LIMIT 200',(after,)).fetchall();return oldok(h,events=[dict(r) for r in rs])
     if u.path=='/api/native/context':return oldok(h,context=video_context_key(s,q.get('key','')))
     if u.path=='/api/native/contexts':
      keys=q.get('keys','').split('|')
      if len(keys)>100:raise ValueError('一次最多查询100个录像关联')
      contexts=[];missing=[]
      for key in keys:
       try:contexts.append(video_context_key(s,key))
       except (ValueError,KeyError):missing.append(key)
      return oldok(h,contexts=contexts,missing=missing)
     if u.path=='/api/native/export':return oldok(h,**export_data(s,q))
     if u.path=='/api/native/health':return oldok(h,version='3.0.3',last=s.STORE.db.execute('SELECT COALESCE(MAX(id),0) FROM native_event').fetchone()[0])
   except Exception as e:return h._fail(str(e))
   return h._nf()
  CTX.action='program';CTX.raw='';CTX.scan_at='';CTX.device='电脑/未知'
  return oldget(h)
 def post(h):
  if hasattr(h,'_desktop_body'):del h._desktop_body
  # UI selection is not a privileged export endpoint; retain original local Host/Origin guard.
  if h.path=='/api/native/ui':
   if not h._desktop_guard():return
   b=oldbody(h)
   try:
    with s.state_lock:
     if b.get('kind')=='stop':emit(s,'stop',{'reason':'退出/切换批次'})
     elif b.get('kind')=='tracking':
      source,sid,bid=str(b.get('key','')).split(':');sid=int(sid);bid=int(bid)
      store={'普通':s.STORE,'无采购单':s.UNPLANNED.store,'联合':s.JOINT.store}[source]
      row=store.db.execute('SELECT id,session_id,tracking_no FROM batch WHERE id=? AND session_id=?',(bid,sid)).fetchone()
      if not row:raise ValueError('所选物流不存在')
      emit_tracking(s,source,sid,bid)
     else:raise ValueError('未知事件')
     return oldok(h)
   except (ValueError,KeyError,TypeError) as e:return h._fail(str(e))
  if h.path=='/api/native/shutdown':
   if not h._desktop_guard():return
   if not auth(h):return h._fail('native authority required',code=403)
   h.close_connection=True
   with s.state_lock:oldok(h,msg='stopping')
   threading.Thread(target=h.server.shutdown,daemon=True).start();return
  CTX.action='program';CTX.raw='';CTX.scan_at='';CTX.device='PDA/未标注' if '/pda/' in h.path else '电脑/未知'
  return oldpost(h)
 H._body=body;H._ok=ok;H.do_GET=get;H.do_POST=post

 for route in ('item_scan','item_choose','item_resolve','line_set','line_del','line_edit','batch_copy','unplanned_action','joint_action'):
  name='_post_api_'+route;original=getattr(H,name,None)
  if original is None:continue
  def tracked(h,_original=original,_route=route):
   source='无采购单' if _route=='unplanned_action' else '联合' if _route=='joint_action' else '普通'
   store=video_store(s,source)
   h._video_audit=(source,store.db.execute('SELECT COALESCE(MAX(id),0) FROM scan_audit').fetchone()[0])
   try:return _original(h)
   finally:h._video_audit=None
  setattr(H,name,tracked)

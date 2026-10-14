"""Append-only notes and durable PDA receipt request journal."""
import csv
import hashlib
import io
import json
import time

class Operations:
    def __init__(self, store, client, inventory):
        self.db, self.client, self.inventory = store.db, client, inventory
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS operation_note (
          id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL,
          login_co_id TEXT NOT NULL, login_uid TEXT NOT NULL, actor TEXT NOT NULL,
          text TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS pda_request (
          id TEXT PRIMARY KEY, login_co_id TEXT NOT NULL, login_uid TEXT NOT NULL,
          actor TEXT NOT NULL, path TEXT NOT NULL, digest TEXT NOT NULL,
          status TEXT NOT NULL, response TEXT, created REAL NOT NULL);
        UPDATE pda_request SET status='unknown' WHERE status='sending';
        """)
        self.db.commit()

    def identity(self):
        if not self.client.uid:
            raise ValueError("请先在电脑端登录聚水潭")
        return str(self.client.coid), str(self.client.uid)

    def rows(self):
        co, uid = self.identity()
        out = []
        for j in self.inventory.history():
            s = j["snapshot"]
            out.append({"kind":"inventory", "ref":j["token"], "status":j["status"],
                "title":s["sku_id"], "scope":s["owner"] + " / " + s["auth"] + " / " + s["warehouse"],
                "detail":s["old_qty"] + " → " + s["target_qty"], "error":j["error"],
                "actor":s.get("actor", "旧版电脑端记录"), "created":j["created"]})
        for r in self.db.execute("SELECT * FROM inbound_job WHERE login_co_id=? ORDER BY id DESC LIMIT 100", (co,)):
            s = json.loads(r["snapshot_json"])
            out.append({"kind":"inbound", "ref":str(r["id"]), "sid":r["session_id"], "status":r["status"],
                "title":"采购单 " + str(s.get("po_id", "")) + " / 入库单 " + (r["io_id"] or "待核实"),
                "scope":str(s.get("owner_co_id", "")) + " / " + str(s.get("auth_co_id", "")) + " / " + str((s.get("warehouse") or {}).get("name", "旧版原仓库")),
                "detail": "实收生成待入库单；未自动确认入库", "error":r["error"],
                "actor":"ERP公司 " + co, "created":r["created_at"]})
        for r in self.db.execute("SELECT * FROM pda_request WHERE login_co_id=? AND login_uid=? AND status!='done' ORDER BY created DESC LIMIT 100", (co,uid)):
            out.append({"kind":"receipt", "ref":r["id"], "status":r["status"], "title":"PDA收货请求",
                "scope":r["path"], "detail":"先核对本地实收明细，不要重新扫描同一件", "error":"处理结果待人工核实", "actor":r["actor"], "created":r["created"]})
        for r in out:
            r["notes"] = [dict(n) for n in self.db.execute("SELECT actor,text,created FROM operation_note WHERE kind=? AND ref=? AND login_co_id=? ORDER BY id", (r["kind"],r["ref"],co))]
        return out

    def note(self, kind, ref, text, actor):
        co, uid = self.identity()
        text = str(text).strip()
        if not text or len(text) > 1000:
            raise ValueError("备注需为1到1000字")
        if not any(r["kind"]==kind and r["ref"]==ref for r in self.rows()):
            raise ValueError("记录不存在或不属于当前登录范围")
        with self.db:
            self.db.execute("INSERT INTO operation_note(kind,ref,login_co_id,login_uid,actor,text,created) VALUES(?,?,?,?,?,?,?)",(kind,ref,co,uid,actor,text,time.time()))

    def export(self):
        f=io.StringIO(newline="")
        w=csv.writer(f)
        w.writerow(["类型","记录ID","状态","商品或单号","操作范围","数量/说明","提示","操作端","时间","核实备注"])
        def cell(v):
            v=str(v)
            return "'"+v if v.lstrip().startswith(("=","+","-","@")) or v.startswith(("\t","\r","\n")) else v
        for r in self.rows():
            w.writerow([cell(r.get(k,"")) for k in ("kind","ref","status","title","scope","detail","error","actor","created")] + [cell(" | ".join(n["actor"]+": "+n["text"] for n in r["notes"]))])
        return ("\ufeff"+f.getvalue()).encode("utf-8")

    def reserve(self, request_id, path, body, actor):
        co, uid=self.identity()
        if len(request_id)!=32 or any(c not in "0123456789abcdef" for c in request_id):
            raise ValueError("缺少有效请求编号")
        digest=hashlib.sha256((path+json.dumps(body,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
        old=self.request(request_id, missing_ok=True)
        if old:
            if old["digest"]!=digest:
                raise ValueError("请求编号不可用于不同内容")
            return old
        with self.db:
            self.db.execute("INSERT INTO pda_request VALUES(?,?,?,?,?,?,?,NULL,?)", (request_id,co,uid,actor,path,digest,"sending",time.time()))
        return None

    def request(self, request_id, missing_ok=False):
        co,uid=self.identity()
        r=self.db.execute("SELECT * FROM pda_request WHERE id=?",(request_id,)).fetchone()
        if not r:
            if missing_ok: return None
            return {"status":"not_received", "id":request_id}
        if (r["login_co_id"],r["login_uid"])!=(co,uid):
            raise ValueError("请求不属于当前账号")
        return dict(r)

    def finish(self, request_id, response):
        with self.db:
            self.db.execute("UPDATE pda_request SET status='done',response=? WHERE id=?",(json.dumps(response,ensure_ascii=False),request_id))

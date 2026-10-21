"""Opt-in HTTPS LAN companion. Current PC account is the authority, no ERP cookies leave PC."""
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from store import validate_qty

ASSETS = {"/", "/pda.html", "/pda.js", "/style.css", "/pda.css", "/inventory.js", "/inbound.js", "/operations.js", "/ux.js", "/receipt-editor.js", "/receiving-round.js"}
GETS = {"/api/inventory/partners", "/api/inventory/config", "/api/inventory/lookup", "/api/inventory/history", "/api/inbound/options", "/api/inbound/preview", "/api/session/detail", "/api/operations"}
POSTS = {"/api/line/edit", "/api/inventory/preview", "/api/inventory/submit", "/api/inventory/verify", "/api/inbound/create", "/api/inbound/resume", "/api/tracking/scan", "/api/item/scan", "/api/item/choose", "/api/operations/note"}
RECEIPT = {"/api/tracking/scan", "/api/item/scan", "/api/item/choose"}
PAIR_BODY_MAX = 1024          # unauthenticated pairing body cap
BODY_MAX = 65536
BODY_DEADLINE = 5.0           # whole body must arrive within this many seconds
PAIR_PER_IP_PER_MIN = 10      # pairing attempts per source IP per minute
PAIR_MAX_FAILURES = 5         # wrong codes before the current pairing code is burned

class SecureServer(ThreadingHTTPServer):
    daemon_threads = True
    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(15)
        return sock, addr

class PdaHost:
    def __init__(self, app):
        self.app=app
        self.server=None
        self.sessions={}
        self.pair_code=""
        self.pair_until=0
        self.attempts={}        # source ip -> [timestamps] within the last minute
        self.pair_failures=0
        # Guards sessions / pairing state: guard() and pairing run outside the global
        # business lock, while desktop requests change the same state under it.
        self.lock=threading.RLock()
        self.host=""
        self.port=0
        self.pin=""
        self.identity=""

    def who(self):
        return str(self.app.JST.coid)+":"+str(self.app.JST.uid) if self.app.JST.uid else ""

    def invalidate(self):
        with self.lock:
            self.sessions={}
            self.pair_code=""
            self.pair_until=0
            self.pair_failures=0

    def addresses(self):
        out=set()
        try:
            for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip=ipaddress.ip_address(row[4][0])
                if any(ip in ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
                    out.add(str(ip))
        except OSError:
            pass
        return sorted(out)

    def certificate(self, host):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        directory=Path(self.app.DATA)/"pda-private"
        directory.mkdir(parents=True,exist_ok=True)
        certfile, keyfile=directory/"host.pem", directory/"host-key.pem"
        if not certfile.exists() or not keyfile.exists():
            key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"JST PDA private LAN host")])
            now=datetime.now(timezone.utc)
            cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
                .not_valid_after(now+timedelta(days=825))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]),critical=False)
                .sign(key,hashes.SHA256()))
            keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        cert=x509.load_pem_x509_certificate(certfile.read_bytes())
        if cert.not_valid_after_utc < datetime.now(timezone.utc):
            raise ValueError("PDA证书已到期：停用后备份并移走data/pda-private，重新启用和配对")
        self.pin=cert.fingerprint(hashes.SHA256()).hex()
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile,keyfile)
        return ctx

    def start(self, host, port=8766):
        if self.server:
            raise ValueError("PDA服务已启用；更换网卡请先停用")
        if not self.who():
            raise ValueError("请先在电脑端登录聚水潭再启用PDA")
        if host not in self.addresses() and host!="127.0.0.1":
            raise ValueError("请选择本机局域网IPv4地址")
        port=int(port)
        if not 1024<=port<=65535:
            raise ValueError("端口需为1024到65535")
        ctx=self.certificate(host)
        srv=SecureServer((host,port),make_handler(self,self.app.Handler))
        srv.socket=ctx.wrap_socket(srv.socket,server_side=True,do_handshake_on_connect=False)
        self.server=srv
        self.host,self.port=host,srv.server_address[1]
        self.identity=self.who()
        threading.Thread(target=srv.serve_forever,daemon=True,name="pda-https").start()
        return self.status()

    def stop(self):
        srv=self.server
        self.server=None
        self.invalidate()
        if srv:
            # Don't join handler threads (they may be waiting for the global business lock).
            srv.shutdown()
            srv.server_close()

    def status(self):
        with self.lock:
            if self.identity!=self.who():
                self.invalidate()
                self.identity=self.who()
            now=time.time()
            self.sessions={k:v for k,v in self.sessions.items() if v["expires"]>now}
            sessions=list(self.sessions.values())
        return {"enabled":bool(self.server),"addresses":self.addresses(),"url":("https://%s:%d"%(self.host,self.port)) if self.server else "",
            "fingerprint":self.pin if self.server else "", "devices":[{k:v[k] for k in ("id","name","created","expires")} for v in sessions]}

    def pairing(self):
        self.status()
        if not self.server or not self.who(): raise ValueError("请先登录并启用PDA服务")
        with self.lock:
            self.pair_code=str(secrets.randbelow(90000000)+10000000)
            self.pair_until=time.time()+300
            self.pair_failures=0
        obj={"url":self.status()["url"], "pin":self.pin, "code":self.pair_code}
        import qrcode
        payload = "JSTPDA1:"+base64.urlsafe_b64encode(json.dumps(obj,separators=(",",":")).encode()).decode()
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
        qr.add_data(payload); qr.make(fit=True)
        matrix = qr.get_matrix(); size = len(matrix)
        path = " ".join("M%d,%dh1v1h-1z" % (x,y) for y,row in enumerate(matrix) for x,on in enumerate(row) if on)
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges"><rect width="100%%" height="100%%" fill="white"/><path d="%s" fill="black"/></svg>' % (size,size,path)
        return {"code":self.pair_code,"expires":self.pair_until,"text":payload,
                "qr":"data:image/svg+xml;base64,"+base64.b64encode(svg.encode()).decode()}

    def pair(self, code, name, ip=""):
        self.status()
        with self.lock:
            now=time.time()
            # Per source IP: one noisy LAN device can no longer block everyone else's pairing.
            self.attempts={k:[t for t in v if t>now-60] for k,v in self.attempts.items()}
            self.attempts={k:v for k,v in self.attempts.items() if v}
            mine=self.attempts.setdefault(str(ip),[])
            if len(mine)>=PAIR_PER_IP_PER_MIN: raise ValueError("配对请求过多，请一分钟后再试")
            mine.append(now)
            if not self.who() or not self.pair_code or now>self.pair_until:
                raise ValueError("配对码无效或过期，请在电脑端重新生成")
            if not hmac.compare_digest(str(code),self.pair_code):
                self.pair_failures+=1
                if self.pair_failures>=PAIR_MAX_FAILURES:
                    # Burn the code: guessing can't continue across sources, the desktop regenerates.
                    self.pair_code=""
                    self.pair_until=0
                    self.pair_failures=0
                    raise ValueError("配对码输错次数过多，已作废；请在电脑端重新生成配对码")
                raise ValueError("配对码无效或过期，请在电脑端重新生成")
            if len(self.sessions)>=20: raise ValueError("最多20台配对设备，请先撤销不用的设备")
            name=str(name).strip()[:60]
            if not name: raise ValueError("请输入设备名称")
            self.pair_code=""
            self.pair_failures=0
            token=secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()]={"id":secrets.token_hex(8),"name":name,"created":now,"expires":now+8*3600,"identity":self.who()}
            return token

    def authorize(self, cookie):
        self.status()
        c=SimpleCookie()
        try:
            c.load(cookie or "")
            token=c["jst_pda"].value
        except Exception:
            return None
        with self.lock:
            sess=self.sessions.get(hashlib.sha256(token.encode()).hexdigest())
        return sess if sess and sess["identity"]==self.who() else None

    def revoke(self, device_id):
        with self.lock:
            self.sessions={k:v for k,v in self.sessions.items() if v["id"]!=device_id}

    def receipt_allowed(self, s):
        if not s: return False
        p=self.app.load_partners()
        if p.get("err"): return False
        owners={str(x["id"]) for x in p.get("owners",[])}|{str(self.app.JST.coid)}
        auths={str(x["id"]) for x in p.get("wms",[])}|{str(self.app.JST.coid)}
        owner,auth=str(s.get("owner_co_id") or ""),str(s.get("auth_co_id") or "")
        return owner not in ("","0","2147483647") and auth not in ("","0","2147483647") and owner in owners and auth in auths


def make_handler(host, base):
    app=host.app
    class Handler(base):
        def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
            headers={"X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer", "X-Frame-Options":"DENY",
                "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}
            headers.update(extra or {})
            if getattr(self,"receipt_request",None):
                # Persist the exact business response BEFORE sending bytes. Lost response is recoverable.
                try:
                    obj=json.loads(body) if isinstance(body,(bytes,str)) else body
                    app.OPERATIONS.finish(self.receipt_request,obj)
                finally:
                    self.receipt_request=None
            return super()._send(code,body,ctype,headers)

        def _body(self, limit=BODY_MAX):
            if hasattr(self,"parsed_body"): return self.parsed_body
            if self.headers.get("Transfer-Encoding"): raise ValueError("不支持分块请求")
            try: size=int(self.headers.get("Content-Length") or 0)
            except ValueError: raise ValueError("请求长度无效")
            if size<0 or size>limit: raise ValueError("请求过大")
            if self.headers.get_content_type()!="application/json": raise ValueError("需要JSON请求")
            value=json.loads(self._read_exact(size).decode("utf-8"))
            if not isinstance(value,dict): raise ValueError("请求格式错误")
            self.parsed_body=value
            return value

        def _read_exact(self, size, deadline=None):
            # Total deadline for the whole body, not per recv(): a client trickling one
            # byte at a time cannot keep a handler thread busy for long.
            end=time.monotonic()+(BODY_DEADLINE if deadline is None else deadline)
            chunks,got=[],0
            try:
                while got<size:
                    left=end-time.monotonic()
                    if left<=0: raise ValueError("请求体接收超时")
                    self.connection.settimeout(left)
                    chunk=self.rfile.read1(size-got)
                    if not chunk: raise ValueError("请求不完整")
                    chunks.append(chunk); got+=len(chunk)
            except (socket.timeout, TimeoutError):
                raise ValueError("请求体接收超时")
            except OSError:
                raise ValueError("请求不完整")
            finally:
                try: self.connection.settimeout(15)   # SecureServer's normal per-socket timeout
                except OSError: pass
            return b"".join(chunks)

        def guard(self, pairing=False):
            self.close_connection=True
            if self.headers.get("Host")!="%s:%d"%(host.host,host.port):
                self._fail("主机地址不匹配",code=403); return False
            if self.command=="POST":
                origin=self.headers.get("Origin")
                expected="https://%s:%d"%(host.host,host.port)
                if origin!=expected and not (pairing and not origin and self.headers.get("X-JST-Pair")=="1"):
                    self._fail("禁止跨站请求",code=403); return False
            if not pairing:
                self.device=host.authorize(self.headers.get("Cookie"))
                if not self.device:
                    self._fail("PDA未配对或授权已过期，请返回连接页面重新配对",code=401); return False
            return True

        def _desktop_guard(self):
            # Parent GET dispatch follows our HTTPS Host, session and origin checks.
            return True

        def _actor(self):
            return "PDA:"+self.device["name"]+" ["+self.device["id"]+"] / ERP:"+host.who()

        def check_receipt(self, b):
            sid=int(b.get("sid") or 0)
            if not sid and b.get("job_id"):
                job=app.STORE.get_inbound(int(b["job_id"]))
                sid=job["session_id"] if job else 0
            if not sid or not host.receipt_allowed(app.STORE.get_session(sid)):
                raise ValueError("收货单不在当前账号的具体货主/仓库公司范围，请在电脑端检查")
            path=self.path.split("?")[0]
            if path in ("/api/item/scan","/api/item/choose"):
                bid=int(b.get("batch_id") or 0)
                if not any(x["id"]==bid for x in app.STORE.batches(sid)):
                    raise ValueError("PDA必须选择属于当前收货单的物流子批次")
                qty=b.get("qty")
                # Same integer rule as the desktop endpoints (shared validator); PDA must send a JSON number.
                if isinstance(qty,bool) or not isinstance(qty,(int,float)):
                    raise ValueError("请输入有效数量，不能为负数或超过10亿")
                validate_qty(qty,allow_zero=not path.endswith("choose"))
                if path.endswith("choose"):
                    b["item"]=app.receipt_selection(app.STORE.get_session(sid), b)

        def _op_device(self):
            d=getattr(self,"device",None) or {}
            return {"kind":"pda","id":d.get("id",""),"name":d.get("name","") or "PDA"}

        def do_GET(self):
            u=urllib.parse.urlparse(self.path)
            q=dict(urllib.parse.parse_qsl(u.query))
            with app.state_lock:
                try:
                    if not self.guard(): return
                    if u.path in ASSETS:
                        return self._static("/pda.html" if u.path in ("/","/pda.html") else u.path)
                    if u.path=="/api/pda/info": return self._ok(user=app.JST.uname,company=app.JST.coname,device=self.device["name"],expires=self.device["expires"])
                    if u.path=="/api/pda/receipts":
                        rows=[app.session_payload(s["id"]) for s in app.STORE.recent_sessions(100) if host.receipt_allowed(s)]
                        return self._ok(rows=rows)
                    if u.path=="/api/pda/request":
                        r=app.OPERATIONS.request(q.get("id",""))
                        return self._ok(status=r["status"],response=json.loads(r["response"]) if r.get("response") else None)
                    if u.path not in GETS: return self._fail("PDA不能使用此接口",code=403)
                    if u.path.startswith("/api/inbound/") or u.path=="/api/session/detail": self.check_receipt(q)
                    return super().do_GET()
                except Exception as e:
                    self._fail(str(e))

        def do_POST(self):
            self.receipt_request=None
            self.parsed_body=None
            del self.parsed_body
            path=urllib.parse.urlparse(self.path).path
            pairing=path=="/api/pda/pair"
            # Authenticate and read the whole body BEFORE taking the global business lock:
            # an unauthenticated or slow sender must never hold state_lock.
            try:
                if not self.guard(pairing): return
                b=self._body(PAIR_BODY_MAX if pairing else BODY_MAX)
                if pairing:
                    token=host.pair(b.get("code",""),b.get("name",""),self.client_address[0])
                    return self._send(200,json.dumps({"ok":True}),extra={"Set-Cookie":"jst_pda="+token+"; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=28800"})
            except Exception as e:
                return self._fail(str(e))
            with app.state_lock:
                try:
                    if path not in POSTS: return self._fail("PDA不能使用此接口",code=403)
                    if path in RECEIPT or path == "/api/line/edit" or path.startswith("/api/inbound/"): self.check_receipt(b)
                    if path in RECEIPT and not (path=="/api/item/scan" and b.get("qty")==0):
                        rid=self.headers.get("X-Request-ID","")
                        old=app.OPERATIONS.reserve(rid,path,b,self._actor())
                        if old:
                            if old["status"]=="done": return self._json(json.loads(old["response"]))
                            return self._fail("此请求结果待核实，禁止重复计数；请查看本地实收记录")
                        self.receipt_request=rid
                    fn=getattr(self,"_post_"+path.strip("/").replace("/","_"))
                    return self._run_logged(path,fn)
                except Exception as e:
                    # A handler may have committed before failing. Leave reservation unknown, never replay it.
                    if app.STORE.db.in_transaction:
                        app.STORE.db.rollback()   # never leave a half-written transaction behind
                    if self.receipt_request:
                        with app.STORE.db:
                            app.STORE.db.execute("UPDATE pda_request SET status='unknown' WHERE id=?",(self.receipt_request,))
                        self.receipt_request=None
                    return self._fail(str(e))
                finally:
                    if app.STORE.db.in_transaction:
                        app.STORE.db.rollback()
    return Handler

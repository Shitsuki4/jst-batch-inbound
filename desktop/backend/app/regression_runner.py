# -*- coding: utf-8 -*-
"""一键跑 HTTP 回归：起 server（临时库）→ 灌数据 → 跑 smoke / batch_flow_http / po_items_http。

用法（项目根目录下）：
    python app/regression_runner.py

每个测试用**独立临时库**，互不干扰，也不碰真实 data/jst.db。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PO_ITEMS = [
    {"poi_id": "1", "sku_id": "H2298", "i_id": "H2298", "sku_code": "6936333312036",
     "aux_codes": "", "name": "AKF散粉新款4#", "spec": "", "qty": 240},
    {"poi_id": "2", "sku_id": "H1100", "i_id": "H1100", "sku_code": "6973785387120",
     "aux_codes": "", "name": "AKF散粉（镭射款）", "spec": "", "qty": 100},
    {"poi_id": "3", "sku_id": "H3300", "i_id": "H3300", "sku_code": "6973785387137",
     "aux_codes": "", "name": "AKF散粉3#", "spec": "", "qty": 100},
]

PREPARE = r'''
import os, sys
sys.path.insert(0, r"%s")
from store import Store
st = Store(os.path.join(os.environ["JST_DB_DIR"], "jst.db"))
items = %r
sid = st.open_session("PO-TEST-1", "PO-TEST-1", "po_id", {"supplier": "测试", "wh_name": "测试仓"}, items)
st.activate_session(sid)
b, _ = st.ensure_batch(sid, "JD1000001")   # 只建单号，不预收明细（测试要从 0 开始）
print("prepared sid=%%s bid=%%s" %% (sid, b["id"]))
'''


def wait_port(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/heartbeat" % port, timeout=2):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def free_port(start):
    for p in range(start, start + 50):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    return start


def run_block(name, base_port, prepare, tests):
    d = tempfile.mkdtemp(prefix="jst-reg-")
    port = free_port(base_port)
    env = dict(os.environ)
    env.update({"JST_DB_DIR": d, "JST_PORT": str(port),
                "JST_NO_WATCHDOG": "1", "JST_NO_BROWSER": "1", "PYTHONIOENCODING": "utf-8"})
    lines = ["=" * 12 + " %s (port %d) " % (name, port) + "=" * 12]
    log = open(os.path.join(d, "server.log"), "wb")
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")],
                           cwd=ROOT, env=env, stdout=log, stderr=log)
    ok = True
    try:
        if not wait_port(port):
            lines.append("!! server 未就绪")
            log.flush()
            try:
                with open(os.path.join(d, "server.log"), encoding="utf-8", errors="replace") as f:
                    lines.append(f.read()[-2000:])
            except Exception:
                pass
            return False, "\n".join(lines)
        if prepare:
            p = subprocess.run([sys.executable, "-c", prepare], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            lines.append(p.stdout.decode("utf-8", "replace"))
        for t in tests:
            lines.append("-" * 10 + " " + t + " " + "-" * 10)
            r = subprocess.run([sys.executable, os.path.join(ROOT, t)], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            lines.append(r.stdout.decode("utf-8", "replace"))
            if r.returncode != 0:
                ok = False
                lines.append("!! %s 退出码 %d" % (t, r.returncode))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=8)
        except Exception:
            srv.kill()
        log.close()
        shutil.rmtree(d, ignore_errors=True)
    return ok, "\n".join(lines)


def main():
    results, all_log = [], []
    results.append(("smoke",) + run_block("smoke", 8791, None, ["app/smoke_test.py"]))
    prep = PREPARE % (HERE, PO_ITEMS)
    results.append(("batch-flow-http",) + run_block("batch-flow-http", 8830, prep,
                                                    ["app/batch_flow_http_test.py"]))
    results.append(("po-items-http",) + run_block("po-items-http", 8870, prep,
                                                  ["app/po_items_http_test.py"]))
    # 新功能自身使用独立临时库和 FakeJst，不接触真实聚水潭会话。
    for name, script in [("inventory-unit", "inventory_test.py"),
                         ("inventory-http", "inventory_http_test.py"),
                         ("catalog", "catalog_test.py"),
                         ("scan-ux", "scan_ux_test.py"),
                         ("pda-backup", "pda_test.py"),
                         ("inbound-unit", "inbound_test.py"),
                         ("inbound-http", "inbound_http_test.py"),
                         ("existing-features", "new_features_test.py"),
                         ("batch-flow-unit", "batch_flow_test.py"),
                         ("concurrency-fixes", "concurrency_fixes_test.py"),
                         ("remote-access", "remote_access_test.py"),
                         ("learned-codes", "learned_codes_test.py")]:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", JST_DB_DIR=tempfile.gettempdir())
        r = subprocess.run([sys.executable, os.path.join(HERE, script)], cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        results.append((name, r.returncode == 0, r.stdout.decode("utf-8", "replace")))
    for _, _, text in results:
        all_log.append(text)
    logpath = os.path.join(HERE, "regression_last.log")
    try:
        with open(logpath, "w", encoding="utf-8") as f:
            f.write("\n".join(all_log))
    except Exception:
        logpath = "(写日志失败)"
    print("\n======== SUMMARY (full log: %s) ========" % logpath)
    for n, ok, text in results:
        print("  %-18s %s" % (n, "PASS" if ok else "FAIL"))
        if not ok:
            for ln in text.splitlines():
                if "[FAIL]" in ln or ln.startswith("!!") or "Traceback" in ln or "Error" in ln:
                    print("      " + ln)
    return 1 if any(not ok for _, ok, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())

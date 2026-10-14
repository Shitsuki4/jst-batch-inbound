# -*- coding: utf-8 -*-
"""本次新功能的回归测试（不联网）。

覆盖：
  1. match_item 合并分级识别：A 的条码 = B 的辅助码（SFJ32/OL146 互挂）必须 multi，
     且每个候选带 match_via；同商品多级命中不算冲突；
  2. store：常用账号（存/列/删，不含密码）、物流单号改名（重名拒绝）、sku_cache pic 列；
  3. HTTP：设置接受 default_id_type=auto；扫码互挂码返回 multi+match_via；
     物流单删除/改名端点；/api/item/pic 与 /api/accounts。

必须先设置 JST_DB_DIR 指向临时目录（server 需要起在同一个目录上）。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jst import match_item  # noqa: E402
from store import Store  # noqa: E402

PASS = FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, extra))


def call(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as r:
        return json.loads(r.read().decode())


def test_match_item():
    print("== 1. match_item：条码/辅助码互挂必须弹多候选 ==")
    items = [
        {"poi_id": "1", "sku_id": "SFJ32", "i_id": "SFJ3", "sku_code": "6900000000032",
         "aux_codes": "OL146", "name": "商品A", "spec": "", "qty": 10},
        {"poi_id": "2", "sku_id": "OL146", "i_id": "OL14", "sku_code": "OL146X",
         "aux_codes": "6900000000032", "name": "商品B", "spec": "", "qty": 5},
        {"poi_id": "3", "sku_id": "P777", "i_id": "P77", "sku_code": "6900000000777",
         "aux_codes": "", "name": "商品C", "spec": "", "qty": 8},
    ]
    # 扫 A 的条码：该条码同时是 B 的辅助码 → 两个商品都要给出
    r = match_item(items, "6900000000032")
    check(r["mode"] == "multi" and len(r["items"]) == 2,
          "A 的条码同时是 B 的辅助码 → multi 2 个候选", str(r)[:160])
    via = {i["sku_id"]: i.get("match_via", "") for i in r.get("items", [])}
    check(via.get("SFJ32") == "条码", "商品A 标注「条码命中」", str(via))
    check(via.get("OL146") == "辅助码", "商品B 标注「辅助码命中」", str(via))

    # 扫 B 的商品编码 OL146：它同时是 A 的辅助码 → multi
    r = match_item(items, "OL146")
    check(r["mode"] == "multi" and len(r["items"]) == 2,
          "扫 OL146（B 的编码 = A 的辅助码）→ multi 2 个候选", str(r)[:160])

    # 无冲突：普通条码直接唯一命中
    r = match_item(items, "6900000000777")
    check(r["mode"] == "unique" and r["item"]["sku_id"] == "P777" and r["via"] == "barcode",
          "无冲突条码 → 唯一命中", str(r)[:120])

    # 同一商品多级命中（自己的条码同时也是自己的辅助码）→ 不算冲突
    items2 = [{"poi_id": "1", "sku_id": "S1", "i_id": "S", "sku_code": "X1",
               "aux_codes": "X1", "name": "自挂", "qty": 3}]
    r = match_item(items2, "X1")
    check(r["mode"] == "unique", "同商品条码=辅助码 → 仍唯一命中", str(r)[:120])

    # 款式编码多规格 → multi（既有行为不变）
    items3 = [
        {"poi_id": "1", "sku_id": "S1", "i_id": "STYLE", "sku_code": "C1", "aux_codes": "", "name": "红", "qty": 1},
        {"poi_id": "2", "sku_id": "S2", "i_id": "STYLE", "sku_code": "C2", "aux_codes": "", "name": "蓝", "qty": 1},
    ]
    r = match_item(items3, "STYLE")
    check(r["mode"] == "multi" and len(r["items"]) == 2, "同款式多规格 → multi", str(r)[:120])

    # 三码互挂 + 第三者商品编码命中：全部进候选
    items4 = [
        {"poi_id": "1", "sku_id": "A1", "i_id": "", "sku_code": "K", "aux_codes": "", "name": "a", "qty": 1},
        {"poi_id": "2", "sku_id": "B1", "i_id": "", "sku_code": "K", "aux_codes": "K", "name": "b", "qty": 1},
        {"poi_id": "3", "sku_id": "K", "i_id": "", "sku_code": "Z9", "aux_codes": "", "name": "c", "qty": 1},
    ]
    r = match_item(items4, "K")
    check(r["mode"] == "multi" and len(r["items"]) == 3,
          "条码+辅助码+商品编码三方冲突 → 3 个候选", str(r)[:160])


def test_store(d):
    print("== 2. store：常用账号 / 物流单改名 / 缓存 pic ==")
    st = Store(os.path.join(d, "t.db"))
    st.save_account("zhangsan", "pw")
    st.save_account("lisi", "sms")
    st.save_account("zhangsan", "pw")          # 再存一次只刷新时间，不重复
    accs = st.list_accounts()
    check(len(accs) == 2 and {a["name"] for a in accs} == {"zhangsan", "lisi"},
          "账号记住 2 个（重复保存不新增）", str(accs))
    check(all("password" not in a and "pwd" not in a for a in accs), "账号记录不含密码字段")
    st.del_account("lisi")
    check([a["name"] for a in st.list_accounts()] == ["zhangsan"], "删除后只剩 1 个")

    items = [{"sku_id": "S1", "sku_code": "B1", "name": "商品一", "qty": 10}]
    sid = st.open_session("PO-9", "PO-9", "po_id", {"supplier": "x"}, items)
    b, _ = st.ensure_batch(sid, "SF888")
    st.add_qty(b["id"], sid, {"key": "S1", "sku_id": "S1", "sku_code": "B1",
                              "name": "商品一", "matched": True}, 3)
    st.rename_batch(b["id"], "SF999")
    check(st.batches(sid)[0]["tracking_no"] == "SF999", "物流单号已改为 SF999")
    check(st.batch_lines(b["id"])[0]["qty"] == 3, "改名后明细保留（仍 3 件）")
    b2, _ = st.ensure_batch(sid, "SF888")
    try:
        st.rename_batch(b2["id"], "SF999")
        check(False, "改成重名单号应被拒绝")
    except ValueError:
        check(True, "改成重名单号被拒绝")
    try:
        st.rename_batch(b["id"], "  ")
        check(False, "空单号应被拒绝")
    except ValueError:
        check(True, "空单号被拒绝")

    st.sku_cache_put("PIC-1", {"sku_id": "S1", "name": "带图商品", "sku_code": "B1",
                               "pic": "https://img.example.com/a.jpg"}, "12034457")
    hit = st.sku_cache_get("PIC-1")
    check(hit.get("pic") == "https://img.example.com/a.jpg", "缓存记录带 pic 列")
    st.db.close()


def test_http(d):
    print("== 3. HTTP：自动类型 / 多候选 / 物流单管理端点 ==")
    os.environ["JST_DB_DIR"] = d
    os.environ["JST_PORT"] = "8799"
    os.environ["JST_NO_WATCHDOG"] = "1"
    import server
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 8799), server.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:8799"
    try:
        r = call(base, "/api/settings", {"default_id_type": "auto"})
        check(r["ok"] and server.STORE.get_kv("default_id_type") == "auto",
              "设置接受 default_id_type=auto")
        r = call(base, "/api/settings", {"default_id_type": "bogus"})
        check(r["ok"] and server.STORE.get_kv("default_id_type") == "auto",
              "非法单号类型被忽略")

        # 灌一张「互挂码」采购单：A 的条码/辅助码分别等于 B 的辅助码/商品编码，反之亦然
        items = [
            {"poi_id": "1", "sku_id": "SFJ32", "i_id": "SFJ3", "sku_code": "6930000000032",
             "aux_codes": "OL146", "name": "商品A", "qty": 10},
            {"poi_id": "2", "sku_id": "OL146", "i_id": "OL14", "sku_code": "SFJ32",
             "aux_codes": "6930000000032", "name": "商品B", "qty": 5},
        ]
        sid = server.STORE.open_session("PO-CF", "PO-CF", "po_id", {"supplier": "冲突测试"}, items)
        server.STORE.activate_session(sid)
        b, _ = server.STORE.ensure_batch(sid, "SF0001")
        bid = b["id"]

        r = call(base, "/api/item/scan", {"code": "SFJ32", "batch_id": bid, "sid": sid, "qty": 0})
        check(r.get("ok") and r.get("multi") and len(r.get("candidates") or []) == 2,
              "扫 SFJ32（=商品A编码 + 商品B条码）→ 返回 2 候选", str(r)[:200])
        via = {c["sku_id"]: c.get("match_via") for c in r.get("candidates") or []}
        check(via.get("SFJ32") == "商品编码" and via.get("OL146") == "条码",
              "候选带 match_via 标注", str(via))
        r = call(base, "/api/item/scan", {"code": "6930000000032", "batch_id": bid, "sid": sid, "qty": 0})
        check(r.get("multi") and len(r.get("candidates") or []) == 2,
              "扫商品A条码（= 商品B辅助码）→ multi", str(r)[:200])

        r = call(base, "/api/item/choose",
                 {"batch_id": bid, "sid": sid, "qty": 2,
                  "item": {"poi_id": "1", "sku_id": "SFJ32", "sku_code": "6930000000032",
                           "name": "商品A"}})
        check(r.get("ok") and float(r.get("qty", 0)) == 2, "人工选定后入账 2 件", str(r)[:160])

        r = call(base, "/api/batch/rename", {"batch_id": bid, "tracking_no": "SF0002"})
        check(r.get("ok") and r["batches"][0]["tracking_no"] == "SF0002", "HTTP 改物流单号成功")
        r = call(base, "/api/batch/rename", {"batch_id": bid, "tracking_no": ""})
        check(not r.get("ok"), "空单号被拒绝")
        r = call(base, "/api/batch/del", {"batch_id": bid})
        check(r.get("ok") and "物流单" in r.get("msg", ""), "删除物流单端点", str(r)[:120])
        check(server.STORE.batches(sid) == [], "删除后该采购单下无物流单")

        r = call(base, "/api/item/pic?code=")
        check(r.get("ok") and r.get("pic") == "", "空 code 返回空图")
        r = call(base, "/api/accounts")
        check(r.get("ok") and isinstance(r.get("rows"), list), "/api/accounts 可用")
        r = call(base, "/api/account/del", {"name": "nobody"})
        check(r.get("ok"), "/api/account/del 可用")
    finally:
        srv.shutdown()
        srv.server_close()
        try:
            server.STORE.db.close()
        except Exception:
            pass


def main():
    if not os.environ.get("JST_DB_DIR") and not os.environ.get("NEW_FEAT_ALLOW"):
        # 本测试自己建临时目录；数据层部分直接用文件路径，不依赖该变量
        pass
    d = tempfile.mkdtemp(prefix="jst-newfeat-")
    try:
        test_match_item()
        test_store(d)
        test_http(os.path.join(d, "http"))
        print("\n合计：%d 通过 / %d 失败" % (PASS, FAIL))
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

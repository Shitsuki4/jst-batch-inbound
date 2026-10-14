# -*- coding: utf-8 -*-
"""左栏「采购单明细」+ 右栏「物流单号明细」+ 扫码待确认数量 —— HTTP 端到端测试。

需先起 server（用测试库）并灌一条带物流单号与采购单明细的记录：
  set JST_DB_DIR=...\\tmp-i && set JST_PORT=8797 && python app/server.py
  set JST_DB_DIR=...\\tmp-i && set JST_PORT=8797 && python app/po_items_http_test.py

覆盖：
  · /api/state 返回 items（采购单明细，含应收/已收/done），顺序即采购单原顺序；
  · 扫码「只识别」dry 模式（qty=0）不入账；
  · 显式 qty = 本次增加量（累加语义），不传 qty 向后兼容按设置累加；
  · 明细排序：采购单里的按采购单顺序在前，采购单没有的沉底；
  · /api/session/detail 也带 items。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:%s" % os.environ.get("JST_PORT", "8797")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PASS = FAIL = 0

# 采购单里三个商品的原始顺序（与灌数据脚本一致）
PO_SEQ = ["H2298", "H1100", "H3300"]


def call(path, body=None):
    path = urllib.parse.quote(path, safe="/?&=")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}) if data else \
        urllib.request.Request(BASE + path)
    with OPENER.open(req, timeout=30) as r:
        return json.loads(r.read().decode())


def num(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, extra))


def main():
    if not os.environ.get("JST_DB_DIR"):
        print("!! 请先设置 JST_DB_DIR（测试库）再运行")
        return 2

    st = call("/api/state")
    if not (st.get("session") and st.get("items")):
        print("!! 库里没有带采购单明细的收货单，请先灌一条测试数据")
        return 2
    sid = st["session"]["id"]
    bid = st["current_batch"]
    codes = {it["sku_id"]: it["sku_code"] for it in st["items"]}

    print("== 1. /api/state 的采购单明细 items ==")
    its = st["items"]
    check(len(its) == 3, "items 共 3 条", "got=%d" % len(its))
    check([i["sku_id"] for i in its] == PO_SEQ, "items 保持采购单原顺序",
          str([i["sku_id"] for i in its]))
    check(all("plan" in i and "recv" in i and "done" in i for i in its),
          "每条含 plan/recv/done")
    check(all(num(i["recv"]) == 0 for i in its), "初始已收都是 0")

    print("== 2. 扫码先「只识别」不入账（qty=0 dry run） ==")
    r = call("/api/item/scan", {"code": codes["H2298"], "batch_id": bid, "sid": sid, "qty": 0})
    check(r.get("ok") and r.get("dry"), "返回 dry 标记", str(r)[:100])
    check(r.get("item", {}).get("sku_id") == "H2298", "识别出 H2298")
    check(not r.get("lines"), "dry 不返回 lines（没入账）")
    check(all(num(i["recv"]) == 0 for i in call("/api/state")["items"]),
          "dry 后已收仍为 0")

    print("== 3. 回车确认默认 1 → 累加 1 ==")
    r = call("/api/item/scan", {"code": codes["H2298"], "batch_id": bid, "sid": sid, "qty": 1})
    check(r.get("ok") and not r.get("dry"), "正常入账")
    check(num(r.get("added")) == 1 and num(r.get("qty")) == 1, "added=1 / qty=1", str(r.get("qty")))
    check("items" in r, "入账响应带 items（左栏联动）")
    hit = [i for i in r["items"] if i["sku_id"] == "H2298"][0]
    check(num(hit["recv"]) == 1, "左栏 H2298 已收 1")

    print("== 4. 改数字再回车 → 累加该数字（非覆盖） ==")
    call("/api/item/scan", {"code": codes["H2298"], "batch_id": bid, "sid": sid, "qty": 1})
    r = call("/api/item/scan", {"code": codes["H2298"], "batch_id": bid, "sid": sid, "qty": 10})
    check(num(r.get("qty")) == 12 and num(r.get("added")) == 10, "1+1+10=12", str(r.get("qty")))
    hit = [i for i in r["items"] if i["sku_id"] == "H2298"][0]
    check(num(hit["recv"]) == 12, "左栏同步 12")

    print("== 5. 不传 qty 向后兼容（按设置累加） ==")
    r = call("/api/item/scan", {"code": codes["H1100"], "batch_id": bid, "sid": sid})
    check(r.get("ok") and num(r.get("added")) == 1, "旧调用方式 added=1", str(r.get("added")))

    print("== 6. 排序：采购单里的按顺序，采购单没有的沉底 ==")
    r = call("/api/item/scan", {"code": "UNKNOWN-XYZ", "batch_id": bid, "sid": sid, "qty": 1})
    check(r.get("ok") and r.get("unmatched"), "计划外商品记入差异")
    call("/api/item/scan", {"code": codes["H3300"], "batch_id": bid, "sid": sid, "qty": 1})
    lines = call("/api/state?batch_id=%d" % bid)["lines"]
    seq = [(l["sku_id"], l["matched"]) for l in lines]
    print("   明细顺序:", seq)
    matched = [l["sku_id"] for l in lines if l["matched"]]
    check(matched == [s for s in PO_SEQ if s in matched], "采购单里的按采购单顺序", str(matched))
    check(lines[-1]["matched"] == 0, "采购单没有的排在最后", str(seq))
    check(all(l["matched"] == 1 for l in lines[:-1]), "前面全是采购单里有的")

    print("== 7. 左栏 done / over 标记 ==")
    r = call("/api/item/scan", {"code": codes["H1100"], "batch_id": bid, "sid": sid, "qty": 99})
    hit = [i for i in r["items"] if i["sku_id"] == "H1100"][0]
    check(num(hit["recv"]) == 100 and hit["done"], "H1100 收满 → done=true", str(hit))
    check(not hit["over"], "收满不算多收")

    print("== 8. 会话明细接口也带 items ==")
    r = call("/api/session/detail?sid=%d" % sid)
    check("items" in r and len(r["items"]) == 3, "detail 带 items", str(len(r.get("items") or [])))

    print("== 9. 左栏 items 带编码（sku_code）供「编码 + 名称」显示 ==")
    items = call("/api/state")["items"]
    check(all(i.get("sku_code") for i in items), "每条 items 都有 sku_code",
          str([i.get("sku_code") for i in items]))

    print("== 10. 换物流单号后，上一单号收满的商品沉底显示（不消失） ==")
    # H1100 此刻已在本单号收满 100；开一个新物流单号，它应作为 done_elsewhere 出现
    r = call("/api/tracking/scan", {"tracking_no": "JD-NEXT-2", "sid": sid})
    check(r.get("ok") and r.get("batch_id"), "新建第二个物流单号", str(r.get("batch_id")))
    bid2 = r["batch_id"]
    call("/api/item/scan", {"code": codes["H2298"], "batch_id": bid2, "sid": sid, "qty": 2})
    st2 = call("/api/state?batch_id=%d" % bid2)
    de = [l for l in st2["lines"] if l.get("done_elsewhere")]
    check(len(de) == 1 and de[0]["sku_id"] == "H1100",
          "第二个单号里 H1100 以 done_elsewhere 沉底出现", str([(l["sku_id"], l.get("done_elsewhere")) for l in st2["lines"]]))
    check(st2["lines"][-1].get("done_elsewhere"), "沉底行确实在最后", str(st2["lines"][-1].get("sku_id")))
    check(de[0].get("from_batch") == "JD1000001", "沉底行标注是在哪个单号收满的", str(de[0].get("from_batch")))
    check(all(l.get("id") for l in st2["lines"] if not l.get("done_elsewhere")),
          "正常明细行仍带真实 line id（可改可删）")

    print("== 11. 记录查询带采购单维度实收汇总 po_summary ==")
    r = call("/api/query/po?no=PO-TEST-1")
    rec = (r.get("records") or [{}])[0]
    ps = rec.get("po_summary") or []
    # 采购单 3 个商品 + 1 个计划外（UNKNOWN-XYZ）= 4 行，一致的也列出
    check(len(ps) == 4, "po_summary = 采购单商品 + 计划外（一致也列出）", str(len(ps)))
    by_sku = {d["sku_id"]: d for d in ps}
    check(num(by_sku["H1100"]["plan"]) == 100 and num(by_sku["H1100"]["recv"]) == 100
          and by_sku["H1100"]["done"], "H1100 应收=实收=100 且 done", str(by_sku["H1100"]))
    # H2298：sec.3 扫1 + sec.4 扫1、扫10 + sec.10 扫2 = 14
    check(num(by_sku["H2298"]["recv"]) == 14, "H2298 跨单号合计实收 14", str(by_sku["H2298"]))
    check(any(d.get("extra") for d in ps), "计划外商品也在 po_summary 里带 extra 标记",
          str([(d["sku_id"], d.get("extra")) for d in ps]))
    d = call("/api/session/detail?sid=%d" % sid)
    check(len(d.get("po_summary") or []) == 4, "session/detail 也带 po_summary",
          str(len(d.get("po_summary") or [])))

    print("\n合计：%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

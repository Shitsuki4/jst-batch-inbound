# -*- coding: utf-8 -*-
"""本地流程冒烟测试：不经聚水潭，直接向库里灌一条真实采购单快照，跑完整收货链路。"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:%s" % os.environ.get("JST_PORT", "8791")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))
from store import Store  # noqa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 测试库与正式库分开：JST_DB_DIR 指向临时目录时不写用户真实数据
DB_DIR = os.environ.get("JST_DB_DIR") or os.path.join(ROOT, "data")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(path, body=None):
    # 测试用例里有中文关键字，需要自己转义成百分号编码
    path = urllib.parse.quote(path, safe="/?&=")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}) if data else \
          urllib.request.Request(BASE + path)
    with OPENER.open(req, timeout=30) as r:
        return json.loads(r.read().decode())

def show(tag, r):
    extra = ""
    for k in ("lines", "diff", "records", "batches", "preview"):
        if isinstance(r, dict) and k in r:
            extra = " %s=%d" % (k, len(r[k]) if r[k] is not None else 0)
            break
    print("  %-28s ok=%s%s %s" % (tag, r.get("ok"), extra, r.get("msg") or r.get("hint") or ""))

ITEMS = [
    {"poi_id": "12306680", "sku_id": "H2298", "i_id": "H2298", "sku_code": "6936333312036",
     "aux_codes": "6971870595085,6901843910379", "name": "AKF散粉新款4#", "spec": "AKF散粉新款4#",
     "qty": 240, "order_qty": 240, "in_qty": 0},
    {"poi_id": "12306679", "sku_id": "H1100", "i_id": "H1100", "sku_code": "6973785387120",
     "aux_codes": "6979408630949", "name": "AKF散粉（镭射款）", "spec": "AKF散粉(镭射款)",
     "qty": 240, "order_qty": 240, "in_qty": 0},
]

def main():
    # 护栏：smoke_test 会在库里灌固定采购单快照（1323803）并测试删除逻辑，
    # 不设 JST_DB_DIR 就会写到正式数据 data/jst.db，可能污染真实收货记录。
    if not os.environ.get("JST_DB_DIR"):
        print("!! 拒绝运行：请先设置 JST_DB_DIR 指向一个临时目录（与正式数据隔离），例如：")
        print("!!   Windows:  set JST_DB_DIR=%CD%\\.tmp-smoke && python app/smoke_test.py")
        print("!!   Bash:     JST_DB_DIR=\"$PWD/.tmp-smoke\" python app/smoke_test.py")
        return 2
    print("== 准备数据 ==")
    if not os.path.isdir(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
    print("  数据库路径：", os.path.join(DB_DIR, "jst.db"))
    st = Store(os.path.join(DB_DIR, "jst.db"))
    # 先清掉本用例固定单号的历史残留（上次运行留下的 closed 记录会污染"删除后查不到"断言）
    stale = st.db.execute(
        "SELECT id FROM session WHERE po_id='1323803' OR outer_po_id='377656'").fetchall()
    for row in stale:
        st.del_session(row["id"])
    if stale:
        print("  已清理 %d 条历史残留记录" % len(stale))
    old = st.active_session()
    if old:
        st.close_session(old["id"])
    sid = st.open_session("1323803", "1323803", "po_id",
                          {"outer_po_id": "377656", "supplier": "刷货", "wh_name": "东莞品秀刷货仓",
                           "status": "已确认", "po_date": "2026-09-29 14:17:33",
                           "owner_co_id": "12034457", "auth_co_id": "15195086"}, ITEMS)
    print("  session id =", sid)

    # 商品资料反查的本地缓存：相当于接口已经告诉过我们这些码对应什么商品。
    # 冒烟测试不联网，用它来验证「认码 → 回查采购单明细」这条链路。
    st.sku_cache_put("K-8888", {"sku_id": "H1100", "i_id": "H1100", "name": "AKF散粉（镭射款）",
                                "spec": "AKF散粉(镭射款)", "sku_code": "6973785387120"}, "12034457")
    st.sku_cache_put("Z-9999", {"sku_id": "Q5555", "i_id": "Q5555", "name": "陌生物料A",
                                "spec": "", "sku_code": "6900000000001"}, "12034457")
    st.sku_cache_put("NOPE-1", None, "")        # 负缓存：接口确认查无此商品

    print("== 收货链路 ==")
    s = call("/api/state")
    assert s["session"] and s["session"]["state"] == "active", s
    print("  state ok, po =", s["session"]["po_id"])

    for tk in ("SF1000000001", "SF1000000002"):
        r = call("/api/tracking/scan", {"tracking_no": tk})
        show("tracking/scan " + tk, r)
        assert r["ok"] and r["created"]
    bid1 = r["batch"]["id"]

    r = call("/api/tracking/scan", {"tracking_no": "SF1000000001"})
    assert r["ok"] and not r["created"], "重复扫同一物流单不应新建批次"
    show("tracking/scan 重复", r)

    b1 = [b for b in call("/api/state")["batches"] if b["tracking_no"] == "SF1000000001"][0]
    b2 = [b for b in call("/api/state")["batches"] if b["tracking_no"] == "SF1000000002"][0]

    r = call("/api/item/scan", {"batch_id": b1["id"], "code": "6936333312036"})
    assert r["ok"] and not r.get("multi") and not r.get("unmatched"), r
    show("item/scan 69码", r)
    for _ in range(4):
        call("/api/item/scan", {"batch_id": b1["id"], "code": "6936333312036"})
    r = call("/api/item/scan", {"batch_id": b1["id"], "code": "6901843910379"})
    assert r["ok"] and r.get("via") == "aux", r
    show("item/scan 辅助码兜底", r)

    r = call("/api/item/scan", {"batch_id": b1["id"], "code": "H2298"})
    assert r["ok"] and r.get("via") == "sku_id", r
    show("item/scan 商品编码", r)

    r = call("/api/item/scan", {"batch_id": b1["id"], "code": "K-8888"})
    assert r["ok"] and r.get("via") == "catalog", r
    show("item/scan 商品资料反查→命中采购单", r)

    r = call("/api/item/scan", {"batch_id": b1["id"], "code": "Z-9999"})
    assert r["ok"] and r.get("unmatched") and r.get("resolved"), r
    assert r["item"]["name"] == "陌生物料A", r
    show("item/scan 反查成功但采购单无此商品", r)

    r = call("/api/item/scan", {"batch_id": b1["id"], "code": "NOPE-1"})
    assert r["ok"] and r.get("unmatched") and not r.get("resolved"), r
    show("item/scan 确认无此商品（负缓存）", r)

    call("/api/settings", {"sku_lookup": "0"})
    r = call("/api/item/scan", {"batch_id": b1["id"], "code": "K-8888"})
    assert r["ok"] and r.get("unmatched") and not r.get("resolved"), r
    show("关闭商品资料反查后降级", r)
    call("/api/settings", {"sku_lookup": "1"})

    r = call("/api/partners")
    assert r["ok"], r
    show("partners 清单", r)

    r = call("/api/item/scan", {"batch_id": b1["id"], "code": "9999999999999"})
    assert r["ok"] and r.get("unmatched"), r
    show("item/scan 未知码", r)

    call("/api/item/scan", {"batch_id": b2["id"], "code": "6973785387120"})
    call("/api/item/scan", {"batch_id": b2["id"], "code": "6973785387120"})

    st_state = call("/api/state?batch_id=%d" % b1["id"])
    print("  批次1明细：", [(l["name"], l["sku_id"], l["qty"], l["matched"]) for l in st_state["lines"]])
    print("  总进度：", call("/api/state")["progress"])

    lid = st_state["lines"][0]["id"]
    r = call("/api/line/set", {"line_id": lid, "qty": 12, "batch_id": b1["id"]})
    show("line/set 手动改量", r)
    assert r["ok"]

    print("== 差异校验 ==")
    r = call("/api/diff")
    show("diff", r)
    for d in r["rows"]:
        print("     %-8s 应收%-6s 实收%-6s  %s" % (d["sku_id"], d["plan"], d["recv"], d["reason"]))

    print("== 查询 ==")
    r = call("/api/query/po?no=1323803")
    show("query/po", r)
    r = call("/api/query/po?no=377656")
    show("query/po 外部单号", r)
    r = call("/api/query/tracking?no=SF1000000001")
    show("query/tracking", r)

    print("== 模糊查询 ==")
    r = call("/api/query/po?no=13238")
    assert r["ok"] and r["found"], r
    show("query/po 单号片段", r)
    r = call("/api/query/po?no=刷货")
    assert r["ok"] and r["found"], r
    show("query/po 按供应商", r)
    r = call("/api/query/tracking?no=SF100")
    assert r["ok"] and r["found"] and len(r["batches"]) == 2, r
    show("query/tracking 片段", r)
    r = call("/api/query/po?no=不存在的单号")
    assert r["ok"] and not r["found"], r
    show("query/po 无结果", r)

    print("== 导出 ==")
    for mode, val in (("po", "1323803"), ("tracking", "SF100000000"), ("range", "")):
        for fmt in ("csv", "xlsx"):
            q = "/api/export?mode=%s&value=%s&format=%s" % (mode, val, fmt)
            if mode == "range":
                q += "&start=2026-01-01&end=2030-12-31"
            r = call(q)
            show("export %s/%s" % (mode, fmt), r)
            assert r["ok"], r
            preview = r["preview"]
            assert preview["cols"] == ["采购单号", "对应物流单号", "扫描时间", "商品编码", "商品名称", "数量"], preview["cols"]
            print("      行数=%d 首行=%s" % (r["rows"], preview["rows"][0]))

    print("== 结束采购单 ==")
    r = call("/api/session/close", {})
    show("session/close", r)
    assert r["ok"]
    r = call("/api/tracking/scan", {"tracking_no": "SF1000000003"})
    assert not r["ok"], "结束后不应还能收货"
    show("结束后收货拦截", r)

    print("== 记录修改 / 删除 ==")
    r = call("/api/batch/lines?batch_id=%d" % b2["id"])
    assert r["ok"] and r["lines"], r
    show("batch/lines 已结束单据", r)
    lid2 = r["lines"][0]["id"]

    r = call("/api/line/set", {"line_id": lid2, "qty": 5})
    assert r["ok"], r
    show("结束后仍可改数量", r)
    r = call("/api/batch/lines?batch_id=%d" % b2["id"])
    assert float(r["lines"][0]["qty"]) == 5, r
    show("改后回读", r)

    r = call("/api/line/del", {"line_id": lid2})
    assert r["ok"], r
    show("line/del", r)

    r = call("/api/batch/del", {"batch_id": b2["id"]})
    assert r["ok"], r
    show("batch/del 删除子批次", r)
    r = call("/api/query/tracking?no=SF1000000002")
    assert r["ok"] and not r["found"], r
    show("删除批次后查不到", r)

    r = call("/api/cache/clear", {})
    assert r["ok"], r
    show("cache/clear", r)

    r = call("/api/session/del", {"sid": sid})
    assert r["ok"], r
    show("session/del 删除整单", r)
    r = call("/api/query/po?no=1323803")
    assert r["ok"] and not r["found"], r
    show("整单删除后查不到", r)

    print("\n全部用例通过")
    return 0

if __name__ == "__main__":
    sys.exit(main())

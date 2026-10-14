# -*- coding: utf-8 -*-
"""收货批次流转 —— HTTP 端到端测试（需先起 server）。

重点验证需求：
  · 「退出批次」后能创建其他批次，不再被卡住；
  · 挂起的批次能一点恢复、接着扫；
  · 记录查询页点进去即返回两段式数据（分物流单号明细 + 汇总差异）。

用法：
  set JST_DB_DIR=...\\tmp-f  && set JST_PORT=8796 && python app/server.py   (另开窗口)
  set JST_DB_DIR=...\\tmp-f  && set JST_PORT=8796 && python app/batch_flow_http_test.py
"""
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:%s" % os.environ.get("JST_PORT", "8796")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PASS = FAIL = 0


def call(path, body=None):
    path = urllib.parse.quote(path, safe="/?&=")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}) if data else \
        urllib.request.Request(BASE + path)
    with OPENER.open(req, timeout=30) as r:
        return json.loads(r.read().decode())


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


def main():
    if not os.environ.get("JST_DB_DIR"):
        print("!! 请先设置 JST_DB_DIR（测试库）再运行")
        return 2

    print("== 从查询接口拿到一条本地记录（不联网） ==")
    # 需要一个已存在的 session 才能测退出/恢复；这里要求先跑过 smoke_test 灌数据，
    # 或直接依赖 server 库中已有记录。
    rec = call("/api/recents")
    if not rec.get("rows"):
        print("!! 库里没有收货单，请先运行 smoke_test.py 灌一条测试数据")
        return 2
    sid = rec["rows"][0]["id"]
    print("  使用 session id =", sid)

    print("== 恢复挂起批次（若已结束则跳过恢复断言） ==")
    st = call("/api/state?sid=%d" % sid)
    print("  当前 session state =", (st.get("session") or {}).get("state"))

    print("== 退出批次 ==")
    r = call("/api/session/pause", {"sid": sid})
    print("  pause ->", r.get("ok"), r.get("msg"))
    st = call("/api/state")
    check(st.get("session") is None, "退出后当前无 active 批次（人已空闲）")
    ps = st.get("paused") or []
    check(any(p["id"] == sid for p in ps), "该批次出现在「挂起中的批次」列表里",
          str([p["id"] for p in ps]))

    print("== 恢复批次 ==")
    r = call("/api/session/resume", {"sid": sid})
    check(r.get("ok"), "resume 成功", str(r))
    st = call("/api/state")
    check((st.get("session") or {}).get("id") == sid, "恢复后它重新成为当前批次")

    print("== 两段式查询 ==")
    d = call("/api/session/detail?sid=%d" % sid)
    check(d.get("ok"), "session/detail 可用")
    check(isinstance(d.get("groups"), list) and d["groups"],
          "第一段：按物流单号分组明细", "groups=%d" % len(d.get("groups") or []))
    check(all("lines" in g and "tracking_no" in g for g in d.get("groups") or []),
          "每个分组带 tracking_no 与自己独立的明细行")
    check("diff" in d and "progress" in d,
          "第二段：汇总差异 + 进度", str(d.get("progress")))
    q = call("/api/query/po?no=%s" % (d["session"]["po_id"]))
    check(q.get("ok") and q.get("found"), "query/po 也能返回两段式")
    if q.get("found"):
        check("groups" in q["records"][0], "query/po 记录含 groups 字段")

    print("== 结单后不可再扫 ==")
    r = call("/api/session/close", {"sid": sid})
    check(r.get("ok"), "结单成功")
    r = call("/api/tracking/scan", {"tracking_no": "SF-NEW-TEST", "sid": sid})
    check(not r.get("ok"), "结单后扫码被拒", str(r.get("msg")))
    r = call("/api/session/resume", {"sid": sid})
    check(not r.get("ok"), "结单后不可恢复", str(r.get("msg")))

    print("\n合计：%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

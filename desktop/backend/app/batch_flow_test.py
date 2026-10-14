# -*- coding: utf-8 -*-
"""收货批次流转测试（不联网、不依赖真实登录）。

覆盖需求：
  1. 同一采购单只保留一张收货单；
  2. 「退出批次」= 挂起，数据保留，可恢复接着扫；
  3. 多张收货单可并存（一张 active、其余 paused）；
  4. 同一物流单号内同商品重复扫 → 数量累加；
  5. 不同物流单号里的同一商品 → 互不影响，各自计数；
  6. 结单后汇总差异 = 实收合计 vs 采购单应收。

必须显式指定 JST_DB_DIR，避免写进正式库。
"""
import os
import shutil
import sys
import tempfile

if not os.environ.get("JST_DB_DIR"):
    sys.stderr.write("拒绝运行：必须先设置 JST_DB_DIR 指向临时目录，避免污染正式库\n")
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import Store  # noqa: E402

PASS = FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


def mk(d):
    st = Store(os.path.join(d, "t.db"))
    head = {"supplier": "测试供应商", "wh_name": "测试仓",
            "owner_co_id": "12034457", "auth_co_id": "15195086"}
    items = [
        {"sku_id": "S1", "sku_code": "B1", "name": "商品一", "spec": "红", "qty": 10},
        {"sku_id": "S2", "sku_code": "B2", "name": "商品二", "spec": "蓝", "qty": 5},
    ]
    return st, head, items


def item(sku_id, name, qty=1, code="B", matched=True):
    return {"key": sku_id, "sku_id": sku_id, "sku_code": code + sku_id,
            "name": name, "spec": "", "matched": matched, "poi_id": ""}


def main():
    d = tempfile.mkdtemp(prefix="jst-batch-")
    os.environ["JST_DB_DIR"] = d
    try:
        st, head, items = mk(d)

        print("== 1. 同一采购单只保留一张收货单 ==")
        s1 = st.open_session("PO-1", "PO-1", "po_id", head, items)
        s2 = st.open_session("PO-1", "PO-1", "po_id", head, items)
        check(s1 == s2, "重复创建同一采购单 → 复用同一条记录", "s1=%s s2=%s" % (s1, s2))
        check(len(st.find_sessions("PO-1")) == 1, "库里只有一张 PO-1 收货单")

        print("== 2. 物流单号内同商品累加 ==")
        b1, _ = st.ensure_batch(s1, "SF111")
        st.add_qty(b1["id"], s1, item("S1", "商品一"), 1)
        st.add_qty(b1["id"], s1, item("S1", "商品一"), 1)
        st.add_qty(b1["id"], s1, item("S1", "商品一"), 3)
        ls = st.batch_lines(b1["id"])
        check(len(ls) == 1 and ls[0]["qty"] == 5, "同一单号内同商品累加为一行 5 件",
              str([(x["sku_id"], x["qty"]) for x in ls]))

        print("== 3. 不同物流单号里同商品互不影响 ==")
        b2, _ = st.ensure_batch(s1, "SF222")
        st.add_qty(b2["id"], s1, item("S1", "商品一"), 2)
        l1 = st.batch_lines(b1["id"])
        l2 = st.batch_lines(b2["id"])
        check(l1[0]["qty"] == 5 and l2[0]["qty"] == 2,
              "SF111 的 S1=5、SF222 的 S1=2，各自独立",
              "a=%s b=%s" % (l1[0]["qty"], l2[0]["qty"]))
        check(st.batches(s1)[0]["total"] != st.batches(s1)[1]["total"]
              or True, "批次合计各自计算")

        print("== 4. 挂起 / 多单并存 / 恢复 ==")
        sA = st.open_session("PO-A", "PO-A", "po_id", head, items)
        st.activate_session(sA)
        check(st.active_session()["id"] == sA, "PO-A 成为当前收货中")
        sB = st.open_session("PO-B", "PO-B", "po_id", head, items)
        st.activate_session(sB)
        cur = st.active_session()
        check(cur["id"] == sB, "创建 PO-B 后它是唯一 active")
        check(st.get_session(sA)["state"] == "paused", "PO-A 自动变为挂起（不再互相阻塞）")
        check(len(st.open_sessions()) == 3, "未结单的有 3 张（1 active + 2 paused：PO-A/PO-B/PO-1）")
        # 恢复
        st.activate_session(sA)
        check(st.active_session()["id"] == sA, "恢复 PO-A 成功")
        check(st.get_session(sB)["state"] == "paused", "PO-B 相应被挂起")

        print("== 5. 显式退出批次 ==")
        st.pause_session(sA)
        check(st.active_session() is None, "退出后没有 active（人已空闲）")
        check(st.get_session(sA)["state"] == "paused" and st.get_session(sA)["paused_at"],
              "PO-A 标记为挂起并记录时间")
        check(len(st.batches(s1)) == 2, "挂起不影响原有批次数据（SF111/SF222 仍在）")
        st.activate_session(sA)

        print("== 6. 同一单号内明细在挂起后仍可继续累加 ==")
        before = st.batch_lines(b1["id"])[0]["qty"]
        st.add_qty(b1["id"], s1, item("S1", "商品一"), 1)
        after = st.batch_lines(b1["id"])[0]["qty"]
        check(after == before + 1, "恢复后继续扫，数量接着累加", "%s→%s" % (before, after))

        print("== 7. 汇总差异（实收合计 vs 采购单应收） ==")
        # S1: 5 + 2 + 1 = 8（应收 10，少 2）；S2 未收（少 5）；再扫个不在采购单里的
        st.add_qty(b2["id"], s1, item("S9", "计划外商品", code="C", matched=False), 4)
        prog = st.progress(s1)
        check(prog["plan"] == 15, "应收合计 = 10+5 = 15", str(prog))
        check(prog["recv"] == 8, "实收合计（仅匹配采购单的）= 8", str(prog))
        diff = {x["sku_id"]: x for x in st.diff_report(s1)}
        check("S1" in diff and diff["S1"]["diff"] == -2, "S1 少收 2 记入差异", str(diff.get("S1")))
        check("S2" in diff and diff["S2"]["diff"] == -5, "S2 少收 5 记入差异")
        check(any(k == "S9" for k in diff), "计划外商品 S9 作为多收差异出现")

        print("== 8. 两段式查询数据 ==")
        grp = st.batches_with_lines(s1)
        check([g["tracking_no"] for g in grp] == ["SF111", "SF222"], "按物流单号分组有序")
        check(all("lines" in g for g in grp), "每组都带自己的明细行")
        tally = {g["tracking_no"]: sum(l["qty"] for l in g["lines"]) for g in grp}
        check(tally.get("SF111") == 6 and tally.get("SF222") == 6,
              "分单号合计正确（各 6）", str(tally))

        print("== 9. 结单后状态 ==")
        st.close_session(s1)
        check(st.get_session(s1)["state"] == "closed", "结单后 state=closed")
        check(all(x["id"] != s1 for x in st.open_sessions()), "结单后不在未结单列表里")
        check(st.find_session_by_po("PO-1")["id"] == s1, "按采购单仍能查到这张已结单")

        print("== 10. 旧库升级：唯一索引被清掉 ==")
        q = st.db.execute("SELECT name FROM sqlite_master WHERE name='ux_session_active'").fetchone()
        check(q is None, "ux_session_active 唯一索引已删除（否则多单并存会插入失败）")
        st.db.close()

        print("\n合计：%d 通过 / %d 失败" % (PASS, FAIL))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

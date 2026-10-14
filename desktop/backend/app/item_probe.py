# -*- coding: utf-8 -*-
"""
商品编码反查自查脚本 —— 验证「扫码值 → 商品资料接口」这条链路在你账号上能不能通。

用法（命令提示符，建议 chcp 936）：
    python app/item_probe.py              # 交互输入账号 / 密码 / 要查的码
    python app/item_probe.py 账号 密码 条码或商品编码

会依次做：
  1. 登录（同界面一致的合成风控参数）
  2. 取货主 / 分仓清单（就是界面上那两个下拉框的数据源）
  3. 对指定的码跑三级反查：详情接口 → 列表按商品编码 → 列表按条码
  4. 打印每一步是否命中、耗时、返回的商品档案

账号密码只在内存里使用，不写文件不打日志。
"""
import getpass
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from jst import JstClient, LoginError, BizError, ItemLookupError  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SEP = "-" * 66


def synth_vd144():
    import random
    return "%d_%s" % (int(time.time() * 1000),
                      "".join("%x" % random.randint(0, 15) for _ in range(32)))


def show_item(tag, t, it, err=None):
    if err:
        print("    %-22s %6.2fs  异常：%s" % (tag, t, str(err)[:120]))
        return
    if not it:
        print("    %-22s %6.2fs  未命中" % (tag, t))
        return
    print("    %-22s %6.2fs  命中 → 编码=%s 名称=%s 条码=%s 款式=%s 状态=%s"
          % (tag, t, it.get("sku_id"), it.get("name"), it.get("sku_code"),
             it.get("i_id"), it.get("enabled")))


def step(tag, fn):
    t = time.time()
    try:
        return fn(), None, time.time() - t
    except Exception as e:
        return None, e, time.time() - t


def main():
    print(SEP)
    print(" 商品编码反查自查")
    print(SEP)
    acc = sys.argv[1] if len(sys.argv) > 1 else input("聚水潭账号：").strip()
    pwd = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("密码（不回显）：")
    code = sys.argv[3] if len(sys.argv) > 3 else input("要试的条码 / 商品编码：").strip()
    if not (acc and pwd and code):
        print("账号、密码、待查编码都不能为空")
        return 1

    cli = JstClient(timeout=20)
    print("\n【1】登录")
    try:
        info = cli.login(acc, pwd, "", synth_vd144())
        print("    成功：%s（%s / coid=%s）" % (info.get("uname"), info.get("uid"), info.get("coid")))
    except LoginError as e:
        print("    失败：%s" % e)
        print("    → 界面里能登录的话，用界面登录后再回来跑；本脚本拿不到浏览器指纹，")
        print("      遇到风控类告警属正常现象。")
        return 1

    print("\n【2】货主 / 分仓清单")
    owners, err, _ = step("owners", cli.partners)
    if err:
        print("    取货主清单失败：%s" % err)
    else:
        print("    货主 %d 个：" % len(owners or []))
        for o in (owners or [])[:20]:
            print("        %-12s %s" % (o["id"], o["name"]))
    wms, err, _ = step("wms", cli.wms_list)
    if err:
        print("    取分仓清单失败：%s" % err)
    else:
        print("    分仓 %d 个：" % len(wms or []))
        for w in (wms or [])[:20]:
            print("        %-12s %s" % (w["id"], w["name"]))
    owner = (owners or [{}])[0].get("id", "") if owners else ""

    print("\n【3】对「%s」做三级反查" % code)
    it, err, t = step("详情 GetEditOrCreate", lambda: cli.item_detail(code, owner, owner))
    show_item("① 详情（商品编码）", t, it, err)
    it2, err2, t2 = step("列表 sku_id", lambda: cli.item_search("sku_id", code, owner))
    show_item("② 列表（商品编码）", t2, (it2 or [None])[0], err2)
    it3, err3, t3 = step("列表 sku_code", lambda: cli.item_search("sku_code", code, owner))
    show_item("③ 列表（条码）", t3, (it3 or [None])[0], err3)

    print("\n【4】组合调用 lookup_item（界面走的同一个入口）")
    t = time.time()
    try:
        got = cli.lookup_item(code, owner, owner)
        dt = time.time() - t
        if got:
            print("    命中，耗时 %.2fs：编码=%s 名称=%s 来源=%s"
                  % (dt, got["sku_id"], got["name"], got.get("src")))
        else:
            print("    未查询到该编码对应的商品（%.2fs）—— 扫码时会记为「采购单无此商品」" % dt)
    except ItemLookupError as e:
        print("    接口不可用：%s" % e)
        print("    → 通常是该账号没有商品资料模块的权限，扫码会自动降级，不影响收货")

    print()
    print(SEP)
    print("核查要点：")
    print("  · 三级里只要有一级命中，界面扫码就能认出这个码；")
    print("  · 全部未命中 → 确认这个码在聚水潭「商品资料」里是否真的存在、是否属于所选货主；")
    print("  · 权限类报错 → 让管理员开通「商品资料」的查看权限。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")

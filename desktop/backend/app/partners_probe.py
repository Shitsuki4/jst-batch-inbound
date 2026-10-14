# -*- coding: utf-8 -*-
"""
货主 / 分仓清单接口探测 —— 当界面下拉里全是数字时，用它看清接口到底返回了什么。

用法（在本机「命令提示符」里，不要双击）：
    python app/partners_probe.py                 # 交互输入账号密码
    python app/partners_probe.py 13800138000     # 只带账号

会打印 GetTPWPartnerOwnersV2 与 GetWmsCoIds 的**原始响应体**（截断显示），
以及当前解析器认出来的 id/name 对照表。密码只在内存里用，不落盘。
"""
import getpass
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jst import JstClient, LoginError, API_ROOTS  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def show_raw(tag, obj, limit=1600):
    print("\n---- %s 原始结构 ----" % tag)
    try:
        txt = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        txt = repr(obj)
    if len(txt) > limit:
        txt = txt[:limit] + "\n  …（已截断，共 %d 字符）" % len(txt)
    print(txt)


def main():
    print("=" * 62)
    print(" 聚水潭扫码入库 —— 货主 / 分仓清单探测")
    print("=" * 62)

    account = sys.argv[1] if len(sys.argv) > 1 else input("\n请输入聚水潭账号：").strip()
    password = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("请输入密码（输入不回显）：")
    if not account or not password:
        print("账号和密码都不能为空")
        return 1

    c = JstClient()
    print("\n登录中…")
    try:
        info = c.login(account, password)
    except LoginError as e:
        print("登录失败：%s" % e)
        return 1
    print("登录成功：%s（coid=%s）" % (info.get("uname") or account, c.coid))

    # ---- 1. 分仓清单 ----
    print("\n" + "=" * 62)
    print("【1】GetWmsCoIds（分仓清单）")
    print("=" * 62)
    url = API_ROOTS["api"] + "ItemApi/PartnerRelation/GetWmsCoIds"
    body = {"ip": "", "uid": c.uid, "coid": c.coid,
            "data": "AddAllWms", "isOtherStore": False}
    try:
        raw = c._request(url, json.dumps(body, ensure_ascii=False),
                         referer="https://src.erp321.com/", is_json=True)
        j = json.loads(raw)
        print("code=%s  msg=%s" % (j.get("code"), (j.get("msg") or "").strip()))
        show_raw("data", j.get("data"))
        print("\n---- 当前解析器结果 ----")
        for x in c.wms_list():
            flag = "  ← 名称=id，异常！" if str(x["name"]) == str(x["id"]) else ""
            print("  %-14s %s%s" % (x["id"], x["name"], flag))
    except urllib.error.HTTPError as e:
        print("HTTP %s：%s" % (e.code, e.read()[:400].decode("utf-8", "replace")))
    except Exception as e:
        print("请求异常：%s: %s" % (type(e).__name__, e))

    # ---- 2. 货主清单 ----
    print("\n" + "=" * 62)
    print("【2】GetTPWPartnerOwnersV2（货主清单）")
    print("=" * 62)
    url2 = API_ROOTS["api"] + "ItemApi/PartnerRelation/GetTPWPartnerOwnersV2"
    body2 = {"ip": "", "uid": c.uid, "coid": c.coid,
             "data": {"isExcludeSelf": False, "isExcludeOwner": False},
             "isOtherStore": False}
    try:
        raw2 = c._request(url2, json.dumps(body2, ensure_ascii=False),
                          referer="https://src.erp321.com/", is_json=True)
        j2 = json.loads(raw2)
        print("code=%s  msg=%s" % (j2.get("code"), (j2.get("msg") or "").strip()))
        show_raw("data", j2.get("data"))
        print("\n---- 当前解析器结果 ----")
        for x in c.partners():
            flag = "  ← 名称=id，异常！" if str(x["name"]) == str(x["id"]) else ""
            print("  %-14s %s%s" % (x["id"], x["name"], flag))
    except urllib.error.HTTPError as e:
        print("HTTP %s：%s" % (e.code, e.read()[:400].decode("utf-8", "replace")))
    except Exception as e:
        print("请求异常：%s: %s" % (type(e).__name__, e))

    print("\n" + "=" * 62)
    print("把上面两段「原始结构」发我，就能确认字段名对不对。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")

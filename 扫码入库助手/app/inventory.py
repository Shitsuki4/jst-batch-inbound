# -*- coding: utf-8 -*-
"""库存盘点：仅操作明确选中的 PackItemId。所有测试使用假客户端，不写线上库存。
增减先换算绝对数量；本地持久化先于远端写入。未知结果只能回读，不重发。
远端 API 无 CAS，提交前复核不能替代多人同时盘点时的业务锁。
"""
import html
import json
import re
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from inbound import text, json_text
from jst import CallbackRejected


class InventoryError(ValueError):
    pass


def number(value):
    s = text(value)
    if len(s) > 40 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d{1,4})?|\.\d{1,4})", s):
        raise InventoryError("数量请输入普通数字，最多4位小数，不支持科学计数法")
    try:
        q = Decimal(s)
        if not q.is_finite():
            raise ValueError()
        return q
    except (InvalidOperation, ValueError):
        raise InventoryError("数量必须是有限数字")


def scope(client, owner, auth):
    if not client.uid or not client.coid:
        raise InventoryError("请先登录聚水潭")
    owner, auth = text(owner), text(auth)
    if not owner.isdigit() or not auth.isdigit() or owner in ("0", "2147483647") or auth in ("0", "2147483647"):
        raise InventoryError("必须选择具体货主和仓库公司，不能选择所有商家／所有仓库")
    return {"owner": owner, "auth": auth}


class InventoryAPI:
    ROOT = "https://www.erp321.com/app/wms/Pack/PackItems.aspx?"

    def __init__(self, client, owner, auth):
        self.client = client
        s = scope(client, owner, auth)
        self.context = {"owner_co_id": s["owner"], "authorize_co_id": s["auth"]}
        self.url = self.ROOT + urlencode(self.context)
        self.fields = dict.fromkeys(("picking_count no_sales_bin_ids repeat_bin_ids combine_sku_id "
            "bin_group_id bin_group pack_id qty io_id supplier_id supplier_name labels_search "
            "labels_exclude_search pack_type _jt_page_count_enabled _cbb_supplier_name").split(), "")
        self.fields.update(self.context, picking_time="7", exclude_time="1")

    def config(self):
        source = self.client._request(self.url, referer=self.url)
        # Delegated WMS rows retain the login company's co_id. Validate selected page context too.
        for variable, expected in (("_OwnerCoid", self.context["owner_co_id"]),
                                   ("___RequestAuthorizeCoid", self.context["authorize_co_id"])):
            hit = re.search(r"var\s+" + variable + r"\s*=\s*(\d+)\s*;", source)
            if (hit and hit.group(1) != expected) or (not hit and self.context["authorize_co_id"] != text(self.client.coid)):
                raise InventoryError("盘点页面货主／授权仓库上下文不一致，已停止")
        # 来自盘点页面自己的仓库选择器，不把渠道仓当作库存仓。
        warehouses = []
        for wid, label in re.findall(r'<label\b[^>]*for=["\']pack_type_wh_(\d+)["\'][^>]*>(.*?)</label>', source, re.S | re.I):
            if int(wid) > 0:
                warehouses.append({"id": wid, "name": html.unescape(re.sub(r"<[^>]+>", "", label)).strip()})
        flag = re.search(r"var\s+_IsQtyUseDecimal\s*=\s*(true|false)", source)
        if not warehouses or not flag or len({w["id"] for w in warehouses}) != len(warehouses):
            raise InventoryError("未能读取盘点仓库或数量规则，请检查登录、权限或页面版本")
        return {"warehouses": warehouses, "decimal": flag.group(1) == "true",
                "locations": [{"id":"all", "name":"全部位置（含暂存位）"}, {"id":"bin", "name":"正式仓位"}] +
                ([{"id":"general", "name":"通用暂存位"}] if 'pack_type_genaral_default_ack' in source else [])}

    @staticmethod
    def editable(row):
        return (text(row.get("bin")) not in ("补货暂存位", "盘亏位", "bin.1.add")
                and text(row.get("wh_id")) not in ("装箱出库", "-1") and text(row.get("whId")) not in ("", "-1", "0"))

    def rows(self, code, wh_id, code_type="sku", location="all"):
        if location not in ("all", "bin", "general"):
            raise InventoryError("请选择有效的盘点位置")
        if self.context["authorize_co_id"] != text(self.client.coid): self.config()
        keys = {"sku": "[pit].sku_id", "barcode": "sku_code"}
        if code_type not in keys:
            raise InventoryError("请选择商品编码或仓库条码")
        code, wh_id = text(code), text(wh_id)
        if not code or len(code) > 200 or any(c in code for c in (",", "*", "\n", "\r")):
            raise InventoryError("请输入单个完整编码，不支持批量或模糊查询")
        if not wh_id.isdigit() or int(wh_id) <= 0:
            raise InventoryError("请选择具体库存仓库")
        filters = [{"k": keys[code_type], "v": code, "c": "@=" if code_type == "sku" else "="},
                   {"k": "[p].wh_id", "v": wh_id, "c": "="}]
        pack_type = "wh_" + wh_id
        default_pack = ""
        if location == "general":
            if not any(v["id"] == "general" for v in self.config()["locations"]):
                raise InventoryError("当前仓库不支持通用暂存位查询")
            rv = self.client.callback(self.url, "GetDefaultPackId", [], fields=self.fields, call_control="{page}")
            # Numeric strings must never pass through a float: Pack IDs exceed JS safe integers.
            if not isinstance(rv, (str, int)) or isinstance(rv, bool) or not re.fullmatch(r"[1-9]\d{0,24}", str(rv)):
                raise InventoryError("读取通用暂存位ID失败，已停止")
            default_pack = str(rv)
            filters.append({"k":"[p].pack_id", "v":default_pack, "c":"="})
            pack_type = "genaral_default_ack"
        elif location == "bin":
            filters.append({"k":"pack_type", "v":"bin", "c":"="})
            pack_type = "bin"
        out, seen = [], set()
        for page in range(1, 101):
            res = self.client.callback(self.url, "LoadDataToJSON", [str(page), json_text(filters), "{}"],
                fields=dict(self.fields, pack_type=pack_type), page_size=50)
            if not isinstance(res, dict) or not isinstance(res.get("datas"), list):
                raise InventoryError("库存列表响应异常，已停止")
            for row in res["datas"]:
                # fail closed：不相信仅靠接口筛选，不容忍跨货主/仓库的结果。
                if (text(row.get("owner_co_id")) != self.context["owner_co_id"] or
                    text(row.get("co_id")) != text(self.client.coid) or text(row.get("whId")) != wh_id):
                    raise InventoryError("库存返回货主／仓库上下文不一致，已停止")
                if default_pack and text(row.get("sys_pack_id")) != default_pack:
                    raise InventoryError("返回了非通用暂存位库存，已停止")
                if location == "bin" and not text(row.get("bin")):
                    raise InventoryError("返回了非正式仓位库存，已停止")
                field = "sku_id" if code_type == "sku" else "sku_code"
                if text(row.get(field)) != code:
                    raise InventoryError("库存查询返回了非精确编码，已停止，不自动选取")
                rid = text(row.get("pack_item_id"))
                if not rid.isdigit() or rid in seen or not text(row.get("sku_id")):
                    raise InventoryError("库存明细ID无效或分页重复，不能盘点")
                seen.add(rid)
                number(row.get("qty"))
                out.append(row)
            dp = res.get("dp") or {}
            if dp.get("IsLast") is True:
                return out
            if dp.get("IsLast") is not False:
                raise InventoryError("库存分页信息缺失，不能保证明细完整")
        raise InventoryError("库存明细过多，请到聚水潭缩小范围处理")

    def write(self, row_id, target):
        q = number(target)
        value = int(q) if q == q.to_integral_value() else float(q)
        # ERP _ACP receives JSON.stringify(rows): Args[0] must be a JSON string, not an array.
        rv = self.client.callback(self.url, "ModifyStockByPackItems",
            [json_text([{"PackItemId": text(row_id), "RQty": value}])], fields=self.fields, page_size=50, call_control="{page}")
        if rv is not True:
            raise InventoryError("库存修改未返回明确成功，必须回读核实，不得重复提交")


class InventoryService:
    def __init__(self, store, client):
        self.store, self.client = store, client
        self.db = store.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS inventory_op (
            token TEXT PRIMARY KEY, identity TEXT NOT NULL, login_co_id TEXT NOT NULL,
            login_uid TEXT NOT NULL, status TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, error TEXT NOT NULL DEFAULT '')""")
        self.db.execute("CREATE INDEX IF NOT EXISTS inventory_op_identity ON inventory_op(identity,status)")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS inventory_one_active ON inventory_op(identity) WHERE status IN ('sending','unknown')")
        self.db.commit()

    def api(self, owner, auth):
        return InventoryAPI(self.client, owner, auth)

    def config(self, owner, auth):
        return self.api(owner, auth).config()

    @staticmethod
    def public_row(row):
        keys = ("pack_item_id", "sku_id", "sku_code", "name", "properties_value", "qty", "bin", "pack_id",
                "sys_pack_id", "pack_type", "wh_id", "whId", "owner_co_id", "co_id", "pb_id", "produced_date", "expiration_date", "freeze_qty")
        r = {k: row.get(k) for k in keys}
        r["qty"] = str(number(row["qty"]))
        r["editable"] = InventoryAPI.editable(row)
        return r

    def lookup(self, owner, auth, wh_id, code, code_type="sku", location="all"):
        api = self.api(owner, auth)
        return [self.public_row(r) for r in api.rows(code, wh_id, code_type, location)]

    @staticmethod
    def identity(row):
        return {k: text(row.get(k)) for k in ("owner_co_id", "co_id", "whId", "sku_id", "pack_item_id", "sys_pack_id", "bin", "pb_id")}

    def find(self, api, snap):
        rows = api.rows(snap["sku_id"], snap["wh_id"], location=snap.get("location", "all"))
        row = next((r for r in rows if text(r.get("pack_item_id")) == snap["pack_item_id"]), None)
        if not row or self.identity(row) != snap["row_identity"] or not api.editable(row):
            raise InventoryError("库存明细身份／仓位已变化或不可修改，请重新查询")
        return row

    def preview(self, b):
        api = self.api(b.get("owner"), b.get("auth"))
        config = api.config()
        wh_id = text(b.get("wh_id"))
        wh = next((w for w in config["warehouses"] if w["id"] == wh_id), None)
        if not wh:
            raise InventoryError("请选择该仓库公司下有效的库存仓")
        rows = api.rows(b.get("code"), wh_id, b.get("code_type", "sku"), b.get("location", "all"))
        row = next((r for r in rows if text(r.get("pack_item_id")) == text(b.get("pack_item_id"))), None)
        if not row or not api.editable(row):
            raise InventoryError("请明确选择一条可修改的库存明细，不自动分摊仓位")
        old, value = number(row["qty"]), number(b.get("value"))
        if old != number(b.get("expected_qty")):
            raise InventoryError("库存数量已变化，请重新查询后预览")
        mode = b.get("mode")
        if mode not in ("set", "delta"):
            raise InventoryError("请选择直接改数量或增减数量")
        target = value if mode == "set" else old + value
        limit = Decimal("9999999999.9999" if config["decimal"] else "2147483647")
        if target < 0 or target > limit or abs(value) > limit:
            raise InventoryError("盘点后数量不能为负数或超过系统上限")
        if not config["decimal"] and (target != target.to_integral_value() or value != value.to_integral_value()):
            raise InventoryError("当前仓库未开启小数数量，请输入整数")
        if target == old:
            raise InventoryError("数量未变化，无需提交盘点")
        # 锁定到库存主键而非 bin 等可变字段，迁位不能绕开未决请求锁。
        ident = json_text([api.context["owner_co_id"], api.context["authorize_co_id"], text(row["pack_item_id"])])
        if self.db.execute("SELECT 1 FROM inventory_op WHERE identity=? AND status IN ('sending','unknown')", (ident,)).fetchone():
            raise InventoryError("该库存明细存在结果待核实的操作，请在操作记录回读核对，禁止重复盘点")
        snap = {"owner": api.context["owner_co_id"], "auth": api.context["authorize_co_id"], "wh_id": wh_id,
            "location": b.get("location", "all"), "position": text(row.get("bin") or row.get("pack_id") or row.get("pack_type")),
            "warehouse": wh["name"], "sku_id": text(row["sku_id"]), "name": text(row.get("name")),
            "pack_item_id": text(row["pack_item_id"]), "row_identity": self.identity(row), "bin": text(row.get("bin")),
            "actor": str(b.get("actor") or "电脑端"), "old_qty": str(old), "target_qty": str(target), "mode": mode, "value": str(value)}
        token, now = uuid.uuid4().hex, time.time()
        self.db.execute("INSERT INTO inventory_op VALUES (?,?,?,?,?,?,?,?,?)", (token, ident,
            text(self.client.coid), text(self.client.uid), "preview", json_text(snap), now, now, ""))
        self.db.commit()
        return self.get(token)

    def get(self, token):
        row = self.db.execute("SELECT * FROM inventory_op WHERE token=?", (text(token),)).fetchone()
        if not row:
            raise InventoryError("找不到盘点记录，请重新查询预览")
        if not self.client.uid or text(self.client.coid) != row["login_co_id"] or text(self.client.uid) != row["login_uid"]:
            raise InventoryError("请使用创建盘点记录的原账号登录")
        r = dict(row)
        r["snapshot"] = json.loads(r.pop("snapshot_json"))
        r.pop("identity", None)
        return r

    def update(self, token, status, error=""):
        self.db.execute("UPDATE inventory_op SET status=?,updated=?,error=? WHERE token=?", (status, time.time(), error, token))
        self.db.commit()

    def history(self):
        if not self.client.uid:
            return []
        rows = self.db.execute("SELECT token FROM inventory_op WHERE login_co_id=? AND login_uid=? AND status!='preview' ORDER BY created DESC LIMIT 100",
            (text(self.client.coid), text(self.client.uid))).fetchall()
        return [self.get(r["token"]) for r in rows]

    def submit(self, token):
        job = self.get(token)
        if job["status"] != "preview":
            return job  # 重复提交只返回原状态，从不重写
        if time.time() - job["created"] > 120:
            self.update(token, "stale", "预览已超过2分钟，请重新查询")
            raise InventoryError("预览已过期，请重新查询")
        snap = job["snapshot"]
        api = self.api(snap["owner"], snap["auth"])
        row = self.find(api, snap)
        if number(row["qty"]) != number(snap["old_qty"]):
            self.update(token, "stale", "提交前库存已变化，本次未写入")
            raise InventoryError("提交前库存已变化，本次未写入，请重新查询")
        # 与同库存其它待核实记录互斥，在本地事务中预留；应用 POST 另有全局锁。
        with self.db:
            current = self.db.execute("SELECT * FROM inventory_op WHERE token=?", (token,)).fetchone()
            if current["status"] != "preview":
                return self.get(token)
            active = self.db.execute("SELECT 1 FROM inventory_op WHERE identity=? AND status IN ('sending','unknown')", (current["identity"],)).fetchone()
            if active:
                raise InventoryError("该库存有未决操作，禁止再次提交")
            changed = self.db.execute("UPDATE inventory_op SET status='sending',updated=? WHERE token=? AND status='preview'", (time.time(), token))
            if changed.rowcount != 1:
                return self.get(token)
        try:
            api.write(snap["pack_item_id"], snap["target_qty"])
        except CallbackRejected as e:
            self.update(token, "rejected", "ERP明确拒绝：" + str(e))
            return self.get(token)
        except Exception as e:
            self.update(token, "unknown", "写入结果不明，禁止重发；请回读核对。" + str(e))
            return self.get(token)
        return self.verify(token)

    def verify(self, token):
        job = self.get(token)
        if job["status"] not in ("sending", "unknown"):
            return job
        snap = job["snapshot"]
        try:
            row = self.find(self.api(snap["owner"], snap["auth"]), snap)
            if number(row["qty"]) != number(snap["target_qty"]):
                raise InventoryError("当前数量为 %s，目标为 %s；请在聚水潭人工核实，不要重提" % (row["qty"], snap["target_qty"]))
        except Exception as e:
            self.update(token, "unknown", str(e))
        else:
            self.update(token, "verified", "已回读确认当前数量等于目标；不代表可排除其它终端同时修改")
        return self.get(token)

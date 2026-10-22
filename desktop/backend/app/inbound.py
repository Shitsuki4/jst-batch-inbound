# -*- coding: utf-8 -*-
"""按收货实收生成聚水潭待入库单；不调用 ConfirmBatch。

网络写入不是本地事务：先持久化快照和物流批次占用，再创建远端单据。
创建结果不明时绝不自动重建；取得 io_id 后，重试只覆写同一草稿的绝对数量。
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from jst import CallbackRejected
from statelock import keyed, unlocking



class InboundError(ValueError):
    pass


def quantity(value):
    try:
        q = Decimal(str(value))
        if not q.is_finite() or q < 0:
            raise ValueError()
        return q
    except (InvalidOperation, ValueError, TypeError):
        raise InboundError("实收数量必须是有限的非负数字")


def text(value):
    return str(value if value is not None else "").strip()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PurchaseInAPI:
    """只使用随项目提供、已记录参数的 WebForms 接口。"""
    ROOT = "https://www.erp321.com/app/scm/purchaseIn/"

    def __init__(self, client, owner, auth):
        self.client = client
        self.context = {"owner_co_id": owner, "authorize_co_id": auth}
        self.list_url = self.ROOT + "purchaseIn.aspx?" + urlencode(self.context)
        self.fields = dict.fromkeys((
            "o_id io_id date_type io_date status f_status supplier_name_id supplier_name "
            "group sku_id creator_name wh_id remark wmslabels nowmslabels so_id lc_id_v lc_id "
            "l_id merge_so_id is_print _jt_page_count_enabled _cbb_supplier_name _cbb_lc_id "
            "queryType purchase_share").split(), "")
        self.fields.update(self.context, bin_check_name="pick", freight_radio="-1", created_pay="-1")

    def detail_url(self, io_id):
        return self.ROOT + "purchaseinitem.aspx?" + urlencode(dict(self.context, io_id=io_id))

    def call(self, method, args, io_id=None):
        url = self.detail_url(io_id) if io_id else self.list_url
        return self.client.callback(url, method, args,
                                    fields=self.context if io_id else self.fields,
                                    call_control="{page}")

    def warehouses(self):
        # 附属仓字典是只读调用，但 WebForms 仍要求 CallControl。
        result = self.call("LoadWarehouse", [])
        if not isinstance(result, dict):
            raise InboundError("附属仓列表返回异常，请检查账号权限")
        return [{"id": text(k), "name": text(v)} for k, v in result.items()
                if text(k).isdigit() and isinstance(v, str)]

    def row(self, io_id):
        def find(wh_id=""):
            filters = [{"k": "io_id", "v": text(io_id), "c": "@="}]
            fields = dict(self.fields)
            if wh_id:
                filters.append({"k": "wh_id", "v": wh_id, "c": "="})
                fields["wh_id"] = wh_id
            result = self.client.callback(self.list_url, "LoadDataToJSON",
                ["1", json_text(filters), "{}"], fields=fields, page_size=25)
            rows = result.get("datas", []) if isinstance(result, dict) else []
            return next((r for r in rows if text(r.get("io_id")) == text(io_id)), None)
        row = find()
        if row: return row
        # 不带 wh_id 默认只查主仓；附属仓单据必须逐仓精确查询，不能误报不存在。
        for wh in self.warehouses():
            row = find(wh["id"])
            if row: return row
        raise InboundError("未能在原货主/仓储公司及附属仓下找到该入库单，请检查账号及单号")

    def details(self, io_id):
        url = self.detail_url(io_id)
        result = self.client._parse_jt_data(self.client._request(url, referer=url))
        if not isinstance(result, dict) or not isinstance(result.get("datas"), list):
            raise InboundError("入库明细响应异常，不能核对数量")
        return result["datas"]

    def create(self, po_id):
        result = self.call("NewWithPurchaseByPoIds", [text(po_id), "false"])
        data = result.get("data", result) if isinstance(result, dict) else {}
        io_id = text(data.get("io_id"))
        if not io_id or not io_id.isdigit():
            raise InboundError("创建响应未返回有效入库单号；请先到聚水潭核实，勿重复开单")
        return io_id

    def validate_products(self, snapshot):
        """Live exact SKU check at generation/resume, never on the scan hot path.

        Purchase-detail validation may be disabled; product existence may not.
        Do not use barcode lookup or stale local snapshots as proof of existence.
        """
        missing = []
        for sku in dict.fromkeys(p["sku_id"] for p in snapshot["items"]):
            try:
                matches = self.client.item_search("sku_id", sku,
                    self.context["owner_co_id"], auth=self.context["authorize_co_id"])
                if not isinstance(matches, list):
                    raise ValueError("invalid product response")
                found = any(isinstance(p, dict) and text(p.get("sku_id")) == sku for p in matches)
            except Exception as e:
                raise InboundError("商品资料校验失败（编码 %s），已停止入库写入。请检查网络、登录及当前货主权限后重试；已有入库单请继续原单，不要重新开单。" % sku) from e
            if not found:
                missing.append(sku)
        if missing:
            raise InboundError("以下商品编码在当前货主的商品资料中不存在，不能生成或加入入库单：%s。请先在聚水潭建立商品资料，或更正/移除收货编码后重新预览；采购单外商品也必须有商品资料，关闭采购明细校验不能跳过此检查。" % "、".join(missing))

    def fill(self, io_id, snapshot):
        row = self.row(io_id)
        self.check_source(row, snapshot)
        self.validate_products(snapshot)
        row = self.ensure_warehouse(io_id, row, snapshot)
        desired = {p["sku_id"]: quantity(p["qty"]) for p in snapshot["items"]}
        rows = self.details(io_id)
        # 即使已被人在聚水潭确认，也允许只读核对成功；绝不修改已入库单。
        if row.get("status") != "待入库":
            if row.get("status") != "已入库":
                raise InboundError("入库单已取消或状态不支持，请在聚水潭核对原单")
            self.verify(rows, desired)
            return
        existing = {text(r.get("sku_id")) for r in rows}
        missing = [sku for sku in desired if sku not in existing]
        if missing:
            if not row.get("__KeyData"):
                raise InboundError("入库单缺少行校验数据，不能添加实收商品")
            if snapshot.get("purchase_watch_permission"):
                # Procurement members retain the official procurement-add path;
                # unrelated SKUs never borrow a similar SKU's purchase cost.
                outside = {p['sku_id'] for p in snapshot['items'] if p.get('outside_purchase')}
                inside = [sku for sku in missing if sku not in outside]
                if inside:
                    additions = [{"sku_id": sku, "in_qty": str(desired[sku]), "qty": float(desired[sku])} for sku in inside]
                    if self.call("CheckAndChoose", [json_text(additions), row["__KeyData"]]) is not True:
                        raise InboundError("采购行添加校验未通过，请核对原单")
                    self.call("AddItemsUnify", [row["__KeyData"], json_text(additions), "true", "false"])
                extra = [sku for sku in missing if sku in outside]
                if extra:
                    fresh = self.row(io_id)
                    self.check_source(fresh, snapshot)
                    if fresh.get('status')!='待入库' or not fresh.get('__KeyData'):
                        raise InboundError('原单状态变化，停止添加单外商品')
                    self.call("AddItems", [fresh["__KeyData"], ",".join(extra)])
            elif snapshot.get("warehouse") and snapshot.get("validate_purchase_details", True):
                # 当前界面抓包确认的按明细数量添加流程；旧记录仍走原协议。
                additions = [{"sku_id": sku, "in_qty": str(desired[sku]),
                              "qty": int(desired[sku]) if desired[sku] == desired[sku].to_integral_value() else float(desired[sku])}
                             for sku in missing]
                if self.call("CheckAndChoose", [json_text(additions), row["__KeyData"]]) is not True:
                    raise InboundError("添加实收商品的前置检查未通过，已停止，请核对原单")
                self.call("AddItemsUnify", [row["__KeyData"], json_text(additions), "true", "false"])
            else:
                # 不校验采购明细时使用界面「添加商品」流程，不改采购单。
                # 新增默认数量可能为1；下方只按实收绝对值覆写，并回读确认。
                self.call("AddItems", [row["__KeyData"], ",".join(missing)])
            rows = self.details(io_id)
        by_sku, ids = {}, set()
        for r in rows:
            sku, row_id = text(r.get("sku_id")), text(r.get("ioi_id"))
            if sku in by_sku or not row_id.isdigit() or row_id in ids:
                raise InboundError("远端入库明细存在重复商品/无效明细ID，请人工核查，不自动分摊")
            by_sku[sku] = r
            ids.add(row_id)
        if not set(desired).issubset(by_sku):
            raise InboundError("实收商品未完整加入草稿，请稍后继续完成此单")
        groups = {}
        for sku, r in by_sku.items():
            target = desired.get(sku, Decimal(0))
            # false 理论上创建空明细；如果上游带入零数量采购行，保持为零，不套应收。
            if sku not in desired and quantity(r.get("qty")) != 0:
                raise InboundError("草稿包含本次未实收的非零商品，停止写入，请人工核查")
            if quantity(r.get("qty")) != target:
                groups.setdefault(str(target), []).append(text(r["ioi_id"]))
        for qty, row_ids in groups.items():
            self.call("UpQtyBatch", [qty, ",".join(row_ids)], io_id=io_id)
        self.verify(self.details(io_id), desired)
        final = self.row(io_id)
        self.check_source(final, snapshot)
        if final.get("status") not in ("待入库", "已入库"):
            raise InboundError("入库单状态已变化，请人工核对原单")
        if snapshot.get("warehouse") and text(final.get("wh_id")) != snapshot["warehouse"]["id"]:
            raise InboundError("入库仓库回读不一致，请核对原单")

    def ensure_warehouse(self, io_id, row, snapshot):
        target = snapshot.get("warehouse")
        if not target:  # 兼容升级前已持久化的原单，不替旧单擅自选仓
            return row
        options = self.warehouses()
        if target not in options or sum(w["name"] == target["name"] for w in options) != 1:
            raise InboundError("仓库字典已变化或存在同名仓，请核对原入库单")
        if text(row.get("wh_id")) == target["id"]:
            return row
        if row.get("status") != "待入库":
            raise InboundError("原单仓库与所选仓库不符，且不是待入库草稿，禁止修改")
        if not row.get("__KeyData"):
            raise InboundError("原入库单缺少行校验数据，禁止换仓")
        # Official jTable.Save uses $.toJSON(data), not an object-valued argument.
        # Save 用完整列表行，warehouse 为目标名称，wh_id 保留原值。
        # 不传 CallControl；不碰 lwh_id（渠道仓），不构造空白行抹掉其它业务字段。
        payload = {k: ("" if v is None else v) for k, v in row.items()}
        payload["warehouse"] = target["name"]
        payload["wh_id"] = text(row["wh_id"])
        result = self.client.callback(self.list_url, "Save", [json_text(payload)], fields=self.fields)
        saved = result.get("data", {}) if isinstance(result, dict) else {}
        self.check_source(saved, snapshot)
        if text(saved.get("io_id")) != text(io_id) or text(saved.get("wh_id")) != target["id"]:
            raise InboundError("换仓响应不一致，已停止，请继续核对原单")
        fresh = self.row(io_id)  # 取换仓后新的 __KeyData，不能复用旧校验数据
        self.check_source(fresh, snapshot)
        if text(fresh.get("wh_id")) != target["id"] or fresh.get("status") != "待入库":
            raise InboundError("换仓后回读不一致，已停止操作")
        return fresh

    @staticmethod
    def check_source(row, snapshot):
        if text(row.get("o_id")) != snapshot["po_id"]:
            raise InboundError("入库单的源采购单与收货批次不一致，已停止操作")
        owner = snapshot["owner_co_id"]
        owner = owner if owner and owner != "0" else snapshot["auth_co_id"]
        for key, expected in (("owner_co_id", owner), ("wms_co_id", snapshot["auth_co_id"])):
            if (key in row or snapshot.get("warehouse")) and text(row.get(key)) != expected:
                raise InboundError("入库单货主／仓库公司与原收货批次不一致，已停止操作")

    @staticmethod
    def verify(rows, desired):
        actual = {}
        for r in rows:
            qty, sku = quantity(r.get("qty")), text(r.get("sku_id"))
            if qty:
                actual[sku] = actual.get(sku, Decimal(0)) + qty
        if actual != desired:
            raise InboundError("聚水潭回读数量与本次实收不一致，未标记完成；请继续核对原单，勿重新开单")


class InboundService:
    """入库生成。远程调用全部在 state_lock 之外（客户端经 Unlocking 包装）。

    同一收货库同一批次的 create / resume 由 keyed 锁串行；远程核对期间其他终端仍可扫码，
    预留（reserve_inbound）前在锁内比较本地状态令牌，期间有变化即报冲突，不用旧快照开单。
    预留之后由 inbound_batch / inbound_line 触发器冻结相关明细。"""

    def __init__(self, store, client):
        self.store, self.client = store, unlocking(client)

    def _key(self, sid):
        return ("inbound", getattr(self.store, "path", None) or id(self.store), int(sid or 0))

    def local_token(self, sid):
        """本批次本地状态的版本令牌：会话上下文、全部明细及入库记录状态。在锁内调用。"""
        s = self.store.get_session(sid) or {}
        lines = [tuple(r) for r in self.store.db.execute(
            "SELECT id,batch_id,key,sku_id,qty,matched FROM line WHERE session_id=? ORDER BY id", (sid,))]
        batches = [tuple(r) for r in self.store.db.execute(
            "SELECT id,tracking_no FROM batch WHERE session_id=? ORDER BY id", (sid,))]
        jobs = [(j["id"], j["status"], j["io_id"]) for j in self.store.inbound_jobs(sid)]
        head = tuple(s.get(k) for k in ("po_id", "owner_co_id", "auth_co_id", "items_json", "state"))
        return hashlib.sha256(json_text([head, lines, batches, jobs]).encode("utf-8")).hexdigest()

    def preview(self, sid, wh_id=None, *, validate_override=None, mode=None):
        session = self.store.get_session(sid)
        if not session:
            raise InboundError("找不到该收货批次")
        validate_purchase = (self.store.get_kv("inbound_validate_purchase", "1") != "0") if validate_override is None else bool(validate_override)
        if mode not in (None, "purchase_only", "all_received"):
            raise InboundError("请选择有效的入库方式")
        if mode is not None:
            validate_purchase = mode == "purchase_only"
        plan = {}
        for p in json.loads(session.get("items_json") or "[]"):
            sku = text(p.get("sku_id"))
            plan[sku] = plan.get(sku, Decimal(0)) + quantity(p.get("qty", 0))
        items, batches, warnings, excluded = {}, [], [], []
        for batch in self.store.batches(sid):
            lines = self.store.batch_lines(batch["id"])
            positive = []
            for line in lines:
                if line.get("submitted_job_id"):
                    continue
                qty = quantity(line["qty"])
                if not qty:
                    continue
                sku = text(line.get("sku_id"))
                if not sku or "," in sku or any(ord(c) < 32 or ord(c) == 127 for c in sku):
                    raise InboundError("物流单号 %s 的商品编码不合法，请先核对" % batch["tracking_no"])
                if mode == "purchase_only" and sku not in plan:
                    excluded.append({"id": line["id"], "tracking_no": batch["tracking_no"], "sku_id": sku, "name": line.get("name") or "", "qty": str(qty)})
                    continue
                if mode is None and validate_purchase and not line.get("matched"):
                    raise InboundError("物流单号 %s 的商品 %s 未匹配采购单；请重新打开「按实收入库」选择入库方式" %
                                       (batch["tracking_no"], sku))
                positive.append({"id": line["id"], "sku_id": sku, "qty": str(qty)})
                item = items.setdefault(sku, {"sku_id": sku, "name": line.get("name") or "",
                                              "qty": Decimal(0), "unmatched": False})
                item["unmatched"] = item["unmatched"] or not bool(line.get("matched"))
                item["qty"] += qty
            if positive:
                batches.append({"id": batch["id"], "tracking_no": batch["tracking_no"], "lines": positive})
        products = [dict(p, qty=str(p["qty"])) for p in items.values()]
        if products:
            warnings.append("生成前必须核对当前货主商品资料：采购单外但资料中存在的商品可选择「全部实收编码」添加；资料不存在或查询失败时停止，不创建空入库单。")
        if not validate_purchase:
            warnings.append("全部实收模式：实收商品通过「添加商品」加入入库草稿，不修改采购单；请确认商品编码及成本价。")
        for p in products:
            p["outside_purchase"] = p.pop("unmatched") or p["sku_id"] not in plan
            if mode is not None:
                p["outside_purchase"] = p["sku_id"] not in plan
            if p["outside_purchase"]:
                warnings.append("%s 为采购单外／未匹配商品，将按商品编码添加；生成前会核对当前货主的商品资料，不存在的编码将被拦截。成本价请在聚水潭确认。" % p["sku_id"])
            elif quantity(p["qty"]) > plan.get(p["sku_id"], Decimal(0)):
                warnings.append("%s 本次实收超过本地采购应收，请核对后再生成" % p["sku_id"])
        snapshot = {"session_id": sid, "po_id": text(session["po_id"]),
                    "owner_co_id": text(session.get("owner_co_id")),
                    "auth_co_id": text(session.get("auth_co_id")),
                    "batches": batches, "items": products,
                    "validate_purchase_details": validate_purchase}
        if mode is not None:
            snapshot.update(mode=mode, excluded_items=excluded)
        if wh_id is not None:
            api = self.context(snapshot)
            options = api.warehouses()
            chosen = next((w for w in options if w["id"] == text(wh_id)), None)
            if not chosen or sum(w["name"] == chosen["name"] for w in options) != 1:
                raise InboundError("请选择原货主／仓库公司下有效的入库仓库")
            snapshot["warehouse"] = chosen
        fingerprint = hashlib.sha256(json_text(snapshot).encode("utf-8")).hexdigest()
        jobs = []
        for j in self.store.inbound_jobs(sid):
            saved = json.loads(j["snapshot_json"])
            jobs.append(dict(j, warehouse=saved.get("warehouse"),
                             validate_purchase_details=saved.get("validate_purchase_details", True)))
        return {"snapshot": snapshot, "fingerprint": fingerprint,
                "total_qty": str(sum((quantity(p["qty"]) for p in products), Decimal(0))),
                "warnings": warnings, "jobs": jobs,
                "excluded_qty": str(sum((quantity(p["qty"]) for p in excluded), Decimal(0)))}

    def context(self, snapshot):
        if not self.client.uid or not self.client.coid:
            raise InboundError("请先登录聚水潭，再生成入库单")
        owner, auth = text(snapshot["owner_co_id"]), text(snapshot["auth_co_id"])
        if owner == "2147483647" or auth == "2147483647":
            raise InboundError("所有商家／所有仓库不能创建入库单，请选择具体货主和仓库公司重新打开采购单")
        if not auth:
            raise InboundError("该旧批次缺少公司上下文，请用原账号重新打开采购单后再生成")
        # 0 是聚水潭自有货主，使用原仓储公司；不使用当前界面临时选择的公司。
        owner = owner if owner and owner != "0" else auth
        return PurchaseInAPI(self.client, owner, auth)

    def create(self, sid, fingerprint, wh_id=None, *, mode=None):
        if mode not in (None, "purchase_only", "all_received"):
            raise InboundError("请选择有效的入库方式")
        with keyed(self._key(sid)):
            return self._create(sid, fingerprint, wh_id, mode=mode)

    def _create(self, sid, fingerprint, wh_id=None, *, mode=None):
        if getattr(self, "cross_guard", None): self.cross_guard(self.store, sid)
        token = self.local_token(sid)
        existing = self.store.db.execute("SELECT id,session_id FROM inbound_job WHERE fingerprint=?",
                                         (fingerprint,)).fetchone()
        if existing:
            if existing["session_id"] != sid:
                raise InboundError("收货批次不匹配")
            prior = json.loads(self.store.get_inbound(existing["id"])["snapshot_json"])
            if mode is not None and prior.get("mode") != mode:
                raise InboundError("确认入库方式与原入库记录不一致，请继续原单")
            if wh_id is not None:
                if prior.get("warehouse", {}).get("id") != text(wh_id):
                    raise InboundError("确认仓库与原入库记录不一致，请重新查看原单")
            return self.resume(existing["id"])
        preview = self.preview(sid, wh_id, mode=mode) if mode is not None else self.preview(sid, wh_id)
        snapshot = preview["snapshot"]
        if not snapshot["items"]:
            raise InboundError("没有尚未生成入库单的正数实收明细")
        if not fingerprint or fingerprint != preview["fingerprint"]:
            raise InboundError("实收明细、仓库或入库方式已变化，请重新预览并确认")
        if any(j["status"] not in ("complete", "rejected") for j in preview["jobs"]):
            raise InboundError("该收货批次尚有未完成的入库记录，请先核对/继续原单")
        api = self.context(snapshot)
        # 开关固定在确认快照中，原单续作不受后续设置变化影响。
        if snapshot.get("validate_purchase_details", True) or snapshot.get("purchase_watch_permission"):
            # 写之前读取原采购单，避免账号/上下文错误、商品已被从采购单移除。
            detail = self.client.fetch_detail(snapshot["po_id"], api.context["owner_co_id"], api.context["authorize_co_id"])
            counts = {}
            for p in detail.get("datas", []):
                sku = text(p.get("sku_id"))
                counts[sku] = counts.get(sku, 0) + 1
            for p in snapshot["items"]:
                if counts.get(p["sku_id"], 0) > 1 or (snapshot.get("validate_purchase_details", True) and counts.get(p["sku_id"], 0) != 1):
                    raise InboundError("商品 %s 在最新采购单中不存在或有多行，不能自动确定采购明细/成本" % p["sku_id"])
        # Check every SKU before reserving batches or creating an empty ERP draft.
        api.validate_products(snapshot)
        # 以上远程核对都在锁外进行；回到锁内后确认期间没有其他终端改动本批次，再原子预留。
        if self.local_token(sid) != token:
            raise InboundError("核对期间收货明细或入库记录已变化（可能有其他终端操作），未生成入库单；请重新预览并确认")
        if getattr(self, "cross_guard", None): self.cross_guard(self.store, sid)
        job_id = self.store.reserve_inbound(snapshot, fingerprint, text(self.client.coid))
        try:
            io_id = api.create(snapshot["po_id"])
            self.store.update_inbound(job_id, "pending", io_id=io_id)
        except CallbackRejected as e:
            self.store.reject_inbound(job_id, str(e))
            raise InboundError("聚水潭明确拒绝创建，实收未锁定。请处理权限或业务错误后重新预览：" + str(e))
        except Exception as e:
            self.store.update_inbound(job_id, "unknown", error=str(e))
            raise InboundError("创建结果待核实，已禁止重复开单。请在聚水潭按采购单核查，找到单号后关联原单。原因：" + str(e))
        return self.resume(job_id)

    def resume(self, job_id, bind_io_id=""):
        job = self.store.get_inbound(job_id)
        if not job:
            raise InboundError("找不到入库生成记录")
        with keyed(self._key(job["session_id"])):
            return self._resume(job_id, bind_io_id)

    def _resume(self, job_id, bind_io_id=""):
        job = self.store.get_inbound(job_id)
        if not job:
            raise InboundError("找不到入库生成记录")
        if job["status"] == "rejected":
            raise InboundError("该创建请求已被拒绝，请重新预览后生成")
        snapshot = json.loads(job["snapshot_json"])
        api = self.context(snapshot)
        if text(self.client.coid) != job["login_co_id"]:
            raise InboundError("请登录创建这条入库记录时的公司账号，再继续原单")
        if job["status"] == "complete":
            return job
        io_id = job["io_id"]
        if bind_io_id:
            if io_id and io_id != bind_io_id:
                raise InboundError("该记录已关联入库单，不能更换单号")
            if not bind_io_id.isdigit():
                raise InboundError("请输入有效的聚水潭入库单号")
            row = api.row(bind_io_id)
            api.check_source(row, snapshot)
            rows = api.details(bind_io_id)
            desired = {p["sku_id"]: quantity(p["qty"]) for p in snapshot["items"]}
            # 手工关联只接收空草稿或完全一致的单据，避免覆写同采购单的其它业务单。
            if any(quantity(r.get("qty")) for r in rows):
                api.verify(rows, desired)
            elif row.get("status") != "待入库":
                raise InboundError("只能关联待入库空草稿或数量完全一致的单据")
            current = self.store.get_inbound(job_id)
            if not current or current["status"] != job["status"] or (current["io_id"] or "") != (io_id or ""):
                raise InboundError("入库记录在核对期间已变化，请刷新后查看原单")
            self.store.update_inbound(job_id, "pending", io_id=bind_io_id)
            io_id = bind_io_id
        if not io_id:
            raise InboundError("上次创建结果不明，请先在聚水潭核查并关联原单号，系统不会自动重建")
        try:
            api.fill(io_id, snapshot)
            self.store.update_inbound(job_id, "complete")
        except Exception as e:
            self.store.update_inbound(job_id, "pending", error=str(e))
            raise InboundError("入库单 %s 尚未完成数量核对，请继续原单（不会重复创建）。原因：%s" % (io_id, e))
        return self.store.get_inbound(job_id)

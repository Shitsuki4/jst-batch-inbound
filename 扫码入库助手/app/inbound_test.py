# -*- coding: utf-8 -*-
"""离线实收入库回归：真实 SQLite + 模拟已记录的聚水潭 WebForms 协议。"""
import copy
import json
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from inbound import InboundService, InboundError, PurchaseInAPI, quantity
from store import Store


class FakeJst:
    uid, coid = 'USER', 'WAREHOUSE'

    def __init__(self):
        self.calls = []
        self.docs = {}
        self.creates = 0
        self.lose_create = False
        self.fail_qty = False
        self.ignore_qty = False
        self.create_zero_rows = False
        self.detail_rows = [{'sku_id': 'A', 'qty': 100}, {'sku_id': 'B', 'qty': 200}]
        self.lose_add = False
        self.ignore_add = False
        self.products = {s: {"sku_id": s, "name": "商品"+s} for s in ("A", "B", "X", "Y")}
        self.product_queries = []
        self.product_error = False

    def item_search(self, field, value, owner="", limit=200, timeout=6, auth=""):
        assert field == "sku_id", "Product validation must not resolve barcodes"
        self.product_queries.append((field, value, owner, auth))
        if self.product_error:
            raise TimeoutError("模拟商品接口不可用")
        return [dict(self.products[value])] if value in self.products else []

    def fetch_detail(self, po_id, owner, auth):
        self.detail_context = (po_id, owner, auth)
        return {'datas': self.detail_rows}

    def callback(self, url, method, args, **kw):
        self.calls.append((url, method, args, kw))
        if method == 'LoadDataToJSON':
            assert 'call_control' not in kw
            io_id = str(json.loads(args[1])[0]['v'])
            d = self.docs.get(io_id)
            return {'datas': [dict(d['head'])] if d else []}
        if method == 'Save':
            assert 'call_control' not in kw
            assert len(args)==1 and isinstance(args[0],str), "Save requires JSON string"
            row = json.loads(args[0])
            doc = self.docs[str(row['io_id'])]
            assert row['__KeyData'] == doc['head']['__KeyData']
            assert str(row['wh_id']) == str(doc['head']['wh_id'])
            assert row.get('remark') == doc['head'].get('remark')
            wid = next(k for k, v in {'1':'主仓', '3':'进货仓', '4':'次品仓'}.items() if v == row['warehouse'])
            doc['head'].update(warehouse=row['warehouse'], wh_id=wid, __KeyData='KEY-' + str(row['io_id']) + '-new')
            return {'data': dict(doc['head'])}
        assert kw['call_control'] == '{page}'
        if method == 'LoadWarehouse':
            return {'1': '主仓', '3': '进货仓', '4': '次品仓'}
        if method == 'NewWithPurchaseByPoIds':
            assert args[1] == 'false', '不可带入采购未入库数量'
            self.creates += 1
            io_id = str(8000 + self.creates)
            self.docs[io_id] = {'head': {'io_id': io_id, 'o_id': args[0], 'status': '待入库', '__KeyData': 'KEY-' + io_id, 'owner_co_id': kw['fields']['owner_co_id'], 'wms_co_id': kw['fields']['authorize_co_id'], 'wh_id': '1', 'warehouse': '主仓', 'remark': '保留原备注'},
                                'rows': []}
            if self.create_zero_rows:
                self.docs[io_id]['rows'] = [{'ioi_id': '1', 'sku_id': 'B', 'qty': '0'}]
            if self.lose_create:
                raise TimeoutError('模拟服务端创建成功但响应丢失')
            return {'data': {'io_id': io_id}}
        if method == 'CheckAndChoose':
            assert isinstance(args[0],str), 'CheckAndChoose requires JSON string'
            assert isinstance(json.loads(args[0]),list)
            assert args[1] in [d['head']['__KeyData'] for d in self.docs.values()]
            return True
        if method == 'AddItemsUnify':
            assert isinstance(args[1],str), 'AddItemsUnify requires JSON string'
            assert args[2:] == ['true', 'false']
            if self.fail_qty:
                self.fail_qty = False
                raise TimeoutError('模拟添加绝对数量超时')
            doc = self.docs[args[0].split('-')[1]]
            for p in json.loads(args[1]):
                assert p['sku_id'] not in {r['sku_id'] for r in doc['rows']}
                doc['rows'].append({'ioi_id': str(len(doc['rows']) + 1), 'sku_id': p['sku_id'], 'qty': p['in_qty']})
            return None
        if method == 'AddItems':
            if self.ignore_add:
                return None
            doc = self.docs[args[0].split('-')[1]]
            for sku in args[1].split(','):
                assert sku not in {r['sku_id'] for r in doc['rows']}, '不能重复添加'
                doc['rows'].append({'ioi_id': str(len(doc['rows']) + 1), 'sku_id': sku, 'qty': '1'})
            if self.lose_add:
                self.lose_add = False
                raise TimeoutError('模拟添加商品成功但响应丢失')
            return None
        if method == 'UpQtyBatch':
            if self.fail_qty:
                self.fail_qty = False
                raise TimeoutError('模拟改数量超时')
            if not self.ignore_qty:
                io_id = parse_qs(urlparse(url).query)['io_id'][0]
                for r in self.docs[io_id]['rows']:
                    if r['ioi_id'] in args[1].split(','):
                        r['qty'] = args[0]
            return None
        raise AssertionError('不应调用接口 ' + method)

    def _request(self, url, **kw):
        io_id = parse_qs(urlparse(url).query)['io_id'][0]
        return json.dumps({'datas': self.docs[io_id]['rows']})

    _parse_jt_data = staticmethod(json.loads)


class InboundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='jst-inbound-test-')
        self.path = os.path.join(self.tmp.name, 'jst.db')
        self.st = Store(self.path)
        self.jst = FakeJst()
        self.svc = InboundService(self.st, self.jst)
        self.sid = self.st.open_session('OUT-1', '123', 'po_id',
            {'owner_co_id': 'OWNER', 'auth_co_id': 'WAREHOUSE', 'supplier': '测试供应商'},
            [{'sku_id': 'A', 'qty': 100}, {'sku_id': 'B', 'qty': 200}])
        self.b1, _ = self.st.ensure_batch(self.sid, 'SF001')
        self.b2, _ = self.st.ensure_batch(self.sid, 'SF002')

    def tearDown(self):
        self.st.db.close()
        self.tmp.cleanup()

    def add(self, batch, sku, qty, matched=True):
        return self.st.add_qty(batch['id'], self.sid, {'sku_id': sku, 'name': '商品' + sku, 'matched': matched}, qty)[0]

    def generate(self):
        return self.svc.create(self.sid, self.svc.preview(self.sid)['fingerprint'])

    def actual(self, job):
        return {r['sku_id']: Decimal(str(r['qty'])) for r in self.jst.docs[job['io_id']]['rows'] if quantity(r['qty'])}

    def test_aggregate_only_actual_not_plan_with_decimal_and_zero(self):
        self.add(self.b1, 'A', 2.5); self.add(self.b2, 'A', 3.25); self.add(self.b2, 'B', 0)
        preview = self.svc.preview(self.sid)
        self.assertEqual(preview['total_qty'], '5.75')
        self.assertEqual(len(preview['snapshot']['items']), 1)
        job = self.generate()
        self.assertEqual(self.actual(job), {'A': Decimal('5.75')})
        self.assertEqual(self.jst.detail_context, ('123', 'OWNER', 'WAREHOUSE'))
        self.assertEqual(job['status'], 'complete')
        self.assertNotIn('ConfirmBatch', [c[1] for c in self.jst.calls])
        self.assertEqual(len(self.st.batches_with_lines(self.sid)), 2)
        self.assertTrue(all(b['inbound_job_id'] == job['id'] for b in self.st.batches(self.sid)))

    def test_different_quantities_and_duplicate_request(self):
        self.add(self.b1, 'A', 2); self.add(self.b1, 'B', 7)
        fp = self.svc.preview(self.sid)['fingerprint']
        j = self.svc.create(self.sid, fp)
        self.assertEqual(self.actual(j), {'A': Decimal(2), 'B': Decimal(7)})
        self.assertEqual(self.svc.create(self.sid, fp)['id'], j['id'])
        self.assertEqual(self.jst.creates, 1)

    def test_empty_zero_unmatched_nan_negative_stale_are_rejected_before_write(self):
        with self.assertRaises(InboundError): self.generate()
        lid = self.add(self.b1, 'A', 0)
        with self.assertRaises(InboundError): self.generate()
        self.st.set_qty(lid, 2)
        old = self.svc.preview(self.sid)['fingerprint']
        self.st.set_qty(lid, 3)
        with self.assertRaisesRegex(InboundError, '变化'): self.svc.create(self.sid, old)
        self.st.db.execute('UPDATE line SET matched=0 WHERE id=?', (lid,)); self.st.db.commit()
        with self.assertRaisesRegex(InboundError, '未匹配'): self.generate()
        for q in ['NaN', 'Infinity', '-2', None, 'bad']:
            with self.subTest(q=q):
                with self.assertRaises(InboundError): quantity(q)
        self.assertEqual(self.jst.creates, 0)

    def test_preflight_login_missing_duplicate_and_missing_sku(self):
        self.add(self.b1, 'A', 2)
        self.jst.uid = ''
        with self.assertRaisesRegex(InboundError, '登录'): self.generate()
        self.jst.uid = 'USER'
        for rows in [[], [{'sku_id': 'A'}, {'sku_id': 'A'}]]:
            self.jst.detail_rows = rows
            with self.assertRaises(InboundError): self.generate()
        self.assertEqual(self.jst.creates, 0)
        self.assertFalse(self.st.inbound_jobs(self.sid))

    def test_new_receipt_excludes_previous_and_other_session(self):
        self.add(self.b1, 'A', 2)
        one = self.generate()
        self.add(self.b2, 'A', 4)
        two = self.generate()
        self.assertEqual(self.actual(one), {'A': Decimal(2)})
        self.assertEqual(self.actual(two), {'A': Decimal(4)})
        self.assertNotEqual(one['io_id'], two['io_id'])
        other = self.st.open_session('456', '456', 'po_id', {}, [])
        b, _ = self.st.ensure_batch(other, 'SF003')
        self.st.add_qty(b['id'], other, {'sku_id': 'A'}, 90)
        self.assertEqual(self.svc.preview(self.sid)['snapshot']['items'], [])

    def test_lock_all_mutations_and_restart_persistence(self):
        lid = self.add(self.b1, 'A', 2)
        job = self.generate()
        actions = [lambda: self.st.set_qty(lid, 3), lambda: self.st.del_line(lid),
                   lambda: self.st.del_batch(self.b1['id']), lambda: self.st.del_session(self.sid),
                   lambda: self.st.rename_batch(self.b1['id'], 'NEW'), lambda: self.add(self.b1, 'B', 1)]
        for action in actions:
            with self.assertRaises((ValueError, sqlite3.IntegrityError)): action()
            self.st.db.rollback()
        self.st.db.close(); self.st = Store(self.path); self.svc = InboundService(self.st, self.jst)
        self.assertEqual(self.svc.resume(job['id'])['io_id'], job['io_id'])
        self.assertEqual(self.jst.creates, 1)
        self.assertEqual(self.st.batch_lines(self.b1['id'])[0]['qty'], 2)

    def test_partial_failure_resumes_same_document_absolute_qty(self):
        self.add(self.b1, 'A', 8); self.jst.fail_qty = True
        with self.assertRaisesRegex(InboundError, '继续原单'): self.generate()
        job = self.st.inbound_jobs(self.sid)[0]
        self.assertEqual(job['status'], 'pending')
        result = self.svc.resume(job['id'])
        self.assertEqual(self.actual(result), {'A': Decimal(8)})
        self.assertEqual(self.jst.creates, 1)
        self.assertEqual(len([c for c in self.jst.calls if c[1] == 'AddItems']), 1)

    def test_unknown_create_never_retries_and_can_bind_empty_original(self):
        self.add(self.b1, 'A', 6); self.jst.lose_create = True
        fp = self.svc.preview(self.sid)['fingerprint']
        with self.assertRaisesRegex(InboundError, '结果待核实'): self.svc.create(self.sid, fp)
        job = self.st.inbound_jobs(self.sid)[0]
        with self.assertRaisesRegex(InboundError, '不会自动重建'): self.svc.create(self.sid, fp)
        self.assertEqual(self.jst.creates, 1)
        done = self.svc.resume(job['id'], '8001')
        self.assertEqual(self.actual(done), {'A': Decimal(6)})
        self.assertEqual(self.jst.creates, 1)

    def test_pending_blocks_new_generation_and_wrong_company_resume(self):
        self.add(self.b1, 'A', 5); self.jst.fail_qty = True
        with self.assertRaises(InboundError): self.generate()
        self.add(self.b2, 'A', 2)
        with self.assertRaisesRegex(InboundError, '未完成'): self.generate()
        self.jst.coid = 'OTHER'
        with self.assertRaisesRegex(InboundError, '公司账号'): self.svc.resume(self.st.inbound_jobs(self.sid)[0]['id'])
        self.assertEqual(self.jst.creates, 1)

    def test_readback_mismatch_does_not_mark_success(self):
        self.add(self.b1, 'A', 9); self.jst.ignore_qty = True
        with self.assertRaisesRegex(InboundError, '不一致'): self.generate()
        job = self.st.inbound_jobs(self.sid)[0]
        self.assertEqual(job['status'], 'pending')
        self.jst.ignore_qty = False
        self.assertEqual(self.svc.resume(job['id'])['status'], 'complete')

    def test_bind_rejects_other_po_nonmatching_or_already_linked_document(self):
        self.add(self.b1, 'A', 9); self.jst.lose_create = True
        with self.assertRaises(InboundError): self.generate()
        job = self.st.inbound_jobs(self.sid)[0]
        self.jst.docs['8001']['head']['o_id'] = '999'
        with self.assertRaisesRegex(InboundError, '源采购单'): self.svc.resume(job['id'], '8001')
        self.jst.docs['8001']['head']['o_id'] = '123'
        self.jst.docs['8001']['rows'] = [{'ioi_id': '1', 'sku_id': 'A', 'qty': '2'}]
        with self.assertRaisesRegex(InboundError, '不一致'): self.svc.resume(job['id'], '8001')
        self.assertFalse(self.st.get_inbound(job['id'])['io_id'])

    def test_inherited_zero_purchase_rows_never_receive_plan(self):
        self.add(self.b1, 'A', 6); self.jst.create_zero_rows = True
        job = self.generate()
        self.assertEqual(self.actual(job), {'A': Decimal(6)})
        self.assertEqual(next(r for r in self.jst.docs[job['io_id']]['rows'] if r['sku_id'] == 'B')['qty'], '0')

    def test_over_receipt_warning_does_not_clamp_actual(self):
        self.add(self.b1, 'A', 101)
        self.assertTrue(self.svc.preview(self.sid)['warnings'])
        self.assertEqual(self.actual(self.generate()), {'A': Decimal(101)})

    def test_definite_rejection_unlocks_and_allows_fresh_confirmation(self):
        from jst import CallbackRejected
        self.add(self.b1, 'A', 4)
        with patch.object(PurchaseInAPI, 'create', side_effect=CallbackRejected('没有创建权限')):
            with self.assertRaisesRegex(InboundError, '明确拒绝'): self.generate()
        self.assertEqual(self.st.inbound_jobs(self.sid)[0]['status'], 'rejected')
        self.assertFalse(self.st.batches(self.sid)[0]['inbound_job_id'])
        self.add(self.b1, 'A', 1)
        self.assertEqual(self.actual(self.generate()), {'A': Decimal(5)})

    def test_cancelled_document_cannot_be_marked_complete(self):
        self.add(self.b1, 'A', 4); self.jst.fail_qty = True
        with self.assertRaises(InboundError): self.generate()
        job = self.st.inbound_jobs(self.sid)[0]
        self.jst.docs[job['io_id']]['head']['status'] = '已取消'
        with self.assertRaisesRegex(InboundError, '已取消'): self.svc.resume(job['id'])
        self.assertEqual(self.st.get_inbound(job['id'])['status'], 'pending')

    def test_existing_database_migration_is_additive(self):
        self.add(self.b1, 'A', 2)
        before = self.st.batch_lines(self.b1['id'])
        again = Store(self.path)
        try:
            self.assertEqual(again.batch_lines(self.b1['id']), before)
            self.assertTrue(again.db.execute("SELECT 1 FROM sqlite_master WHERE name='inbound_job'").fetchone())
        finally: again.db.close()



    def test_switch_default_on_and_disabled_adds_purchase_external_items(self):
        self.add(self.b1, 'A', 2)
        self.add(self.b1, 'X', 2.5, matched=False)
        self.add(self.b2, 'X', 3, matched=False)
        self.add(self.b2, 'ZERO', 0, matched=False)
        with self.assertRaisesRegex(InboundError, '选择入库方式'):
            self.svc.preview(self.sid, '4')
        self.st.set_many({'inbound_validate_purchase': '0'})
        preview = self.svc.preview(self.sid, '4')
        self.assertFalse(preview['snapshot']['validate_purchase_details'])
        self.assertEqual(preview['total_qty'], '7.5')
        self.assertTrue(next(p for p in preview['snapshot']['items'] if p['sku_id'] == 'X')['outside_purchase'])
        self.assertIn('成本价', ''.join(preview['warnings']))
        with patch.object(self.jst, 'fetch_detail', side_effect=AssertionError('关闭后不得要求匹配采购单')):
            job = self.svc.create(self.sid, preview['fingerprint'], '4')
        self.assertEqual(self.actual(job), {'A': Decimal('2'), 'X': Decimal('5.5')})
        self.assertEqual(self.jst.docs[job['io_id']]['head']['wh_id'], '4')
        added = [c for c in self.jst.calls if c[1] == 'AddItems']
        self.assertEqual(len(added), 1)
        self.assertEqual(set(added[0][2][1].split(',')), {'A', 'X'})
        self.assertTrue(added[0][2][0].endswith('-new'))
        self.assertFalse(any(c[1] in ('ConfirmBatch', 'AddItemsUnify', 'CheckAndChoose', 'AddNotInItems') for c in self.jst.calls))
        self.assertEqual(self.jst.detail_rows, [{'sku_id': 'A', 'qty': 100}, {'sku_id': 'B', 'qty': 200}])

    def test_setting_change_invalidates_preview_both_directions(self):
        self.add(self.b1, 'A', 2)
        for original, updated in [('1', '0'), ('0', '1')]:
            self.st.set_many({'inbound_validate_purchase': original})
            preview = self.svc.preview(self.sid, '1')
            self.st.set_many({'inbound_validate_purchase': updated})
            with self.assertRaisesRegex(InboundError, '变化'):
                self.svc.create(self.sid, preview['fingerprint'], '1')
        self.assertEqual(self.jst.creates, 0)

    def test_disabled_add_response_lost_resume_keeps_snapshot_and_no_duplicate(self):
        self.st.set_many({'inbound_validate_purchase': '0'})
        self.add(self.b1, 'X', 5, matched=False)
        preview = self.svc.preview(self.sid, '3')
        self.jst.lose_add = True
        with self.assertRaisesRegex(InboundError, '继续原单'):
            self.svc.create(self.sid, preview['fingerprint'], '3')
        job = self.st.inbound_jobs(self.sid)[0]
        self.assertEqual(job['status'], 'pending')
        self.st.set_many({'inbound_validate_purchase': '1'})
        job = self.svc.resume(job['id'])
        self.assertEqual(job['status'], 'complete')
        self.assertEqual(self.actual(job), {'X': Decimal(5)})
        self.assertFalse(json.loads(job['snapshot_json'])['validate_purchase_details'])
        self.assertEqual(self.svc.create(self.sid, preview['fingerprint'], '3')['id'], job['id'])
        self.assertEqual(self.jst.creates, 1)
        self.assertEqual(len([c for c in self.jst.calls if c[1] == 'AddItems']), 1)

    def test_disabled_missing_add_and_quantity_readback_still_block_completion(self):
        self.st.set_many({'inbound_validate_purchase': '0'})
        self.add(self.b1, 'X', 5, matched=False)
        preview = self.svc.preview(self.sid, '1')
        self.jst.ignore_add = True
        with self.assertRaisesRegex(InboundError, '未完整加入'):
            self.svc.create(self.sid, preview['fingerprint'], '1')
        job = self.st.inbound_jobs(self.sid)[0]
        self.assertEqual(job['status'], 'pending')
        self.jst.ignore_add = False
        self.jst.ignore_qty = True
        with self.assertRaisesRegex(InboundError, '回读数量'):
            self.svc.resume(job['id'])
        self.jst.ignore_qty = False
        self.assertEqual(self.svc.resume(job['id'])['status'], 'complete')
        self.assertEqual(self.jst.creates, 1)
        self.assertEqual(self.actual(job), {'X': Decimal(5)})

    def test_disabled_unknown_create_never_recreates_and_binding_still_checks_source(self):
        self.st.set_many({'inbound_validate_purchase': '0'})
        self.add(self.b1, 'X', 5, matched=False)
        self.jst.lose_create = True
        with self.assertRaisesRegex(InboundError, '待核实'):
            self.generate()
        job = self.st.inbound_jobs(self.sid)[0]
        with self.assertRaises(InboundError): self.svc.resume(job['id'])
        self.jst.docs['8001']['head']['o_id'] = 'other'
        with self.assertRaisesRegex(InboundError, '源采购单'):
            self.svc.resume(job['id'], '8001')
        self.jst.docs['8001']['head']['o_id'] = '123'
        self.assertEqual(self.svc.resume(job['id'], '8001')['status'], 'complete')
        self.assertEqual(self.jst.creates, 1)

    def test_disabled_invalid_codes_and_all_merchants_are_still_rejected(self):
        self.st.set_many({'inbound_validate_purchase': '0'})
        lid = self.add(self.b1, 'X', 5, matched=False)
        for code in ['', 'A,B', 'A\nB', 'A\x00B']:
            self.st.db.execute('UPDATE line SET sku_id=? WHERE id=?', (code, lid)); self.st.db.commit()
            with self.assertRaisesRegex(InboundError, '编码不合法'): self.generate()
        self.st.db.execute('UPDATE line SET sku_id=? WHERE id=?', ('X', lid))
        self.st.db.execute("UPDATE session SET owner_co_id='2147483647' WHERE id=?", (self.sid,))
        self.st.db.commit()
        with self.assertRaisesRegex(InboundError, '所有商家'): self.generate()
        self.assertEqual(self.jst.creates, 0)

    def test_setting_persists_after_reopen_and_legacy_snapshot_uses_strict_protocol(self):
        self.st.set_many({'inbound_validate_purchase': '0'})
        self.st.db.close()
        self.st = Store(self.path)
        self.svc = InboundService(self.st, self.jst)
        self.add(self.b1, 'A', 4)
        preview = self.svc.preview(self.sid, '1')
        self.assertFalse(preview['snapshot']['validate_purchase_details'])
        snapshot = preview['snapshot']
        del snapshot['validate_purchase_details']
        api = self.svc.context(snapshot)
        io_id = api.create(snapshot['po_id'])
        api.fill(io_id, snapshot)
        self.assertIn('AddItemsUnify', [c[1] for c in self.jst.calls])

    def test_product_existence_is_required_even_when_purchase_check_disabled(self):
        self.st.set_many({'inbound_validate_purchase':'0'})
        self.add(self.b1,'NOT-IN-CATALOG',2,matched=False)
        with self.assertRaisesRegex(InboundError,'商品资料中不存在.*NOT-IN-CATALOG'):
            self.generate()
        self.assertEqual(self.jst.creates,0)
        self.assertEqual(self.st.inbound_jobs(self.sid),[])
        self.assertFalse(self.st.batches(self.sid)[0].get('inbound_job_id'))

    def test_purchase_line_does_not_prove_current_product_exists(self):
        self.add(self.b1,'A',7);del self.jst.products['A']
        with self.assertRaisesRegex(InboundError,'商品资料中不存在.*A'):self.generate()
        self.assertEqual(self.jst.creates,0)

    def test_product_network_failure_is_not_called_missing_and_does_not_create(self):
        self.add(self.b1,'A',7);self.jst.product_error=True
        with self.assertRaisesRegex(InboundError,'商品资料校验失败.*网络'):self.generate()
        self.assertEqual(self.jst.creates,0);self.assertEqual(self.st.inbound_jobs(self.sid),[])

    def test_missing_products_are_listed_and_exact_code_is_required(self):
        self.st.set_many({'inbound_validate_purchase':'0'})
        self.add(self.b1,'WRONG-1',1,matched=False);self.add(self.b2,'WRONG-2',2,matched=False)
        self.jst.item_search=lambda *a,**kw:[{'sku_id':'A','sku_code':a[1]}]
        with self.assertRaisesRegex(InboundError,'WRONG-1、WRONG-2'):self.generate()
        self.assertEqual(self.jst.creates,0)

    def test_resume_rechecks_catalog_before_any_further_mutation(self):
        self.st.set_many({'inbound_validate_purchase':'0'});self.add(self.b1,'X',5,matched=False)
        self.jst.lose_add=True
        with self.assertRaises(InboundError):self.generate()
        job=self.st.inbound_jobs(self.sid)[0];del self.jst.products['X'];n=len(self.jst.calls)
        with self.assertRaisesRegex(InboundError,'商品资料中不存在'):self.svc.resume(job['id'])
        self.assertTrue(all(c[1] in ('LoadDataToJSON','LoadWarehouse') for c in self.jst.calls[n:]))
        self.jst.products['X']={'sku_id':'X'}
        self.assertEqual(self.svc.resume(job['id'])['status'],'complete');self.assertEqual(self.jst.creates,1)
        self.assertEqual(self.actual(job),{'X':Decimal(5)})

    def test_catalog_check_uses_stored_owner_auth_and_does_not_run_on_scan_preview(self):
        self.add(self.b1,'A',7);self.svc.preview(self.sid)
        self.assertEqual(self.jst.product_queries,[])
        self.generate()
        self.assertTrue(self.jst.product_queries)
        self.assertTrue(all(q==('sku_id','A','OWNER','WAREHOUSE') for q in self.jst.product_queries))


class InboundWireTests(unittest.TestCase):
    def test_real_http_form_preserves_json_string_arguments_for_all_write_paths(self):
        from jst import JstClient
        for strict in (True,False):
            with self.subTest(strict=strict):
                fake=FakeJst();client=JstClient();client.uid='USER';client.coid='WAREHOUSE'
                client._viewstate=lambda url:('TEST_VIEWSTATE','TEST_GENERATOR')
                client.item_search=fake.item_search
                sent=[]
                def request(url,body=None,**kw):
                    form=parse_qs(body);p=json.loads(form['__CALLBACKPARAM'][0]);sent.append(p)
                    self.assertEqual(form['owner_co_id'],['OWNER']);self.assertEqual(form['authorize_co_id'],['WAREHOUSE'])
                    args={'fields':{'owner_co_id':'OWNER','authorize_co_id':'WAREHOUSE'}}
                    if 'CallControl' in p:args['call_control']=p['CallControl']
                    rv=fake.callback(url,p['Method'],p['Args'],**args)
                    return json.dumps({'IsSuccess':True,'ReturnValue':json.dumps(rv)})
                client._request=request
                api=PurchaseInAPI(client,'OWNER','WAREHOUSE');io_id=api.create('123')
                api.details=lambda io:copy.deepcopy(fake.docs[io]['rows'])
                snap={'po_id':'123','owner_co_id':'OWNER','auth_co_id':'WAREHOUSE','warehouse':{'id':'3','name':'进货仓'},
                      'validate_purchase_details':strict,'items':[{'sku_id':'A','qty':'7'},{'sku_id':'B','qty':'2'}]}
                api.fill(io_id,snap)
                self.assertEqual({r['sku_id']:quantity(r['qty']) for r in fake.docs[io_id]['rows']},{'A':Decimal(7),'B':Decimal(2)})
                save=next(p for p in sent if p['Method']=='Save');self.assertIsInstance(save['Args'][0],str)
                self.assertNotIn('CallControl',save);self.assertEqual(json.loads(save['Args'][0])['warehouse'],'进货仓')
                if strict:
                    check=next(p for p in sent if p['Method']=='CheckAndChoose');add=next(p for p in sent if p['Method']=='AddItemsUnify')
                    self.assertIsInstance(check['Args'][0],str);self.assertIsInstance(add['Args'][1],str)
                    self.assertEqual(json.loads(check['Args'][0]),json.loads(add['Args'][1]))
                    self.assertEqual(add['Args'][2:],['true','false'])
                else:
                    add=next(p for p in sent if p['Method']=='AddItems');self.assertEqual(add['Args'][1],'A,B')
                    updates=[p['Args'] for p in sent if p['Method']=='UpQtyBatch'];self.assertEqual(updates,[['7','1'],['2','2']])
                self.assertNotIn('ConfirmBatch',[p['Method'] for p in sent])


if __name__ == '__main__':
    unittest.main(verbosity=2)

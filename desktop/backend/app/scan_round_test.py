# -*- coding: utf-8 -*-
"""4.1.8：扫码入账后接口带回本轮数量（receiving_round），商品列表即时刷新；
扫码超收不另行提醒（接口不再给 over_receipt）。

离线运行，不碰真实聚水潭：python scan_round_test.py
"""
import json
import unittest
import uuid

from concurrency_fixes_test import Base


class ScanRoundTests(Base):
    def setUp(self):
        super().setUp()
        # 起始基线：A 采购 10、ERP 已入 0 → 本轮应收 10
        st = self.s.STORE
        items = json.loads(st.get_session(self.sid)['items_json'])
        for it in items:
            it.update(qty=10, qty_known=True, in_qty=0, in_qty_known=True)
        st.db.execute("UPDATE session SET round_baseline_json=?, items_json=? WHERE id=?",
                      (json.dumps(items), json.dumps(items), self.sid))
        st.db.commit()

    def plan_recv(self, sku='A'):
        row = next(r for r in self.s.STORE.receiving_round(self.sid)['rows'] if r['sku_id'] == sku)
        return row['plan'], row['recv']

    def test_scan_returns_receiving_round(self):
        r = self.scan(qty=1, client_op_id=uuid.uuid4().hex)
        self.assertTrue(r['ok'], r)
        self.assertTrue(r.get('receiving_round', {}).get('rows'), '扫码后要带回本轮数量，商品列表才会即时刷新')

    def test_scan_over_plan_has_no_over_receipt(self):
        plan, recv = self.plan_recv()
        self.assertIsNotNone(plan)
        r = self.scan(qty=int(plan - recv) + 2, client_op_id=uuid.uuid4().hex)
        self.assertTrue(r['ok'], r)
        self.assertNotIn('over_receipt', r)
        self.assertEqual(self.plan_recv()[1], plan + 2, '超收照常入账，不截断')

    def test_replay_does_not_double_count(self):
        plan, recv = self.plan_recv()
        opid = uuid.uuid4().hex
        self.scan(qty=int(plan - recv) + 1, client_op_id=opid)
        self.scan(qty=int(plan - recv) + 1, client_op_id=opid)
        self.assertEqual(self.plan_recv()[1], plan + 1, '同一操作编号重试不能重复入账')


if __name__ == '__main__':
    unittest.main()

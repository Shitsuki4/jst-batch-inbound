# -*- coding: utf-8 -*-
"""4.1.8：扫码入账后本轮实收超过应收时，接口要给出 over_receipt，前端据此提示「超收」。

离线运行，不碰真实聚水潭：python scan_over_test.py
"""
import json
import unittest
import uuid

from concurrency_fixes_test import Base


class OverReceiptTests(Base):
    def setUp(self):
        super().setUp()
        # 起始基线：A 采购 10、ERP 已入 0 → 本轮应收 10（旧记录没有基线，应收待核实，不提示超收）
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

    def test_scan_within_plan_has_no_warning(self):
        r = self.scan(qty=1, client_op_id=uuid.uuid4().hex)
        self.assertTrue(r['ok'], r)
        self.assertIsNone(r.get('over_receipt'))
        self.assertTrue(r.get('receiving_round', {}).get('rows'), '扫码后要带回本轮数量，商品列表才会即时刷新')

    def test_scan_over_plan_reports_over_receipt(self):
        plan, recv = self.plan_recv()
        self.assertIsNotNone(plan)
        need = int(plan - recv)
        r = self.scan(qty=need + 2, client_op_id=uuid.uuid4().hex)
        self.assertTrue(r['ok'], r)
        over = r.get('over_receipt')
        self.assertIsNotNone(over, r)
        self.assertEqual(over['sku_id'], 'A')
        self.assertEqual(over['over'], 2)
        self.assertEqual(over['plan'], plan)
        # 再扫一件仍然超收，继续提示
        r = self.scan(qty=1, client_op_id=uuid.uuid4().hex)
        self.assertEqual(r['over_receipt']['over'], 3)

    def test_replay_returns_same_over_receipt(self):
        plan, recv = self.plan_recv()
        opid = uuid.uuid4().hex
        first = self.scan(qty=int(plan - recv) + 1, client_op_id=opid)
        again = self.scan(qty=int(plan - recv) + 1, client_op_id=opid)
        self.assertEqual(first.get('over_receipt'), again.get('over_receipt'))
        self.assertEqual(self.plan_recv()[1], plan + 1, '同一操作编号重试不能重复入账')


if __name__ == '__main__':
    unittest.main()

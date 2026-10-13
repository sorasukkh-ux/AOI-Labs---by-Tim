"""Business rules (app/models/rules). Run all tests with:  python -m unittest discover -s tests -v"""
import unittest
from datetime import datetime

from app.models.rules import charges, receive_time, status_flow as flow, yield_rule

DEFAULT = {"rice": 0.5152, "bran": 0.1000, "broken": 0.0606, "husk": 0.2200}
PERIODS = ["10:00", "13:00", "16:00", "18:00"]


def plan(orders, now, per_kg=0.273, gap=1):
    return receive_time.schedule(orders, now, per_kg, gap, "09:00", "18:00", PERIODS)


class YieldRule(unittest.TestCase):
    def test_default_ratios_are_valid(self):
        result = yield_rule.check(DEFAULT)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 0.8958)

    def test_exactly_100_percent_is_allowed(self):
        self.assertTrue(yield_rule.check({"rice": 0.65, "bran": 0.08, "broken": 0.05, "husk": 0.22})["ok"])

    def test_over_100_percent_is_rejected(self):
        result = yield_rule.check({"rice": 0.65, "bran": 0.08, "broken": 0.05, "husk": 0.2201})
        self.assertTrue(result["in_range"])
        self.assertFalse(result["ok"])

    def test_out_of_range_or_blank_is_rejected(self):
        for bad in (1.2, -0.1, float("nan"), None, "0.5"):
            self.assertFalse(yield_rule.check(dict(DEFAULT, rice=bad))["in_range"], bad)

    def test_outputs_match_the_mill_test(self):
        # 33 kg of paddy gave 17 kg of rice and 2 kg of broken rice
        self.assertEqual(yield_rule.outputs(33, DEFAULT), {"rice": 17.0, "bran": 3.3, "broken": 2.0, "husk": 7.26})


class Charges(unittest.TestCase):
    def calc(self, rice, bran, broken, take_bran, take_broken, km=None, pct=10, rate=0):
        return charges.milling_charges(rice, bran, broken, take_bran, take_broken, km, 50, 12, rate, 12.00, 12.30, pct)

    def test_fee_and_take_home_with_promotion(self):
        c = self.calc(1701, 386, 165, 30, 15)
        self.assertEqual((c["units"], c["fee"]), (35, 420.0))
        self.assertEqual(c["bran"]["amount"], 324.0)            # 30 kg x 12.00 x 90%
        self.assertEqual(c["broken"]["amount"], 166.05)         # 15 kg x 12.30 x 90%
        self.assertEqual(c["total"], 910.05)

    def test_fee_rounds_up_to_full_units(self):
        for rice, units in ((0, 0), (1, 1), (50, 1), (50.01, 2), (100, 2), (120, 3)):
            self.assertEqual(self.calc(rice, 0, 0, 0, 0)["units"], units, rice)

    def test_discount_only_up_to_own_output(self):
        c = self.calc(100, 10, 0, 20, 0)
        self.assertEqual((c["bran"]["own_kg"], c["bran"]["extra_kg"]), (10, 10))
        self.assertEqual(c["bran"]["amount"], 228.0)            # 10 x 12 x 90% + 10 x 12

    def test_no_promotion_means_normal_price(self):
        self.assertEqual(self.calc(100, 10, 0, 5, 0, pct=0)["bran"]["amount"], 60.0)

    def test_delivery_fee_only_for_delivery_orders(self):
        self.assertEqual(self.calc(100, 0, 0, 0, 0, km=None, rate=5)["delivery"]["amount"], 0.0)
        c = self.calc(100, 0, 0, 0, 0, km=6, rate=5)
        self.assertEqual((c["delivery"]["amount"], c["total"]), (30.0, 54.0))
        self.assertEqual(self.calc(100, 0, 0, 0, 0, km=6, rate=0)["delivery"]["amount"], 0.0)


class StatusFlow(unittest.TestCase):
    def order(self, status, order_type="T", flag="N"):
        return {"MILL_STATUS": status, "ORDER_TYPE": order_type, "CANCEL_REQ_FLG": flag}

    def test_allowed_changes(self):
        # Tim's take-away diagram (2026-10-08)
        self.assertEqual(flow.allowed_next(self.order("Q")), ["I", "H", "CC"])
        self.assertEqual(flow.allowed_next(self.order("I")), ["Q", "H", "F", "CC"])
        self.assertEqual(flow.allowed_next(self.order("H")), ["Q", "I", "CC"])
        self.assertEqual(flow.allowed_next(self.order("F", "T")), ["C", "CC"])
        self.assertEqual(flow.allowed_next(self.order("F", "D")), ["D", "CC"])
        self.assertEqual(flow.allowed_next(self.order("D", "D")), ["C"])
        self.assertEqual(flow.allowed_next(self.order("C")), [])
        self.assertEqual(flow.allowed_next(self.order("CC")), [])

    def test_pending_request_blocks_finished(self):
        self.assertTrue(flow.blocked_by_pending_request(self.order("I", flag="P"), "F"))
        self.assertFalse(flow.blocked_by_pending_request(self.order("I", flag="P"), "H"))
        self.assertFalse(flow.blocked_by_pending_request(self.order("I", flag="R"), "F"))

    def test_cancel_request_only_in_queue_or_in_process(self):
        self.assertTrue(flow.can_request_cancel(self.order("Q")))
        self.assertTrue(flow.can_request_cancel(self.order("I")))
        self.assertFalse(flow.can_request_cancel(self.order("I", flag="P")))
        for status in ("H", "F", "D", "C", "CC"):
            self.assertFalse(flow.can_request_cancel(self.order(status)), status)


class ReceiveTime(unittest.TestCase):
    def test_small_order_same_day(self):
        result = plan([{"ORD_ID": "A", "REGIST_PADDY": 33}], datetime(2026, 10, 7, 9, 0))["A"]
        self.assertEqual(result["finish"], datetime(2026, 10, 7, 9, 9, 0, 540000))       # 33 x 0.273 = 9.009 minutes
        self.assertEqual(result["receive"], datetime(2026, 10, 7, 10, 0))

    def test_order_that_cannot_finish_today_starts_next_morning(self):
        result = plan([{"ORD_ID": "A", "REGIST_PADDY": 33}], datetime(2026, 10, 7, 17, 55))["A"]
        self.assertEqual(result["start"], datetime(2026, 10, 8, 9, 0))
        self.assertEqual(result["receive"], datetime(2026, 10, 8, 10, 0))

    def test_registered_after_closing(self):
        result = plan([{"ORD_ID": "A", "REGIST_PADDY": 33}], datetime(2026, 10, 7, 20, 0))["A"]
        self.assertEqual(result["start"], datetime(2026, 10, 8, 9, 0))

    def test_order_longer_than_a_working_day_continues_next_day(self):
        # 3,000 kg x 0.273 = 819 minutes; a working day is 540 minutes
        result = plan([{"ORD_ID": "A", "REGIST_PADDY": 3000}], datetime(2026, 10, 7, 9, 0))["A"]
        self.assertEqual(result["finish"], datetime(2026, 10, 8, 13, 39))
        self.assertEqual(result["receive"], datetime(2026, 10, 8, 16, 0))

    def test_finish_after_the_last_period_moves_to_next_day(self):
        self.assertEqual(receive_time.round_up_to_period(datetime(2026, 10, 7, 18, 0), PERIODS), datetime(2026, 10, 7, 18, 0))
        self.assertEqual(receive_time.round_up_to_period(datetime(2026, 10, 7, 18, 0, 1), PERIODS), datetime(2026, 10, 8, 10, 0))

    def test_orders_run_one_after_another_with_a_gap(self):
        orders = [{"ORD_ID": "A", "REGIST_PADDY": 100}, {"ORD_ID": "B", "REGIST_PADDY": 100}]
        result = plan(orders, datetime(2026, 10, 7, 9, 0), per_kg=0.3, gap=1)
        self.assertEqual(result["A"]["finish"], datetime(2026, 10, 7, 9, 30))
        self.assertEqual(result["B"]["start"], datetime(2026, 10, 7, 9, 31))
        self.assertEqual(result["B"]["finish"], datetime(2026, 10, 7, 10, 1))
        self.assertEqual(result["B"]["receive"], datetime(2026, 10, 7, 13, 0))

    def test_in_process_first_then_priority_then_registration(self):
        t = datetime(2026, 10, 7, 8, 0)
        orders = [
            {"ORD_ID": "low", "MILL_STATUS": "Q", "MILL_PRIORITY": "L", "REGIST_DATE": t.replace(minute=1)},
            {"ORD_ID": "urgent", "MILL_STATUS": "Q", "MILL_PRIORITY": "U", "REGIST_DATE": t.replace(minute=5)},
            {"ORD_ID": "running", "MILL_STATUS": "I", "MILL_PRIORITY": "L", "REGIST_DATE": t.replace(minute=9)},
            {"ORD_ID": "medium2", "MILL_STATUS": "Q", "MILL_PRIORITY": "M", "REGIST_DATE": t.replace(minute=4)},
            {"ORD_ID": "medium1", "MILL_STATUS": "Q", "MILL_PRIORITY": "M", "REGIST_DATE": t.replace(minute=3)},
            {"ORD_ID": "held", "MILL_STATUS": "H", "MILL_PRIORITY": "U", "REGIST_DATE": t},
        ]
        self.assertEqual([o["ORD_ID"] for o in receive_time.queue_sequence(orders)],
                         ["running", "urgent", "medium1", "medium2", "low"])


if __name__ == "__main__":
    unittest.main()

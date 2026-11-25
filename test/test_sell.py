"""出售拖动、金币增量与成交确认。"""

import unittest
from unittest.mock import Mock, call, patch

from game.input import InputUncertain
from game.slots import SLOTS
from screens.sell import DRAG, TARGET, sell, sell_many
from service import Session


class SellTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(1, "framepool")
        self.session.preparation = self.reader = Mock()
        self.session.controller = self.controller = Mock()
        self.controller.post_swipe.return_value.wait.return_value.succeeded = True
        self.session.preparation_frame = Mock(side_effect=["before", "after", "later"])
        self.session.preparation_scene = Mock(return_value={"result": "success", "screen": "备战", "shop": "closed"})
        self.session.save_image = Mock(side_effect=lambda image: {"screenshot": f"{image}.png"})
        self.reader.card.return_value = {"name": "黄泉"}
        self.reader.integer.side_effect = [76, 80]
        self.reader.empty.return_value = True
        self.sleep = patch("screens.sell.sleep").start()
        self.addCleanup(patch.stopall)

    def test_success_verifies_empty_source_and_gold_increase(self):
        result = sell(self.session, "bench_8")
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["gold"], 80)
        self.assertEqual(result["slots"], {"bench_8": None})
        self.assertEqual(result["screenshot"], "after.png")
        self.assertEqual(result["evidence"], {"name": "黄泉", "price": 4, "gold_before": 76,
                                              "gold_after": 80, "source_empty": True})
        self.controller.post_swipe.assert_called_once_with(*SLOTS["bench_8"], *TARGET, DRAG)
        self.reader.empty.assert_called_once_with("after", "bench_8")
        self.session.save_image.assert_called_once_with("after")

    def test_source_empty_or_unknown_stops_before_drag(self):
        for card in (None, {"unknown": True}):
            with self.subTest(card=card):
                self.setUp()
                self.reader.card.return_value = card
                self.assertEqual(sell(self.session, "bench_8")["result"], "failed")
                self.controller.post_swipe.assert_not_called()
                self.reader.integer.assert_not_called()

    def test_unread_gold_stops_before_drag(self):
        self.reader.integer.side_effect = [None]
        self.assertEqual(sell(self.session, "bench_8")["result"], "uncertain")
        self.controller.post_swipe.assert_not_called()

    def test_open_shop_or_choice_stops_before_reading_source(self):
        for scene in ({"screen": "备战", "shop": "open"}, {"screen": "武装箱选择"}):
            with self.subTest(scene=scene):
                self.session.preparation_frame.side_effect = ["before"]
                self.session.preparation_scene.return_value = scene
                self.assertEqual(sell(self.session, "bench_8")["result"], "failed")
        self.reader.card.assert_not_called()
        self.controller.post_swipe.assert_not_called()

    def test_failed_drag_is_uncertain_without_reading_back(self):
        self.controller.post_swipe.return_value.wait.return_value.succeeded = False
        result = sell(self.session, "bench_8")
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.controller.post_swipe.assert_called_once()
        self.reader.empty.assert_not_called()

    def test_delayed_gold_is_observed_without_dragging_again(self):
        self.reader.integer.side_effect = [76, 76, 80]
        self.assertEqual(sell(self.session, "bench_8")["result"], "success")
        self.assertEqual(self.reader.empty.call_args_list, [call("after", "bench_8"), call("later", "bench_8")])
        self.controller.post_swipe.assert_called_once()

    def test_unchanged_gold_or_occupied_source_stays_uncertain(self):
        for after, empty in ((76, True), (80, False)):
            with self.subTest(after=after, empty=empty):
                self.setUp()
                self.reader.integer.side_effect = [76, after]
                self.reader.empty.return_value = empty
                with patch("screens.sell.monotonic", side_effect=[0, 4]):
                    result = sell(self.session, "bench_8")
                self.assertEqual(result["result"], "uncertain")
                self.assertEqual(result["evidence"]["gold_after"], after)
                self.controller.post_swipe.assert_called_once()

    def test_choice_after_drag_returns_scene_without_more_inputs(self):
        self.session.preparation_scene.side_effect = [
            {"screen": "备战", "shop": "closed"}, {"screen": "武装箱选择"}]
        result = sell(self.session, "bench_8")
        self.assertEqual(result["screen"], "武装箱选择")
        self.assertEqual(result["result"], "uncertain")
        self.reader.empty.assert_not_called()
        self.controller.post_swipe.assert_called_once()

    def test_save_failure_after_drag_is_uncertain(self):
        self.session.save_image.side_effect = OSError("磁盘错误")
        with self.assertRaisesRegex(InputUncertain, "OSError: 磁盘错误"):
            sell(self.session, "bench_8")

    def test_session_schedules_collection_only_after_confirmed_sale(self):
        with patch("screens.sell.sell_many", return_value={"result": "success"}), patch("flow.rewards.Collector") as worker:
            result = self.session.sell_character(["bench_8"])
        worker.assert_called_once_with(self.session)
        worker.return_value.request.assert_called_once()
        self.assertTrue(result["collection"]["pending"])
        self.session.collector = None
        with patch("screens.sell.sell_many", return_value={"result": "uncertain"}), patch("flow.rewards.Collector") as worker:
            self.session.sell_character(["bench_8"])
        worker.assert_not_called()

    def test_batch_sell_sums_prices_and_reports_each_slot(self):
        sales = [{"result": "success", "screenshot": "a.png", "gold": 80,
                  "evidence": {"name": "黄泉", "price": 4}},
                 {"result": "success", "screenshot": "b.png", "gold": 84,
                  "evidence": {"name": "流萤", "price": 1}}]
        with patch("screens.sell.sell", side_effect=sales):
            result = sell_many(self.session, ["bench_8", "bench_9"])
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["gold_gained"], 5)
        self.assertIsNone(result["from"])
        self.assertEqual(result["slots"], {"bench_8": None, "bench_9": None})
        self.assertEqual(result["sold"], [{"from": "bench_8", "name": "黄泉", "price": 4},
                                          {"from": "bench_9", "name": "流萤", "price": 1}])
        self.assertEqual(result["screenshot"], "b.png")

    def test_batch_sell_stops_at_first_failure_and_keeps_finished_slots(self):
        sales = [{"result": "success", "screenshot": "a.png", "gold": 80,
                  "evidence": {"name": "黄泉", "price": 4}},
                 {"result": "uncertain", "screenshot": "b.png", "message": "售卖结果待确认"}]
        with patch("screens.sell.sell", side_effect=sales) as single:
            result = sell_many(self.session, ["bench_8", "bench_9", "bench_7"])
        self.assertEqual(single.call_count, 2)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["gold_gained"], 4)
        self.assertEqual(result["slots"], {"bench_8": None})
        self.assertIn("1/3", result["message"])

    def test_empty_batch_stops_without_dragging(self):
        result = sell_many(self.session, [])
        self.assertEqual(result["result"], "failed")
        self.controller.post_swipe.assert_not_called()

    def test_single_source_uses_the_same_result_shape(self):
        with patch("screens.sell.sell", return_value={"result": "success", "evidence": {"price": 2}}) as single:
            result = sell_many(self.session, ["bench_8"])
        single.assert_called_once_with(self.session, "bench_8")
        self.assertNotIn("sold", result)


if __name__ == "__main__":
    unittest.main()


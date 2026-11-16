"""出售拖动、售价读取与成交确认。"""

import unittest
from unittest.mock import Mock, call, patch

from game.input import InputUncertain
from game.slots import SLOTS
from screens.sell import STAGING, TARGET, sell
from service import Session


class SellTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(1, "framepool")
        self.session.preparation = self.reader = Mock()
        self.session.controller = self.controller = Mock()
        for method in ("post_touch_down", "post_touch_move", "post_touch_up"):
            getattr(self.controller, method).return_value.wait.return_value.succeeded = True
        self.session.preparation_frame = Mock(side_effect=["before", "held", "after", "later"])
        self.session.preparation_scene = Mock(return_value={"result": "success", "screen": "备战", "shop": "closed"})
        self.session.save_image = Mock(side_effect=lambda image: {"screenshot": f"{image}.png"})
        self.reader.card.return_value = {"name": "黄泉"}
        self.reader.integer.side_effect = [76, 4, 80]
        self.reader.score.return_value = 0.95
        self.reader.empty.return_value = True
        self.sleep = patch("screens.sell.sleep").start()
        self.addCleanup(patch.stopall)

    def test_success_verifies_empty_source_and_exact_gold_change(self):
        result = sell(self.session, "bench_8")
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["gold"], 80)
        self.assertEqual(result["slots"], {"bench_8": None})
        self.assertEqual(result["screenshot"], "after.png")
        self.assertEqual(result["evidence"], {"name": "黄泉", "price": 4, "gold_before": 76,
                                              "gold_after": 80, "source_empty": True})
        self.controller.post_touch_down.assert_called_once_with(*SLOTS["bench_8"])
        self.assertEqual(self.controller.post_touch_move.call_args_list, [call(*STAGING), call(*TARGET)])
        self.controller.post_touch_up.assert_called_once()
        self.reader.empty.assert_called_once_with("after", "bench_8")
        self.session.save_image.assert_called_once_with("after")

    def test_source_empty_or_unknown_stops_before_drag(self):
        for card in (None, {"unknown": True}):
            with self.subTest(card=card):
                self.setUp()
                self.reader.card.return_value = card
                self.assertEqual(sell(self.session, "bench_8")["result"], "failed")
                self.controller.post_touch_down.assert_not_called()
                self.reader.integer.assert_not_called()

    def test_unread_gold_stops_before_drag(self):
        self.reader.integer.side_effect = [None]
        self.assertEqual(sell(self.session, "bench_8")["result"], "uncertain")
        self.controller.post_touch_down.assert_not_called()

    def test_open_shop_or_choice_stops_before_reading_source(self):
        for scene in ({"screen": "备战", "shop": "open"}, {"screen": "武装箱选择"}):
            with self.subTest(scene=scene):
                self.session.preparation_frame.side_effect = ["before"]
                self.session.preparation_scene.return_value = scene
                self.assertEqual(sell(self.session, "bench_8")["result"], "failed")
        self.reader.card.assert_not_called()
        self.controller.post_touch_down.assert_not_called()

    def test_unread_price_returns_card_to_source_and_releases(self):
        self.reader.integer.side_effect = [76, None]
        result = sell(self.session, "bench_8")
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(self.controller.post_touch_move.call_args_list,
                         [call(*STAGING), call(*SLOTS["bench_8"])])
        self.controller.post_touch_up.assert_called_once()
        self.reader.empty.assert_not_called()

    def test_missing_sale_label_skips_price_and_returns_to_source(self):
        self.reader.score.return_value = 0.5
        self.reader.integer.side_effect = [76]
        self.assertEqual(sell(self.session, "bench_8")["result"], "uncertain")
        self.assertEqual(self.reader.integer.call_count, 1)
        self.assertEqual(self.controller.post_touch_move.call_args_list,
                         [call(*STAGING), call(*SLOTS["bench_8"])])

    def test_failed_mouse_down_still_releases_once(self):
        self.controller.post_touch_down.return_value.wait.return_value.succeeded = False
        result = sell(self.session, "bench_8")
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.controller.post_touch_up.assert_called_once()
        self.controller.post_touch_move.assert_not_called()

    def test_release_failure_is_reported_without_repeating_input(self):
        self.controller.post_touch_up.return_value.wait.return_value.succeeded = False
        result = sell(self.session, "bench_8")
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("释放", result["message"])
        self.controller.post_touch_up.assert_called_once()
        self.reader.empty.assert_not_called()

    def test_delayed_gold_is_observed_without_dragging_again(self):
        self.reader.integer.side_effect = [76, 4, 76, 80]
        self.assertEqual(sell(self.session, "bench_8")["result"], "success")
        self.assertEqual(self.reader.empty.call_args_list, [call("after", "bench_8"), call("later", "bench_8")])
        self.controller.post_touch_down.assert_called_once()

    def test_wrong_gold_change_or_occupied_source_stays_uncertain(self):
        for empty in (True, False):
            with self.subTest(empty=empty):
                self.setUp()
                self.reader.empty.return_value = empty
                self.reader.integer.side_effect = [76, 4, 81]
                with patch("screens.sell.monotonic", side_effect=[0, 4]):
                    result = sell(self.session, "bench_8")
                self.assertEqual(result["result"], "uncertain")
                self.assertEqual(result["evidence"]["gold_after"], 81)
                self.controller.post_touch_down.assert_called_once()

    def test_choice_after_release_returns_scene_without_more_inputs(self):
        self.session.preparation_scene.side_effect = [
            {"screen": "备战", "shop": "closed"}, {"screen": "武装箱选择"}]
        result = sell(self.session, "bench_8")
        self.assertEqual(result["screen"], "武装箱选择")
        self.assertEqual(result["result"], "uncertain")
        self.reader.empty.assert_not_called()
        self.controller.post_touch_up.assert_called_once()

    def test_save_failure_after_drag_is_uncertain(self):
        self.session.save_image.side_effect = OSError("磁盘错误")
        with self.assertRaisesRegex(InputUncertain, "OSError: 磁盘错误"):
            sell(self.session, "bench_8")

    def test_session_schedules_collection_only_after_confirmed_sale(self):
        with patch("screens.sell.sell", return_value={"result": "success"}), patch("flow.rewards.Collector") as worker:
            result = self.session.sell_character("bench_8")
        worker.assert_called_once_with(self.session)
        worker.return_value.request.assert_called_once()
        self.assertTrue(result["collection"]["pending"])
        self.session.collector = None
        with patch("screens.sell.sell", return_value={"result": "uncertain"}), patch("flow.rewards.Collector") as worker:
            self.session.sell_character("bench_8")
        worker.assert_not_called()

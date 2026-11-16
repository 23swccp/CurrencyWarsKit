"""免费次数、付费金额与单次刷新验证。"""

import unittest
from unittest.mock import Mock, patch

from game.input import InputUncertain
from screens.shop import FREE_COUNT, REFRESH_COST, REFRESH_LABEL, REFRESH, observe, read_refresh, refresh
from service import Session


def state(mode, value, gold=40):
    return {"refresh_mode": mode, "refresh_cost": 0 if mode == "free" else value,
            "free_refreshes": value if mode == "free" else 0, "gold": gold}


class RefreshReadingTests(unittest.TestCase):
    def setUp(self):
        self.reader = Mock()
        self.image = object()

    def test_free_one_is_a_count_and_costs_zero(self):
        self.reader.text.return_value = "免费 刷新"
        self.reader.integer.side_effect = [1, 40]
        result = read_refresh(self.image, self.reader)
        self.assertEqual(result, state("free", 1))
        self.reader.text.assert_called_once_with(self.image, "刷新按钮", REFRESH_LABEL)
        self.assertEqual(self.reader.integer.call_args_list[0].args, (self.image, "免费次数", FREE_COUNT))

    def test_paid_two_is_a_price_and_has_zero_available_free_refreshes(self):
        self.reader.text.return_value = "刷新"
        self.reader.integer.side_effect = [2, 7]
        self.assertEqual(read_refresh(self.image, self.reader), state("paid", 2, 7))
        self.assertEqual(self.reader.integer.call_args_list[0].args, (self.image, "刷新费用", REFRESH_COST))

    def test_unknown_title_does_not_infer_price_from_the_number(self):
        self.reader.text.return_value = "1"
        self.reader.integer.return_value = 40
        self.assertEqual(read_refresh(self.image, self.reader),
                         {"refresh_mode": "unknown", "refresh_cost": None, "free_refreshes": None, "gold": 40})
        self.reader.integer.assert_called_once()

    def test_missing_free_count_stays_unknown(self):
        self.reader.text.return_value = "免费刷新"
        self.reader.integer.side_effect = [None, 40]
        self.assertEqual(read_refresh(self.image, self.reader), state("free", None))


class RefreshActionTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(1, "framepool")
        self.session.preparation = Mock()
        self.session.controller = Mock()
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True
        self.session.preparation_frame = Mock(side_effect=["before", "after", "later"])
        self.session.preparation_scene = Mock(return_value={"result": "success", "screen": "备战", "shop": "open"})
        self.session.save_image = Mock(side_effect=lambda image: {"screenshot": f"{image}.png"})
        self.read = patch("screens.shop.read_refresh").start()
        patch("screens.shop.sleep").start()
        self.addCleanup(patch.stopall)

    def test_free_refresh_decrements_once_without_charging_gold(self):
        self.read.side_effect = [state("free", 2), state("free", 1)]
        result = refresh(self.session)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["free_refreshes"], 1)
        self.assertEqual(result["gold"], 40)
        self.assertEqual(result["evidence"], {"mode_used": "free", "cost": 0, "gold_before": 40,
                                              "free_refreshes_before": 2})
        self.session.controller.post_click.assert_called_once_with(*REFRESH)
        self.session.save_image.assert_called_once_with("after")

    def test_last_free_refresh_stops_after_paid_button_appears(self):
        self.read.side_effect = [state("free", 1), state("paid", 2)]
        result = refresh(self.session)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["refresh_mode"], "paid")
        self.assertEqual(result["refresh_cost"], 2)
        self.assertEqual(result["free_refreshes"], 0)
        self.assertEqual(result["evidence"]["cost"], 0)
        self.assertEqual(result["gold"], 40)
        self.session.controller.post_click.assert_called_once()
        self.assertEqual(self.read.call_count, 2)

    def test_free_refresh_works_with_zero_gold(self):
        self.read.side_effect = [state("free", 2, 0), state("free", 1, 0)]
        self.assertEqual(refresh(self.session)["result"], "success")
        self.session.controller.post_click.assert_called_once()

    def test_paid_refresh_uses_price_before_click_and_returns_new_price(self):
        self.read.side_effect = [state("paid", 2), state("paid", 4, 38)]
        result = refresh(self.session)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["evidence"]["cost"], 2)
        self.assertEqual(result["refresh_cost"], 4)
        self.assertEqual(result["gold"], 38)
        self.session.controller.post_click.assert_called_once()

    def test_unknown_count_or_price_sends_no_input(self):
        cases = (state("free", None), state("paid", None), state("free", 1, None),
                 {"refresh_mode": "unknown", "refresh_cost": None, "free_refreshes": None, "gold": 40})
        for before in cases:
            with self.subTest(before=before):
                self.session.preparation_frame.side_effect = ["before"]
                self.read.side_effect = [before]
                self.assertEqual(refresh(self.session)["result"], "uncertain")
        self.session.controller.post_click.assert_not_called()

    def test_zero_free_count_or_insufficient_gold_sends_no_input(self):
        for before in (state("free", 0), state("paid", 2, 1)):
            with self.subTest(before=before):
                self.session.preparation_frame.side_effect = ["before"]
                self.read.side_effect = [before]
                self.assertEqual(refresh(self.session)["result"], "failed")
        self.session.controller.post_click.assert_not_called()

    def test_unexpected_charge_during_free_refresh_returns_uncertain(self):
        self.read.side_effect = [state("free", 1), state("paid", 2, 38)]
        result = refresh(self.session)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["gold"], 38)
        self.assertIn("金币变化", result["message"])
        self.session.controller.post_click.assert_called_once()

    def test_unchanged_count_times_out_without_repeating_click(self):
        self.read.side_effect = [state("free", 1), state("free", 1)]
        with patch("screens.shop.monotonic", side_effect=[0, 9]):
            result = refresh(self.session)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "after.png")
        self.session.controller.post_click.assert_called_once()

    def test_ocr_transition_is_observed_until_count_is_read(self):
        self.read.side_effect = [state("free", 2), state("free", None), state("free", 1)]
        result = refresh(self.session)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["screenshot"], "later.png")
        self.session.controller.post_click.assert_called_once()

    def test_choice_after_click_returns_new_scene_and_stops(self):
        self.read.side_effect = [state("free", 1)]
        self.session.preparation_scene.side_effect = [
            {"screen": "备战", "shop": "open"}, {"screen": "武装箱选择"}]
        result = refresh(self.session)
        self.assertEqual(result["screen"], "武装箱选择")
        self.assertEqual(result["result"], "uncertain")
        self.session.controller.post_click.assert_called_once()
        self.assertEqual(self.read.call_count, 1)

    def test_closed_shop_stops_before_reading_refresh_information(self):
        self.session.preparation_scene.return_value = {"screen": "备战", "shop": "closed"}
        self.assertEqual(refresh(self.session)["result"], "failed")
        self.read.assert_not_called()
        self.session.controller.post_click.assert_not_called()

    def test_click_failure_and_save_failure_report_uncertainty(self):
        self.read.return_value = state("free", 1)
        self.session.controller.post_click.return_value.wait.return_value.succeeded = False
        result = refresh(self.session)
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.session.preparation_frame.side_effect = ["before"]
        self.session.save_image.side_effect = OSError("磁盘错误")
        with self.assertRaisesRegex(InputUncertain, "OSError: 磁盘错误"):
            refresh(self.session)

    def test_read_only_shop_returns_same_frame_without_clicking(self):
        self.read.return_value = state("free", 1)
        result = observe(self.session)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["free_refreshes"], 1)
        self.assertEqual(result["screenshot"], "before.png")
        self.read.assert_called_once_with("before", self.session.preparation)
        self.session.controller.post_click.assert_not_called()

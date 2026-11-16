"""商店布局切换与界面分流。"""

import unittest
from unittest.mock import Mock, patch

from game.input import InputUncertain
from game.slots import SHOP_TOGGLE
from screens.shop import switch
from service import Session


def scene(shop):
    return {"result": "success", "screen": "备战", "shop": shop}


class ShopTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(1, "framepool")
        self.session.controller = Mock()
        self.session.preparation = Mock()
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True
        self.session.preparation_frame = Mock(side_effect=["before", "transition", "after"])
        self.session.preparation_scene = Mock()
        self.session.save_image = Mock(side_effect=lambda image: {"screenshot": f"{image}.png"})
        patch("screens.shop.sleep").start()
        patch("screens.shop.read_refresh", return_value={"refresh_mode": "paid", "refresh_cost": 2,
                                                        "free_refreshes": 0, "gold": 40}).start()
        self.addCleanup(patch.stopall)

    def test_open_and_close_click_once_then_confirm_target_layout(self):
        for target, before in (("open", "closed"), ("closed", "open")):
            with self.subTest(target=target):
                self.setUp()
                self.session.preparation_scene.side_effect = [scene(before), {"screen": "未知"}, scene(target)]
                result = switch(self.session, target)
                self.assertEqual(result["result"], "success")
                self.assertEqual(result["shop"], target)
                self.assertEqual(result["screenshot"], "after.png")
                self.session.controller.post_click.assert_called_once_with(*SHOP_TOGGLE)

    def test_already_in_target_layout_returns_without_input(self):
        self.session.preparation_scene.return_value = scene("open")
        result = switch(self.session, "open")
        self.assertEqual(result["result"], "success")
        self.session.controller.post_click.assert_not_called()

    def test_choice_before_switch_stops_without_input(self):
        self.session.preparation_scene.return_value = {"result": "success", "screen": "选择主角"}
        self.assertEqual(switch(self.session, "open")["result"], "failed")
        self.session.controller.post_click.assert_not_called()

    def test_choice_after_click_stops_without_esc_or_second_click(self):
        self.session.preparation_scene.side_effect = [scene("closed"), {"screen": "武装箱选择"}]
        result = switch(self.session, "open")
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screen"], "武装箱选择")
        self.session.controller.post_click.assert_called_once()
        self.session.controller.post_click_key.assert_not_called()

    def test_timeout_returns_last_frame_and_one_click(self):
        self.session.preparation_scene.side_effect = [scene("closed"), scene("closed")]
        with patch("screens.shop.monotonic", side_effect=[0, 9]):
            result = switch(self.session, "open")
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "transition.png")
        self.session.controller.post_click.assert_called_once()

    def test_click_failure_and_save_failure_are_uncertain(self):
        self.session.preparation_scene.return_value = scene("closed")
        self.session.controller.post_click.return_value.wait.return_value.succeeded = False
        self.assertEqual(switch(self.session, "open")["result"], "uncertain")
        self.session.preparation_frame.side_effect = ["before"]
        self.session.save_image.side_effect = OSError("磁盘错误")
        with self.assertRaisesRegex(InputUncertain, "OSError: 磁盘错误"):
            switch(self.session, "open")


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(1, "framepool")
        self.session.preparation = Mock()
        self.session.preparation.score.return_value = 0.2
        self.session.protagonist = Mock()
        self.session.protagonist.stage.return_value = None
        self.session.strategy = Mock()
        self.session.strategy.page.return_value = False
        self.session.supply = Mock()
        self.session.supply.page.return_value = False

    def test_armament_protagonist_and_strategy_block_normal_preparation(self):
        cases = (("armament", "武装箱选择"), ("protagonist", "选择主角"), ("strategy", "投资策略"))
        for kind, expected in cases:
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "armament":
                    self.session.preparation.score.return_value = 1
                elif kind == "protagonist":
                    self.session.protagonist.stage.return_value = "selection"
                else:
                    self.session.strategy.page.return_value = True
                with patch("screens.preparation.layout") as layout:
                    self.assertEqual(self.session.preparation_scene("frame")["screen"], expected)
                layout.assert_not_called()

    def test_entry_button_does_not_block_collection_but_open_shop_does(self):
        self.session.controller = Mock()
        self.session.protagonist.stage.return_value = "entry"
        with patch("screens.preparation.layout", return_value=scene("open")):
            self.assertEqual(self.session.preparation_scene("frame"), scene("open"))
            self.session.preparation_frame = Mock(return_value="frame")
            self.session.save_image = Mock(return_value={"screenshot": "frame.png"})
            result = self.session.collect_rewards()
        self.assertEqual(result["result"], "uncertain")
        self.session.controller.post_touch_down.assert_not_called()

    def test_unknown_page_stays_unknown(self):
        with patch("screens.preparation.layout", return_value=None):
            self.assertEqual(self.session.preparation_scene("frame")["screen"], "未知")

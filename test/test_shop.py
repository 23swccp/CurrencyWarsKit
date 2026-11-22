"""商店布局切换与界面分流。"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from game.input import InputUncertain
from game.slots import SHOP_TOGGLE
from screens.preparation import Preparation
from screens.shop import read_cards, switch
from service import ROUTES, Session


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
        self.cards = patch("screens.shop.read_cards", return_value={
            "shop_slots": {"shop_1": "白露"}, "shop_stars": {"shop_1": 1}, "shop_empty_slots": [], "shop_goods": {},
        }).start()
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
                if target == "open":
                    self.cards.assert_called_once_with("after", self.session.preparation)
                    self.assertEqual(result["shop_slots"], {"shop_1": "白露"})
                    self.assertEqual(result["shop_stars"], {"shop_1": 1})
                    self.assertEqual(result["shop_empty_slots"], [])
                    self.assertEqual(result["shop_goods"], {})
                else:
                    self.cards.assert_not_called()

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


class ShopReadingTests(unittest.TestCase):
    def test_post_purchase_frame_has_distinct_stars_and_empty_slot(self):
        fixture = Path(__file__).with_name("fixtures") / "shop" / "after-purchase.webp"
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        with Image.open(fixture) as shop:
            image[60:325, 365:1692] = np.asarray(shop.convert("RGB"))[:, :, ::-1]
        reader = Preparation()
        self.addCleanup(reader.close)
        cards = read_cards(image, reader)
        self.assertEqual(cards["shop_slots"], {"shop_1": "景元", "shop_2": "景元", "shop_3": None,
                                               "shop_4": "波提欧", "shop_5": "桂乃芬"})
        self.assertEqual(cards["shop_stars"], {"shop_1": 1, "shop_2": 2, "shop_3": None,
                                               "shop_4": 1, "shop_5": 1})
        self.assertEqual(cards["shop_empty_slots"], ["shop_3"])
        self.assertEqual(cards["shop_goods"], {})

    def test_aha_equipment_card_is_read_in_another_slot_by_its_badge(self):
        fixtures = Path(__file__).with_name("fixtures") / "shop"
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        with Image.open(fixtures / "after-purchase.webp") as shop:
            image[60:325, 365:1692] = np.asarray(shop.convert("RGB"))[:, :, ::-1]
        with Image.open(fixtures / "equipment.webp") as good:
            image[66:325, 912:1142] = np.asarray(good.convert("RGB"))[:, :, ::-1]
        reader = Preparation()
        self.addCleanup(reader.close)
        cards = read_cards(image, reader)
        self.assertEqual(cards["shop_slots"]["shop_3"], "以太钻头")
        self.assertIsNone(cards["shop_stars"]["shop_3"])
        self.assertEqual(cards["shop_empty_slots"], [])
        self.assertEqual(cards["shop_goods"], {
            "shop_3": {"kind": "equipment", "name": "以太钻头", "price": 4, "description": None,
                       "equip_target": "aha"},
        })
        image[193:235, 1044:1087] = 0
        without_badge = read_cards(image, reader)["shop_goods"]["shop_3"]
        self.assertEqual(without_badge["kind"], "equipment")
        self.assertNotIn("equip_target", without_badge)


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(1, "framepool")
        self.session.preparation = Mock()
        self.session.preparation.score.return_value = 0.2
        self.session.protagonist = Mock()
        self.session.protagonist.selection_page.return_value = False
        self.session.strategy = Mock()
        self.session.strategy.page.return_value = False
        self.session.supply = Mock()
        self.session.encounter = Mock()
        self.session.encounter.page.return_value = False
        self.session.supply.page.return_value = False

    def test_armament_protagonist_and_strategy_block_normal_preparation(self):
        cases = (("armament", "武装箱选择"), ("protagonist", "选择主角"), ("strategy", "投资策略"))
        for kind, expected in cases:
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "armament":
                    self.session.preparation.score.return_value = 1
                elif kind == "protagonist":
                    self.session.protagonist.selection_page.return_value = True
                else:
                    self.session.strategy.page.return_value = True
                with patch("flow.preparation_scene.layout") as layout:
                    self.assertEqual(self.session.preparation_scene("frame")["screen"], expected)
                layout.assert_not_called()

    def test_entry_button_does_not_block_collection_but_open_shop_does(self):
        self.session.controller = Mock()
        action = {"next_action": "protagonist-open"}
        self.session.preparation.action_button.return_value = action
        with patch("flow.preparation_scene.layout", return_value=scene("closed")):
            self.assertEqual(self.session.preparation_scene("frame"), {**scene("closed"), **action})
        with patch("flow.preparation_scene.layout", return_value=scene("open")):
            self.assertEqual(self.session.preparation_scene("frame"), scene("open"))
            self.session.preparation_frame = Mock(return_value="frame")
            self.session.save_image = Mock(return_value={"screenshot": "frame.png"})
            result = self.session.collect_rewards()
        self.assertEqual(result["result"], "uncertain")
        self.session.controller.post_touch_down.assert_not_called()

    def test_strategy_precedes_overlapping_preparation_for_all_entry_points(self):
        self.session.preparation_frame = Mock(return_value="frame")
        self.session.save_image = Mock(return_value={"screenshot": "frame.png"})
        self.session.controller = Mock()
        self.session.controller.post_screencap.return_value.wait.return_value.get.return_value = "frame"
        self.session.strategy.page.return_value = True
        self.session.strategy.read.return_value = {
            "screen": "投资策略", "needs_ai": True,
            "options": [{"name": "策略", "text": "效果", "refreshes_left": 1} for _ in range(3)],
        }
        with patch("flow.preparation_scene.layout", return_value=scene("closed")) as layout, \
                patch("flow.prepare.sweep") as sweep:
            identified = self.session.preparation_scene("frame")
            observed = ROUTES["/observe"](self.session, {})
            prepared = ROUTES["/prepare"](self.session, {})
        self.assertTrue(all(result["screen"] == "投资策略" for result in (identified, observed, prepared)))
        self.assertEqual(observed["options"], prepared["options"])
        layout.assert_not_called()
        sweep.assert_not_called()
        self.session.controller.post_click.assert_not_called()

    def test_unknown_page_stays_unknown(self):
        with patch("flow.preparation_scene.layout", return_value=None):
            self.assertEqual(self.session.preparation_scene("frame")["screen"], "未知")

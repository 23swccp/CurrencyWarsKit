"""实机帧通过服务入口回放特殊道具流程。"""

from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from ocr import ImageController
from service import ROUTES, Session


FIXTURES = Path(__file__).parent / "fixtures" / "special_items"


def frame(name):
    return np.asarray(Image.open(FIXTURES / f"{name}.webp").convert("RGB"))[:, :, ::-1].copy()


class Controller(ImageController):
    def __init__(self, initial, following):
        super().__init__(frame(initial))
        self.following = iter(following)
        self.inputs = []

    def advance(self, *args):
        self.inputs.append(args)
        self.image = frame(next(self.following))
        return True

    def swipe(self, *args):
        return self.advance(*args)

    def click(self, *args):
        return self.advance(*args)

    def touch_down(self, contact, x, y, pressure):
        self.start = (x, y)
        return True

    def touch_move(self, contact, x, y, pressure):
        return True

    def touch_up(self, contact):
        return self.advance(*self.start)


class SpecialItemsTests(TestCase):
    def session(self, initial, following):
        session = Session(1, "framepool")
        session.controller = Controller(initial, following)
        self.assertTrue(session.controller.set_screenshot_use_raw_size(True))
        self.assertTrue(session.controller.post_connection().wait().succeeded)
        session.save_image = Mock(return_value={"screenshot": "saved.png"})
        self.addCleanup(session.preparation_reader().close)
        return session

    def test_two_items_through_routes(self):
        cases = (
            ("privilege", "before-privilege", ["privilege-panel", "privilege-selected", "after-privilege"],
             2, "行星钻地弹", 2),
            ("luck", "after-privilege", ["luck-panel", "after-luck"], 4, "生命之环", 1),
        )
        for kind, initial, following, count, name, clicks in cases:
            with self.subTest(item=kind):
                session = self.session(initial, following)
                opened = ROUTES["/special-use"](session, {"item": kind, "target": "back_2"})
                self.assertEqual(opened["result"], "success", opened)
                self.assertEqual(len(opened["options"]), count)
                self.assertTrue(any(name in option["name"] for option in opened["options"]))
                observed = ROUTES["/observe"](session, {})
                self.assertEqual(observed["screen"], "特殊道具选择")
                chosen = ROUTES["/special-choose"](session, {"option": 1})
                self.assertEqual(chosen["result"], "success", chosen)
                self.assertEqual(chosen["screen"], "备战")
                self.assertTrue(chosen["inventory"])
                inventory = chosen["inventory"]
                privilege = next(item for item in inventory.values() if item.get("id") == "flash_grenade_privilege")
                self.assertGreater(privilege["score"], 0.8)
                if kind == "luck":
                    self.assertIn("ring_of_life", [candidate["id"] for candidate in inventory["bag_r5_c1"]["candidates"]])
                self.assertEqual(len(session.controller.inputs), clicks + 1)

    def test_absent_item_does_not_drag(self):
        session = self.session("after-luck", [])
        result = ROUTES["/special-use"](session, {"item": "luck", "target": "back_2"})
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(session.controller.inputs, [])

    def test_names_confirm_panel_when_title_not_recognized(self):
        session = self.session("after-privilege", ["luck-panel", "after-luck"])
        with patch("screens.special_items.panel", return_value=None):
            opened = ROUTES["/special-use"](session, {"item": "luck", "target": "back_2"})
            self.assertEqual(opened["result"], "success", opened)
            observed = ROUTES["/observe"](session, {})
            self.assertEqual(observed["screen"], "特殊道具选择")
            chosen = ROUTES["/special-choose"](session, {"option": 1})
            self.assertEqual(chosen["result"], "success", chosen)

    def test_out_of_range_option_sends_no_input(self):
        session = self.session("privilege-panel", [])
        result = ROUTES["/special-choose"](session, {"option": 4})
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(session.controller.inputs, [])

    def test_unconfirmed_selection_does_not_click_confirm(self):
        session = self.session("privilege-panel", ["privilege-panel"])
        with patch("screens.special_items.monotonic", side_effect=[0, 10]), patch("screens.special_items.sleep"):
            result = ROUTES["/special-choose"](session, {"option": 1})
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(len(session.controller.inputs), 1)

    def test_preparation_text_is_not_an_equipment_option(self):
        session = self.session("before-privilege", [])
        session.special_item = "luck"
        result = ROUTES["/observe"](session, {})
        self.assertEqual(result["screen"], "备战", result)
        self.assertEqual(session.controller.inputs, [])

    def test_front_target_does_not_require_backstage_layout(self):
        session = self.session("before-privilege", ["privilege-panel"])
        with patch("screens.special_items.backstage_size", return_value=None):
            result = ROUTES["/special-use"](session, {"item": "privilege", "target": "front_1"})
        self.assertEqual(result["result"], "success", result)
        self.assertEqual(result["target"], "front_1")

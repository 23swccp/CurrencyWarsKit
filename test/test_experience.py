"""购买经验的点击与到账确认，使用图片控制器和模拟数值。"""

from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from ocr import ImageController
from service import ROUTES, Session


def numbers(gold, current, *, level=3, required=4, cost=4):
    return {"gold": gold, "level": level, "experience": {"current": current, "required": required},
            "experience_cost": cost, "deployment": {"current": 3, "limit": level}}


class Controller(ImageController):
    def __init__(self, button=True):
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        if button:
            path = Path(__file__).parent / "fixtures" / "experience" / "purchase-button.png"
            with Image.open(path) as source:
                image[826:1017, 230:368] = np.asarray(source.convert("RGB"))[:, :, ::-1]
        super().__init__(image)
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))
        return True

    def touch_down(self, contact, x, y, pressure):
        return self.click(x, y)

    def touch_up(self, contact):
        return True


class ExperienceTests(TestCase):
    def session(self, frames, *, button=True, shop="closed"):
        session = Session(1, "framepool")
        session.controller = Controller(button)
        self.assertTrue(session.controller.set_screenshot_use_raw_size(True))
        self.assertTrue(session.controller.post_connection().wait().succeeded)
        session.preparation = Mock()
        session.preparation.numbers.side_effect = frames
        session.preparation_scene = Mock(return_value={"screen": "备战", "shop": shop})
        session.save_image = Mock(return_value={"screenshot": "last.png"})
        return session

    def test_buys_requested_experience_and_verifies_level_carry(self):
        session = self.session([
            numbers(20, 2),
            numbers(16, 6),
            numbers(16, 2, level=4, required=8),
            numbers(12, 6, level=4, required=8),
            numbers(8, 2, level=5, required=20),
        ])
        result = ROUTES["/buy-experience"](session, {"xp": 12})
        self.assertEqual(result["result"], "success", result)
        self.assertEqual((result["purchases_confirmed"], result["xp_bought"], result["gold_spent"]),
                         (3, 12, 12))
        self.assertEqual((result["level"], result["experience"], result["gold"]),
                         (5, {"current": 2, "required": 20}, 8))
        self.assertFalse(result["purchase_pending"])
        self.assertEqual(session.controller.clicks, [(297, 860)] * 3)
        self.assertEqual(session.preparation.numbers.call_count, 5)

    def test_insufficient_budget_unexpected_price_and_open_shop_send_no_click(self):
        cases = ((numbers(8, 0), "closed", "failed"),
                 (numbers(12, 0, cost=5), "closed", "uncertain"),
                 (numbers(12, None), "closed", "uncertain"),
                 (numbers(12, 0), "open", "failed"))
        for state, shop, expected in cases:
            with self.subTest(state=state, shop=shop):
                session = self.session([state], shop=shop)
                result = ROUTES["/buy-experience"](session, {"xp": 12})
                self.assertEqual(result["result"], expected, result)
                self.assertEqual(result["xp_bought"], 0)
                self.assertFalse(result["purchase_pending"])
                self.assertEqual(session.controller.clicks, [])

    def test_stops_after_unconfirmed_purchase_and_returns_partial_progress(self):
        unconfirmed = numbers(12, 0, level=4, required=8)
        session = self.session([numbers(20, 0), numbers(16, 0, level=4, required=8), unconfirmed])
        with patch("screens.experience.monotonic", side_effect=[0, 10, 14]):
            result = ROUTES["/buy-experience"](session, {"xp": 12})
        self.assertEqual(result["result"], "uncertain", result)
        self.assertEqual(result["screenshot"], "last.png")
        self.assertEqual(result["gold"], 12)
        self.assertEqual((result["purchases_confirmed"], result["xp_bought"], result["gold_spent"]),
                         (1, 4, 4))
        self.assertTrue(result["purchase_pending"])
        self.assertEqual(len(session.controller.clicks), 2)

    def test_pipeline_reports_missing_button_without_input(self):
        session = self.session([numbers(12, 0)] * 2, button=False)
        result = ROUTES["/buy-experience"](session, {"xp": 4})
        self.assertEqual(result["result"], "uncertain", result)
        self.assertIn("BuyExperience", result["message"])
        self.assertFalse(result["purchase_pending"])
        self.assertEqual(session.controller.clicks, [])

    def test_verified_purchase_reports_progress_if_screenshot_save_fails(self):
        session = self.session([numbers(12, 0), numbers(8, 0, level=4, required=8)])
        session.save_image.side_effect = OSError("截图写入失败")
        result = ROUTES["/buy-experience"](session, {"xp": 4})
        self.assertEqual(result["result"], "uncertain", result)
        self.assertEqual((result["purchases_confirmed"], result["purchase_pending"]), (1, False))
        self.assertIn("OSError", result["message"])

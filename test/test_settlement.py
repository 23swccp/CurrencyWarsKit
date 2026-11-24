"""整局结算：用实机保存帧经真实 Pipeline 回放，输入仅记坐标。"""

from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from ocr import ImageController
from service import ROUTES, Session


FIXTURES = Path(__file__).parent / "fixtures" / "settlement"
PAGES = ("challenge", "promotion", "summary", "damage", "home")


def frame(name):
    return np.asarray(Image.open(FIXTURES / f"{name}.png").convert("RGB"))[:, :, ::-1].copy()


class Replay(ImageController):
    def __init__(self, images):
        super().__init__(images[0])
        self.following = iter(images[1:])
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))
        self.image = next(self.following, self.image)
        return True

    def touch_down(self, contact, x, y, pressure):
        return self.click(x, y)

    def touch_up(self, contact):
        return True


class SettlementTests(TestCase):
    def session(self, *images):
        session = Session(1, "framepool")
        session.controller = Replay(images)
        self.assertTrue(session.controller.set_screenshot_use_raw_size(True))
        self.assertTrue(session.controller.post_connection().wait().succeeded)
        session.save_image = Mock(return_value={"screenshot": "saved.png"})
        self.addCleanup(session.preparation_reader().close)
        return session

    def test_three_pipelines_click_four_pages_and_read_data(self):
        session = self.session(*(frame(name) for name in PAGES))
        result = ROUTES["/settle"](session, {})
        self.assertEqual(result["result"], "success", result)
        self.assertEqual(session.controller.clicks,
                         [(960, 895), (960, 951), (960, 899), (960, 899)])
        self.assertEqual(result["steps"], ["整局结算", "伤害统计", "货币战争首页"])
        self.assertEqual(result["screen"], "货币战争首页")
        self.assertFalse(result["needs_ai"])
        self.assertEqual((result["rating"], result["rank"], result["level"], result["materials"],
                          result["points"]), ("SSS", "财富造物主3", 54, 100, 2700))
        self.assertEqual((result["health"], result["economy"], result["team_value"]), (100, 319, 79))
        self.assertEqual(result["home_level"], 54)
        receipt = ROUTES["/battle-result"](session, {})["battle"]
        self.assertEqual((receipt["outcome"], receipt["health_after"], receipt["health_after_source"]),
                         ("victory", 100, "final_summary"))
        self.assertEqual(receipt["settlement_screenshot"], "saved.png")
        self.assertEqual(receipt["next_screen"], "货币战争首页")

    def test_missing_settlement_page_does_not_click(self):
        blank = frame("challenge")
        blank[850:995, 700:1220] = 0
        session = self.session(blank)
        with patch("flow.settlement.WAIT", 300):
            result = ROUTES["/settle"](session, {})
        self.assertEqual(result["result"], "failed", result)
        self.assertEqual(result["steps"], [])
        self.assertEqual(session.controller.clicks, [])

    def test_page_that_never_arrives_is_uncertain(self):
        session = self.session(*[frame("challenge")] * 5)
        with patch("flow.settlement.WAIT", 300):
            result = ROUTES["/settle"](session, {})
        self.assertEqual(result["result"], "uncertain", result)
        self.assertIn("未能到达整局结算页面", result["message"])
        self.assertEqual(len(session.controller.clicks), 1)
        self.assertTrue(result["nodes"])

    def test_capture_error_after_click_is_uncertain(self):
        session = self.session(*(frame(name) for name in PAGES))
        with patch.object(session, "preparation_frame",
                          side_effect=[frame("challenge"), OSError("截图断开"), frame("summary")]):
            result = ROUTES["/settle"](session, {})
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("OSError: 截图断开", result["message"])
        self.assertEqual(len(session.controller.clicks), 2)
        receipt = ROUTES["/battle-result"](session, {})["battle"]
        self.assertFalse(receipt["awaiting_health"])
        session.battle.update_receipt({"screen": "备战", "node": "1-1", "health": 100})
        self.assertIsNone(session.battle.receipt()["health_after"])

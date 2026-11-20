"""用实机帧与图片控制器回放开拓商店，输入仅记坐标。"""

from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from flow.prepare import PreparationFlow
from ocr import ImageController
from recognition_debug import trace
from service import ROUTES, Session


FIXTURES = Path(__file__).parent / "fixtures" / "trail_shop"


def frame(name):
    with Image.open(FIXTURES / f"{name}.webp") as source:
        return np.asarray(source.convert("RGB"))[:, :, ::-1].copy()


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


class TrailShopTests(TestCase):
    def session(self, *images):
        session = Session(1, "framepool")
        session.controller = Replay(images)
        self.assertTrue(session.controller.set_screenshot_use_raw_size(True))
        self.assertTrue(session.controller.post_connection().wait().succeeded)
        session.save_image = Mock(return_value={"screenshot": "trail.png"})
        self.addCleanup(session.preparation_reader().close)
        return session

    def test_open_read_buy_and_close_with_real_pipeline(self):
        session = self.session(*(frame(name) for name in ("board", "paid", "received", "board")))
        opened = ROUTES["/trail-shop-open"](session, {})
        self.assertEqual(opened["result"], "success", opened)
        self.assertEqual(opened["screen"], "开拓商店")
        self.assertEqual(opened["trailblaze_power"], 111)
        expected = [("姬子·启行", 75), ("列车强化：光轨", 40), ("后台强度Ⅱ", 30), ("23", 50)]
        self.assertEqual([(item["name"], item["price"]) for item in opened["offers"]], expected)

        watcher = Mock()
        with trace(watcher):
            observed = session.observe()
        self.assertEqual(observed["offers"], opened["offers"])
        marks = watcher.mark.call_args_list
        self.assertTrue(marks)
        self.assertTrue(all(call.args[0] is marks[0].args[0] for call in marks))
        for label in ("开拓力", "开拓商品3名称", "开拓商品3价格"):
            self.assertIn(label, [call.args[2] for call in marks])
        self.assertEqual(session.controller.clicks, [(536, 291)])

        bought = ROUTES["/trail-shop-buy"](session, {"slot": 3})
        self.assertEqual(bought["result"], "success", bought)
        self.assertEqual(bought["purchase"], {"slot": 3, "name": "后台强度Ⅱ", "price": 30,
                                             "power_before": 111, "power_after": 81})
        self.assertEqual(bought["offers"][2]["name"], "幸运一击率Ⅱ")
        closed = ROUTES["/trail-shop-close"](session, {})
        self.assertEqual((closed["result"], closed["screen"]), ("success", "备战"))
        self.assertEqual(session.controller.clicks, [(536, 291), (1048, 263), (12, 12)])

    def test_insufficient_balance_and_preparation_do_not_send_input(self):
        session = self.session(frame("empty-wallet"))
        result = ROUTES["/trail-shop-buy"](session, {"slot": 1})
        self.assertEqual((result["result"], result["trailblaze_power"]), ("failed", 6))
        self.assertEqual([item["price"] for item in result["offers"]], [30, 40, 30, 50])
        self.assertTrue(all(item["affordable"] is False for item in result["offers"]))
        self.assertEqual(result["offers"][0]["name"], "后台强度Ⅱ")
        with patch("flow.prepare.sweep") as sweep:
            prepared = PreparationFlow(session).enter()
        self.assertEqual(prepared["screen"], "开拓商店")
        sweep.assert_not_called()
        self.assertEqual(session.controller.clicks, [])

    def test_purchase_requires_both_deduction_and_replacement(self):
        before = frame("paid")
        after = frame("received")
        withdrawn = before.copy()
        withdrawn[91:129, 590:652] = after[91:129, 590:652]
        replaced = after.copy()
        replaced[91:129, 590:652] = before[91:129, 590:652]
        for image, power in ((withdrawn, 81), (replaced, 111)):
            with self.subTest(power=power), patch("screens.trail_shop.monotonic", side_effect=[0, 6]):
                session = self.session(before, image)
                result = ROUTES["/trail-shop-buy"](session, {"slot": 3})
                self.assertEqual(result["result"], "uncertain", result)
                self.assertEqual(result["trailblaze_power"], power)
                self.assertEqual(session.controller.clicks, [(1048, 263)])

    def test_pipeline_checks_fresh_offer_before_click(self):
        before = frame("paid")
        changed = frame("received")
        session = self.session(changed)
        session.preparation_frame = Mock(side_effect=[before, changed])
        result = ROUTES["/trail-shop-buy"](session, {"slot": 3})
        self.assertEqual(result["result"], "uncertain", result)
        self.assertIn("TrailShopBuy", result["message"])
        self.assertEqual(session.controller.clicks, [])

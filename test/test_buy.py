"""普通商店购买：实机前后帧与失败边界。"""

from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from ocr import ImageController
from screens.buy import gold
from screens.preparation import Preparation
from service import ROUTES, Session


FIXTURES = Path(__file__).parent / "fixtures" / "shop"


def frame(name):
    return np.asarray(Image.open(FIXTURES / f"buy-{name}.webp").convert("RGB"))[:, :, ::-1].copy()


class Controller(ImageController):
    def __init__(self, after):
        super().__init__(frame("before"))
        self.after = after
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))
        self.image = self.after.copy()
        return True

    def touch_down(self, contact, x, y, pressure):
        return self.click(x, y)

    def touch_up(self, contact):
        return True


class BuyTests(TestCase):
    def test_single_digit_gold_uses_enlarged_ocr(self):
        reader = Preparation()
        self.addCleanup(reader.close)
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        patch_image = np.asarray(Image.open(FIXTURES / "gold-seven.png").convert("RGB"))[:, :, ::-1]
        image[897:945, 1618:1691] = patch_image
        self.assertEqual(gold(reader, image), 7)

    def session(self, after=None):
        session = Session(1, "framepool")
        session.controller = Controller(frame("after") if after is None else after)
        self.assertTrue(session.controller.set_screenshot_use_raw_size(True))
        self.assertTrue(session.controller.post_connection().wait().succeeded)
        session.save_image = Mock(return_value={"screenshot": "saved.png"})
        self.addCleanup(session.preparation_reader().close)
        return session

    def test_real_before_after_through_route(self):
        session = self.session()
        result = ROUTES["/buy"](session, {"slot": "shop_3"})
        self.assertEqual(result["result"], "success", result)
        self.assertEqual(session.controller.clicks, [(1027, 200)])
        evidence = result["evidence"]
        self.assertEqual((evidence["name"], evidence["price"], evidence["gold_before"], evidence["gold_after"]),
                         ("刻律德菈", 3, 20, 17))
        self.assertTrue(evidence["shop_slot_empty"])
        self.assertTrue(evidence["pipeline"]["clicked"])
        self.assertFalse(result["needs_ai"])
        self.assertIn("shop_3", result["shop_empty_slots"])

    def test_fifth_slot_purchase_through_route(self):
        def load(name):
            return np.asarray(Image.open(FIXTURES / f"fifth-{name}.webp").convert("RGB"))[:, :, ::-1].copy()

        session = self.session(after=load("after"))
        session.controller.image = load("before")
        result = ROUTES["/buy"](session, {"slot": "shop_5"})
        self.assertEqual(result["result"], "success", result)
        self.assertEqual(session.controller.clicks, [(1564, 200)])
        self.assertEqual((result["evidence"]["gold_before"], result["evidence"]["gold_after"]), (18, 16))
        self.assertTrue(result["evidence"]["shop_slot_empty"])

    def test_closed_shop_is_rejected_without_click(self):
        session = self.session()
        scene = session.preparation_scene(frame("before"))
        with patch.object(session, "preparation_scene", return_value={**scene, "shop": "closed"}):
            result = ROUTES["/buy"](session, {"slot": "shop_3"})
        self.assertEqual(result["result"], "failed", result)
        self.assertEqual(session.controller.clicks, [])

    def test_changed_card_face_is_not_clicked(self):
        session = self.session()
        blank = frame("before").copy()
        blank[90:270, 927:1127] = 0
        session.controller.image = blank
        with patch.object(session, "preparation_frame", return_value=frame("before")):
            result = ROUTES["/buy"](session, {"slot": "shop_3"})
        self.assertEqual(result["result"], "failed", result)
        self.assertFalse(result["evidence"]["pipeline"]["clicked"])
        self.assertEqual(session.controller.clicks, [])

    def test_leaving_shop_after_click_is_uncertain(self):
        session = self.session()
        scene = session.preparation_scene(frame("before"))
        with patch.object(session, "preparation_scene", side_effect=[scene, {**scene, "shop": "closed"}]):
            result = ROUTES["/buy"](session, {"slot": "shop_3"})
        self.assertEqual(result["result"], "uncertain", result)
        self.assertEqual(len(session.controller.clicks), 1)
        self.assertIsNone(result["evidence"]["gold_after"])

    def test_full_bench_and_insufficient_gold_do_not_click(self):
        for reason in ("bench", "gold"):
            with self.subTest(reason=reason):
                session = self.session()
                reader = session.preparation_reader()
                context = patch.object(reader, "empty", return_value=False) if reason == "bench" else \
                          patch("screens.buy.gold", return_value=1)
                with context:
                    result = ROUTES["/buy"](session, {"slot": "shop_3"})
                self.assertEqual(result["result"], "uncertain" if reason == "bench" else "failed")
                self.assertEqual(session.controller.clicks, [])

    def test_sold_slot_without_expected_gold_is_uncertain(self):
        session = self.session()
        with patch("screens.buy.gold", side_effect=[20, 18]), \
                patch("screens.buy.monotonic", side_effect=[0, 9]), patch("screens.buy.sleep"):
            result = ROUTES["/buy"](session, {"slot": "shop_3"})
        self.assertEqual(result["result"], "uncertain", result)
        self.assertTrue(result["evidence"]["shop_slot_empty"])
        self.assertEqual(result["evidence"]["gold_after"], 18)
        self.assertEqual(len(session.controller.clicks), 1)

    def test_empty_shop_slot_is_not_clicked(self):
        session = self.session()
        session.controller.image = frame("after")
        result = ROUTES["/buy"](session, {"slot": "shop_3"})
        self.assertEqual(result["result"], "failed")
        self.assertEqual(session.controller.clicks, [])

    def test_gold_change_without_sold_slot_is_uncertain(self):
        session = self.session(after=frame("before"))
        with patch("screens.buy.gold", side_effect=[20, 17]), \
                patch("screens.buy.purchase", return_value={"succeeded": False, "clicked": True, "nodes": []}) as send, \
                patch("screens.buy.monotonic", side_effect=[0, 9]), patch("screens.buy.sleep"):
            result = ROUTES["/buy"](session, {"slot": "shop_3"})
        self.assertEqual(result["result"], "uncertain", result)
        self.assertFalse(result["evidence"]["shop_slot_empty"])
        send.assert_called_once()

    def test_input_and_followup_capture_error_remain_uncertain(self):
        session = self.session()
        with patch.object(session, "preparation_frame", side_effect=[frame("before"), OSError("截图断开")]), \
                patch("screens.buy.purchase", side_effect=RuntimeError("输入结果未知")):
            result = ROUTES["/buy"](session, {"slot": "shop_3"})
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("RuntimeError: 输入结果未知", result["message"])
        self.assertIn("OSError: 截图断开", result["message"])

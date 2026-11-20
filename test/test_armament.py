"""通过保存帧跑武装箱 Pipeline，所有输入由图片控制器接收。"""

from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from game.input import InputUncertain
from ocr import ImageController
from screens.armament import Armament
from service import ROUTES, Session


FIXTURES = Path(__file__).parent / "fixtures" / "armament"


def frame(kind, state):
    with Image.open(FIXTURES / f"{kind}-{state}.webp") as image:
        return np.asarray(image.convert("RGB"))[:, :, ::-1].copy()


class Controller(ImageController):
    def __init__(self, kind):
        self.clicks = []
        self.following = iter((frame(kind, "panel"), frame(kind, "received")))
        super().__init__(frame(kind, "bench"))

    def click(self, x, y):
        self.clicks.append((x, y))
        self.image = next(self.following)
        return True

    def touch_down(self, contact, x, y, pressure):
        return self.click(x, y)

    def touch_up(self, contact):
        return True


class ArmamentTests(TestCase):
    def session(self, kind):
        session = Session(1, "framepool")
        session.controller = Controller(kind)
        self.assertTrue(session.controller.set_screenshot_use_raw_size(True))
        self.assertTrue(session.controller.post_connection().wait().succeeded)
        session.save_image = Mock(return_value={"screenshot": "saved.png"})
        session.collector = Mock()
        self.addCleanup(session.preparation_reader().close)
        return session

    def test_open_and_choose_through_serial_routes(self):
        cases = (("advanced", "进阶武装箱", "bench_5", 1, "生命之环", (938, 905), (593, 283)),
                 ("simple", "简易武装箱", "bench_8", 4, "光能电池", (1313, 905), (1464, 283)))
        for kind, title, slot, option, name, bench_point, card_point in cases:
            with self.subTest(kind=kind):
                session = self.session(kind)
                reader = session.preparation_reader()
                self.assertEqual(reader.card(session.controller.image, slot),
                                 {"kind": "armament", "action": "armament-open"})

                opened = ROUTES["/armament-open"](session, {"slot": slot})
                self.assertEqual(opened["result"], "success", opened)
                self.assertEqual((opened["title"], opened["source"]), (title, slot))
                self.assertEqual(opened["options"][option-1]["name"], name)
                self.assertEqual(ROUTES["/observe"](session, {})["options"], opened["options"])
                self.assertEqual(session.controller.clicks, [bench_point])
                session.collector.request.assert_not_called()

                chosen = ROUTES["/armament-choose"](session, {"option": option})
                self.assertEqual(chosen["result"], "success", chosen)
                self.assertEqual((chosen["count_before"], chosen["count_after"]), (0, 1))
                self.assertTrue(chosen["source_empty"])
                self.assertEqual(session.controller.clicks, [bench_point, card_point])
                session.collector.request.assert_called_once_with()
                if kind == "advanced":
                    left_top = reader.inventory.read(session.controller.image)["bag_r1_c2"]
                    self.assertIn("ring_of_life", {item["id"] for item in left_top["candidates"]})

    def test_character_slot_and_missing_panel_send_no_input(self):
        session = self.session("advanced")
        opened = ROUTES["/armament-open"](session, {"slot": "bench_1"})
        chosen = ROUTES["/armament-choose"](session, {"option": 1})
        self.assertEqual(opened["result"], "failed")
        self.assertEqual(chosen["result"], "failed")
        self.assertEqual(session.controller.clicks, [])
        session.collector.request.assert_not_called()

    def test_unconfirmed_pipeline_returns_latest_frame_without_retrying(self):
        for action, state, following, args in (
            ("open", "bench", "panel", {"slot": "bench_5"}),
            ("choose", "panel", "received", {"option": 1}),
        ):
            with self.subTest(action=action):
                session = self.session("advanced")
                session.controller.image = frame("advanced", state)
                latest = frame("advanced", following)

                def fail_click(*args):
                    session.controller.image = latest
                    raise InputUncertain("模拟节点未完成")

                route = f"/armament-{action}"
                with patch.object(Armament, "click", side_effect=fail_click) as click:
                    result = ROUTES[route](session, args)
                self.assertEqual(result["result"], "uncertain")
                self.assertEqual(result["action"], route[1:])
                self.assertEqual(result["screenshot"], "saved.png")
                self.assertIn("InputUncertain: 模拟节点未完成", result["message"])
                click.assert_called_once()
                session.save_image.assert_called_once()
                np.testing.assert_array_equal(session.save_image.call_args.args[0], latest)
                session.collector.request.assert_not_called()

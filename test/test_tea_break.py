"""用茶歇录像帧回放服务入口，输入只记录坐标。"""

from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from ocr import ImageController
from service import ROUTES, Session


FIXTURES = Path(__file__).parent / "fixtures" / "tea-break"


def frame(name):
    with Image.open(FIXTURES / f"{name}.webp") as image:
        return np.asarray(image.convert("RGB"))[:, :, ::-1].copy()


class Replay(ImageController):
    def __init__(self, first, transitions=()):
        self.transitions = iter(transitions)
        self.clicks = []
        self.following = iter(())
        super().__init__(frame(first))

    def screencap(self):
        self.image = next(self.following, self.image)
        return self.image

    def click(self, x, y):
        self.clicks.append((x, y))
        names = next(self.transitions, ())
        self.following = iter(frame(name) for name in names)
        return True

    def touch_down(self, contact, x, y, pressure):
        return self.click(x, y)

    def touch_up(self, contact):
        return True


class TeaBreakTests(TestCase):
    def session(self, first, transitions=()):
        session = Session(1, "framepool")
        session.controller = Replay(first, transitions)
        self.assertTrue(session.controller.set_screenshot_use_raw_size(True))
        self.assertTrue(session.controller.post_connection().wait().succeeded)
        session.save_image = Mock(return_value={"screenshot": "tea-break.png"})
        self.addCleanup(session.preparation_reader().close)
        return session

    def test_entry_is_observable_and_never_clicked_as_battle(self):
        session = self.session("entry")
        observed = ROUTES["/observe"](session, {})
        self.assertEqual((observed["screen"], observed["stage"], observed["node"]), ("幕后茶歇", "entry", "2-6"))
        self.assertEqual(observed["next_action"], "prepare")
        self.assertIn("slots", observed)
        self.assertIn("inventory", observed)
        self.assertIn("trait_projection", observed)
        battle = ROUTES["/start-battle"](session, {})
        self.assertEqual(battle["result"], "failed")
        self.assertEqual(battle["button_text"], "幕后茶歇")
        self.assertEqual(session.controller.clicks, [])

    def test_open_dialogue_confirm_reward_and_resume_replay(self):
        session = self.session("entry", (
            ("menu",), ("dialogue-1",), ("dialogue-2",), ("dialogue-3",), ("commissions",),
            ("selected",), ("reward",), ("settlement",) * 4 + ("arrival", "arrival", "boss"), ("board",),
        ))
        menu = ROUTES["/prepare"](session, {})
        self.assertEqual((menu["result"], menu["stage"], menu["health"], menu["gold"]),
                         ("success", "menu", 100, 72), menu)
        self.assertEqual([option["title"] for option in menu["options"]], ["狼的诱惑", "享用茶歇"])
        self.assertEqual(menu["options"][1]["health_recovery"], 20)
        self.assertEqual(session.controller.clicks, [(1813, 750)])

        commissions = ROUTES["/tea-break-choose"](session, {"option": 1})
        self.assertEqual((commissions["result"], commissions["stage"]), ("success", "commission"), commissions)
        self.assertEqual([(option["title"], option["goal"], option["reward"]) for option in commissions["options"]],
                         [("跌跌撞撞Ⅱ", "累计不完美通关3次", {"text": "5费聘用书", "count": 2}),
                          ("完美连胜Ⅱ", "连续完美通关4次", {"text": "经验", "count": 36})])
        self.assertIsNone(commissions["selected"])

        follow = {"result": "success", "screen": "选择主角", "node": "2-7"}
        with patch.object(session, "enter_preparation", return_value=follow) as prepare:
            result = ROUTES["/tea-break-commission"](session, {"option": 2})
        self.assertEqual((result["screen"], result["node"]), ("选择主角", "2-7"), result)
        prepare.assert_called_once()
        np.testing.assert_array_equal(prepare.call_args.args[0], frame("board"))
        self.assertEqual(result["tea_break"]["commission"]["title"], "完美连胜Ⅱ")
        self.assertEqual(result["tea_break"]["settlement"], {
            "gold_before": 72, "gold_after": 85, "total": 13, "verified": True,
            "items": [{"label": "基础奖励", "gold": 5}, {"label": "利息", "gold": 5}, {"label": "连胜", "gold": 3}],
        })
        self.assertEqual(session.controller.clicks, [(1813, 750), (386, 837), (1500, 1000), (1500, 1000),
                         (1500, 1000), (1410, 678), (1760, 698), (960, 982), (1500, 1000)])

    def test_different_selected_commission_returns_without_input(self):
        session = self.session("selected")
        result = ROUTES["/tea-break-commission"](session, {"option": 1})
        self.assertEqual((result["result"], result["stage"], result["selected"]), ("uncertain", "commission", 2))
        self.assertEqual(session.controller.clicks, [])

    def test_advance_on_commission_waits_for_choice(self):
        from screens.tea_break import TeaBreak

        session = self.session("commissions")
        operation = TeaBreak(session, session.preparation_frame())
        result = operation.execute("advance", current="commission")
        self.assertEqual((result["result"], result["stage"]), ("failed", "commission"))
        self.assertEqual(session.controller.clicks, [])

    def test_four_dialogue_updates_advance_until_commission(self):
        from screens.tea_break import TeaBreak

        session = Mock()
        reader = session.preparation_reader.return_value
        reader.text_boxes.side_effect = [[{}] * count for count in (1, 2, 3, 4)]
        operation = TeaBreak(session, "frame")
        operation.stage = Mock(side_effect=["dialogue"] * 4 + ["commission"])
        operation.click = Mock()
        with patch("screens.tea_break.matched", return_value=True), \
                patch("screens.tea_break.read", return_value={"stage": "commission"}):
            result = operation.follow_choice()
        self.assertEqual(result["stage"], "commission")
        self.assertEqual(operation.click.call_count, 4)

    def test_event_card_and_dialogue_do_not_depend_on_the_recorded_title(self):
        recorded_frame = frame

        def renamed_frame(name):
            image = recorded_frame(name)
            if name == "menu":
                image[625:670, 318:455] = image[625:670, 1466:1603]
                image[818:856, 341:435] = image[818:856, 1489:1583]
            elif name in {"dialogue-1", "commissions"}:
                image[142:183, 885:1040] = image[186:227, 885:1040]
            return image

        with patch(__name__ + ".frame", side_effect=renamed_frame):
            session = self.session("entry", (("menu",), ("dialogue-1",), ("commissions",)))
            menu = ROUTES["/prepare"](session, {})
            self.assertEqual((menu["result"], menu["options"][0]["title"]), ("success", "享用茶歇"), menu)
            result = ROUTES["/tea-break-choose"](session, {"option": 1})
        self.assertEqual((result["result"], result["stage"]), ("success", "commission"), result)
        self.assertEqual(result["tea_break"]["choice"], menu["options"][0]["title"])
        self.assertEqual(session.controller.clicks, [(1813, 750), (386, 837), (1500, 1000)])

    def test_tea_choice_continues_when_reward_page_is_recognized(self):
        session = self.session("menu", (("reward",),))
        with patch("screens.tea_break.TeaBreak.claim", return_value={"result": "success", "stage": "reward"}) as claim:
            result = ROUTES["/tea-break-choose"](session, {"option": 1})
        self.assertEqual(result["tea_break"]["choice"], "狼的诱惑")
        self.assertEqual(session.controller.clicks, [(386, 837)])
        claim.assert_called_once()

    def test_meal_choice_selects_and_confirms_before_reward(self):
        session = self.session("meal-menu", (("meal",), ("meal-selected",), ("meal-reward",)))
        with patch("screens.tea_break.TeaBreak.claim", return_value={"result": "success", "stage": "reward"}):
            result = ROUTES["/tea-break-choose"](session, {"option": 2})
        self.assertEqual((result["result"], result["stage"]), ("success", "reward"), result)
        self.assertEqual(result["tea_break"]["meal"], {"health_before": 41, "health_after": 61,
                                                       "health_recovery": 20})
        self.assertEqual(session.controller.clicks, [(1534, 837), (1450, 475), (1760, 480)])

    def test_meal_selection_timeout_does_not_click_confirm(self):
        session = self.session("meal-menu", (("meal",), ("meal",)))
        result = ROUTES["/tea-break-choose"](session, {"option": 2})
        self.assertEqual((result["result"], result["stage"]), ("uncertain", "meal"), result)
        self.assertIn("TeaSelectMeal", result["message"])
        self.assertEqual(session.controller.clicks, [(1534, 837), (1450, 475)])

    def test_meal_missing_recovery_stops_before_selecting(self):
        from screens.tea_break import read_menu

        session = self.session("meal-menu", (("meal",),))
        def omit_recovery(reader, image):
            options = read_menu(reader, image)
            del options[1]["health_recovery"]
            return options
        with patch("screens.tea_break.read_menu", side_effect=omit_recovery):
            result = ROUTES["/tea-break-choose"](session, {"option": 2})
        self.assertEqual((result["result"], result["stage"]), ("uncertain", "meal"), result)
        self.assertEqual(result["message"], "茶歇回复量待确认")
        self.assertEqual(session.controller.clicks, [(1534, 837)])

    def test_meal_unreadable_health_stops_before_claim(self):
        session = self.session("meal-menu", (("meal",), ("meal-selected",), ("meal-reward",)))
        reader = session.preparation_reader()
        integer = reader.integer
        reader.integer = Mock(side_effect=lambda image, name, roi:
                              None if name == "茶歇生命值" else integer(image, name, roi))
        result = ROUTES["/tea-break-choose"](session, {"option": 2})
        self.assertEqual((result["result"], result["stage"]), ("uncertain", "reward"), result)
        self.assertEqual(result["message"], "茶歇回血结果待核对")
        self.assertEqual(session.controller.clicks, [(1534, 837), (1450, 475), (1760, 480)])

    def test_meal_unexpected_health_change_stops_before_claim(self):
        session = self.session("meal-menu", (("meal",), ("meal-selected",), ("meal-reward",)))
        reader = session.preparation_reader()
        integer = reader.integer
        reader.integer = Mock(side_effect=lambda image, name, roi:
                              41 if name == "茶歇生命值" else integer(image, name, roi))
        result = ROUTES["/tea-break-choose"](session, {"option": 2})
        self.assertEqual((result["result"], result["stage"]), ("uncertain", "reward"), result)
        self.assertEqual(result["message"], "茶歇回血结果待核对")
        self.assertEqual(session.controller.clicks, [(1534, 837), (1450, 475), (1760, 480)])

    def test_unconfirmed_tea_transition_keeps_frame_and_does_not_repeat_click(self):
        session = self.session("menu")
        result = ROUTES["/tea-break-choose"](session, {"option": 2})
        self.assertEqual((result["result"], result["stage"], result["screenshot"]),
                         ("uncertain", "menu", "tea-break.png"))
        self.assertIn("TeaChoose", result["message"])
        self.assertEqual(session.controller.clicks, [(1534, 837)])

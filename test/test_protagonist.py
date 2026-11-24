"""主角选择、单侧刷新与备战恢复测试。"""

import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

from game.input import InputUncertain
from screens.protagonist import CHARACTERS, CONFIRM, TARGET, Protagonist, ProtagonistOperation
from screens.preparation import Preparation
from service import Session


def observation(character=None, selected=None, count=6, gold=46):
    return {"screen": "选择主角", "stage": "selection", "needs_ai": True,
            "characters": {"front_1": {"name": "云璃"}, "front_4": None},
            "character": {"name": character} if character else None,
            "selected": selected, "confirm_enabled": character is not None and selected is not None,
            "refreshes_left": count, "gold": gold,
            "options": [{"name": "光环甲", "text": "效果甲", "refresh_cost": 1},
                        {"name": "光环乙", "text": "效果乙", "refresh_cost": 1}]}


class ProtagonistReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = Protagonist.__new__(Protagonist)
        self.reader.preparation = Mock()
        self.reader.score = Mock(return_value=0.2)
        self.reader.tasker = Mock()
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_stage_uses_ocr_mapping_for_entry_and_title_for_selection(self):
        self.reader.find = Mock(return_value=None)
        self.reader.preparation.action_button.return_value = {"button_text": "选择主角", "next_action": "protagonist-open"}
        with patch("screens.protagonist.preparation_page", return_value={"result": "success", "screen": "备战"}):
            self.assertEqual(self.reader.stage(self.image), "entry")
        self.assertEqual(self.reader.find.call_count, 1)
        self.reader.find.return_value = Mock()
        self.reader.preparation.action_button.reset_mock()
        self.assertEqual(self.reader.stage(self.image), "selection")
        self.reader.preparation.action_button.assert_not_called()

    def test_unknown_button_or_unconfirmed_preparation_is_not_main_character_entry(self):
        self.reader.find = Mock(return_value=None)
        self.reader.preparation.action_button.return_value = {"button_text": "新buff文字", "next_action": None}
        with patch("screens.protagonist.preparation_page", return_value={"result": "success", "screen": "备战"}):
            self.assertIsNone(self.reader.stage(self.image))
        self.reader.preparation.action_button.reset_mock()
        with patch("screens.protagonist.preparation_page", return_value={"result": "uncertain", "screen": "未知"}):
            self.assertIsNone(self.reader.stage(self.image))
        self.reader.preparation.action_button.assert_not_called()

    def test_size_checked_before_matching(self):
        self.reader.find = Mock()
        with self.assertRaises(ValueError):
            self.reader.stage(self.image[:720, :1280])
        self.reader.find.assert_not_called()

    def test_missing_template_and_task_error_are_distinct(self):
        detail = self.reader.tasker.post_recognition.return_value.wait.return_value.get.return_value
        detail.nodes = [Mock()]
        detail.nodes[0].recognition.best_result = None
        self.assertIsNone(self.reader.find(self.image, "title", (0, 0, 0, 0)))
        self.reader.tasker.post_recognition.return_value.wait.return_value.get.return_value = None
        with self.assertRaisesRegex(RuntimeError, "主角模板识别出错"):
            self.reader.find(self.image, "title", (0, 0, 0, 0))

    def test_reads_shared_count_gold_tags_and_difficulty_from_one_frame(self):
        self.reader.lines = Mock(side_effect=[
            ["光环甲", "速度", "效果甲", "敌人难度+3"], ["×1", "刷新"],
            ["光环乙", "变形", "效果乙"], ["×2", "刷新"], ["1刷新还剩6次"], ["46"]])
        self.reader.characters = Mock(return_value={})
        self.reader.character = Mock(return_value=None)
        result = self.reader.read(self.image)
        self.assertEqual(result["refreshes_left"], 6)
        self.assertEqual(result["gold"], 46)
        self.assertEqual([option["refresh_cost"] for option in result["options"]], [1, 2])
        self.assertEqual(result["options"][0]["enemy_difficulty_change"], 3)
        self.assertEqual(result["options"][0]["tags"], "速度")
        self.assertTrue(all(c.args[0] is self.image for c in self.reader.lines.call_args_list))

    def test_live_frame_reads_gold_twenty_one(self):
        path = Path(__file__).parent / "fixtures" / "protagonist" / "gold-twenty-one.webp"
        with Image.open(path) as source:
            image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        preparation = Preparation()
        self.addCleanup(preparation.close)
        reader = Protagonist(preparation)
        self.assertEqual(reader.read(image)["gold"], 21)

    def test_confirmation_uses_visible_button_brightness(self):
        self.assertFalse(self.reader.enabled(self.image))
        self.image[943:966, 780:800] = 230
        self.assertTrue(self.reader.enabled(self.image))

    def test_following_accepts_any_nonempty_action_button_text(self):
        self.reader.score = Mock()
        for text in ("出战", "任意增益修改的文字"):
            self.reader.preparation.action_button.return_value = {
                "button_text": text, "next_action": "start-battle" if text == "出战" else None}
            with self.subTest(text=text), patch("screens.protagonist.preparation_page",
                                                return_value={"result": "success", "screen": "备战"}):
                result = self.reader.following(self.image)
            self.assertEqual(result["button_text"], text)
            self.assertEqual(result["result"], "success")
        self.reader.score.assert_not_called()

    def test_following_waits_when_button_text_is_empty(self):
        self.reader.preparation.action_button.return_value = {"button_text": "", "next_action": None}
        with patch("screens.protagonist.preparation_page", return_value={"result": "success", "screen": "备战"}):
            self.assertIsNone(self.reader.following(self.image))
        self.reader.preparation.action_button.assert_called_once_with(self.image)

    def test_following_does_not_ocr_before_preparation_is_confirmed(self):
        with patch("screens.protagonist.preparation_page", return_value={"result": "uncertain", "screen": "未知"}):
            self.assertIsNone(self.reader.following(self.image))
        self.reader.preparation.action_button.assert_not_called()

    def test_following_does_not_complete_while_known_entry_remains(self):
        self.reader.preparation.action_button.return_value = {"button_text": "选择主角", "next_action": "protagonist-open"}
        with patch("screens.protagonist.preparation_page", return_value={"result": "success", "screen": "备战"}):
            self.assertIsNone(self.reader.following(self.image))


class ProtagonistOperationTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.save_image.side_effect = lambda image: {"screenshot": f"{image}.png"}
        self.job = self.session.controller.post_screencap.return_value.wait.return_value
        self.job.succeeded = True
        self.job.get.side_effect = [f"frame-{i}" for i in range(1, 15)]
        for method in ("post_click", "post_swipe", "post_touch_move"):
            getattr(self.session.controller, method).return_value.wait.return_value.succeeded = True
        self.reader = Mock()
        self.reader.stage.return_value = "selection"
        self.reader.read.return_value = observation()
        self.reader.following.return_value = None
        self.reader.preparation.action_button.return_value = {"button_text": "选择主角", "next_action": "protagonist-open"}
        self.reader.refresh_button.return_value = Mock(box=(1524, 758, 66, 30))
        self.reader.preparation.header.return_value = {"node": "1-9"}
        self.operation = ProtagonistOperation(self.session, self.reader)
        self.sleep = patch("screens.protagonist.sleep").start()
        self.addCleanup(self.sleep.stop)

    def test_read_is_same_frame_and_sends_no_input(self):
        result = self.operation.save(self.operation.execute())
        self.assertEqual(result["screenshot"], "frame-1.png")
        self.reader.read.assert_called_once_with("frame-1")
        self.session.controller.post_click.assert_not_called()
        self.session.controller.post_swipe.assert_not_called()

    def test_read_entry_preserves_readonly_behavior(self):
        self.reader.stage.return_value = "entry"
        self.assertEqual(self.operation.execute()["stage"], "entry")
        self.reader.read.assert_not_called()
        self.session.controller.post_click.assert_not_called()

    def test_open_clicks_once_and_waits_through_transition(self):
        self.reader.stage.side_effect = ["entry", None, "selection"]
        self.assertEqual(self.operation.execute("open")["stage"], "selection")
        self.session.controller.post_click.assert_called_once_with(1810, 750)
        self.assertEqual(self.sleep.call_args_list, [call(2), call(2)])

    def test_open_reuses_known_same_frame_entry_without_navigating_again(self):
        entry = {"result": "success", "screen": "选择主角", "stage": "entry",
                 "button_text": "选择主角", "next_action": "protagonist-open", "needs_ai": True}
        self.reader.stage.return_value = "selection"
        self.assertEqual(self.operation.execute("open", image="entry", observation=entry)["stage"], "selection")
        self.reader.stage.assert_called_once_with("frame-1")
        self.session.controller.post_click.assert_called_once_with(1810, 750)

    def test_open_timeout_does_not_repeat_click(self):
        self.reader.stage.return_value = "entry"
        result = self.operation.execute("open")
        self.assertEqual(result["result"], "uncertain")
        self.session.controller.post_click.assert_called_once()
        self.assertEqual(self.job.get.call_count, 4)

    def test_drag_uses_main_character_page_coordinates_and_verifies_target(self):
        self.reader.read.side_effect = [observation(), observation(), observation(character="云璃")]
        result = self.operation.execute("character", slot="front_1")
        self.assertEqual(result["result"], "success")
        x, y, width, height = CHARACTERS["front_1"]
        self.session.controller.post_swipe.assert_called_once_with(x + width // 2, y + height // 2, *TARGET, 700)
        self.session.controller.post_touch_move.assert_called_once()

    def test_empty_character_sends_no_drag(self):
        self.assertEqual(self.operation.execute("character", slot="front_4")["result"], "failed")
        self.session.controller.post_swipe.assert_not_called()

    def test_unknown_character_sends_no_drag(self):
        before = observation()
        before["characters"]["front_1"] = {"unknown": True}
        self.reader.read.return_value = before
        self.assertEqual(self.operation.execute("character", slot="front_1")["result"], "uncertain")
        self.session.controller.post_swipe.assert_not_called()

    def test_refresh_checks_shared_count_and_gold_with_one_click(self):
        self.reader.read.side_effect = [observation(), observation(count=5, gold=46), observation(count=5, gold=45)]
        result = self.operation.execute("refresh", option=2)
        self.assertEqual(result["result"], "success")
        self.session.controller.post_click.assert_called_once_with(1557, 773)
        self.reader.refresh_button.assert_called_once_with("frame-1", 2)

    def test_aura_clicks_twice_without_matching_selection_marker(self):
        self.reader.read.side_effect = [observation(character="云璃"), observation(character="云璃", selected=2)]
        result = self.operation.execute("aura", option=2)
        self.assertEqual(result["result"], "success")
        clicks = self.session.controller.post_click.call_args_list
        self.assertEqual(len(clicks), 2)
        self.assertEqual(clicks[0], clicks[1])
        self.reader.selected.assert_not_called()
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])

    def test_aura_requires_character_before_clicking(self):
        self.assertEqual(self.operation.execute("aura", option=1)["result"], "failed")
        self.session.controller.post_click.assert_not_called()

    def test_unchanged_refresh_times_out_without_repeating_input(self):
        self.assertEqual(self.operation.execute("refresh", option=1)["result"], "uncertain")
        self.session.controller.post_click.assert_called_once()
        self.assertEqual(self.sleep.call_args_list, [call(2)] * 3)

    def test_zero_count_or_low_gold_stops_before_refresh(self):
        for before in (observation(count=0), observation(gold=0)):
            with self.subTest(before=before):
                self.reader.read.return_value = before
                self.assertEqual(self.operation.execute("refresh", option=1)["result"], "failed")
        self.session.controller.post_click.assert_not_called()

    def test_partial_options_are_returned_without_spending(self):
        before = observation()
        before["options"][0]["unknown"] = True
        self.reader.read.return_value = before
        self.assertEqual(self.operation.execute("refresh", option=1)["result"], "uncertain")
        self.session.controller.post_click.assert_not_called()

    def test_confirm_requires_both_choices_and_enabled_button(self):
        self.reader.read.return_value = observation(character="云璃")
        self.assertEqual(self.operation.execute("confirm")["result"], "failed")
        self.session.controller.post_click.assert_not_called()

    def test_confirm_waits_for_preparation_and_action_button_text(self):
        self.reader.read.return_value = observation(character="云璃", selected=1)
        self.reader.following.side_effect = [None, {"result": "success", "screen": "备战"}]
        result = self.operation.execute("confirm")
        self.assertEqual(result["screen"], "备战")
        self.session.controller.post_click.assert_called_once_with(*CONFIRM)
        self.reader.following.assert_has_calls([call("frame-2"), call("frame-3")])

    def test_input_error_returns_recent_frame_with_context(self):
        self.session.controller.post_swipe.return_value.wait.return_value.succeeded = False
        result = self.operation.save(self.operation.execute("character", slot="front_1"))
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.assertEqual(result["screenshot"], "frame-1.png")
        self.session.controller.post_touch_move.assert_not_called()

    def test_save_after_input_error_is_uncertain(self):
        self.reader.stage.side_effect = ["entry", "selection"]
        result = self.operation.execute("open")
        self.session.save_image.side_effect = OSError("磁盘出错")
        with self.assertRaisesRegex(InputUncertain, "OSError"):
            self.operation.save(result)


class ProtagonistSessionTests(unittest.TestCase):
    def test_reader_is_cached_and_operation_is_fresh(self):
        session = Session(1, "framepool")
        session.preparation = Mock()
        with patch("screens.protagonist.Protagonist") as reader, patch("screens.protagonist.ProtagonistOperation") as operation:
            result = {"result": "success", **observation()}
            operation.return_value.execute.return_value = result
            operation.return_value.save.return_value = result
            session.protagonist_action()
            session.protagonist_action("refresh", option=2)
        reader.assert_called_once_with(session.preparation)
        self.assertEqual(operation.call_count, 2)

    def test_confirm_resumes_existing_preparation_progress(self):
        session = Session(1, "framepool")
        session.protagonist = Mock()
        session.preparation_flow = Mock()
        session.preparation_flow.resume.return_value = {"result": "success", "screen": "备战"}
        with patch("screens.protagonist.ProtagonistOperation") as operation:
            operation.return_value.execute.return_value = {"result": "success", "screen": "备战"}
            operation.return_value.image = "confirmed"
            result = session.protagonist_action("confirm")
        session.preparation_flow.resume.assert_called_once_with("confirmed")
        self.assertEqual(result["action"], "protagonist-confirm")
        operation.return_value.save.assert_not_called()



if __name__ == "__main__":
    unittest.main()

"""行动按钮映射、单帧 OCR 复用及正常入口分流。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from flow.prepare import PreparationFlow
from game.slots import ACTION_BUTTON
from screens.battle import Battle
from screens.preparation import Preparation
from screens.protagonist import Protagonist
from service import Session


class ActionButtonTests(unittest.TestCase):
    def setUp(self):
        self.reader = Preparation.__new__(Preparation)
        self.reader.button_image = None
        self.reader.button_result = None
        self.reader.load_ocr = Mock()
        self.reader.text = Mock(return_value="出战")
        self.reader.enlarged_text = Mock(return_value="80")
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_known_labels_map_and_unknown_names_keep_their_text(self):
        for text, expected in (("出战", "start-battle"), ("选择主角", "protagonist-open"),
                               ("任意增益文字", None), ("", None)):
            with self.subTest(text=text):
                self.reader.text.return_value = text
                result = self.reader.action_button(self.image.copy())
                self.assertEqual(result, {"button_text": text, "next_action": expected})

    def test_label_region_and_whitespace_normalization(self):
        self.reader.text.return_value = " \n选择 主角\t "
        result = self.reader.action_button(self.image)
        self.assertEqual(result, {"button_text": "选择主角", "next_action": "protagonist-open"})
        self.reader.text.assert_called_once_with(self.image, "右侧行动按钮", ACTION_BUTTON)
        self.reader.load_ocr.assert_called_once()

    def test_same_frame_reads_ocr_once_even_for_blank_results(self):
        self.reader.text.return_value = " \n\t "
        for _ in range(4):
            self.assertEqual(self.reader.action_button(self.image), {"button_text": "", "next_action": None})
        self.reader.text.assert_called_once()

    def test_new_frame_replaces_result_without_reusing_older_frames(self):
        self.reader.text.side_effect = ["出战", "选择主角", "新buff"]
        first, second = self.image, self.image.copy()
        self.assertEqual(self.reader.action_button(first)["next_action"], "start-battle")
        self.assertEqual(self.reader.action_button(second)["next_action"], "protagonist-open")
        self.assertEqual(self.reader.action_button(first), {"button_text": "新buff", "next_action": None})
        self.assertEqual(self.reader.text.call_count, 3)

    def test_ocr_error_is_not_cached_as_no_text(self):
        self.reader.text.side_effect = RuntimeError("OCR 任务失败")
        with self.assertRaisesRegex(RuntimeError, "OCR 任务失败"):
            self.reader.action_button(self.image)
        self.assertIsNone(self.reader.button_image)
        self.assertIsNone(self.reader.button_result)

    def test_stage_battle_and_confirmation_reuse_one_same_frame_result(self):
        protagonist = Protagonist.__new__(Protagonist)
        protagonist.preparation = self.reader
        protagonist.find = Mock(return_value=None)
        protagonist.score = Mock()
        battle = Battle.__new__(Battle)
        battle.score = Mock()
        page = {"result": "success", "screen": "备战"}
        with patch("screens.protagonist.preparation_page", return_value=page), \
                patch("screens.battle.preparation_page", return_value=page):
            self.assertIsNone(protagonist.stage(self.image))
            self.assertEqual(battle.ready(self.image, self.reader)["next_action"], "start-battle")
            self.assertEqual(protagonist.following(self.image)["next_action"], "start-battle")
        action_reads = [call for call in self.reader.text.call_args_list if call.args[1] == "右侧行动按钮"]
        self.assertEqual(len(action_reads), 1)


class ActionDispatchTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")
        self.preparation = Preparation.__new__(Preparation)
        self.preparation.score = Mock(side_effect=lambda image, name, roi: float(name == "shop.png"))
        self.preparation.header = Mock(return_value={"layout": "closed", "node": "1-9",
                                                     "phase_box": [430, 33, 80, 24]})
        self.preparation.button_image = None
        self.preparation.button_result = None
        self.preparation.load_ocr = Mock()
        self.preparation.text = Mock(return_value="出战")
        self.preparation.page = Mock(return_value={"result": "success", "screen": "备战"})
        self.preparation.observe = Mock(side_effect=lambda image: {
            "result": "success", "slots": {}, **self.preparation.action_button(image)})
        self.protagonist = Protagonist.__new__(Protagonist)
        self.protagonist.preparation = self.preparation
        self.protagonist.find = Mock(return_value=None)
        self.protagonist.score = Mock()
        self.session = Session(1, "framepool")
        self.session.preparation = self.preparation
        self.session.protagonist = self.protagonist
        self.session.supply = Mock()
        self.session.encounter = Mock()
        self.session.encounter.page.return_value = False
        self.session.supply.page.return_value = False
        self.session.strategy = Mock()
        self.session.strategy.page.return_value = False
        self.session.preparation_frame = Mock(return_value=self.image)
        self.session.save_image = Mock(return_value={"screenshot": "frame.png"})
        self.session.controller = Mock()
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True



    def test_prepare_opens_known_entry_once_without_rechecking_that_frame(self):
        self.preparation.text.return_value = "选择主角"
        selected = self.image.copy()
        self.protagonist.find.side_effect = lambda image, name, roi: (
            Mock() if image is selected and name == "title" else None)
        self.protagonist.read = Mock(return_value={"screen": "选择主角", "stage": "selection",
            "gold": 46, "refreshes_left": 6, "options": [{"name": "光环", "text": "效果"}], "needs_ai": True})
        job = self.session.controller.post_screencap.return_value.wait.return_value
        job.succeeded = True
        job.get.return_value = selected
        with patch("flow.prepare.sweep") as sweep, patch("screens.protagonist.sleep"):
            result = self.session.run("prepare", lambda: PreparationFlow(self.session).enter(self.image))
        self.assertEqual(result["stage"], "selection")
        self.session.controller.post_click.assert_called_once_with(1810, 750)
        self.preparation.text.assert_called_once_with(self.image, "右侧行动按钮", ACTION_BUTTON)
        self.assertEqual(self.protagonist.find.call_count, 2)
        self.assertIs(self.protagonist.find.call_args_list[0].args[0], self.image)
        self.assertIs(self.protagonist.find.call_args_list[1].args[0], selected)
        sweep.assert_not_called()


if __name__ == "__main__":
    unittest.main()

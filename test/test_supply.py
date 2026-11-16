"""补给可选刷新、单选确认及入场分流。"""

from itertools import count
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

from flow.prepare import PreparationFlow
from game.input import InputUncertain
from ocr import ImageController
from screens.supply import CONFIRM, OPTIONS, REFRESH, REFRESH_AREA, Supply, SupplyOperation, complete
from service import Session


def observation(can_refresh=True, refreshes=1, current=0, limit=1):
    result = {"screen": "补给", "title": "补给阶段", "needs_ai": True,
              "node": "1-5",
              "selection": {"current": current, "limit": limit}, "can_refresh": can_refresh,
              "options": [{"slot": f"option_{i}", "character": f"角色{i}", "equipment": f"装备{i}",
                           "trial": i <= 3} for i in range(1, 6)]}
    if can_refresh:
        result["refreshes_left"] = refreshes
    return result


class SupplyReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = Supply.__new__(Supply)
        self.reader.preparation = Mock()
        self.reader.preparation.header.return_value = {"node": "1-5"}
        self.reader.score = Mock(return_value=0.95)
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_names_trial_and_counts_use_the_same_frame(self):
        names = [text for i in range(1, 6) for text in (f"角色{i}", f"装备{i}")]
        self.reader.preparation.text.side_effect = [*names, "已选择 0/1", "剩余次数：1"]
        self.reader.score.side_effect = [1, 1, 1, 0.1, 0.1, 1]
        result = self.reader.read(self.image)
        self.assertEqual(result, observation())
        self.assertTrue(all(c.args[0] is self.image for c in self.reader.preparation.text.call_args_list))

    def test_absent_refresh_button_does_not_read_count(self):
        self.reader.score.return_value = 0.2
        self.assertEqual(self.reader.refresh(self.image), {"can_refresh": False})
        self.reader.preparation.text.assert_not_called()

    def test_unreadable_refresh_count_is_distinct_from_absent_and_zero(self):
        self.reader.preparation.text.return_value = "剩余次数："
        self.assertEqual(self.reader.refresh(self.image), {"can_refresh": True, "refreshes_left": None})
        self.reader.preparation.text.return_value = "剩余次数：0"
        self.assertEqual(self.reader.refresh(self.image), {"can_refresh": True, "refreshes_left": 0})

    def test_optional_refresh_does_not_block_complete_selection(self):
        self.assertTrue(complete(observation(False)))
        self.assertTrue(complete(observation(refreshes=None)))
        self.assertTrue(complete(observation(refreshes=0)))

    def test_missing_name_or_selection_is_incomplete(self):
        result = observation()
        result["options"][0]["unknown"] = True
        self.assertFalse(complete(result))
        self.assertFalse(complete(observation(current=None, limit=None)))
        self.reader.preparation.text.return_value = "已选择"
        self.assertEqual(self.reader.selection(self.image), {"current": None, "limit": None})

    def test_partial_option_keeps_read_text(self):
        self.reader.preparation.text.side_effect = ["", "装备1", *["文字"] * 8, "已选择0/1", "剩余次数：1"]
        result = self.reader.read(self.image)
        self.assertTrue(result["options"][0]["unknown"])
        self.assertEqual(result["options"][0]["equipment"], "装备1")

    def test_title_and_dimensions_are_checked_before_ocr(self):
        self.assertTrue(self.reader.page(self.image))
        self.reader.score.return_value = 0.849
        self.assertFalse(self.reader.page(self.image))
        with self.assertRaisesRegex(ValueError, "1920"):
            self.reader.page(self.image[:720, :1280])
        self.reader.preparation.text.assert_not_called()


class SupplyFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with Image.open(Path(__file__).parent / "fixtures" / "supply" / "five-options.png") as image:
            cls.image = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
        cls.controller = ImageController(cls.image)
        if not cls.controller.post_connection().wait().succeeded:
            raise RuntimeError("离线图片控制器连接失败")
        cls.reader = Supply(SimpleNamespace(controller=cls.controller, header=Mock(return_value={"node": "1-5"})))

    def test_source_title_trial_and_refresh_templates(self):
        self.assertTrue(self.reader.page(self.image))
        self.assertGreaterEqual(self.reader.score(self.image, "supply_refresh.png", REFRESH_AREA), 0.9)
        # 实际文本读取另行离线验证；这里固定检查样例中前三张卡的试用标签。
        with patch.object(self.reader, "text", return_value="文字"), \
                patch.object(self.reader, "selection", return_value={"current": 0, "limit": 1}):
            result = self.reader.read(self.image)
        self.assertEqual([o["trial"] for o in result["options"]], [True, True, True, False, False])

    def test_covering_refresh_keeps_supply_page_and_no_refresh_count(self):
        image = self.image.copy()
        x, y, width, height = REFRESH_AREA
        image[y:y+height, x:x+width] = 0
        self.assertTrue(self.reader.page(image))
        self.assertEqual(self.reader.refresh(image), {"can_refresh": False})

    def test_covering_title_is_not_supply(self):
        image = self.image.copy()
        image[130:185, 890:1035] = 0
        self.assertFalse(self.reader.page(image))

    def test_native_late_supply_title_and_disabled_refresh_are_recognized(self):
        with Image.open(Path(__file__).parent / "fixtures" / "supply" / "late-options.png") as source:
            image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        self.assertTrue(self.reader.page(image))
        with patch.object(self.reader, "text", return_value="剩余次数：0"):
            self.assertEqual(self.reader.refresh(image), {"can_refresh": True, "refreshes_left": 0})


class SupplyOperationTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.save_image.side_effect = lambda frame: {"screenshot": f"{frame}.png"}
        self.job = self.session.controller.post_screencap.return_value.wait.return_value
        self.job.succeeded = True
        self.job.get.return_value = "frame"
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True
        self.reader = Mock()
        self.reader.page.return_value = True
        self.reader.read.return_value = observation()
        self.reader.refresh.return_value = {"can_refresh": True, "refreshes_left": 0}
        self.reader.selection.return_value = {"current": 1, "limit": 1}
        self.op = SupplyOperation(self.session, self.reader)
        self.addCleanup(patch.stopall)
        patch("screens.supply.sleep").start()
        patch("screens.supply.monotonic", side_effect=count()).start()

    def test_read_without_refresh_returns_same_frame_and_no_input(self):
        self.reader.read.return_value = observation(False)
        result = self.op.save(self.op.execute())
        self.assertEqual(result["result"], "success")
        self.assertNotIn("refreshes_left", result)
        self.reader.read.assert_called_once_with("frame")
        self.session.save_image.assert_called_once_with("frame")
        self.session.controller.post_click.assert_not_called()

    def test_absent_zero_and_unknown_refresh_send_no_input(self):
        for result, expected in ((observation(False), "failed"), (observation(refreshes=0), "failed"),
                                 (observation(refreshes=None), "uncertain")):
            with self.subTest(result=result):
                self.reader.read.return_value = result
                self.assertEqual(self.op.execute("refresh")["result"], expected)
        self.session.controller.post_click.assert_not_called()

    def test_refresh_waits_for_count_and_reads_same_names(self):
        self.reader.refresh.side_effect = [{"can_refresh": True, "refreshes_left": 1},
                                         {"can_refresh": True, "refreshes_left": 0}]
        self.reader.read.side_effect = [observation(), observation(refreshes=0)]
        result = self.op.execute("refresh")
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["options"][0]["character"], "角色1")
        self.session.controller.post_click.assert_called_once_with(*REFRESH)

    def test_refresh_button_disappearing_is_not_assumed_zero(self):
        self.reader.refresh.return_value = {"can_refresh": False}
        result = self.op.execute("refresh")
        self.assertEqual(result["result"], "uncertain")
        self.assertFalse(result["can_refresh"])
        self.session.controller.post_click.assert_called_once_with(*REFRESH)

    def test_unchanged_count_does_not_repeat_refresh(self):
        self.reader.refresh.return_value = {"can_refresh": True, "refreshes_left": 1}
        self.assertEqual(self.op.execute("refresh")["result"], "uncertain")
        self.session.controller.post_click.assert_called_once_with(*REFRESH)

    def test_choose_without_refresh_confirms_count_before_confirmation(self):
        self.reader.read.return_value = observation(False)
        self.reader.page.side_effect = [True, True, False, False]
        self.reader.selection.return_value = {"current": 1, "limit": 1}
        self.session.preparation_scene.side_effect = [
            {"result": "uncertain", "screen": "未知"}, {"result": "success", "screen": "备战", "shop": "open"}]
        result = self.op.execute("choose", 4)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["shop"], "open")
        self.assertEqual(self.session.controller.post_click.call_args_list, [call(*OPTIONS[3]), call(*CONFIRM)])

    def test_failed_selection_does_not_confirm(self):
        self.reader.selection.return_value = {"current": 0, "limit": 1}
        self.assertEqual(self.op.execute("choose", 2)["result"], "uncertain")
        self.session.controller.post_click.assert_called_once_with(*OPTIONS[1])

    def test_preselected_or_multi_selection_sends_no_input(self):
        for current, limit in ((1, 1), (0, 2)):
            self.reader.read.return_value = observation(current=current, limit=limit)
            self.assertEqual(self.op.execute("choose", 2)["result"], "uncertain")
        self.session.controller.post_click.assert_not_called()

    def test_other_choice_after_confirm_returns_control_to_ai(self):
        self.reader.page.side_effect = [True, True, False]
        self.session.preparation_scene.return_value = {"result": "success", "screen": "投资策略", "needs_ai": True}
        result = self.op.execute("choose", 1)
        self.assertEqual(result["screen"], "投资策略")
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(self.session.controller.post_click.call_count, 2)

    def test_staying_on_supply_after_confirmation_is_uncertain(self):
        self.assertEqual(self.op.execute("choose", 1)["result"], "uncertain")
        self.assertEqual(self.session.controller.post_click.call_count, 2)

    def test_partial_options_time_out_without_input(self):
        result = observation()
        result["options"][0]["unknown"] = True
        self.reader.read.return_value = result
        result = self.op.execute("choose", 1)
        self.assertEqual(result["result"], "uncertain")
        self.assertTrue(result["options"][0]["unknown"])
        self.session.controller.post_click.assert_not_called()

    def test_initial_capture_and_ocr_failures_propagate(self):
        self.job.succeeded = False
        with self.assertRaisesRegex(RuntimeError, "截图失败"):
            self.op.execute()
        self.job.succeeded = True
        self.reader.read.side_effect = RuntimeError("OCR 出错")
        with self.assertRaisesRegex(RuntimeError, "OCR 出错"):
            self.op.execute()
        self.session.controller.post_click.assert_not_called()

    def test_failed_click_keeps_last_frame_and_exception_type(self):
        self.session.controller.post_click.return_value.wait.return_value.succeeded = False
        result = self.op.save(self.op.execute("choose", 1))
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.assertEqual(result["screenshot"], "frame.png")
        self.session.controller.post_click.assert_called_once()

    def test_ocr_error_after_input_is_uncertain(self):
        self.reader.refresh.side_effect = RuntimeError("次数 OCR 出错")
        result = self.op.execute("refresh")
        self.assertIn("RuntimeError: 次数 OCR 出错", result["message"])
        self.assertEqual(result["result"], "uncertain")
        self.session.controller.post_click.assert_called_once()

    def test_save_failure_after_input_is_uncertain(self):
        self.reader.read.side_effect = [observation(), observation(refreshes=0)]
        result = self.op.execute("refresh")
        self.session.save_image.side_effect = OSError("磁盘出错")
        with self.assertRaisesRegex(InputUncertain, "OSError"):
            self.op.save(result)


class SupplyFlowTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.protagonist_reader.return_value.stage.return_value = None
        self.reader = self.session.supply_reader.return_value
        self.reader.page.return_value = True
        self.reader.read.return_value = observation(False)
        self.session.save_image.side_effect = lambda frame: {"screenshot": f"{frame}.png"}
        self.job = self.session.controller.post_screencap.return_value.wait.return_value
        self.job.succeeded = True
        self.job.get.return_value = "supply"
        self.sweep = patch("flow.prepare.sweep").start()
        self.sleep = patch("flow.prepare.sleep").start()
        self.addCleanup(patch.stopall)

    def test_supply_precedes_preparation_and_stops_blank_clicking(self):
        with patch("flow.prepare.layout") as layout:
            result = PreparationFlow(self.session).enter(delay=8)
        self.assertEqual(result["screen"], "补给")
        self.assertEqual(result["result"], "success")
        self.assertEqual(self.sleep.call_args_list, [call(2), call(6)])
        self.session.controller.post_click.assert_not_called()
        layout.assert_not_called()
        self.sweep.assert_not_called()

    def test_supply_after_sweep_resumes_without_recollecting(self):
        self.reader.page.side_effect = [False, True, False]
        self.session.preparation_reader.return_value.observe.return_value = {"result": "success", "slots": {}}
        with patch("flow.prepare.layout", return_value={"result": "success", "screen": "备战", "shop": "closed"}):
            flow = PreparationFlow(self.session)
            self.assertEqual(flow.enter("before")["screen"], "补给")
            self.assertEqual(flow.resume("after")["screen"], "备战")
        self.sweep.assert_called_once()
        self.session.preparation_reader.return_value.observe.assert_called_once_with("after")

    def test_read_only_preparation_and_collection_scene_dispatch_supply(self):
        session = Session(1, "framepool")
        session.protagonist = Mock()
        session.protagonist.stage.return_value = None
        session.strategy = Mock()
        session.strategy.page.return_value = False
        session.supply = Mock()
        session.supply.page.return_value = True
        session.preparation = Mock()
        session.preparation.score.return_value = 0
        session.preparation_frame = Mock(return_value="supply")
        session.supply_action = Mock(return_value=observation())
        self.assertEqual(session.observe_preparation()["screen"], "补给")
        session.supply_action.assert_called_once_with(image="supply")
        self.assertEqual(session.preparation_scene("supply")["screen"], "补给")
        session.preparation.recognize.assert_not_called()

    def test_confirmed_choice_resumes_existing_progress_with_same_frame(self):
        session = Session(1, "framepool")
        session.supply = Mock()
        session.preparation_flow = Mock()
        session.preparation_flow.resume.return_value = {"result": "success", "screen": "备战", "slots": {}}
        with patch("screens.supply.SupplyOperation") as operation:
            operation.return_value.execute.return_value = {"result": "success", "screen": "备战", "shop": "closed"}
            operation.return_value.image = "after"
            result = session.supply_action("choose", 4)
        session.preparation_flow.resume.assert_called_once_with("after")
        operation.return_value.save.assert_not_called()
        self.assertEqual(result["action"], "supply")
        self.assertEqual(result["option"], 4)

    def test_reader_is_cached_and_read_does_not_resume_prepare(self):
        session = Session(1, "framepool")
        session.preparation = Mock()
        session.preparation_flow = Mock()
        with patch("screens.supply.Supply") as reader, patch("screens.supply.SupplyOperation") as operation:
            operation.return_value.execute.return_value = {"result": "success", **observation()}
            operation.return_value.save.return_value = {"result": "success", **observation()}
            session.supply_action()
            session.supply_action("refresh")
        reader.assert_called_once_with(session.preparation)
        self.assertEqual(operation.call_count, 2)
        session.preparation_flow.resume.assert_not_called()


if __name__ == "__main__":
    unittest.main()

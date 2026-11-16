"""投资策略识别与动作编排测试。"""

from itertools import count
import unittest
from unittest.mock import Mock, call, patch

import numpy as np

from game.input import InputUncertain
from service import Session
from screens.strategy import CONFIRM, OPTIONS, REFRESH, Strategy, StrategyOperation


def observation(counts=(1, 1, 1), names=("策略甲", "策略乙", "策略丙")):
    return {"screen": "投资策略", "title": "请选择投资策略", "options": [
        {"slot": f"option_{i}", "name": name, "text": f"{name}效果", "refreshes_left": left}
        for i, (name, left) in enumerate(zip(names, counts), 1)], "selected": None, "needs_ai": True}


class StrategyReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = Strategy.__new__(Strategy)
        self.reader.preparation = Mock()
        self.reader.ocr_ready = True
        self.reader.resource = Mock()
        self.reader.tasker = Mock()
        self.reader.selected = Mock(return_value=None)
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_cards_keep_their_own_refresh_counts(self):
        self.reader.text = Mock(side_effect=["策略甲", "效果甲", "刷新次数0",
                                            "策略乙", "效果乙", "刷新次数 1",
                                            "策略丙", "效果丙", "刷新次数2"])
        result = self.reader.read(self.image)
        self.assertEqual([o["refreshes_left"] for o in result["options"]], [0, 1, 2])
        self.assertEqual([o["name"] for o in result["options"]], ["策略甲", "策略乙", "策略丙"])
        self.assertTrue(all(c.args[0] is self.image for c in self.reader.text.call_args_list))

    def test_partial_cards_keep_unknown_separate_from_zero(self):
        self.reader.text = Mock(side_effect=["", "效果甲", "刷新次数0",
                                            "策略乙", "", "刷新次数1",
                                            "策略丙", "效果丙", "刷新次数"])
        result = self.reader.read(self.image)
        self.assertTrue(all(o["unknown"] for o in result["options"]))
        self.assertEqual(result["options"][0]["refreshes_left"], 0)
        self.assertIsNone(result["options"][2]["refreshes_left"])

    def test_title_uses_template_and_checks_size(self):
        self.reader.score = Mock(return_value=0.9)
        self.assertTrue(self.reader.page(self.image))
        self.reader.score.return_value = 0.89
        self.assertFalse(self.reader.page(self.image))
        self.reader.score.reset_mock()
        with self.assertRaises(ValueError):
            self.reader.page(self.image[:720, :1280])
        self.reader.score.assert_not_called()

    def test_ocr_error_is_reported(self):
        self.reader.tasker.post_recognition.return_value.wait.return_value.get.return_value = None
        with self.assertRaisesRegex(RuntimeError, "OCR 详情缺失"):
            self.reader.text(self.image, (1, 2, 3, 4))

    def test_ocr_model_loads_once_after_page_detection(self):
        self.reader.ocr_ready = False
        self.reader.text = Mock(return_value="")
        self.reader.read(self.image)
        self.reader.read(self.image)
        self.reader.resource.use_cpu.assert_called_once()
        self.reader.resource.post_ocr_model.assert_called_once()

    def test_ocr_model_load_failure_is_reported(self):
        self.reader.ocr_ready = False
        self.reader.resource.post_ocr_model.return_value.wait.return_value.succeeded = False
        with self.assertRaisesRegex(RuntimeError, "模型加载失败"):
            self.reader.read(self.image)

    def test_open_shop_requires_both_markers(self):
        self.reader.preparation.header.return_value = {"layout": "open", "node": "1-1", "phase_box": [244, 59, 86, 22]}
        self.reader.preparation.score = Mock(return_value=0.95)
        with patch("screens.preparation.page", return_value={"result": "uncertain"}):
            self.assertEqual(self.reader.following(self.image)["shop"], "open")
            self.reader.preparation.score.return_value = 0.89
            self.assertIsNone(self.reader.following(self.image))


class StrategyOperationTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.supply_reader.return_value.page.return_value = False
        self.session.save_image.side_effect = lambda frame: {"screenshot": f"{frame}.png"}
        self.job = self.session.controller.post_screencap.return_value.wait.return_value
        self.job.succeeded = True
        self.job.get.return_value = "frame"
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True
        self.reader = Mock()
        self.reader.page.return_value = True
        self.reader.read.return_value = observation()
        self.op = StrategyOperation(self.session, self.reader)
        self.addCleanup(patch.stopall)
        patch("screens.strategy.sleep").start()
        patch("screens.strategy.monotonic", side_effect=count()).start()

    def test_read_returns_same_frame_and_sends_no_input(self):
        result = self.op.run()
        self.assertEqual(result["result"], "success")
        self.reader.read.assert_called_once_with("frame")
        self.session.save_image.assert_called_once_with("frame")
        self.session.controller.post_click.assert_not_called()

    def test_waits_for_title_and_complete_cards(self):
        partial = observation()
        partial["options"][1]["unknown"] = True
        self.job.get.side_effect = ["transition", "partial", "ready"]
        self.reader.page.side_effect = [False, True, True]
        self.reader.read.side_effect = [partial, observation()]
        result = self.op.run()
        self.assertEqual(result["screenshot"], "ready.png")
        self.assertEqual(self.reader.read.call_args_list, [call("partial"), call("ready")])

    def test_read_timeout_returns_current_frame_without_old_options(self):
        self.reader.page.side_effect = [True] + [False] * 20
        partial = observation()
        partial["options"][0]["unknown"] = True
        self.reader.read.return_value = partial
        result = self.op.run()
        self.assertEqual(result["result"], "uncertain")
        self.assertNotIn("options", result)
        self.assertEqual(result["screen"], "未知")

    def test_refresh_waits_for_count_and_full_text_with_one_click(self):
        partial = observation((0, 1, 1))
        partial["options"][0]["unknown"] = True
        self.reader.read.side_effect = [observation(), observation(), partial, observation((0, 1, 1))]
        result = self.op.run("refresh", 1)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["options"][0]["name"], "策略甲")
        self.session.controller.post_click.assert_called_once_with(*REFRESH[0])

    def test_zero_refresh_count_sends_no_input(self):
        self.reader.read.return_value = observation((0, 1, 1))
        self.assertEqual(self.op.run("refresh", 1)["result"], "failed")
        self.session.controller.post_click.assert_not_called()

    def test_unchanged_count_times_out_without_repeating_refresh(self):
        result = self.op.run("refresh", 2)
        self.assertEqual(result["result"], "uncertain")
        self.session.controller.post_click.assert_called_once_with(*REFRESH[1])

    def test_choose_waits_for_selection_then_returns_open_preparation(self):
        self.job.get.side_effect = ["before", "selected", "transition", "open"]
        self.reader.page.side_effect = [True, True, False, False]
        self.reader.selected.return_value = 3
        self.reader.following.side_effect = [None, {"result": "success", "screen": "备战", "shop": "open"}]
        result = self.op.run("choose", 3)
        self.assertEqual(result["screen"], "备战")
        self.assertEqual(result["shop"], "open")
        self.assertEqual(result["screenshot"], "open.png")
        self.assertEqual(self.session.controller.post_click.call_args_list, [call(*OPTIONS[2]), call(*CONFIRM)])
        self.reader.read.assert_called_once_with("before")

    def test_selection_mismatch_stops_before_confirm(self):
        self.reader.selected.return_value = 2
        result = self.op.run("choose", 3)
        self.assertEqual(result["result"], "uncertain")
        self.session.controller.post_click.assert_called_once_with(*OPTIONS[2])

    def test_same_choice_screen_after_confirm_is_uncertain(self):
        self.reader.selected.return_value = 1
        result = self.op.run("choose", 1)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(self.session.controller.post_click.call_count, 2)
        self.assertEqual(self.reader.read.call_count, 1)

    def test_new_choice_after_leaving_is_returned_to_ai(self):
        self.job.get.side_effect = ["before", "selected", "transition", "next-choice"]
        self.reader.page.side_effect = [True, True, False, True]
        self.reader.selected.return_value = 1
        self.reader.following.return_value = None
        self.reader.read.side_effect = [observation(), observation((0, 0, 0))]
        result = self.op.run("choose", 1)
        self.assertEqual(result["result"], "uncertain")
        self.assertTrue(result["needs_ai"])
        self.assertEqual(result["options"][2]["refreshes_left"], 0)

    def test_input_failure_returns_last_frame_without_confirm(self):
        self.session.controller.post_click.return_value.wait.return_value.succeeded = False
        result = self.op.run("choose", 1)
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.assertEqual(result["screenshot"], "frame.png")
        self.session.controller.post_click.assert_called_once()

    def test_ocr_error_after_refresh_is_uncertain_with_context(self):
        self.reader.read.side_effect = [observation(), RuntimeError("OCR 出错")]
        result = self.op.run("refresh", 1)
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("RuntimeError: OCR 出错", result["message"])
        self.session.controller.post_click.assert_called_once()

    def test_capture_failure_before_input_is_reported(self):
        self.job.succeeded = False
        with self.assertRaisesRegex(RuntimeError, "截图失败"):
            self.op.run("choose", 1)
        self.session.controller.post_click.assert_not_called()

    def test_capture_failure_after_refresh_is_uncertain(self):
        self.job.get.side_effect = ["before", RuntimeError("截图数据出错")]
        result = self.op.run("refresh", 1)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "before.png")

    def test_save_failure_after_input_is_uncertain(self):
        self.reader.read.side_effect = [observation(), observation((0, 1, 1))]
        self.session.save_image.side_effect = OSError("磁盘写入出错")
        with self.assertRaisesRegex(InputUncertain, "OSError"):
            self.op.run("refresh", 1)

    def test_preparation_wait_resumes_slots_from_confirmed_frame(self):
        self.reader.page.return_value = False
        self.reader.following.return_value = {"result": "success", "screen": "备战", "shop": "closed"}
        self.session.preparation.observe.return_value = {"result": "success", "slots": {"front_1": None}, "gold": 8}
        self.job.get.return_value = "ready"
        result = self.op.run("preparation", image="initial")
        self.assertEqual(result["slots"], {"front_1": None})
        self.session.preparation.observe.assert_called_once_with("ready")
        self.assertEqual(result["gold"], 8)
        self.reader.following.assert_called_once_with("ready")

    def test_preparation_wait_dispatches_supply_and_keeps_its_latest_frame(self):
        self.reader.page.return_value = False
        self.session.supply_reader.return_value.page.return_value = True
        with patch("screens.supply.SupplyOperation") as operation:
            operation.return_value.execute.return_value = {"result": "success", "screen": "补给"}
            operation.return_value.image = "complete-supply"
            result = self.op.save(self.op.execute("preparation", image="initial"))
        operation.return_value.execute.assert_called_once_with(image="frame")
        self.assertEqual(result["screen"], "补给")
        self.assertEqual(result["screenshot"], "complete-supply.png")
        self.reader.following.assert_not_called()


class StrategySessionTests(unittest.TestCase):
    def test_reader_is_cached_and_operation_is_fresh(self):
        session = Session(1, "framepool")
        session.preparation = Mock()
        with patch("screens.strategy.Strategy") as reader, patch("screens.strategy.StrategyOperation") as operation:
            result = {"result": "success", "screen": "投资策略"}
            operation.return_value.execute.return_value = result
            operation.return_value.save.return_value = result
            session.investment_strategy()
            session.investment_strategy("refresh", 2)
        reader.assert_called_once_with(session.preparation)
        self.assertEqual(operation.call_count, 2)
        operation.return_value.execute.assert_has_calls([call("read", None, None), call("refresh", 2, None)])

    def test_preparation_failure_passes_same_frame_to_dispatch(self):
        session = Session(1, "framepool")
        session.protagonist = Mock()
        session.protagonist.stage.return_value = None
        session.supply = Mock()
        session.supply.page.return_value = False
        session.preparation_frame = Mock(return_value="frame")
        session.preparation = Mock()
        session.preparation.recognize.return_value = {"result": "uncertain", "screen": "未知"}
        session.investment_strategy = Mock(return_value={"screen": "投资策略"})
        self.assertEqual(session.observe_preparation()["screen"], "投资策略")
        session.investment_strategy.assert_called_once_with("preparation", image="frame")


if __name__ == "__main__":
    unittest.main()

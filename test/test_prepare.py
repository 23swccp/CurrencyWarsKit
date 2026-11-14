"""备战入场、确认重试与策略恢复测试。"""

import unittest
from unittest.mock import Mock, call, patch

from game.input import InputUncertain
from flow.prepare import PreparationFlow
from service import Session
from game.slots import BLANK, SHOP_TOGGLE


def preparation(shop="closed"):
    return {"result": "success", "screen": "备战", "shop": shop}


def strategy(unknown=False):
    options = [{"name": "策略", "text": "效果", "refreshes_left": 1} for _ in range(3)]
    if unknown:
        options[0]["unknown"] = True
    return {"screen": "投资策略", "options": options, "needs_ai": True}


class PreparationFlowTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.save_image.side_effect = lambda image: {"screenshot": f"{image}.png"}
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True
        self.job = self.session.controller.post_screencap.return_value.wait.return_value
        self.job.succeeded = True
        self.job.get.side_effect = [f"frame-{i}" for i in range(1, 20)]
        self.flow = PreparationFlow(self.session)
        self.flow.protagonist.stage.return_value = None
        self.flow.reader.slots.return_value = {"slots": {"front_1": None}}
        self.flow.strategy.page.return_value = False
        self.layout = patch("flow.prepare.layout", return_value=preparation()).start()
        self.sleep = patch("flow.prepare.sleep").start()
        self.sweep = patch("flow.prepare.sweep").start()
        self.addCleanup(patch.stopall)

    def test_transition_retries_then_collects_and_reads_new_frame(self):
        self.layout.side_effect = [None, None, preparation(), preparation()]
        result = self.flow.enter()
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["screenshot"], "frame-4.png")
        self.assertEqual(result["slots"], {"front_1": None})
        self.assertEqual(self.sleep.call_args_list, [call(2), call(2)])
        self.sweep.assert_called_once_with(self.session.controller)
        self.flow.reader.slots.assert_called_once_with("frame-4")

    def test_three_misses_return_last_frame_after_two_blank_clicks(self):
        self.layout.return_value = None
        result = self.flow.enter()
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screen"], "未知")
        self.assertEqual(result["screenshot"], "frame-3.png")
        self.assertIn("3 次", result["message"])
        self.assertEqual(self.sleep.call_args_list, [call(2), call(2)])
        self.assertEqual(self.session.controller.post_click.call_args_list, [call(*BLANK)] * 2)
        self.sweep.assert_not_called()

    def test_open_shop_clicks_once_and_waits_for_closed_layout(self):
        self.layout.side_effect = [preparation("open"), preparation("open"), None,
                                   preparation(), preparation()]
        result = self.flow.enter()
        self.assertEqual(result["result"], "success")
        self.assertEqual(self.session.controller.post_click.call_args_list, [call(*SHOP_TOGGLE), call(*BLANK)])
        self.assertEqual(self.sleep.call_args_list, [call(2), call(2), call(2)])
        self.sweep.assert_called_once()
        self.flow.reader.slots.assert_called_once_with("frame-5")

    def test_shop_close_timeout_stops_before_collecting(self):
        self.layout.return_value = preparation("open")
        result = self.flow.enter()
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "frame-4.png")
        self.session.controller.post_click.assert_called_once_with(*SHOP_TOGGLE)
        self.sweep.assert_not_called()
        self.flow.reader.slots.assert_not_called()

    def test_choice_before_collecting_returns_options_to_ai(self):
        self.layout.return_value = None
        self.flow.strategy.page.return_value = True
        self.flow.strategy.read.return_value = strategy()
        result = self.flow.enter()
        self.assertEqual(result["screen"], "投资策略")
        self.assertTrue(result["needs_ai"])
        self.assertEqual(len(result["options"]), 3)
        self.sweep.assert_not_called()
        self.session.controller.post_click.assert_not_called()

    def test_incomplete_choice_retries_and_keeps_last_options(self):
        self.layout.return_value = None
        self.flow.strategy.page.return_value = True
        self.flow.strategy.read.return_value = strategy(unknown=True)
        result = self.flow.enter()
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screen"], "投资策略")
        self.assertTrue(result["options"][0]["unknown"])
        self.assertEqual(self.flow.strategy.read.call_count, 3)
        self.assertEqual(result["screenshot"], "frame-3.png")
        self.sweep.assert_not_called()
        self.session.controller.post_click.assert_not_called()

    def test_wait_clicks_at_two_four_six_seconds_and_caps_total_at_three(self):
        now = 0
        clicks = []
        def sleep(seconds):
            nonlocal now
            now += seconds
        click_job = self.session.controller.post_click.return_value
        def click(*point):
            clicks.append((now, point))
            return click_job
        self.sleep.side_effect = sleep
        self.session.controller.post_click.side_effect = click
        self.layout.return_value = None
        result = self.flow.enter(delay=8)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(clicks, [(2, BLANK), (4, BLANK), (6, BLANK)])
        self.assertEqual(now, 12)
        self.assertEqual(self.job.get.call_count, 6)
        self.sweep.assert_not_called()

    def test_wait_stops_clicking_after_preparation_and_keeps_remaining_delay(self):
        self.layout.side_effect = [None, preparation(), preparation(), preparation()]
        result = self.flow.enter(delay=8)
        self.assertEqual(result["result"], "success")
        self.assertEqual(self.sleep.call_args_list, [call(2), call(2), call(4)])
        self.session.controller.post_click.assert_called_once_with(*BLANK)
        self.flow.reader.slots.assert_called_once_with("frame-4")
        self.flow.strategy.read.assert_not_called()

    def test_strategy_title_stops_wait_clicks_before_reading_text(self):
        self.layout.return_value = None
        self.flow.strategy.page.return_value = True
        self.flow.strategy.read.return_value = strategy()
        result = self.flow.enter(delay=8)
        self.assertEqual(result["screen"], "投资策略")
        self.assertEqual(self.sleep.call_args_list, [call(2), call(6)])
        self.flow.strategy.read.assert_called_once_with("frame-2")
        self.session.controller.post_click.assert_not_called()
        self.sweep.assert_not_called()

    def test_blank_click_failure_returns_latest_frame_and_stops_wait(self):
        self.layout.return_value = None
        self.session.controller.post_click.return_value.wait.return_value.succeeded = False
        result = self.flow.enter(delay=8)
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.assertEqual(result["screenshot"], "frame-1.png")
        self.session.controller.post_click.assert_called_once_with(*BLANK)
        self.assertEqual(self.sleep.call_args_list, [call(2)])
        self.sweep.assert_not_called()

    def test_choice_after_collecting_resumes_without_another_sweep(self):
        self.layout.side_effect = [preparation(), None]
        self.flow.strategy.page.return_value = True
        self.flow.strategy.read.return_value = strategy()
        self.assertEqual(self.flow.enter()["screen"], "投资策略")
        self.layout.side_effect = None
        result = self.flow.resume("chosen")
        self.assertEqual(result["screen"], "备战")
        self.sweep.assert_called_once()
        self.flow.reader.slots.assert_called_once_with("chosen")
        self.assertEqual(result["screenshot"], "chosen.png")

    def test_choice_before_collecting_resumes_with_one_sweep(self):
        self.layout.return_value = None
        self.flow.strategy.page.return_value = True
        self.flow.strategy.read.return_value = strategy()
        self.flow.enter()
        self.layout.return_value = preparation()
        result = self.flow.resume("chosen")
        self.assertEqual(result["screen"], "备战")
        self.sweep.assert_called_once()
        self.flow.reader.slots.assert_called_once_with("frame-2")

    def test_new_entry_starts_a_new_collection_round(self):
        self.flow.enter()
        self.flow.enter()
        self.assertEqual(self.sweep.call_count, 2)

    def test_post_collection_confirmation_retries_without_repeating_input(self):
        self.layout.side_effect = [preparation(), None, None, None]
        result = self.flow.enter()
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "frame-4.png")
        self.sweep.assert_called_once()
        self.flow.reader.slots.assert_not_called()

    def test_post_collection_open_layout_keeps_slots_pending(self):
        self.layout.side_effect = [preparation(), *[preparation("open")] * 3]
        result = self.flow.enter()
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["shop"], "open")
        self.sweep.assert_called_once()
        self.session.controller.post_click.assert_not_called()
        self.flow.reader.slots.assert_not_called()

    def test_shop_click_failure_returns_context_and_stops(self):
        self.layout.return_value = preparation("open")
        self.session.controller.post_click.return_value.wait.return_value.succeeded = False
        result = self.flow.enter()
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.session.controller.post_click.assert_called_once()
        self.sweep.assert_not_called()

    def test_capture_failure_after_sweep_returns_last_observation(self):
        self.job.get.side_effect = ["before", RuntimeError("截图数据出错")]
        result = self.flow.enter()
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("RuntimeError: 截图数据出错", result["message"])
        self.assertEqual(result["screenshot"], "before.png")
        self.sweep.assert_called_once()

    def test_initial_capture_failure_reports_error(self):
        self.job.succeeded = False
        with self.assertRaisesRegex(RuntimeError, "备战入场截图失败"):
            self.flow.enter()
        self.sweep.assert_not_called()

    def test_save_failure_after_sweep_is_input_uncertain(self):
        self.session.save_image.side_effect = OSError("保存失败")
        with self.assertRaisesRegex(InputUncertain, "OSError"):
            self.flow.enter()
        self.sweep.assert_called_once()


class PreparationSessionTests(unittest.TestCase):
    def test_main_character_entry_opens_before_collecting(self):
        session = Mock()
        flow = PreparationFlow(session)
        flow.protagonist.stage.return_value = "entry"
        session.save_image.side_effect = lambda image: {"screenshot": f"{image}.png"}
        with patch("flow.prepare.ProtagonistOperation") as operation, patch("flow.prepare.sweep") as sweep:
            operation.return_value.execute.return_value = {"result": "success", "screen": "选择主角", "stage": "selection"}
            operation.return_value.image = "options"
            operation.return_value.clicked = True
            result = flow.enter("entry")
        operation.return_value.execute.assert_called_once_with("open", image="entry")
        self.assertEqual(result["screenshot"], "options.png")
        sweep.assert_not_called()
        flow.reader.slots.assert_not_called()

    def test_main_character_title_stops_blank_clicking_during_wait(self):
        session = Mock()
        flow = PreparationFlow(session)
        flow.protagonist.stage.return_value = "selection"
        choice = {"screen": "选择主角", "stage": "selection", "needs_ai": True,
                  "gold": 46, "refreshes_left": 6, "options": [{"name": "光环", "text": "效果"}]}
        flow.protagonist.read.return_value = choice
        session.controller.post_screencap.return_value.wait.return_value.get.return_value = "options"
        session.save_image.return_value = {"screenshot": "options.png"}
        with patch("flow.prepare.sleep") as sleep, patch("flow.prepare.sweep") as sweep:
            result = flow.enter(delay=8)
        self.assertEqual(result["screen"], "选择主角")
        self.assertEqual(sleep.call_args_list, [call(2), call(6)])
        session.controller.post_click.assert_not_called()
        sweep.assert_not_called()

    def test_entry_is_cached_and_publishes_completed_stage(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        result = {"result": "success", "screen": "备战"}
        with patch("flow.prepare.PreparationFlow") as flow:
            flow.return_value.enter.return_value = result
            session.enter_preparation()
            session.enter_preparation("frame")
        flow.assert_called_once_with(session)
        self.assertEqual(flow.return_value.enter.call_args_list, [call(None, 0), call("frame", 0)])
        session.battle.begin.assert_has_calls([call(result), call(result)])

    def test_strategy_choice_resumes_existing_collection_progress(self):
        session = Session(1, "framepool")
        session.strategy = Mock()
        session.preparation_flow = Mock()
        session.preparation_flow.resume.return_value = {"result": "success", "screen": "备战", "slots": {}}
        with patch("screens.strategy.StrategyOperation") as operation:
            operation.return_value.execute.return_value = preparation()
            operation.return_value.image = "selected-frame"
            result = session.investment_strategy("choose", 2)
        session.preparation_flow.resume.assert_called_once_with("selected-frame")
        operation.return_value.save.assert_not_called()
        self.assertEqual(result["action"], "strategy")
        self.assertEqual(result["option"], 2)


if __name__ == "__main__":
    unittest.main()

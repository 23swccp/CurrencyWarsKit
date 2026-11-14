"""出战识别、等待时限与输入结果测试。"""

import unittest
from threading import Lock
from unittest.mock import Mock, call, patch

import numpy as np

from screens.battle import Battle, CONTINUE_BUTTON, Monitor, START, continue_battle, finish, start
from game.input import InputUncertain
from service import Session


class BattleReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = Battle.__new__(Battle)
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def scores(self, **values):
        self.reader.score = Mock(side_effect=lambda image, name, roi: values.get(name[:-4], 0.2))

    def test_flag_or_intro_confirms_entry(self):
        for marker in ("flag", "intro"):
            with self.subTest(marker=marker):
                self.scores(**{marker: 0.9})
                result = self.reader.recognize(self.image)
                self.assertEqual(result["screen"], "战斗")
                self.assertEqual(result["result"], "success")
                self.assertTrue(all(c.args[0] is self.image for c in self.reader.score.call_args_list))

    def test_entry_without_either_marker_remains_uncertain(self):
        self.scores(flag=0.89, intro=0.89)
        self.assertEqual(self.reader.recognize(self.image)["result"], "uncertain")

    def test_continue_button_takes_priority_at_entry(self):
        self.scores(**{"continue": 0.9, "flag": 1, "intro": 1})
        result = self.reader.recognize(self.image)
        self.assertEqual(result["screen"], "本场结算")
        self.assertEqual(self.reader.score.call_count, 1)

    def test_size_is_checked_before_matching(self):
        self.reader.score = Mock()
        with self.assertRaisesRegex(ValueError, "1920×1080"):
            self.reader.recognize(self.image[:720, :1280])
        self.reader.score.assert_not_called()

    def test_preparation_failure_stops_before_button_match(self):
        self.reader.score = Mock()
        with patch("screens.battle.preparation_page", return_value={"result": "uncertain", "screen": "未知"}):
            self.assertEqual(self.reader.ready(self.image)["result"], "failed")
        self.reader.score.assert_not_called()

    def test_start_button_requires_visible_text(self):
        self.reader.score = Mock(return_value=0.89)
        with patch("screens.battle.preparation_page", return_value={"result": "success", "screen": "备战"}):
            result = self.reader.ready(self.image)
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["anchors"], {"button": 0.89})

    def test_image_controller_connection_failure_is_reported(self):
        with patch("screens.battle.ImageController") as controller:
            controller.return_value.post_connection.return_value.wait.return_value.succeeded = False
            with self.assertRaisesRegex(RuntimeError, "图片控制器连接失败"):
                Battle()


class BattleOperationTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.captures = []
        self.session = Mock()
        self.session.save_image.side_effect = lambda image: {"screenshot": f"{image}.png"}
        self.job = self.session.controller.post_screencap.return_value.wait.return_value
        self.job.succeeded = True
        self.job.get.side_effect = self.frame
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True
        self.reader = Mock()
        self.reader.ready.return_value = {"result": "success", "screen": "备战"}
        self.reader.recognize.return_value = {"result": "uncertain", "screen": "未知"}
        self.clock_patch = patch("screens.battle.monotonic", side_effect=lambda: self.now)
        self.sleep_patch = patch("screens.battle.sleep", side_effect=self.advance)
        self.clock_patch.start()
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.sleep_patch.stop)

    def advance(self, seconds):
        self.now += seconds

    def frame(self):
        self.captures.append(self.now)
        return f"frame-{len(self.captures)}"

    def test_first_observation_waits_two_seconds_and_clicks_once(self):
        self.reader.recognize.return_value = {"result": "success", "screen": "战斗"}
        result = start(self.session, self.reader)
        self.assertEqual(result["screen"], "战斗")
        self.assertEqual(result["screenshot"], "frame-2.png")
        self.assertEqual(self.captures, [0, 2])
        self.sleep.assert_called_once_with(2)
        self.session.controller.post_click.assert_called_once_with(*START)
        self.session.save_image.assert_called_once_with("frame-2")

    def test_timeout_stops_at_ten_seconds_with_five_checks(self):
        result = start(self.session, self.reader)
        self.assertEqual(result["result"], "uncertain")
        self.assertTrue(result["needs_ai"])
        self.assertEqual(result["screenshot"], "frame-6.png")
        self.assertEqual(self.captures, [0, 2, 4, 6, 8, 10])
        self.assertEqual(self.reader.recognize.call_count, 5)
        self.session.controller.post_click.assert_called_once_with(*START)
        self.session.controller.post_click_key.assert_not_called()
        self.session.save_image.assert_called_once_with("frame-6")

    def test_final_wait_uses_remaining_budget(self):
        def recognize(image):
            self.now += 0.25
            return {"result": "uncertain", "screen": "未知"}
        self.reader.recognize.side_effect = recognize
        start(self.session, self.reader)
        self.assertEqual(self.captures, [0, 2, 4.25, 6.5, 8.75, 10])
        self.assertEqual(self.sleep.call_args_list, [call(2), call(2), call(2), call(2), call(1)])

    def test_tenth_second_match_is_accepted(self):
        self.reader.recognize.side_effect = [{"result": "uncertain", "screen": "未知"}] * 4 + [
            {"result": "success", "screen": "战斗"}]
        self.assertEqual(start(self.session, self.reader)["result"], "success")
        self.assertEqual(self.now, 10)

    def test_settlement_can_arrive_before_battle_match(self):
        self.reader.recognize.return_value = {"result": "success", "screen": "本场结算", "needs_ai": True}
        result = start(self.session, self.reader)
        self.assertEqual(result["screen"], "本场结算")
        self.assertEqual(result["result"], "success")

    def test_unconfirmed_preparation_sends_no_input(self):
        self.reader.ready.return_value = {"result": "failed", "screen": "未知"}
        result = start(self.session, self.reader)
        self.assertEqual(result["result"], "failed")
        self.session.controller.post_click.assert_not_called()
        self.reader.recognize.assert_not_called()
        self.sleep.assert_not_called()

    def test_click_failure_returns_last_observation(self):
        self.session.controller.post_click.return_value.wait.return_value.succeeded = False
        result = start(self.session, self.reader)
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.assertEqual(result["screenshot"], "frame-1.png")
        self.assertEqual(self.captures, [0])
        self.sleep.assert_not_called()
        self.session.controller.post_click.assert_called_once()

    def test_capture_failure_before_click_is_reported(self):
        self.job.succeeded = False
        with self.assertRaisesRegex(RuntimeError, "截图失败"):
            start(self.session, self.reader)
        self.session.controller.post_click.assert_not_called()

    def test_capture_failure_after_click_is_uncertain(self):
        self.job.get.side_effect = ["before", RuntimeError("截图数据出错")]
        result = start(self.session, self.reader)
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("RuntimeError: 截图数据出错", result["message"])
        self.assertEqual(result["screenshot"], "before.png")
        self.session.controller.post_click.assert_called_once()

    def test_recognition_error_after_click_preserves_context(self):
        self.reader.recognize.side_effect = ValueError("画面尺寸改变")
        result = start(self.session, self.reader)
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("ValueError: 画面尺寸改变", result["message"])
        self.assertEqual(result["screenshot"], "frame-2.png")

    def test_save_error_after_click_is_uncertain(self):
        self.reader.recognize.return_value = {"result": "success", "screen": "战斗"}
        self.session.save_image.side_effect = OSError("截图写入出错")
        with self.assertRaisesRegex(InputUncertain, "OSError"):
            start(self.session, self.reader)
        self.session.controller.post_click.assert_called_once()

    def test_session_caches_reader(self):
        session = Session(1, "framepool")
        with patch("screens.battle.Battle") as reader, patch("screens.battle.start") as execute:
            session.start_battle()
            session.start_battle()
        reader.assert_called_once_with()
        self.assertEqual(execute.call_args_list, [call(session, reader.return_value)] * 2)


class BattleFinishTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.reader = Mock()
        self.session.save_image.return_value = {"screenshot": "settlement.png"}
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True
        self.result = {"result": "success", "screen": "备战", "screenshot": "prepared.png", "slots": {}}
        self.session.enter_preparation.return_value = self.result
        self.sleep = patch("screens.battle.sleep").start()
        self.addCleanup(patch.stopall)

    def test_continue_clicks_once_waits_eight_seconds_then_hands_off(self):
        result = finish(self.session, self.reader, "settlement")
        self.session.controller.post_click.assert_called_once_with(*CONTINUE_BUTTON)
        self.sleep.assert_not_called()
        self.session.enter_preparation.assert_called_once_with(delay=8)
        self.assertEqual(result["screenshot"], "prepared.png")
        self.assertEqual(result["action"], "continue-battle")
        self.assertEqual(self.reader.begin.call_args_list, [
            call({"result": "success", "screen": "等待备战"}), call(result)])

    def test_preparation_confirmation_failure_does_not_repeat_continue(self):
        self.session.enter_preparation.return_value = {"result": "uncertain", "screen": "未知"}
        self.assertEqual(finish(self.session, self.reader, "settlement")["result"], "uncertain")
        self.session.controller.post_click.assert_called_once()
        self.session.enter_preparation.assert_called_once()

    def test_click_failure_stops_and_preserves_settlement_context(self):
        self.session.controller.post_click.return_value.wait.return_value.succeeded = False
        result = finish(self.session, self.reader, "settlement")
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("InputUncertain", result["message"])
        self.assertEqual(result["screenshot"], "settlement.png")
        self.session.enter_preparation.assert_not_called()
        self.sleep.assert_not_called()

    def test_manual_continue_checks_current_button_before_input(self):
        self.reader.recognize.return_value = {"result": "uncertain", "screen": "未知"}
        with patch("screens.battle.capture", return_value="current"):
            result = continue_battle(self.session, self.reader)
        self.assertEqual(result["result"], "failed")
        self.reader.recognize.assert_called_once_with("current")
        self.session.controller.post_click.assert_not_called()


class BattleStateTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.reader = Battle.__new__(Battle)
        self.reader.state_lock = Lock()
        self.reader.state = {"result": "uncertain", "screen": "未知"}
        self.reader.check_after = None
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")
        self.reader.score = Mock(return_value=0.2)
        timer = patch("screens.battle.monotonic", side_effect=lambda: self.now)
        timer.start()
        self.addCleanup(timer.stop)
        self.reader.begin({"result": "success", "screen": "战斗"})

    def observe_at(self, now):
        self.now = now
        return self.reader.observe(self.image)

    def test_hidden_flag_keeps_battle_until_five_seconds(self):
        for now in (2, 4):
            result = self.observe_at(now)
            self.assertEqual(result["screen"], "战斗")
            self.assertEqual(result["result"], "success")
        self.assertTrue(all(c.args[1] == "flag.png" for c in self.reader.score.call_args_list))
        self.reader.score.side_effect = [0.2, 0.95]
        result = self.observe_at(5)
        self.assertEqual(result["screen"], "本场结算")
        self.assertTrue(result["needs_ai"])
        self.assertEqual(self.reader.current()["screen"], "本场结算")
        self.assertTrue(all(c.args[0] is self.image for c in self.reader.score.call_args_list))

    def test_visible_flag_restarts_window(self):
        self.reader.score.return_value = 0.95
        self.observe_at(4)
        self.reader.score.return_value = 0.2
        self.reader.score.reset_mock()
        self.observe_at(8)
        self.assertEqual(self.reader.score.call_count, 1)
        self.reader.score.side_effect = [0.2, 0.95]
        self.assertEqual(self.observe_at(9)["screen"], "本场结算")

    def test_missing_continue_starts_another_five_second_window(self):
        self.assertEqual(self.observe_at(5)["screen"], "战斗")
        self.assertEqual(self.reader.score.call_count, 2)
        self.reader.score.reset_mock()
        for now in (7, 9):
            self.observe_at(now)
        self.assertEqual(self.reader.score.call_count, 2)
        self.reader.score.side_effect = [0.2, 0.95]
        self.assertEqual(self.observe_at(10)["screen"], "本场结算")

    def test_flag_takes_priority_during_monitoring(self):
        self.reader.score.return_value = 0.95
        self.assertEqual(self.observe_at(5)["screen"], "战斗")
        self.reader.score.assert_called_once()

    def test_final_poll_uses_window_remainder(self):
        self.observe_at(4)
        self.assertEqual(self.reader.next_delay(), 1)
        self.observe_at(5)
        self.assertEqual(self.reader.next_delay(), 2)

    def test_monitor_error_publishes_context(self):
        self.reader.fail(RuntimeError("识别详情缺失"))
        result = self.reader.current()
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screen"], "未知")
        self.assertIn("RuntimeError: 识别详情缺失", result["message"])


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(1, "framepool")
        self.session.controller = Mock()
        self.job = self.session.controller.post_screencap.return_value.wait.return_value
        self.job.succeeded = True
        self.job.get.return_value = "frame"
        self.reader = Mock()
        self.reader.current.return_value = {"screen": "战斗"}
        self.reader.observe.return_value = {"screen": "战斗"}
        self.reader.next_delay.return_value = 1
        self.monitor = Monitor.__new__(Monitor)
        self.monitor.session = self.session
        self.monitor.reader = self.reader

    def test_tick_uses_operation_lock_and_leaves_it_free(self):
        self.assertEqual(self.monitor.tick(), 1)
        self.reader.observe.assert_called_once_with("frame")
        self.assertTrue(self.session.operation_lock.acquire(blocking=False))
        self.session.operation_lock.release()

    def test_cli_operation_skips_monitor_capture(self):
        with self.session.operation_lock:
            self.assertEqual(self.monitor.tick(), 2)
        self.session.controller.post_screencap.assert_not_called()
        self.reader.observe.assert_not_called()

    def test_preparation_stops_further_captures(self):
        self.reader.current.return_value = {"screen": "备战"}
        self.assertEqual(self.monitor.tick(), 2)
        self.session.controller.post_screencap.assert_not_called()

    def test_settlement_uses_verified_frame_and_serializes_entire_handoff(self):
        self.reader.observe.return_value = {"screen": "本场结算"}
        def handoff(image):
            self.assertEqual(image, "frame")
            self.assertTrue(self.session.operation_lock.locked())
            self.assertEqual(self.session.status()["status"], "busy")
            self.assertEqual(self.session.operation, "continue-battle")
            self.reader.current.return_value = {"screen": "备战"}
        self.session.finish_battle = Mock(side_effect=handoff)
        self.monitor.tick()
        self.monitor.tick()
        self.session.finish_battle.assert_called_once_with("frame")
        self.session.controller.post_screencap.assert_called_once()
        self.assertIsNone(self.session.operation)
        self.assertFalse(self.session.operation_lock.locked())

    def test_changed_settlement_publishes_fresh_observation_without_input(self):
        self.reader.current.return_value = {"screen": "本场结算"}
        result = {"result": "uncertain", "screen": "未知"}
        self.reader.recognize.return_value = result
        self.session.finish_battle = Mock()
        self.monitor.tick()
        self.reader.begin.assert_called_once_with(result)
        self.session.finish_battle.assert_not_called()

    def test_capture_error_records_state_and_releases_lock(self):
        self.job.succeeded = False
        self.reader.fail.return_value = "战斗监测出错：RuntimeError: 战斗截图失败"
        self.assertEqual(self.monitor.tick(), 2)
        self.assertIn("RuntimeError", self.session.error)
        self.reader.fail.assert_called_once()
        self.assertFalse(self.session.operation_lock.locked())

    def test_loop_uses_deadline_delay(self):
        self.monitor.stopped = Mock()
        self.monitor.stopped.wait.side_effect = [False, False, True]
        self.monitor.tick = Mock(side_effect=[1, 2])
        self.monitor.run()
        self.assertEqual(self.monitor.stopped.wait.call_args_list, [call(2), call(1), call(2)])

    def test_close_wakes_waiting_thread(self):
        self.reader.current.return_value = {"screen": "未知"}
        monitor = Monitor(self.session, self.reader)
        monitor.close()
        self.assertFalse(monitor.thread.is_alive())
        self.session.controller.post_screencap.assert_not_called()


class BattleSessionTests(unittest.TestCase):
    def test_completed_handoff_returns_saved_result_without_new_input(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        result = {"result": "success", "screen": "备战", "action": "continue-battle", "screenshot": "prepared.png"}
        session.battle.current.return_value = result
        with patch("screens.battle.capture") as capture:
            self.assertEqual(session.observe_battle(), result)
        capture.assert_not_called()

    def test_idle_service_can_report_battle_stage(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        session.battle.current.return_value = {"screen": "战斗"}
        self.assertEqual(session.status()["status"], "idle")
        self.assertEqual(session.status()["screen"], "战斗")

    def test_confirmed_start_launches_monitor_and_returns(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        result = {"result": "success", "screen": "战斗"}
        with patch("screens.battle.start", return_value=result), patch("screens.battle.Monitor") as monitor:
            self.assertEqual(session.start_battle(), result)
            session.watch_battle()
        monitor.assert_called_once_with(session, session.battle)

    def test_uncertain_start_does_not_launch_monitor(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        with patch("screens.battle.start", return_value={"result": "uncertain", "screen": "未知"}), \
                patch("screens.battle.Monitor") as monitor:
            session.start_battle()
        monitor.assert_not_called()

    def test_preparation_during_known_battle_returns_battle_observation(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        session.battle.current.return_value = {"screen": "战斗"}
        session.observe_battle = Mock(return_value={"screen": "战斗"})
        session.preparation_frame = Mock()
        self.assertEqual(session.observe_preparation()["screen"], "战斗")
        session.preparation_frame.assert_not_called()

    def test_stop_signals_monitor(self):
        session = Session(1, "framepool")
        session.battle_monitor = Mock()
        session.stop()
        session.battle_monitor.stop.assert_called_once_with()

    def test_preparation_after_settlement_updates_recorded_stage(self):
        session = Session(1, "framepool")
        session.protagonist = Mock()
        session.protagonist.stage.return_value = None
        session.battle = Mock()
        session.battle.current.return_value = {"screen": "本场结算"}
        session.preparation = Mock()
        observation = {"result": "success", "screen": "备战", "slots": {}}
        session.preparation.recognize.return_value = observation
        session.preparation_frame = Mock(return_value="frame")
        session.save_image = Mock(return_value={"screenshot": "frame.png"})
        self.assertEqual(session.observe_preparation()["screen"], "备战")
        session.battle.begin.assert_called_once_with(observation)

    def test_wrong_observation_command_keeps_battle_stage(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        session.strategy = Mock()
        with patch("screens.strategy.StrategyOperation") as operation:
            result = {"result": "uncertain", "screen": "未知"}
            operation.return_value.execute.return_value = result
            operation.return_value.save.return_value = result
            session.investment_strategy()
        session.battle.begin.assert_not_called()

    def test_battle_observation_preserves_hidden_flag_state(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        session.battle.current.return_value = {"screen": "战斗"}
        observation = {"result": "success", "screen": "战斗", "anchors": {"flag": 0.2}}
        session.battle.observe.return_value = observation
        session.save_image = Mock(return_value={"screenshot": "animation.png"})
        with patch("screens.battle.capture", return_value="animation"), patch("screens.battle.Monitor"):
            self.assertEqual(session.observe_battle()["screen"], "战斗")
        session.battle.observe.assert_called_once_with("animation")
        session.battle.recognize.assert_not_called()

    def test_battle_observation_can_take_over_existing_battle(self):
        session = Session(1, "framepool")
        session.battle = Mock()
        session.battle.current.return_value = {"screen": "未知"}
        observation = {"result": "success", "screen": "战斗"}
        session.battle.recognize.return_value = observation
        session.save_image = Mock(return_value={"screenshot": "frame.png"})
        with patch("screens.battle.capture", return_value="frame"), patch("screens.battle.Monitor") as monitor:
            self.assertEqual(session.observe_battle()["screen"], "战斗")
        session.battle.begin.assert_called_once_with(observation)
        monitor.assert_called_once_with(session, session.battle)


if __name__ == "__main__":
    unittest.main()

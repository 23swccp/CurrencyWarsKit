"""后台单轮补领、商店暂停与前台互斥。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import Mock, patch

from flow.rewards import Collector
from service import Session


def preparation(shop="closed"):
    return {"result": "success", "screen": "备战", "shop": shop}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(1, "framepool")
        self.session.controller = Mock()
        self.session.preparation = Mock()
        self.session.preparation.empty.return_value = True
        self.session.preparation.card.return_value = None
        self.session.preparation.capacity_warning.return_value = {}
        self.session.preparation.observe.return_value = {"result": "success", "gold": 80,
                "slots": {**{f"bench_{i}": None for i in range(1, 10)}, "bench_8": {"name": "刃"}}}
        self.session.preparation_frame = Mock(side_effect=["before", "after", "third", "fourth"])
        self.session.preparation_scene = Mock(return_value=preparation())
        self.session.save_image = Mock(side_effect=lambda image: {"screenshot": f"{image}.png"})
        self.collector = Collector.__new__(Collector)
        self.collector.session = self.session
        self.collector.event = Event()
        self.collector.stopped = Event()
        self.collector.pending = True
        self.collector.state = "waiting"
        self.collector.latest = None
        self.session.collector = self.collector
        self.sweep = patch("flow.rewards.sweep").start()
        self.addCleanup(patch.stopall)

    def test_one_request_sweeps_once_and_publishes_new_frame(self):
        self.collector.tick()
        self.assertEqual(self.collector.summary(), {"state": "done", "pending": False})
        self.sweep.assert_called_once_with(self.session.controller)
        result = self.session.observe_rewards()
        self.assertEqual(result["screenshot"], "after.png")
        self.assertEqual(result["gold"], 80)
        self.assertEqual(result["slots"]["bench_8"], {"name": "刃"})
        self.session.preparation.observe.assert_called_once_with("after")
        self.collector.tick()
        self.assertEqual(self.sweep.call_count, 1)

    def test_requests_coalesce_and_do_not_create_an_automatic_loop(self):
        self.collector.request()
        self.collector.request()
        self.collector.tick()
        self.collector.tick()
        self.sweep.assert_called_once()

    def test_open_shop_pauses_pending_collection_and_closing_resumes(self):
        self.session.preparation_scene.return_value = preparation("open")
        self.collector.tick()
        self.assertEqual(self.collector.summary(), {"state": "paused", "pending": True})
        self.sweep.assert_not_called()
        self.session.preparation.empty.assert_not_called()
        self.session.preparation.capacity_warning.assert_not_called()
        self.session.preparation_scene.return_value = preparation()
        self.collector.tick()
        self.assertEqual(self.collector.state, "done")
        self.sweep.assert_called_once()

    def test_choices_battle_and_unknown_page_pause_without_input(self):
        for screen in ("武装箱选择", "选择主角", "投资策略", "补给", "战斗", "未知"):
            with self.subTest(screen=screen):
                self.session.preparation_frame.side_effect = None
                self.session.preparation_frame.return_value = "frame"
                self.session.preparation_scene.return_value = {"result": "uncertain", "screen": screen}
                self.collector.tick()
                self.assertTrue(self.collector.pending)
                self.assertEqual(self.collector.state, "paused")
        self.sweep.assert_not_called()

    def test_full_bench_pauses_until_a_later_verified_vacancy(self):
        self.session.preparation.empty.return_value = False
        self.collector.tick()
        self.assertEqual(self.session.preparation.empty.call_count, 9)
        self.assertEqual(self.collector.latest["reason"], "bench_full")
        self.assertTrue(self.collector.pending)
        self.sweep.assert_not_called()
        self.session.preparation.empty.return_value = True
        self.collector.tick()
        self.sweep.assert_called_once()

    def test_choice_after_sweep_returns_choice_without_repeating_collection(self):
        self.session.preparation_scene.side_effect = [preparation(), {"result": "success", "screen": "武装箱选择"}]
        self.collector.tick()
        self.assertEqual(self.collector.latest["screen"], "武装箱选择")
        self.assertTrue(self.collector.latest["needs_ai"])
        self.assertFalse(self.collector.pending)
        self.session.preparation.observe.assert_not_called()
        self.sweep.assert_called_once()

    def test_new_full_bench_is_reported_after_one_sweep(self):
        self.session.preparation.observe.return_value = {"result": "success", "reason": "bench_full", "needs_ai": True,
                "slots": {f"bench_{i}": {"name": "角色"} for i in range(1, 10)}}
        self.collector.tick()
        self.assertEqual(self.collector.latest["reason"], "bench_full")
        self.assertTrue(self.collector.latest["needs_ai"])
        self.assertFalse(self.collector.pending)
        self.assertEqual(self.collector.state, "done")
        self.sweep.assert_called_once()

    def test_warning_with_vacancy_pauses_and_requires_sale(self):
        self.session.preparation.capacity_warning.return_value = {"reason": "character_overflow", "needs_ai": True,
                "required_action": "sell", "message": "角色卡溢出，请 AI 选择并售卖角色"}
        self.collector.tick()
        result = self.collector.latest
        self.assertEqual(result["reason"], "character_overflow")
        self.assertEqual(result["required_action"], "sell")
        self.assertIn("角色卡溢出", result["message"])
        self.assertEqual(result["screenshot"], "before.png")
        self.assertTrue(self.collector.pending)
        self.assertEqual(self.collector.state, "paused")
        self.session.preparation.empty.assert_not_called()
        self.session.preparation.observe.assert_not_called()
        self.session.preparation.card.assert_not_called()
        self.session.preparation.capacity_warning.assert_called_once_with("before")
        self.sweep.assert_not_called()

    def test_overflow_after_sweep_preserves_sale_request_and_message(self):
        self.session.preparation.observe.return_value = {"result": "success", "reason": "character_overflow",
                "required_action": "sell", "needs_ai": True, "message": "角色卡溢出，请 AI 选择并售卖角色"}
        self.collector.tick()
        self.assertEqual(self.collector.latest["required_action"], "sell")
        self.assertIn("角色卡溢出", self.collector.latest["message"])
        self.assertFalse(self.collector.pending)
        self.sweep.assert_called_once()

    def test_background_error_preserves_context_and_stops_automatic_attempts(self):
        self.sweep.side_effect = RuntimeError("移动任务失败")
        self.collector.tick()
        self.assertEqual(self.collector.state, "failed")
        self.assertFalse(self.collector.pending)
        self.assertEqual(self.collector.latest["screenshot"], "before.png")
        self.assertIn("RuntimeError: 移动任务失败", self.session.error)
        self.collector.tick()
        self.sweep.assert_called_once()

    def test_foreground_operation_or_waiter_prevents_background_start(self):
        with self.session.operation_lock:
            self.collector.tick()
        self.session.foreground_waiters = 1
        self.collector.tick()
        self.session.preparation_frame.assert_not_called()
        self.sweep.assert_not_called()

    def test_foreground_shop_request_waits_for_sweep_and_release(self):
        entered, release, waiting, opened = Event(), Event(), Event(), Event()
        order = []
        def sweep(controller):
            order.append("sweep-start")
            entered.set()
            if not release.wait(5):
                raise RuntimeError("测试未释放收集")
            order.append("sweep-released")
        self.sweep.side_effect = sweep
        condition_wait = self.session.condition.wait
        def wait(timeout=None):
            waiting.set()
            return condition_wait(timeout)
        def open_shop():
            order.append("shop-open")
            opened.set()
            return {"shop": "open"}
        with ThreadPoolExecutor() as executor, patch.object(self.session.condition, "wait", side_effect=wait):
            background = executor.submit(self.collector.tick)
            self.assertTrue(entered.wait(5))
            foreground = executor.submit(self.session.run, "shop-open", open_shop)
            try:
                self.assertTrue(waiting.wait(5))
                self.assertFalse(opened.is_set())
                self.assertEqual(self.session.status()["operation"], "collect-background")
                self.assertEqual(self.session.foreground_waiters, 1)
            finally:
                release.set()
            background.result(timeout=5)
            self.assertEqual(foreground.result(timeout=5)["shop"], "open")
        self.assertEqual(order, ["sweep-start", "sweep-released", "shop-open"])
        self.assertIsNone(self.session.operation)

    def test_stop_during_collection_releases_gate_and_rejects_waiting_action(self):
        entered, release, waiting = Event(), Event(), Event()
        def sweep(controller):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("测试未释放收集")
        self.sweep.side_effect = sweep
        condition_wait = self.session.condition.wait
        def wait(timeout=None):
            waiting.set()
            return condition_wait(timeout)
        action = Mock(return_value={})
        with ThreadPoolExecutor() as executor, patch.object(self.session.condition, "wait", side_effect=wait):
            background = executor.submit(self.collector.tick)
            self.assertTrue(entered.wait(5))
            foreground = executor.submit(self.session.run, "shop-open", action)
            try:
                self.assertTrue(waiting.wait(5))
                self.session.stop()
            finally:
                release.set()
            background.result(timeout=5)
            self.assertEqual(foreground.result(timeout=5)["reason"], "stopping")
        self.assertEqual(self.collector.state, "stopped")
        self.assertIsNone(self.collector.latest)
        self.assertEqual(self.session.preparation_frame.call_count, 1)
        action.assert_not_called()

    def test_new_preparation_round_cancels_previous_pending_collection(self):
        self.session.preparation_flow = Mock()
        self.session.preparation_flow.enter.return_value = {"result": "success", "screen": "备战"}
        self.session.enter_preparation()
        self.assertEqual(self.collector.summary(), {"state": "idle", "pending": False})

    def test_successful_battle_entry_cancels_pending_collection(self):
        self.session.battle = Mock()
        self.session.watch_battle = Mock()
        with patch("screens.battle.start", return_value={"result": "success", "screen": "战斗"}):
            self.session.start_battle()
        self.assertFalse(self.collector.pending)

    def test_shop_close_wakes_pending_collection_after_releasing_input_lock(self):
        self.collector.state = "paused"
        self.collector.event.clear()
        with patch("screens.shop.switch", return_value=preparation()) as switch:
            self.session.run("shop-close", lambda: self.session.shop("closed"))
        switch.assert_called_once_with(self.session, "closed")
        self.assertTrue(self.collector.event.is_set())
        self.assertTrue(self.session.operation_lock.acquire(blocking=False))
        self.session.operation_lock.release()

    def test_status_summary_does_not_include_full_background_observation(self):
        self.collector.latest = {"result": "success", "slots": {"bench_1": None}, "screenshot": "after.png"}
        self.assertEqual(self.session.status()["collection"], {"state": "waiting", "pending": True})
        self.assertNotIn("slots", self.session.status())
        self.assertIn("slots", self.session.observe_rewards())


class CollectorThreadTests(unittest.TestCase):
    def test_idle_worker_stops_without_capturing_or_sending_input(self):
        session = Session(1, "framepool")
        session.preparation_frame = Mock()
        worker = Collector(session)
        worker.close()
        self.assertFalse(worker.thread.is_alive())
        session.preparation_frame.assert_not_called()

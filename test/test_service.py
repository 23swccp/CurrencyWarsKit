"""服务协议测试；回调使用测试数据，不向游戏发送输入。"""

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

import service
import cw


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = service.Session(1, "framepool")

    def test_successful_captures_use_sequential_filenames(self):
        from numpy import zeros

        self.session.controller = Mock()
        job = self.session.controller.post_screencap.return_value.wait.return_value
        job.succeeded = True
        job.get.return_value = zeros((2, 3, 3), dtype="uint8")
        with TemporaryDirectory() as directory, patch.object(service, "RUNTIME", Path(directory)):
            service.clear_screenshots()
            first = self.session.run("screenshot", self.session.capture)
            job.succeeded = False
            with self.assertRaisesRegex(RuntimeError, "窗口截图失败"):
                self.session.run("screenshot", self.session.capture)
            self.assertIsNone(self.session.image_size)
            job.succeeded = True
            second = self.session.run("resync", self.session.capture)
            self.assertEqual(Path(first["screenshot"]).name, "1.png")
            self.assertEqual(Path(second["screenshot"]).name, "2.png")
            self.assertTrue(Path(second["screenshot"]).is_file())

    def test_failure_records_error_and_releases_operation(self):
        with self.assertRaisesRegex(RuntimeError, "测试失败"):
            self.session.run("input", Mock(side_effect=RuntimeError("测试失败")))
        self.assertEqual(self.session.status()["error"], "测试失败")
        self.assertIsNone(self.session.status()["operation"])
        self.assertEqual(self.session.run("screenshot", dict)["result"], "success")

    def test_concurrent_operation_is_busy(self):
        entered, release = threading.Event(), threading.Event()

        def action():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("测试未释放操作")
            return {}

        with ThreadPoolExecutor() as executor:
            job = executor.submit(self.session.run, "input", action)
            try:
                self.assertTrue(entered.wait(5))
                status = self.session.status()
                self.assertEqual(status["operation"], "input")
                other = Mock(return_value={})
                self.assertEqual(self.session.run("input", other)["result"], "busy")
                other.assert_not_called()
            finally:
                release.set()
            self.assertEqual(job.result(timeout=5)["result"], "success")
        self.assertEqual(self.session.run("input", dict)["result"], "success")

    def test_stop_invalidates_inflight_observation_and_rejects_more_work(self):
        def interrupted():
            self.session.stop()
            return {"screenshot": "not-published.png"}

        self.assertEqual(self.session.run("screenshot", interrupted)["reason"], "stopping")
        action = Mock(return_value={})
        self.assertEqual(self.session.run("screenshot", action)["reason"], "stopping")
        action.assert_not_called()


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.server.session = service.Session(1, "framepool")
        self.server.session.capture = Mock(return_value={"screenshot": "fixture.png"})
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.info = {"port": self.server.server_port}

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def send(self, path="/status", body=b"{}", headers=None, method="POST"):
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_get_cannot_stop_service(self):
        self.assertEqual(self.send("/stop", method="GET")[0], 405)
        self.assertFalse(self.server.session.stopping)

    def test_unknown_route_is_not_executed(self):
        self.server.session.capture.assert_not_called()
        self.assertEqual(self.send("/unknown")[0], 404)
        self.assertEqual(self.send("/unknown", b"{bad")[0], 404)

    def test_malformed_body_is_rejected_before_dispatch(self):
        code, data = self.send("/status", body=b"{bad")
        self.assertEqual(code, 400)
        self.assertEqual(data["result"], "failed")

    def test_every_cli_command_has_a_route(self):
        commands = {"status", "battle-result", "stop", "resync", "screenshot", "navigate", "observe",
                    "prepare", "overlay", "rewards"}
        actions = {"click", "drag", "press", "move", "collect", "start-battle", "continue-battle",
                   "environment", "strategy-refresh", "strategy-choose", "protagonist-open",
                   "protagonist-character", "protagonist-aura", "protagonist-refresh",
                   "protagonist-confirm", "sell", "shop-open", "shop-close", "refresh", "supply-refresh", "supply-choose",
                   "encounter-refresh", "encounter-choose", "armament-open", "armament-choose", "buy-experience",
                   "trail-shop-open", "trail-shop-buy", "trail-shop-close",
                   "tea-break-choose", "tea-break-commission", "special-use", "special-choose", "buy", "settle"}
        self.assertEqual({path[1:] for path in service.ROUTES} | {"stop"}, commands | actions)

    def test_busy_requests_do_not_capture(self):
        with self.server.session.operation_lock:
            code, data = self.send("/screenshot")
        self.assertEqual(code, 200)
        self.assertEqual(data["result"], "busy")
        self.server.session.capture.assert_not_called()

    def test_sell_and_shop_requests_use_the_serial_operation_boundary(self):
        session = self.server.session
        session.sell_character = Mock(return_value={"result": "success", "action": "sell"})
        self.assertEqual(service.request("sell", info=self.info, data={"from": "bench_8"})["result"], "success")
        session.sell_character.assert_called_once_with("bench_8")
        session.shop = Mock(return_value={"result": "success", "screen": "备战"})
        for command, target in (("shop-open", "open"), ("shop-close", "closed")):
            service.request(command, info=self.info)
            session.shop.assert_called_with(target)
        with session.operation_lock:
            self.assertEqual(service.request("sell", info=self.info, data={"from": "bench_8"})["result"], "busy")
        self.assertEqual(session.sell_character.call_count, 1)

    def test_rewards_query_reads_background_result_while_input_is_locked(self):
        self.server.session.observe_rewards = Mock(return_value={"result": "success", "gold": 80,
                                                                "collection": {"state": "done", "pending": False}})
        with self.server.session.operation_lock:
            self.assertEqual(service.request("rewards", info=self.info)["gold"], 80)
        self.server.session.capture.assert_not_called()

    def test_shop_read_and_refresh_are_serialized(self):
        session = self.server.session
        session.refresh_shop = Mock(return_value={"result": "success", "action": "refresh"})
        self.assertEqual(service.request("refresh", info=self.info)["result"], "success")
        with session.operation_lock:
            self.assertEqual(service.request("refresh", info=self.info)["result"], "busy")
        session.refresh_shop.assert_called_once()

    def test_supply_read_refresh_and_choice_use_operation_lock(self):
        session = self.server.session
        session.supply_action = Mock(return_value={"result": "success", "screen": "补给"})
        for command, data, expected in (("supply-refresh", {}, call("refresh")),
                                        ("supply-choose", {"option": 4}, call("choose", 4))):
            with self.subTest(command=command):
                self.assertEqual(service.request(command, info=self.info, data=data)["screen"], "补给")
                self.assertEqual(session.supply_action.call_args, expected)
                with session.operation_lock:
                    self.assertEqual(service.request(command, info=self.info, data=data)["result"], "busy")
        self.assertEqual(session.supply_action.call_count, 2)

    def test_stop_is_accepted_while_capture_is_running(self):
        entered, release = threading.Event(), threading.Event()

        def capture():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("测试未释放截图")
            return {"screenshot": "not-published.png"}

        self.server.session.capture.side_effect = capture
        with ThreadPoolExecutor() as executor:
            pending = executor.submit(self.send, "/screenshot")
            try:
                self.assertTrue(entered.wait(5))
                code, result = self.send("/stop")
                self.assertEqual(code, 200)
                self.assertEqual(result["status"], "stopping")
                self.assertFalse(pending.done())
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5)[1]["reason"], "stopping")

    def test_screenshot_and_resync_return_paths_without_state(self):
        for command in ("screenshot", "resync"):
            self.assertEqual(service.request(command, info=self.info),
                             {"result": "success", "screenshot": "fixture.png"})
        self.assertNotIn("state", service.request("status", info=self.info))

    def test_observe_is_serialized_read_only_observation(self):
        self.server.session.observe = Mock(return_value={"screen": "备战", "slots": {"front_1": None},
            "node": "3-4", "phase_box": [430, 33, 80, 24]})
        result = service.request("observe", info=self.info)
        self.assertEqual(result["slots"]["front_1"], None)
        self.assertEqual(result["screen"], "备战")
        self.assertEqual(result["node"], "3-4")
        self.assertEqual(result["phase_box"], [430, 33, 80, 24])
        with self.server.session.operation_lock:
            self.assertEqual(service.request("observe", info=self.info)["result"], "busy")
        self.server.session.observe.assert_called_once_with()

    def test_collect_uses_operation_lock_and_returns_frame(self):
        self.server.session.collect_rewards = Mock(return_value={"result": "uncertain", "screenshot": "after.png"})
        result = service.request("collect", info=self.info)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "after.png")
        with self.server.session.operation_lock:
            self.assertEqual(service.request("collect", info=self.info)["result"], "busy")
        self.server.session.collect_rewards.assert_called_once_with()

    def test_start_battle_is_serialized_and_preserves_uncertain(self):
        session = self.server.session
        session.start_battle = Mock(return_value={"result": "uncertain", "screenshot": "latest.png"})
        result = service.request("start-battle", info=self.info)
        self.assertEqual(result, {"result": "uncertain", "screenshot": "latest.png"})
        with session.operation_lock:
            self.assertEqual(service.request("start-battle", info=self.info)["result"], "busy")
        session.start_battle.assert_called_once_with()

    def test_strategy_routes_are_serialized(self):
        session = self.server.session
        session.investment_strategy = Mock(return_value={"result": "success", "screen": "投资策略", "needs_ai": True})
        for command, payload, args in (("strategy-refresh", {"option": 2}, ("refresh", 2)),
                                       ("strategy-choose", {"option": 3}, ("choose", 3))):
            with self.subTest(command=command):
                session.investment_strategy.reset_mock()
                result = service.request(command, info=self.info, data=payload)
                self.assertTrue(result["needs_ai"])
                session.investment_strategy.assert_called_once_with(*args)
                with session.operation_lock:
                    self.assertEqual(service.request(command, info=self.info, data=payload)["result"], "busy")
                session.investment_strategy.assert_called_once_with(*args)

    def test_preparation_entry_and_continue_are_serialized(self):
        session = self.server.session
        for command, method in (("prepare", "enter_preparation"), ("continue-battle", "continue_battle")):
            with self.subTest(command=command):
                operation = Mock(return_value={"result": "uncertain", "screen": "未知", "screenshot": "last.png"})
                setattr(session, method, operation)
                result = service.request(command, info=self.info)
                self.assertEqual(result["result"], "uncertain")
                self.assertEqual(result["screenshot"], "last.png")
                with session.operation_lock:
                    self.assertEqual(service.request(command, info=self.info)["result"], "busy")
                operation.assert_called_once_with()

    def test_protagonist_routes_are_serialized(self):
        session = self.server.session
        session.protagonist_action = Mock(return_value={"result": "success", "screen": "选择主角"})
        for command, data, args in (("protagonist-open", {}, ("open",)),
                                    ("protagonist-character", {"slot": "front_1"}, ("character",)),
                                    ("protagonist-aura", {"option": 2}, ("aura",)),
                                    ("protagonist-refresh", {"option": 1}, ("refresh",)),
                                    ("protagonist-confirm", {}, ("confirm",))):
            with self.subTest(command=command):
                session.protagonist_action.reset_mock()
                self.assertEqual(service.request(command, info=self.info, data=data)["screen"], "选择主角")
                session.protagonist_action.assert_called_once_with(*args, **data)
                with session.operation_lock:
                    self.assertEqual(service.request(command, info=self.info, data=data)["result"], "busy")
                session.protagonist_action.assert_called_once_with(*args, **data)

    def test_click_returns_new_snapshot(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        session.controller.post_click.return_value.wait.return_value.succeeded = True
        result = service.request("click", info=self.info, data={"x": 99, "y": 49})
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["screenshot"], "fixture.png")
        session.controller.post_click.assert_called_once_with(99, 49)
        session.capture.assert_called_once_with()

    def test_click_rejects_outside_coordinates(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        result = service.request("click", info=self.info, data={"x": 100, "y": 0})
        self.assertEqual(result["result"], "failed")
        session.controller.post_click.assert_not_called()

    def test_click_requires_screenshot_dimensions(self):
        session = self.server.session
        session.controller = Mock()
        result = service.request("click", info=self.info, data={"x": 1, "y": 1})
        self.assertEqual(result["result"], "failed")
        self.assertIn("screenshot", result["message"])
        session.controller.post_click.assert_not_called()

    def test_click_failure_or_followup_capture_is_uncertain(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        for click_ok in (False, True):
            with self.subTest(click_ok=click_ok):
                session.controller.post_click.return_value.wait.return_value.succeeded = click_ok
                session.capture.side_effect = RuntimeError("截图失败") if click_ok else None
                result = service.request("click", info=self.info, data={"x": 1, "y": 1})
                self.assertEqual(result["result"], "uncertain")

    def test_capture_value_error_after_click_is_uncertain(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        session.controller.post_click.return_value.wait.return_value.succeeded = True
        session.capture.side_effect = ValueError("图像数据无效")
        result = service.request("click", info=self.info, data={"x": 1, "y": 1})
        self.assertEqual(result["result"], "uncertain")
        self.assertIn("ValueError", result["message"])
        session.controller.post_click.assert_called_once_with(1, 1)

    def test_busy_click_does_not_send_input(self):
        session = self.server.session
        session.controller = Mock()
        with session.operation_lock:
            result = service.request("click", info=self.info, data={"x": 1, "y": 1})
        self.assertEqual(result["result"], "busy")
        session.controller.post_click.assert_not_called()

    def test_move_drags_slots_once_and_returns_uncertain_with_snapshot(self):
        session = self.server.session
        session.image_size = (1920, 1080)
        session.controller = Mock()
        session.controller.post_swipe.return_value.wait.return_value.succeeded = True
        payload = {"from": "bench_1", "to": "bench_2"}
        result = service.request("move", info=self.info, data=payload)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "fixture.png")
        session.controller.post_swipe.assert_called_once_with(438, 905, 563, 905, 700)

    def test_move_rejects_unverified_image_size(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        result = service.request("move", info=self.info,
                                 data={"from": "bench_1", "to": "front_1"})
        self.assertEqual(result["result"], "failed")
        session.controller.post_swipe.assert_not_called()

    def test_move_reads_backstage_coordinates_from_the_current_frame(self):
        session = self.server.session
        session.controller = Mock()
        session.controller.post_swipe.return_value.wait.return_value.succeeded = True
        session.preparation = Mock()
        session.preparation.page.return_value = {"result": "success", "screen": "备战"}
        session.preparation_frame = Mock()
        session.save_image = Mock(return_value={"screenshot": "before.png"})
        image = np.zeros((1080, 1920, 3), dtype="uint8")
        session.preparation_frame.return_value = image
        payload = {"from": "back_4", "to": "back_7"}
        fixtures = Path(__file__).with_name("fixtures") / "preparation"
        with Image.open(fixtures / "backstage-seven.png") as band:
            image[593:602] = np.asarray(band)[:, :, ::-1]
        result = service.request("move", info=self.info, data=payload)
        self.assertEqual(result["screenshot"], "fixture.png")
        self.assertEqual(result["result"], "uncertain")
        session.controller.post_swipe.assert_called_once_with(960, 670, 1389, 670, 700)
        session.controller.post_swipe.reset_mock()

        with Image.open(fixtures / "backstage-six.png") as band:
            image[593:602] = np.asarray(band)[:, :, ::-1]
        result = service.request("move", info=self.info, data=payload)
        self.assertEqual(result["result"], "failed")
        image[593:602] = 0
        result = service.request("move", info=self.info, data=payload)
        self.assertEqual(result["screenshot"], "before.png")
        self.assertEqual(result["result"], "uncertain")
        session.controller.post_swipe.assert_not_called()

    def test_drag_and_press_publish_snapshots(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        session.controller.post_swipe.return_value.wait.return_value.succeeded = True
        session.controller.post_click_key.return_value.wait.return_value.succeeded = True
        drag = {"from_x": 1, "from_y": 2, "to_x": 99, "to_y": 49, "duration": 500}
        result = service.request("drag", info=self.info, data=drag)
        session.controller.post_swipe.assert_called_once_with(1, 2, 99, 49, 500)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["screenshot"], "fixture.png")
        result = service.request("press", info=self.info, data={"key": "escape"})
        self.assertEqual(result["result"], "success")
        session.controller.post_click_key.assert_called_once_with(0x1B)

    def test_drag_rejects_outside_coordinates(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        drag = {"from_x": 0, "from_y": 0, "to_x": 10, "to_y": 10, "duration": 500}
        result = service.request("drag", info=self.info, data={**drag, "to_x": 100})
        self.assertEqual(result["result"], "failed")
        session.controller.post_swipe.assert_not_called()
        session.controller.post_click_key.assert_not_called()

    def test_drag_and_press_failures_are_uncertain_and_busy_does_not_send(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        for command, payload, method in (
            ("drag", {"from_x": 1, "from_y": 1, "to_x": 2, "to_y": 2, "duration": 500}, "post_swipe"),
            ("press", {"key": "a"}, "post_click_key"),
        ):
            with self.subTest(command=command):
                target = getattr(session.controller, method)
                with session.operation_lock:
                    self.assertEqual(service.request(command, info=self.info, data=payload)["result"], "busy")
                target.assert_not_called()
                target.return_value.wait.return_value.succeeded = False
                self.assertEqual(service.request(command, info=self.info, data=payload)["result"], "uncertain")

    def test_local_client_does_not_use_proxy(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            self.assertEqual(service.request("status", info=self.info)["result"], "success")


class CliTests(unittest.TestCase):
    def test_supply_commands_send_intent_and_option(self):
        for argv, command, kwargs in ((["act", "supply", "--option", "4"], "supply-choose", {"data": {"option": 4}}),
                                      (["act", "supply-refresh"], "supply-refresh", {"data": {}})):
            with self.subTest(argv=argv), patch.object(sys, "argv", ["cw", *argv]), \
                    patch.object(service, "INFO", Mock()), patch("cw.output"), \
                    patch("service.request", return_value={"result": "success"}) as request:
                self.assertEqual(cw.main(), 0)
                request.assert_called_once_with(command, **kwargs)

    def test_sell_shop_and_rewards_send_expected_requests(self):
        cases = ((["act", "sell", "--from", "bench_8"], "sell", {"data": {"from": ["bench_8"]}}),
                 (["act", "sell", "--from", "bench_2", "bench_5"], "sell",
                  {"data": {"from": ["bench_2", "bench_5"]}}),
                 (["act", "move", "--from", "bench_2", "--to", "back_7"], "move",
                  {"data": {"from": "bench_2", "to": "back_7"}}),
                 (["act", "shop-open"], "shop-open", {"data": {}}),
                 (["act", "shop-close"], "shop-close", {"data": {}}),
                 (["act", "refresh"], "refresh", {"data": {}}),
                 (["rewards"], "rewards", {}))
        for argv, command, kwargs in cases:
            with self.subTest(argv=argv), patch.object(sys, "argv", ["cw", *argv]), \
                    patch.object(service, "INFO", Mock()), patch("cw.output"), \
                    patch("service.request", return_value={"result": "success"}) as request:
                self.assertEqual(cw.main(), 0)
                request.assert_called_once_with(command, **kwargs)

    def test_protagonist_commands_send_intent_and_targets(self):
        commands = ((["act", "protagonist-open"], "protagonist-open", {}),
                    (["act", "protagonist-character", "--slot", "front_1"], "protagonist-character", {"slot": "front_1"}),
                    (["act", "protagonist-aura", "--option", "2"], "protagonist-aura", {"option": 2}),
                    (["act", "protagonist-refresh", "--option", "1"], "protagonist-refresh", {"option": 1}),
                    (["act", "protagonist-confirm"], "protagonist-confirm", {}))
        for argv, command, data in commands:
            with self.subTest(command=command), patch.object(sys, "argv", ["cw", *argv]), \
                    patch.object(service, "INFO", Mock()), patch("service.request", return_value={"result": "success"}) as request, \
                    patch("cw.output"):
                self.assertEqual(cw.main(), 0)
                request.assert_called_once_with(command, **({} if data is None else {"data": data}))

    def test_protagonist_rejects_bench_slot_and_invalid_side(self):
        for argv in (["protagonist-character", "--slot", "bench_1"], ["protagonist-aura", "--option", "3"]):
            with self.subTest(argv=argv), patch.object(sys, "argv", ["cw", "act", *argv]), \
                    patch("service.request") as request, patch("argparse.ArgumentParser.error", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    cw.main()
                request.assert_not_called()

    def test_preparation_entry_and_continue_send_expected_requests(self):
        for argv, command, kwargs in ((["prepare"], "prepare", {}),
                                      (["act", "continue-battle"], "continue-battle", {"data": {}})):
            with self.subTest(command=command), patch.object(sys, "argv", ["cw", *argv]), \
                    patch.object(service, "INFO", Mock()), patch("service.request", return_value={"result": "success"}) as request, \
                    patch("cw.output"):
                self.assertEqual(cw.main(), 0)
                request.assert_called_once_with(command, **kwargs)

    def test_buy_experience_sends_requested_amount(self):
        with (patch.object(sys, "argv", ["cw", "act", "buy-experience", "--xp", "12"]),
              patch.object(service, "INFO", Mock()),
              patch.object(service, "request", return_value={"result": "success"}) as request,
              patch.object(cw, "output")):
            self.assertEqual(cw.main(), 0)
        request.assert_called_once_with("buy-experience", data={"xp": 12})

    def test_observe_sends_observation_request(self):
        with patch.object(sys, "argv", ["cw", "observe"]), patch.object(service, "INFO", Mock()), \
                patch.object(cw, "output"), patch.object(service, "request", return_value={"result": "success"}) as request:
            self.assertEqual(cw.main(), 0)
        request.assert_called_once_with("observe")

    def test_start_battle_sends_empty_payload(self):
        with patch.object(sys, "argv", ["cw", "act", "start-battle"]), \
                patch.object(service, "INFO", Mock()), patch.object(cw, "output"), \
                patch.object(service, "request", return_value={"result": "success"}) as request:
            self.assertEqual(cw.main(), 0)
        request.assert_called_once_with("start-battle", data={})

    def test_strategy_commands_send_intent_and_option(self):
        for argv, command, payload in ((["act", "strategy", "--option", "3"], "strategy-choose", {"option": 3}),
                                      (["act", "strategy-refresh", "--option", "1"], "strategy-refresh", {"option": 1})):
            with self.subTest(argv=argv), patch.object(sys, "argv", ["cw", *argv]), \
                    patch.object(service, "INFO", Mock()), patch.object(cw, "output"), \
                    patch.object(service, "request", return_value={"result": "success"}) as request:
                self.assertEqual(cw.main(), 0)
                if payload is None:
                    request.assert_called_once_with(command)
                else:
                    request.assert_called_once_with(command, data=payload)

    def test_click_sends_only_coordinates(self):
        with (patch.object(sys, "argv", ["cw", "act", "click", "--x", "1", "--y", "2"]),
              patch.object(service, "INFO", Mock()),
              patch.object(service, "request", return_value={"result": "success"}) as request,
              patch.object(cw, "output")):
            self.assertEqual(cw.main(), 0)
        request.assert_called_once_with("click", data={"x": 1, "y": 2})

    def test_invalid_arguments_are_rejected_at_cli(self):
        cli = Path(__file__).resolve().parent.parent / "tools" / "cw.py"
        cases = [
            ["supply", "--option", "0"],
            ["supply", "--option", "6"],
            ["sell", "--from", "front_1"],
            ["sell", "--from", "bench_10"],
            ["press", "--key", "unknown"],
            ["drag", "--from-x", "0", "--from-y", "0", "--to-x", "1", "--to-y", "1",
             "--duration", "0"],
            ["drag", "--from-x", "0", "--from-y", "0", "--to-x", "1", "--to-y", "1",
             "--duration", "10001"],
            ["move", "--from", "shop_1", "--to", "bench_1"],
            ["move", "--from", "bench_1", "--to", "bench_1"],
            ["move", "--from", "front_5", "--to", "back_1"],
            ["click", "--x", "not-an-int", "--y", "1"],
            ["strategy", "--option", "0"],
            ["strategy-refresh", "--option", "4"],
            ["strategy"],
            ["buy-experience", "--xp", "0"],
            ["buy-experience", "--xp", "-4"],
            ["buy-experience", "--xp", "6"],
            ["trail-shop-buy", "--slot", "0"],
            ["trail-shop-buy", "--slot", "5"],
        ]
        for args in cases:
            with self.subTest(args=args):
                completed = subprocess.run([sys.executable, str(cli), "act", *args], capture_output=True)
                self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()

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
from unittest.mock import Mock, patch

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

    def test_busy_requests_do_not_capture(self):
        with self.server.session.operation_lock:
            code, data = self.send("/screenshot")
        self.assertEqual(code, 200)
        self.assertEqual(data["result"], "busy")
        self.server.session.capture.assert_not_called()

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
            ["press", "--key", "unknown"],
            ["drag", "--from-x", "0", "--from-y", "0", "--to-x", "1", "--to-y", "1",
             "--duration", "0"],
            ["drag", "--from-x", "0", "--from-y", "0", "--to-x", "1", "--to-y", "1",
             "--duration", "10001"],
            ["move", "--from", "shop_1", "--to", "bench_1"],
            ["move", "--from", "bench_1", "--to", "bench_1"],
            ["move", "--from", "front_5", "--to", "back_1"],
            ["click", "--x", "not-an-int", "--y", "1"],
        ]
        for args in cases:
            with self.subTest(args=args):
                completed = subprocess.run([sys.executable, str(cli), "act", *args], capture_output=True)
                self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()

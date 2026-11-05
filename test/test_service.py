"""服务协议测试；回调使用测试数据，不向游戏发送输入。"""

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch

import service


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = service.Session(1, "framepool")

    def test_observations_change_state_but_status_does_not(self):
        first = self.session.run("screenshot", lambda: {"screenshot": "first.png"})
        self.assertEqual(self.session.status()["state"], first["state"])
        second = self.session.run("resync", lambda: {"screenshot": "second.png"})
        self.assertNotEqual(first["state"], second["state"])
        self.assertEqual(self.session.status()["state"], second["state"])

    def test_stale_state_never_calls_action(self):
        old = self.session.run("screenshot", dict)["state"]
        current = self.session.run("screenshot", dict)["state"]
        action = Mock(return_value={})
        self.assertEqual(self.session.run("input", action, old)["reason"], "stale_state")
        action.assert_not_called()
        self.assertEqual(self.session.state, current)

    def test_restart_rejects_old_state(self):
        old = self.session.run("screenshot", dict)["state"]
        restarted = service.Session(1, "framepool")
        action = Mock(return_value={})
        self.assertEqual(restarted.run("input", action, old)["reason"], "stale_state")
        action.assert_not_called()
        self.assertNotEqual(restarted.run("screenshot", dict)["state"], old)

    def test_failure_consumes_state_and_records_error(self):
        old = self.session.run("screenshot", dict)["state"]
        with self.assertRaisesRegex(RuntimeError, "测试失败"):
            self.session.run("input", Mock(side_effect=RuntimeError("测试失败")), old)
        self.assertIsNone(self.session.state)
        self.assertEqual(self.session.status()["error"], "测试失败")
        self.assertIsNone(self.session.status()["operation"])
        self.assertEqual(self.session.run("screenshot", dict)["result"], "success")

    def test_concurrent_operation_is_busy_and_state_is_consumed(self):
        old = self.session.run("screenshot", dict)["state"]
        entered, release = threading.Event(), threading.Event()

        def action():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("测试未释放操作")
            return {}

        with ThreadPoolExecutor() as executor:
            job = executor.submit(self.session.run, "input", action, old)
            try:
                self.assertTrue(entered.wait(5))
                status = self.session.status()
                self.assertEqual(status["operation"], "input")
                self.assertIsNone(status["state"])
                other = Mock(return_value={})
                self.assertEqual(self.session.run("input", other, old)["result"], "busy")
                other.assert_not_called()
            finally:
                release.set()
            self.assertEqual(job.result(timeout=5)["result"], "success")
        self.assertEqual(self.session.run("input", dict, old)["reason"], "stale_state")

    def test_stop_invalidates_inflight_observation_and_rejects_more_work(self):
        def interrupted():
            self.session.stop()
            return {"screenshot": "not-published.png"}

        self.assertEqual(self.session.run("screenshot", interrupted)["reason"], "stopping")
        action = Mock(return_value={})
        self.assertEqual(self.session.run("screenshot", action)["reason"], "stopping")
        action.assert_not_called()
        self.assertIsNone(self.session.state)


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

    def test_invalid_payloads_cannot_execute(self):
        for body, headers in [
            (b"{}", {"Content-Type": "text/plain"}),
            (b"{bad", {}), (b"[]", {}), (b'{"extra":1}', {}),
            (b'{"output":"unused.png"}', {}),
            (b" " * 16385, {}),
        ]:
            with self.subTest(body=body[:40]):
                self.assertEqual(self.send("/screenshot", body, headers)[0], 400)
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
                self.assertIsNone(self.server.session.status()["state"])
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5)[1]["reason"], "stopping")

    def test_client_and_resync_preserve_snapshot_state_relationship(self):
        first = service.request("screenshot", info=self.info)
        self.assertEqual(first["screenshot"], "fixture.png")
        self.assertEqual(service.request("status", info=self.info)["state"], first["state"])
        second = service.request("resync", info=self.info)
        self.assertNotEqual(second["state"], first["state"])

    def test_click_consumes_token_and_returns_new_snapshot(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        session.controller.post_click.return_value.wait.return_value.succeeded = True
        old = service.request("screenshot", info=self.info)["state"]
        result = service.request("click", info=self.info, data={"x": 99, "y": 49, "state": old})
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["screenshot"], "fixture.png")
        self.assertNotEqual(result["state"], old)
        session.controller.post_click.assert_called_once_with(99, 49)
        stale = service.request("click", info=self.info, data={"x": 1, "y": 1, "state": old})
        self.assertEqual(stale["reason"], "stale_state")
        session.controller.post_click.assert_called_once()

    def test_click_rejects_bad_payload_and_outside_coordinates(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        state = service.request("screenshot", info=self.info)["state"]
        for data in ({"x": True, "y": 1, "state": state}, {"x": 1, "y": 1},
                     {"x": 1, "y": 1, "state": ""}, {"x": 1, "y": 1, "state": state, "extra": 1}):
            self.assertEqual(self.send("/click", json.dumps(data).encode())[0], 400)
        result = service.request("click", info=self.info, data={"x": 100, "y": 0, "state": state})
        self.assertEqual(result["result"], "failed")
        session.controller.post_click.assert_not_called()

    def test_click_failure_or_followup_capture_is_uncertain(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        for click_ok in (False, True):
            with self.subTest(click_ok=click_ok):
                session.capture.side_effect = None
                state = service.request("screenshot", info=self.info)["state"]
                session.controller.post_click.return_value.wait.return_value.succeeded = click_ok
                session.capture.side_effect = RuntimeError("截图失败") if click_ok else None
                result = service.request("click", info=self.info, data={"x": 1, "y": 1, "state": state})
                self.assertEqual(result["result"], "uncertain")
                self.assertIsNone(session.state)

    def test_busy_click_does_not_send_input(self):
        session = self.server.session
        session.controller = Mock()
        state = service.request("screenshot", info=self.info)["state"]
        with session.operation_lock:
            result = service.request("click", info=self.info, data={"x": 1, "y": 1, "state": state})
        self.assertEqual(result["result"], "busy")
        session.controller.post_click.assert_not_called()

    def test_drag_and_press_publish_snapshots_and_reject_stale_state(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        session.controller.post_swipe.return_value.wait.return_value.succeeded = True
        session.controller.post_click_key.return_value.wait.return_value.succeeded = True
        state = service.request("screenshot", info=self.info)["state"]
        drag = {"from_x": 1, "from_y": 2, "to_x": 99, "to_y": 49, "duration": 500, "state": state}
        result = service.request("drag", info=self.info, data=drag)
        session.controller.post_swipe.assert_called_once_with(1, 2, 99, 49, 500)
        self.assertEqual(result["result"], "success")
        self.assertNotEqual(result["state"], state)
        self.assertEqual(service.request("drag", info=self.info, data=drag)["reason"], "stale_state")
        session.controller.post_swipe.assert_called_once()
        result = service.request("press", info=self.info, data={"key": "escape", "state": result["state"]})
        self.assertEqual(result["result"], "success")
        session.controller.post_click_key.assert_called_once_with(0x1B)

    def test_drag_and_press_reject_invalid_inputs(self):
        session = self.server.session
        session.image_size = (100, 50)
        session.controller = Mock()
        state = service.request("screenshot", info=self.info)["state"]
        drag = {"from_x": 0, "from_y": 0, "to_x": 10, "to_y": 10, "duration": 500, "state": state}
        for change in ({"duration": 0}, {"duration": 10001}, {"duration": True}, {"to_x": "10"}):
            self.assertEqual(self.send("/drag", json.dumps({**drag, **change}).encode())[0], 400)
        for key in ("unknown", "A", 1):
            self.assertEqual(self.send("/press", json.dumps({"key": key, "state": state}).encode())[0], 400)
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
                state = service.request("screenshot", info=self.info)["state"]
                data = {**payload, "state": state}
                target = getattr(session.controller, method)
                with session.operation_lock:
                    self.assertEqual(service.request(command, info=self.info, data=data)["result"], "busy")
                target.assert_not_called()
                target.return_value.wait.return_value.succeeded = False
                self.assertEqual(service.request(command, info=self.info, data=data)["result"], "uncertain")
                self.assertIsNone(session.state)

    def test_local_client_does_not_use_proxy(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            self.assertEqual(service.request("status", info=self.info)["result"], "success")


if __name__ == "__main__":
    unittest.main()

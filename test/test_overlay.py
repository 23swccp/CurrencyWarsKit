"""识别框的数据、操作隔离与开关协议；不创建窗口。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import unittest
from unittest.mock import Mock, patch
import sys

import numpy as np

import cw
import service
from overlay import Overlay
from recognition_debug import focus, trace
from screens.preparation import Recognition
from screens.strategy import Strategy


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_real_template_and_ocr_entry_points_publish_the_submitted_roi(self):
        observer = Mock()
        template = Recognition.__new__(Recognition)
        template.tasker = Mock()
        template.tasker.post_recognition.return_value.wait.return_value.get.return_value.nodes = [
            Mock(recognition=Mock(best_result=Mock(score=0.95)))]
        ocr = Strategy.__new__(Strategy)
        ocr.tasker = Mock()
        ocr.tasker.post_recognition.return_value.wait.return_value.get.return_value.nodes = [
            Mock(recognition=Mock(filtered_results=[Mock(text="刷新次数3", box=(10, 20, 30, 40))]))]
        roi = (430, 35, 80, 23)
        with trace(observer):
            self.assertEqual(template.score(self.image, "shop.png", roi), 0.95)
            self.assertEqual(ocr.text(self.image, roi), "刷新次数3")
        self.assertEqual([call.args[1:] for call in observer.mark.call_args_list],
                         [(roi, "shop.png", "template"), (roi, "投资策略 OCR", "ocr")])
        for tasker in (template.tasker, ocr.tasker):
            self.assertEqual(tasker.post_recognition.call_args.args[1].roi, roi)

    def test_observer_is_reset_after_operation_failure(self):
        session = service.Session(1, "framepool")
        session.overlay = observer = Mock()
        def fail():
            focus(self.image, (1, 2, 3, 4), "出错前")
            raise RuntimeError("OCR 详情缺失")
        with self.assertRaisesRegex(RuntimeError, "OCR 详情缺失"):
            session.run("preparation", fail)
        focus(self.image, (1, 2, 3, 4), "操作外")
        observer.mark.assert_called_once()
        observer.begin.assert_called_once()
        observer.end.assert_called_once()

    def test_worker_template_matching_does_not_draw_library_coordinates(self):
        observer = Mock()
        with trace(observer), ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(focus, self.image, (0, 0, 0, 0), "图库比较").result()
            focus(self.image, (0, 0, 0, 0), "全图")
        observer.mark.assert_called_once_with(self.image, (0, 0, 1920, 1080), "全图", "template")

    def test_disabled_trace_does_not_query_windows(self):
        session = service.Session(1, "framepool")
        with patch("overlay.client_bounds") as bounds:
            session.run("preparation", lambda: (focus(self.image, (1, 2, 3, 4), "范围") or {}))
        bounds.assert_not_called()

    def test_observer_is_reset_even_when_display_end_reports_an_error(self):
        observer = Mock()
        observer.end.side_effect = RuntimeError("绘制出错")
        with self.assertRaisesRegex(RuntimeError, "绘制出错"):
            with trace(observer):
                pass
        focus(self.image, (1, 2, 3, 4), "操作外")
        observer.mark.assert_not_called()


class OverlayStateTests(unittest.TestCase):
    def setUp(self):
        self.overlay = Overlay.__new__(Overlay)
        self.overlay.target = 123
        self.overlay.lock = Lock()
        self.overlay.error = None
        self.overlay.revision = 0
        self.overlay.generation = 0
        self.overlay.position_pending = False
        self.overlay.regions = {}
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_same_frame_accumulates_and_new_frame_replaces_regions(self):
        with patch("overlay.client_bounds", return_value=(100, 200, 960, 540)) as bounds:
            self.overlay.begin()
            self.overlay.locate()
            self.overlay.mark(self.image, (1, 2, 3, 4), "标题", "template")
            self.overlay.mark(self.image, (5, 6, 7, 8), "金币", "ocr")
            self.assertEqual(len(self.overlay.regions), 2)
            self.overlay.mark(self.image.copy(), (9, 10, 11, 12), "下一帧", "ocr")
        bounds.assert_called_once_with(123)
        self.assertEqual(self.overlay.regions, {((9, 10, 11, 12), "ocr"): "下一帧"})
        with patch("overlay.monotonic", return_value=10):
            self.overlay.end()
        self.assertAlmostEqual(self.overlay.expires, 11.2)

    def test_minimized_operation_has_no_visible_bounds(self):
        with patch("overlay.client_bounds", return_value=None):
            self.overlay.begin()
            self.overlay.locate()
        self.overlay.mark(self.image, (1, 2, 3, 4), "最小化", "ocr")
        self.assertIsNone(self.overlay.bounds)

    def test_window_queries_run_once_in_display_thread_not_recognition(self):
        with patch("overlay.client_bounds", return_value=(100, 200, 960, 540)) as bounds:
            self.overlay.begin()
            for index in range(30):
                self.overlay.mark(self.image, (index, 2, 3, 4), "模板", "template")
            self.overlay.end()
            bounds.assert_not_called()
            self.overlay.locate()
            self.overlay.locate()
        bounds.assert_called_once_with(123)

    def test_display_error_prevents_next_operation(self):
        self.overlay.error = RuntimeError("绘制失败")
        with patch("overlay.client_bounds") as bounds:
            with self.assertRaisesRegex(RuntimeError, "识别框显示出错.*绘制失败"):
                self.overlay.begin()
        bounds.assert_not_called()


class OverlayProtocolTests(unittest.TestCase):
    def test_capture_exclusion_failure_rejects_enable_and_destroys_native_resources(self):
        with patch.multiple("overlay.user32", RegisterClassW=Mock(return_value=1),
                            CreateWindowExW=Mock(return_value=99),
                            SetLayeredWindowAttributes=Mock(return_value=1),
                            SetWindowDisplayAffinity=Mock(return_value=0),
                            DestroyWindow=Mock(), UnregisterClassW=Mock()), \
                patch.multiple("overlay.gdi32", CreateSolidBrush=Mock(return_value=43),
                               CreatePen=Mock(return_value=42), DeleteObject=Mock()):
            with self.assertRaisesRegex(RuntimeError, "识别框显示出错"):
                Overlay(1)
            from overlay import user32, gdi32
            user32.DestroyWindow.assert_called_once_with(99)
            user32.UnregisterClassW.assert_called_once()
            self.assertEqual(gdi32.DeleteObject.call_count, 4)

    def test_enable_is_idempotent_and_disable_closes_the_window(self):
        session = service.Session(1, "framepool")
        with patch("overlay.Overlay") as factory:
            for enabled in (True, True, False, False):
                result = service.ROUTES["/overlay"](session, {"enabled": enabled})
                self.assertEqual(result, {"result": "success", "overlay": enabled})
            factory.assert_called_once_with(1)
            factory.return_value.close.assert_called_once()
        self.assertFalse(session.status()["overlay"])

    def test_toggle_obeys_operation_lock(self):
        session = service.Session(1, "framepool")
        with session.operation_lock, patch("overlay.Overlay") as factory:
            self.assertEqual(service.ROUTES["/overlay"](session, {"enabled": True})["result"], "busy")
        factory.assert_not_called()

    def test_failed_overlay_can_still_be_disabled(self):
        session = service.Session(1, "framepool")
        session.overlay = observer = Mock()
        observer.begin.side_effect = RuntimeError("显示故障")
        result = service.ROUTES["/overlay"](session, {"enabled": False})
        self.assertEqual(result, {"result": "success", "overlay": False})
        observer.begin.assert_not_called()
        observer.close.assert_called_once()
        self.assertIsNone(session.overlay)

    def test_cli_sends_boolean_and_rejects_invalid_state(self):
        for state in ("on", "off"):
            with patch.object(sys, "argv", ["cw", "overlay", state]), patch.object(service, "INFO", Mock()), \
                    patch("service.request", return_value={"result": "success"}) as request, patch("cw.output"):
                self.assertEqual(cw.main(), 0)
                request.assert_called_once_with("overlay", data={"enabled": state == "on"})
        with patch.object(sys, "argv", ["cw", "overlay", "yes"]), \
                patch("argparse.ArgumentParser.error", side_effect=ValueError), patch("service.request") as request:
            with self.assertRaises(ValueError):
                cw.main()
            request.assert_not_called()

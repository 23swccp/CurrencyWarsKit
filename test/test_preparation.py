"""备战识别与只读观察测试。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, get_ident
import unittest
from unittest.mock import Mock, call, patch

import numpy as np

from screens.preparation import Preparation, Recognition
from service import Session
from game.slots import CARDS, ICONS, PHASE, SHOP


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.reader = Preparation.__new__(Preparation)
        self.reader.resource = Mock()
        self.reader.tasker = Mock()
        self.reader.portraits = [("云璃", [None, None]), ("火花", [None, None])]
        self.reader.workers = [(Mock(), self.reader.portraits)]
        self.reader.pool = ThreadPoolExecutor(max_workers=4)
        self.addCleanup(self.reader.close)
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_other_page_stops_before_slots(self):
        self.reader.score = Mock(return_value=0.69)
        self.reader.match = Mock()
        result = self.reader.recognize(self.image)
        self.assertEqual(result["result"], "uncertain")
        self.assertNotIn("slots", result)
        self.reader.match.assert_not_called()

    def test_phase_and_shop_at_threshold_pass_without_icons(self):
        self.reader.score = Mock(side_effect=[0.7, 0.9])
        result = self.reader.page(self.image)
        self.assertEqual(result, {"result": "success", "screen": "备战"})
        self.assertEqual(self.reader.score.call_args_list, [
            call(self.image, "phase.png", PHASE), call(self.image, "shop.png", SHOP)])

    def test_icons_confirm_page_when_shop_is_not_visible(self):
        self.reader.score = Mock(side_effect=[0.7, 0.4, 0.9])
        self.assertEqual(self.reader.page(self.image), {"result": "success", "screen": "备战"})
        self.assertEqual(self.reader.score.call_args_list, [
            call(self.image, "phase.png", PHASE), call(self.image, "shop.png", SHOP),
            call(self.image, "icons.png", ICONS)])

    def test_phase_alone_stops_before_slots(self):
        self.reader.score = Mock(side_effect=[1, 0.89, 0.89])
        self.reader.match = Mock()
        result = self.reader.recognize(self.image)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["anchors"], {"phase": 1, "shop": 0.89, "icons": 0.89})
        self.assertNotIn("slots", result)
        self.reader.match.assert_not_called()

    def test_auxiliary_anchors_cannot_replace_phase(self):
        self.reader.score = Mock(side_effect=[0.69, 1, 1])
        self.assertEqual(self.reader.page(self.image)["result"], "uncertain")
        self.reader.score.assert_called_once_with(self.image, "phase.png", PHASE)

    def test_tasks_bind_to_image_controller(self):
        taskers = []
        def tasker():
            instance = Mock()
            taskers.append(instance)
            return instance
        with patch("screens.preparation.ImageController") as controller, patch("screens.preparation.Resource"), \
                patch("screens.preparation.Tasker", side_effect=tasker):
            reader = Preparation()
            self.addCleanup(reader.close)
        self.assertEqual(len(taskers), 17)
        for instance in taskers:
            self.assertIs(instance.bind.call_args.args[1], controller.return_value)

    def test_image_controller_connection_error_is_reported(self):
        with patch("screens.preparation.ImageController") as controller:
            controller.return_value.post_connection.return_value.wait.return_value.succeeded = False
            with self.assertRaisesRegex(RuntimeError, "图片控制器连接失败"):
                Preparation()

    def test_size_checked_before_matching(self):
        self.reader.score = Mock()
        with self.assertRaises(ValueError):
            self.reader.recognize(self.image[:720, :1280])
        self.reader.score.assert_not_called()

    def test_empty_and_unknown_slots_are_distinct(self):
        self.reader.score = Mock(side_effect=[0.7, 0.9, 0.2] + [1] * 18)
        self.reader.match = Mock(return_value={"unknown": True, "candidates": []})
        slots = self.reader.recognize(self.image)["slots"]
        self.assertEqual(set(slots), set(CARDS))
        self.assertTrue(slots["front_1"]["unknown"])
        self.assertTrue(all(value is None for slot, value in slots.items() if slot != "front_1"))
        self.reader.match.assert_called_once()

    def test_same_character_can_occupy_multiple_slots(self):
        self.reader.score = Mock(side_effect=[0.7, 0.9, 0.2, 0.2] + [1] * 17)
        self.reader.match = Mock(return_value={"name": "云璃"})
        slots = self.reader.recognize(self.image)["slots"]
        self.assertEqual(slots["front_1"]["name"], "云璃")
        self.assertEqual(slots["front_2"]["name"], "云璃")

    def test_variants_compete_by_character(self):
        worker = Recognition.__new__(Recognition)
        worker.resource = Mock()
        worker.score = Mock(side_effect=[0.95, 0.94, 0.4, 0.3])
        self.reader.workers = [(worker, self.reader.portraits)]
        result = self.reader.match(self.image[:54, :68])
        self.assertEqual(result["name"], "云璃")
        self.assertEqual(result["candidates"][1]["name"], "火花")

    def test_low_score_and_close_candidates_are_unknown(self):
        for scores in ([0.7, 0.6, 0.4, 0.3], [0.95, 0.94, 0.9, 0.85]):
            with self.subTest(scores=scores):
                self.reader.workers[0][0].compare.return_value = [(max(scores[:2]), "云璃"), (max(scores[2:]), "火花")]
                self.assertTrue(self.reader.match(self.image[:54, :68])["unknown"])

    def test_four_workers_overlap_and_own_their_templates(self):
        barrier = Barrier(4, timeout=5)
        threads = set()
        self.reader.workers = []
        for index in range(4):
            worker = Recognition.__new__(Recognition)
            worker.resource = Mock()
            def score(image, template, index=index):
                threads.add(get_ident())
                barrier.wait()
                return 0.95 if index == 3 else 0.2
            worker.score = score
            self.reader.workers.append((worker, [(str(index), [None])]))
        result = self.reader.match(self.image[:54, :68])
        self.assertEqual(result["name"], "3")
        self.assertEqual(len(threads), 4)
        self.assertEqual(len({id(worker.resource) for worker, _ in self.reader.workers}), 4)
        for worker, _ in self.reader.workers:
            worker.resource.override_image.assert_called_once()

    def test_worker_error_is_reported(self):
        entered, finished = Event(), Event()
        def fail(*args):
            self.assertTrue(entered.wait(5))
            raise RuntimeError("候选识别出错")
        def finish(*args):
            entered.set()
            finished.set()
            return [(0.9, "云璃")]
        self.reader.workers = [(Mock(compare=fail), []), (Mock(compare=finish), [])]
        with self.assertRaisesRegex(RuntimeError, "候选识别出错"):
            self.reader.match(self.image[:54, :68])
        self.assertTrue(finished.is_set())

    def test_recognition_error_is_reported(self):
        self.reader.tasker.post_recognition.return_value.wait.return_value.get.return_value = None
        with self.assertRaisesRegex(RuntimeError, "模板识别出错"):
            self.reader.score(self.image, "phase.png")

    def test_capture_and_result_use_same_frame_and_cached_reader(self):
        session = Session(1, "framepool")
        session.protagonist = Mock()
        session.protagonist.stage.return_value = None
        session.controller = Mock()
        job = session.controller.post_screencap.return_value.wait.return_value
        job.succeeded = True
        job.get.return_value = self.image
        session.save_image = Mock(return_value={"screenshot": "frame.png"})
        with patch("screens.preparation.Preparation") as reader:
            reader.return_value.recognize.return_value = {"result": "success", "screen": "备战"}
            for _ in range(2):
                self.assertEqual(session.observe_preparation()["screenshot"], "frame.png")
            reader.assert_called_once_with()
            self.assertTrue(all(call.args[0] is self.image for call in reader.return_value.recognize.call_args_list))
        self.assertTrue(all(call.args[0] is self.image for call in session.save_image.call_args_list))
        session.controller.post_click.assert_not_called()
        job.succeeded = False
        with self.assertRaisesRegex(RuntimeError, "备战截图失败"):
            session.observe_preparation()


if __name__ == "__main__":
    unittest.main()

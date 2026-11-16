"""备战标题坐标、节点读取和单帧 OCR 复用。"""

import unittest
from unittest.mock import Mock, call

import numpy as np

from game.slots import HEADER, PHASE, SHOP, SHOP_OPEN
from screens.preparation import Preparation, layout, parse_header


def text(value, box):
    return {"text": value, "box": list(box)}


CLOSED = [text("备战阶段", (430, 33, 80, 24)), text("3-4", (443, 63, 54, 28))]
OPEN = [text("备战阶段", (244, 59, 86, 22)), text("3-4", (262, 93, 52, 29))]


class HeaderParseTests(unittest.TestCase):
    def test_both_layouts_confirm_title_and_node_at_actual_positions(self):
        for texts, expected in ((CLOSED, "closed"), (OPEN, "open")):
            with self.subTest(layout=expected):
                self.assertEqual(parse_header(texts), {
                    "layout": expected, "phase_box": texts[0]["box"], "node": "3-4"})

    def test_title_at_center_or_other_position_does_not_confirm_preparation(self):
        for box in ((875, 485, 170, 40), (600, 30, 80, 24), (430, 0, 80, 24)):
            with self.subTest(box=box):
                self.assertIsNone(parse_header([text("备战阶段", box)])["layout"])

    def test_full_title_box_must_stay_within_expected_region(self):
        left, top, width, height = PHASE
        self.assertEqual(parse_header([text("备战阶段", PHASE)])["layout"], "closed")
        self.assertIsNone(parse_header([text("备战阶段", (left - 1, top, width, height))])["layout"])
        self.assertIsNone(parse_header([text("备战阶段", (left, top, width + 1, height))])["layout"])

    def test_other_phase_or_empty_results_are_not_preparation(self):
        for texts in ([], [text("战斗阶段", (430, 33, 80, 24))], [text("备战", (430, 33, 80, 24))]):
            self.assertEqual(parse_header(texts), {"layout": None, "phase_box": None, "node": None})

    def test_missing_or_bad_node_does_not_invalidate_correct_phase(self):
        for value in ("", "3/4", "3-?", "3-4提示"):
            result = parse_header([CLOSED[0], text(value, (443, 63, 54, 28))])
            self.assertEqual(result["layout"], "closed")
            self.assertIsNone(result["node"])

    def test_node_must_belong_to_the_detected_layout(self):
        result = parse_header([CLOSED[0], OPEN[1], text("2-5", (900, 900, 50, 30))])
        self.assertIsNone(result["node"])

    def test_title_and_node_allow_ocr_whitespace(self):
        result = parse_header([text("备战 阶段", CLOSED[0]["box"]), text("3 - 4", CLOSED[1]["box"])])
        self.assertEqual(result["layout"], "closed")
        self.assertEqual(result["node"], "3-4")


class HeaderReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = Preparation.__new__(Preparation)
        self.reader.header_image = None
        self.reader.header_result = None
        self.reader.load_ocr = Mock()
        self.reader.text_boxes = Mock(return_value=CLOSED)
        self.reader.score = Mock(return_value=0.9)
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_page_and_layout_share_one_ocr_and_return_node_to_caller(self):
        page = self.reader.page(self.image)
        result = layout(self.image, self.reader)
        self.assertEqual(page["node"], "3-4")
        self.assertEqual(result["node"], "3-4")
        self.assertEqual(result["phase_box"], CLOSED[0]["box"])
        self.reader.text_boxes.assert_called_once_with(self.image, "备战标题与节点", HEADER)
        self.reader.score.assert_has_calls([call(self.image, "shop.png", SHOP)] * 2)
        self.assertTrue(all("phase" not in c.args[1] for c in self.reader.score.call_args_list))

    def test_new_frame_updates_progress_and_does_not_keep_previous_node(self):
        self.reader.text_boxes.side_effect = [CLOSED, [CLOSED[0]]]
        self.assertEqual(self.reader.header(self.image)["node"], "3-4")
        self.assertIsNone(self.reader.header(self.image.copy())["node"])
        self.assertEqual(self.reader.text_boxes.call_count, 2)

    def test_open_layout_uses_ocr_position_and_existing_close_button_anchor(self):
        self.reader.text_boxes.return_value = OPEN
        result = layout(self.image, self.reader)
        self.assertEqual(result["shop"], "open")
        self.assertEqual(result["node"], "3-4")
        self.reader.score.assert_called_once_with(self.image, "shop_open.png", SHOP_OPEN)
        self.reader.text_boxes.assert_called_once()

    def test_open_title_without_close_button_is_not_confirmed(self):
        self.reader.text_boxes.return_value = OPEN
        self.reader.score.return_value = 0.89
        self.assertIsNone(layout(self.image, self.reader))

    def test_correct_closed_title_still_requires_an_auxiliary_anchor(self):
        self.reader.score.return_value = 0.89
        self.assertEqual(self.reader.page(self.image)["result"], "uncertain")
        self.assertEqual(self.reader.text_boxes.call_count, 1)

    def test_bad_title_stops_before_auxiliary_matching(self):
        self.reader.text_boxes.return_value = []
        self.assertEqual(self.reader.page(self.image)["result"], "uncertain")
        self.reader.score.assert_not_called()

    def test_ocr_error_is_not_cached_as_missing_title(self):
        self.reader.text_boxes.side_effect = RuntimeError("备战 OCR 详情缺失")
        with self.assertRaisesRegex(RuntimeError, "OCR 详情缺失"):
            self.reader.header(self.image)
        self.assertIsNone(self.reader.header_image)
        self.assertIsNone(self.reader.header_result)


if __name__ == "__main__":
    unittest.main()

"""备战数值解析、同帧读取与 OCR 错误。"""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from flow.prepare import PreparationFlow
from game.slots import NUMBERS
from recognition_debug import trace
from screens.preparation import Preparation, parse_numbers
from service import Session


def texts(**changes):
    return {"gold": "31", "level": "Lv.5", "experience": "6/20",
            "experience_cost": "4", "deployment": "5/5", **changes}


class NumberTests(unittest.TestCase):
    def test_health_from_saved_preparation_frames(self):
        fixtures = Path(__file__).parent / "fixtures" / "preparation"
        reader = Preparation()
        self.addCleanup(reader.close)
        for name, health in (("gold-eight.webp", 80), ("level-five-hover.webp", 88),
                             ("deployment-six.webp", 92), ("health-hundred.webp", 100)):
            with self.subTest(frame=name), Image.open(fixtures / name) as source:
                image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
                self.assertEqual(reader.health(image), health)

    def test_deployment_region_reads_six_of_six(self):
        path = Path(__file__).parent / "fixtures" / "preparation" / "deployment-six.webp"
        with Image.open(path) as source:
            image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        reader = Preparation()
        self.addCleanup(reader.close)
        self.assertEqual(reader.numbers(image)["deployment"], {"current": 6, "limit": 6})

    def test_level_region_reads_hovered_level_five(self):
        path = Path(__file__).parent / "fixtures" / "preparation" / "level-five-hover.webp"
        with Image.open(path) as source:
            image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        reader = Preparation()
        self.addCleanup(reader.close)
        self.assertEqual(reader.numbers(image)["level"], 5)

    def test_gold_region_reads_eight_from_saved_preparation_frame(self):
        path = Path(__file__).parent / "fixtures" / "preparation" / "gold-eight.webp"
        with Image.open(path) as source:
            image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        reader = Preparation()
        self.addCleanup(reader.close)
        reader.load_ocr()
        self.assertEqual(reader.integer(image, "gold", NUMBERS["gold"]), 8)

    def test_gold_region_reads_three_digits_from_saved_preparation_frame(self):
        path = Path(__file__).parent / "fixtures" / "armament" / "advanced-bench.webp"
        with Image.open(path) as source:
            image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        reader = Preparation()
        self.addCleanup(reader.close)
        reader.load_ocr()
        self.assertEqual(reader.integer(image, "gold", NUMBERS["gold"]), 132)

    def test_zero_and_whitespace_are_parsed_from_all_five_fields(self):
        result = parse_numbers(texts(gold=" 0 ", experience="0 / 20", level="Lv. 5"))
        self.assertEqual(result, {"result": "success", "gold": 0, "level": 5,
                                  "experience": {"current": 0, "required": 20},
                                  "experience_cost": 4, "deployment": {"current": 5, "limit": 5}})

    def test_incomplete_field_keeps_other_values(self):
        for field, value in (("gold", "3O"), ("level", "等级5"), ("experience", "6/"),
                             ("experience_cost", ""), ("deployment", "5/5/5")):
            with self.subTest(field=field):
                result = parse_numbers(texts(**{field: value}))
                self.assertEqual(result["result"], "uncertain")
                self.assertTrue(result["needs_ai"])
                if field in {"experience", "deployment"}:
                    self.assertTrue(all(value is None for value in result[field].values()))
                else:
                    self.assertIsNone(result[field])
                if field != "gold":
                    self.assertEqual(result["gold"], 31)

    def test_ocr_loads_once_and_uses_one_frame_per_observation(self):
        reader = Preparation.__new__(Preparation)
        reader.resource = Mock()
        reader.ocr_ready = False
        reader.text = Mock(side_effect=lambda image, name, roi: texts()[name])
        first, second = object(), object()
        self.assertEqual(reader.numbers(first)["gold"], 31)
        self.assertEqual(reader.numbers(second)["experience"]["required"], 20)
        reader.resource.use_cpu.assert_called_once()
        reader.resource.post_ocr_model.assert_called_once()
        self.assertEqual(reader.text.call_count, 10)
        self.assertTrue(all(call.args[0] is first for call in reader.text.call_args_list[:5]))
        self.assertTrue(all(call.args[0] is second for call in reader.text.call_args_list[5:]))
        self.assertEqual({call.args[1]: call.args[2] for call in reader.text.call_args_list[:5]}, NUMBERS)

    def test_model_failure_is_reported_before_reading_regions(self):
        reader = Preparation.__new__(Preparation)
        reader.resource = Mock()
        reader.ocr_ready = False
        reader.text = Mock()
        reader.resource.post_ocr_model.return_value.wait.return_value.succeeded = False
        with self.assertRaisesRegex(RuntimeError, "备战 OCR 模型加载失败"):
            reader.numbers(object())
        self.assertFalse(reader.ocr_ready)
        reader.text.assert_not_called()

    def test_ocr_error_and_no_text_have_different_results(self):
        reader = Preparation.__new__(Preparation)
        reader.tasker = Mock()
        job = reader.tasker.post_recognition.return_value.wait.return_value
        job.get.return_value = None
        with self.assertRaisesRegex(RuntimeError, "备战 OCR 详情缺失：gold"):
            reader.text(object(), "gold", NUMBERS["gold"])
        job.get.return_value = Mock(nodes=[Mock(recognition=Mock(filtered_results=[]))])
        self.assertEqual(reader.text(object(), "gold", NUMBERS["gold"]), "")

    def test_number_ocr_reports_its_actual_roi_to_overlay(self):
        reader = Preparation.__new__(Preparation)
        reader.tasker = Mock()
        reader.tasker.post_recognition.return_value.wait.return_value.get.return_value = Mock(
            nodes=[Mock(recognition=Mock(filtered_results=[Mock(text="31", box=(1638, 906, 42, 28))]))])
        image = np.zeros((1080, 1920, 3), dtype="uint8")
        observer = Mock()
        with trace(observer):
            self.assertEqual(reader.text(image, "gold", NUMBERS["gold"]), "31")
        observer.mark.assert_called_once_with(image, NUMBERS["gold"], "gold", "ocr")
        self.assertEqual(reader.tasker.post_recognition.call_args.args[1].roi, NUMBERS["gold"])

    def test_slots_and_numbers_share_frame_and_unknown_values_replace_previous_values(self):
        reader = Preparation.__new__(Preparation)
        reader.header = Mock(return_value={"node": "1-1"})
        reader.action_button = Mock(return_value={"button_text": "出战", "next_action": "start-battle"})
        reader.capacity_warning = Mock(return_value={})
        slots = {"front_1": None, **{f"bench_{i}": None for i in range(1, 10)}}
        reader.slots = Mock(return_value={"slots": slots, "backstage_count": 6})
        reader.inventory = Mock()
        reader.inventory.read.return_value = None
        reader.special_items = Mock(return_value={})
        reader.emblems = Mock()
        reader.emblems.read.return_value = {}
        reader.numbers = Mock(side_effect=[parse_numbers(texts()), parse_numbers(texts(gold=""))])
        reader.health = Mock(side_effect=[80, None])
        first, second = object(), object()
        self.assertEqual(reader.observe(first)["gold"], 31)
        result = reader.observe(second)
        self.assertIsNone(result["health"])
        self.assertIsNone(result["gold"])
        self.assertEqual(result["slots"], slots)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual([call.args[0] for call in reader.slots.call_args_list], [first, second])
        self.assertEqual([call.args[0] for call in reader.numbers.call_args_list], [first, second])


    def test_prepare_reads_numbers_once_after_collection_and_does_not_retry_partial_values(self):
        session = Mock()
        session.protagonist_reader.return_value.selection_page.return_value = False
        session.supply_reader.return_value.page.return_value = False
        session.encounter_reader.return_value.page.return_value = False
        session.strategy_reader.return_value.page.return_value = False
        job = session.controller.post_screencap.return_value.wait.return_value
        job.succeeded = True
        job.get.side_effect = ["before", "after"]
        session.preparation_reader.return_value.score.return_value = 0
        session.preparation_reader.return_value.action_button.return_value = {"next_action": None}
        session.preparation_reader.return_value.observe.return_value = {
            "slots": {"front_1": None}, **parse_numbers(texts(experience=""))}
        session.save_image.return_value = {"screenshot": "after.png"}
        with patch("flow.preparation_scene.layout", return_value={"result": "success", "screen": "备战", "shop": "closed"}), \
                patch("flow.prepare.sweep") as sweep:
            result = PreparationFlow(session).enter()
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["gold"], 31)
        self.assertEqual(result["slots"], {"front_1": None})
        session.preparation_reader.return_value.observe.assert_called_once_with("after")
        session.save_image.assert_called_once_with("after")
        self.assertEqual(job.get.call_count, 2)
        sweep.assert_called_once()
        session.controller.post_click.assert_not_called()

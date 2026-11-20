"""库存框定位、固定样例与备战观察集成。"""

from pathlib import Path
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from game.equipment import ASSETS, Equipment
from recognition_debug import trace
from screens.inventory import AREA, BOTH_COLUMNS, Inventory
from screens.preparation import Preparation


FIXTURES = Path(__file__).parent / "fixtures" / "inventory"


def sample(name="six-items"):
    image = np.zeros((1080, 1920, 3), dtype="uint8")
    with Image.open(FIXTURES / f"{name}.png") as crop:
        x, y, width, height = AREA
        image[y:y+height, x:x+width] = np.asarray(crop.convert("RGB"))[:, :, ::-1]
    return image


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = Inventory()

    def test_six_items_keep_duplicate_objects_in_separate_slots(self):
        result = self.reader.read(sample())
        self.assertEqual(list(result), [f"bag_r{i}_c1" for i in range(1, 7)])
        self.assertEqual([item["id"] for item in result.values()],
                         ["peace_pistol", "lucky_star", "solar_battery", "lucky_star",
                          "peace_pistol", "solar_battery"])
        self.assertTrue(all(Path(item["asset"]).is_file() for item in result.values()))
        self.assertTrue(all(len(item["box"]) == 4 for item in result.values()))

    def test_earlier_layout_locates_frames_at_a_different_height(self):
        result = self.reader.read(sample("two-items"))
        self.assertEqual([item["id"] for item in result.values()], ["flower_of_life", "roller_skates"])
        self.assertEqual(result["bag_r1_c1"]["box"][1], 212)

    def test_missing_middle_frame_preserves_later_row_numbers(self):
        image = sample()
        image[313:388, 1810:1887] = 0
        result = self.reader.read(image)
        self.assertNotIn("bag_r2_c1", result)
        self.assertEqual(result["bag_r3_c1"]["id"], "solar_battery")
        self.assertEqual(result["bag_r6_c1"]["id"], "solar_battery")

    def test_items_outside_both_columns_are_ignored(self):
        image = sample()
        expected = self.reader.read(image)
        image[200:710, 1704:1790] = image[200:710, 1804:1890]
        self.assertEqual(self.reader.read(image), expected)

    def test_no_frames_returns_unknown_inventory_without_matching_icons(self):
        reader = Inventory.__new__(Inventory)
        reader.equipment = Mock()
        self.assertIsNone(reader.read(np.zeros((1080, 1920, 3), dtype="uint8")))
        reader.equipment.match.assert_not_called()

    def test_unrecognized_icon_keeps_slot_and_candidates(self):
        image = sample()
        image[238:307, 1814:1883] = np.random.default_rng(7).integers(0, 256, (69, 69, 3), dtype="uint8")
        result = self.reader.read(image)
        self.assertTrue(result["bag_r1_c1"]["unknown"])
        self.assertEqual(len(result["bag_r1_c1"]["candidates"]), 2)
        self.assertEqual(result["bag_r2_c1"]["id"], "lucky_star")

    def test_icon_matching_error_propagates(self):
        reader = Inventory.__new__(Inventory)
        reader.equipment = Mock()
        reader.equipment.match.side_effect = RuntimeError("匹配任务出错")
        with self.assertRaisesRegex(RuntimeError, "匹配任务出错"):
            reader.read(sample())

    def test_preparation_returns_inventory_from_same_frame_and_reports_crop_regions(self):
        reader = Preparation.__new__(Preparation)
        reader.header = Mock(return_value={"node": "1-1"})
        reader.action_button = Mock(return_value={"button_text": "出战", "next_action": "start-battle"})
        reader.capacity_warning = Mock(return_value={})
        reader.numbers = Mock(return_value={"result": "uncertain", "gold": None})
        reader.slots = Mock(return_value={"slots": {"front_1": None, **{f"bench_{i}": None for i in range(1, 10)}},
                                        "backstage_count": 6})
        reader.inventory = self.reader
        reader.emblems = Mock()
        reader.emblems.read.return_value = {}
        image = sample()
        observer = Mock()
        with trace(observer):
            result = reader.observe(image)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(len(result["inventory"]), 6)
        reader.numbers.assert_called_once_with(image)
        reader.slots.assert_called_once_with(image)
        calls = observer.mark.call_args_list
        self.assertEqual(calls[0].args[1:], (BOTH_COLUMNS, "右侧库存定位", "crop"))
        self.assertEqual(len(calls), 7)
        for call, (slot, item) in zip(calls[1:], result["inventory"].items()):
            x, y, width, height = item["box"]
            self.assertIs(call.args[0], image)
            self.assertEqual(call.args[1:], ((x+2, y+2, width-4, height-4), slot, "crop"))


class EquipmentTests(unittest.TestCase):
    def test_shared_matcher_recognizes_asset_and_reports_constant_or_ambiguous_icons(self):
        reader = Equipment()
        with Image.open(ASSETS / "peace_pistol.png") as source:
            icon = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        self.assertEqual(reader.match(icon)["id"], "peace_pistol")
        self.assertEqual(reader.match(np.zeros((69, 69, 3), dtype="uint8")),
                         {"unknown": True, "candidates": []})
        index = next(i for i, path in enumerate(reader.paths) if path.stem == "peace_pistol")
        reader.vectors = np.stack([reader.vectors[index], reader.vectors[index]])
        reader.paths = [Path("first.png"), Path("second.png")]
        result = reader.match(icon)
        self.assertTrue(result["unknown"])
        self.assertEqual(len(result["candidates"]), 2)

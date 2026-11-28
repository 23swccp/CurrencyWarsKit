"""备战识别与只读观察测试。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, get_ident
import unittest
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

from recognition_debug import trace
from screens.preparation import Preparation, Recognition, progress
from service import Session
from game.slots import ACTION_BUTTON, BENCH, BENCH_WARNING, CARDS, FRONT, ICONS, PHASE, SHOP


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.reader = Preparation.__new__(Preparation)
        self.reader.resource = Mock()
        self.reader.tasker = Mock()
        self.reader.score = Mock(return_value=0)
        self.reader.numbers = Mock(return_value={"result": "success"})
        self.reader.health = Mock(return_value=80)
        self.reader.header = Mock(return_value={"layout": "closed", "node": "1-1", "phase_box": [430, 33, 80, 24]})
        self.reader.action_button = Mock(return_value={"button_text": "出战", "next_action": "start-battle"})
        self.reader.inventory = Mock()
        self.reader.inventory.read.return_value = None
        self.reader.special_items = Mock(return_value={})
        self.reader.emblems = Mock()
        self.reader.emblems.read.return_value = {}
        self.reader.portraits = [("云璃", [None, None]), ("火花", [None, None])]
        self.reader.workers = [(Mock(), self.reader.portraits)]
        self.reader.pool = ThreadPoolExecutor(max_workers=4)
        self.addCleanup(self.reader.close)
        self.image = np.zeros((1080, 1920, 3), dtype="uint8")

    def test_other_page_stops_before_slots(self):
        self.reader.header.return_value = {"layout": None, "node": None, "phase_box": None}
        self.reader.score = Mock(return_value=0.69)
        self.reader.match = Mock()
        result = self.reader.recognize(self.image)
        self.assertEqual(result["result"], "uncertain")
        self.assertNotIn("slots", result)
        self.reader.match.assert_not_called()

    def test_observation_returns_active_trait_and_recruit_candidates(self):
        slots = {slot: None for slot in CARDS}
        slots.update(front_1={"name": "那刻夏"}, back_1={"name": "白露"}, bench_1={"name": "星期日"})
        self.reader.slots = Mock(return_value={"slots": slots, "backstage_count": 6})
        self.reader.score.return_value = 0.9
        self.reader.emblems.read.return_value = {"front_1": ["interwoven_dreams_emblem"]}

        result = self.reader.recognize(self.image)["trait_projection"]

        self.reader.emblems.read.assert_called_once_with(self.image, slots)
        self.assertEqual(result["basis"], "native_and_emblems")
        self.assertIn("梦境交织", [trait["name"] for trait in result["current"]])
        chain = next(trait for trait in result["current"] if trait["name"] == "连击")
        self.assertEqual(chain, {"name": "连击", "members": ["front_1", "back_1"], "count": 2,
                                 "tier": 2, "next_tier": 4})
        plan = next(option for option in result["combinations"] if option["name"] == "连击")
        self.assertEqual((plan["target_tier"], plan["needed"]), (4, 2))
        self.assertIn("星期日", plan["candidates"])
        self.assertNotIn("那刻夏", plan["candidates"])

    def test_ocr_title_and_shop_at_threshold_pass_without_icons(self):
        self.reader.score = Mock(return_value=0.9)
        result = self.reader.page(self.image)
        self.assertEqual(result, {"result": "success", "screen": "备战", "node": "1-1", "phase_box": [430, 33, 80, 24]})
        self.reader.score.assert_called_once_with(self.image, "shop.png", SHOP)

    def test_icons_confirm_page_when_shop_is_not_visible(self):
        self.reader.score = Mock(side_effect=[0.4, 0.9])
        self.assertEqual(self.reader.page(self.image)["result"], "success")
        self.assertEqual(self.reader.score.call_args_list, [
            call(self.image, "shop.png", SHOP),
            call(self.image, "icons.png", ICONS)])

    def test_phase_alone_stops_before_slots(self):
        self.reader.score = Mock(side_effect=[0.89, 0.89])
        self.reader.match = Mock()
        result = self.reader.recognize(self.image)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["anchors"], {"shop": 0.89, "icons": 0.89})
        self.assertNotIn("slots", result)
        self.reader.match.assert_not_called()

    def test_auxiliary_anchors_cannot_replace_phase(self):
        self.reader.header.return_value = {"layout": None, "node": None, "phase_box": None}
        self.reader.score = Mock(return_value=1)
        self.assertEqual(self.reader.page(self.image)["result"], "uncertain")
        self.reader.score.assert_not_called()

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

    def backstage_frame(self, name):
        fixture = Path(__file__).with_name("fixtures") / "preparation" / f"backstage-{name}.png"
        with Image.open(fixture) as band:
            self.image[593:602] = np.asarray(band)[:, :, ::-1]
        return self.image

    def test_progress_splits_plane_index_and_leaves_unknown_nodes_empty(self):
        for node, expected in (("2-4", (2, 4, 7, 3)), ("1-9", (1, 9, 9, 0)),
                               ("2-7", (2, 7, 7, 0)), ("3-7", (3, 7, 7, 0)),
                               ("3-5", (3, 5, 7, 2)), ("4-1", (4, 1, None, None)),
                               ("2-9", (2, 9, None, None)), ("1-0", (1, 0, None, None)),
                               ("1-10", (1, 10, None, None)), ("3-8", (3, 8, None, None))):
            with self.subTest(node=node):
                result = progress(node)
                self.assertEqual(tuple(result[key] for key in ("plane", "index", "nodes", "remaining")), expected)
        self.assertIsNone(progress("4-1")["node_reference"])
        self.assertIsNone(progress("2-9")["node_reference"])
        self.assertIsNone(progress("1-0")["node_reference"])
        self.assertEqual(progress(None), {})
        self.assertEqual(progress("未知"), {})

    def test_user_node_reference_preserves_event_alternatives(self):
        first = progress("1-1")["node_reference"]
        self.assertEqual(first["source"], "user_example")
        self.assertEqual([entry["possible_types"] for entry in first["nodes"]], [
            ["奖励"], ["奖励"], ["战斗"], ["战斗"], ["补给"], ["战斗"],
            ["遭遇"], ["奖励", "幕后茶歇"], ["首领"]])
        self.assertEqual(progress("1-9")["node_reference"]["nodes"],
                         [{"node": "1-9", "possible_types": ["首领"]}])
        for plane in (2, 3):
            entries = progress(f"{plane}-2")["node_reference"]["nodes"]
            self.assertEqual([entry["node"] for entry in entries], [f"{plane}-{index}" for index in range(2, 8)])
            self.assertEqual([entry["possible_types"] for entry in entries], [
                ["投资策略"], ["补给"], ["战斗"], ["遭遇"], ["奖励", "幕后茶歇"], ["首领"]])

    def test_positions_come_from_roster_and_skip_unknown_cards(self):
        reader = Preparation.__new__(Preparation)
        self.assertEqual(reader.positions({"name": "寒鸦"}), {"positions": ["back"]})
        self.assertEqual(reader.positions({"name": "缇宝"}), {"positions": ["front", "back"]})
        self.assertEqual(reader.positions({"unknown": True, "candidates": []}), {})

    def test_empty_and_unknown_slots_are_distinct(self):
        self.reader.score = Mock(side_effect=[0.9, 0.699] + [0.9] * 18 + [0])
        self.reader.match = Mock(return_value={"unknown": True, "candidates": []})
        slots = self.reader.recognize(self.backstage_frame("six"))["slots"]
        self.assertEqual(set(slots), set(CARDS))
        self.assertTrue(slots["front_1"]["unknown"])
        self.assertIsNone(slots["front_1"]["stars"])
        self.assertTrue(all(value is None for slot, value in slots.items() if slot != "front_1"))
        self.reader.match.assert_called_once()

    def test_observation_reads_gold_and_blue_stars_independently_of_identity(self):
        image = self.backstage_frame("seven")
        fixture = Path(__file__).with_name("fixtures") / "preparation" / "front-stars.png"
        with Image.open(fixture) as band:
            image[425:470, 677:1242] = np.asarray(band)[:, :, ::-1]
        self.reader.score.return_value = 0.9
        self.reader.empty = Mock(side_effect=lambda image, slot, backstage: not slot.startswith("front_"))
        self.reader.match = Mock(side_effect=[{"unknown": True, "candidates": []}]
                                 + [{"name": "云璃"}] * 3)
        observer = Mock()

        with trace(observer):
            result = self.reader.recognize(image)
        slots = result["slots"]

        fronts = [slots[f"front_{index}"] for index in range(1, 5)]
        self.assertEqual([card["stars"] for card in fronts], [3, 3, 2, 1])
        self.assertTrue(fronts[0]["unknown"])
        self.assertEqual(fronts[1]["name"], "云璃")
        self.assertEqual(fronts[1]["positions"], ["front", "back"])
        self.assertEqual(result["front_capacity"], 4)
        self.assertEqual((result["plane"], result["index"], result["nodes"], result["remaining"]), (1, 1, 9, 8))
        self.assertEqual(result["node_reference"]["nodes"][0],
                         {"node": "1-1", "possible_types": ["奖励"]})
        observer.mark.assert_any_call(image, (697, 427, 91, 40), "front_1 星级", "crop")

    def test_same_character_can_occupy_multiple_slots(self):
        self.reader.score = Mock(side_effect=[0.9, 0.2, 0.2] + [1] * 17 + [0])
        self.reader.match = Mock(return_value={"name": "云璃"})
        slots = self.reader.recognize(self.backstage_frame("six"))["slots"]
        self.assertEqual(slots["front_1"]["name"], "云璃")
        self.assertEqual(slots["front_2"]["name"], "云璃")

    def test_seven_backstage_matches_vacancies_in_real_frame_regions(self):
        reader = Preparation()
        self.addCleanup(reader.close)
        reader.match = Mock(return_value={"unknown": True, "candidates": []})
        image = self.backstage_frame("seven")
        fixtures = Path(__file__).with_name("fixtures") / "preparation"
        for name, first_vacant in (("vacant", 4), ("occupied", 6)):
            with self.subTest(name=name), Image.open(fixtures / f"backstage-seven-{name}.png") as band:
                image[641:699, 510:1410] = np.asarray(band.convert("RGB"))[:, :, ::-1]
                result = reader.slots(image)
                self.assertEqual(result["backstage_count"], 7)
                slots = result["slots"]
                for index in range(1, first_vacant):
                    self.assertTrue(slots[f"back_{index}"]["unknown"])
                for index in range(first_vacant, 8):
                    self.assertIsNone(slots[f"back_{index}"])

    def test_seven_backstage_without_empty_template_does_not_guess_vacancy(self):
        self.reader.score.return_value = 1
        self.reader.match = Mock(return_value={"unknown": True, "candidates": []})
        for index in range(1, 4):
            self.assertTrue(self.reader.card(self.image, f"back_{index}", 7)["unknown"])
        self.reader.score.assert_not_called()

    def test_unconfirmed_backstage_preserves_known_coordinates_and_partial_traits(self):
        self.reader.score.side_effect = lambda image, name, roi: 0 if name == "front_1.png" else 0.9
        self.reader.match = Mock(return_value={"name": "那刻夏"})
        self.reader.inventory.read.return_value = {"bag_r1_c1": {"id": "antigravity_boots"}}
        self.reader.numbers.return_value = {"result": "success", "gold": 31}

        result = self.reader.observe(self.image)

        self.assertEqual(result["result"], "uncertain")
        self.assertIsNone(result["backstage_count"])
        self.assertEqual(set(result["slots"]), set(FRONT) | set(BENCH))
        self.assertEqual(result["slots"]["front_1"]["name"], "那刻夏")
        self.assertIsNone(result["slots"]["bench_9"])
        self.assertEqual(result["gold"], 31)
        self.assertEqual(result["reason"], "character_overflow")
        self.assertEqual(result["inventory"], self.reader.inventory.read.return_value)
        self.reader.emblems.read.assert_called_once_with(self.image, result["slots"])
        self.assertFalse(any(call.args[1].startswith("back_") for call in self.reader.score.call_args_list))
        traits = result["trait_projection"]
        self.assertTrue(traits["partial"])
        self.assertEqual(traits["combinations"], [])
        chain = next(trait for trait in traits["current"] if trait["name"] == "连击")
        self.assertEqual(chain, {"name": "连击", "members": ["front_1"], "count": 1,
                                 "tier": None, "next_tier": None})

    def test_full_bench_and_warning_require_sale_with_known_or_unknown_field(self):
        for card in ({"name": "云璃"}, {"unknown": True, "candidates": []}):
            with self.subTest(card=card):
                slots = {name: None for name in CARDS}
                slots.update({f"bench_{i}": {"name": "云璃"} for i in range(1, 10)})
                slots["front_4"] = card
                self.reader.slots = Mock(return_value={"slots": slots, "backstage_count": 6})
                self.reader.score = Mock(return_value=0.9)
                result = self.reader.observe(self.image)
                self.assertEqual(result["reason"], "character_overflow")
                self.assertEqual(result["required_action"], "sell")
                self.assertTrue(result["needs_ai"])
                self.assertIn("角色卡溢出", result["message"])
                self.assertIs(result["slots"], slots)
                self.reader.slots.assert_called_once_with(self.image)
                self.reader.score.assert_called_once_with(self.image, "bench_warning.png", BENCH_WARNING)

    def test_warning_with_bench_vacancy_still_requires_sale(self):
        slots = {name: {"name": "云璃"} for name in CARDS}
        slots.update(bench_9=None, front_4={"unknown": True})
        self.reader.slots = Mock(return_value={"slots": slots, "backstage_count": 6})
        self.reader.score.return_value = 1
        result = self.reader.observe(self.image)
        self.assertEqual(result["reason"], "character_overflow")
        self.assertEqual(result["required_action"], "sell")
        self.reader.score.assert_called_once_with(self.image, "bench_warning.png", BENCH_WARNING)

    def test_no_warning_with_bench_vacancy_has_no_capacity_request(self):
        self.reader.slots = Mock(return_value={"slots": {name: None for name in CARDS}, "backstage_count": 6})
        result = self.reader.observe(self.image)
        self.assertNotIn("reason", result)
        self.assertNotIn("required_action", result)

    def test_full_bench_and_all_unknown_field_without_warning_is_normal_full(self):
        slots = {name: {"unknown": True} for name in CARDS}
        self.reader.slots = Mock(return_value={"slots": slots, "backstage_count": 6})
        self.reader.score.return_value = 0.899
        result = self.reader.observe(self.image)
        self.assertEqual(result["reason"], "bench_full")
        self.assertNotIn("required_action", result)

    def test_full_bench_without_unknown_field_keeps_normal_full_status(self):
        slots = {name: None for name in CARDS}
        slots.update({f"bench_{i}": {"name": "云璃"} for i in range(1, 10)})
        slots["bench_6"] = {"unknown": True}
        self.reader.slots = Mock(return_value={"slots": slots, "backstage_count": 6})
        result = self.reader.observe(self.image)
        self.assertEqual(result["reason"], "bench_full")
        self.assertTrue(result["needs_ai"])
        self.assertNotIn("required_action", result)

    def test_overflow_sale_request_survives_missing_numbers(self):
        slots = {name: {"name": "云璃"} for name in CARDS}
        slots["back_4"] = {"unknown": True}
        self.reader.slots = Mock(return_value={"slots": slots, "backstage_count": 6})
        self.reader.score.return_value = 1
        self.reader.numbers.return_value = {"result": "uncertain", "level": None,
                                           "message": "备战数值待确认"}
        result = self.reader.observe(self.image)
        self.assertEqual(result["result"], "uncertain")
        self.assertIsNone(result["level"])
        self.assertEqual(result["required_action"], "sell")
        self.assertIn("角色卡溢出", result["message"])

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
            Recognition.score(self.reader, self.image, "shop.png")



if __name__ == "__main__":
    unittest.main()

"""原生羁绊的当前档位、候选与未知贡献。"""

import unittest

from game.traits import projection


def by_name(rows):
    return {row["name"]: row for row in rows}


class TraitProjectionTests(unittest.TestCase):
    def test_exact_count_and_unknown_slots(self):
        slots = {"front_1": {"name": "火花"}}
        self.assertEqual(by_name(projection(slots, {})["current"])["少女乐队"]["tier"], 1)
        slots["back_1"] = {"name": "知更鸟"}
        result = projection(slots, {})
        band = by_name(result["current"])["少女乐队"]
        self.assertIsNone(band["tier"])
        plan = by_name(result["combinations"])["少女乐队"]
        self.assertEqual(plan["target_tier"], 4)
        self.assertEqual(plan["needed"], 2)
        self.assertCountEqual(plan["candidates"], ["乱破", "希露瓦"])
        slots["back_2"] = {"unknown": True, "candidates": [{"name": "乱破"}]}
        slots["back_3"] = {"name": "星神★阿哈"}
        result = projection(slots, {})
        self.assertEqual(result["unresolved_slots"], ["back_2", "back_3"])
        self.assertNotIn("少女乐队", by_name(result["combinations"]))

    def test_duplicate_slot_keeps_count_and_discloses_extra_card(self):
        slots = {"front_1": {"name": "那刻夏"}, "front_2": {"name": "那刻夏"},
                 "bench_1": {"name": "星期日"}}
        result = projection(slots, {})
        self.assertEqual(result["duplicate_slots"], ["front_2"])
        chain = by_name(result["current"])["连击"]
        self.assertEqual(chain["count"], 1)
        self.assertIsNone(chain["tier"])
        self.assertIn("星期日", by_name(result["combinations"])["连击"]["candidates"])

    def test_variant_disagreement_and_star_requirement_are_not_guessed(self):
        slots = {f"front_{index}": {"name": name} for index, name in enumerate(
            ("火花", "真珠", "星期日", "绯英", "银狼LV.999", "千冶•刃", "不死途"), 1)}
        current = by_name(projection(slots, {})["current"])
        self.assertEqual(current["幻月游戏"]["tier"], 7)
        self.assertIsNone(current["幻月游戏"]["next_tier"])
        self.assertIsNone(current["千星商会"]["tier"])
        self.assertIsNone(current["巡海游侠"]["tier"])

    def test_emblem_grants_trait_without_counting_native_members_twice(self):
        slots = {"front_1": {"name": "那刻夏"}, "front_2": {"name": "花火"},
                 "bench_1": {"name": "白露"}}
        emblems = {"front_1": ["interwoven_dreams_emblem"], "front_2": ["interwoven_dreams_emblem"],
                   "bench_1": ["interwoven_dreams_emblem"]}
        result = projection(slots, emblems)
        dream = by_name(result["current"])["梦境交织"]
        self.assertEqual(dream, {"name": "梦境交织", "members": ["front_1", "front_2"],
                                 "count": 2, "tier": 2, "next_tier": 3})
        self.assertEqual(by_name(result["combinations"])["梦境交织"]["needed"], 1)

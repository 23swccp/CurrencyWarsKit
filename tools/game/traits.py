"""根据已识别的上阵角色与星徽推算羁绊和补位候选。"""

from collections import defaultdict
from functools import cache
import json
from pathlib import Path


DATA = Path(__file__).resolve().parents[2] / "data"


@cache
def catalog():
    return json.loads((DATA / "traits.json").read_text(encoding="utf-8"))


@cache
def roster():
    characters = json.loads((DATA / "characters.json").read_text(encoding="utf-8"))
    members = defaultdict(list)
    for name, character in characters.items():
        # 防：星神★阿哈仅有站位资料，未确认原生羁绊。
        for trait in character.get("traits", ()):
            members[trait].append(name)
    rules = catalog()
    return characters, {**rules["factions"], **rules["styles"]}, members


def levels(tiers, count, exact):
    reached = (tier for tier in tiers if tier == count or (not exact and tier < count))
    return max(reached, default=None), next((tier for tier in tiers if tier > count), None)


def projection(slots, worn_emblems, *, partial=False):
    characters, rules, members = roster()
    emblem_traits = catalog()["emblems"]
    lineup = set()
    contributors = defaultdict(list)
    unresolved_slots = []
    duplicate_slots = []
    duplicate_traits = set()
    for slot, card in slots.items():
        if not slot.startswith(("front_", "back_")) or card is None:
            continue
        # 防：头像漏读，保留该槽供核对。
        if "unknown" in card:
            unresolved_slots.append(slot)
            continue
        name = card["name"]
        traits = characters[name].get("traits")
        # 防：星神★阿哈缺少归属资料。
        if traits is None:
            unresolved_slots.append(slot)
            continue
        # 防：同名上阵的额外羁绊贡献尚未验证，保留重复位供核对。
        if name in lineup:
            duplicate_slots.append(slot)
            duplicate_traits.update(traits)
            continue
        lineup.add(name)
        granted = (emblem_traits[emblem] for emblem in worn_emblems.get(slot, ()))
        for trait in set(traits).union(granted):
            contributors[trait].append(slot)

    current = []
    combinations = []
    for trait, names in members.items():
        assigned = contributors[trait]
        count = len(assigned)
        tier = next_tier = None
        rule = rules.get(trait, {})
        exact = rule.get("exact_count", False)
        # 防：后台整排缺失，前台成员只提供人数下限。
        if not partial:
            if "variants" in rule:
                # 防：本局幻月游戏变体未读取，只有各变体一致时才给出档位。
                reached, upcoming = zip(*(levels(tiers, count, False) for tiers in rule["variants"].values()))
                tier = reached[0] if len(set(reached)) == 1 else None
                next_tier = upcoming[0] if len(set(upcoming)) == 1 else None
            elif "tiers" in rule:
                # 防：少女乐队必须人数恰好，未知或同名成员可能改变是否激活。
                if not exact or (not unresolved_slots and trait not in duplicate_traits):
                    tier, next_tier = levels(rule["tiers"], count, exact)
        if count:
            current.append({"name": trait, "members": assigned, "count": count, "tier": tier,
                            "next_tier": next_tier})
        if next_tier is not None and (count or next_tier == 1):
            needed = next_tier - count
            candidates = [name for name in names if name not in lineup]
            if len(candidates) >= needed:
                combinations.append({"name": trait, "target_tier": next_tier,
                                     "needed": needed, "candidates": candidates})
    combinations.sort(key=lambda option: option["needed"])
    return {"basis": "native_and_emblems", "partial": partial, "emblems": worn_emblems,
            "unresolved_slots": unresolved_slots, "duplicate_slots": duplicate_slots,
            "current": current, "combinations": combinations}

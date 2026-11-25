"""商店卡片识别、开关与单次刷新。"""

import re
from time import monotonic, sleep

import numpy as np
from PIL import Image

from game.input import ERRORS, InputUncertain, uncertain
from game.slots import NUMBERS, SHOP_TOGGLE
from game.traits import roster
from recognition_debug import focus
from screens.card_stars import row_count


TIMEOUT = 8
INTERVAL = 0.3
REFRESH = (1618, 524)
REFRESH_LABEL = (1555, 470, 135, 40)
FREE_COUNT = (1595, 512, 55, 51)
REFRESH_COST = (1628, 512, 42, 51)
CARD_CENTERS = (490, 759, 1027, 1295, 1564)
EMPTY_SCORE = 0.89
EQUIPMENT_SCORE = 0.9
NAME_CORRECTIONS = {"刻律德": "刻律德菈"}


def read_refresh(image, reader):
    reader.load_ocr()
    title = re.sub(r"\s+", "", reader.text(image, "刷新按钮", REFRESH_LABEL))
    mode = {"免费刷新": "free", "刷新": "paid"}.get(title, "unknown")
    count = reader.integer(image, "免费次数", FREE_COUNT) if mode == "free" else None
    cost = reader.integer(image, "刷新费用", REFRESH_COST) if mode == "paid" else None
    return {"refresh_mode": mode, "refresh_cost": 0 if mode == "free" else cost,
            "free_refreshes": 0 if mode == "paid" else count,
            "gold": reader.integer(image, "gold", NUMBERS["gold"])}


def complete(result):
    return result["refresh_mode"] != "unknown" and all(result[key] is not None
            for key in ("refresh_cost", "free_refreshes", "gold"))


def read_stars(image, slot, center):
    left, top, width, height = center - 60, 65, 120, 40
    focus(image, (left, top, width, height), f"{slot} 星级", "crop")
    band = Image.fromarray(image[top:top+height, left:left+width])
    band = band.resize((width // 2, height // 2), Image.Resampling.LANCZOS)
    blue, green, red = np.asarray(band, dtype=np.int16).transpose(2, 0, 1)
    # 收紧亮核阈值，分开相邻金星的光晕。
    gold = (red > 244) & (green > 215) & (green - blue > 17)
    return row_count(gold, minimum=2)


def card_name(text):
    name = re.sub(r"\s+", "", text)
    return NAME_CORRECTIONS.get(name, name)


def read_product(image, reader, slot, center, name, lines):
    # 正文从卡面左侧起排，角色的羁绊文字从图标右侧起排。
    description = "".join(line["text"].strip() for line in lines
                          if line["box"][0] < center - 84 and 115 <= line["box"][1] < 286)
    price = reader.integer(image, f"{slot} 商品价格", (center + 84, 287, 31, 35))
    result = {"name": name or None, "price": price, "description": description or None}
    if reader.score(image, "shop_equipment.png", (center - 63, 112, 42, 42)) >= EQUIPMENT_SCORE:
        result["kind"] = "equipment"
        if reader.score(image, "shop_aha.png", (center + 17, 193, 43, 42)) >= EQUIPMENT_SCORE:
            result["equip_target"] = "aha"
    else:
        result["kind"] = "effect" if description else "unknown"
    return result


def read_price(image, reader, slot, center):
    """卡底价格：字号很小，放大后再认；位置避开设在左侧的金币图标。"""
    text = reader.enlarged_text(image, f"{slot} 价格", (center + 72, 288, 50, 36))
    match = re.search(r"\d+", text)
    return int(match[0]) if match else None


def read_cards(image, reader):
    reader.load_ocr()
    characters, _, _ = roster()
    names = {}
    stars = {}
    prices = {}
    empty_slots = []
    goods = {}
    for index, center in enumerate(CARD_CENTERS, 1):
        slot = f"shop_{index}"
        if reader.score(image, "shop_empty.png", (center - 40, 96, 80, 200)) >= EMPTY_SCORE:
            names[slot] = stars[slot] = prices[slot] = None
            empty_slots.append(slot)
            continue
        name = card_name(reader.text(image, f"{slot} 卡底名称", (center - 114, 288, 170, 34)))
        if name.replace("·", "•") not in characters:
            lines = reader.text_boxes(image, f"{slot} 商品整卡", (center - 115, 66, 230, 259))
            footer = next((card_name(line["text"]) for line in lines
                           if line["box"][0] < center + 55 and line["box"][1] >= 288), "")
            if footer.replace("·", "•") in characters:
                name = footer
            else:
                name = name or footer
                goods[slot] = read_product(image, reader, slot, center, name, lines)
        names[slot] = name or None
        stars[slot] = None if slot in goods else read_stars(image, slot, center) or 1
        prices[slot] = None if slot in goods else read_price(image, reader, slot, center)
    return {"shop_slots": names, "shop_stars": stars, "shop_prices": prices,
            "shop_empty_slots": empty_slots, "shop_goods": goods}


def save(session, image, result, action, clicked):
    try:
        return {**session.save_image(image), **result, "action": action}
    except ERRORS as error:
        if clicked:
            raise InputUncertain(f"商店输入后保存截图出错：{type(error).__name__}: {error}") from error
        raise


def switch(session, target):
    image = session.preparation_frame()
    scene = session.preparation_scene(image)
    clicked = False
    result = {**scene, "result": "failed", "needs_ai": True, "message": "商店切换要求备战画面"}
    try:
        if scene["screen"] == "备战":
            if scene["shop"] != target:
                clicked = True
                # 防：切换点击任务失败。
                if not session.controller.post_click(*SHOP_TOGGLE).wait().succeeded:
                    raise InputUncertain("商店切换点击结果待核对")
                deadline = monotonic() + TIMEOUT
                while True:
                    sleep(INTERVAL)
                    image = session.preparation_frame()
                    scene = session.preparation_scene(image)
                    if scene["screen"] == "备战" and scene["shop"] == target:
                        break
                    if scene["screen"] not in {"备战", "未知"} or monotonic() >= deadline:
                        break
            result = {**scene, "result": "success" if scene["screen"] == "备战" and scene["shop"] == target
                      else "uncertain"}
            if result["result"] == "uncertain":
                result.update(needs_ai=True, message="商店切换结果待确认")
            elif target == "open":
                reader = session.preparation_reader()
                result.update(read_refresh(image, reader))
                result.update(read_cards(image, reader))
                if not complete(result):
                    result.update(result="uncertain", needs_ai=True, message="商店已展开，刷新信息待确认")
    except ERRORS as error:
        if not clicked:
            raise
        result = uncertain(error, "商店切换出错，附最近画面")
    return save(session, image, result, f"shop-{'open' if target == 'open' else 'close'}", clicked)


def refresh(session):
    image = session.preparation_frame()
    scene = session.preparation_scene(image)
    if scene["screen"] != "备战" or scene["shop"] != "open":
        return save(session, image, {**scene, "result": "failed", "needs_ai": True,
                                    "message": "刷新要求商店展开"}, "refresh", False)
    reader = session.preparation_reader()
    before = read_refresh(image, reader)
    # 防：按钮、次数、费用或金币漏读。
    if not complete(before):
        return save(session, image, {**scene, **before, "result": "uncertain", "needs_ai": True,
                                    "message": "刷新条件待确认"}, "refresh", False)
    free = before["refresh_mode"] == "free"
    # 防：免费次数耗尽或金币不足。
    if (free and before["free_refreshes"] == 0) or (not free and before["gold"] < before["refresh_cost"]):
        return save(session, image, {**scene, **before, "result": "failed", "needs_ai": True,
                                    "message": "免费次数或金币不足"}, "refresh", False)
    evidence = {"mode_used": before["refresh_mode"], "cost": before["refresh_cost"],
                "gold_before": before["gold"], "free_refreshes_before": before["free_refreshes"]}
    try:
        # 防：刷新点击任务失败。
        if not session.controller.post_click(*REFRESH).wait().succeeded:
            raise InputUncertain("商店刷新点击结果待核对")
        deadline = monotonic() + TIMEOUT
        while True:
            sleep(INTERVAL)
            image = session.preparation_frame()
            scene = session.preparation_scene(image)
            result = {**scene, "result": "uncertain", "needs_ai": True, "message": "商店刷新结果待确认"}
            if scene["screen"] not in {"备战", "未知"}:
                break
            if scene["screen"] == "备战":
                if scene["shop"] != "open":
                    break
                after = read_refresh(image, reader)
                result.update(after)
                if complete(after):
                    expected_gold = before["gold"] - before["refresh_cost"]
                    if after["gold"] == expected_gold:
                        consumed = (after["free_refreshes"] == before["free_refreshes"]-1 if free
                                    else after["gold"] < before["gold"])
                        if consumed:
                            result.update(read_cards(image, reader), result="success",
                                          needs_ai=False, message="已确认刷新")
                            break
                    # 防：金币变化与本次费用不符。
                    if after["gold"] != before["gold"] and after["gold"] != expected_gold:
                        result["message"] = "刷新后金币变化待核对"
                        break
            if monotonic() >= deadline:
                break
    except ERRORS as error:
        result = uncertain(error, "商店刷新出错，附最近画面")
    return save(session, image, {**result, "evidence": evidence}, "refresh", True)

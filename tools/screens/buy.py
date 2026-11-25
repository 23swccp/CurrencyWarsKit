"""普通商店单次购买与成交核对。"""

import re
from time import monotonic, sleep

import numpy as np
from maa.resource import Resource
from maa.tasker import Tasker
from PIL import Image

from game.input import ERRORS, InputUncertain, uncertain
from game.slots import BENCH, NUMBERS
from recognition_debug import focus
from screens.preparation import ASSETS
from screens.shop import CARD_CENTERS, EMPTY_SCORE, INTERVAL, TIMEOUT, read_cards, read_refresh


OFFER = "offer.png"
SOLD = "shop_empty.png"
TASK = "BuyCharacter"
SOLD_TASK = "SoldOut"
FACE = (-100, 90, 200, 180)
SOLD_AREA = (-40, 96, 80, 200)
PRICE = (84, 287, 31, 35)
CLICK_Y = 200
MATCH_SCORE = 0.9
POST_DELAY = 1000
WAIT = 5000


def region(center, box):
    """把相对槽位中心的横向偏移换算成截图内的区域。"""
    left, top, width, height = box
    return center + left, top, width, height


def gold(reader, image):
    value = reader.integer(image, "金币", NUMBERS["gold"])
    if value is not None:
        return value
    # 防：商店展开的实机截图中单个金币数字漏读。
    text = re.sub(r"\s+", "", reader.enlarged_text(image, "金币", NUMBERS["gold"]))
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def purchase(controller, image, slot, center):
    """以购前卡面为点击条件点击一次，并确认目标格售空。"""
    roi = list(region(center, FACE))
    x, y, width, height = roi
    focus(image, tuple(roi), f"{slot} 购买卡面")
    resource = Resource()
    resource.override_image(OFFER, image[y:y+height, x:x+width].copy())
    with Image.open(ASSETS / "shop" / "empty.png") as source:
        resource.override_image(SOLD, np.asarray(source.convert("RGB"))[:, :, ::-1].copy())
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("购买角色 Pipeline 初始化失败")
    pipeline = {
        TASK: {
            "recognition": {"type": "TemplateMatch", "param": {
                "template": [OFFER], "roi": roi, "threshold": [MATCH_SCORE], "method": 5}},
            "action": {"type": "Click", "param": {"target": [center, CLICK_Y, 1, 1]}},
            "post_delay": POST_DELAY, "timeout": WAIT, "next": [SOLD_TASK],
        },
        SOLD_TASK: {"recognition": {"type": "TemplateMatch", "param": {
            "template": [SOLD], "roi": list(region(center, SOLD_AREA)), "threshold": [EMPTY_SCORE], "method": 5}}},
    }
    job = tasker.post_task(TASK, pipeline).wait()
    detail = job.get()
    nodes = [(node.name, node.completed) for node in detail.nodes] if detail else None
    # 卡面锁定区的识别没有通过时 Pipeline 不会点击；节点带动作才说明点击已发出。
    clicked = detail is not None and any(node.name == TASK and node.action is not None for node in detail.nodes)
    return {"succeeded": job.succeeded, "clicked": clicked, "nodes": nodes}


def buy(session, slot):
    reader = session.preparation_reader()
    image = session.preparation_frame()
    scene = session.preparation_scene(image)
    # 防：非备战或商店收起时，商店卡片坐标不适用。
    if scene["screen"] != "备战" or scene["shop"] != "open":
        return {**session.save_image(image), **scene, "result": "failed", "needs_ai": True,
                "action": "buy", "slot": slot, "message": "购买角色要求商店展开"}
    center = CARD_CENTERS[int(slot.split("_")[1])-1]
    cards = read_cards(image, reader)
    evidence = {"name": cards["shop_slots"][slot], "price": reader.integer(image, "角色售价", region(center, PRICE)),
                "gold_before": gold(reader, image), "gold_after": None, "shop_slot_empty": None}

    def rejected(result, message):
        return {**session.save_image(image), **scene, **cards, "result": result, "needs_ai": True,
                "action": "buy", "slot": slot, "evidence": evidence, "message": message}

    # 防：售空格、非角色商品或名称漏读。
    if slot in cards["shop_empty_slots"] or slot in cards["shop_goods"] or not evidence["name"]:
        return rejected("failed", "目标槽未确认角色")
    # 防：实际售价或购前金币漏读；免费购买规则待确认。
    if evidence["price"] is None or evidence["price"] <= 0 or evidence["gold_before"] is None:
        return rejected("uncertain", "售价或金币待确认")
    # 防：购前金币少于实际售价。
    if evidence["gold_before"] < evidence["price"]:
        return rejected("failed", "金币不足")
    # 防：备战席没有空位时新角色无处落地，先不点击。
    if not any(reader.empty(image, bench) for bench in BENCH):
        return rejected("uncertain", "备战席没有确认的空位")
    result = {**scene, "result": "uncertain", "needs_ai": True, "message": "购买结果待确认"}
    try:
        task = purchase(session.controller, image, slot, center)
        evidence["pipeline"] = task
        # 防：截图与点击之间卡面变化，Pipeline 未发出点击。
        if not task["clicked"]:
            return {**session.save_image(image), **scene, **cards, "result": "failed", "needs_ai": True,
                    "action": "buy", "slot": slot, "evidence": evidence,
                    "message": "目标卡面与购前不一致，未发送购买点击"}
        deadline = monotonic() + TIMEOUT
        while True:
            image = session.preparation_frame()
            scene = session.preparation_scene(image)
            result = {**scene, "result": "uncertain", "needs_ai": True, "message": "购买结果待确认"}
            if scene["screen"] == "备战" and scene["shop"] == "open":
                after = gold(reader, image)
                sold = reader.score(image, SOLD, region(center, SOLD_AREA)) >= EMPTY_SCORE
                evidence.update(gold_after=after, shop_slot_empty=sold)
                if after == evidence["gold_before"] - evidence["price"] and sold:
                    result.update(read_refresh(image, reader), **read_cards(image, reader), result="success",
                                  gold=after, needs_ai=False, message="已确认购买角色")
                    break
            # 防：购买后出现其他界面，交回现场。
            elif scene["screen"] != "未知":
                break
            if monotonic() >= deadline:
                break
            sleep(INTERVAL)
    except ERRORS as error:
        result = uncertain(error, "购买角色待核对")
        try:
            image = session.preparation_frame()
        # 防：输入后再次截图失败，保留最近画面和错误。
        except ERRORS as capture_error:
            result["message"] += f"；补充截图失败：{type(capture_error).__name__}: {capture_error}"
    try:
        return {**session.save_image(image), **result, "action": "buy", "slot": slot, "evidence": evidence}
    except ERRORS as error:
        raise InputUncertain(f"购买后保存截图失败：{type(error).__name__}: {error}") from error

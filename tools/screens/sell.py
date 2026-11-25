"""备战席角色出售与结果确认。"""

from time import monotonic, sleep

from game.input import ERRORS, InputUncertain, uncertain
from game.slots import NUMBERS, SLOTS


TARGET = (1825, 920)
DRAG = 450
TIMEOUT = 3
INTERVAL = 0.3


def sell_many(session, sources):
    """按顺序出售多个角色；每个目标拖动前都重新读帧，补领或合成改变槽位时以现场为准。"""
    if not sources:
        return {"result": "failed", "needs_ai": True, "message": "未指定出售槽位"}
    if len(sources) == 1:
        return sell(session, sources[0])
    sold = []
    result = None
    for source in sources:
        result = sell(session, source)
        if result["result"] != "success":
            break
        evidence = result["evidence"]
        sold.append({"from": source, "name": evidence["name"], "price": evidence["price"]})
    complete = len(sold) == len(sources)
    total = sum(item["price"] for item in sold)
    return {**result, "action": "sell", "from": None, "sold": sold, "gold_gained": total,
            "slots": {item["from"]: None for item in sold},
            "result": "success" if complete else result["result"],
            "needs_ai": not complete,
            "message": f"已出售 {len(sold)}/{len(sources)} 个角色，共得 {total} 金币" if complete
                       else f"{result['message']}；本次已售出 {len(sold)}/{len(sources)} 个角色"}


def sell(session, source):
    reader = session.preparation_reader()
    image = session.preparation_frame()
    result = session.preparation_scene(image)
    if result["screen"] != "备战" or result["shop"] != "closed":
        return {**session.save_image(image), **result, "result": "failed", "needs_ai": True,
                "message": "售卖要求商店收起的备战画面"}
    card = reader.card(image, source)
    # 防：来源为空或角色未确认。
    if card is None or "name" not in card:
        return {**session.save_image(image), "result": "failed", "screen": "备战", "needs_ai": True,
                "message": "来源角色待确认", "slots": {source: card}}
    gold = reader.integer(image, "gold", NUMBERS["gold"])
    # 防：售前金币漏读。
    if gold is None:
        return {**session.save_image(image), "result": "uncertain", "screen": "备战", "needs_ai": True,
                "message": "售前金币待确认"}
    try:
        # 防：MaaWin32 的 touch_down/move 实机不触发游戏拖拽（1-6 实测），改用一次 swipe 送进出售区。
        if not session.controller.post_swipe(*SLOTS[source], *TARGET, DRAG).wait().succeeded:
            raise InputUncertain("售卖拖动结果待核对")
        deadline = monotonic() + TIMEOUT
        while True:
            sleep(INTERVAL)
            image = session.preparation_frame()
            scene = session.preparation_scene(image)
            result = {**scene, "result": "uncertain", "needs_ai": True, "message": "售卖结果待确认"}
            if scene["screen"] != "备战" or scene["shop"] != "closed":
                break
            empty = reader.empty(image, source)
            after = reader.integer(image, "gold", NUMBERS["gold"])
            # 售价在拖动中显示，swipe 一次完成后只能按金币增量回推。
            gained = None if after is None else after - gold
            result.update(gold=after, evidence={"name": card["name"], "price": gained,
                                                "gold_before": gold, "gold_after": after, "source_empty": empty})
            if empty and gained is not None and gained > 0:
                result.update(result="success", needs_ai=False, slots={source: None}, message="已确认出售")
                break
            if monotonic() >= deadline:
                break
    except ERRORS as error:
        result = uncertain(error, "售卖出错，附最近画面")
    try:
        return {**session.save_image(image), **result, "action": "sell", "from": source}
    except ERRORS as error:
        raise InputUncertain(f"售卖后保存截图出错：{type(error).__name__}: {error}") from error

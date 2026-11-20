"""备战席角色出售与结果确认。"""

from time import monotonic, sleep

from game.input import ERRORS, InputUncertain, uncertain
from game.slots import NUMBERS, SLOTS


STAGING = (1500, 780)
TARGET = (1825, 920)
LABEL = (40, 825, 120, 70)
PRICE = (102, 910, 45, 52)
TIMEOUT = 3
INTERVAL = 0.3


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
    held = False
    sent = False
    price = None

    def send(job, step):
        # 防：鼠标任务失败。
        if not job.wait().succeeded:
            raise InputUncertain(f"售卖{step}结果待核对")

    try:
        try:
            held = True
            send(session.controller.post_touch_down(*SLOTS[source]), "按下")
            send(session.controller.post_touch_move(*STAGING), "拖动")
            sleep(INTERVAL)
            image = session.preparation_frame()
            label = reader.score(image, "sell.png", LABEL)
            if label >= 0.9:
                price = reader.integer(image, "sell_price", PRICE)
            # 防：出售面板或售价未确认。
            if price is None:
                send(session.controller.post_touch_move(*SLOTS[source]), "拖回来源")
            else:
                send(session.controller.post_touch_move(*TARGET), "移入出售区")
                sent = True
        finally:
            if held:
                send(session.controller.post_touch_up(), "释放")
        if not sent:
            image = session.preparation_frame()
            result = {"result": "uncertain", "screen": "备战", "needs_ai": True,
                      "message": "出售面板或售价待确认，已结束拖动"}
        else:
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
                result.update(gold=after, evidence={"name": card["name"], "price": price,
                              "gold_before": gold, "gold_after": after, "source_empty": empty})
                if empty and after == gold + price:
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

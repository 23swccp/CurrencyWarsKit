"""按批购买备战经验，逐次确认金币和经验变化。"""

from time import monotonic, sleep

from maa.resource import Resource
from maa.tasker import Tasker

from game.input import ERRORS
from game.slots import NUMBERS
from ocr import MODEL
from recognition_debug import focus


COST = 4
XP = 4
LABEL = (230, 835, 136, 48)
TASK = "BuyExperience"
TIMEOUT = 3
INTERVAL = 0.3

PIPELINE = {
    TASK: {
        "recognition": {"type": "OCR", "param": {
            "expected": ["购买经验"], "roi": list(LABEL)}},
        "action": {"type": "Click", "param": {"target": [297, 860, 1, 1]}},
        "post_delay": 300,
        "timeout": 1000,
    },
}


def purchase_tasker(controller):
    resource = Resource()
    if not resource.use_cpu() or not resource.post_ocr_model(MODEL).wait().succeeded:
        raise RuntimeError(f"购买经验 OCR 模型加载失败：{MODEL}")
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("购买经验 Pipeline 初始化失败")
    return tasker


def closed(scene):
    return scene["screen"] == "备战" and scene["shop"] == "closed"


def read(reader, image):
    numbers = reader.numbers(image)
    return {key: numbers[key] for key in NUMBERS}


def received(before, after):
    old, new = before["experience"], after["experience"]
    # 防：扣款、等级或经验区域在升级动画中漏读。
    if None in (after["gold"], after["level"], new["current"], new["required"]):
        return False
    if before["gold"] - after["gold"] != COST:
        return False
    if after["level"] == before["level"]:
        return (new["required"] == old["required"] and new["current"] < old["required"]
                and new["current"] - old["current"] == XP)
    if after["level"] == before["level"] + 1:
        # 同时核对升一级和结转量，升级条跳动时继续等实际数值。
        return old["required"] - old["current"] + new["current"] == XP
    return False


def buy(session, xp):
    reader = session.preparation_reader()
    image = session.preparation_frame()
    scene = session.preparation_scene(image)
    numbers = {}
    confirmed = 0
    pending = False

    def finish(result, message):
        data = {**scene, **numbers, "result": result, "action": "buy-experience",
                "xp_requested": xp, "xp_bought": confirmed * XP, "gold_spent": confirmed * COST,
                "purchases_confirmed": confirmed, "purchase_pending": pending,
                "needs_ai": result != "success", "message": message}
        try:
            return {**session.save_image(image), **data}
        except ERRORS as error:
            # 防：购买后保存截图失败，仍返回已确认进度。
            if not (confirmed or pending):
                raise
            return {**data, "result": "uncertain", "needs_ai": True,
                    "message": f"{message}。保存截图失败：{type(error).__name__}: {error}"}

    if not closed(scene):
        return finish("failed", "购买经验要求商店收起的备战画面")

    numbers = read(reader, image)
    count = xp // XP
    tasker = None
    while confirmed < count:
        exp = numbers["experience"]
        # 防：购前金币、等级、经验或费用漏读。
        if None in (numbers["gold"], numbers["level"], exp["current"], exp["required"],
                    numbers["experience_cost"]):
            return finish("uncertain", "购买前的金币、等级、经验或费用待确认")
        if numbers["experience_cost"] != COST:
            return finish("uncertain", "当前经验费用与每次 4 金币的购买规则不符")
        if numbers["gold"] < (count - confirmed) * COST:
            return finish("failed", "金币不足以完成剩余购买")
        if tasker is None:
            tasker = purchase_tasker(session.controller)

        before = numbers
        focus(image, LABEL, "购买经验按钮")
        pending = True
        try:
            job = tasker.post_task(TASK, PIPELINE).wait()
            if not job.succeeded:
                detail = job.get()
                nodes = detail.nodes if detail is not None else []
                pending = detail is None or any(node.action is not None for node in nodes)
                image = session.preparation_frame()
                numbers = {}
                scene = session.preparation_scene(image)
                if closed(scene):
                    numbers = read(reader, image)
                return finish("uncertain", f"购买经验任务 {TASK} 未完成，节点 "
                              f"{[(node.name, node.completed) for node in nodes]}")

            deadline = monotonic() + TIMEOUT
            while True:
                image = session.preparation_frame()
                numbers = {}
                scene = session.preparation_scene(image)
                if closed(scene):
                    numbers = read(reader, image)
                    if received(before, numbers):
                        confirmed += 1
                        pending = False
                        break
                # 防：购买后跳到其他已识别界面，停止该批次。
                elif scene["screen"] != "未知":
                    return finish("uncertain", "购买经验后离开备战画面，结果待确认")
                if monotonic() >= deadline:
                    return finish("uncertain", "本次扣款或经验变化待确认")
                sleep(INTERVAL)
        except ERRORS as error:
            # 防：购买任务或随后的识别出错，交回已确认批次进度。
            return finish("uncertain", f"购买经验结果待核对：{type(error).__name__}: {error}")

    return finish("success", "已确认请求的经验全部到账")

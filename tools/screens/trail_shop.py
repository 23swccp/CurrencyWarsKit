"""开拓商店的识别、进出与单次购买。"""

import re
from time import monotonic, sleep

import numpy as np
from PIL import Image
from maa.resource import Resource
from maa.tasker import Tasker

from game.input import ERRORS, InputUncertain
from screens.preparation import ASSETS


SCREEN = "开拓商店"
TITLE = (958, 48, 190, 63)
ENTRY = (475, 267, 129, 44)
BALANCE = (590, 91, 62, 38)
LEFTS = (436, 686, 936, 1185)
TITLE_SCORE = 0.88
ENTRY_SCORE = 0.88
DETAILS_SCORE = 0.86
TIMEOUT = 5
INTERVAL = 0.3


def page(reader, image):
    return reader.score(image, "trail_title.png", TITLE) >= TITLE_SCORE


def balance(reader, image):
    return reader.integer(image, "开拓力", BALANCE)


def name_area(x):
    return (x+7, 356, 137, 42)


def price_area(x):
    return (x+176, 359, 47, 42)


def offer(reader, image, slot, power):
    x = LEFTS[slot-1]
    reader.load_ocr()
    name = reader.enlarged_text(image, f"开拓商品{slot}名称", name_area(x)).strip()
    name = re.sub(r"(?<=[\u4e00-\u9fff])II$", "Ⅱ", name)
    price = reader.integer(image, f"开拓商品{slot}价格", price_area(x))
    return {"slot": slot, "name": name or None, "price": price,
            "affordable": power >= price if power is not None and price is not None else None}


def catalog(reader, image, power, checked=None):
    offers = [checked if checked is not None and checked["slot"] == slot else offer(reader, image, slot, power)
              for slot in range(1, len(LEFTS)+1)]
    complete = power is not None and all(item["name"] and item["price"] is not None for item in offers)
    return {"result": "success" if complete else "uncertain", "screen": SCREEN, "mode": "paid",
            "trailblaze_power": power, "offers": offers, "needs_ai": True}


def read(reader, image):
    power = balance(reader, image)
    details = sum(reader.score(image, "trail_details.png", (x+22, 417, 178, 51)) >= DETAILS_SCORE for x in LEFTS)
    if details >= 3:
        return catalog(reader, image, power)

    # 免费启动前只有中央启动项。
    name = reader.text(image, "开拓商店启动项", (937, 352, 156, 57)).strip()
    launch = ("星穹列车" in name and reader.integer(image, "启动项价格", (1121, 360, 35, 42)) == 0)
    result = {"screen": SCREEN, "mode": "launch" if launch else "unknown",
              "result": "success" if launch and power is not None else "uncertain",
              "trailblaze_power": power, "offers": [], "needs_ai": True}
    if launch:
        result["launch"] = {"name": name, "price": 0}
    return result


def template(name, roi, score):
    return {"type": "TemplateMatch", "param": {
        "template": [name], "roi": list(roi), "threshold": [score], "method": 5}}


class TrailShop:
    def __init__(self, session):
        self.session = session
        self.reader = session.preparation_reader()
        self.image = session.preparation_frame()
        self.clicked = False

    def capture(self):
        self.image = self.session.preparation_frame()

    def current(self):
        scene = self.session.preparation_scene(self.image)
        return read(self.reader, self.image) if scene["screen"] == SCREEN else scene

    def click(self, task, source, point, *, arrive=None, item_x=None):
        resource = Resource()
        for name in ("title", "entry"):
            with Image.open(ASSETS / "trail_shop" / f"{name}.png") as image:
                resource.override_image(f"trail_{name}.png", np.asarray(image.convert("RGB"))[:, :, ::-1].copy())
        pipeline = {}
        if item_x is not None:
            pipeline["TrailShopTitle"] = {"recognition": source}
            checks = (("TrailShopOfferName", name_area(item_x)), ("TrailShopOfferPrice", price_area(item_x)),
                      ("TrailShopPower", BALANCE))
            for node_name, area in checks:
                name = node_name + ".png"
                x, y, w, h = area
                resource.override_image(name, self.image[y:y+h, x:x+w].copy())
                pipeline[node_name] = {"recognition": template(name, area, 0.90)}
            source = {"type": "And", "param": {"all_of": list(pipeline), "box_index": 0}}
        tasker = Tasker()
        if not tasker.bind(resource, self.session.controller) or not tasker.inited:
            raise RuntimeError("开拓商店 Pipeline 初始化失败")

        node = {"recognition": source, "action": {"type": "Click", "param": {"target": [*point, 1, 1]}},
                "post_delay": 300, "timeout": 1000}
        pipeline[task] = node
        if arrive is not None:
            node.update(next=["TrailShopArrived"], timeout=5000)
            pipeline["TrailShopArrived"] = {"recognition": arrive}

        self.clicked = True
        job = tasker.post_task(task, pipeline).wait()
        # 防：Pipeline 未完成时保留失败节点和当前画面。
        if not job.succeeded:
            detail = job.get()
            nodes = [] if detail is None else [(node.name, node.completed) for node in detail.nodes]
            self.capture()
            raise InputUncertain(f"开拓商店任务 {task} 未完成，节点 {nodes}")

    def wait(self, check):
        deadline = monotonic() + TIMEOUT
        while True:
            self.capture()
            result = check()
            if result is not None:
                return result
            if monotonic() >= deadline:
                return None
            sleep(INTERVAL)

    def open(self):
        scene = self.session.preparation_scene(self.image)
        if scene["screen"] == SCREEN:
            return read(self.reader, self.image)
        # 防：普通商店、选择页或无开拓入口的备战页。
        if (scene["screen"] != "备战" or scene["shop"] != "closed"
                or self.reader.score(self.image, "trail_entry.png", ENTRY) < ENTRY_SCORE):
            return {**scene, "result": "failed", "needs_ai": True, "message": "未确认开拓商店入口"}
        self.click("TrailShopOpen", template("trail_entry.png", ENTRY, ENTRY_SCORE), (536, 291),
                   arrive=template("trail_title.png", TITLE, TITLE_SCORE))

        def ready():
            if page(self.reader, self.image):
                result = read(self.reader, self.image)
                if result["mode"] != "unknown":
                    return result
            return None

        return self.wait(ready) or {**self.current(), "result": "uncertain", "message": "开拓商店内容待确认"}

    def close(self):
        scene = self.session.preparation_scene(self.image)
        if scene["screen"] == "备战" and scene["shop"] == "closed":
            return scene
        if scene["screen"] != SCREEN:
            return {**scene, "result": "failed", "needs_ai": True, "message": "当前未打开开拓商店"}
        self.click("TrailShopClose", template("trail_title.png", TITLE, TITLE_SCORE), (12, 12))

        def back():
            scene = self.session.preparation_scene(self.image)
            return scene if scene["screen"] == "备战" and scene["shop"] == "closed" else None

        return self.wait(back) or {**self.current(), "result": "uncertain", "message": "关闭开拓商店结果待确认"}

    def buy(self, slot):
        before = self.current()
        # 防：付费货架未展开、余额漏读或目标格未识别。
        if before["screen"] != SCREEN or before["mode"] != "paid":
            return {**before, "result": "failed", "needs_ai": True, "message": "购买要求已打开的开拓商店付费货架"}
        old = before["offers"][slot-1]
        power = before["trailblaze_power"]
        if not old["name"] or old["price"] is None or power is None:
            return {**before, "result": "uncertain", "message": "商品名称、价格或开拓力待确认"}
        if not old["affordable"]:
            return {**before, "result": "failed", "message": "开拓力不足"}

        x = LEFTS[slot-1]
        self.click("TrailShopBuy", template("trail_title.png", TITLE, TITLE_SCORE), (x+112, 263),
                   item_x=x)
        evidence = {"slot": slot, "name": old["name"], "price": old["price"], "power_before": power}

        def received():
            if not page(self.reader, self.image):
                return None
            after = balance(self.reader, self.image)
            new = offer(self.reader, self.image, slot, after)
            if after == power - old["price"] and new["name"] and new["name"] != old["name"]:
                result = catalog(self.reader, self.image, after, checked=new)
                return {**result, "result": "success", "purchase": {**evidence, "power_after": after},
                        "message": "已确认扣款和原格换货"}
            return None

        return self.wait(received) or {**self.current(), "result": "uncertain",
                                       "purchase": evidence, "message": "扣款和原格换货待确认"}

    def run(self, action, slot=None):
        try:
            result = self.buy(slot) if action == "buy" else (self.open() if action == "open" else self.close())
        except ERRORS as error:
            if not self.clicked:
                raise
            result = {"result": "uncertain", "needs_ai": True,
                      "message": f"开拓商店操作待核对：{type(error).__name__}: {error}"}
        try:
            return {**self.session.save_image(self.image), **result, "action": "trail-shop-" + action}
        except ERRORS as error:
            # 防：输入已经发出，截图写入失败时把已确认结果一起返回。
            if not self.clicked:
                raise
            return {**result, "result": "uncertain", "needs_ai": True, "action": "trail-shop-" + action,
                    "message": f"保存截图失败：{type(error).__name__}: {error}"}

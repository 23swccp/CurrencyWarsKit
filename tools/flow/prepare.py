"""备战入场、奖励收集与观察编排。"""

from time import sleep

from game.input import ERRORS, InputUncertain, uncertain
from screens.preparation import layout
from screens.protagonist import ProtagonistOperation, complete as protagonist_complete
from flow.rewards import sweep
from game.slots import BLANK, SHOP_TOGGLE
from screens.strategy import complete
from screens.supply import complete as supply_complete


ATTEMPTS = 3
INTERVAL = 2
BLANK_CLICKS = 3


class PreparationFlow:
    def __init__(self, session):
        self.session = session
        self.reader = session.preparation_reader()
        self.strategy = session.strategy_reader()
        self.protagonist = session.protagonist_reader()
        self.supply = session.supply_reader()
        self.collected = False
        self.image = None
        self.clicked = False
        self.blank_clicks = 0

    def capture(self):
        job = self.session.controller.post_screencap().wait()
        if not job.succeeded:
            raise RuntimeError("备战入场截图失败")
        return job.get()

    def inspect(self, image):
        stage = self.protagonist.stage(image)
        if stage == "entry":
            return {"result": "success", "screen": "选择主角", "stage": stage, "needs_ai": True,
                    **self.reader.action_button(image), "node": self.reader.header(image)["node"]}
        if stage == "selection":
            result = self.protagonist.read(image)
            return {**result, "result": "success" if protagonist_complete(result) else "uncertain"}
        if self.supply.page(image):
            result = self.supply.read(image)
            return {**result, "result": "success" if supply_complete(result) else "uncertain"}
        result = layout(image, self.reader)
        if result is not None:
            return result
        if self.strategy.page(image):
            result = self.strategy.read(image)
            return {**result, "result": "success" if complete(result) else "uncertain"}
        return {"result": "uncertain", "screen": "未知"}

    def dismiss(self):
        if self.blank_clicks == BLANK_CLICKS:
            return
        self.blank_clicks += 1
        self.clicked = True
        if not self.session.controller.post_click(*BLANK).wait().succeeded:
            raise InputUncertain("右下空白点击结果待核对")

    def wait(self, delay):
        for elapsed in range(INTERVAL, delay, INTERVAL):
            sleep(INTERVAL)
            self.image = self.capture()
            if (self.protagonist.stage(self.image) is not None or self.supply.page(self.image)
                    or layout(self.image, self.reader) is not None or self.strategy.page(self.image)):
                sleep(delay - elapsed)
                return
            self.dismiss()
        sleep(INTERVAL)

    def confirm(self, image=None, closed=False):
        for attempt in range(ATTEMPTS):
            self.image = self.capture() if image is None else image
            result = self.inspect(self.image)
            if result["result"] == "success":
                if result["screen"] != "备战" or not closed or result["shop"] == "closed":
                    return result
            if attempt + 1 < ATTEMPTS:
                if result["screen"] == "未知":
                    self.dismiss()
                sleep(INTERVAL)
                image = None
        message = "商店收起仍待确认" if closed else "备战或选择界面仍待确认"
        return {**result, "result": "uncertain", "needs_ai": True,
                "message": f"{message}，已确认 {ATTEMPTS} 次"}

    def close_shop(self, result):
        if result["result"] != "success" or result["screen"] != "备战" or result["shop"] == "closed":
            return result
        self.clicked = True
        if not self.session.controller.post_click(*SHOP_TOGGLE).wait().succeeded:
            raise InputUncertain("收起商店点击结果待核对")
        sleep(INTERVAL)
        return self.confirm(closed=True)

    def open_protagonist(self, result):
        if result["screen"] == "选择主角" and result.get("stage") == "entry":
            operation = ProtagonistOperation(self.session, self.protagonist)
            result = operation.execute("open", image=self.image, observation=result)
            self.image = operation.image
            self.clicked = self.clicked or operation.clicked
        return result

    def enter(self, image=None, delay=0):
        self.collected = False
        return self.resume(image, delay)

    def resume(self, image=None, delay=0):
        self.image = image
        self.clicked = False
        self.blank_clicks = 0
        try:
            if delay:
                self.wait(delay)
                image = None
            result = self.close_shop(self.open_protagonist(self.confirm(image)))
            if result["result"] == "success" and result["screen"] == "备战" and not self.collected:
                self.clicked = True
                sweep(self.session.controller)
                self.collected = True
                result = self.open_protagonist(self.confirm(closed=True))
            if result["result"] == "success" and result["screen"] == "备战":
                result = {**result, **self.reader.observe(self.image), "needs_ai": True}
        except ERRORS as error:
            if self.image is None:
                raise
            result = uncertain(error, "备战入场出错，附最近观察截图")
        try:
            return {**self.session.save_image(self.image), **result, "action": "prepare"}
        except ERRORS as error:
            if self.clicked:
                raise InputUncertain(f"备战输入后保存截图出错：{type(error).__name__}: {error}") from error
            raise

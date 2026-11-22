"""备战入场、奖励收集与观察编排。"""

from time import sleep

from flow.preparation_scene import identify, read_choice
from flow.rewards import sweep
from game.input import ERRORS, InputUncertain, uncertain
from game.slots import BLANK, SHOP_TOGGLE
from screens.protagonist import ProtagonistOperation
from screens.tea_break import SCREEN as TEA_BREAK, TeaBreak


ATTEMPTS = 3
INTERVAL = 2
BLANK_CLICKS = 3


class PreparationFlow:
    def __init__(self, session):
        self.session = session
        self.reader = session.preparation_reader()
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
        scene = identify(self.session, image)
        if scene is None:
            return {"result": "uncertain", "screen": "未知"}
        return read_choice(self.session, image, scene)

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
            if identify(self.session, self.image) is not None:
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
            operation = ProtagonistOperation(self.session, self.session.protagonist_reader())
            result = operation.execute("open", image=self.image, observation=result)
            self.image = operation.image
            self.clicked = self.clicked or operation.clicked
        return result

    def advance_tea_break(self, result):
        if result["screen"] != TEA_BREAK or result["stage"] not in {"entry", "dialogue", "reward", "settlement"}:
            return result, {}
        operation = TeaBreak(self.session, self.image)
        result = operation.execute("advance", current=result["stage"])
        self.image = operation.image
        self.clicked = self.clicked or operation.clicked
        if result["stage"] != "complete":
            return result, {}
        self.collected = False
        return self.confirm(self.image), {"tea_break": result["tea_break"]}

    def enter(self, image=None, delay=0):
        self.collected = False
        return self.resume(image, delay)

    def resume(self, image=None, delay=0):
        self.image = image
        self.clicked = False
        self.blank_clicks = 0
        tea_break = {}
        try:
            if delay:
                self.wait(delay)
                image = None
            result, tea_break = self.advance_tea_break(self.confirm(image))
            result = self.close_shop(self.open_protagonist(result))
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
            return {**self.session.save_image(self.image), **result, **tea_break, "action": "prepare"}
        except ERRORS as error:
            if self.clicked:
                raise InputUncertain(f"备战输入后保存截图出错：{type(error).__name__}: {error}") from error
            raise

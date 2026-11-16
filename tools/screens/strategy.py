"""投资策略的观察、单项刷新与选择确认。"""

import re
from time import monotonic, sleep

import numpy as np
from PIL import Image
from maa.pipeline import JOCR, JRecognitionType

from game.input import ERRORS, InputUncertain, uncertain
from ocr import ImageController, MODEL
from recognition_debug import focus
from screens.preparation import ASSETS, Recognition, layout as preparation_layout


TITLE = (845, 76, 235, 48)
LEFTS = (250, 750, 1250)
OPTIONS = ((460, 490), (960, 490), (1460, 490))
REFRESH = ((390, 855), (890, 855), (1390, 855))
CONFIRM = (960, 984)
TIMEOUT = 8
INTERVAL = 0.3


class Strategy(Recognition):
    def __init__(self, preparation):
        self.preparation = preparation
        self.controller = ImageController(np.zeros((1, 1, 3), dtype="uint8"))
        if not self.controller.post_connection().wait().succeeded:
            raise RuntimeError("投资策略图片控制器连接失败")
        super().__init__(self.controller)
        self.ocr_ready = False
        for name in ("title", "selected"):
            with Image.open(ASSETS / "strategy" / f"{name}.png") as image:
                self.resource.override_image(f"{name}.png", np.asarray(image.convert("RGB"))[:, :, ::-1].copy())

    def page(self, image):
        if image.shape[:2] != (1080, 1920):
            raise ValueError("投资策略识别要求 1920×1080 截图")
        return self.score(image, "title.png", TITLE) >= 0.9

    def text(self, image, roi):
        focus(image, roi, "投资策略 OCR", "ocr")
        detail = self.tasker.post_recognition(JRecognitionType.OCR, JOCR(roi=roi), image).wait().get()
        if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
            raise RuntimeError(f"投资策略 OCR 详情缺失：{roi}")
        lines = sorted(detail.nodes[0].recognition.filtered_results,
                       key=lambda item: (item.box[1], item.box[0]))
        return "".join(item.text for item in lines)

    def selected(self, image):
        for index, x in enumerate(LEFTS, 1):
            if self.score(image, "selected.png", (x - 10, 180, 70, 75)) >= 0.9:
                return index
        return None

    def read(self, image):
        if not self.ocr_ready:
            if not self.resource.use_cpu() or not self.resource.post_ocr_model(MODEL).wait().succeeded:
                raise RuntimeError(f"投资策略 OCR 模型加载失败：{MODEL}")
            self.ocr_ready = True
        options = []
        for index, x in enumerate(LEFTS, 1):
            name = self.text(image, (x + 30, 468, 360, 45))
            text = self.text(image, (x + 30, 518, 380, 260))
            count = self.text(image, (x + 165, 830, 175, 55))
            match = re.fullmatch(r"刷新次数(\d+)", re.sub(r"\s+", "", count))
            option = {"slot": f"option_{index}", "name": name, "text": text,
                      "refreshes_left": int(match[1]) if match else None}
            if not name or not text or match is None:
                option["unknown"] = True
            options.append(option)
        return {"screen": "投资策略", "title": "请选择投资策略", "options": options,
                "selected": self.selected(image), "needs_ai": True}

    def following(self, image):
        result = preparation_layout(image, self.preparation)
        return {**result, "needs_ai": True} if result is not None else None


def complete(observation):
    return all("unknown" not in option for option in observation["options"])


class StrategyOperation:
    def __init__(self, session, reader):
        self.session = session
        self.reader = reader
        self.image = None
        self.clicked = False
        self.observation = {"screen": "未知", "needs_ai": True}

    def capture(self):
        job = self.session.controller.post_screencap().wait()
        if not job.succeeded:
            raise RuntimeError("投资策略截图失败")
        return job.get()

    def click(self, point):
        self.clicked = True
        if not self.session.controller.post_click(*point).wait().succeeded:
            raise InputUncertain(f"投资策略点击结果待核对：{point}")

    def wait(self, inspect, message, image=None):
        deadline = monotonic() + TIMEOUT
        while True:
            self.image = self.capture() if image is None else image
            self.observation = {"screen": "未知", "needs_ai": True}
            result = inspect(self.image)
            if result is not None:
                return result
            if monotonic() >= deadline:
                return {**self.observation, "result": "uncertain", "message": message}
            sleep(INTERVAL)
            image = None

    def observe(self, image=None, preparation=False):
        def inspect(frame):
            if self.reader.page(frame):
                self.observation = self.reader.read(frame)
                if complete(self.observation):
                    return {**self.observation, "result": "success"}
            elif preparation and frame is not image:
                # 调用方给的那帧已判过不是备战页，只检查新截图。
                supply = self.session.supply_reader()
                if supply.page(frame):
                    from screens.supply import SupplyOperation
                    operation = SupplyOperation(self.session, supply)
                    result = operation.execute(image=frame)
                    self.image = operation.image
                    return result
                protagonist = self.session.protagonist_reader()
                if protagonist.stage(frame) in {"entry", "selection"}:
                    from screens.protagonist import ProtagonistOperation
                    return ProtagonistOperation(self.session, protagonist).execute(image=frame)
                following = self.reader.following(frame)
                if following is not None:
                    if following["shop"] == "closed":
                        return {**following, **self.session.preparation.observe(frame)}
                    return following
            return None
        message = "备战或投资策略界面等待超时" if preparation else "投资策略选项读取超时"
        return self.wait(inspect, message, image)

    def refresh(self, option, before):
        count = before["options"][option - 1]["refreshes_left"]
        if count == 0:
            return {**before, "result": "failed", "message": f"第 {option} 项刷新次数为零"}
        self.click(REFRESH[option - 1])

        def inspect(frame):
            if self.reader.page(frame):
                self.observation = self.reader.read(frame)
                if (complete(self.observation)
                        and self.observation["options"][option - 1]["refreshes_left"] == count - 1):
                    return {**self.observation, "result": "success"}
            return None
        return self.wait(inspect, f"第 {option} 项刷新结果待核对")

    def choose(self, option):
        self.click(OPTIONS[option - 1])

        def selected(frame):
            if self.reader.page(frame):
                current = self.reader.selected(frame)
                self.observation = {"screen": "投资策略", "selected": current, "needs_ai": True}
                if current == option:
                    return {"result": "success"}
            return None

        selection = self.wait(selected, f"第 {option} 项选中结果待核对")
        if selection["result"] != "success":
            return selection
        self.click(CONFIRM)
        left = False

        def following(frame):
            nonlocal left
            if self.reader.page(frame):
                self.observation = {"screen": "投资策略", "needs_ai": True}
                if left:
                    self.observation = self.reader.read(frame)
                    if complete(self.observation):
                        return {**self.observation, "result": "uncertain",
                                "message": "确认后出现投资策略选项，请核对选择结果"}
            else:
                left = True
                return self.reader.following(frame)
            return None

        return self.wait(following, "投资策略确认后的界面待核对")

    def execute(self, action="read", option=None, image=None):
        try:
            result = self.observe(image, preparation=action == "preparation")
            if result["result"] == "success" and action in {"refresh", "choose"}:
                result = self.refresh(option, result) if action == "refresh" else self.choose(option)
        except ERRORS as error:
            if not self.clicked:
                raise
            result = uncertain(error, "投资策略操作后出错，附最近观察截图")
        if action in {"refresh", "choose"}:
            result = {**result, "action": "strategy-refresh" if action == "refresh" else "strategy",
                      "option": option}
        return result

    def save(self, result):
        try:
            return {**self.session.save_image(self.image), **result}
        except ERRORS as error:
            if self.clicked:
                raise InputUncertain(f"投资策略操作后保存截图出错：{type(error).__name__}: {error}") from error
            raise

    def run(self, action="read", option=None, image=None):
        return self.save(self.execute(action, option, image))

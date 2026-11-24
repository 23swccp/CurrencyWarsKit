"""主角与光环的识别、刷新和选择。"""

import re
from time import sleep

import numpy as np
from PIL import Image
from maa.pipeline import JOCR, JRecognitionType, JTemplateMatch

from game.input import ERRORS, InputUncertain, uncertain
from ocr import ImageController, MODEL
from recognition_debug import focus
from screens.preparation import ASSETS, Recognition, page as preparation_page
from game.slots import BLANK, PORTRAIT


TITLE = (780, 55, 370, 65)
TARGET = (960, 445)
CONFIRM = (960, 954)
INTERVAL = 2
ATTEMPTS = 3
CHARACTERS = {
    **{f"front_{i + 1}": (765 + 104 * i, 567, 90, 95) for i in range(4)},
    **{f"back_{i + 1}": (670 + 102 * i, 709, 94, 95) for i in range(6)},
}
CHARACTERS["back_3"] = (876, 709, 96, 93)
SIDES = ((195, 180, 435, 665), (1290, 180, 435, 665))


def portrait(image, rect):
    focus(image, rect, "主角头像裁剪", "crop")
    x, y, width, height = rect
    card = Image.fromarray(image[y:y + height, x:x + width]).resize((120, 130), Image.Resampling.BICUBIC)
    return np.asarray(card.crop(PORTRAIT)).copy()


def complete(result):
    return (result["gold"] is not None and result["refreshes_left"] is not None
            and all("unknown" not in option for option in result["options"]))


def observation(reader, image, stage):
    if stage == "selection":
        result = reader.read(image)
        return {**result, "result": "success" if complete(result) else "uncertain"}
    if stage == "entry":
        return {"result": "success", "screen": "选择主角", "stage": "entry", "needs_ai": True,
                **reader.preparation.action_button(image), "node": reader.preparation.header(image)["node"]}
    return {"result": "uncertain", "screen": "未知", "needs_ai": True}


class Protagonist(Recognition):
    def __init__(self, preparation):
        self.preparation = preparation
        self.controller = ImageController(np.zeros((1, 1, 3), dtype="uint8"))
        if not self.controller.post_connection().wait().succeeded:
            raise RuntimeError("主角图片控制器连接失败")
        super().__init__(self.controller)
        self.ocr_ready = False
        for path in (ASSETS / "protagonist").glob("*.png"):
            with Image.open(path) as image:
                self.resource.override_image(f"protagonist_{path.name}", np.asarray(image.convert("RGB"))[:, :, ::-1].copy())

    def find(self, image, name, roi):
        focus(image, roi, name)
        detail = self.tasker.post_recognition(JRecognitionType.TemplateMatch,
            JTemplateMatch(template=[f"protagonist_{name}.png"], roi=roi, threshold=[0.0], method=5), image).wait().get()
        if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
            raise RuntimeError(f"主角模板识别出错：{name}")
        best = detail.nodes[0].recognition.best_result
        return best if best is not None and best.score >= 0.9 else None

    def selection_page(self, image):
        if image.shape[:2] != (1080, 1920):
            raise ValueError("主角识别要求 1920×1080 截图")
        return self.find(image, "title", TITLE) is not None

    def stage(self, image):
        if self.selection_page(image):
            return "selection"
        if (preparation_page(image, self.preparation)["result"] == "success"
                and self.preparation.action_button(image)["next_action"] == "protagonist-open"):
            return "entry"
        return None

    def lines(self, image, roi):
        if not self.ocr_ready:
            if not self.resource.use_cpu() or not self.resource.post_ocr_model(MODEL).wait().succeeded:
                raise RuntimeError(f"主角 OCR 模型加载失败：{MODEL}")
            self.ocr_ready = True
        focus(image, roi, "主角 OCR", "ocr")
        detail = self.tasker.post_recognition(JRecognitionType.OCR, JOCR(roi=roi), image).wait().get()
        if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
            raise RuntimeError(f"主角 OCR 详情缺失：{roi}")
        return [item.text for item in sorted(detail.nodes[0].recognition.filtered_results,
                                             key=lambda item: (item.box[1], item.box[0]))]

    def characters(self, image):
        result = {}
        for slot, rect in CHARACTERS.items():
            x, y, width, height = rect
            template = "empty_front" if slot.startswith("front") else "empty_back_2"
            if slot in {"back_4", "back_5", "back_6"}:
                template = f"empty_{slot}"
            empty = self.find(image, template, (x, y, width, height))
            result[slot] = None if empty is not None else self.preparation.match(portrait(image, rect))
        return result

    def character(self, image):
        if self.find(image, "empty", (905, 380, 115, 125)) is not None:
            return None
        return self.preparation.match(portrait(image, (909, 385, 107, 112)))

    def enabled(self, image):
        return float(image[943:966, 780:800].mean()) >= 180

    def read(self, image):
        options = []
        for index, roi in enumerate(SIDES, 1):
            x = roi[0]
            lines = self.lines(image, (x + 30, 400, 375, 330))
            cost = "".join(self.lines(image, (x + 60, 735, 300, 110)))
            match = re.search(r"[×xX](\d+)", re.sub(r"\s+", "", cost))
            option = {"slot": f"option_{index}", "name": lines[0] if lines else "",
                      "tags": lines[1] if len(lines) > 1 else "", "text": "\n".join(lines[2:]),
                      "refresh_cost": int(match[1]) if match else None}
            difficulty = re.search(r"敌人难度([+＋-]\d+)", "".join(lines))
            option["enemy_difficulty_change"] = int(difficulty[1].replace("＋", "+")) if difficulty else None
            if len(lines) < 3 or match is None:
                option["unknown"] = True
            options.append(option)
        banner = "".join(self.lines(image, (840, 115, 330, 40)))
        count = re.search(r"剩(\d+)次", banner)
        money = "".join(self.lines(image, (1345, 933, 55, 43)))
        return {"screen": "选择主角", "stage": "selection", "needs_ai": True,
                "characters": self.characters(image), "character": self.character(image),
                "options": options, "confirm_enabled": self.enabled(image),
                "refreshes_left": int(count[1]) if count else None,
                "gold": int(money) if money.isdecimal() else None}

    def following(self, image):
        result = preparation_page(image, self.preparation)
        if result["result"] == "success":
            button = self.preparation.action_button(image)
            # 防：返回备战的过场中文字尚未出现，或仍显示未完成的「选择主角」入口。
            if button["button_text"] and button["next_action"] != "protagonist-open":
                return {**result, **button}
        return None

    def refresh_button(self, image, option):
        side = "left" if option == 1 else "right"
        for name in (f"refresh_{side}", f"refresh_{side}_hover"):
            match = self.find(image, name, SIDES[option - 1])
            if match is not None:
                return match
        return None


class ProtagonistOperation:
    def __init__(self, session, reader):
        self.session = session
        self.reader = reader
        self.image = None
        self.clicked = False
        self.observation = {"screen": "未知", "needs_ai": True}

    def capture(self):
        job = self.session.controller.post_screencap().wait()
        if not job.succeeded:
            raise RuntimeError("主角截图失败")
        return job.get()

    def input(self, job, step):
        self.clicked = True
        if not job.wait().succeeded:
            raise InputUncertain(f"主角{step}结果待核对")

    def click(self, point):
        self.input(self.session.controller.post_click(*point), "点击")

    def park(self):
        self.input(self.session.controller.post_touch_move(*BLANK), "移开鼠标")

    def observe(self, image):
        return observation(self.reader, image, self.reader.stage(image))

    def wait(self, check, message):
        for _ in range(ATTEMPTS):
            sleep(INTERVAL)
            self.image = self.capture()
            self.observation = self.observe(self.image)
            if check(self.observation):
                return self.observation
        return {**self.observation, "result": "uncertain", "message": message}

    def choose_character(self, slot, before):
        target = before["characters"][slot]
        if target is None:
            return {**before, "result": "failed", "message": f"角色槽位为空：{slot}"}
        if "unknown" in target:
            return {**before, "result": "uncertain", "message": f"角色槽位待确认：{slot}"}
        x, y, width, height = CHARACTERS[slot]
        self.input(self.session.controller.post_swipe(x + width // 2, y + height // 2, *TARGET, 700), "拖选")
        self.park()
        return self.wait(lambda result: (result["result"] == "success" and result.get("stage") == "selection"
                         and result["character"] is not None and result["character"].get("name") == target["name"]),
                         f"主角拖选结果待核对：{slot}")

    def choose_aura(self, option, before):
        if before["character"] is None or "unknown" in before["character"]:
            return {**before, "result": "failed", "message": "先指定主角角色"}
        x, y, width, height = SIDES[option - 1]
        point = (x + width // 2, y + height // 2)
        self.click(point)
        sleep(1)
        self.click(point)
        self.park()
        sleep(INTERVAL)
        self.image = self.capture()
        return self.observe(self.image)

    def refresh(self, option, before):
        cost = before["options"][option - 1]["refresh_cost"]
        if before["refreshes_left"] == 0 or before["gold"] < cost:
            return {**before, "result": "failed", "message": "光环刷新次数或金币不足"}
        match = self.reader.refresh_button(self.image, option)
        if match is None:
            return {**before, "result": "failed", "message": "光环刷新按钮待确认"}
        x, y, width, height = match.box
        self.click((x + width // 2, y + height // 2))
        self.park()
        return self.wait(lambda result: (result["result"] == "success" and result.get("stage") == "selection"
                         and result["refreshes_left"] == before["refreshes_left"] - 1
                         and result["gold"] == before["gold"] - cost), "光环刷新次数与金币变化待核对")

    def confirm(self, before):
        if before["character"] is None or "unknown" in before["character"] or not before["confirm_enabled"]:
            return {**before, "result": "failed", "message": "主角、光环及确认按钮待确认"}
        self.click(CONFIRM)
        for _ in range(ATTEMPTS):
            sleep(INTERVAL)
            self.image = self.capture()
            result = self.reader.following(self.image)
            if result is not None:
                return result
            self.observation = self.observe(self.image)
        return {**self.observation, "result": "uncertain", "message": "主角确认后的备战与行动按钮文字待核对"}

    def execute(self, action="read", slot=None, option=None, image=None, observation=None):
        try:
            self.image = self.capture() if image is None else image
            result = self.observe(self.image) if observation is None else observation
            if action == "open" and result.get("stage") == "entry":
                self.click((1810, 750))
                result = self.wait(lambda current: current["result"] == "success" and current.get("stage") == "selection",
                                   "主角选择页仍待确认")
            elif action not in {"read", "open"}:
                if result.get("stage") != "selection":
                    return {**result, "result": "failed", "message": "主角选择页与选项待确认"}
                if result["result"] != "success":
                    return result
                if action == "character":
                    result = self.choose_character(slot, result)
                elif action == "aura":
                    result = self.choose_aura(option, result)
                elif action == "refresh":
                    result = self.refresh(option, result)
                elif action == "confirm":
                    result = self.confirm(result)
        except ERRORS as error:
            if not self.clicked:
                raise
            result = uncertain(error, "主角操作后出错，附最近观察截图")
        return result

    def save(self, result):
        try:
            return {**self.session.save_image(self.image), **result}
        except ERRORS as error:
            if self.clicked:
                raise InputUncertain(f"主角输入后保存截图出错：{type(error).__name__}: {error}") from error
            raise

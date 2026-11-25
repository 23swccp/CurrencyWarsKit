"""遭遇节点的奖励、难度与单次选择。"""

import re
from time import monotonic, sleep

import numpy as np
from PIL import Image
from maa.resource import Resource
from maa.tasker import Tasker

from game.input import ERRORS, InputUncertain, uncertain
from screens.preparation import ASSETS, Recognition

TITLE = (880, 65, 170, 70)
LEFTS = (487, 1073)
OPTIONS = ((667, 480), (1253, 480))
REFRESH = (672, 900)
CONFIRM = (1082, 900)
TIMEOUT = 8
INTERVAL = 0.3


def compact(text):
    return re.sub(r"\s+", "", text)


def complete(result):
    return all("unknown" not in option for option in result["options"])


class Encounter(Recognition):
    def __init__(self, preparation):
        self.preparation = preparation
        super().__init__(preparation.controller)
        for name in ("title",):
            with Image.open(ASSETS / "encounter" / f"{name}.png") as image:
                self.resource.override_image(f"encounter_{name}.png",
                                             np.asarray(image.convert("RGB"))[:, :, ::-1].copy())

    def page(self, image):
        # 防：识别区域仅适用于 1920×1080 的画面。
        if image.shape[:2] != (1080, 1920):
            raise ValueError("遭遇识别要求 1920×1080 截图")
        return self.score(image, "encounter_title.png", TITLE) >= 0.9

    def text(self, image, name, roi):
        self.preparation.load_ocr()
        return compact(self.preparation.text(image, name, roi))

    def selected(self, image):
        matches = [i for i, x in enumerate(LEFTS, 1)
                   if np.all(image[235:271, x + 120:x + 240] >= 230, axis=2).sum() >= 400]
        return matches[0] if len(matches) == 1 else None

    def refreshes(self, image):
        text = self.text(image, "遭遇刷新次数", (700, 875, 160, 50))
        match = re.fullmatch(r"剩余次数[:：](\d+)", text)
        # 防：刷新次数漏读。
        return int(match[1]) if match else None

    def read(self, image):
        options = []
        for index, x in enumerate(LEFTS, 1):
            title = self.text(image, f"遭遇{index}难度", (x + 63, 360, 240, 65))
            match = re.fullmatch(r"遭遇其([一二三四五六七八九])", title)
            difficulty = "一二三四五六七八九".index(match[1]) + 1 if match else None
            reward = self.text(image, f"遭遇{index}奖励", (x + 128, 625, 200, 60))
            count_text = self.text(image, f"遭遇{index}数量", (x + 93, 650, 35, 38))
            count = int(count_text) if re.fullmatch(r"\d+", count_text) else None
            option = {"slot": f"option_{index}", "title": title, "difficulty": difficulty,
                      "reward": {"text": reward, "count": count}}
            # 防：难度或奖励文字 OCR 漏读，保留已识别内容交给 AI。
            # 装备类奖励本来没有数量（实机 1-7 光能电池），count 为空不算未读全。
            if difficulty is None or not reward:
                option["unknown"] = True
            options.append(option)
        return {"screen": "遭遇", "title": "遭遇节点", "options": options,
                "selected": self.selected(image), "refreshes_left": self.refreshes(image), "needs_ai": True}


class EncounterOperation:
    def __init__(self, session, reader):
        self.session = session
        self.reader = reader
        self.image = None
        self.clicked = False
        self.observation = {"screen": "未知", "needs_ai": True}

    def capture(self):
        job = self.session.controller.post_screencap().wait()
        # 防：截图失败。
        if not job.succeeded:
            raise RuntimeError("遭遇截图失败")
        return job.get()

    def wait(self, inspect, message, image=None):
        deadline = monotonic() + TIMEOUT
        while True:
            self.image = self.capture() if image is None else image
            result = inspect(self.image)
            if result is not None:
                return result
            # 防：识别等待超过 8 秒，返回最后现场。
            if monotonic() >= deadline:
                return {**self.observation, "result": "uncertain", "message": message}
            sleep(INTERVAL)
            image = None

    def observe(self, image=None):
        def inspect(frame):
            if self.reader.page(frame):
                self.observation = self.reader.read(frame)
                if complete(self.observation):
                    return {**self.observation, "result": "success"}
            return None
        return self.wait(inspect, "遭遇选项读取或页面确认超时", image)

    def refresh(self, before):
        count = before["refreshes_left"]
        # 防：剩余次数漏读或为零。
        if count is None:
            return {**before, "result": "uncertain", "message": "遭遇刷新次数待确认"}
        if count == 0:
            return {**before, "result": "failed", "message": "遭遇刷新次数为零"}
        self.clicked = True
        if not self.session.controller.post_click(*REFRESH).wait().succeeded:
            raise InputUncertain("遭遇刷新点击结果待核对")

        def inspect(frame):
            if self.reader.page(frame):
                current = self.reader.refreshes(frame)
                self.observation = {"screen": "遭遇", "refreshes_left": current, "needs_ai": True}
                if current == count - 1:
                    self.observation = self.reader.read(frame)
                    return {**self.observation, "result": "success" if complete(self.observation) else "uncertain"}
            return None
        return self.wait(inspect, "遭遇刷新结果待核对")

    def choose(self, option, before):
        # 防：已有选中项时再点会改动已确认的选择，交回调用方核对。
        if before["selected"] is not None:
            return {**before, "result": "uncertain", "message": "已有选中项，请核对后操作"}
        resource = Resource()
        for name in ("confirm",):
            with Image.open(ASSETS / "encounter" / f"{name}.png") as image:
                resource.override_image(f"encounter_{name}.png", np.asarray(image.convert("RGB"))[:, :, ::-1].copy())
        tasker = Tasker()
        if not tasker.bind(resource, self.session.controller) or not tasker.inited:
            raise RuntimeError("遭遇选择 Pipeline 初始化失败")
        x, y = OPTIONS[option - 1]
        pipeline = {
            "EncounterSelect": {"recognition": "DirectHit", "action": {"type": "Click", "param": {"target": [x, y, 1, 1]}},
                                "next": ["EncounterSelected"], "timeout": 8000, "post_delay": 300},
            "EncounterSelected": {"recognition": {"type": "ColorMatch", "param": {
                "roi": [LEFTS[option - 1] + 120, 235, 120, 36], "lower": [[230, 230, 230]],
                "upper": [[255, 255, 255]], "method": 4, "count": 400}},
                "next": ["EncounterConfirm"], "timeout": 8000},
            "EncounterConfirm": {"recognition": {"type": "TemplateMatch", "param": {
                "template": ["encounter_confirm.png"], "roi": [890, 870, 390, 65],
                "threshold": [0.9], "method": 5}},
                "action": {"type": "Click", "param": {"target": [*CONFIRM, 1, 1]}}},
        }
        self.clicked = True
        job = tasker.post_task("EncounterSelect", pipeline).wait()
        if not job.succeeded:
            self.image = self.capture()
            detail = job.get()
            nodes = [(node.name, node.completed) for node in detail.nodes] if detail else None
            return {"result": "uncertain", "screen": "遭遇", "needs_ai": True,
                    "message": f"遭遇选择 Pipeline 未完成，任务 EncounterSelect，节点：{nodes}"}

        def following(frame):
            if self.reader.page(frame):
                self.observation = self.reader.read(frame)
            else:
                self.observation = self.session.preparation_scene(frame)
                if self.observation["screen"] == "备战":
                    return self.observation
                # 防：确认后出现其他界面，交给 AI 判断。
                if self.observation["screen"] != "未知":
                    return {**self.observation, "result": "uncertain", "needs_ai": True,
                            "message": "遭遇确认后出现其他界面，请核对现场"}
            return None
        return self.wait(following, "遭遇确认后的界面待核对")

    def execute(self, action="read", option=None, image=None):
        try:
            result = self.observe(image)
            if result["result"] == "success":
                if action == "refresh":
                    result = self.refresh(result)
                elif action == "choose":
                    result = self.choose(option, result)
        except ERRORS as error:
            # 防：输入已发出后出错，返回待核对而非确定失败。
            if not self.clicked:
                raise
            result = uncertain(error, "遭遇操作后出错，附最近观察截图")
        return result

    def save(self, result):
        try:
            return {**self.session.save_image(self.image), **result}
        except ERRORS as error:
            # 防：输入后保存现场失败，游戏结果待核对。
            if self.clicked:
                raise InputUncertain(f"遭遇操作后保存截图出错：{type(error).__name__}: {error}") from error
            raise

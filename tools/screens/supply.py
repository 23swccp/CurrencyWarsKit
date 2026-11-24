"""补给选项、可选刷新与单选确认。"""

import re
from time import monotonic, sleep

import numpy as np
from PIL import Image

from game.input import ERRORS, InputUncertain, uncertain
from screens.preparation import ASSETS, Recognition


TITLE = (890, 130, 145, 55)
TITLE_SCORE = 0.85
LEFTS = (124, 479, 835, 1190, 1545)
OPTIONS = tuple((x + 125, 440) for x in LEFTS)
SELECTION = (1635, 910, 135, 35)
REFRESH_AREA = (1285, 955, 65, 65)
REFRESH_COUNT = (1345, 963, 150, 46)
REFRESH = (1318, 987)
CONFIRM = (1694, 987)
TIMEOUT = 8
INTERVAL = 0.3


def complete(result):
    return (result["selection"]["current"] is not None and result["selection"]["limit"] is not None
            and all("unknown" not in option for option in result["options"]))


class Supply(Recognition):
    def __init__(self, preparation):
        self.preparation = preparation
        super().__init__(preparation.controller)
        for name in ("title", "trial", "refresh", "refresh_disabled"):
            with Image.open(ASSETS / "supply" / f"{name}.png") as image:
                self.resource.override_image(f"supply_{name}.png",
                                             np.asarray(image.convert("RGB"))[:, :, ::-1].copy())

    def page(self, image):
        # 防：固定布局仅适配 1920×1080 的窗口截图。
        if image.shape[:2] != (1080, 1920):
            raise ValueError("补给识别要求 1920×1080 截图")
        return self.score(image, "supply_title.png", TITLE) >= TITLE_SCORE

    def text(self, image, name, roi):
        self.preparation.load_ocr()
        return re.sub(r"\s+", "", self.preparation.text(image, name, roi))

    def selection(self, image):
        text = self.text(image, "补给选择计数", SELECTION)
        match = re.fullmatch(r"已选择(\d+)/(\d+)", text)
        # 防：选择计数漏读。
        return {"current": int(match[1]) if match else None, "limit": int(match[2]) if match else None}

    def refresh(self, image):
        visible = (self.score(image, "supply_refresh.png", REFRESH_AREA) >= 0.9
                   or self.score(image, "supply_refresh_disabled.png", REFRESH_AREA) >= 0.9)
        result = {"can_refresh": visible}
        if visible:
            text = self.text(image, "补给刷新次数", REFRESH_COUNT)
            match = re.fullmatch(r"剩余次数[:：](\d+)", text)
            # 防：刷新按钮存在但次数漏读，与没有刷新按钮区分。
            result["refreshes_left"] = int(match[1]) if match else None
        return result

    def read(self, image):
        options = []
        for index, x in enumerate(LEFTS, 1):
            character = self.text(image, f"补给{index}角色", (x, 548, 255, 43))
            equipment = self.text(image, f"补给{index}装备", (x + 72, 675, 220, 58))
            trial = self.score(image, "supply_trial.png", (x + 200, 355, 65, 45)) >= 0.9
            option = {"slot": f"option_{index}", "character": character, "equipment": equipment, "trial": trial}
            # 防：角色或装备文字漏读，保留已经识别到的选项内容。
            if not character or not equipment:
                option["unknown"] = True
            options.append(option)
        return {"screen": "补给", "title": "补给阶段", "options": options,
                "selection": self.selection(image), **self.refresh(image), "node": self.preparation.header(image)["node"],
                "needs_ai": True}


class SupplyOperation:
    def __init__(self, session, reader):
        self.session = session
        self.reader = reader
        self.image = None
        self.clicked = False
        self.observation = {"screen": "未知", "needs_ai": True}

    def capture(self):
        job = self.session.controller.post_screencap().wait()
        # 防：截图任务失败，保留此前现场。
        if not job.succeeded:
            raise RuntimeError("补给截图失败")
        return job.get()

    def click(self, point):
        self.clicked = True
        # 防：点击任务失败，游戏输入结果待核对。
        if not self.session.controller.post_click(*point).wait().succeeded:
            raise InputUncertain(f"补给点击结果待核对：{point}")

    def wait(self, inspect, message, image=None):
        deadline = monotonic() + TIMEOUT
        while True:
            self.image = self.capture() if image is None else image
            self.observation = {"screen": "未知", "needs_ai": True}
            result = inspect(self.image)
            if result is not None:
                return result
            # 防：过场或识别内容在八秒内仍未确认，停止等待并返回最后现场。
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
        return self.wait(inspect, "补给选项或选择计数读取超时", image)

    def refresh(self, before):
        # 防：部分补给没有刷新控件。
        if not before["can_refresh"]:
            return {**before, "result": "failed", "message": "当前补给不支持刷新"}
        count = before["refreshes_left"]
        # 防：刷新按钮存在但次数漏读。
        if count is None:
            return {**before, "result": "uncertain", "message": "补给刷新次数待确认"}
        # 防：界面显示没有剩余刷新次数。
        if count == 0:
            return {**before, "result": "failed", "message": "补给刷新次数为零"}
        self.click(REFRESH)

        def inspect(frame):
            if self.reader.page(frame):
                refresh = self.reader.refresh(frame)
                self.observation = {"screen": "补给", **refresh, "needs_ai": True}
                if refresh["can_refresh"] and refresh["refreshes_left"] == count - 1:
                    self.observation = self.reader.read(frame)
                    if complete(self.observation):
                        return {**self.observation, "result": "success"}
            return None
        return self.wait(inspect, "补给刷新结果待核对")

    def choose(self, option, before):
        # 防：当前样例是单选；多选规则及已有选中项的标记尚无实机样例。
        if before["selection"] != {"current": 0, "limit": 1}:
            return {**before, "result": "uncertain", "message": "当前仅支持从未选择状态执行单选补给，请核对现场"}
        self.click(OPTIONS[option - 1])

        def selected(frame):
            if self.reader.page(frame):
                selection = self.reader.selection(frame)
                self.observation = {"screen": "补给", "selection": selection, "needs_ai": True}
                if selection == {"current": 1, "limit": 1}:
                    return {"result": "success"}
            return None
        result = self.wait(selected, f"第 {option} 项补给选中结果待核对")
        if result["result"] != "success":
            return result
        self.click(CONFIRM)

        def following(frame):
            if self.reader.page(frame):
                self.observation = self.reader.read(frame)
            else:
                scene = self.session.preparation_scene(frame)
                self.observation = scene
                if scene["screen"] == "备战":
                    return scene
                # 防：确认后出现其他选择，保留现场交给 AI。
                if scene["screen"] != "未知":
                    return {**scene, "result": "uncertain", "needs_ai": True,
                            "message": "补给确认后出现其他界面，请核对领取结果"}
            return None
        return self.wait(following, "补给确认后的界面待核对")

    def execute(self, action="read", option=None, image=None):
        try:
            result = self.observe(image)
            if result["result"] == "success":
                if action == "refresh":
                    result = self.refresh(result)
                elif action == "choose":
                    result = self.choose(option, result)
        except ERRORS as error:
            # 防：输入已经发出后出错，实际游戏结果只能交回核对。
            if not self.clicked:
                raise
            result = uncertain(error, "补给操作后出错，附最近观察截图")
        return result

    def save(self, result):
        try:
            return {**self.session.save_image(self.image), **result}
        except ERRORS as error:
            # 防：输入后写截图失败，游戏结果待核对。
            if self.clicked:
                raise InputUncertain(f"补给操作后保存截图出错：{type(error).__name__}: {error}") from error
            raise

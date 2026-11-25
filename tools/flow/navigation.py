"""货币战争首页导航、最高职级复位与开始对局。"""

from time import monotonic, sleep

from maa.pipeline import JOCR, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker

from ocr import MODEL
from recognition_debug import focus

# 1920×1080 画面的点击坐标。
START = (1590, 973)
STANDARD = (260, 260)
ENTER_STANDARD = (1634, 960)
RANK_DOWN = (960, 843)
HIGHEST_RANK = (1380, 966)
START_GAME = (1690, 966)
NEXT = (1400, 984)
CONTINUE = (960, 965)
OPTIONS = ((440, 530), (960, 530), (1480, 530))
CONFIRM = (960, 984)
DISMISS_INTRO = (100, 1000)


class NavigationStopped(RuntimeError):
    def __init__(self, result):
        super().__init__(result["message"])
        self.result = result


class Navigator:
    def __init__(self, session):
        from game.equipment import Equipment
        self.session = session
        self.clicked = False
        self.equipment = Equipment()
        self.resource = Resource()
        if not self.resource.use_cpu() or not self.resource.post_ocr_model(MODEL).wait().succeeded:
            raise RuntimeError(f"OCR 模型加载失败：{MODEL}")
        self.tasker = Tasker()
        if not self.tasker.bind(self.resource, session.controller) or not self.tasker.inited:
            raise RuntimeError("无法初始化导航文字识别")

    def capture_image(self):
        job = self.session.controller.post_screencap().wait()
        # 防：截图失败或尺寸不是样例的 1920×1080。
        if not job.succeeded:
            raise RuntimeError("导航截图失败")
        image = job.get()
        if image.shape[:2] != (1080, 1920):
            raise ValueError("导航仅支持 1920×1080 截图")
        return image

    def read(self, image, roi):
        focus(image, roi, "导航 OCR", "ocr")
        detail = self.tasker.post_recognition(JRecognitionType.OCR, JOCR(roi=roi), image).wait().get()
        # 防：OCR 未返回详情，与「没有匹配文字」区分。
        if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
            raise RuntimeError("导航 OCR 未返回识别详情")
        return [{"text": item.text, "box": item.box, "score": item.score}
                for item in detail.nodes[0].recognition.filtered_results]

    def texts(self, image, roi):
        return [item["text"] for item in self.read(image, roi)]

    def observe(self):
        image = self.capture_image()
        return self.texts(image, (1250, 900, 640, 145)), self.texts(image, (40, 130, 490, 410))

    def click(self, x, y):
        from game.input import InputUncertain
        # 防：点击失败时游戏可能已收到输入。
        self.clicked = True
        if not self.session.controller.post_click(x, y).wait().succeeded:
            raise InputUncertain("导航点击失败，输入结果不确定；请重新观察")

    def run(self, option=None):
        from game.input import ERRORS, InputUncertain
        try:
            return self.navigate() if option is None else self.choose_environment(option)
        except NavigationStopped as error:
            return error.result
        except InputUncertain:
            raise
        # 防：点击已发出后截图或识别失败，改判 uncertain。
        except ERRORS as error:
            if self.clicked:
                raise InputUncertain(f"导航点击后出错，结果不确定：{type(error).__name__}: {error}") from error
            raise

    def navigate(self):
        button, modes = self.observe()
        if any("下一步" in t for t in button):
            return self.opening()
        if any("开始对局" in t for t in button):
            return self.reset_rank()
        if any("开始" in t and "货币战争" in t for t in button):
            self.click(*START)
            deadline = monotonic() + 8
            while True:
                button, modes = self.observe()
                if any("标准博弈" in t for t in modes):
                    break
                if monotonic() >= deadline:
                    self.timeout("未能确认模式选择页面")
                sleep(0.3)
        if not any("标准博弈" in t for t in modes):
            image = self.capture_image()
            if any("投资环境" in t for t in self.texts(image, (850, 65, 240, 75))):
                return self.read_environment(image)
            return {**self.session.save_image(image), "result": "uncertain", "message": "未能确认模式选择页面"}
        if any("进入标准博弈" in t for t in button):
            return self.enter_rank()
        # 防：模式按钮文字漏读。
        if not any("进入超频博弈" in t for t in button):
            return {**self.session.capture(), "result": "uncertain", "message": "无法确认当前模式"}
        self.click(*STANDARD)
        deadline = monotonic() + 8
        while True:
            button, modes = self.observe()
            if any("进入标准博弈" in t for t in button) and any("标准博弈" in t for t in modes):
                return self.enter_rank()
            if monotonic() >= deadline:
                self.timeout("未能确认切换至标准博弈")
            sleep(0.3)

    def wait_button(self, predicate, message):
        deadline = monotonic() + 8
        while True:
            button, _ = self.observe()
            if predicate(button):
                return
            # 防：8 秒未确认结果，按用户指定步骤 Esc 一次。
            if monotonic() >= deadline:
                self.timeout(message)
            sleep(0.3)

    def timeout(self, message):
        from game.input import KEYS, execute

        def capture_after_escape():
            image = self.capture_image()
            # 防：Esc 后第一帧仍是职级页（实机观察），补截两帧。
            for _ in range(2):
                sleep(0.3)
                image = self.capture_image()
            return image

        self.clicked = True
        image = execute("导航超时 Escape", lambda: self.session.controller.post_click_key(KEYS["escape"]),
                        capture_after_escape)
        texts = self.texts(image, (35, 55, 1850, 980))
        screen = current_screen(texts)
        # 防：Esc 可能改变页面，返回重新识别的现场。
        raise NavigationStopped({**self.session.save_image(image), "result": "uncertain",
                                 "screen": screen, "texts": texts, "needs_ai": True,
                                 "message": f"{message}；已按一次 Esc 并重新识别当前页面"})

    def enter_rank(self):
        self.click(*ENTER_STANDARD)
        self.wait_button(lambda texts: any("开始对局" in t for t in texts), "未能确认职级选择页面")
        return self.reset_rank()

    def reset_rank(self):
        self.click(*RANK_DOWN)
        deadline = monotonic() + 4
        reset = False
        while True:
            button = self.texts(self.capture_image(), (1250, 900, 640, 145))
            if any("返回最高职级" in t for t in button):
                self.click(*HIGHEST_RANK)
                self.wait_button(lambda texts: any("开始对局" in t for t in texts)
                                 and not any("返回最高职级" in t for t in texts), "未能确认最高职级复位")
                reset = True
                break
            if monotonic() >= deadline:
                # 防：已是最高职级（实机 A8/3 顶层带锁）时下移无效，页面只剩开始对局。
                if not any("开始对局" in t for t in button):
                    self.timeout("下移后未能确认返回最高职级按钮")
                break
            sleep(0.3)
        self.click(*START_GAME)
        sleep(5)  # 用户指定开始对局后等待 5 秒。
        return {**self.opening(), "rank_reset": reset}

    def wait_text(self, roi, text):
        deadline = monotonic() + 8
        while True:
            image = self.capture_image()
            if any(text in value for value in self.texts(image, roi)):
                return image
            # 防：过场 8 秒未到目标页面，按用户指定步骤 Esc 一次。
            if monotonic() >= deadline:
                self.timeout(f"未能确认页面文字：{text}")
            sleep(0.3)

    def opening(self):
        self.wait_text((470, 170, 240, 65), "本场对局首领")
        self.wait_text((1200, 940, 410, 90), "下一步")
        sleep(3)  # 实机页面文字出现后按钮尚未响应，按用户要求再多等两秒。
        self.click(*NEXT)
        self.wait_text((830, 930, 280, 80), "点击空白处继续")
        self.click(*CONTINUE)
        image = self.wait_text((850, 65, 240, 75), "投资环境")
        return self.read_environment(image)

    def read_environment(self, image):
        from screens.environment import recognize

        deadline = monotonic() + 8
        while True:
            result = recognize(image, self.read, self.equipment)
            if all("unknown" not in option for option in result["options"]):
                return {**self.session.save_image(image), **result}
            # 防：标题先于卡片出现（实机），继续截图直到 8 秒。
            if monotonic() >= deadline:
                self.timeout("未能读全投资环境卡片文字")
            sleep(0.3)
            image = self.capture_image()

    def choose_environment(self, option):
        # 防：option 坐标只适用于投资环境页。
        self.wait_text((850, 65, 240, 75), "投资环境")
        self.click(*OPTIONS[option - 1])
        self.wait_text((770, 950, 385, 75), "确认")
        self.click(*CONFIRM)
        sleep(4)  # 用户指定确认投资环境后等待 4 秒，再点击左下角关闭说明。
        self.click(*DISMISS_INTRO)
        return {**self.session.capture(), "result": "uncertain", "action": "choose_environment",
                "option": option, "message": "已确认投资环境，等待 4 秒后点击一次左下角；请查看截图确认备战页"}


def current_screen(texts):
    """超时按 Esc 后，用本次画面的明确文字识别已知页面。"""
    for marker, screen in (("挑战失败", "挑战失败"), ("投资环境", "投资环境"),
                           ("本场对局首领", "首领介绍"), ("点击空白处继续", "位面介绍"),
                           ("开始对局", "职级选择"), ("进入标准博弈", "模式选择"),
                           ("进入超频博弈", "模式选择")):
        if any(marker in text for text in texts):
            return screen
    if any("开始" in text and "货币战争" in text for text in texts):
        return "货币战争首页"
    return "未知"

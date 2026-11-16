"""战斗入场确认与旗帜监测。"""

from threading import Event, Lock, Thread
from time import monotonic, sleep

import numpy as np
from PIL import Image

from game.input import ERRORS, InputUncertain, uncertain
from ocr import ImageController
from recognition_debug import trace
from screens.preparation import ASSETS, Recognition, page as preparation_page


START = (1810, 750)
INTRO = (830, 320, 185, 80)
FLAG = (35, 0, 100, 65)
CONTINUE = (740, 830, 440, 130)
CONTINUE_BUTTON = (960, 895)
PREPARATION_DELAY = 8
TIMEOUT = 10
INTERVAL = 2
MISSING = 5
MIN_SCORE = 0.9


class Battle(Recognition):
    def __init__(self):
        self.controller = ImageController(np.zeros((1, 1, 3), dtype="uint8"))
        if not self.controller.post_connection().wait().succeeded:
            raise RuntimeError("战斗图片控制器连接失败")
        super().__init__(self.controller)
        self.state_lock = Lock()
        self.state = {"result": "uncertain", "screen": "未知", "needs_ai": True}
        self.check_after = None
        for name in ("intro", "flag", "continue"):
            with Image.open(ASSETS / "battle" / f"{name}.png") as image:
                self.resource.override_image(f"{name}.png", np.asarray(image.convert("RGB"))[:, :, ::-1].copy())

    def ready(self, image, preparation):
        page = preparation_page(image, preparation)
        if page["result"] != "success":
            return {**page, "result": "failed", "message": "出战前备战页面待确认"}
        button = preparation.action_button(image)
        # 防：右侧行动按钮未读到文字，停止点击；buff 改名后的非空文字同样接受。
        if not button["button_text"]:
            return {**page, **button, "result": "failed",
                    "message": "右侧行动按钮文字待确认"}
        # 防：已知「选择主角」入口会打开选择页，不能作为出战执行。
        if button["next_action"] == "protagonist-open":
            return {**page, **button, "result": "failed", "needs_ai": True,
                    "message": "当前入口为选择主角，请先完成主角选择"}
        return {**page, **button}

    def recognize(self, image):
        check_size(image)
        button = self.score(image, "continue.png", CONTINUE)
        if button >= MIN_SCORE:
            return {"result": "success", "screen": "本场结算", "needs_ai": True,
                    "anchors": {"continue": round(button, 3)}}
        flag = self.score(image, "flag.png", FLAG)
        anchors = {"flag": round(flag, 3)}
        if flag >= MIN_SCORE:
            return {"result": "success", "screen": "战斗", "anchors": anchors}
        intro = self.score(image, "intro.png", INTRO)
        anchors["intro"] = round(intro, 3)
        if intro >= MIN_SCORE:
            return {"result": "success", "screen": "战斗", "anchors": anchors}
        return {"result": "uncertain", "screen": "未知", "anchors": anchors}

    def current(self):
        with self.state_lock:
            return self.state.copy()

    def publish(self, result):
        with self.state_lock:
            self.state = result.copy()

    def begin(self, result):
        self.check_after = monotonic() + MISSING if result["screen"] == "战斗" else None
        self.publish(result)

    def observe(self, image):
        check_size(image)
        flag = self.score(image, "flag.png", FLAG)
        now = monotonic()
        result = {"result": "success", "screen": "战斗", "anchors": {"flag": round(flag, 3)}}
        if flag >= MIN_SCORE:
            self.check_after = now + MISSING
        # check_after 只在战斗状态非空，见 begin 与 fail。
        elif now >= self.check_after:
            button = self.score(image, "continue.png", CONTINUE)
            result["anchors"]["continue"] = round(button, 3)
            self.check_after = now + MISSING
            if button >= MIN_SCORE:
                result.update(screen="本场结算", needs_ai=True)
                self.check_after = None
        self.publish(result)
        return result

    def next_delay(self):
        return max(0, min(INTERVAL, self.check_after - monotonic()))

    def fail(self, error):
        message = f"战斗监测出错：{type(error).__name__}: {error}"
        self.check_after = None
        self.publish({"result": "uncertain", "screen": "未知", "needs_ai": True, "message": message})
        return message


def check_size(image):
    if image.shape[:2] != (1080, 1920):
        raise ValueError("战斗识别要求 1920×1080 截图")


def capture(controller):
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("战斗截图失败")
    return job.get()


def start(session, reader):
    image = capture(session.controller)
    result = reader.ready(image, session.preparation_reader())
    if result["result"] != "success":
        return {**session.save_image(image), **result, "action": "start-battle"}
    try:
        if not session.controller.post_click(*START).wait().succeeded:
            raise InputUncertain("出战点击结果待核对")
        deadline = monotonic() + TIMEOUT
        result = {"result": "uncertain", "screen": "未知"}
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(INTERVAL, remaining))
            image = capture(session.controller)
            result = reader.recognize(image)
            if result["result"] == "success":
                break
        if result["result"] != "success":
            result = {**result, "needs_ai": True, "message": "出战后 10 秒内战斗界面仍待确认"}
    except ERRORS as error:
        result = uncertain(error, "出战确认出错，附最近观察截图")
    reader.begin(result)
    try:
        return {**session.save_image(image), **result, "action": "start-battle"}
    except ERRORS as error:
        raise InputUncertain(f"出战后保存截图出错：{type(error).__name__}: {error}") from error


def finish(session, reader, image):
    before = session.save_image(image)
    try:
        reader.begin({"result": "success", "screen": "等待备战"})
        if not session.controller.post_click(*CONTINUE_BUTTON).wait().succeeded:
            raise InputUncertain("继续挑战点击结果待核对")
        result = session.enter_preparation(delay=PREPARATION_DELAY)
    except ERRORS as error:
        result = {**before, **uncertain(error, "继续挑战后出错，附点击前结算截图")}
    result = {**result, "action": "continue-battle"}
    reader.begin(result)
    return result


def continue_battle(session, reader):
    image = capture(session.controller)
    result = reader.recognize(image)
    if result["screen"] != "本场结算":
        return {**session.save_image(image), **result, "result": "failed", "action": "continue-battle",
                "message": "继续挑战按钮待确认"}
    return finish(session, reader, image)


class Monitor:
    def __init__(self, session, reader):
        self.session = session
        self.reader = reader
        self.stopped = Event()
        self.thread = Thread(target=self.run, name="battle-monitor", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def close(self):
        self.stop()
        self.thread.join()

    def tick(self):
        if not self.session.operation_lock.acquire(blocking=False):
            return INTERVAL
        try:
            screen = self.reader.current()["screen"]
            if screen not in {"战斗", "本场结算"}:
                return INTERVAL
            with trace(self.session.overlay):
                image = capture(self.session.controller)
                result = self.reader.observe(image) if screen == "战斗" else self.reader.recognize(image)
                if screen == "本场结算":
                    self.reader.begin(result)
                if result["screen"] == "本场结算":
                    with self.session.metadata_lock:
                        self.session.operation = "continue-battle"
                    self.session.finish_battle(image)
            return self.reader.next_delay() if result["screen"] == "战斗" else INTERVAL
        except (OSError, RuntimeError, ValueError) as error:
            message = self.reader.fail(error)
            with self.session.metadata_lock:
                self.session.error = message
            return INTERVAL
        finally:
            with self.session.metadata_lock:
                self.session.operation = None
            self.session.operation_lock.release()
            if self.session.collector is not None:
                with self.session.metadata_lock:
                    waiting = self.session.collector.state == "waiting"
                if waiting:
                    self.session.collector.wake()

    def run(self):
        delay = INTERVAL
        while not self.stopped.wait(delay):
            delay = self.tick()

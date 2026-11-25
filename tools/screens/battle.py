"""战斗入场确认与旗帜监测。"""

import re
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
LIMIT_WARNING = (690, 490, 540, 55)
LIMIT_CONFIRM = (1172, 684)
PREPARATION_DELAY = 8
TIMEOUT = 10
INTERVAL = 2
MISSING = 5
MIN_SCORE = 0.9
RESULT_TITLE = (810, 190, 300, 75)
RESULT_HEALTH = (640, 480, 195, 50)


def settlement_details(image, preparation):
    preparation.load_ocr()
    title = re.sub(r"\s+", "", preparation.text(image, "战斗结果", RESULT_TITLE))
    health_text = re.sub(r"\s+", "", preparation.text(image, "结算生命值", RESULT_HEALTH))
    health = re.fullmatch(r"小队生命值(\d+)", health_text)
    # 防：已确认普通及最终战胜利标题，其他标题不推断胜负；原文交给调用方判断。
    return {"outcome": "victory" if title == "挑战成功" else "unknown",
            "title": title or None,
            "health": int(health[1]) if health else None}


def limit_warning(image, preparation):
    """出战后上阵人数未达上限时的确认框文字（实机 1-3，4/5 上阵）。"""
    preparation.load_ocr()
    text = re.sub(r"\s+", "", preparation.text(image, "可出战人数提示", LIMIT_WARNING))
    return "人数未达上限" in text


class Battle(Recognition):
    def __init__(self):
        self.controller = ImageController(np.zeros((1, 1, 3), dtype="uint8"))
        if not self.controller.post_connection().wait().succeeded:
            raise RuntimeError("战斗图片控制器连接失败")
        super().__init__(self.controller)
        self.state_lock = Lock()
        self.state = {"result": "uncertain", "screen": "未知", "needs_ai": True}
        self.active_battle = None
        self.latest_battle = None
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
        # 防：「选择主角」或「幕后茶歇」入口会打开对应界面。
        if button["next_action"] not in (None, "start-battle"):
            return {**page, **button, "result": "failed", "needs_ai": True,
                    "message": f"当前入口为{button['button_text']}，请先处理该流程"}
        return {**page, **button, "health": preparation.health(image)}

    def start_receipt(self, node, health):
        with self.state_lock:
            self.active_battle = {"node": node, "outcome": "unknown", "health_before": health,
                                  "health_after": None, "health_after_source": None, "health_change": None,
                                  "settlement_screenshot": None, "next_screen": None}
            if self.latest_battle is not None:
                self.latest_battle["awaiting_health"] = False

    def settlement_receipt(self, screenshot, outcome, health, *, final=False):
        with self.state_lock:
            battle = self.active_battle
            # 防：服务重启后从本场结算手动接管，没有出战前记录。
            if battle is None:
                battle = {"node": None, "outcome": "unknown", "health_before": None,
                          "health_after": None, "health_after_source": None, "health_change": None,
                          "settlement_screenshot": None, "next_screen": None}
            # 防：继续挑战点击结果不确定，手动接管同一结算页。
            if battle["settlement_screenshot"] is not None:
                screenshot = battle["settlement_screenshot"]
                outcome = battle["outcome"] if outcome == "unknown" else outcome
                health = battle["health_after"] if health is None else health
            self.latest_battle = {**battle, "outcome": outcome, "health_after": health,
                                  "health_after_source": "settlement" if health is not None else None,
                                  "settlement_screenshot": screenshot, "awaiting_health": health is None and not final}
            if health is not None and battle["health_before"] is not None:
                self.latest_battle["health_change"] = health - battle["health_before"]
            self.active_battle = self.latest_battle.copy()

    def end_battle(self):
        with self.state_lock:
            self.active_battle = None

    def update_receipt(self, result):
        with self.state_lock:
            battle = self.latest_battle
            if battle is None:
                return
            if battle["next_screen"] is None:
                battle["next_screen"] = result["screen"]
            if not battle["awaiting_health"]:
                return
            # 防：战后先进入选择页，等下一张备战帧，不沿用上一场生命值。
            if result["screen"] == "备战" and result.get("node") is not None and result.get("health") is not None:
                battle["health_after"] = result["health"]
                battle["health_after_source"] = "preparation"
                if battle["health_before"] is not None:
                    battle["health_change"] = result["health"] - battle["health_before"]
                battle["awaiting_health"] = False

    def final_health(self, health):
        with self.state_lock:
            battle = self.latest_battle
            # 防：整局结算页面的生命值可能漏读。
            if health is None:
                return
            battle["health_after"] = health
            battle["health_after_source"] = "final_summary"
            if battle["health_before"] is not None:
                battle["health_change"] = health - battle["health_before"]
            battle["awaiting_health"] = False

    def receipt(self):
        with self.state_lock:
            return None if self.latest_battle is None else self.latest_battle.copy()

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
    reader.update_receipt(result)
    reader.start_receipt(result["node"], result["health"])
    try:
        if not session.controller.post_click(*START).wait().succeeded:
            raise InputUncertain("出战点击结果待核对")
        deadline = monotonic() + TIMEOUT
        preparation = session.preparation_reader()
        result = {"result": "uncertain", "screen": "未知"}
        confirmed = False
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(INTERVAL, remaining))
            image = capture(session.controller)
            result = reader.recognize(image)
            if result["result"] == "success":
                break
            # 防：上阵人数未达上限时出战会先弹确认框，只点一次并重新计时。
            if not confirmed and result["screen"] == "未知" and limit_warning(image, preparation):
                if not session.controller.post_click(*LIMIT_CONFIRM).wait().succeeded:
                    raise InputUncertain("出战人数确认点击结果待核对")
                confirmed = True
                deadline = monotonic() + TIMEOUT
        if result["result"] != "success":
            waited = "确认人数后 10 秒" if confirmed else "10 秒"
            result = {**result, "needs_ai": True, "message": f"出战后{waited}内战斗界面仍待确认"}
    except ERRORS as error:
        result = uncertain(error, "出战确认出错，附最近观察截图")
    reader.begin(result)
    try:
        return {**session.save_image(image), **result, "action": "start-battle"}
    except ERRORS as error:
        raise InputUncertain(f"出战后保存截图出错：{type(error).__name__}: {error}") from error


def finish(session, reader, image):
    before = session.save_image(image)
    details = settlement_details(image, session.preparation_reader())
    reader.settlement_receipt(before["screenshot"], details["outcome"], details["health"])
    try:
        reader.begin({"result": "success", "screen": "等待备战"})
        if not session.controller.post_click(*CONTINUE_BUTTON).wait().succeeded:
            raise InputUncertain("继续挑战点击结果待核对")
        reader.end_battle()
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

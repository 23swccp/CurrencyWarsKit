"""按住鼠标，沿奖励区遍历一轮。"""

from math import ceil, hypot
from time import sleep
from threading import Event, Thread

from game.input import ERRORS, InputUncertain
from game.slots import REWARD_CORNERS, REWARD_START
from recognition_debug import trace


def reward_path():
    points = [REWARD_CORNERS[0]]
    for (x0, y0), (x1, y1) in zip(REWARD_CORNERS, REWARD_CORNERS[1:]):
        steps = ceil(hypot(x1 - x0, y1 - y0) / 25)
        points.extend((round(x0 + (x1 - x0) * step / steps), round(y0 + (y1 - y0) * step / steps))
                      for step in range(1, steps + 1))
    return tuple(points)


TRACE = reward_path()


def wait_input(job, name):
    # 防：鼠标任务失败。
    if not job.wait().succeeded:
        raise RuntimeError(f"奖励收集{name}失败")


def sweep(controller):
    try:
        wait_input(controller.post_touch_down(*REWARD_START), "按下")
        for x, y in TRACE:
            wait_input(controller.post_touch_move(x, y), f"移动至 ({x}, {y})")
            sleep(0.04)
    finally:
        wait_input(controller.post_touch_up(), "释放")


def collect(controller, capture):
    try:
        sweep(controller)
        image = capture()
    # 防：输入已发出后鼠标任务或截图出错。
    except ERRORS as error:
        raise InputUncertain(f"奖励收集出错：{type(error).__name__}: {error}") from error
    return {**image, "result": "uncertain", "action": "collect",
            "message": "已遍历奖励区一轮，请核对奖励和后续界面"}


class Collector:
    def __init__(self, session):
        self.session = session
        self.event = Event()
        self.stopped = Event()
        self.pending = False
        self.state = "idle"
        self.latest = None
        self.thread = Thread(target=self.run, name="reward-collector", daemon=True)
        self.thread.start()

    def request(self):
        with self.session.condition:
            if not self.stopped.is_set():
                self.pending = True
                self.state = "waiting"
                self.event.set()

    def wake(self):
        with self.session.condition:
            if self.pending and not self.stopped.is_set():
                self.event.set()

    def cancel(self):
        with self.session.condition:
            self.pending = False
            self.state = "idle"

    def summary(self):
        return {"state": self.state, "pending": self.pending}

    def stop(self):
        self.stopped.set()
        with self.session.condition:
            self.pending = False
            self.state = "stopped"
        self.event.set()

    def close(self):
        self.stop()
        self.thread.join()

    def tick(self):
        session = self.session
        with session.condition:
            if self.stopped.is_set() or session.stopping or not self.pending or session.foreground_waiters:
                return
            if not session.operation_lock.acquire(blocking=False):
                return
            session.operation = "collect-background"
        before = {}
        try:
            with trace(session.overlay):
                image = session.preparation_frame()
                scene = session.preparation_scene(image)
                before = session.save_image(image)
                if scene["screen"] != "备战" or scene["shop"] != "closed":
                    result = {**before, **scene, "result": "uncertain", "needs_ai": True,
                              "message": "补领已暂停，等待商店收起的备战画面"}
                    state = "paused"
                elif warning := session.preparation.capacity_warning(image):
                    result = {**before, **scene, "result": "uncertain", **warning}
                    state = "paused"
                elif not any(session.preparation.empty(image, f"bench_{i}") for i in range(1, 10)):
                    result = {**before, **scene, "result": "uncertain", "message": "备战席已满，补领已暂停",
                              "reason": "bench_full", "needs_ai": True}
                    state = "paused"
                else:
                    with session.condition:
                        self.pending = False
                        self.state = "collecting"
                    sweep(session.controller)
                    if self.stopped.is_set() or session.stopping:
                        return
                    image = session.preparation_frame()
                    scene = session.preparation_scene(image)
                    if scene["screen"] == "备战" and scene["shop"] == "closed":
                        result = {**scene, **session.preparation.observe(image), "needs_ai": True}
                    else:
                        result = {**scene, "needs_ai": True}
                    result = {**session.save_image(image), "message": "后台已补领一轮", **result,
                              "action": "collect", "sweeps": 1}
                    state = "done"
                with session.condition:
                    if not self.stopped.is_set() and not session.stopping:
                        self.latest = result
                        self.state = state
        except ERRORS as error:
            result = {**before, "result": "uncertain", "action": "collect", "needs_ai": True,
                      "message": f"后台补领出错：{type(error).__name__}: {error}"}
            with session.condition:
                self.pending = False
                self.state = "stopped" if self.stopped.is_set() else "failed"
                self.latest = result
                session.error = result["message"]
        finally:
            with session.condition:
                session.operation = None
                session.operation_lock.release()
                session.condition.notify_all()

    def run(self):
        while not self.stopped.is_set():
            self.event.wait()
            self.event.clear()
            if not self.stopped.is_set():
                self.tick()

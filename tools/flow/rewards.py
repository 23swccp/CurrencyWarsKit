"""按住鼠标，沿奖励区遍历一轮。"""

from math import ceil, hypot
from time import sleep

from game.input import ERRORS, InputUncertain
from game.slots import REWARD_CORNERS, REWARD_START


def trace():
    points = [REWARD_CORNERS[0]]
    for (x0, y0), (x1, y1) in zip(REWARD_CORNERS, REWARD_CORNERS[1:]):
        steps = ceil(hypot(x1 - x0, y1 - y0) / 50)
        points.extend((round(x0 + (x1 - x0) * step / steps), round(y0 + (y1 - y0) * step / steps))
                      for step in range(1, steps + 1))
    return tuple(points)


TRACE = trace()


def wait_input(job, name):
    # 防：鼠标任务失败。
    if not job.wait().succeeded:
        raise RuntimeError(f"奖励收集{name}失败")


def sweep(controller):
    try:
        wait_input(controller.post_touch_down(*REWARD_START), "按下")
        for x, y in TRACE:
            wait_input(controller.post_touch_move(x, y), f"移动至 ({x}, {y})")
            sleep(0.02)
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

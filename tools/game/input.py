"""MaaFramework 基础输入：坐标检查、发送动作与输入后的结果判定。"""

KEYS = {"escape": 0x1B, "enter": 0x0D, "space": 0x20, "tab": 0x09,
        "backspace": 0x08, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
KEYS.update({chr(code).lower(): code for code in range(0x41, 0x5B)})
KEYS.update({chr(code): code for code in range(0x30, 0x3A)})

# 输入已发出后可能出现的错误；各动作统一按待核对处理。
ERRORS = (OSError, RuntimeError, ValueError, ModuleNotFoundError)


class InputUncertain(RuntimeError):
    pass


def uncertain(error, message):
    """输入已发出后出错：保留异常类型与上下文，交给 AI 核对现场。

    传入的 error 也可以是 InputUncertain，此时再补一层动作说明和截图。
    """
    return {"result": "uncertain", "screen": "未知", "needs_ai": True,
            "message": f"{message}：{type(error).__name__}: {error}"}


def points_in_image(image_size, *points):
    # 防：尚未截图，坐标没有宽高依据。
    if image_size is None:
        raise ValueError("没有可用的截图尺寸；请先运行 screenshot")
    # 防：坐标超出最近一次截图范围。
    if any(not (0 <= x < image_size[0] and 0 <= y < image_size[1]) for x, y in points):
        raise ValueError("输入坐标超出截图范围，未发送输入")


def execute(name, action, capture):
    try:
        if not action().wait().succeeded:
            raise InputUncertain(f"{name}任务失败，输入结果不确定；重新观察后再决定是否重发")
        return capture()
    except InputUncertain:
        raise
    # 防：输入已发出，后续截图因无效图像数据抛 ValueError。
    except ERRORS as error:
        raise InputUncertain(f"{name}或后续截图出错，输入结果不确定；重新观察：{type(error).__name__}: {error}") from error


def click(controller, image_size, capture, x, y):
    points_in_image(image_size, (x, y))
    return execute("点击", lambda: controller.post_click(x, y), capture)


def drag(controller, image_size, capture, x1, y1, x2, y2, duration):
    points_in_image(image_size, (x1, y1), (x2, y2))
    return execute("拖动", lambda: controller.post_swipe(x1, y1, x2, y2, duration), capture)


def move(controller, image_size, capture, source, target, positions):
    # 防：槽位坐标只按 1920×1080 截图确定。
    if image_size != (1920, 1080):
        raise ValueError("槽位拖动仅支持 1920×1080 截图")
    result = drag(controller, image_size, capture, *positions[source], *positions[target], 700)
    return {**result, "result": "uncertain", "action": "move", "from": source, "to": target,
            "message": "已发送槽位拖动；请检查后续画面确认移动或交换结果"}


def press(controller, capture, key):
    return execute("按键", lambda: controller.post_click_key(KEYS[key]), capture)

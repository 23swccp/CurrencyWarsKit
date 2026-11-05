"""MaaFramework 基础输入：坐标检查、发送动作与输入后截图。"""

KEYS = {"escape": 0x1B, "enter": 0x0D, "space": 0x20, "tab": 0x09,
        "backspace": 0x08, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
KEYS.update({chr(code).lower(): code for code in range(0x41, 0x5B)})
KEYS.update({chr(code): code for code in range(0x30, 0x3A)})


class InputUncertain(RuntimeError):
    pass


def points_in_image(image_size, *points):
    # 防：点击或拖动坐标超出生成当前令牌的截图范围。
    if any(not (0 <= x < image_size[0] and 0 <= y < image_size[1]) for x, y in points):
        raise ValueError("输入坐标超出截图范围，未发送输入")


def execute(name, action, capture):
    try:
        if not action().wait().succeeded:
            raise InputUncertain(f"{name}任务失败，输入结果不确定；请重新观察，不要盲目重发")
        return capture()
    except InputUncertain:
        raise
    except (OSError, RuntimeError, ModuleNotFoundError) as error:
        raise InputUncertain(f"{name}或后续截图出错，输入结果不确定；请重新观察：{type(error).__name__}: {error}") from error


def click(controller, image_size, capture, x, y):
    points_in_image(image_size, (x, y))
    return execute("点击", lambda: controller.post_click(x, y), capture)


def drag(controller, image_size, capture, x1, y1, x2, y2, duration):
    points_in_image(image_size, (x1, y1), (x2, y2))
    return execute("拖动", lambda: controller.post_swipe(x1, y1, x2, y2, duration), capture)


def press(controller, capture, key):
    return execute("按键", lambda: controller.post_click_key(KEYS[key]), capture)

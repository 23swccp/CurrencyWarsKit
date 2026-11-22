"""幕后茶歇的页面识别、委托选择与结算。"""

import re
from time import monotonic, sleep

from maa.tasker import Tasker

from game.input import ERRORS, InputUncertain
from recognition_debug import focus


SCREEN = "幕后茶歇"
ROIS = {
    "entry": (1732, 711, 173, 82),
    "hud": (795, 31, 80, 60),
    "menu": (891, 137, 140, 52),
    "reward": (843, 161, 235, 69),
    "settlement": (843, 161, 235, 69),
    "back": (1130, 787, 95, 65),
    "bubble": (1207, 275, 68, 534),
    "prompt": (1407, 897, 190, 45),
    "option_left": (243, 800, 79, 66),
    "option_right": (1389, 800, 79, 66),
    "claim": (911, 951, 105, 67),
    "boss": (908, 367, 195, 76),
    "boss_prompt": (851, 757, 220, 72),
}
MENU = (
    ("option_left", (386, 837), (200, 620, 375, 58), (198, 670, 378, 137)),
    ("option_right", (1534, 837), (1348, 620, 375, 58), (1346, 670, 378, 137)),
)
ROWS = (446, 608)
GOLD = (1035, 63, 55, 34)
HEALTH = (959, 60, 69, 41)
NODE = (870, 40, 73, 49)
DIALOGUE = (1250, 291, 590, 494)
ADVANCE = (1500, 1000)
INTERVAL = 0.25
BOARD_SETTLE = 2.5
MIN_SCORE = 0.88


def compact(text):
    return re.sub(r"\s+", "", text)


def matched(reader, image, mark, roi=None):
    return reader.score(image, f"tea_{mark}.png", roi or ROIS[mark]) >= MIN_SCORE


def stage(reader, image):
    if not matched(reader, image, "hud"):
        return None
    for mark in ("reward", "settlement", "menu"):
        if matched(reader, image, mark):
            return mark
    if matched(reader, image, "back"):
        return "commission"
    if matched(reader, image, "bubble") or matched(reader, image, "prompt"):
        return "dialogue"
    return "transition"


def text(reader, image, label, roi):
    return compact(reader.text(image, label, roi))


def confirm_area(index):
    return (1699, ROWS[index] + 60, 120, 53)


def selected(reader, image):
    return next((index for index in range(len(ROWS))
                 if matched(reader, image, "confirm", confirm_area(index))), None)


def read_menu(reader, image):
    options = []
    for index, (_, _, title_area, description_area) in enumerate(MENU, 1):
        name = text(reader, image, f"茶歇选项{index}", title_area)
        description = text(reader, image, f"茶歇选项{index}说明", description_area)
        recovery = re.search(r"回复(\d+)小队生命值", description)
        option = {"slot": index, "title": name, "description": description}
        if recovery:
            option["health_recovery"] = int(recovery[1])
        if not name or not description:
            option["unknown"] = True
        options.append(option)
    return options


def read_commissions(reader, image):
    options = []
    for index, y in enumerate(ROWS, 1):
        title = compact(reader.enlarged_text(image, f"委托{index}名称", (1140, y - 2, 150, 45)))
        details = text(reader, image, f"委托{index}内容", (1139, y + 50, 541, 75))
        match = re.fullmatch(r"委托内容[:：](.+?)委托奖励[:：](.+?)[×xX](\d+)", details)
        option = {"slot": index, "title": title, "text": details}
        if title and match:
            goal, reward, count = match.groups()
            # 防：第二级委托的「Ⅱ」在录像帧上被 OCR 读成感叹号。
            if title == "跌跌撞撞！" and goal == "累计不完美通关3次。" and reward == "5费聘用书":
                title = "跌跌撞撞Ⅱ"
            option.update(title=re.sub(r"II$", "Ⅱ", title),
                          goal=goal.rstrip("。"), reward={"text": reward, "count": int(count)})
        else:
            option["unknown"] = True
        options.append(option)
    return options


def read_settlement(reader, image):
    items = []
    for y in (276, 328, 380):
        label = text(reader, image, "茶歇结算奖励", (737, y, 115, 37))
        if not label:
            continue
        label = re.sub(r"^(利息|连胜).*", r"\1", label)
        amount = reader.integer(image, label, (1145, y, 41, 37))
        items.append({"label": label, "gold": amount})
    return items


def read(reader, image, current):
    reader.load_ocr()
    node = text(reader, image, "茶歇节点", NODE)
    result = {"result": "success", "screen": SCREEN, "stage": current,
              "node": node if re.fullmatch(r"\d+-\d+", node) else None,
              "health": reader.integer(image, "茶歇生命值", HEALTH),
              "gold": reader.integer(image, "茶歇金币", GOLD),
              "needs_ai": current in {"menu", "commission"}}
    if current in {"menu", "commission"}:
        options = read_menu(reader, image) if current == "menu" else read_commissions(reader, image)
        result["options"] = options
        if current == "commission":
            index = selected(reader, image)
            result["selected"] = index + 1 if index is not None else None
        if any("unknown" in option for option in options):
            result["result"] = "uncertain"
    elif current == "settlement":
        result["items"] = read_settlement(reader, image)
    return result


class TeaBreak:
    def __init__(self, session, image):
        self.session = session
        self.reader = session.preparation_reader()
        self.image = image
        self.tasker = None
        self.clicked = False
        self.current = None
        self.evidence = {}

    def capture(self):
        self.image = self.session.preparation_frame()
        self.current = None

    def stage(self):
        self.current = stage(self.reader, self.image)
        return self.current

    def click(self, task, sources, point, *, arrivals=(), confirm_roi=None):
        if self.tasker is None:
            self.tasker = Tasker()
            if not self.tasker.bind(self.reader.resource, self.session.controller) or not self.tasker.inited:
                raise RuntimeError("幕后茶歇 Pipeline 初始化失败")
        pipeline = {}

        def template(mark):
            roi = confirm_roi if mark == "confirm" else ROIS[mark]
            return {"type": "TemplateMatch", "param": {"template": [f"tea_{mark}.png"],
                    "roi": list(roi), "threshold": [MIN_SCORE], "method": 5}}

        def gate(prefix, marks):
            if len(marks) == 1:
                return template(marks[0])
            names = []
            for mark in marks:
                name = f"{prefix}_{mark}"
                pipeline[name] = {"recognition": template(mark)}
                names.append(name)
            return {"type": "And", "param": {"all_of": names, "box_index": 0}}

        node = {"recognition": gate(task, sources),
                "action": {"type": "Click", "param": {"target": [*point, 1, 1]}},
                "post_delay": 300, "timeout": 6000}
        pipeline[task] = node
        if arrivals:
            node["next"] = []
            for index, marks in enumerate(arrivals, 1):
                arrival = f"{task}Arrived{index}"
                node["next"].append(arrival)
                pipeline[arrival] = {"recognition": gate(arrival, marks)}
        self.clicked = True
        job = self.tasker.post_task(task, pipeline).wait()
        self.capture()
        # 防：点击已提交或到达识别超时，保留最近帧和节点详情。
        if not job.succeeded:
            detail = job.get()
            nodes = [] if detail is None else [(node.name, node.completed) for node in detail.nodes]
            self.stage()
            raise InputUncertain(f"任务 {task} 未完成，节点 {nodes}")

    def follow_choice(self):
        self.reader.load_ocr()
        deadline = monotonic() + 12
        last_line_count = 0
        while monotonic() < deadline:
            current = self.stage()
            if current == "commission":
                return read(self.reader, self.image, current)
            if current == "reward":
                return self.claim()
            if current == "dialogue" and matched(self.reader, self.image, "prompt"):
                line_count = len(self.reader.text_boxes(self.image, "茶歇对白", DIALOGUE))
                # 防：继续提示在相邻对白之间仍可见，等新对白再推进。
                if line_count > last_line_count:
                    last_line_count = line_count
                    self.click("TeaAdvance", ("hud", "prompt"), ADVANCE)
                    continue
            sleep(INTERVAL)
            self.capture()
        raise InputUncertain("茶歇选择后的界面待确认")

    def choose(self, index, option):
        button, point, _, _ = MENU[index]
        self.evidence["choice"] = option["title"]
        self.click("TeaChoose", ("menu", button), point,
                   arrivals=(("hud", "reward"), ("hud", "back"), ("hud", "bubble"), ("hud", "prompt")))
        return self.follow_choice()

    def commission(self, index, before):
        other = before["selected"]
        if other is not None and other != index + 1:
            return {**before, "result": "uncertain", "message": "当前选中的是另一项委托"}
        roi = confirm_area(index)
        if other is None:
            self.click("TeaSelectCommission", ("hud", "back"), (1410, ROWS[index] + 70),
                       arrivals=(("hud", "back", "confirm"),), confirm_roi=roi)
        focus(self.image, roi, "茶歇委托确认")
        self.click("TeaConfirmCommission", ("hud", "back", "confirm"), (1760, ROWS[index] + 90),
                   arrivals=(("reward",),), confirm_roi=roi)
        self.evidence["commission"] = before["options"][index]
        return self.claim()

    def claim(self):
        before = read(self.reader, self.image, "reward")
        # 防：金币漏读，无法核对本次领取的到账额。
        if before["gold"] is None:
            return {**before, "result": "uncertain", "needs_ai": True, "message": "领奖前金币待确认"}
        self.click("TeaClaim", ("reward", "claim"), (960, 982), arrivals=(("settlement",),))
        return self.settle(before["gold"], before["node"])

    def settle(self, gold_before=None, node=None):
        deadline = monotonic() + 3
        while self.stage() == "settlement":
            result = read(self.reader, self.image, "settlement")
            self.evidence["settlement"] = {"gold_before": gold_before, "gold_after": result["gold"],
                                           "items": result["items"]}
            items = result["items"]
            if items and result["gold"] is not None and all(item["gold"] is not None for item in items):
                total = sum(item["gold"] for item in items)
                if gold_before is None or result["gold"] - gold_before == total:
                    self.evidence["settlement"].update(total=total, verified=gold_before is not None)
                    return self.wait_board(node or result["node"])
            if monotonic() >= deadline:
                break
            sleep(INTERVAL)
            self.capture()
        raise InputUncertain("茶歇金币结算待核对")

    def wait_board(self, node):
        from screens.preparation import layout

        deadline = monotonic() + 12
        board_since = None
        dismissed = False
        while monotonic() < deadline:
            if matched(self.reader, self.image, "boss"):
                board_since = None
                if not dismissed and matched(self.reader, self.image, "boss_prompt"):
                    self.click("TeaDismissBoss", ("boss", "boss_prompt"), ADVANCE)
                    dismissed = True
                    continue
            else:
                board = layout(self.image, self.reader)
                if board and board["node"] is not None and board["node"] != node:
                    now = monotonic()
                    board_since = now if board_since is None else board_since
                    # 录像中 2-7 棋盘先出现，随后才显示强敌提示。
                    if dismissed or now - board_since >= BOARD_SETTLE:
                        return {**board, "stage": "complete"}
                else:
                    board_since = None
            sleep(INTERVAL)
            self.capture()
        raise InputUncertain("茶歇结束后的备战界面待确认")

    def execute(self, action, option=None, current=None):
        try:
            result = self.perform(action, option, current)
        except InputUncertain as error:
            result = {"result": "uncertain", "screen": SCREEN, "stage": self.current or "transition",
                      "needs_ai": True, "message": str(error)}
        except ERRORS as error:
            if not self.clicked:
                raise
            result = {"result": "uncertain", "screen": SCREEN, "stage": self.current or "transition",
                      "needs_ai": True, "message": f"茶歇操作待核对：{type(error).__name__}: {error}"}
        if self.evidence:
            result["tea_break"] = self.evidence
        return result

    def perform(self, action, option, current):
        self.current = current if current is not None else self.stage()
        if action == "advance":
            match self.current:
                case "entry":
                    self.click("TeaOpen", ("entry",), (1813, 750),
                               arrivals=(("menu", "option_left", "option_right"),))
                    return read(self.reader, self.image, "menu")
                case "dialogue":
                    return self.follow_choice()
                case "reward":
                    return self.claim()
                case "settlement":
                    return self.settle()
            return {"result": "failed", "screen": SCREEN if self.current else "未知",
                    "stage": self.current, "needs_ai": True, "message": "当前茶歇阶段无法自动推进"}
        expected = "menu" if action == "choose" else "commission"
        if self.current != expected:
            return {"result": "failed", "screen": SCREEN if self.current else "未知",
                    "stage": self.current, "needs_ai": True, "message": "茶歇操作要求对应的选择界面"}
        before = read(self.reader, self.image, self.current)
        index = option - 1
        # 防：目标选项文字漏读时，无法核对将要执行的选择。
        selected_option = before["options"][index]
        if "unknown" in selected_option:
            return {**before, "message": "茶歇选项待确认"}
        return self.choose(index, selected_option) if action == "choose" else self.commission(index, before)

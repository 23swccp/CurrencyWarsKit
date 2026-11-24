"""备战席武装箱、四选一面板与到账核对。"""

from pathlib import Path
from time import monotonic, sleep

import numpy as np
from PIL import Image
from maa.pipeline import JRecognitionType, JTemplateMatch
from maa.resource import Resource
from maa.tasker import Tasker

from game.input import ERRORS, InputUncertain, uncertain
from game.slots import BENCH
from recognition_debug import focus
from screens.inventory import BOTH_COLUMNS


TITLES = {"简易武装箱": "armament.png", "进阶武装箱": "armament_advanced.png",
          "阿哈·简易武装箱": "armament_aha.png"}
TITLE = (940, 35, 175, 50)
OPEN = "armament_open.png"
TITLE_SCORE = 0.84
OPEN_SCORE = 0.80
RECEIPT_SCORE = 0.78
ICON_LEFTS = (1739, 1818)
CENTERS = (593, 883, 1173, 1464)


def panel(reader, image):
    scores = {name: reader.score(image, template, TITLE) for name, template in TITLES.items()}
    title = max(scores, key=scores.get)
    return title if scores[title] >= TITLE_SCORE else None


def label_roi(slot):
    x, _ = BENCH[slot]
    return (x-37, 922, 74, 51)


def is_box(reader, image, slot):
    return reader.score(image, OPEN, label_roi(slot)) >= OPEN_SCORE


def icon(image, center):
    roi = (center-42, 185, 84, 84)
    focus(image, roi, "武装箱选项图标", "crop")
    return image[185:269, center-42:center+42]


def source_slot(image):
    # 两段录像中选中箱均有白色横边，悬停时卡片还会上移。
    focus(image, (382, 833, 1115, 19), "武装箱来源白框", "crop")
    selected = []
    for slot, (x, _) in BENCH.items():
        white = image[833:852, x-49:x+50].min(axis=2) > 215
        if white.sum(axis=1).max() >= 84:
            selected.append(slot)
    return selected[0] if len(selected) == 1 else None


def read(reader, image, title):
    reader.load_ocr()
    instruction = reader.text(image, "武装箱选择数量", (968, 82, 125, 31))
    options = []
    for i, x in enumerate(CENTERS, 1):
        name = reader.text(image, f"武装箱选项{i}", (x-86, 315, 172, 40)).strip()
        item = reader.inventory.equipment.match(icon(image, x), min_score=0.76, margin=0.08)
        options.append({"slot": f"option_{i}", "name": name, "equipment": item})
    complete = "".join(instruction.split()) == "请选择1个" and all(item["name"] for item in options)
    return {"result": "success" if complete else "uncertain", "screen": "武装箱选择", "title": title,
            "source": source_slot(image), "options": options, "instruction": instruction, "needs_ai": True}


def match_node(templates, roi, threshold):
    return {"type": "TemplateMatch", "param": {"roi": list(roi), "template": templates,
                                              "threshold": [threshold] * len(templates), "method": 5}}


class Armament:
    def __init__(self, controller):
        self.resource = Resource()
        for name in (*TITLES.values(), OPEN):
            with Image.open(Path(__file__).resolve().parents[2] / "asset" / "preparation" / name) as src:
                self.resource.override_image(name, np.asarray(src.convert("RGB"))[:, :, ::-1].copy())
        self.tasker = Tasker()
        if not self.tasker.bind(self.resource, controller) or not self.tasker.inited:
            raise RuntimeError("武装箱 Pipeline 初始化失败")

    def click(self, task, recognition, point, following=None):
        entry = {"recognition": recognition, "action": {"type": "Click", "param": {"target": [*point, 1, 1]}},
                 "post_delay": 350}
        pipeline = {task: entry}
        if following:
            entry.update(next=["ArmamentPanel"], timeout=5000)
            pipeline["ArmamentPanel"] = {"recognition": following, "post_delay": 350}
        job = self.tasker.post_task(task, pipeline).wait()
        # 防：点击或到达识别失败时保留实际执行节点。
        if not job.succeeded:
            detail = job.get()
            nodes = [(n.name, n.completed) for n in detail.nodes] if detail else None
            raise InputUncertain(f"{task} 未完成，节点 {nodes}")

    def count(self, image):
        focus(image, BOTH_COLUMNS, "武装箱所选装备库存数量")
        param = JTemplateMatch(template=["chosen.png"], roi=BOTH_COLUMNS, threshold=[RECEIPT_SCORE], method=5)
        detail = self.tasker.post_recognition(JRecognitionType.TemplateMatch, param, image).wait().get()
        if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
            raise RuntimeError("武装箱库存匹配未返回识别详情")
        # 防：鼠标悬停会在两列之间绘制所选装备的副本。
        hits = detail.nodes[0].recognition.filtered_results
        return sum(any(abs(hit.box[0] - left) <= 4 for left in ICON_LEFTS) for hit in hits)

    def execute(self, session, action, value):
        reader = session.preparation_reader()
        image = session.preparation_frame()
        # 防：开箱与选项点击坐标只适用于 1920×1080。
        if image.shape[:2] != (1080, 1920):
            raise ValueError("武装箱操作要求 1920×1080 截图")
        if action == "open":
            scene = session.preparation_scene(image)
            if scene["screen"] != "备战" or scene["shop"] != "closed" or not is_box(reader, image, value):
                return {**session.save_image(image), **scene, "result": "failed", "message": "指定备战槽未确认可开箱"}
            task = "ArmamentOpen"
            recognition = match_node([OPEN], label_roi(value), OPEN_SCORE)
            point = BENCH[value]
            following = match_node(list(TITLES.values()), TITLE, TITLE_SCORE)
        else:
            title = panel(reader, image)
            if not title:
                return {**session.save_image(image), "result": "failed", "message": "当前未确认武装箱面板"}
            before = read(reader, image, title)
            slot = before["source"]
            # 防：面板动画中选项或来源白框未读全。
            if before["result"] != "success" or slot is None:
                return {**session.save_image(image), **before, "result": "uncertain", "message": "选项或箱子来源待确认"}
            x = CENTERS[value-1]
            # 同一图标从卡面缩到库存尺寸，去掉边框与左下角叠字。
            small = np.asarray(Image.fromarray(icon(image, x)).resize((69, 69), Image.Resampling.BILINEAR))
            self.resource.override_image("chosen.png", small[5:54, 7:62].copy())
            count = self.count(image)
            task = "ArmamentChoose"
            recognition = match_node([TITLES[title]], TITLE, TITLE_SCORE)
            point, following = (x, 283), None

        try:
            try:
                self.click(task, recognition, point, following)
            except InputUncertain as error:
                image = session.preparation_frame()
                return {**session.save_image(image), **uncertain(error, "武装箱操作待核对"),
                        "action": "armament-" + action}
            deadline = monotonic() + 5
            while True:
                image = session.preparation_frame()
                title = panel(reader, image)
                if action == "open":
                    result = read(reader, image, title) if title else {"result": "uncertain", "screen": "未知"}
                    if result["result"] == "success" and result["source"] == value:
                        break
                    result.update(result="uncertain", needs_ai=True, message="开箱后来源或选项待确认")
                else:
                    result = {"result": "uncertain", "screen": "武装箱选择" if title else "未知",
                              "needs_ai": True, "source": slot, "option": value,
                              "name": before["options"][value-1]["name"], "count_before": count,
                              "message": "箱子消耗或装备到账待确认"}
                    if not title and reader.page(image)["result"] == "success":
                        empty = reader.empty(image, slot)
                        after = self.count(image)
                        result.update(screen="备战", source_empty=empty, count_after=after)
                        if empty and after == count + 1:
                            result.update(result="success", needs_ai=False, message="箱子已消耗，装备已到账")
                            break
                if monotonic() >= deadline:
                    break
                sleep(0.3)
            return {**session.save_image(image), **result, "action": "armament-" + action}
        except ERRORS as error:
            raise InputUncertain(f"{task} 结果待核对：{type(error).__name__}: {error}") from error

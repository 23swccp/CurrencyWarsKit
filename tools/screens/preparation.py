"""备战页面、角色与数值识别。"""

from concurrent.futures import ThreadPoolExecutor, wait
import json
import re
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image
from maa.pipeline import JOCR, JRecognitionType, JTemplateMatch
from maa.resource import Resource
from maa.tasker import Tasker

from ocr import ImageController, MODEL
from recognition_debug import focus
from screens.inventory import Inventory
from game.actions import BUTTON_ACTIONS
from game.slots import (
    ACTION_BUTTON, BENCH_WARNING, CARDS, CARD_SIZE, EMPTY, HEADER, ICONS,
    NODE, NODE_OPEN, NUMBERS, PORTRAIT, PHASE, PHASE_OPEN, SHOP, SHOP_OPEN,
)


ASSETS = Path(__file__).resolve().parents[2] / "asset"
SCALES = (0.94, 0.98, 1.02, 1.06)
ASPECTS = (0.9, 1.0)
WORKERS = 16
MIN_SCORE = 0.8
MIN_MARGIN = 0.1


def parse_numbers(texts):
    patterns = {"gold": r"([0-9]+)", "level": r"Lv\.([0-9]+)",
                "experience": r"([0-9]+)/([0-9]+)", "experience_cost": r"([0-9]+)",
                "deployment": r"([0-9]+)/([0-9]+)"}
    # 防：数值漏读或格式不符。
    matches = {name: re.fullmatch(pattern, re.sub(r"\s+", "", texts[name]))
               for name, pattern in patterns.items()}
    result = {name: int(matches[name][1]) if matches[name] else None
              for name in ("gold", "level", "experience_cost")}
    for name, limit in (("experience", "required"), ("deployment", "limit")):
        match = matches[name]
        result[name] = {"current": int(match[1]) if match else None,
                        limit: int(match[2]) if match else None}
    result["result"] = "success" if all(matches.values()) else "uncertain"
    if result["result"] == "uncertain":
        result.update(needs_ai=True, message="备战数值待确认")
    return result


def inside(box, region):
    x, y, width, height = box
    left, top, region_width, region_height = region
    return left <= x and top <= y and x + width <= left + region_width and y + height <= top + region_height


def parse_header(texts):
    for layout, title_region, node_region in (("closed", PHASE, NODE), ("open", PHASE_OPEN, NODE_OPEN)):
        # 防：中央过场也显示「备战阶段」，只接受顶部固定标题位置内的文字框。
        title = next((item for item in texts if re.sub(r"\s+", "", item["text"]) == "备战阶段"
                      and inside(item["box"], title_region)), None)
        if title is not None:
            # 防：节点漏读、格式或位置不符时返回 null，不沿用上一帧节点。
            node = next((re.sub(r"\s+", "", item["text"]) for item in texts
                         if inside(item["box"], node_region)
                         and re.fullmatch(r"\d+-\d+", re.sub(r"\s+", "", item["text"]))), None)
            return {"layout": layout, "phase_box": title["box"], "node": node}
    return {"layout": None, "phase_box": None, "node": None}


def page(image, reader):
    if image.shape[:2] != (1080, 1920):
        raise ValueError("备战识别要求 1920×1080 截图")
    header = reader.header(image)
    progress = {"node": header["node"], "phase_box": header["phase_box"]}
    if header["layout"] != "closed":
        return {"result": "uncertain", "screen": "未知", **progress, "message": "备战标题文字或位置未确认"}
    shop = reader.score(image, "shop.png", SHOP)
    anchors = {}
    anchors["shop"] = round(shop, 3)
    if shop < 0.9:
        icons = reader.score(image, "icons.png", ICONS)
        anchors["icons"] = round(icons, 3)
        if icons < 0.9:
            return {"result": "uncertain", "screen": "未知", **progress, "message": "备战辅助标志匹配未通过",
                    "anchors": anchors}
    return {"result": "success", "screen": "备战", **progress}


def layout(image, reader):
    result = page(image, reader)
    if result["result"] == "success":
        return {**result, "shop": "closed"}
    header = reader.header(image)
    if header["layout"] == "open" and reader.score(image, "shop_open.png", SHOP_OPEN) >= 0.9:
        return {"result": "success", "screen": "备战", "shop": "open",
                "node": header["node"], "phase_box": header["phase_box"]}
    return None


class Recognition:
    def __init__(self, controller):
        self.resource = Resource()
        self.tasker = Tasker()
        # 防：识别任务绑定失败。
        if not self.tasker.bind(self.resource, controller):
            raise RuntimeError("备战识别初始化失败")

    def score(self, image, template, roi=(0, 0, 0, 0)):
        focus(image, roi, template)
        detail = self.tasker.post_recognition(JRecognitionType.TemplateMatch,
            JTemplateMatch(template=[template], roi=roi, threshold=[0.0], method=5), image).wait().get()
        # 防：模板识别任务出错。
        if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
            raise RuntimeError(f"备战模板识别出错：{template}")
        best = detail.nodes[0].recognition.best_result
        return float(best.score) if best is not None else 0.0

    def compare(self, patch, portraits):
        self.resource.override_image("portrait.png", patch)
        return [(max(self.score(variant, "portrait.png") for variant in variants), name)
                for name, variants in portraits]


class Preparation(Recognition):
    def __init__(self):
        self.controller = ImageController(np.zeros((1, 1, 3), dtype="uint8"))
        # 防：图片控制器连接失败。
        if not self.controller.post_connection().wait().succeeded:
            raise RuntimeError("备战图片控制器连接失败")
        super().__init__(self.controller)
        self.ocr_ready = False
        self.button_image = None
        self.button_result = None
        self.header_image = None
        self.header_result = None
        self.inventory = Inventory()
        for name in ("shop", "icons", "shop_open", "sell", "armament", "bench_warning", *EMPTY):
            with Image.open(ASSETS / "preparation" / f"{name}.png") as image:
                self.resource.override_image(f"{name}.png", np.asarray(image.convert("RGB"))[:, :, ::-1].copy())
        index = json.loads((ASSETS / "character" / "index.json").read_text(encoding="utf-8"))
        self.portraits = []
        for character in index["characters"]:
            with Image.open(ASSETS / "character" / character["file"]) as source:
                variants = [np.asarray(source.convert("RGB").resize(
                    (round(source.width * scale * aspect / 2), round(source.height * scale / 2)),
                    Image.Resampling.LANCZOS))[:, :, ::-1].copy()
                    for scale in SCALES for aspect in ASPECTS]
            self.portraits.append((character["name"], variants))
        self.workers = [(Recognition(self.controller), self.portraits[index::WORKERS]) for index in range(WORKERS)]
        self.pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="portrait")

    def close(self):
        self.pool.shutdown(wait=True)

    def match(self, image):
        patch = np.asarray(Image.fromarray(image).resize((34, 27), Image.Resampling.LANCZOS)).copy()
        futures = [self.pool.submit(worker.compare, patch, portraits) for worker, portraits in self.workers]
        # 每张卡完成后复用识别上下文。
        wait(futures)
        scores = [score for future in futures for score in future.result()]
        scores.sort(reverse=True)
        candidates = [{"name": name, "score": round(score, 3)} for score, name in scores[:2]]
        # 防：库外图标或相似头像分数接近。
        if scores[0][0] < MIN_SCORE or scores[0][0] - scores[1][0] < MIN_MARGIN:
            return {"unknown": True, "candidates": candidates}
        return {"name": scores[0][1], "score": candidates[0]["score"], "candidates": candidates}

    def page(self, image):
        return page(image, self)

    def recognize(self, image):
        page = self.page(image)
        if page["result"] != "success":
            return page
        return {**page, **self.observe(image)}

    def observe(self, image):
        started = perf_counter()
        numbers = self.numbers(image)
        cards = self.slots(image)
        capacity = self.capacity_warning(image)
        if not capacity and all(cards["slots"][f"bench_{i}"] is not None for i in range(1, 10)):
            capacity = {"reason": "bench_full", "needs_ai": True}
        return {**cards, **numbers, **capacity, "inventory": self.inventory.read(image),
                **self.action_button(image), "node": self.header(image)["node"],
                "recognition_ms": round((perf_counter() - started) * 1000, 1)}

    def capacity_warning(self, image):
        # 防：游戏提示处理容量，交给 AI 售卖角色。
        if self.score(image, "bench_warning.png", BENCH_WARNING) >= 0.9:
            return {"reason": "character_overflow", "needs_ai": True, "required_action": "sell",
                    "message": "角色卡溢出，请 AI 选择并售卖角色"}
        return {}

    def text_boxes(self, image, name, roi):
        focus(image, roi, name, "ocr")
        detail = self.tasker.post_recognition(JRecognitionType.OCR, JOCR(roi=roi), image).wait().get()
        # 防：OCR 任务详情缺失。
        if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
            raise RuntimeError(f"备战 OCR 详情缺失：{name} {roi}")
        lines = sorted(detail.nodes[0].recognition.filtered_results, key=lambda item: (item.box[1], item.box[0]))
        return [{"text": item.text, "box": list(item.box)} for item in lines]

    def text(self, image, name, roi):
        return "".join(item["text"] for item in self.text_boxes(image, name, roi))

    def header(self, image):
        if image is not self.header_image:
            self.load_ocr()
            result = parse_header(self.text_boxes(image, "备战标题与节点", HEADER))
            self.header_image, self.header_result = image, result
        return self.header_result

    def numbers(self, image):
        self.load_ocr()
        return parse_numbers({name: self.text(image, name, roi) for name, roi in NUMBERS.items()})

    def load_ocr(self):
        if not self.ocr_ready:
            # 防：OCR 模型加载失败。
            if not self.resource.use_cpu() or not self.resource.post_ocr_model(MODEL).wait().succeeded:
                raise RuntimeError(f"备战 OCR 模型加载失败：{MODEL}")
            self.ocr_ready = True

    def integer(self, image, name, roi):
        self.load_ocr()
        text = re.sub(r"\s+", "", self.text(image, name, roi))
        # 防：金币或售价漏读。
        return int(text) if re.fullmatch(r"[0-9]+", text) else None

    def action_button(self, image):
        if image is not self.button_image:
            self.load_ocr()
            text = re.sub(r"\s+", "", self.text(image, "右侧行动按钮", ACTION_BUTTON))
            # 防：buff 改变按钮文字，未收录的名称保留原文，不当成禁止操作。
            result = {"button_text": text, "next_action": BUTTON_ACTIONS.get(text)}
            self.button_image, self.button_result = image, result
        return self.button_result

    def empty(self, image, slot):
        x, y, width, height = EMPTY[slot]
        return self.score(image, f"{slot}.png", (x-4, y-4, width+8, height+8)) >= 0.9

    def card(self, image, slot):
        if self.empty(image, slot):
            return None
        quad = CARDS[slot]
        xs, ys = quad[::2], quad[1::2]
        focus(image, (min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)), f"{slot} 卡片裁剪", "crop")
        card = Image.fromarray(image).transform(CARD_SIZE, Image.Transform.QUAD, quad, Image.Resampling.BICUBIC)
        return self.match(np.asarray(card.crop(PORTRAIT)).copy())

    def slots(self, image):
        slots = {slot: self.card(image, slot) for slot in CARDS}
        return {"slots": slots, "candidate_count": len(self.portraits)}

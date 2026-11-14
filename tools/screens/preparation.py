"""备战页面与固定槽位的角色识别。"""

from concurrent.futures import ThreadPoolExecutor, wait
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image
from maa.pipeline import JRecognitionType, JTemplateMatch
from maa.resource import Resource
from maa.tasker import Tasker

from ocr import ImageController
from game.slots import CARDS, CARD_SIZE, EMPTY, ICONS, PORTRAIT, PHASE, PHASE_OPEN, SHOP, SHOP_OPEN


ASSETS = Path(__file__).resolve().parents[2] / "asset"
SCALES = (0.94, 0.98, 1.02, 1.06)
ASPECTS = (0.9, 1.0)
WORKERS = 16
MIN_SCORE = 0.8
MIN_MARGIN = 0.1


def page(image, score):
    if image.shape[:2] != (1080, 1920):
        raise ValueError("备战识别要求 1920×1080 截图")
    phase = score(image, "phase.png", PHASE)
    anchors = {"phase": round(phase, 3)}
    if phase < 0.7:
        return {"result": "uncertain", "screen": "未知", "message": "备战页面匹配未通过",
                "anchors": anchors}
    shop = score(image, "shop.png", SHOP)
    anchors["shop"] = round(shop, 3)
    if shop < 0.9:
        icons = score(image, "icons.png", ICONS)
        anchors["icons"] = round(icons, 3)
        if icons < 0.9:
            return {"result": "uncertain", "screen": "未知", "message": "备战页面匹配未通过",
                    "anchors": anchors}
    return {"result": "success", "screen": "备战"}


def layout(image, score):
    result = page(image, score)
    if result["result"] == "success":
        return {**result, "shop": "closed"}
    if (score(image, "phase_open.png", PHASE_OPEN) >= 0.9
            and score(image, "shop_open.png", SHOP_OPEN) >= 0.9):
        return {"result": "success", "screen": "备战", "shop": "open"}
    return None


class Recognition:
    def __init__(self, controller):
        self.resource = Resource()
        self.tasker = Tasker()
        # 防：识别任务绑定失败。
        if not self.tasker.bind(self.resource, controller):
            raise RuntimeError("备战识别初始化失败")

    def score(self, image, template, roi=(0, 0, 0, 0)):
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
        for name in ("phase", "shop", "icons", "phase_open", "shop_open", *EMPTY):
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
        return page(image, self.score)

    def recognize(self, image):
        page = self.page(image)
        if page["result"] != "success":
            return page
        return {**page, **self.slots(image)}

    def slots(self, image):
        started = perf_counter()
        slots = {}
        source = Image.fromarray(image)
        for slot, quad in CARDS.items():
            x, y, width, height = EMPTY[slot]
            empty_score = self.score(image, f"{slot}.png", (x - 4, y - 4, width + 8, height + 8))
            if empty_score >= 0.9:
                slots[slot] = None
            else:
                card = source.transform(CARD_SIZE, Image.Transform.QUAD, quad, Image.Resampling.BICUBIC)
                slots[slot] = self.match(np.asarray(card.crop(PORTRAIT)).copy())
        return {"slots": slots,
                "candidate_count": len(self.portraits), "recognition_ms": round((perf_counter() - started) * 1000, 1)}

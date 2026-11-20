"""识别上阵角色卡下方的已穿戴星徽。"""

import numpy as np
from PIL import Image
from maa.pipeline import JRecognitionType, JTemplateMatch

from game.equipment import ASSETS
from game.slots import CARDS_BY_BACKSTAGE
from recognition_debug import focus


SIDE = 22
MIN_SCORE = 0.82
MIN_MARGIN = 0.08
def vector(image):
    pixels = image.reshape(-1, 3).astype(np.float32)
    return (pixels - pixels.mean(axis=0)).ravel()


class Emblems:
    def __init__(self, resource, tasker, asset_ids):
        self.tasker = tasker
        self.ids = tuple(asset_ids)
        self.templates = [f"worn_{asset_id}.png" for asset_id in self.ids]
        vectors = []
        for asset_id, name in zip(self.ids, self.templates):
            with Image.open(ASSETS / f"{asset_id}.png") as source:
                w, h = source.size
                crop = source.convert("RGB").crop((round(w * .16), round(h * .16),
                                                 round(w * .84), round(h * .84)))
                image = np.asarray(crop.resize((SIDE, SIDE), Image.Resampling.BILINEAR))[:, :, ::-1].copy()
            resource.override_image(name, image)
            vectors.append(vector(image))
        matrix = np.stack(vectors)
        self.vectors = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    def read(self, image, slots):
        result = {}
        cards = CARDS_BY_BACKSTAGE[7 if "back_7" in slots else 6]
        for area in ("front_", "back_"):
            occupied = {slot: (cards[slot][2], cards[slot][4], cards[slot][3])
                        for slot, card in slots.items() if slot.startswith(area) and card is not None}
            if not occupied:
                continue
            left = min(box[0] for box in occupied.values())
            right = max(box[1] for box in occupied.values())
            bottom = next(iter(occupied.values()))[2]
            roi = (left, bottom - 15, right - left, 62)
            focus(image, roi, "已穿戴星徽")
            job = self.tasker.post_recognition(JRecognitionType.TemplateMatch,
                JTemplateMatch(template=self.templates, roi=roi, threshold=[MIN_SCORE], method=5), image).wait()
            detail = job.get()
            # 防：星徽识别任务没有返回详情，与没找到星徽区分。
            if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
                raise RuntimeError(f"已穿戴星徽识别详情缺失：{roi}")
            for hit in detail.nodes[0].recognition.filtered_results:
                x, y, w, h = hit.box
                center = x + w / 2
                slot = next((name for name, (lo, hi, _) in occupied.items() if lo <= center < hi), None)
                # 防：整条窄带也包含卡片间隙及未上阵位置。
                if slot is None:
                    continue
                patch = vector(image[y:y+h, x:x+w])
                scores = self.vectors @ (patch / np.linalg.norm(patch))
                second, best = np.argsort(scores)[-2:]
                # 防：相似星徽竞争同一位置，分差不足时不计入。
                if scores[best] - scores[second] >= MIN_MARGIN:
                    result.setdefault(slot, set()).add(self.ids[best])
        return {slot: sorted(ids) for slot, ids in result.items()}

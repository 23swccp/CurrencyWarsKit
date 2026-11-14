"""投资环境：读取三张卡片，并比较卡片内的装备图标。"""

from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(__file__).resolve().parents[2] / "asset" / "equipment"
CARDS = ((215, 380, 450, 465), (735, 380, 450, 465), (1255, 380, 450, 465))


class Equipment:
    def __init__(self):
        self.paths = sorted(ASSETS.glob("*.png"))
        vectors = np.stack([np.asarray(Image.open(path).convert("RGB").resize((60, 60)),
                                       dtype=np.float32).ravel() for path in self.paths])
        vectors -= vectors.mean(axis=1, keepdims=True)
        self.vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def match(self, image):
        vector = np.asarray(Image.fromarray(image[:, :, ::-1]).resize((60, 60)), dtype=np.float32).ravel()
        vector -= vector.mean()
        norm = np.linalg.norm(vector)
        # 防：图标区域无像素变化时分数无定义，返回 unknown。
        if norm == 0:
            return {"unknown": True, "candidates": []}
        scores = self.vectors @ (vector / norm)
        best = np.argsort(scores)[-2:][::-1]
        candidates = [{"id": self.paths[index].stem, "score": round(float(scores[index]), 3)} for index in best]
        # 防：武装箱等库外图标、相似候选分数接近时返回 unknown。
        if scores[best[0]] < 0.8 or scores[best[0]] - scores[best[1]] < 0.1:
            return {"unknown": True, "candidates": candidates}
        path = self.paths[best[0]]
        return {"id": path.stem, "asset": str(path.resolve()), "score": candidates[0]["score"]}


def recognize(image, read, equipment):
    """read 接收同一张截图的区域，返回 OCR 文字及位置。"""
    options = []
    for index, (x, y, width, height) in enumerate(CARDS, 1):
        # 整张卡片 OCR 曾将「简易武装箱」误读为「简易装备」，描述单独识别。
        lines = read(image, (x, y, width, height))
        lines = [item for item in lines if not 415 <= item["box"][1] < 600]
        lines += read(image, (x + 20, 415, width - 40, 180))
        lines.sort(key=lambda item: (item["box"][1], item["box"][0]))
        texts = [item["text"] for item in lines]
        option = {"slot": f"option_{index}", "texts": texts}
        # 防：卡片文字不足两行（名称或描述漏读）时标记 unknown。
        if len(texts) < 2:
            option["unknown"] = True
        if "装备" in texts:
            # 当前样例每张卡片的装备区域只有中央一格，其他布局待样例验证。
            icon_x = (410, 930, 1450)[index - 1]
            option["equipment"] = [equipment.match(image[780:845, icon_x:icon_x + 65])]
        options.append(option)
    return {"screen": "投资环境", "options": options, "needs_ai": True}

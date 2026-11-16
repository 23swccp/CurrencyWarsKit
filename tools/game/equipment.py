"""装备素材加载与图标匹配。"""

from pathlib import Path

import numpy as np
from PIL import Image


ASSETS = Path(__file__).resolve().parents[2] / "asset" / "equipment"


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
        # 防：图标区域无像素变化。
        if norm == 0:
            return {"unknown": True, "candidates": []}
        scores = self.vectors @ (vector / norm)
        best = np.argsort(scores)[-2:][::-1]
        candidates = [{"id": self.paths[index].stem, "score": round(float(scores[index]), 3)} for index in best]
        # 防：库外图标或相似候选分数接近。
        if scores[best[0]] < 0.8 or scores[best[0]] - scores[best[1]] < 0.1:
            return {"unknown": True, "candidates": candidates}
        path = self.paths[best[0]]
        return {"id": path.stem, "asset": str(path), "score": candidates[0]["score"]}

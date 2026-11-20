"""备战右侧库存的方框定位与物品识别。"""

import numpy as np

from game.equipment import Equipment
from recognition_debug import focus


AREA = (1804, 200, 86, 510)
BOTH_COLUMNS = (1725, 200, 165, 510)
ROW_PITCH = 77
INSET = 2


def frames(image, x0):
    _, y0, width, height = AREA
    area = image[y0:y0+height, x0:x0+width].astype(np.int16)
    brightness = area.mean(axis=2)
    gray = (np.ptp(area, axis=2) < 45) & (brightness > 100) & (brightness < 220)
    blue, green, red = area.transpose(2, 0, 1)
    gold = (red > 155) & (green > 140) & (red > blue + 28) & (green > blue)
    border = gray | gold
    rows = np.pad(border.cumsum(axis=1), ((0, 0), (1, 0)))
    columns = np.pad(border.cumsum(axis=0), ((1, 0), (0, 0)))
    candidates = []
    for w in range(70, 77):
        horizontal = (rows[:, w:] - rows[:, :-w]) / w
        for h in range(70, 77):
            vertical = (columns[h:] - columns[:-h]) / h
            ny, nx = height-h+1, width-w+1
            coverage = np.minimum.reduce((horizontal[:ny], horizontal[h-1:],
                                          vertical[:, :nx], vertical[:, w-1:]))
            ys, xs = np.where(coverage >= 0.85)
            candidates.extend((int(x+x0), int(y+y0), w, h, float(coverage[y, x]))
                              for y, x in zip(ys, xs))
    selected = []
    for box in sorted(candidates, key=lambda b: (b[4], b[2]*b[3]), reverse=True):
        if all(abs(box[1]-other[1]) > 35 for other in selected):
            selected.append(box)
    return [box[:4] for box in sorted(selected, key=lambda b: b[1])]


class Inventory:
    def __init__(self):
        self.equipment = Equipment()

    def read(self, image):
        focus(image, BOTH_COLUMNS, "右侧库存定位", "crop")
        boxes = frames(image, AREA[0]) + frames(image, BOTH_COLUMNS[0])
        # 防：库存方框未定位。
        if not boxes:
            return None
        top = min(box[1] for box in boxes)
        slots = {}
        for x, y, width, height in boxes:
            row = round((y-top) / ROW_PITCH) + 1
            column = 1 if x >= AREA[0] else 2
            slot = f"bag_r{row}_c{column}"
            roi = (x+INSET, y+INSET, width-2*INSET, height-2*INSET)
            focus(image, roi, slot, "crop")
            patch = image[y+INSET:y+height-INSET, x+INSET:x+width-INSET]
            slots[slot] = {**self.equipment.match(patch), "box": [x, y, width, height]}
        return slots

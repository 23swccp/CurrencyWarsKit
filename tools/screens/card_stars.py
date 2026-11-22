"""按星标亮核读取角色卡星级。"""

from math import ceil, floor

import numpy as np

from game.slots import CARD_SIZE
from recognition_debug import focus


STAR_BAND = (18, 87, 102, 126)
NEIGHBORS = ((-1, -1), (-1, 0), (-1, 1), (0, -1),
             (0, 1), (1, -1), (1, 0), (1, 1))


def _band_box(quad):
    width, height = CARD_SIZE
    left, top, right, bottom = STAR_BAND
    top_left, card_top, bottom_left, card_bottom, bottom_right, _, top_right, _ = quad
    xs = []
    for y in (top / height, bottom / height):
        start = top_left + (bottom_left - top_left) * y
        end = top_right + (bottom_right - top_right) * y
        xs.extend(start + (end - start) * x / width for x in (left, right))
    card_height = card_bottom - card_top
    x = floor(min(xs))
    y = floor(card_top + card_height * top / height)
    return x, y, ceil(max(xs)) - x, ceil(card_top + card_height * bottom / height) - y


def _cores(mask):
    ys, xs = np.nonzero(mask)
    remaining = set(zip(xs.tolist(), ys.tolist()))
    centers = []
    while remaining:
        pixels = [remaining.pop()]
        for x, y in pixels:
            for dx, dy in NEIGHBORS:
                neighbor = (x + dx, y + dy)
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pixels.append(neighbor)
        xx, yy = zip(*pixels)
        area = len(pixels)
        width = max(xx) - min(xx) + 1
        height = max(yy) - min(yy) + 1
        if 21 <= area <= 140 and 5 <= width <= 20 and 5 <= height <= 18:
            centers.append((sum(xx) / area, sum(yy) / area))
    return sorted(centers)


def row_count(mask, *, minimum=1):
    centers = _cores(mask)
    if not minimum <= len(centers) <= 4:
        return None
    xx, yy = zip(*centers)
    # 防：立绘高光和金边可能通过颜色筛选，星标应居中成行且间距在限定范围内。
    if abs((xx[0] + xx[-1]) / 2 - mask.shape[1] / 2) > 10 or max(yy) - min(yy) > 5:
        return None
    if all(10 <= b - a <= 24 for a, b in zip(xx, xx[1:])):
        return len(centers)
    return None


def read(image, card, quad, slot):
    focus(image, _band_box(quad), f"{slot} 星级", "crop")
    # 备战截图使用 BGR，卡片裁正保留原通道顺序。
    blue, green, red = np.asarray(card.crop(STAR_BAND), dtype=np.int16).transpose(2, 0, 1)
    gold = (red > 232) & (green > 183) & (green - blue > 17)
    cyan = (blue > 228) & (green > 202) & (blue - red > 35)
    counts = [count for mask in (gold, cyan) if (count := row_count(mask)) is not None]
    # 防：异色高光形成第二组候选时，无法唯一确定该卡的星级。
    return counts[0] if len(counts) == 1 else None

"""1920×1080 商店收起时的备战槽位中心坐标。"""

CENTERS = {
    "front": ((746, 398), (888, 398), (1031, 398), (1176, 398)),
    "back": ((605, 670), (746, 670), (889, 670), (1032, 670), (1175, 670), (1318, 670)),
    "bench": tuple((438 + 125 * index, 905) for index in range(9)),
}

SLOTS = {f"{area}_{index}": point for area, points in CENTERS.items()
         for index, point in enumerate(points, 1)}

# 卡片四角按左上、左下、右下、右上排列。
CARDS = {}
for index in range(4):
    top, bottom = 690 + 140 * index, 678 + 146 * index
    CARDS[f"front_{index + 1}"] = (top, 341, bottom, 470, bottom + 126, 470, top + 120, 341)
for index in range(6):
    top, bottom = 550 + 140 * index, 532 + 146 * index
    CARDS[f"back_{index + 1}"] = (top, 598, bottom, 731, bottom + 129, 731, top + 123, 598)
for index in range(9):
    left = 382 + 125 * index
    CARDS[f"bench_{index + 1}"] = (left, 845, left, 979, left + 114, 979, left + 114, 845)

CARD_SIZE = (120, 130)
PORTRAIT = (14, 38, 82, 92)
EMPTY = {slot: (x - 17, y - 25, 34, 50) for slot, (x, y) in SLOTS.items()}

PHASE = (430, 35, 80, 23)
SHOP = (1520, 800, 220, 260)
ICONS = (1540, 0, 380, 110)
PHASE_OPEN = (235, 50, 110, 45)
SHOP_OPEN = (1575, 950, 110, 65)
SHOP_TOGGLE = (1622, 980)
BLANK = (1910, 1070)

REWARD_START = (12, 12)
REWARD_CORNERS = (
    (1577, 148), (1302, 148), (1302, 192), (1572, 191),
    (1581, 236), (1309, 238), (1314, 286), (1592, 286),
    (1601, 320), (1318, 318), (1322, 354), (1610, 354),
    (1616, 390), (1328, 388), (1332, 423), (1602, 424),
    (1564, 460), (1336, 460),
)

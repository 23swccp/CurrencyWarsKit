"""1920×1080 商店收起时的备战布局。"""

FRONT = {f"front_{index}": (x, 398) for index, x in enumerate((746, 888, 1031, 1176), 1)}
BENCH = {f"bench_{index + 1}": (438 + 125 * index, 905) for index in range(9)}
BACK_X = {6: (605, 746, 889, 1032, 1175, 1318),
          7: (534, 675, 817, 960, 1103, 1246, 1389)}
SLOTS_BY_BACKSTAGE = {
    count: {**FRONT, **{f"back_{index}": (x, 670) for index, x in enumerate(xs, 1)}, **BENCH}
    for count, xs in BACK_X.items()
}
SLOTS = SLOTS_BY_BACKSTAGE[6]

# 卡片四角按左上、左下、右下、右上排列。
FRONT_CARDS, BENCH_CARDS = {}, {}
for index in range(4):
    top, bottom = 690 + 140 * index, 678 + 146 * index
    FRONT_CARDS[f"front_{index + 1}"] = (top, 341, bottom, 470, bottom + 126, 470, top + 120, 341)
for index in range(9):
    left = 382 + 125 * index
    BENCH_CARDS[f"bench_{index + 1}"] = (left, 845, left, 979, left + 114, 979, left + 114, 845)

BACK_SIX = [(550 + 140 * index, 532 + 146 * index) for index in range(6)]
BACK_SEVEN = [(481 + 139 * index, bottom)
              for index, bottom in enumerate((459, 604, 749, 894, 1039, 1185, 1330))]
CARDS_BY_BACKSTAGE = {}
for count, corners in ((6, BACK_SIX), (7, BACK_SEVEN)):
    back = {f"back_{index}": (top, 598, bottom, 731, bottom + 129, 731, top + 123, 598)
            for index, (top, bottom) in enumerate(corners, 1)}
    CARDS_BY_BACKSTAGE[count] = {**FRONT_CARDS, **back, **BENCH_CARDS}
CARDS = CARDS_BY_BACKSTAGE[6]

CARD_SIZE = (120, 130)
PORTRAIT = (14, 38, 82, 92)

HEADER = (220, 20, 320, 115)
PHASE = (424, 28, 96, 32)
NODE = (425, 58, 90, 42)
SHOP = (1520, 800, 220, 260)
ICONS = (1540, 0, 380, 110)
PHASE_OPEN = (235, 50, 110, 40)
NODE_OPEN = (245, 88, 95, 42)
SHOP_OPEN = (1575, 950, 110, 65)
SHOP_TOGGLE = (1622, 980)
BLANK = (1910, 1070)
ACTION_BUTTON = (1700, 710, 210, 90)
BENCH_WARNING = (775, 515, 400, 40)

NUMBERS = {
    "gold": (1625, 897, 66, 48),
    "level": (255, 893, 80, 43),
    "experience": (250, 935, 96, 33),
    "experience_cost": (285, 970, 52, 44),
    "deployment": (890, 225, 150, 75),
}

HEALTH = (1430, 60, 50, 35)

REWARD_START = (12, 12)
REWARD_CORNERS = (
    (1577, 148), (1302, 148), (1302, 192), (1572, 191),
    (1581, 236), (1309, 238), (1314, 286), (1592, 286),
    (1601, 320), (1318, 318), (1322, 354), (1610, 354),
    (1616, 390), (1328, 388), (1332, 423), (1602, 424),
    (1564, 460), (1336, 460),
)

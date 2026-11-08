"""1920×1080 商店收起时的备战槽位中心坐标。"""

CENTERS = {
    "front": ((746, 398), (888, 398), (1031, 398), (1176, 398)),
    "back": ((605, 670), (746, 670), (889, 670), (1032, 670), (1175, 670), (1318, 670)),
    "bench": tuple((438 + 125 * index, 905) for index in range(9)),
}

SLOTS = {f"{area}_{index}": point for area, points in CENTERS.items()
         for index, point in enumerate(points, 1)}

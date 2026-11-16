"""投资环境：读取三张卡片，并比较卡片内的装备图标。"""

from recognition_debug import focus

CARDS = ((215, 380, 450, 465), (735, 380, 450, 465), (1255, 380, 450, 465))


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
            focus(image, (icon_x, 780, 65, 65), f"option_{index} 装备裁剪", "crop")
            option["equipment"] = [equipment.match(image[780:845, icon_x:icon_x + 65])]
        options.append(option)
    return {"screen": "投资环境", "options": options, "needs_ai": True}

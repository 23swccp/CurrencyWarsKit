"""备战顶部特殊道具及其选择面板。"""

from time import monotonic, sleep

from maa.pipeline import JRecognitionType, JTemplateMatch

from game.input import ERRORS, InputUncertain, uncertain
from game.slots import SLOTS_BY_BACKSTAGE
from screens.preparation import backstage_size


ITEMS = {"privilege": "特权赋予卡", "luck": "好运令牌"}
CENTERS = {"privilege": (878, 1180), "luck": (593, 883, 1173, 1464)}
TITLE_ROI = (930, 20, 210, 70)
TOP_ROI = (1570, 145, 320, 80)
TIMEOUT = 5


def locate(reader, image, name, roi, threshold=0.84):
    detail = reader.tasker.post_recognition(JRecognitionType.TemplateMatch,
        JTemplateMatch(template=[name], roi=roi, threshold=[threshold], method=5), image).wait().get()
    # 防：MaaFramework 没有返回模板识别详情。
    if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
        raise RuntimeError(f"特殊道具模板识别失败：{name}")
    return detail.nodes[0].recognition.best_result


def read_items(reader, image):
    result = {}
    for kind, name in ITEMS.items():
        hit = locate(reader, image, f"special_{kind}_icon.png", TOP_ROI)
        if hit is not None:
            x, y, w, h = hit.box
            result[kind] = {"name": name, "box": [x, y, w, h]}
    return result


def panel(reader, image):
    for kind in ITEMS:
        if reader.score(image, f"special_{kind}_title.png", TITLE_ROI) >= 0.84:
            return kind
    return None


def read(reader, image, kind):
    reader.load_ocr()
    centers = CENTERS[kind]
    y = 315 if kind == "privilege" else 294
    icon_y = 185 if kind == "privilege" else 162
    options = [{"slot": f"option_{i}", "name": reader.text(image, "特殊道具选项", (x-115, y, 230, 42)).strip(),
                "equipment": reader.inventory.equipment.match(image[icon_y:icon_y+84, x-42:x+42],
                                                               min_score=0.76, margin=0.08)}
               for i, x in enumerate(centers, 1)]
    complete = all(option["name"] for option in options)
    return {"result": "success" if complete else "uncertain", "screen": "特殊道具选择",
            "item": kind, "title": ITEMS[kind], "options": options, "needs_ai": True}


def named_equipment(result):
    return any(option["name"] and "id" in option["equipment"] for option in result["options"])


def save(session, image, result):
    return {**session.save_image(image), **result}


def send(job, name):
    if not job.wait().succeeded:
        raise InputUncertain(f"{name}任务未完成")


def use(session, kind, target):
    reader = session.preparation_reader()
    image = session.preparation_frame()
    scene = session.preparation_scene(image)
    # 防：非备战或商店展开时，道具和角色坐标不适用。
    if scene["screen"] != "备战" or scene["shop"] != "closed":
        return save(session, image, {**scene, "result": "failed", "message": "使用道具要求商店收起的备战画面"})
    items = read_items(reader, image)
    # 防：指定道具不在顶部已识别的图标中。
    if kind not in items:
        return save(session, image, {"result": "uncertain", "needs_ai": True, "message": "未确认指定道具"})
    count = backstage_size(image) if target.startswith("back_") else 6
    # 防：后台布局未确认或当前布局没有指定槽位。
    if count is None or target not in SLOTS_BY_BACKSTAGE[count]:
        return save(session, image, {"result": "uncertain", "needs_ai": True, "message": "目标槽位布局待确认"})
    card = reader.card(image, target, count)
    # 防：拖动目标为空位或角色未识别。
    if card is None or "name" not in card:
        return save(session, image, {"result": "uncertain", "needs_ai": True, "message": "目标角色待确认"})
    x, y, w, h = items[kind]["box"]
    try:
        send(session.controller.post_swipe(x+w//2, y+h//2, *SLOTS_BY_BACKSTAGE[count][target], 800), "道具拖动")
        deadline = monotonic() + TIMEOUT
        while True:
            sleep(0.3)
            image = session.preparation_frame()
            found = panel(reader, image)
            result = read(reader, image, kind)
            # 拖动后可由标题或选项名称确认弹窗；选项不全时仍需核对。
            if found == kind or named_equipment(result):
                session.special_item = kind
                return save(session, image, {**result, "target": target})
            if monotonic() >= deadline:
                return save(session, image, {"result": "uncertain", "needs_ai": True, "message": "道具选择面板待确认"})
    except ERRORS as error:
        return save(session, session.preparation_frame(), uncertain(error, "道具操作待核对"))


def choose(session, option):
    reader = session.preparation_reader()
    image = session.preparation_frame()
    found = panel(reader, image)
    kind = found or session.special_item
    # 防：没有识别到标题且没有本次使用道具的上下文。
    if kind is None:
        return save(session, image, {"result": "failed", "message": "未确认特殊道具面板"})
    before = read(reader, image, kind)
    # 防：好运令牌名称区域在备战页会读到「前台区」。
    if not found and not named_equipment(before):
        return save(session, image, {"result": "uncertain", "needs_ai": True, "message": "特殊道具面板待确认"})
    # 防：选项未读全或选择编号超出实际面板。
    if before["result"] != "success" or option > len(before["options"]):
        return save(session, image, {**before, "result": "uncertain", "message": "选项待确认"})
    x = CENTERS[kind][option-1]
    try:
        send(session.controller.post_click(x, 235), "选择道具选项")
        deadline = monotonic() + TIMEOUT
        if kind == "privilege":
            while True:
                sleep(0.3)
                image = session.preparation_frame()
                # 防：选项点击尚未生效；选中卡片顶部出现白边。
                band = image[130:138, x-100:x+100]
                selected = (band.min(axis=2) > 225).sum(axis=1).max() >= 100
                # 防：选中后确认按钮文字未识别。
                if selected and "确认选择" in reader.text(image, "确认选择", (1286, 489, 380, 58)):
                    break
                if monotonic() >= deadline:
                    return save(session, image, {"result": "uncertain", "needs_ai": True, "message": "选中状态待确认"})
            send(session.controller.post_click(1475, 518), "确认道具选项")
            deadline = monotonic() + TIMEOUT
        while True:
            sleep(0.3)
            image = session.preparation_frame()
            scene = reader.page(image)
            if scene["result"] == "success":
                session.special_item = None
                inventory = reader.inventory.read(image)
                return save(session, image, {"result": "success" if inventory is not None else "uncertain",
                    "screen": "备战", "item": kind, "option": option, "name": before["options"][option-1]["name"],
                    "inventory": inventory, "special_items": read_items(reader, image),
                    "message": "已返回备战并重新读取装备栏"})
            if monotonic() >= deadline:
                return save(session, image, {"result": "uncertain", "needs_ai": True, "message": "选择后画面待确认"})
    except ERRORS as error:
        return save(session, session.preparation_frame(), uncertain(error, "道具选择待核对"))

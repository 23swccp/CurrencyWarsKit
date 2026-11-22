"""根据同一张截图只读观察当前游戏界面。"""

from flow.preparation_scene import identify, read_choice
from screens import armament
from screens.shop import complete as shop_complete, read_cards, read_refresh


def observe(session):
    image = session.preparation_frame()
    result = inspect(session, image)
    return {**session.save_image(image), **result}


def inspect(session, image):
    scene = identify(session, image)
    if scene is None:
        result = session.battle_reader().recognize(image)
        if result["screen"] == "未知":
            result["needs_ai"] = True
        return result
    result = read_choice(session, image, scene)
    if result["screen"] != "备战":
        if result["screen"] in {"幕后茶歇", "选择主角"} and result["stage"] == "entry":
            result = {**result, **session.preparation_reader().observe(image)}
        if result["screen"] == "选择主角" and result["stage"] == "entry":
            preparation = session.preparation_reader()
            result["armament_slots"] = [slot for slot in armament.BENCH if armament.is_box(preparation, image, slot)]
        return result

    preparation = session.preparation_reader()
    if scene["shop"] == "closed":
        return {**scene, **preparation.observe(image)}

    result = {**scene, **read_refresh(image, preparation), **read_cards(image, preparation)}
    # 防：商店已展开，但 OCR 未读全刷新信息。
    if not shop_complete(result):
        result.update(result="uncertain", needs_ai=True, message="商店刷新信息待确认")
    return result

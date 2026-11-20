"""按同一优先级识别备战布局和选择界面。"""

from screens import armament, trail_shop
from screens.encounter import complete as encounter_complete
from screens.preparation import layout
from screens.protagonist import observation as protagonist_observation
from screens.strategy import complete as strategy_complete
from screens.supply import complete as supply_complete


def identify(session, image):
    preparation = session.preparation_reader()
    # 防：选择界面可能保留底下的备战标题和按钮，优先检查专用界面。
    if title := armament.panel(preparation, image):
        return {"result": "success", "screen": "武装箱选择", "title": title, "needs_ai": True}
    if trail_shop.page(preparation, image):
        return {"result": "success", "screen": trail_shop.SCREEN, "needs_ai": True}
    if session.protagonist_reader().selection_page(image):
        return {"result": "success", "screen": "选择主角", "stage": "selection", "needs_ai": True}
    if session.supply_reader().page(image):
        return {"result": "success", "screen": "补给", "needs_ai": True}
    if session.encounter_reader().page(image):
        return {"result": "success", "screen": "遭遇", "needs_ai": True}
    if session.strategy_reader().page(image):
        return {"result": "success", "screen": "投资策略", "needs_ai": True}
    return layout(image, preparation)


def read_choice(session, image, scene):
    match scene["screen"]:
        case "武装箱选择":
            return armament.read(session.preparation_reader(), image, scene["title"])
        case trail_shop.SCREEN:
            return trail_shop.read(session.preparation_reader(), image)
        case "选择主角":
            return protagonist_observation(session.protagonist_reader(), image, scene["stage"])
        case "补给":
            reader, complete = session.supply_reader(), supply_complete
        case "遭遇":
            reader, complete = session.encounter_reader(), encounter_complete
        case "投资策略":
            reader, complete = session.strategy_reader(), strategy_complete
        case "备战":
            if scene["shop"] == "closed":
                preparation = session.preparation_reader()
                action = preparation.action_button(image)
                if action["next_action"] == "protagonist-open":
                    return {"result": "success", "screen": "选择主角", "stage": "entry", "needs_ai": True,
                            **action, "node": scene["node"]}
            return scene
    result = reader.read(image)
    return {**result, "result": "success" if complete(result) else "uncertain"}

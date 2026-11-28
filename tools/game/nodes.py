"""用户提供的位面节点参考路线。"""

FIRST = (("奖励",), ("奖励",), ("战斗",), ("战斗",), ("补给",), ("战斗",),
         ("遭遇",), ("奖励", "幕后茶歇"), ("首领",))
SECOND = (("战斗",), ("投资策略",), ("补给",), ("战斗",), ("遭遇",),
          ("奖励", "幕后茶歇"), ("首领",))
ROUTES = {1: FIRST, 2: SECOND, 3: SECOND}


def reference(plane, index):
    route = ROUTES.get(plane)
    # 防：OCR 节点号超出示例路线或位面未知时，不推算节点类型。
    if route is None or not 1 <= index <= len(route):
        return None
    return {"source": "user_example", "nodes": [
        {"node": f"{plane}-{position}", "possible_types": list(types)}
        for position, types in enumerate(route, 1) if position >= index
    ]}

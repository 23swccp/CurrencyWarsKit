# 右侧行动按钮

已知文字与动作的对应表在 `tools/game/actions.py` 的 `BUTTON_ACTIONS`，观察返回 `button_text` 与 `next_action`。

| OCR 文字 | `next_action` | 分流 |
|---|---|---|
| 出战 | `start-battle` | 返回备战局势，等 AI 明确出战 |
| 选择主角 | `protagonist-open` | `prepare` 直接打开选择页；`preparation` 只读返回入口 |
| 其他非空文字 | `null` | 返回原文；明确的出战指令仍接受 |
| 空白 | `null` | 不推断动作 |

buff 会改变按钮文字，这张表只是分流提示，不是操作白名单。已知「选择主角」入口不按出战执行；主角确认后仍显示该入口时，本次选择不算完成。`next_action` 不触发自动出战或自动选择。

`Preparation.action_button(image)` 按截图对象复用一次 OCR：换帧重读，不跨截图推断。页面分流、局势观察、出战前检查与主角确认共用同一结果；OCR 出错不入缓存，按输入前后边界报告。`prepare` 已持有入口观察时直接传给打开动作，点击后仍等待并确认选择页。

入口识别与复用待实机验证。

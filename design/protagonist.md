# 选择主角

1-9、2-9 备战有此流程，实现在 `tools/screens/protagonist.py`。布局与坐标来自 `2026-11-14 12-08-58.mp4` 的 1-9 画面（1920×1080）。

## 命令

```powershell
.\.venv\Scripts\python.exe tools/cw.py observe
.\.venv\Scripts\python.exe tools/cw.py act protagonist-open
.\.venv\Scripts\python.exe tools/cw.py act protagonist-character --slot front_1
.\.venv\Scripts\python.exe tools/cw.py act protagonist-aura --option 1
.\.venv\Scripts\python.exe tools/cw.py act protagonist-refresh --option 2
.\.venv\Scripts\python.exe tools/cw.py act protagonist-confirm
```

`observe` 只读观察；`option` 从左到右为 1、2；角色使用本页的 `front_1`～`front_4`、`back_1`～`back_6`。各动作独立返回现场，由 AI 决定下一步，刷新只在明确指令下执行。

## 分流

```mermaid
flowchart TD
    Screen{"同帧确认备战布局或选择标题？"} -->|选择标题| Observe["读取角色、两侧光环、费用、共享次数和金币"]
    Screen -->|备战且右侧按钮为选择主角| Entry{"触发入口"}
    Entry -->|prepare 或 protagonist-open| Click["复用入口观察，点击一次"]
    Entry -->|observe| ReadEntry["只读返回入口"]
    Click -->|最多 3 次、间隔 2 秒确认| Observe
    Click -->|到期仍未识别| Pending["返回现场供核对"]
    Observe --> AI["同帧返回截图和 needs_ai"]
```

以「选择一个角色获得主角光环」识别选择页。备战入口使用[右侧按钮文字](actions.md)。节点编号用于报告进度。该分流优先于普通备战，识别后停止空白点击。

## 角色与光环

```mermaid
flowchart TD
    Order{"AI 发出哪项指令？"} -->|拖选角色| Drag["从已确认的本页槽位拖至中央"]
    Drag --> Verify["核对中央主角框，原角色卡留在原位"]
    Order -->|选择左或右光环| Select["同点点击两次，间隔 1 秒"]
    Select --> Wait["移开鼠标，等 2 秒并返回新画面"]
    Order -->|刷新某侧| Raw{"共享次数和金币足够？"}
    Raw -->|否| Reject["返回当前条件和现场"]
    Raw -->|是| Click["按当前画面定位，点击指定侧刷新一次"]
    Click --> VerifyRefresh["核对共享次数减少及扣款，返回两侧新内容"]
    Verify --> Confirm["角色和光环齐备后，底部确认按钮亮起"]
    Wait --> Confirm
```

角色选择页坐标随本模块维护，录像在 12.3～12.9 秒显示拖选动作。两侧光环观察返回名称、标签、可见效果、敌人难度变化与刷新费用。选中边框用于画面展示，程序通过确认按钮亮起和页面状态核对。只选角色时游戏提示「尚未选择主角光环」。悬停会放大卡片。

录像中每次刷新花 1 金币。右侧刷新后共享次数 6→5，金币 46→45。左侧刷新后次数 5→4，金币 45→44。

## 确认与恢复

```mermaid
flowchart TD
    Command["act protagonist-confirm"] --> Ready{"中央角色确认且确认按钮已亮？"}
    Ready -->|否| Pending["返回现场供 AI 核对"]
    Ready -->|是| Click["点击确认一次"]
    Click --> Board{"最多 3 次确认返回备战"}
    Board -->|已确认| Button{"同帧 OCR 右侧按钮"}
    Board -->|到期| Pending
    Button -->|非空且入口已变| Resume["续接本轮备战，从新帧读角色，保留已领奖进度"]
    Button -->|空白或仍为选择主角| Board
    Button -->|OCR 任务失败| Error["返回 uncertain、异常类型和最近截图"]
```

等待次数包含按钮文字确认，备战确认前不读取按钮。按钮名称会随 buff 改变，复用[右侧行动按钮](actions.md)的文字区域和模型。角色与空位复用已有头像库、候选阈值和工作线程。新判据待实机验证。

拖选、刷新和确认各执行一次，光环按两次点击执行。空位或角色待确认时返回对应槽位，刷新次数为零或金币不足时返回 `failed`。输入前的任务错误直接报告，输入后返回 `uncertain`、异常类型和最近截图。

## 录像样例

- 约 7 秒：角色与光环均未选，左「疾行派对客」、右「意志传承」，共享刷新 6 次。
- 约 13 秒：云璃已放入中央，光环未选。
- 约 17 秒：右侧光环出现选中边框，未刷新。
- 约 21 秒：右侧刷新为「连携追击」，剩 5 次、金币 45。
- 约 25 秒：左侧刷新为「双数成阵」，剩 4 次、金币 44，确认按钮亮起。
- 约 39 秒：返回 1-9 备战，右侧出现「出战」。

保存帧经 Session 入口回放覆盖读取、自动打开、拖选、单侧刷新、光环两次点击与最终确认，输入使用 Mock；随后恢复普通备战、收集一轮并返回 19 个槽位。记录见 `tools/.runtime/protagonist-video/session-replay.json`。

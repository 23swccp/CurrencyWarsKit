# 补给选择

依据用户 2026-11-16 提供的 1-5 补给截图与「刷新选项有时候没有」的说明，另取得 3-3 原始 1920×1080 画面并完成一次白厄选择。当前适配五列、单选补给。

## 命令

```powershell
.\.venv\Scripts\python.exe tools/cw.py observe
.\.venv\Scripts\python.exe tools/cw.py act supply --option 4
.\.venv\Scripts\python.exe tools/cw.py act supply-refresh
```

## 读取

以中央「补给阶段」标题识别，阈值 0.85（3-3 实机 0.889，备战与主角选择帧 0～0.196），先于备战布局判断；左上「备战阶段」区分不了此页。`prepare`、只读 `observe`、备战等待与后台补领都接入该页，命中后停止空白点击与收集。

从左到右返回 `option_1`～`option_5`，每项含 `character`、`equipment`、`trial`，另附 `selection.current/limit`、`can_refresh`、`refreshes_left` 与同帧截图。文字漏读保留已读内容并标 `unknown`，计数漏读为 `null`。

刷新是可选控件：没有按钮时 `can_refresh: false` 并省略次数；有按钮时读次数，漏读为 `null`。两种情况都不阻塞观察与选择。

## 刷新与单选确认

```mermaid
flowchart TD
    Page["确认补给页，读取五列和选择计数"] --> Intent{"收到哪条指令？"}
    Intent -->|单次刷新| Refresh{"刷新按钮和次数"}
    Refresh -->|无按钮或零次| Reject["返回 failed"]
    Refresh -->|次数未知| Pending["返回 uncertain 和最近截图"]
    Refresh -->|次数大于零| Click["点击刷新一次"]
    Click --> Changed{"0.3 秒轮询，8 秒内确认次数减一？"}
    Changed -->|是| Read["返回同帧全部选项"]
    Changed -->|未确认| Pending
    Intent -->|单选确认| Count{"读取到未选择的 0/1？"}
    Count -->|否| Pending
    Count -->|是| Pick["点击指定卡，等待计数 1/1"]
    Pick -->|已确认 1/1| Confirm["点击确认一次"]
    Pick -->|未确认| Pending
    Confirm --> Next{"确认下一页"}
    Next -->|备战| Resume["续接本轮 prepare.resume"]
    Next -->|其他已知选择| ReturnChoice["返回该页和 uncertain"]
    Next -->|当前页或未知| Pending
```

刷新后的名称可以相同。按钮消失时仍需核对次数变化。已有选中项和多选均返回现场，由 AI 处理。

3-3 实机：程序经 `0/1→1/1` 确认后进入 3-4，白厄在 `bench_4` 识别 0.918，天基轨道炮经截图确认到账（库存识别未返回该件）。刷新、试用角色落点与其他奖励情形待验证。

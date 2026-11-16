# 补给选择

依据用户 2026-11-16 提供的 1-5 补给截图与「刷新选项有时候没有」的说明，另取得 3-3 原始 1920×1080 画面并完成一次白厄选择。当前适配五列、单选补给。

## 命令

```powershell
.\.venv\Scripts\python.exe tools/cw.py supply
.\.venv\Scripts\python.exe tools/cw.py act supply --option 4
.\.venv\Scripts\python.exe tools/cw.py act supply-refresh
```

## 读取

以中央「补给阶段」标题识别，阈值 0.85（3-3 实机 0.889，备战与主角选择帧 0～0.196），先于备战布局判断；左上「备战阶段」区分不了此页。`prepare`、只读 `preparation`、备战等待与后台补领都接入该页，命中后停止空白点击与收集。

从左到右返回 `option_1`～`option_5`，每项含 `character`、`equipment`、`trial`，另附 `selection.current/limit`、`can_refresh`、`refreshes_left` 与同帧截图。文字漏读保留已读内容并标 `unknown`，计数漏读为 `null`。

刷新是可选控件：没有按钮时 `can_refresh: false` 并省略次数；有按钮时读次数，漏读为 `null`。两种情况都不阻塞观察与选择。

## 单次刷新

按钮存在且次数大于零才点击一次，随后每 0.3 秒确认次数减一，最多 8 秒，再读同帧选项，名称可与刷新前相同。没有按钮或次数为零返回 `failed`，次数未知返回 `uncertain`，三种情况都不发输入。按钮消失不等于次数归零，确认不到减少就返回 `uncertain`，不重发点击。

## 单选与确认

从 `0/1` 开始，点击 AI 指定卡片，计数变为 `1/1` 后点击确认一次，再确认后续页面。识别到备战即衔接本轮 `prepare.resume`；出现其他已知选择返回该页与 `uncertain`；仍在本页或未知时返回最后现场。已有选中项或多选返回 `uncertain`，保留现场，不自动取消或改选。

3-3 实机：程序经 `0/1→1/1` 确认后进入 3-4，白厄在 `bench_4` 识别 0.918，天基轨道炮经截图确认到账（库存识别未返回该件）。刷新、试用角色落点与其他奖励情形待验证。

<p align="center">
  <img src="asset/logo.png" alt="CurrencyWarsKit Logo" width="200">
</p>

<h1 align="center">CurrencyWarsKit</h1>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-0078D4" alt="Platform: Windows">
  <img src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&amp;logoColor=white" alt="Python: 3.12">
</p>

> **正在开发中**：功能、命令和数据格式可能调整。部分流程已通过实机或保存帧验证，完整对局与不同游戏状态的适配仍在进行中。

CurrencyWarsKit 是一个面向《崩坏：星穹铁道》「货币战争」玩法的 Windows 本地工具。它连接游戏窗口，提供截图、界面识别和可组合的命令，供外部 AI 或其他程序观察局势、做出决策并执行操作。

项目采用“AI 决策、程序执行”的方式：AI 根据局势选择阵容、投资策略和行动；CLI 负责识别、输入和必要的结果核对。它目前不是一个开箱即用的全自动通关工具，也不内置 AI 模型。

## 功能

- **观察局势**：读取当前界面、备战槽位、角色（含前台／后台定位）、金币、等级、生命值、商店货名与价格，以及可见装备等信息，并保存对应截图。
- **运营与选择**：提供商店购买、刷新、购买经验、角色移动，以及投资策略、补给、遭遇、主角和武装箱等操作。
- **战斗与结算**：确认出战、监测战斗、处理“继续挑战”，并提供最近一场战斗回执和整局结算命令。
- **基础操作**：支持按截图坐标点击、拖动和按键，用于处理尚未封装的界面。

识别结果和动作验证以返回数据与截图为准。未识别出的值可能为 `null`，需要选择或确认的情况会交回调用方。各流程的支持范围和待验证事项见[设计文档](design/README.md)。

## 环境要求

- Windows，已运行《崩坏：星穹铁道》游戏窗口。
- Python 3.12；项目当前使用 `maafw==5.14.3` 和 `Pillow==13.0.0`。
- 当前界面识别与坐标主要适配 **1920 × 1080** 游戏画面；其他分辨率和界面变化尚待验证。
- 如果游戏以管理员权限运行，控制服务也需要相应权限。服务由启动命令的进程创建，不会自动提权。

### 安装依赖

在项目目录中使用 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 准备 OCR 模型

识别功能需要在以下位置准备兼容 MaaFramework 的 OCR 模型：

```text
tools/.runtime/ocr-model/
├── det.onnx
├── rec.onnx
└── keys.txt
```

模型文件不包含在仓库中，模型来源与发布授权仍待确认；仅安装 Python 依赖还不能使用需要 OCR 的功能。

## 快速开始

先启动游戏并进入货币战争界面。在项目目录中执行：

```powershell
# 查看窗口，获取游戏窗口的 hwnd
.\.venv\Scripts\python.exe tools\cw.py windows

# 将 <hwnd> 替换为返回的窗口句柄，启动后台服务
.\.venv\Scripts\python.exe tools\cw.py start --hwnd <hwnd>

# 查看当前画面与识别结果
.\.venv\Scripts\python.exe tools\cw.py observe

# 查看服务状态
.\.venv\Scripts\python.exe tools\cw.py status

# 使用结束后停止服务
.\.venv\Scripts\python.exe tools\cw.py stop
```

命令输出 JSON。`observe` 只读当前画面；`prepare` 会进入备战流程并收集奖励，`act` 命令会向游戏发送输入。请根据当前界面和返回结果选择下一步操作。

截图保存在 `tools/.runtime/screenshots/`，服务日志和连接信息位于 `tools/.runtime/service/`。**每次服务成功启动时，截图目录会清空并从 `1.png` 重新编号**；需要保留的截图请先另存。

## 常用命令

```powershell
# 查看完整命令和动作参数
.\.venv\Scripts\python.exe tools\cw.py --help
.\.venv\Scripts\python.exe tools\cw.py act --help
.\.venv\Scripts\python.exe tools\cw.py act buy --help

# 备战与商店：先确认当前画面，再按需要执行
.\.venv\Scripts\python.exe tools\cw.py prepare
.\.venv\Scripts\python.exe tools\cw.py act shop-open
.\.venv\Scripts\python.exe tools\cw.py act buy --slot shop_3
.\.venv\Scripts\python.exe tools\cw.py act shop-close
.\.venv\Scripts\python.exe tools\cw.py act move --from bench_1 --to front_1

# 战斗及最近一场战斗回执
.\.venv\Scripts\python.exe tools\cw.py act start-battle
.\.venv\Scripts\python.exe tools\cw.py battle-result
```

`navigate` 会尝试进入标准博弈、复位最高职级并开始对局；它会实际操作游戏。战斗回执只保存在当前服务会话中，识别不出的胜负或生命值会保留为未知。最后一关的整局结算使用 `act settle`，详见[战斗阶段](design/battle.md)和[整局结算](design/settlement.md)。

## 工作方式

```text
外部 AI / 调用方
      │ CLI 命令与 JSON 结果
      ▼
本机服务 ── MaaFramework ── 游戏窗口
      │
      ├─ 截图、OCR、模板匹配
      ├─ 界面动作与结果核对
      └─ 战斗监测与奖励收集
```

CLI 负责参数和 JSON 输出；常驻服务复用窗口连接及识别资源，并串行执行游戏操作。服务与 CLI 之间使用本机 HTTP 通信；该协议是项目内部接口。

主要代码：

| 路径 | 内容 |
| --- | --- |
| `tools/cw.py` | 命令行入口 |
| `tools/service.py` | 本机服务、窗口连接和操作调度 |
| `tools/game/` | 槽位坐标、输入和游戏数据 |
| `tools/screens/` | 单界面识别与动作 |
| `tools/flow/` | 跨界面流程 |
| `asset/`、`data/` | 识别素材与游戏数据 |
| `test/` | 自动化测试与保存帧 |

## 开发与测试

```powershell
$env:PYTHONPATH = "$PWD\tools"
.\.venv\Scripts\python.exe -m unittest discover -s test -q
```

测试使用模拟输入，不向真实游戏窗口发送操作。项目正在开发，游戏版本、界面布局和识别素材变化都可能影响结果；设计及验证状态见对应的[设计页](design/README.md)。

## 许可证

本项目代码采用 [MIT License](LICENSE)。游戏画面、角色形象及第三方组件按各自的权利和许可使用。

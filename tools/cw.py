"""货币战争 CLI：命令解析、调用本机服务、输出 JSON。"""

import argparse
import json
import sys


VERSION = "0.3.0"


def output(data):
    print(json.dumps(data, ensure_ascii=False))


def window_handle(value):
    try:
        handle = int(value, 16 if value.lower().startswith("0x") else 10)
    except ValueError:
        raise argparse.ArgumentTypeError("窗口句柄必须是十进制或十六进制整数") from None
    if not 0 < handle < 2 ** 64:
        raise argparse.ArgumentTypeError("窗口句柄必须是非零的 64 位无符号整数")
    return handle


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="货币战争 CLI：窗口连接与常驻服务")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="查询后台服务状态")
    commands.add_parser("stop", help="等待当前操作结束，释放连接并停止服务")
    commands.add_parser("resync", help="重新截图")
    commands.add_parser("version", help="查看程序版本")
    echo = commands.add_parser("echo", help="原样返回测试文字，不执行游戏操作")
    echo.add_argument("text", help="要返回的测试文字")
    find = commands.add_parser("windows", help="通过 MaaFramework 列出窗口")
    find.add_argument("--title", default="", help="按窗口标题包含的文字筛选")
    start = commands.add_parser("start", help="在后台启动服务并连接指定窗口")
    start.add_argument("--hwnd", type=window_handle, required=True, help="windows 返回的窗口句柄")
    start.add_argument("--method", choices=["framepool", "screendc", "printwindow"],
                       default="framepool", help="截图方式；默认 framepool，不自动切换")
    commands.add_parser("screenshot", help="截图到 .runtime/screenshots，返回编号后的文件路径")
    commands.add_parser("navigate", help="进入标准博弈，复位最高职级并开始对局")
    commands.add_parser("observe", help="只读识别当前界面并返回局势")
    commands.add_parser("prepare", help="进入备战流程，收奖励并返回观察")
    commands.add_parser("rewards", help="读取最近一次后台补领结果")
    overlay = commands.add_parser("overlay", help="开关运行时识别区域显示")
    overlay.add_argument("state", choices=("on", "off"), help="on 开启，off 关闭")
    act = commands.add_parser("act", help="执行基础输入")
    actions = act.add_subparsers(dest="action", required=True)
    actions.add_parser("collect", help="按住鼠标沿右侧奖励区遍历一轮并截图")
    actions.add_parser("shop-open", help="打开商店并确认布局")
    actions.add_parser("shop-close", help="收起商店并确认布局")
    actions.add_parser("trail-shop-open", help="打开开拓商店并读取货架")
    actions.add_parser("trail-shop-close", help="关闭开拓商店并确认备战")
    trail_buy = actions.add_parser("trail-shop-buy", help="购买一个开拓商品并确认扣款换货")
    trail_buy.add_argument("--slot", required=True, type=int, choices=range(1, 5))
    actions.add_parser("refresh", help="按当前免费次数或费用刷新商店一次")
    actions.add_parser("supply-refresh", help="有刷新按钮且次数可用时刷新补给一次")
    supply = actions.add_parser("supply", help="选择一项补给并确认")
    supply.add_argument("--option", type=int, choices=range(1, 6), required=True, help="从左到右的选项编号")
    actions.add_parser("encounter-refresh", help="刷新所有遭遇选项一次")
    encounter = actions.add_parser("encounter", help="选择一项遭遇并确认")
    encounter.add_argument("--option", type=int, choices=(1, 2), required=True, help="从左到右的选项编号")
    box = actions.add_parser("armament-open", help="打开指定备战席武装箱")
    box.add_argument("--slot", required=True, choices=[f"bench_{i}" for i in range(1, 10)])
    reward = actions.add_parser("armament-choose", help="领取指定武装箱装备并核对到账")
    reward.add_argument("--option", required=True, type=int, choices=range(1, 5))
    experience = actions.add_parser("buy-experience", help="按经验量分次购买并核对到账")
    experience.add_argument("--xp", type=int, required=True, help="要购买的经验，须为 4 的正整数倍")
    sell = actions.add_parser("sell", help="出售指定备战席角色，确认后排入后台补领")
    sell.add_argument("--from", dest="source", choices=[f"bench_{i}" for i in range(1, 10)], required=True)
    actions.add_parser("start-battle", help="点击出战一次，最多等待十秒确认战斗")
    actions.add_parser("continue-battle", help="点击继续挑战，等待并进入备战流程")
    actions.add_parser("protagonist-open", help="打开主角选择页")
    actions.add_parser("protagonist-confirm", help="确认主角与光环，继续备战")
    character = actions.add_parser("protagonist-character", help="拖选主角角色")
    character.add_argument("--slot", choices=[f"{area}_{i}" for area, count in (("front", 4), ("back", 6))
                                            for i in range(1, count + 1)], required=True)
    for name in ("protagonist-aura", "protagonist-refresh"):
        aura = actions.add_parser(name, help="选择或刷新指定侧光环")
        aura.add_argument("--option", type=int, choices=(1, 2), required=True)
    click = actions.add_parser("click", help="按截图像素坐标单击一次并重新截图")
    click.add_argument("--x", type=int, required=True)
    click.add_argument("--y", type=int, required=True)
    drag = actions.add_parser("drag", help="按截图坐标拖动一次并重新截图")
    for flag in ("from-x", "from-y", "to-x", "to-y"):
        drag.add_argument("--" + flag, type=int, required=True)
    drag.add_argument("--duration", type=int, default=500, help="拖动时长，毫秒；1 至 10000，默认 500")
    press = actions.add_parser("press", help="按下并释放一个按键，再重新截图")
    press.add_argument("--key", required=True, type=str.lower, help="按键名，如 escape、enter、a 或 1")
    move = actions.add_parser("move", help="在备战槽位之间拖动，结果需检查截图")
    move.add_argument("--from", dest="source", required=True, help="来源槽位，如 bench_1")
    move.add_argument("--to", dest="target", required=True, help="目标槽位，如 front_1")
    environment = actions.add_parser("environment", help="选择投资环境并点击确认")
    environment.add_argument("--option", type=int, choices=(1, 2, 3), required=True, help="从左到右的选项编号")
    for name, help_text in (("strategy", "选择投资策略并确认"), ("strategy-refresh", "刷新指定投资策略一次")):
        strategy = actions.add_parser(name, help=help_text)
        strategy.add_argument("--option", type=int, choices=(1, 2, 3), required=True, help="从左到右的选项编号")
    args = parser.parse_args()
    # 防：游戏按 4 经验一笔购买。
    if args.command == "act" and args.action == "buy-experience" and (args.xp <= 0 or args.xp % 4):
        experience.error("--xp 须为 4 的正整数倍")
    if args.command == "version":
        data = {"result": "success", "version": VERSION}
    elif args.command == "echo":
        data = {"result": "success", "text": args.text}
    else:
        try:
            if sys.platform != "win32":
                raise RuntimeError("窗口与运行控制目前仅支持 Windows")
            from game import input as game_input
            import service
            from game.slots import SLOTS_BY_BACKSTAGE

            # 防：未支持的按键、超出通信时限的拖动时长。
            if args.command == "act" and args.action == "press" and args.key not in game_input.KEYS:
                parser.error("不支持的按键；支持 escape、enter、space、tab、backspace、方向键、字母和数字")
            if args.command == "act" and args.action == "drag" and not 1 <= args.duration <= 10000:
                parser.error("拖动时长必须为 1 至 10000 毫秒")
            # 防：商店槽位、无效槽位或相同起终点。
            if args.command == "act" and args.action == "move" and (
                    args.source not in SLOTS_BY_BACKSTAGE[7] or args.target not in SLOTS_BY_BACKSTAGE[7]
                    or args.source == args.target):
                parser.error("来源和目标须为不同的备战席、前台或后台槽位")

            if args.command == "windows":
                service.init_maa()
                data = service.windows(args.title)
            elif args.command == "start":
                data = service.start(args.hwnd, args.method)
            elif args.command == "status":
                data = service.status()
            elif args.command == "stop":
                data = service.stop()
            else:
                if not service.INFO.exists():
                    raise RuntimeError("服务未启动；请先运行 start --hwnd 窗口句柄")
                if args.command == "overlay":
                    data = service.request("overlay", data={"enabled": args.state == "on"})
                elif args.command == "act":
                    fields = {"click": ("x", "y"), "drag": ("from_x", "from_y", "to_x", "to_y", "duration"),
                              "press": ("key",), "move": ("source", "target"), "environment": ("option",),
                              "strategy": ("option",), "strategy-refresh": ("option",), "collect": (),
                              "supply": ("option",), "supply-refresh": (), "encounter": ("option",),
                              "encounter-refresh": (),
                              "start-battle": (), "continue-battle": (), "protagonist-open": (),
                              "protagonist-confirm": (), "protagonist-character": ("slot",),
                              "protagonist-aura": ("option",), "protagonist-refresh": ("option",),
                              "armament-open": ("slot",), "armament-choose": ("option",),
                              "buy-experience": ("xp",), "sell": ("source",), "shop-open": (), "shop-close": (),
                              "trail-shop-open": (), "trail-shop-close": (), "trail-shop-buy": ("slot",),
                              "refresh": ()}[args.action]
                    payload = {field: getattr(args, field) for field in fields}
                    if args.action == "move":
                        payload = {"from": payload["source"], "to": payload["target"]}
                    elif args.action == "sell":
                        payload = {"from": payload["source"]}
                    command = args.action + "-choose" if args.action in {"strategy", "supply", "encounter"} else args.action
                    data = service.request(command, data=payload)
                else:
                    data = service.request(args.command)
        except ModuleNotFoundError as error:
            data = {"result": "failed", "message": f"{error}；请用当前 Python 安装 requirements.txt"}
        except (OSError, RuntimeError, ValueError) as error:
            data = {"result": "failed", "message": str(error)}
    output(data)
    return 0 if data["result"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())

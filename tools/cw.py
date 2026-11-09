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
    act = commands.add_parser("act", help="执行基础输入")
    actions = act.add_subparsers(dest="action", required=True)
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
    args = parser.parse_args()
    if args.command == "version":
        data = {"result": "success", "version": VERSION}
    elif args.command == "echo":
        data = {"result": "success", "text": args.text}
    else:
        try:
            if sys.platform != "win32":
                raise RuntimeError("窗口与运行控制目前仅支持 Windows")
            import input as game_input
            import service
            from slots import SLOTS

            # 防：命令行传入未支持的按键或超出通信时限的拖动时长。
            if args.command == "act" and args.action == "press" and args.key not in game_input.KEYS:
                parser.error("不支持的按键；支持 escape、enter、space、tab、backspace、方向键、字母和数字")
            if args.command == "act" and args.action == "drag" and not 1 <= args.duration <= 10000:
                parser.error("拖动时长必须为 1 至 10000 毫秒")
            # 防：命令行传入商店、无效槽位或相同的起点和终点。
            if args.command == "act" and args.action == "move" and (
                    args.source not in SLOTS or args.target not in SLOTS or args.source == args.target):
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
                if args.command == "act":
                    fields = {"click": ("x", "y"), "drag": ("from_x", "from_y", "to_x", "to_y", "duration"),
                              "press": ("key",), "move": ("source", "target")}[args.action]
                    payload = {field: getattr(args, field) for field in fields}
                    if args.action == "move":
                        payload = {"from": payload["source"], "to": payload["target"]}
                    data = service.request(args.action, data=payload)
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

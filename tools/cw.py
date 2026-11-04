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
    commands.add_parser("status", help="查询后台服务状态，不刷新观察令牌")
    commands.add_parser("stop", help="等待当前操作结束，释放连接并停止服务")
    commands.add_parser("resync", help="重新截图并发布新的观察令牌")
    commands.add_parser("version", help="查看程序版本")
    echo = commands.add_parser("echo", help="原样返回测试文字，不执行游戏操作")
    echo.add_argument("text", help="要返回的测试文字")
    find = commands.add_parser("windows", help="通过 MaaFramework 列出窗口")
    find.add_argument("--title", default="", help="按窗口标题包含的文字筛选")
    start = commands.add_parser("start", help="在后台启动服务并连接指定窗口")
    start.add_argument("--hwnd", type=window_handle, required=True, help="windows 返回的窗口句柄")
    start.add_argument("--method", choices=["framepool", "screendc", "printwindow"],
                       default="framepool", help="截图方式；默认 framepool，不自动切换")
    commands.add_parser("screenshot", help="截图到 .runtime/screenshots，按本次运行编号并返回观察令牌")
    args = parser.parse_args()
    if args.command == "version":
        data = {"result": "success", "version": VERSION}
    elif args.command == "echo":
        data = {"result": "success", "text": args.text}
    else:
        try:
            if sys.platform != "win32":
                raise RuntimeError("窗口与运行控制目前仅支持 Windows")
            import service

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
                data = service.request(args.command)
        except ModuleNotFoundError as error:
            data = {"result": "failed", "message": f"{error}；请用当前 Python 安装 requirements.txt"}
        except (OSError, RuntimeError, ValueError) as error:
            data = {"result": "failed", "message": str(error)}
    output(data)
    return 0 if data["result"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())

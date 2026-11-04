"""本机常驻服务：保留窗口连接，串行执行操作，管理观察令牌。"""

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


RUNTIME = Path(__file__).resolve().parent / ".runtime"
DIRECTORY = RUNTIME / "service"
INFO = DIRECTORY / "server.json"
METHODS = ("framepool", "screendc", "printwindow")


class ServiceBusy(RuntimeError):
    pass


def init_maa():
    from maa.define import LoggingLevelEnum
    from maa.tasker import Tasker

    if not Tasker.set_stdout_level(LoggingLevelEnum.Off):
        raise RuntimeError("无法关闭 MaaFramework 标准输出日志")
    log_dir = RUNTIME / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not Tasker.set_log_dir(log_dir):
        raise RuntimeError("无法设置 MaaFramework 日志目录")


def windows(title):
    from maa.toolkit import Toolkit

    return {"result": "success", "windows": [
        {"hwnd": w.hwnd, "title": w.window_name, "class_name": w.class_name}
        for w in Toolkit.find_desktop_windows()
        if title.casefold() in w.window_name.casefold()
    ]}


@contextmanager
def service_lock():
    """进程持有期间锁住一个字节，进程退出由系统释放。"""
    with (DIRECTORY / "server.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except PermissionError as error:
            raise ServiceBusy("已有服务持有运行锁；请用 status 查询") from error
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def clear_screenshots():
    directory = RUNTIME / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.glob("*.png"):
        path.unlink()


class Session:
    def __init__(self, hwnd, method):
        self.hwnd = hwnd
        self.method = method
        self.controller = None
        self.title = None
        self.operation_lock = threading.Lock()
        self.metadata_lock = threading.Lock()
        self.stopping = False
        self.operation = None
        self.state = None
        self.prefix = uuid4().hex
        self.counter = 0
        self.error = None

    def connect(self):
        from maa.controller import Win32Controller
        from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum

        init_maa()
        window = next((w for w in windows("")["windows"] if w["hwnd"] == self.hwnd), None)
        if window is None:
            raise RuntimeError(f"未找到窗口 {self.hwnd}；请重新运行 windows 获取句柄")
        self.title = window["title"]
        methods = {
            "framepool": MaaWin32ScreencapMethodEnum.FramePool,
            "screendc": MaaWin32ScreencapMethodEnum.ScreenDC,
            "printwindow": MaaWin32ScreencapMethodEnum.PrintWindow,
        }
        controller = Win32Controller(
            self.hwnd, methods[self.method],
            mouse_method=MaaWin32InputMethodEnum.Null,
            keyboard_method=MaaWin32InputMethodEnum.Null,
        )
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError(f"连接窗口 {self.hwnd} 失败")
        if not controller.set_screenshot_use_raw_size(True):
            raise RuntimeError("无法设置原始截图尺寸")
        with self.metadata_lock:
            self.controller = controller

    def status(self):
        with self.metadata_lock:
            return {
                "result": "success",
                "status": "stopping" if self.stopping else "busy" if self.operation else "idle",
                "pid": os.getpid(), "hwnd": self.hwnd, "title": self.title,
                "connected": self.controller is not None, "method": self.method,
                "operation": self.operation, "state": self.state, "error": self.error,
            }

    def run(self, name, action, state=None):
        """操作共用此入口；未来输入动作必须提供观察令牌。"""
        if not self.operation_lock.acquire(blocking=False):
            return {"result": "busy", "message": "正在执行另一个操作"}
        try:
            with self.metadata_lock:
                if self.stopping:
                    return {"result": "failed", "reason": "stopping", "message": "服务正在停止"}
                if state is not None and state != self.state:
                    return {"result": "failed", "reason": "stale_state", "message": "观察令牌已失效"}
                self.state = None
                self.operation = name
                self.error = None
            try:
                data = action()
            except (OSError, RuntimeError, ModuleNotFoundError) as error:
                with self.metadata_lock:
                    self.error = str(error)
                raise
            with self.metadata_lock:
                if self.stopping:
                    return {"result": "failed", "reason": "stopping", "message": "服务正在停止，未发布新观察"}
                self.counter += 1
                self.state = f"{self.prefix}-{self.counter}"
                return {"result": "success", **data, "state": self.state}
        finally:
            with self.metadata_lock:
                self.operation = None
            self.operation_lock.release()

    def capture(self):
        from PIL import Image

        job = self.controller.post_screencap().wait()
        if not job.succeeded:
            raise RuntimeError(f"窗口截图失败，截图方式：{self.method}；目标窗口可能已关闭")
        image = job.get()
        if image.size == 0:
            raise RuntimeError("MaaFramework 返回空截图")
        path = RUNTIME / "screenshots" / f"{self.counter + 1}.png"
        Image.fromarray(image[:, :, ::-1]).save(path, format="PNG")
        return {"hwnd": self.hwnd, "title": self.title, "method": self.method,
                "screenshot": str(path.resolve()), "width": image.shape[1], "height": image.shape[0]}

    def stop(self):
        with self.metadata_lock:
            self.stopping = True
            self.state = None


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)  # 不让未发完的本机请求一直阻塞服务退出。

    def log_message(self, *args):
        pass  # 常规请求不写访问日志。

    def reply(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # CLI 退出不取消已接受的操作；结果仍可通过 status 查询。
            print("CLI 已断开，未能发送响应", file=sys.stderr, flush=True)

    def do_GET(self):
        self.reply({"result": "failed", "message": "仅接受 POST JSON 请求"}, 405)

    def do_POST(self):
        if self.path not in {"/status", "/stop", "/screenshot", "/resync"}:
            self.reply({"result": "failed", "message": "接口不存在"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if self.headers.get_content_type() != "application/json" or not 0 < length <= 16384:
            self.reply({"result": "failed", "message": "需要不超过 16 KiB 的 JSON 请求"}, 400)
            return
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            self.reply({"result": "failed", "message": "JSON 格式错误"}, 400)
            return
        if not isinstance(data, dict):
            self.reply({"result": "failed", "message": "请求必须是 JSON 对象"}, 400)
            return
        if data:
            self.reply({"result": "failed", "message": "请求参数不符合接口约定"}, 400)
            return
        session = self.server.session
        try:
            if self.path == "/status":
                result = session.status()
            elif self.path == "/stop":
                session.stop()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                result = {"result": "success", "status": "stopping"}
            else:
                result = session.run(self.path[1:], session.capture)
        except (OSError, RuntimeError, ModuleNotFoundError) as error:
            result = {"result": "failed", "message": str(error)}
        self.reply(result)


def request(command, info=None):
    if info is None:
        info = json.loads(INFO.read_text(encoding="utf-8"))
    req = Request(f"http://127.0.0.1:{info['port']}/{command}", data=b"{}", headers={
        "Content-Type": "application/json",
    })
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"服务拒绝请求（HTTP {error.code}）") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("服务通信失败或超时；操作可能仍在运行，请查询 status，不要盲目重发") from error


def start(hwnd, method):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    ready = DIRECTORY / f"start-{uuid4().hex}.json"
    log_path = DIRECTORY / "service.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--hwnd", str(hwnd),
             "--method", method, "--ready", ready.name],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready.exists():
            result = json.loads(ready.read_text(encoding="utf-8"))
            ready.unlink()
            return result
        if process.poll() is not None and not ready.exists():
            raise RuntimeError(f"服务启动失败，详见 {log_path}")
        time.sleep(0.05)
    raise RuntimeError(f"服务启动尚未确认（PID {process.pid}）；请查询 status，日志：{log_path}")


def status():
    if not INFO.exists():
        return {"result": "success", "status": "stopped", "connected": False, "state": None}
    return request("status")


def stop():
    if not INFO.exists():
        return {"result": "success", "status": "stopped"}
    info = json.loads(INFO.read_text(encoding="utf-8"))
    result = request("stop", info=info)
    if result["result"] != "success":
        return result
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with service_lock():
                return {"result": "success", "status": "stopped"}
        except ServiceBusy:
            if INFO.exists() and json.loads(INFO.read_text(encoding="utf-8"))["pid"] != info["pid"]:
                return {"result": "success", "status": "stopped"}
        time.sleep(0.05)
    raise RuntimeError("服务仍在停止，当前截图可能尚未结束；请查询 status")


def serve(args):
    ready = DIRECTORY / args.ready
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    try:
        with service_lock():
            INFO.unlink(missing_ok=True)
            session = Session(args.hwnd, args.method)
            server = None
            thread = None
            try:
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                server.session = session
                session.operation = "connect"
                write_json(INFO, {"port": server.server_port, "pid": os.getpid()})
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
                with session.operation_lock:
                    thread.start()
                    try:
                        session.connect()
                        clear_screenshots()
                    finally:
                        with session.metadata_lock:
                            session.operation = None
                if session.stopping:
                    write_json(ready, {"result": "failed", "message": "启动期间收到停止请求"})
                else:
                    write_json(ready, session.status())
                thread.join()
            finally:
                if server is not None:
                    if thread is not None:
                        server.shutdown()
                        thread.join()
                    # 等待已接受的请求结束，再销毁连接并释放进程锁。
                    server.server_close()
                session.controller = None
                INFO.unlink(missing_ok=True)
    except ServiceBusy as error:
        write_json(ready, {"result": "busy", "message": str(error)})
    except (OSError, RuntimeError, ModuleNotFoundError) as error:
        write_json(ready, {"result": "failed", "message": str(error)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CLI 内部后台服务入口")
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--method", choices=METHODS, required=True)
    parser.add_argument("--ready", required=True)
    serve(parser.parse_args())

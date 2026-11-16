"""本机常驻服务：保留窗口连接，串行执行操作并保存截图。"""

import argparse
from game import input as game_input
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
from recognition_debug import trace


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
        self.condition = threading.Condition(self.metadata_lock)
        self.foreground_waiters = 0
        self.stopping = False
        self.operation = None
        self.screenshot_count = 0
        self.error = None
        self.image_size = None
        self.preparation = None
        self.preparation_flow = None
        self.strategy = None
        self.supply = None
        self.protagonist = None
        self.battle = None
        self.battle_monitor = None
        self.overlay = None
        self.collector = None

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
            mouse_method=MaaWin32InputMethodEnum.Seize,
            keyboard_method=MaaWin32InputMethodEnum.Seize,
        )
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError(f"连接窗口 {self.hwnd} 失败")
        if not controller.set_screenshot_use_raw_size(True):
            raise RuntimeError("无法设置原始截图尺寸")
        with self.metadata_lock:
            self.controller = controller

    def status(self):
        with self.metadata_lock:
            result = {
                "result": "success",
                "status": "stopping" if self.stopping else "busy" if self.operation else "idle",
                "pid": os.getpid(), "hwnd": self.hwnd, "title": self.title,
                "connected": self.controller is not None, "method": self.method,
                "operation": self.operation, "error": self.error,
                "overlay": self.overlay is not None,
            }
            if self.battle is not None:
                result["screen"] = self.battle.current()["screen"]
            if self.collector is not None:
                result["collection"] = self.collector.summary()
            return result

    def run(self, name, action):
        """串行执行截图和输入。"""
        with self.condition:
            self.foreground_waiters += 1
            try:
                while self.operation == "collect-background":
                    self.condition.wait()
                if not self.operation_lock.acquire(blocking=False):
                    return {"result": "busy", "message": "正在执行另一个操作"}
            finally:
                self.foreground_waiters -= 1
            self.operation = name
        try:
            with self.metadata_lock:
                if self.stopping:
                    return {"result": "failed", "reason": "stopping", "message": "服务正在停止"}
                self.error = None
            try:
                with trace(None if name == "overlay" else self.overlay):
                    data = action()
            except game_input.ERRORS as error:
                with self.metadata_lock:
                    self.error = str(error)
                raise
            with self.metadata_lock:
                if self.stopping:
                    return {"result": "failed", "reason": "stopping", "message": "服务正在停止，未发布新观察"}
                return {"result": "success", **data}
        finally:
            with self.condition:
                self.operation = None
                self.operation_lock.release()
                self.condition.notify_all()
            if self.collector is not None:
                self.collector.wake()

    def set_overlay(self, enabled):
        from overlay import Overlay

        if enabled:
            if self.overlay is None:
                self.overlay = Overlay(self.hwnd)
            self.overlay.check_error()
        elif self.overlay is not None:
            overlay, self.overlay = self.overlay, None
            overlay.close()
        return {"overlay": enabled}

    def capture(self):
        # 防：本次截图失败后仍沿用旧尺寸发输入。
        self.image_size = None
        job = self.controller.post_screencap().wait()
        if not job.succeeded:
            raise RuntimeError(f"窗口截图失败，截图方式：{self.method}；目标窗口可能已关闭")
        return self.save_image(job.get())

    def preparation_reader(self):
        from screens.preparation import Preparation

        if self.preparation is None:
            self.preparation = Preparation()
        return self.preparation

    def preparation_frame(self):
        self.preparation_reader()
        job = self.controller.post_screencap().wait()
        # 防：备战观察截图失败。
        if not job.succeeded:
            raise RuntimeError("备战截图失败")
        return job.get()

    def preparation_scene(self, image):
        from screens.preparation import layout

        reader = self.preparation_reader()
        if reader.score(image, "armament.png", (940, 20, 190, 80)) >= 0.9:
            return {"result": "success", "screen": "武装箱选择", "needs_ai": True}
        if self.protagonist_reader().stage(image) == "selection":
            return {"result": "success", "screen": "选择主角", "needs_ai": True}
        if self.strategy_reader().page(image):
            return {"result": "success", "screen": "投资策略", "needs_ai": True}
        if self.supply_reader().page(image):
            return {"result": "success", "screen": "补给", "needs_ai": True}
        result = layout(image, reader)
        if result is not None and result["shop"] == "closed":
            return {**result, **reader.action_button(image)}
        return result if result is not None else {"result": "uncertain", "screen": "未知", "needs_ai": True}

    def sell_character(self, source):
        from screens.sell import sell
        from flow.rewards import Collector

        result = sell(self, source)
        if result["result"] == "success":
            if self.collector is None:
                self.collector = Collector(self)
            self.collector.request()
            result = {**result, "collection": {"state": "waiting", "pending": True}}
        return result

    def shop(self, target):
        from screens.shop import switch

        return switch(self, target)

    def observe_shop(self):
        from screens.shop import observe

        return observe(self)

    def refresh_shop(self):
        from screens.shop import refresh

        return refresh(self)

    def observe_rewards(self):
        with self.metadata_lock:
            if self.collector is None:
                return {"result": "success", "collection": {"state": "idle", "pending": False}}
            return {"result": "success", **(self.collector.latest or {}), "collection": self.collector.summary()}

    def observe_preparation(self):
        if self.battle is not None and self.battle.current()["screen"] == "战斗":
            return self.observe_battle()
        image = self.preparation_frame()
        reader = self.protagonist_reader()
        if reader.stage(image) is not None:
            return self.protagonist_action(image=image)
        if self.supply_reader().page(image):
            return self.supply_action(image=image)
        result = self.preparation.recognize(image)
        if result["result"] != "success" and result["screen"] != "备战":
            return self.investment_strategy("preparation", image=image)
        if self.battle is not None:
            self.battle.begin(result)
        return {**self.save_image(image), **result}

    def investment_strategy(self, action="read", option=None, image=None):
        from screens.strategy import StrategyOperation

        operation = StrategyOperation(self, self.strategy_reader())
        result = operation.execute(action, option, image)
        if action == "choose" and result["result"] == "success" and result["screen"] == "备战":
            if self.preparation_flow is None:
                result = self.enter_preparation(operation.image)
            else:
                result = self.preparation_flow.resume(operation.image)
            result = {**result, "action": "strategy", "option": option}
        else:
            result = operation.save(result)
        if self.battle is not None and result["result"] == "success":
            self.battle.begin(result)
        return result

    def strategy_reader(self):
        from screens.strategy import Strategy

        if self.strategy is None:
            self.strategy = Strategy(self.preparation_reader())
        return self.strategy

    def supply_reader(self):
        from screens.supply import Supply

        if self.supply is None:
            self.supply = Supply(self.preparation_reader())
        return self.supply

    def supply_action(self, action="read", option=None, image=None):
        from screens.supply import SupplyOperation

        operation = SupplyOperation(self, self.supply_reader())
        result = operation.execute(action, option, image)
        if action == "choose" and result["result"] == "success" and result["screen"] == "备战":
            if self.preparation_flow is None:
                result = self.enter_preparation(operation.image)
            else:
                result = self.preparation_flow.resume(operation.image)
        else:
            result = operation.save(result)
        if action != "read":
            result = {**result, "action": "supply" if action == "choose" else "supply-refresh"}
            if option is not None:
                result["option"] = option
        if self.battle is not None and result["result"] == "success":
            self.battle.begin(result)
        return result

    def protagonist_reader(self):
        from screens.protagonist import Protagonist

        if self.protagonist is None:
            self.protagonist = Protagonist(self.preparation_reader())
        return self.protagonist

    def protagonist_action(self, action="read", slot=None, option=None, image=None):
        from screens.protagonist import ProtagonistOperation

        operation = ProtagonistOperation(self, self.protagonist_reader())
        result = operation.execute(action, slot, option, image)
        if action == "confirm" and result["result"] == "success" and result["screen"] == "备战":
            if self.preparation_flow is None:
                result = self.enter_preparation(operation.image)
            else:
                result = self.preparation_flow.resume(operation.image)
        else:
            result = operation.save(result)
        if action != "read":
            result = {**result, "action": f"protagonist-{action}"}
            if slot is not None:
                result["slot"] = slot
            if option is not None:
                result["option"] = option
        if self.battle is not None and result["result"] == "success":
            self.battle.begin(result)
        return result

    def enter_preparation(self, image=None, delay=0):
        from flow.prepare import PreparationFlow

        if self.collector is not None:
            self.collector.cancel()
        if self.preparation_flow is None:
            self.preparation_flow = PreparationFlow(self)
        result = self.preparation_flow.enter(image, delay)
        if self.battle is not None:
            self.battle.begin(result)
        return result

    def collect_rewards(self):
        from flow.rewards import collect

        image = self.preparation_frame()
        page = self.preparation_scene(image)
        if page["screen"] != "备战" or page["shop"] != "closed":
            return {**self.save_image(image), **page, "result": "uncertain", "needs_ai": True}
        return collect(self.controller, self.capture)

    def battle_reader(self):
        from screens.battle import Battle

        if self.battle is None:
            self.battle = Battle()
        return self.battle

    def watch_battle(self):
        from screens.battle import Monitor

        if self.battle_monitor is None:
            self.battle_monitor = Monitor(self, self.battle)

    def start_battle(self):
        from screens.battle import start

        result = start(self, self.battle_reader())
        if result["result"] == "success" and result["screen"] in {"战斗", "本场结算"}:
            if self.collector is not None:
                self.collector.cancel()
            self.watch_battle()
        return result

    def observe_battle(self):
        from screens.battle import capture

        reader = self.battle_reader()
        state = reader.current()
        if state.get("action") == "continue-battle":
            return state
        image = capture(self.controller)
        if reader.current()["screen"] == "战斗":
            result = reader.observe(image)
        else:
            result = reader.recognize(image)
            reader.begin(result)
        if result["screen"] == "战斗":
            self.watch_battle()
        return {**self.save_image(image), **result}

    def finish_battle(self, image):
        from screens.battle import finish

        return finish(self, self.battle_reader(), image)

    def continue_battle(self):
        from screens.battle import continue_battle

        return continue_battle(self, self.battle_reader())

    def save_image(self, image):
        from PIL import Image

        path = RUNTIME / "screenshots" / f"{self.screenshot_count + 1}.png"
        Image.fromarray(image[:, :, ::-1]).save(path, format="PNG")
        self.screenshot_count += 1
        self.image_size = (image.shape[1], image.shape[0])
        return {"hwnd": self.hwnd, "title": self.title, "method": self.method,
                "screenshot": str(path.resolve()), "width": image.shape[1], "height": image.shape[0]}

    def stop(self):
        with self.metadata_lock:
            self.stopping = True
        if self.battle_monitor is not None:
            self.battle_monitor.stop()
        if self.collector is not None:
            self.collector.stop()


def navigate(session, data):
    from flow.navigation import Navigator

    return session.run("navigate", lambda: Navigator(session).run())


def environment(session, data):
    from flow.navigation import Navigator

    return session.run("environment", lambda: Navigator(session).run(data["option"]))


def strategy(action=None):
    if action is None:
        return lambda session, data: session.run("strategy", session.investment_strategy)
    return lambda session, data: session.run(f"strategy-{action}",
                                            lambda: session.investment_strategy(action, data["option"]))


def protagonist(action=None):
    if action is None:
        return lambda session, data: session.run("protagonist", session.protagonist_action)
    return lambda session, data: session.run(f"protagonist-{action}",
                                            lambda: session.protagonist_action(action, **data))


# 接口路径与处理函数的唯一清单，返回路径即表示接口存在。
ROUTES = {
    "/status": lambda session, data: session.status(),
    "/rewards": lambda session, data: session.observe_rewards(),
    "/shop": lambda session, data: session.run("shop", session.observe_shop),
    "/overlay": lambda session, data: session.run("overlay", lambda: session.set_overlay(data["enabled"])),
    "/screenshot": lambda session, data: session.run("screenshot", session.capture),
    "/resync": lambda session, data: session.run("resync", session.capture),
    "/click": lambda session, data: session.run("click", lambda: game_input.click(
        session.controller, session.image_size, session.capture, data["x"], data["y"])),
    "/drag": lambda session, data: session.run("drag", lambda: game_input.drag(
        session.controller, session.image_size, session.capture,
        data["from_x"], data["from_y"], data["to_x"], data["to_y"], data["duration"])),
    "/press": lambda session, data: session.run("press", lambda: game_input.press(
        session.controller, session.capture, data["key"])),
    "/move": lambda session, data: session.run("move", lambda: game_input.move(
        session.controller, session.image_size, session.capture, data["from"], data["to"])),
    "/sell": lambda session, data: session.run("sell", lambda: session.sell_character(data["from"])),
    "/shop-open": lambda session, data: session.run("shop-open", lambda: session.shop("open")),
    "/shop-close": lambda session, data: session.run("shop-close", lambda: session.shop("closed")),
    "/refresh": lambda session, data: session.run("refresh", session.refresh_shop),
    "/navigate": navigate,
    "/environment": environment,
    "/preparation": lambda session, data: session.run("preparation", session.observe_preparation),
    "/strategy": strategy(),
    "/strategy-refresh": strategy("refresh"),
    "/strategy-choose": strategy("choose"),
    "/supply": lambda session, data: session.run("supply", session.supply_action),
    "/supply-refresh": lambda session, data: session.run("supply-refresh", lambda: session.supply_action("refresh")),
    "/supply-choose": lambda session, data: session.run("supply-choose",
        lambda: session.supply_action("choose", data["option"])),
    "/protagonist": protagonist(),
    **{f"/protagonist-{name}": protagonist(name)
       for name in ("open", "character", "aura", "refresh", "confirm")},
    "/collect": lambda session, data: session.run("collect", session.collect_rewards),
    "/start-battle": lambda session, data: session.run("start-battle", session.start_battle),
    "/battle": lambda session, data: session.run("battle", session.observe_battle),
    "/prepare": lambda session, data: session.run("prepare", session.enter_preparation),
    "/continue-battle": lambda session, data: session.run("continue-battle", session.continue_battle),
}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)  # 防：请求未发完时阻塞服务关闭。

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
        if self.path == "/stop":
            self.server.session.stop()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            self.reply({"result": "success", "status": "stopping"})
            return
        route = ROUTES.get(self.path)
        if route is None:
            self.reply({"result": "failed", "message": "接口不存在"}, 404)
            return
        # 防：请求只发了一半或不是 JSON。
        try:
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        except (KeyError, ValueError, TimeoutError):
            self.reply({"result": "failed", "message": "请求体不完整或不是 JSON"}, 400)
            return
        try:
            result = route(self.server.session, data)
        except game_input.InputUncertain as error:
            result = {"result": "uncertain", "message": str(error)}
        except game_input.ERRORS as error:
            result = {"result": "failed", "message": str(error)}
        self.reply(result)


def request(command, info=None, data=None):
    if info is None:
        info = json.loads(INFO.read_text(encoding="utf-8"))
    req = Request(
        f"http://127.0.0.1:{info['port']}/{command}",
        data=json.dumps(data if data is not None else {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    timeout = 60 if (
        command in {"navigate", "preparation", "strategy", "strategy-refresh",
                    "strategy-choose", "prepare", "continue-battle"}
        or command.startswith(("protagonist", "supply"))
    ) else 30
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"服务拒绝请求（HTTP {error.code}）") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("服务通信失败或超时；操作可能仍在运行，先用 status 查询状态") from error


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
        return {"result": "success", "status": "stopped", "connected": False}
    return request("status")


def stop():
    if not INFO.exists():
        return {"result": "success", "status": "stopped"}
    info = json.loads(INFO.read_text(encoding="utf-8"))
    request("stop", info=info)
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
                if session.battle_monitor is not None:
                    session.battle_monitor.close()
                if session.collector is not None:
                    session.collector.close()
                if session.preparation is not None:
                    session.preparation.close()
                try:
                    if session.overlay is not None:
                        session.overlay.close()
                finally:
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

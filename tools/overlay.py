"""Windows 透明识别框；识别线程只更新内存，窗口线程负责绘制。"""

import ctypes
from ctypes import wintypes
from threading import Event, Lock, Thread
from time import monotonic


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT)

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                            wintypes.WPARAM, wintypes.LPARAM)


class WindowClass(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("proc", WNDPROC), ("class_extra", ctypes.c_int),
                ("window_extra", ctypes.c_int), ("instance", wintypes.HANDLE),
                ("icon", wintypes.HANDLE), ("cursor", wintypes.HANDLE),
                ("background", wintypes.HANDLE), ("menu", wintypes.LPCWSTR),
                ("name", wintypes.LPCWSTR)]


class PaintStruct(ctypes.Structure):
    _fields_ = [("dc", wintypes.HDC), ("erase", wintypes.BOOL), ("rect", wintypes.RECT),
                ("restore", wintypes.BOOL), ("update", wintypes.BOOL), ("reserved", wintypes.BYTE * 32)]


# ctypes 默认按 32 位返回整数；窗口与 GDI 句柄在本机为 64 位。
for dll, name, result, args in (
    (kernel32, "GetModuleHandleW", wintypes.HANDLE, (wintypes.LPCWSTR,)),
    (user32, "RegisterClassW", wintypes.ATOM, (ctypes.POINTER(WindowClass),)),
    (user32, "UnregisterClassW", wintypes.BOOL, (wintypes.LPCWSTR, wintypes.HANDLE)),
    (user32, "CreateWindowExW", wintypes.HWND, (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p)),
    (user32, "DefWindowProcW", ctypes.c_ssize_t,
        (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)),
    (user32, "DestroyWindow", wintypes.BOOL, (wintypes.HWND,)),
    (user32, "SetLayeredWindowAttributes", wintypes.BOOL,
        (wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD)),
    (user32, "SetTimer", ctypes.c_size_t, (wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)),
    (user32, "GetMessageW", ctypes.c_int,
        (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)),
    (user32, "DispatchMessageW", ctypes.c_ssize_t, (ctypes.POINTER(wintypes.MSG),)),
    (user32, "BeginPaint", wintypes.HDC, (wintypes.HWND, ctypes.POINTER(PaintStruct))),
    (user32, "EndPaint", wintypes.BOOL, (wintypes.HWND, ctypes.POINTER(PaintStruct))),
    (user32, "FillRect", ctypes.c_int, (wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE)),
    (user32, "DrawTextW", ctypes.c_int,
        (wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT)),
    (user32, "InvalidateRect", wintypes.BOOL, (wintypes.HWND, ctypes.c_void_p, wintypes.BOOL)),
    (gdi32, "CreateSolidBrush", wintypes.HANDLE, (wintypes.DWORD,)),
    (gdi32, "CreatePen", wintypes.HANDLE, (ctypes.c_int, ctypes.c_int, wintypes.DWORD)),
    (gdi32, "GetStockObject", wintypes.HANDLE, (ctypes.c_int,)),
    (gdi32, "SelectObject", wintypes.HANDLE, (wintypes.HDC, wintypes.HANDLE)),
    (gdi32, "DeleteObject", wintypes.BOOL, (wintypes.HANDLE,)),
    (gdi32, "CreateCompatibleDC", wintypes.HDC, (wintypes.HDC,)),
    (gdi32, "CreateCompatibleBitmap", wintypes.HANDLE, (wintypes.HDC, ctypes.c_int, ctypes.c_int)),
    (gdi32, "DeleteDC", wintypes.BOOL, (wintypes.HDC,)),
    (gdi32, "Rectangle", wintypes.BOOL,
        (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)),
    (gdi32, "SetTextColor", wintypes.DWORD, (wintypes.HDC, wintypes.DWORD)),
    (gdi32, "SetBkMode", ctypes.c_int, (wintypes.HDC, ctypes.c_int)),
    (gdi32, "BitBlt", wintypes.BOOL, (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)),
):
    function = getattr(dll, name)
    function.argtypes = args
    function.restype = result

COLORS = {"template": 0x70ff38, "ocr": 0xffbf56, "crop": 0xffff00}
LINGER = 1.2


def client_bounds(hwnd):
    # 防：最小化窗口没有可供用户查看的画面，不绘制识别框。
    if user32.IsIconic(hwnd):
        return None
    rect, origin = wintypes.RECT(), wintypes.POINT()
    # 防：窗口定位失败。
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())
    return origin.x, origin.y, rect.right, rect.bottom


class Overlay:
    def __init__(self, target):
        self.target = target
        self.lock = Lock()
        self.ready = Event()
        self.stopped = Event()
        self.error = None
        self.bounds = None
        self.frame = None
        self.regions = {}
        self.expires = 0
        self.revision = 0
        self.generation = 0
        self.position_pending = False
        self.thread = Thread(target=self.run, name="recognition-overlay", daemon=True)
        self.thread.start()
        self.ready.wait()
        self.check_error()

    def check_error(self):
        # 防：窗口初始化或绘制失败。
        if self.error is not None:
            raise RuntimeError(f"识别框显示出错：{type(self.error).__name__}: {self.error}") from self.error

    def begin(self):
        self.check_error()
        with self.lock:
            self.bounds = None
            self.frame = None
            self.regions.clear()
            self.expires = float("inf")
            self.revision += 1
            self.generation += 1
            self.position_pending = True

    def locate(self):
        with self.lock:
            generation = self.generation
            pending = self.position_pending
        if pending:
            bounds = client_bounds(self.target)
            with self.lock:
                # 防：定位期间开始了新操作。
                if generation == self.generation:
                    self.bounds = bounds
                    self.position_pending = False

    def mark(self, image, roi, label, kind):
        with self.lock:
            if self.frame is not image:
                self.frame = image
                self.regions.clear()
            self.regions.pop((roi, kind), None)
            self.regions[(roi, kind)] = label
            self.revision += 1

    def end(self):
        with self.lock:
            self.expires = monotonic() + LINGER

    def close(self):
        self.stopped.set()
        self.thread.join()
        self.check_error()

    def run(self):
        instance = kernel32.GetModuleHandleW(None)
        class_name = f"CWRecognitionOverlay_{id(self)}"
        hwnd = None
        registered = False
        pens = {}
        brush = None
        drawn = -1
        visible = False
        width, height = 1, 1
        boxes = []

        def paint(window):
            ps = PaintStruct()
            dc = user32.BeginPaint(window, ctypes.byref(ps))
            buffer = gdi32.CreateCompatibleDC(dc)
            bitmap = gdi32.CreateCompatibleBitmap(dc, width, height)
            # 防：GDI 缓冲区创建失败。
            if not buffer or not bitmap:
                if bitmap:
                    gdi32.DeleteObject(bitmap)
                if buffer:
                    gdi32.DeleteDC(buffer)
                user32.EndPaint(window, ctypes.byref(ps))
                raise ctypes.WinError(ctypes.get_last_error())
            old_bitmap = gdi32.SelectObject(buffer, bitmap)
            user32.FillRect(buffer, ctypes.byref(wintypes.RECT(0, 0, width, height)), brush)
            old_brush = gdi32.SelectObject(buffer, gdi32.GetStockObject(5))
            old_font = gdi32.SelectObject(buffer, gdi32.GetStockObject(17))
            gdi32.SetBkMode(buffer, 1)
            for index, (x1, y1, x2, y2, kind, label) in enumerate(boxes):
                old_pen = gdi32.SelectObject(buffer, pens[kind])
                gdi32.Rectangle(buffer, x1, y1, x2, y2)
                gdi32.SelectObject(buffer, old_pen)
                if index == len(boxes)-1:
                    gdi32.SetTextColor(buffer, COLORS[kind])
                    label_rect = wintypes.RECT(x1+3, max(0, y1-18), x2, max(18, y1))
                    user32.DrawTextW(buffer, label, -1, ctypes.byref(label_rect), 0x120)
            gdi32.SelectObject(buffer, old_font)
            gdi32.SelectObject(buffer, old_brush)
            gdi32.BitBlt(dc, 0, 0, width, height, buffer, 0, 0, 0x00cc0020)
            gdi32.SelectObject(buffer, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(buffer)
            user32.EndPaint(window, ctypes.byref(ps))

        def update(window):
            nonlocal drawn, visible, width, height, boxes
            if self.stopped.is_set():
                user32.DestroyWindow(window)
                return
            self.locate()
            with self.lock:
                bounds = self.bounds
                frame = self.frame
                regions = tuple(self.regions.items())
                revision = self.revision
                expires = self.expires
            expired = monotonic() >= expires
            if not regions or bounds is None or expired:
                if visible:
                    user32.ShowWindow(window, 0)
                    visible = False
                if expired:
                    with self.lock:
                        if self.revision == revision:
                            self.frame = None
                            self.regions.clear()
            elif revision != drawn:
                left, top, width, height = bounds
                # 防：显示层定位失败。
                if not user32.SetWindowPos(window, -1, left, top, width, height, 0x10):
                    raise ctypes.WinError(ctypes.get_last_error())
                scale_x, scale_y = width / frame.shape[1], height / frame.shape[0]
                boxes = []
                for (roi, kind), label in regions:
                    x, y, w, h = roi
                    boxes.append((round(x*scale_x), round(y*scale_y), round((x+w)*scale_x),
                                  round((y+h)*scale_y), kind, label))
                user32.InvalidateRect(window, None, False)
                user32.ShowWindow(window, 4)  # SW_SHOWNOACTIVATE。
                visible = True
                drawn = revision

        @WNDPROC
        def window_proc(window, message, wparam, lparam):
            try:
                if message == 0x113:  # WM_TIMER
                    update(window)
                    return 0
                if message == 0x0f:  # WM_PAINT
                    paint(window)
                    return 0
                if message == 0x14:  # WM_ERASEBKGND，双缓冲统一清背景。
                    return 1
                if message == 0x84:  # WM_NCHITTEST，鼠标穿透。
                    return -1
                if message == 0x02:  # WM_DESTROY
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(window, message, wparam, lparam)
            except (OSError, RuntimeError, ValueError, ctypes.ArgumentError) as error:
                # 防：原生回调出错，记录异常并结束显示。
                self.error = error
                user32.DestroyWindow(window)
                return 0

        try:
            brush = gdi32.CreateSolidBrush(0xff00ff)
            pens = {kind: gdi32.CreatePen(0, 2, color) for kind, color in COLORS.items()}
            # 防：GDI 画笔或透明背景画刷创建失败。
            if not brush or not all(pens.values()):
                raise ctypes.WinError(ctypes.get_last_error())
            window_class = WindowClass(proc=window_proc, instance=instance, name=class_name)
            # 防：Win32 窗口类注册失败。
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            hwnd = user32.CreateWindowExW(0x080800a8, class_name, "货币战争 CLI 识别区域",
                                          0x80000000, 0, 0, 1, 1, None, None, instance, None)
            # 防：原生透明窗口或透明色设置失败。
            if not hwnd or not user32.SetLayeredWindowAttributes(hwnd, 0xff00ff, 0, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            # 防：显示框进入截图，干扰识别。
            if not user32.SetWindowDisplayAffinity(hwnd, 0x11):
                raise ctypes.WinError(ctypes.get_last_error())
            # 防：窗口定时器创建失败。
            if not user32.SetTimer(hwnd, 1, 33, None):
                raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set()
            message = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                # 防：Win32 消息读取失败，停止显示并报告错误。
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    hwnd = None  # WM_DESTROY 已销毁窗口。
                    break
                user32.DispatchMessageW(ctypes.byref(message))
        except (OSError, RuntimeError, ValueError, ctypes.ArgumentError) as error:
            self.error = error
        finally:
            if hwnd is not None:
                user32.DestroyWindow(hwnd)
            if registered:
                user32.UnregisterClassW(class_name, instance)
            for pen in pens.values():
                gdi32.DeleteObject(pen)
            if brush:
                gdi32.DeleteObject(brush)
            self.ready.set()

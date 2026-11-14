"""离线 OCR 试验：对一张图片运行 MaaFramework 文字识别。"""

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
from PIL import Image
from maa.controller import CustomController
from maa.define import LoggingLevelEnum
from maa.pipeline import JOCR, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker

MODEL = Path(__file__).resolve().parent / ".runtime" / "ocr-model"


class ImageController(CustomController):
    """只提供离线图片，不发送输入。"""

    def __init__(self, image):
        self.image = image
        super().__init__()

    def connect(self):
        return True

    def request_uuid(self):
        return "offline-image"

    def screencap(self):
        return self.image

    def unsupported(self, *args):
        return False

    start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = unsupported
    click_key = input_text = key_down = key_up = unsupported


def memory():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                    ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
                    ("peak_paged_pool", ctypes.c_size_t), ("paged_pool", ctypes.c_size_t),
                    ("peak_nonpaged_pool", ctypes.c_size_t), ("nonpaged_pool", ctypes.c_size_t),
                    ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
                    ("private_bytes", ctypes.c_size_t)]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
    # 防：内存计数读取失败时不出统计。
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
                                                    ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return {"working_set_mib": round(counters.working_set / 1024 ** 2, 1),
            "private_mib": round(counters.private_bytes / 1024 ** 2, 1),
            "peak_working_set_mib": round(counters.peak_working_set / 1024 ** 2, 1)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="使用 MaaFramework 离线识别图片中的文字")
    parser.add_argument("image", type=Path, help="待识别的图片")
    parser.add_argument("model", type=Path, help="包含 det.onnx、rec.onnx 和 keys.txt 的模型目录")
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"),
                        default=(0, 0, 0, 0), help="识别区域，按图片像素指定")
    parser.add_argument("--repeat", type=int, default=1, help="对同一张图片重复识别的次数")
    args = parser.parse_args()
    # 防：--repeat 小于 1。
    if args.repeat < 1:
        parser.error("重复次数必须大于零")

    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    started = perf_counter()
    initial_memory = memory()
    resource = Resource()
    if not resource.use_cpu():
        raise RuntimeError("无法设置 CPU 推理")
    # 防：模型文件缺失或与 MaaFramework 不兼容。
    if not resource.post_ocr_model(args.model).wait().succeeded:
        raise RuntimeError(f"OCR 模型加载失败：{args.model}")
    image = np.asarray(Image.open(args.image).convert("RGB"))[:, :, ::-1].copy()
    controller = ImageController(image)
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError("无法连接离线图片控制器")
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("无法初始化 MaaFramework 识别任务")
    loaded = perf_counter()
    loaded_memory = memory()
    runs = []
    for _ in range(args.repeat):
        ocr_started = perf_counter()
        job = tasker.post_recognition(JRecognitionType.OCR, JOCR(roi=tuple(args.roi)), image).wait()
        detail = job.get()
        # 防：识别任务失败或未返回详情。
        if detail is None or not detail.nodes or detail.nodes[0].recognition is None:
            raise RuntimeError("OCR 任务未返回识别详情")
        recognition = detail.nodes[0].recognition
        result = [{"text": item.text, "box": item.box, "score": item.score}
                  for item in recognition.filtered_results]
        runs.append({"texts": result, "ocr_ms": round((perf_counter() - ocr_started) * 1000, 1)})
    print(json.dumps({"result": "success", "runs": runs,
                      "load_ms": round((loaded - started) * 1000, 1),
                      "memory_mib": {"initial": initial_memory, "loaded": loaded_memory,
                                     "after_ocr": memory()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()

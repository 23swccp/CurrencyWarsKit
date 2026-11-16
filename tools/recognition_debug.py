"""把当前操作的识别区域交给可选的调试显示端。"""

from contextlib import contextmanager
from contextvars import ContextVar


_observer = ContextVar("recognition_observer", default=None)


@contextmanager
def trace(observer):
    token = _observer.set(observer)
    try:
        if observer is not None:
            observer.begin()
        yield
    finally:
        try:
            if observer is not None:
                observer.end()
        finally:
            _observer.reset(token)


def focus(image, roi, label, kind="template"):
    observer = _observer.get()
    if observer is not None:
        x, y, width, height = roi
        if width == 0 and height == 0:
            height, width = image.shape[:2]
        observer.mark(image, (x, y, width, height), label, kind)

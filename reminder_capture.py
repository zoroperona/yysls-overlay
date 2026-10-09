"""Exclude reminder windows from capture; fail closed on unsupported systems."""
import ctypes
from ctypes import wintypes
import sys
import threading


class CaptureOccluded(RuntimeError):
    pass


def exclude_window_from_capture(window_id):
    """Tk's ID can name a child HWND; display affinity requires its root wrapper."""
    if sys.platform != 'win32':
        return False
    try:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        hwnd = user32.GetAncestor(window_id, 2)  # GA_ROOT: actual top-level Tk wrapper
        return bool(hwnd and user32.SetWindowDisplayAffinity(hwnd, 0x11))  # WDA_EXCLUDEFROMCAPTURE
    except (AttributeError, OSError):
        return False


class CaptureGuard:
    def __init__(self):
        self._lock = threading.Lock()
        self._windows = {}
        self._generation = 0

    def set_window(self, key, bounds, excluded):
        with self._lock:
            old = self._windows.get(key)
            value = (tuple(bounds), bool(excluded))
            if old != value:
                self._windows[key] = value
                if not excluded or (old is not None and not old[1]):
                    self._generation += 1

    def moving(self, key, bounds):
        # Cover both old and new positions before Tk applies geometry asynchronously.
        with self._lock:
            previous = self._windows.get(key)
            if previous is None or previous[1]:
                return
            old = previous[0]
            union = (min(old[0], bounds[0]), min(old[1], bounds[1]),
                     max(old[2], bounds[2]), max(old[3], bounds[3]))
            self._windows[key] = (union, False)
            self._generation += 1

    def remove(self, key):
        with self._lock:
            previous = self._windows.pop(key, None)
            if previous is not None and not previous[1]:
                self._generation += 1

    def check(self, box=None, generation=None):
        with self._lock:
            if generation is not None and generation != self._generation:
                raise CaptureOccluded('浮层移动中，已跳过本帧检测')
            for bounds, excluded in self._windows.values():
                if excluded:
                    continue
                if box is None or (box[0] < bounds[2] and box[2] > bounds[0]
                                   and box[1] < bounds[3] and box[3] > bounds[1]):
                    raise CaptureOccluded('浮层可被截图捕获，请将浮层移出检测区域')
            return self._generation

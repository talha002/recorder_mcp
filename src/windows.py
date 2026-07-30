import ctypes

import psutil
import pyautogui
import pywintypes
import win32con
import win32gui
import win32process

from src.models import WindowInfo

__all__ = [
    "enumerate_windows",
    "find_window",
    "focus_window",
    "get_client_rect_screen",
    "get_window_rect",
    "init_dpi_awareness",
    "is_valid",
]

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_dpi_initialized = False


def init_dpi_awareness() -> None:
    global _dpi_initialized
    if _dpi_initialized:
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        )
    except OSError:
        pass
    _dpi_initialized = True


def is_valid(hwnd: int) -> bool:
    return bool(win32gui.IsWindow(hwnd))


def _window_title(hwnd: int) -> str:
    try:
        return str(win32gui.GetWindowText(hwnd))
    except pywintypes.error:
        return ""


def _process_name(pid: int) -> str:
    try:
        return str(psutil.Process(pid).name())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""


def _window_process(hwnd: int) -> str:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except pywintypes.error:
        return ""
    return _process_name(pid)


def _build_window_info(hwnd: int) -> WindowInfo:
    return WindowInfo(hwnd=hwnd, title=_window_title(hwnd), process=_window_process(hwnd))


def enumerate_windows(visible_only: bool = True) -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    def _callback(hwnd: int, _: None) -> bool:
        if visible_only and not win32gui.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title:
            return True
        windows.append(WindowInfo(hwnd=hwnd, title=title, process=_window_process(hwnd)))
        return True

    win32gui.EnumWindows(_callback, None)
    return windows


def find_window(
    hwnd: int | None = None,
    title: str | None = None,
    process: str | None = None,
) -> WindowInfo | None:
    provided = [v is not None for v in (hwnd, title, process)]
    if sum(provided) != 1:
        raise ValueError("exactly one of 'hwnd', 'title', 'process' must be provided")

    if hwnd is not None:
        if not is_valid(hwnd):
            return None
        return _build_window_info(hwnd)

    candidates = enumerate_windows(visible_only=True)
    if title is not None:
        needle = title.casefold()
        for info in candidates:
            if needle in info.title.casefold():
                return info
        return None

    assert process is not None
    needle = process.casefold()
    for info in candidates:
        if info.process.casefold() == needle:
            return info
    return None


def get_window_rect(hwnd: int) -> dict[str, int]:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def get_client_rect_screen(hwnd: int) -> dict[str, int]:
    _, _, right, bottom = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return {"left": left, "top": top, "width": right, "height": bottom}


def focus_window(hwnd: int) -> None:
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    rect = get_window_rect(hwnd)
    center_x = rect["left"] + rect["width"] // 2
    center_y = rect["top"] + rect["height"] // 2
    pyautogui.click(center_x, center_y)

import ctypes
from collections.abc import Callable
from typing import Any

import psutil
import pyautogui
import pytest
import win32con
import win32gui
import win32process

from src import windows


class _FakePsutilProcess:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


@pytest.fixture
def fake_win32(monkeypatch: pytest.MonkeyPatch) -> dict[int, dict[str, Any]]:
    fake: dict[int, dict[str, Any]] = {
        100: {"title": "Command Prompt", "pid": 1000, "visible": True},
        200: {"title": "Notepad", "pid": 2000, "visible": True},
        300: {"title": "cmd.exe - hidden", "pid": 1000, "visible": False},
        400: {"title": "", "pid": 3000, "visible": True},
    }
    proc_names = {1000: "cmd.exe", 2000: "notepad.exe", 3000: "svchost.exe"}

    def enum_windows(callback: Callable[[int, None], bool], arg: None) -> None:
        for hwnd in fake:
            callback(hwnd, arg)

    monkeypatch.setattr(win32gui, "EnumWindows", enum_windows)
    monkeypatch.setattr(win32gui, "IsWindowVisible", lambda hwnd: bool(fake[hwnd]["visible"]))
    monkeypatch.setattr(win32gui, "GetWindowText", lambda hwnd: str(fake[hwnd]["title"]))
    monkeypatch.setattr(win32gui, "IsWindow", lambda hwnd: int(hwnd in fake))
    monkeypatch.setattr(
        win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (1, int(fake[hwnd]["pid"])),
    )
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakePsutilProcess(proc_names[pid]))
    return fake


def test_enumerate_windows_visible_only(fake_win32: dict[int, dict[str, Any]]) -> None:
    result = windows.enumerate_windows()
    assert [(w.hwnd, w.title, w.process) for w in result] == [
        (100, "Command Prompt", "cmd.exe"),
        (200, "Notepad", "notepad.exe"),
    ]


def test_enumerate_windows_includes_hidden_and_skips_untitled(
    fake_win32: dict[int, dict[str, Any]],
) -> None:
    result = windows.enumerate_windows(visible_only=False)
    assert [w.hwnd for w in result] == [100, 200, 300]


def test_find_window_by_hwnd(fake_win32: dict[int, dict[str, Any]]) -> None:
    info = windows.find_window(hwnd=200)
    assert info is not None
    assert info.title == "Notepad"
    assert info.process == "notepad.exe"


def test_find_window_by_invalid_hwnd_returns_none(
    fake_win32: dict[int, dict[str, Any]],
) -> None:
    assert windows.find_window(hwnd=999) is None


def test_find_window_by_title_substring_case_insensitive(
    fake_win32: dict[int, dict[str, Any]],
) -> None:
    info = windows.find_window(title="command PROMPT")
    assert info is not None
    assert info.hwnd == 100


def test_find_window_by_process_case_insensitive(
    fake_win32: dict[int, dict[str, Any]],
) -> None:
    info = windows.find_window(process="CMD.EXE")
    assert info is not None
    assert info.hwnd == 100


def test_find_window_not_found(fake_win32: dict[int, dict[str, Any]]) -> None:
    assert windows.find_window(title="does not exist") is None
    assert windows.find_window(process="missing.exe") is None


def test_find_window_requires_exactly_one_criterion(
    fake_win32: dict[int, dict[str, Any]],
) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        windows.find_window()
    with pytest.raises(ValueError, match="exactly one"):
        windows.find_window(hwnd=1, title="x")


def test_get_window_rect_returns_mss_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda hwnd: (10, 20, 210, 120))
    assert windows.get_window_rect(1) == {"left": 10, "top": 20, "width": 200, "height": 100}


def test_get_client_rect_screen_returns_mss_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win32gui, "GetClientRect", lambda hwnd: (0, 0, 190, 90))
    monkeypatch.setattr(win32gui, "ClientToScreen", lambda hwnd, pt: (15, 45))
    assert windows.get_client_rect_screen(1) == {
        "left": 15,
        "top": 45,
        "width": 190,
        "height": 90,
    }


def test_focus_window_restores_before_click(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        win32gui, "ShowWindow", lambda hwnd, cmd: calls.append(("show", cmd))
    )
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda hwnd: (0, 0, 200, 100))
    monkeypatch.setattr(pyautogui, "click", lambda x, y: calls.append(("click", (x, y))))
    windows.focus_window(1)
    assert calls == [("show", win32con.SW_RESTORE), ("click", (100, 50))]


def test_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win32gui, "IsWindow", lambda hwnd: 1 if hwnd == 5 else 0)
    assert windows.is_valid(5) is True
    assert windows.is_valid(6) is False


def test_init_dpi_awareness_called_once_and_tolerant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    class _User32:
        @staticmethod
        def SetProcessDpiAwarenessContext(ctx: Any) -> int:
            calls.append(ctx)
            if len(calls) > 1:
                raise OSError("already set")
            return 1

    monkeypatch.setattr(ctypes, "windll", type("WinDLL", (), {"user32": _User32})())
    monkeypatch.setattr(windows, "_dpi_initialized", False)

    windows.init_dpi_awareness()
    windows.init_dpi_awareness()
    assert len(calls) == 1

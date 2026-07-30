import time
from typing import Any

import pytest
from pyautogui import FailSafeException

from src.keyboard import InvalidWindowError
from src.ocr import WindowMinimizedError
from src.services import command_runner


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    log: list[tuple[Any, ...]] = []
    monkeypatch.setattr(command_runner, "focus_window", lambda hwnd: log.append(("focus", hwnd)))
    monkeypatch.setattr(
        command_runner,
        "type_into_window",
        lambda hwnd, text, press_enter=False: log.append(("type", hwnd, text, press_enter)),
    )
    monkeypatch.setattr(time, "sleep", lambda s: log.append(("sleep", s)))

    def _capture(hwnd: int, client_area_only: bool = True) -> str:
        log.append(("ocr", hwnd, client_area_only))
        return "hello\r\n"

    monkeypatch.setattr(command_runner, "capture_window_text", _capture)
    return log


def test_run_command_sync_call_order(calls: list[tuple[Any, ...]]) -> None:
    result = command_runner.run_command_sync(100, "echo hello", 1.5)
    assert calls == [
        ("focus", 100),
        ("type", 100, "echo hello", True),
        ("sleep", 1.5),
        ("ocr", 100, True),
    ]
    assert result.output == "hello\r\n"
    assert result.error is None


def test_run_command_sync_wait_s_passed_to_sleep(calls: list[tuple[Any, ...]]) -> None:
    command_runner.run_command_sync(100, "dir", 3.25)
    assert ("sleep", 3.25) in calls


def test_run_command_sync_returns_ocr_text_verbatim(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[Any, ...]]
) -> None:
    raw = "C:\\> echo hello\r\nhello\r\n\r\nC:\\>\r\n"
    monkeypatch.setattr(
        command_runner,
        "capture_window_text",
        lambda hwnd, client_area_only=True: raw,
    )
    result = command_runner.run_command_sync(100, "echo hello", 1.0)
    assert result.output == raw


def test_run_command_sync_focus_failure_short_circuits(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[Any, ...]]
) -> None:
    def _boom(hwnd: int) -> None:
        raise FailSafeException("window vanished")

    monkeypatch.setattr(command_runner, "focus_window", _boom)
    result = command_runner.run_command_sync(100, "echo hello", 1.0)
    assert result.output == ""
    assert result.error is not None and "window vanished" in result.error
    assert calls == []


def test_run_command_sync_type_failure_short_circuits(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[Any, ...]]
) -> None:
    def _boom(hwnd: int, text: str, press_enter: bool = False) -> None:
        raise InvalidWindowError(f"invalid window: hwnd {hwnd}")

    monkeypatch.setattr(command_runner, "type_into_window", _boom)
    result = command_runner.run_command_sync(100, "echo hello", 1.0)
    assert result.output == ""
    assert result.error is not None and "invalid window" in result.error
    assert calls == [("focus", 100)]


def test_run_command_sync_capture_failure_short_circuits(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[Any, ...]]
) -> None:
    def _boom(hwnd: int, client_area_only: bool = True) -> str:
        raise WindowMinimizedError("window minimized")

    monkeypatch.setattr(command_runner, "capture_window_text", _boom)
    result = command_runner.run_command_sync(100, "echo hello", 1.0)
    assert result.output == ""
    assert result.error is not None and "window minimized" in result.error
    assert calls == [
        ("focus", 100),
        ("type", 100, "echo hello", True),
        ("sleep", 1.0),
    ]

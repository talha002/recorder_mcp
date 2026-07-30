import time
from typing import Any

import pyautogui
import pytest

from src import keyboard


@pytest.fixture
def calls(
    monkeypatch: pytest.MonkeyPatch, mock_pyautogui: list[tuple[Any, ...]]
) -> list[tuple[Any, ...]]:
    monkeypatch.setattr(keyboard, "is_valid", lambda hwnd: True)
    monkeypatch.setattr(
        keyboard, "focus_window", lambda hwnd: mock_pyautogui.append(("focus", hwnd))
    )
    monkeypatch.setattr(time, "sleep", lambda s: mock_pyautogui.append(("sleep", s)))
    return mock_pyautogui


def test_type_into_window_focus_delay_write_enter_order(
    calls: list[tuple[Any, ...]],
) -> None:
    keyboard.type_into_window(100, "echo hello", press_enter=True)
    assert calls == [
        ("focus", 100),
        ("sleep", keyboard.SETTLE_DELAY_S),
        ("write", "echo hello", keyboard.TYPE_INTERVAL_S),
        ("press", "enter"),
    ]


def test_type_into_window_press_enter_false_skips_enter(
    calls: list[tuple[Any, ...]],
) -> None:
    keyboard.type_into_window(100, "echo hello")
    assert calls == [
        ("focus", 100),
        ("sleep", keyboard.SETTLE_DELAY_S),
        ("write", "echo hello", keyboard.TYPE_INTERVAL_S),
    ]


def test_type_into_window_invalid_hwnd_raises_before_typing(
    monkeypatch: pytest.MonkeyPatch, mock_pyautogui: list[tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(keyboard, "is_valid", lambda hwnd: False)
    monkeypatch.setattr(
        keyboard, "focus_window", lambda hwnd: mock_pyautogui.append(("focus", hwnd))
    )
    with pytest.raises(keyboard.InvalidWindowError, match="invalid window"):
        keyboard.type_into_window(999, "echo hello")
    assert mock_pyautogui == []


def test_write_interval_passed_through(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[Any, ...]]
) -> None:
    assert keyboard.TYPE_INTERVAL_S == 0.01
    monkeypatch.setattr(keyboard, "TYPE_INTERVAL_S", 0.05)
    keyboard.type_into_window(100, "x")
    assert ("write", "x", 0.05) in calls


def test_failsafe_stays_enabled() -> None:
    assert pyautogui.FAILSAFE is True

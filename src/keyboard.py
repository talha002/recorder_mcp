"""Focus a window and type text into it via pyautogui (sync).

Named ``keyboard.py`` to avoid clashing with the stdlib ``typing`` module.

pyautogui safety: ``pyautogui.FAILSAFE`` is kept enabled (the default) —
moving the mouse into a screen corner raises ``pyautogui.FailSafeException``
and aborts typing mid-stream.

Limitation: ``pyautogui.write`` only supports ASCII characters; non-ASCII
text is unsupported (a clipboard fallback is out of MVP scope).
"""

import time

import pyautogui

from src.windows import focus_window, is_valid

__all__ = [
    "SETTLE_DELAY_S",
    "TYPE_INTERVAL_S",
    "InvalidWindowError",
    "type_into_window",
]

SETTLE_DELAY_S = 0.15
TYPE_INTERVAL_S = 0.01


class InvalidWindowError(Exception):
    """Raised when the target hwnd is not a valid window."""


def type_into_window(hwnd: int, text: str, press_enter: bool = False) -> None:
    if not is_valid(hwnd):
        raise InvalidWindowError(f"invalid window: hwnd {hwnd}")
    focus_window(hwnd)
    time.sleep(SETTLE_DELAY_S)
    pyautogui.write(text, interval=TYPE_INTERVAL_S)
    if press_enter:
        pyautogui.press("enter")

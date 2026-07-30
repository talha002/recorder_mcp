"""Compose focus -> type+Enter -> wait -> OCR into one sync call (Epic 5).

Window-agnostic convenience composite: works for any window that accepts
keyboard input (cmd, PowerShell, Python REPL, ssh session, ...). Any
primitive failure short-circuits into ``CommandResult.error`` — no retries
in MVP-1, and no output post-processing (prompt stripping is a future
enhancement). Blocking by design; callers wrap it in ``asyncio.to_thread``.
"""

import time
from dataclasses import dataclass

from mss.exception import ScreenShotError
from pyautogui import FailSafeException
from pytesseract import TesseractError
from pywintypes import error as PyWinError

from src.keyboard import InvalidWindowError, type_into_window
from src.ocr import WindowMinimizedError, capture_window_text
from src.windows import focus_window

__all__ = ["CommandResult", "run_command_sync"]

_FOCUS_ERRORS = (PyWinError, FailSafeException)
_TYPE_ERRORS = (InvalidWindowError, PyWinError, FailSafeException)
_CAPTURE_ERRORS = (
    WindowMinimizedError,
    PyWinError,
    ScreenShotError,
    TesseractError,
    OSError,
    RuntimeError,
)


@dataclass(frozen=True)
class CommandResult:
    output: str = ""
    error: str | None = None


def run_command_sync(hwnd: int, command: str, wait_s: float) -> CommandResult:
    try:
        focus_window(hwnd)
    except _FOCUS_ERRORS as exc:
        return CommandResult(error=f"focus failed: {exc}")
    try:
        type_into_window(hwnd, command, press_enter=True)
    except _TYPE_ERRORS as exc:
        return CommandResult(error=f"type failed: {exc}")
    time.sleep(wait_s)
    try:
        return CommandResult(output=capture_window_text(hwnd, client_area_only=True))
    except _CAPTURE_ERRORS as exc:
        return CommandResult(error=f"capture failed: {exc}")

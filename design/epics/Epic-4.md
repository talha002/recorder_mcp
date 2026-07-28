# Epic 4: Keyboard Input & OCR

> Spec: [`design/specs/mvp-1.md`](../specs/mvp-1.md) — Build Order step 4 (first half)
> Depends on: Epic 1, Epic 2 (focus_window, geometry)
> Goal: type text into any window and read any window's text via OCR — the two primitives that `run_command` (Epic 5) composes.

## Overview

Two sync modules: `src/keyboard.py` (focus + pyautogui typing; named `keyboard.py` to avoid clashing with the stdlib `typing`) and `src/ocr.py` (region grab → preprocessing → pytesseract). Two MCP tools: `type_text`, `capture_output`.

## Architecture

### Keyboard (`src/keyboard.py`, sync)

- `type_into_window(hwnd, text, press_enter=False)`:
  1. `focus_window(hwnd)` from `windows.py` (`SW_RESTORE` + click center — handles foreground restrictions).
  2. Small settle delay (~100–200 ms, configurable) so the target app processes focus.
  3. `pyautogui.write(text, interval=...)`; optional `pyautogui.press("enter")`.
- pyautogui safety: keep `FAILSAFE` enabled (mouse to corner aborts) but document it; set a sane default `interval` (e.g. 0.01 s) — terminal apps drop keystrokes at interval 0.
- Guards: `IsWindow` before focusing; surface failure as `error`.

### OCR (`src/ocr.py`, sync)

- `capture_window_text(hwnd, client_area_only=True) -> str`:
  1. Rect via `get_client_rect_screen` (default) or `get_window_rect`.
  2. mss grab → PIL image.
  3. Preprocess for dark themes (cmd, VS Code dark):
     - upscale 2× (Lanczos),
     - grayscale,
     - **invert-if-dark** (mean luminance < 128 → invert),
     - Otsu threshold (via numpy; no OpenCV dependency).
  4. `pytesseract.image_to_string(processed)` with `pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd`.
- Skip-grab guards: `IsIconic` → return `error="window minimized"`.
- Preprocessing pipeline implemented as small composable functions (`upscale`, `to_grayscale`, `invert_if_dark`, `otsu_threshold`) so thresholds are unit-testable.

### MCP wiring

Both endpoints run their sync core via `asyncio.to_thread` after resolving the target window through Epic 2's `find_window`.

## MCP Tools

### `type_text` — POST `/type-text`

Focus any window → `pyautogui.write` (+ optional Enter). Response: `{error}`.

### `capture_output` — POST `/capture-output`

Grab window region → preprocess → OCR → `{text, error}`.

## Stories

| ID | Title | Summary |
|---|---|---|
| E4.S1 | `keyboard.py` — focus + typing | `type_into_window` with settle delay, interval, guards |
| E4.S2 | `type_text` endpoint | Route + resolution + `asyncio.to_thread` |
| E4.S3 | `ocr.py` — grab + preprocessing + pytesseract | Composable pipeline, invert-if-dark, Otsu, tesseract_cmd wiring |
| E4.S4 | `capture_output` endpoint | Route + resolution + minimized guard |

## Acceptance Criteria (epic-level)

- `type_text` into a real cmd window types the exact string; `press_enter=true` executes it.
- Typing works when the server process is not the foreground process (focus recipe reliable).
- `capture_output` on cmd after `echo hello` returns text containing `hello`.
- OCR works on both light and dark themed terminals (invert-if-dark verified on both).
- Minimized window → `error="window minimized"`, no exception.

## Test Coverage (epic-level)

- `test_keyboard.py` — mocked pyautogui/win32: call order (focus → delay → write → enter), `IsWindow` guard, interval passthrough.
- `test_ocr.py` — synthetic PIL images (light text/dark bg and inverse): invert-if-dark decision, Otsu output type/range, pipeline composition; mocked pytesseract asserting `image_to_string` receives preprocessed image and `tesseract_cmd` set from settings.
- `test_main.py` additions — both endpoints with mocked cores; window-not-found; minimized.
- Manual: dark-theme cmd + light-theme notepad OCR sanity.

## Dependencies

- Epic 1 (models, settings `TESSERACT_CMD`, auth).
- Epic 2 (`find_window`, `focus_window`, rect helpers, `is_valid`).

## Risks / Notes

- OCR accuracy depends on font size/theme — preprocessing mitigates but small fonts may misread; document tuning knobs (upscale factor, threshold) in CODEBASE_MAP.md.
- pyautogui pulls in pywin32 — already required by Epic 2.

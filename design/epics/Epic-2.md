# Epic 2: Window Management

> Spec: [`design/specs/mvp-1.md`](../specs/mvp-1.md) — Build Order step 2
> Depends on: Epic 1
> Goal: enumerate, resolve, launch, and focus any desktop window generically (hwnd / title substring / process name) — verified against a real cmd window.

## Overview

`src/windows.py` is the single sync module wrapping `win32gui`/`win32process`/`win32con`. Every later epic (recording, typing, OCR, command runner) resolves windows through it. Nothing here is cmd-specific. Two MCP tools ship in this epic: `list_windows` and `open_app`.

## Architecture

### DPI awareness (startup)

`SetProcessDpiAwarenessContext(-4)` (per-monitor v2) via ctypes at server startup — **before** any coordinate call. This makes `GetWindowRect`, mss, and pyautogui agree on scaled displays. Called once from `main.py` startup hook (implemented here because Epic 2 owns window coordinates; the hook is registered in Epic 1's startup section).

### Window resolution contract

All targeting accepts exactly one of:

- `hwnd: int` — used directly after `IsWindow` validation.
- `title: str` — case-insensitive substring match against visible window titles; first match wins; ambiguous matches documented in response.
- `process: str` — process name (e.g. `cmd.exe`, case-insensitive) resolved via PID → hwnd enumeration.

Resolution returns a `WindowInfo { hwnd, title, process }`. Not-found → `error: "window not found: <criteria>"` (HTTP 200, per models pattern).

### Geometry helpers

- `get_window_rect(hwnd)` → screen rect via `win32gui.GetWindowRect` → `{left, top, width, height}` (mss-compatible dict).
- `get_client_rect_screen(hwnd)` → `GetClientRect` + `ClientToScreen` for client-area-only capture (excludes title bar/borders). Used by recorder (Epic 3) and OCR (Epic 4) when `client_area_only=true`.

### Foreground restrictions

Background processes can't always `SetForegroundWindow`. Reliable focus recipe: `ShowWindow(hwnd, SW_RESTORE)` → `pyautogui.click` at window center. Encapsulated as `focus_window(hwnd)`; reused by `type_text`, `run_command`, and `open_app`.

## Module Specification: `src/windows.py` (sync)

| Function | Behavior |
|---|---|
| `init_dpi_awareness() -> None` | ctypes `SetProcessDpiAwarenessContext(-4)`; tolerant if already set |
| `enumerate_windows(visible_only=True) -> list[WindowInfo]` | `EnumWindows` + `GetWindowTextW` + `GetWindowThreadProcessId` + `psutil`/win32 process name |
| `find_window(hwnd=None, title=None, process=None) -> WindowInfo \| None` | resolution contract above |
| `get_window_rect(hwnd) -> dict` | mss region dict of full window |
| `get_client_rect_screen(hwnd) -> dict` | mss region dict of client area |
| `focus_window(hwnd) -> None` | `SW_RESTORE` + click center |
| `is_valid(hwnd) -> bool` | `IsWindow` guard |

## MCP Tools

### `list_windows` — POST `/list-windows`

- `EnumWindows` → `[{hwnd, title, process}]`, visible only.
- Optional `title_filter` / `process_filter` (case-insensitive substring).
- Runs via `asyncio.to_thread`.

### `open_app` — POST `/open-app`

- `subprocess.Popen([path, *args])` → poll for a window owned by the new PID (timeout configurable, default ~10 s) → `focus_window` → return `{hwnd, pid, title}`.
- Failure modes: executable not found, timeout waiting for window → `error` populated.

## Stories

| ID | Title | Summary |
|---|---|---|
| E2.S1 | `windows.py` — enumeration, resolution, geometry, focus, DPI | All functions in the table above |
| E2.S2 | `list_windows` endpoint | Route + filters + `asyncio.to_thread` |
| E2.S3 | `open_app` endpoint | Popen → hwnd resolution by PID/title → foreground |

## Acceptance Criteria (epic-level)

- `list_windows` returns the real visible windows of the session, incl. hwnd/title/process, with working filters.
- `open_app("cmd.exe")` launches cmd, returns its hwnd, and the window is foregrounded.
- On a 125%/150% scaled display, `get_window_rect` coordinates match physical pixels (verified manually against a screenshot tool).
- Not-found criteria yield `error` with HTTP 200; never an unhandled exception.

## Test Coverage (epic-level)

- `test_windows.py` — win32 mocks: enumeration parsing, title/process/hwnd resolution precedence, not-found path, rect→mss-dict conversion, focus sequence order (`SW_RESTORE` before click).
- `test_main.py` additions — `list_windows` with mocked `enumerate_windows`; `open_app` with mocked `Popen` + `find_window` (success, executable-missing, timeout).
- Manual: `open_app` on real cmd; `list_windows` shows it.

## Dependencies

- Epic 1 (app, models, auth, settings).
- `WindowInfo` and targeting union from `models.py` (E1.S3).

## Risks / Notes

- Process-name lookup: prefer `psutil` if already transitive, else `QueryFullProcessImageNameW` — decide in E2.S1 and record in CODEBASE_MAP.md.
- Elevated vs non-elevated: server cannot foreground/inspect elevated windows unless run elevated — document in README (Epic 6).

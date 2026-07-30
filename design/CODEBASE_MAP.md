# CODEBASE MAP — my_record_agent

> **Maintenance rule:** this file MUST be updated in the same PR/commit that closes any issue.
> Code builder agents: read this first for the big picture, then the relevant `design/epics/Epic-N.md` for detail.

## Project

General-purpose window-recording MCP server. FastAPI + fastapi-mcp over streamable HTTP at `/mcp`. Records **any** desktop window (hwnd / title / process), injects keystrokes, OCRs window content. Spec: `design/specs/mvp-1.md`.

## Architecture Invariants

- Tools = `@app.post` endpoints with `operation_id`; `FastApiMCP(app)` + `mount_http()` at top of `main.py`; `setup_server()` after all routes.
- All win32/mss/pyautogui/pytesseract code is **sync**, called via `asyncio.to_thread(...)`.
- Recording loop = dedicated `threading.Thread` + `threading.Event` stop flag (never an asyncio task).
- Responses use the `error: str | None` pattern (HTTP 200 + `error` on tool-level failure; 401 for auth).
- DPI awareness (`SetProcessDpiAwarenessContext(-4)`) set once at startup before any coordinate call.
- Run: `uvicorn src.main:app` (no console scripts, no Docker).

## Module Map

| Module | Status | Responsibility | Used by |
|---|---|---|---|
| `src/main.py` | implemented | FastAPI app, FastApiMCP mount, `/health`, Tesseract startup check, `list_windows` + `open_app` + `start_recording` live, 4 placeholder tool routes (DPI hook pending Epic 2) | MCP clients |
| `src/config.py` | implemented | `Settings` (pydantic-settings) + `settings` singleton | all modules |
| `src/auth.py` | implemented | `verify_mcp_token` Bearer dependency | every tool route |
| `src/models.py` | implemented | All request/response Pydantic models, `WindowInfo` | main, services |
| `src/windows.py` | implemented | win32: enumerate / find / find-by-PID / rect / client-rect / focus / DPI init; process names via `psutil` | recorder, keyboard, ocr, command_runner, main |
| `src/recorder.py` | implemented | `RecordingManager` + capture-loop thread + PyAV PTS encoding (mss grab, locked geometry, h264/yuv420p, flush-in-finally, zero-frames cleanup) | main (start/stop) |
| `src/keyboard.py` | planned | `type_into_window` (focus + pyautogui) | command_runner, main |
| `src/ocr.py` | planned | region grab → preprocess (2×, grayscale, invert-if-dark, Otsu) → pytesseract | command_runner, main |
| `src/services/command_runner.py` | planned | `run_command_sync`: focus → type+Enter → wait → OCR | main |

## MCP Tools (7)

| Tool | Route | Core module | Request key fields | Response |
|---|---|---|---|---|
| `list_windows` | POST `/list-windows` | windows.py | `title_filter?`, `process_filter?` | `windows[]`, `error` |
| `open_app` | POST `/open-app` | windows.py | `path`, `args?`, `wait_timeout_s?` | `hwnd`, `pid`, `title`, `error` |
| `start_recording` | POST `/start-recording` | recorder.py | hwnd\|title\|process, `client_area_only?`, `fps?` | `session_id`, `error` |
| `stop_recording` | POST `/stop-recording` | recorder.py | `session_id` | `mp4_path`, `duration_s`, `error` |
| `type_text` | POST `/type-text` | keyboard.py | hwnd\|title\|process, `text`, `press_enter?` | `error` |
| `capture_output` | POST `/capture-output` | ocr.py | hwnd\|title\|process | `text`, `error` |
| `run_command` | POST `/run-command` | services/command_runner.py | hwnd\|title\|process, `command`, `wait_s?` | `output`, `error` |

## Data Flow

- **Recording:** endpoint → `find_window` → `RecordingManager.start` → thread: `GetWindowRect`/`GetClientRect`+`ClientToScreen` → mss grab → PIL (resize to locked geometry) → PyAV h264/yuv420p CRF23, `pts = int(elapsed * fps)` → MP4 in `OUTPUT_DIR`.
- **Command:** endpoint → `find_window` → `focus_window` (SW_RESTORE + click center) → `pyautogui.write` + Enter → sleep `wait_s` → mss grab client area → preprocess → `pytesseract.image_to_string` → text.
- **OCR:** endpoint → `find_window` → grab → preprocess → pytesseract → text.

## Configuration (`.env`)

| Key | Default | Purpose |
|---|---|---|
| `MCP_API_TOKEN` | — (required) | Bearer token for all tool endpoints |
| `TESSERACT_CMD` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Tesseract binary path |
| `OUTPUT_DIR` | `./recordings` | MP4 output directory |
| `FPS` | `30` | Recording frame rate / PTS time base |
| `HOST` | `127.0.0.1` | Uvicorn bind host |
| `PORT` | `8000` | Uvicorn bind port |

## External Dependencies

- Tesseract OCR binary (UB Mannheim installer) — server fails fast at startup if missing.
- Interactive Windows session (no Docker / RDP-locked session).
- `av` wheel bundles FFmpeg — no system FFmpeg needed.

## Epics

| Epic | File | Scope |
|---|---|---|
| 1 | `design/epics/Epic-1.md` | Foundation & MCP skeleton (config, models, auth, main) |
| 2 | `design/epics/Epic-2.md` | Window management (windows.py, list_windows, open_app) |
| 3 | `design/epics/Epic-3.md` | Recording engine (recorder.py, start/stop_recording) |
| 4 | `design/epics/Epic-4.md` | Keyboard & OCR (keyboard.py, ocr.py, type_text, capture_output) |
| 5 | `design/epics/Epic-5.md` | Command orchestration (command_runner, run_command) |
| 6 | `design/epics/Epic-6.md` | Testing, MCP handshake, manual E2E, README |

## Change Log

- 2026-07-30 — E3.S2: `start_recording` endpoint implemented (window resolution via `find_window` in `asyncio.to_thread`; `error="window not found: <target>"` on miss; `manager.start(hwnd, client_area_only, fps or settings.fps)`; `AlreadyRecordingError` → `error="already recording"`); 5 endpoint tests added to `tests/test_main.py`.

- 2026-07-30 — E3.S1: `src/recorder.py` implemented (`RecordingSession`/`RecordingManager`/`StopResult`, module singleton `manager`); duplicate-hwnd guard via `AlreadyRecordingError`; capture loop with `IsWindow`/`IsIconic` guards, per-frame rect re-read (follows moves), geometry locked even-rounded at first frame, PIL resize to locked dims on window resize; h264/yuv420p CRF 23 preset fast, `pts = int(elapsed * fps)` with dedup for strict monotonicity; encoder flush + container close in `finally`; zero-frames → file deleted + `error="no frames captured"`; `mss.MSS()` used (`mss.mss()` deprecated in mss 10.2); `tests/test_recorder.py` added (12 tests, mocked win32/mss/av).
- 2026-07-30 — E2.S3: `open_app` endpoint implemented (`Popen` → poll for visible window by PID via new `find_window_by_pid`, fall back to exe-stem title match → `focus_window`; poll interval 100 ms; on timeout the child process is **left running** and `error` says so); endpoint tests + `find_window_by_pid` unit tests added.

- 2026-07-30 — E2.S2: `list_windows` endpoint implemented (`enumerate_windows` via `asyncio.to_thread`, case-insensitive `title_filter`/`process_filter`, combinable); endpoint tests added to `tests/test_main.py`.
- 2026-07-30 — E2.S1: `src/windows.py` implemented (DPI init guard, enumeration, hwnd/title/process resolution, mss-dict rect helpers, SW_RESTORE+click focus); `psutil` chosen for PID→process-name lookup; `pywin32` + `psutil` added to deps.
- 2026-07-30 — E1.S5: `src/main.py` implemented (FastApiMCP at `/mcp`, `/health`, Tesseract startup check, 7 placeholder routes); `mcp` pinned `<2.0.0` (fastapi-mcp 0.4.0 incompatibility with mcp 2.x).
- 2026-07-28 — Initial map created from `design/specs/mvp-1.md` (all modules `planned`).

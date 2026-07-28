# MVP-1: `my_record_agent` — General-Purpose Window-Recording MCP Server

## Goal

An MCP server that records **any** desktop window — cmd, VS Code, browsers, arbitrary apps — with optional keystroke injection and OCR-based text capture. Nothing is cmd-specific; all tools address windows generically by `hwnd`, title substring, or process name. Architecture and folder pattern fully mirror `D:\yazilim\news_agent` (FastAPI + fastapi-mcp over HTTP). Capture internals borrow proven patterns (PyAV PTS-based encoding, mss region grab) from `D:\yazilim\video_gen\devstudio` without copying its AGPL-licensed code wholesale.

Example workflow (cmd — one of many possible targets):

1. Open cmd (via `open_app`)
2. Start recording the cmd window (`start_recording` by hwnd/title)
3. Type commands and capture their outputs (`run_command`)
4. Stop recording → MP4 of the window + captured text output

Other scenarios use the same primitives: record VS Code while editing, record a browser during a demo, screenshot-and-OCR any app.

## Stack

- Python ≥ 3.13
- **FastAPI + fastapi-mcp** — endpoints become MCP tools via `operation_id`
- **uvicorn** — streamable HTTP transport at `/mcp`
- **mss** — region-based screen capture
- **av** (bundled FFmpeg) — H.264 encoding
- **pillow / numpy** — frame conversion
- **pyautogui** — keystroke injection (pulls in `pywin32` for window APIs)
- **pytesseract** — OCR of window screenshots
- **pydantic-settings** — configuration
- pytest + pytest-asyncio, ruff, mypy (strict) — tooling

## External Prerequisite

Tesseract OCR binary must be installed on Windows (UB Mannheim installer). Path configurable via the `TESSERACT_CMD` env var; default `C:\Program Files\Tesseract-OCR\tesseract.exe`. Server fails with a clear error message if the binary is missing.

## Architecture (mirroring news_agent)

- **Framework:** FastAPI + `fastapi-mcp` — tools are `@app.post` endpoints with `operation_id`; `FastApiMCP(app)` + `mount_http()` at top of `main.py`, `setup_server()` **after** all routes are registered.
- **Transport:** streamable HTTP at `/mcp` via uvicorn; Bearer-token auth as a FastAPI dependency on every tool endpoint (`MCP_API_TOKEN` in `.env`); `/health` excluded from MCP with `include_in_schema=False`.
- **Sync core:** all win32/mss/pyautogui/pytesseract code is synchronous, called via `asyncio.to_thread(...)`; the recording loop runs as a **dedicated thread** with a `threading.Event` stop flag (not an asyncio task).
- **No console scripts;** run via `uvicorn src.main:app`. Docker omitted — desktop capture requires an interactive Windows session.

## Project Structure

```
my_record_agent/
├── pyproject.toml          # setuptools, py-modules=["src"], ruff(100)/mypy strict/pytest cfg
├── .env.example            # MCP_API_TOKEN, TESSERACT_CMD, OUTPUT_DIR, FPS, HOST, PORT
├── design/specs/mvp-1.md
├── src/
│   ├── __init__.py
│   ├── main.py             # FastAPI app + FastApiMCP + 7 tool endpoints
│   ├── config.py           # Settings singleton (pydantic-settings, .env)
│   ├── auth.py             # verify_mcp_token dependency
│   ├── models.py           # All Pydantic request/response models (error: str | None pattern)
│   ├── windows.py          # win32gui: enumerate/find/rect/focus (sync)
│   ├── recorder.py         # RecordingManager + capture-loop thread (sync core)
│   ├── keyboard.py         # focus + pyautogui typing (sync; named to avoid stdlib typing clash)
│   ├── ocr.py              # region grab → preprocess → pytesseract (sync)
│   └── services/
│       ├── __init__.py
│       └── command_runner.py   # run_command orchestration: type → wait → OCR
└── tests/
    ├── conftest.py         # TestClient, auth_headers, mock fixtures
    ├── test_main.py        # endpoints + real MCP handshake on /mcp
    └── test_<module>.py    # one per module: auth, config, models, windows, recorder, keyboard, ocr
```

## MCP Tools (7)

Each tool is a POST route whose `operation_id` is the tool name; Pydantic request/response models live in `src/models.py` and follow the `error: str | None` response pattern.

| Tool (`operation_id`) | Route | Behavior |
|---|---|---|
| `list_windows` | POST `/list-windows` | `EnumWindows` → `[{hwnd, title, process}]`, visible only; optional title/process filter |
| `open_app` | POST `/open-app` | Launch any executable via `subprocess.Popen` → resolve hwnd by PID/title → foreground |
| `start_recording` | POST `/start-recording` | Spawns capture thread for any window (hwnd \| title \| process); returns `session_id` |
| `stop_recording` | POST `/stop-recording` | Sets stop event, joins thread, flushes encoder → MP4 path + duration |
| `type_text` | POST `/type-text` | Focus any window → `pyautogui.write` (+ optional Enter) |
| `capture_output` | POST `/capture-output` | Grab any window's region → OCR → text |
| `run_command` | POST `/run-command` | Convenience composite for terminal-like windows: focus → type + Enter → wait → OCR → output text |

All tools are window-agnostic; `run_command` merely composes the primitives and works for any window that accepts keyboard input.

## Capture Loop

Approach adapted (not copied verbatim) from devstudio's monitor-based loop.

- Per frame: `win32gui.GetWindowRect(hwnd)` → mss region dict `{'left','top','width','height'}` → `sct.grab(region)`. Follows window moves automatically.
- `client_area_only=true` uses `GetClientRect` + `ClientToScreen` — crops cmd's title bar/borders for cleaner video + OCR.
- **Fixed stream geometry:** video size locked at recording start; if the window resizes, frames are resized (PIL) to stream dims — avoids PyAV dimension mismatch.
- Encoding recipe: `h264`, `yuv420p`, CRF 23, preset fast, 30 fps `time_base`, `frame.pts = int(elapsed * fps)` (PTS-based timing: capture as fast as possible, PTS smooths playback). Flush encoder + close container in `finally`.
- Loop gated by a `threading.Event` stop flag.
- Per-frame guards: `IsWindow(hwnd)` → error + stop if closed; `IsIconic` → skip frame if minimized.
- Accepted trade-off: occlusion is not handled — mss captures whatever is on top of the window region.

## OCR

- `pytesseract.image_to_string` on the region grab of the window's client area.
- Preprocessing for dark themes (cmd, VS Code dark, etc.): upscale 2×, grayscale, invert-if-dark, Otsu threshold.
- `tesseract_cmd` from settings.

## Windows-Specific Details

- **DPI awareness:** call `SetProcessDpiAwarenessContext(-4)` (per-monitor v2) via ctypes at startup so `GetWindowRect`, mss, and pyautogui coordinates all agree on scaled displays.
- **Foreground restrictions:** background processes can't always `SetForegroundWindow` → use `ShowWindow(SW_RESTORE)` + `pyautogui.click` at window center before typing (simple, reliable).

## Testing

- One `test_<module>.py` per module; fixtures in `conftest.py` (TestClient, `auth_headers`, mocked win32/mss/pytesseract).
- `test_main.py` exercises the real MCP JSON-RPC handshake against `/mcp`.
- One manual integration test: open cmd → record → `type_text("echo hello")` → `run_command("dir")` → OCR → stop; assert MP4 exists and OCR contains "hello".
- Validate with `pytest`, `ruff check`, `mypy`, and a real MCP client pointed at `http://localhost:8000/mcp`.

## Tooling Config

- setuptools build backend, `py-modules = ["src"]` (flat `src` package, imports as `from src.config import settings`)
- ruff: line-length 100, target py313
- mypy: strict + pydantic plugin
- pytest: `testpaths=["tests"]`, `asyncio_mode="auto"`, `pythonpath=["src"]`

## Build Order

1. `pyproject.toml` + `src/` skeleton + `config.py`/`auth.py`/`models.py` (verify server boots, `/health` responds)
2. `windows.py` + `list_windows`/`open_app` (verify against real cmd window)
3. `recorder.py` + `start_recording`/`stop_recording` (verify MP4 plays, follows window moves)
4. `keyboard.py` + `ocr.py` + `services/command_runner.py` + `run_command` (verify end-to-end workflow)
5. Tests + README with client config

## Open Risks

- OCR accuracy on cmd depends on font size/theme — preprocessing mitigates, but small fonts may misread.
- devstudio is AGPL-3.0 — we adapt patterns and rewrite the loop logic, not copy code verbatim. If its capture code is copied directly instead, this project must be AGPL too.
- Desktop capture cannot run inside Docker or a non-interactive Windows session (no RDP-locked/minimized desktop).

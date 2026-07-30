# recorder-mcp

MCP server for Windows window recording, OCR capture, and input automation.
FastAPI + fastapi-mcp over streamable HTTP at `/mcp`.

## Quickstart

Requirements:

- Windows with an **interactive desktop session** (no Docker, no locked RDP)
- Python ≥ 3.13
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (UB Mannheim
  installer)

Setup:

1. Install Tesseract with the UB Mannheim installer. If you install to a
   non-default path, note the full path to `tesseract.exe`.
2. `pip install -e .[dev]`
3. Copy `.env.example` to `.env` and set `MCP_API_TOKEN` to a long random
   token. Set `TESSERACT_CMD` if Tesseract is not at
   `C:\Program Files\Tesseract-OCR\tesseract.exe`.
4. Start the server: `uvicorn src.main:app`

The server fails fast at startup if the Tesseract binary is not found. The MCP
endpoint is `http://localhost:8000/mcp`; every tool call requires Bearer auth
with `MCP_API_TOKEN`. `GET /health` is unauthenticated.

## MCP client configuration

Claude Desktop-style config (`claude_desktop_config.json`), bridging stdio to
the streamable-HTTP endpoint via `mcp-remote`:

```json
{
  "mcpServers": {
    "recorder-mcp": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:8000/mcp",
        "--header",
        "Authorization: Bearer <your MCP_API_TOKEN>"
      ]
    }
  }
}
```

Any MCP client that supports streamable HTTP with custom headers can connect
directly to `http://localhost:8000/mcp` with the same `Authorization` header.

## Tool reference

All tools return HTTP 200 with `error: str | None` in the response body —
`null` on success, a message on tool-level failure. Auth failures return 401.

Window-targeting tools (`start_recording`, `type_text`, `capture_output`,
`run_command`) take **exactly one** of `hwnd`, `title`, or `process` to
resolve the target window.

| Tool | Parameters | Returns | Description |
|---|---|---|---|
| `list_windows` | `title_filter?: str`, `process_filter?: str` | `windows[]` (`hwnd`, `title`, `process`), `error` | List visible windows; filters are case-insensitive substring matches and combinable. Body may be omitted entirely. |
| `open_app` | `path: str`, `args?: str[]`, `wait_timeout_s?: float = 10.0` | `hwnd`, `pid`, `title`, `error` | Launch a process, wait for its window, focus it. On timeout the process is left running and `error` says so. |
| `start_recording` | `hwnd\|title\|process`, `client_area_only?: bool = false`, `fps?: int` | `session_id`, `error` | Start recording the window to MP4 (h264/yuv420p). Only one recording per window. `fps` defaults to `FPS` from `.env`. |
| `stop_recording` | `session_id: str` | `mp4_path`, `duration_s`, `error` | Stop a recording and finalize the MP4 in `OUTPUT_DIR`. |
| `type_text` | `hwnd\|title\|process`, `text: str`, `press_enter?: bool = false` | `error` | Focus the window and type text via pyautogui. |
| `capture_output` | `hwnd\|title\|process` | `text`, `error` | OCR the window's client area (Tesseract) and return the text. |
| `run_command` | `hwnd\|title\|process`, `command: str`, `wait_s?: float = 1.0` | `output`, `error` | Focus the window, type the command + Enter, wait `wait_s`, then OCR the result. Latency ≈ `wait_s` + ~0.3–1s OCR. |

## Known limitations

- **Occlusion not handled** — recording and OCR capture whatever is on screen;
  overlapping windows will be captured instead of the target content.
- **Elevated windows** — typing into windows running as administrator fails
  unless the server itself runs elevated.
- **No Docker / RDP-locked sessions** — requires an interactive, unlocked
  Windows desktop session.
- **OCR small-font sensitivity** — OCR quality degrades on small fonts; the
  pipeline upscales 2× but very small or low-contrast text may be missed.
- **`pyautogui.write` ASCII only** — `type_text` / `run_command` support ASCII
  characters only; non-ASCII input is not supported by pyautogui.

## Development

```
pytest                        # full test suite (fully mocked, no desktop needed)
ruff check                    # lint
mypy src tests scripts        # typecheck (strict)
```

## Manual E2E test

`scripts/e2e_manual.py` exercises the full tool workflow against a live server:
open cmd via `open_app` → `start_recording` → `type_text("echo hello")` →
`run_command("dir")` → `capture_output` → `stop_recording`, then asserts the
recorded MP4 exists and is > 0 bytes and the OCR text contains `hello`.

**Manual-only:** not collected by pytest, not run in CI. Requires:

- an interactive Windows desktop session (a real cmd window is opened and typed into),
- Tesseract OCR installed and `TESSERACT_CMD` configured,
- the server running (`uvicorn src.main:app` from the repo root),
- `MCP_API_TOKEN` set in the environment or in `.env`.

```
python scripts/e2e_manual.py
```

Exit code 0 = pass, 1 = fail (reason printed). The cmd window is closed and any
active recording is stopped on exit, including failures.

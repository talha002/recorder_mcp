# Epic 1: Foundation & MCP Server Skeleton

> Spec: [`design/specs/mvp-1.md`](../specs/mvp-1.md) — Build Order step 1
> Goal: a bootable FastAPI + fastapi-mcp server with config, auth, and models, verified by `/health`, before any window logic exists.

## Overview

Everything in this epic is window-agnostic plumbing. It establishes the project layout, tooling gates (ruff / mypy-strict / pytest), the settings singleton, the Bearer-token auth dependency, all Pydantic request/response models, and the FastAPI application with `FastApiMCP` mounted at `/mcp`. Later epics only add routes and sync core modules — they must not restructure anything defined here.

## Architecture

### Framework wiring (mirrors `D:\yazilim\news_agent`)

- Tools are plain `@app.post` endpoints; each has an `operation_id` equal to the MCP tool name.
- `FastApiMCP(app)` is created and `mount_http()` called **at the top** of `main.py`; `setup_server()` is invoked **after all routes are registered** (at module bottom or via app factory finalizer). This ordering is load-bearing — endpoints registered after `setup_server()` are not exposed as MCP tools.
- Transport: streamable HTTP at `/mcp` via uvicorn. Run command: `uvicorn src.main:app` (no console scripts).
- `/health` is excluded from the MCP tool list with `include_in_schema=False`.
- Auth: `verify_mcp_token` FastAPI dependency (Bearer token vs `MCP_API_TOKEN`) attached to every tool endpoint — not to `/health`.

### Sync-core rule

All win32 / mss / pyautogui / pytesseract code written in later epics is synchronous and must be invoked via `asyncio.to_thread(...)`. The recording loop is a dedicated `threading.Thread` with a `threading.Event` stop flag — never an `asyncio` task. This epic's `main.py` must already demonstrate the pattern (e.g. the Tesseract startup check runs sync code through `asyncio.to_thread` or a startup hook).

### External prerequisite

Tesseract OCR binary (UB Mannheim installer). Path from `TESSERACT_CMD` env var, default `C:\Program Files\Tesseract-OCR\tesseract.exe`. Server fails fast at startup with a clear, actionable error message if the binary is missing.

## Module Specifications

### `pyproject.toml`

- Build backend: `setuptools`, with `py-modules = ["src"]` (flat `src` package; imports are `from src.config import settings`).
- Requires-Python: `>=3.13`.
- Dependencies: `fastapi`, `fastapi-mcp`, `uvicorn`, `mss`, `av`, `pillow`, `numpy`, `pyautogui`, `pytesseract`, `pydantic-settings`.
- Dev dependencies: `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `httpx` (TestClient).
- Tool config:
  - ruff: `line-length = 100`, `target-version = "py313"`.
  - mypy: `strict = true`, `plugins = ["pydantic.mypy"]`.
  - pytest: `testpaths = ["tests"]`, `asyncio_mode = "auto"`, `pythonpath = ["src"]`.

### `.env.example`

Keys: `MCP_API_TOKEN`, `TESSERACT_CMD`, `OUTPUT_DIR`, `FPS`, `HOST`, `PORT` — each with a sane example value and a comment.

### `src/config.py`

- `Settings(BaseSettings)` reading `.env`; fields for every key above with types and defaults (`FPS: int = 30`, `HOST: str = "127.0.0.1"`, `PORT: int = 8000`, `OUTPUT_DIR: Path`, `TESSERACT_CMD: Path` with the Windows default).
- Module-level singleton: `settings = Settings()`.
- Validation: `OUTPUT_DIR` created if missing (on first use or startup, not import).

### `src/models.py`

One request + one response model per tool (7 tools). Response pattern: `error: str | None = None` on every response; on failure the endpoint returns HTTP 200 with `error` populated (tool-friendly), reserving 4xx/5xx for auth/transport failures.

| Tool | Request fields (indicative) | Response fields (indicative) |
|---|---|---|
| `list_windows` | `title_filter: str \| None`, `process_filter: str \| None` | `windows: list[WindowInfo]`, `error` |
| `open_app` | `path: str`, `args: list[str]`, `wait_timeout_s: float` | `hwnd: int`, `pid: int`, `title: str`, `error` |
| `start_recording` | `hwnd \| title \| process`, `client_area_only: bool`, `fps: int \| None` | `session_id: str`, `error` |
| `stop_recording` | `session_id: str` | `mp4_path: str`, `duration_s: float`, `error` |
| `type_text` | `hwnd \| title \| process`, `text: str`, `press_enter: bool` | `error` |
| `capture_output` | `hwnd \| title \| process` | `text: str`, `error` |
| `run_command` | `hwnd \| title \| process`, `command: str`, `wait_s: float` | `output: str`, `error` |

Shared: `WindowInfo { hwnd: int, title: str, process: str }`. Window targeting is a discriminated union — exactly one of `hwnd` / `title` / `process` required.

### `src/auth.py`

- `verify_mcp_token(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()))` — compares `credentials.credentials` against `settings.mcp_api_token` using `secrets.compare_digest`; raises `HTTPException(401)` on mismatch/missing.

### `src/main.py`

- App factory or module-level `app`; `FastApiMCP(app)` + `mount_http()` at top; `setup_server()` after routes.
- `GET /health` (`include_in_schema=False`) → `{"status": "ok"}`.
- Startup hook: DPI-awareness is Epic 2, but the **Tesseract check** lives here — verify `settings.tesseract_cmd` exists; log/refuse clearly if missing.
- All 7 tool routes are registered with auth dependency; in this epic they may return `501`-style `error: "not implemented"` placeholders until Epics 2–5 fill them in — but routes, models, and `operation_id`s are final here.

## Stories

| ID | Title | Summary |
|---|---|---|
| E1.S1 | Project scaffolding & tooling | `pyproject.toml`, `.env.example`, `src/`, `tests/` skeleton; ruff/mypy/pytest green on empty code |
| E1.S2 | Settings singleton (`config.py`) | pydantic-settings over `.env` with all keys, defaults, singleton |
| E1.S3 | Pydantic models (`models.py`) | All request/response models for 7 tools, `error: str \| None` pattern |
| E1.S4 | Bearer auth dependency (`auth.py`) | `verify_mcp_token`, constant-time compare, 401 on failure |
| E1.S5 | FastAPI app & MCP mount (`main.py`) | App + FastApiMCP at `/mcp`, `/health`, Tesseract startup check, placeholder tool routes |

## Acceptance Criteria (epic-level)

- `uvicorn src.main:app` boots; `GET /health` returns 200 `{"status":"ok"}` without auth.
- `POST /mcp` with correct Bearer token completes the MCP handshake and lists 7 tools; without/with wrong token → 401.
- `ruff check`, `mypy`, and `pytest` all pass.
- Missing Tesseract binary produces a clear startup error naming `TESSERACT_CMD`.
- `pip install -e .` works; `from src.config import settings` resolves.

## Test Coverage (epic-level)

- `test_config.py` — defaults, `.env` override, missing-token behavior.
- `test_auth.py` — 401 without token, 401 with wrong token, 200 with correct token.
- `test_models.py` — validation of each request model (incl. exactly-one-of hwnd/title/process), response serialization.
- `test_main.py` (partial in this epic) — `/health`, auth on a placeholder route.

## Dependencies

None — this is the foundation. Epics 2–5 all depend on Epic 1.

## Risks / Notes

- `fastapi-mcp` version pin matters: `mount_http()` / `setup_server()` API must match the pinned release.
- Windows-only stack (pywin32 via pyautogui) — do not add Docker; capture requires an interactive session.

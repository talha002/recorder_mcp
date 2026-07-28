# Epic 6: Testing, Integration & Documentation

> Spec: [`design/specs/mvp-1.md`](../specs/mvp-1.md) — Build Order step 5
> Depends on: Epics 1–5
> Goal: full automated test suite, a real MCP handshake test, a manual E2E script, and user-facing docs.

## Overview

Hardens the MVP: one `test_<module>.py` per module with shared fixtures, a JSON-RPC handshake test against `/mcp` with a real MCP client, a documented manual integration test, README with client configuration, and the standing rule that `design/CODEBASE_MAP.md` is updated whenever an issue closes.

## Architecture

### Test layout

```
tests/
├── conftest.py            # TestClient, auth_headers, mocked win32/mss/pyautogui/pytesseract fixtures
├── test_main.py           # endpoints + real MCP handshake on /mcp
├── test_auth.py           # (Epic 1)
├── test_config.py         # (Epic 1)
├── test_models.py         # (Epic 1)
├── test_windows.py        # (Epic 2)
├── test_recorder.py       # (Epic 3)
├── test_keyboard.py       # (Epic 4)
├── test_ocr.py            # (Epic 4)
└── test_command_runner.py # (Epic 5)
```

### `conftest.py` fixtures

- `client` — `fastapi.testclient.TestClient(app)`; env overrides via `monkeypatch` (e.g. `MCP_API_TOKEN=test-token`).
- `auth_headers` — `{"Authorization": "Bearer test-token"}`.
- Win32 mocks: `mock_enum_windows`, `mock_find_window` returning a fixed `WindowInfo`.
- `mock_mss` — synthetic frames (configurable size, solid color / pattern).
- `mock_av` — in-memory container capturing frames for assertion.
- `mock_pytesseract` — returns canned text, records the image it received.
- `mock_pyautogui` — records `write`/`press`/`click` calls.
- Rule: **no test touches real win32/mss/tesseract**; only the manual E2E does.

### MCP handshake test

Real JSON-RPC over the ASGI transport against `/mcp`: `initialize` → `notifications/initialized` → `tools/list` asserting all 7 tool names, then `tools/call` for `list_windows` with mocks in place. Auth negative cases included.

### Manual E2E (scripted, marked `@pytest.mark.manual` or kept as `scripts/e2e_manual.py`)

Open cmd → `start_recording` → `type_text("echo hello")` → `run_command("dir")` → `capture_output` → `stop_recording`. Assert MP4 exists & non-trivial size; OCR text contains `hello`. Requires interactive Windows session + Tesseract installed.

### README

- Quickstart: install (incl. Tesseract UB Mannheim note + `TESSERACT_CMD`), `.env` setup, `uvicorn src.main:app`.
- MCP client config snippet (Claude Desktop-style JSON with Bearer token header pointing at `http://localhost:8000/mcp`).
- Tool reference table (7 tools, parameters, error pattern).
- Known limitations: occlusion, elevated windows, Docker/RDP non-support, OCR font-size sensitivity.

### CODEBASE_MAP maintenance rule

`design/CODEBASE_MAP.md` is updated in the same PR/commit that closes any issue: new modules, changed public functions, new env vars, new MCP tools. The epic/story issue templates reference this rule.

## Stories

| ID | Title | Summary |
|---|---|---|
| E6.S1 | Unit test suite completion | conftest + per-module tests reach green across Epics 1–5 |
| E6.S2 | MCP handshake test | Real JSON-RPC `initialize`/`tools/list`/`tools/call` on `/mcp` + auth negatives |
| E6.S3 | Manual E2E integration | Scripted cmd workflow asserting MP4 + OCR |
| E6.S4 | README + MCP client config + CODEBASE_MAP final sync | Docs complete; map matches shipped code |

## Acceptance Criteria (epic-level)

- `pytest` green (incl. handshake test); `ruff check` and `mypy` clean.
- Coverage: every module has a dedicated test file; every endpoint has ≥2 tests (happy + error).
- Manual E2E passes on a clean Windows machine following only the README.
- A real MCP client (per README config) lists and calls all 7 tools.
- CODEBASE_MAP.md accurately reflects the final codebase.

## Test Coverage (epic-level)

This epic *is* the coverage. Meta-checks: tests must run without a desktop session (all hardware mocked except the manual marker), and the suite must pass from a fresh venv via `pip install -e .[dev] && pytest`.

## Dependencies

- Epics 1–5 complete (suite exercises their public surfaces).

## Risks / Notes

- fastapi-mcp streamable-HTTP handshake details (SSE vs JSON response mode) must match the pinned version — pin in Epic 1 and test here.
- Manual E2E is environment-sensitive; keep it out of CI and clearly marked.

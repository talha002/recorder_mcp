# recorder-mcp

MCP server for Windows window recording, OCR capture, and input automation.
FastAPI + fastapi-mcp over streamable HTTP at `/mcp`.

## Quickstart

Requirements: Windows with an interactive desktop session, Python 3.13+,
Tesseract OCR (UB Mannheim installer).

1. `pip install -e .[dev]`
2. Copy `.env.example` to `.env` and set `MCP_API_TOKEN` (and `TESSERACT_CMD`
   if Tesseract is not at the default path).
3. Start the server: `uvicorn src.main:app`
4. MCP endpoint: `http://localhost:8000/mcp` (Bearer auth with `MCP_API_TOKEN`).

Run the automated test suite with `pytest` (fully mocked, no desktop session
needed). Lint/typecheck: `ruff check` and `mypy src tests scripts`.

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

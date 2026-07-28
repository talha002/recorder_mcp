# Epic 5: Command Orchestration (`run_command`)

> Spec: [`design/specs/mvp-1.md`](../specs/mvp-1.md) — Build Order step 4 (second half)
> Depends on: Epic 1, Epic 2, Epic 4 (keyboard + ocr primitives)
> Goal: one convenience tool that composes focus → type + Enter → wait → OCR for terminal-like windows.

## Overview

`src/services/command_runner.py` is the only orchestration service. It composes Epic 4 primitives into the `run_command` MCP tool. It is a *convenience composite* — window-agnostic, useful for any window that accepts keyboard input (cmd, PowerShell, Python REPL, ssh session, …).

## Architecture

### Flow (`run_command_sync(hwnd, command, wait_s) -> str`)

1. `focus_window(hwnd)` (Epic 2).
2. `type_into_window(hwnd, command, press_enter=True)` (Epic 4) — focus is re-asserted inside; harmless.
3. Wait `wait_s` seconds (default from request model, e.g. 1.0–2.0 s) — blocking sleep is fine; the whole function runs in a thread via `asyncio.to_thread`.
4. `capture_window_text(hwnd, client_area_only=True)` (Epic 4) → raw OCR text.
5. Return text as-is. Post-processing (prompt stripping, output-only extraction) is **out of scope for MVP-1** — record as a future enhancement candidate.

### Failure semantics

- Any primitive failure (window vanished, minimized) short-circuits: `{output: "", error: "<cause>"}`.
- No retry logic in MVP-1; caller adjusts `wait_s` for slow commands.

### Why a service module

Keeps `main.py` thin: the endpoint only validates, resolves the window, and delegates. Future orchestrations (e.g. `run_and_record`) land in `src/services/` next to it.

## MCP Tool

### `run_command` — POST `/run-command`

Request: window targeting union + `command: str` + `wait_s: float` (bounded, e.g. 0.1–60).
Response: `{output: str, error: str | None}`.

## Stories

| ID | Title | Summary |
|---|---|---|
| E5.S1 | `services/command_runner.py` | `run_command_sync` composition + short-circuit errors |
| E5.S2 | `run_command` endpoint | Route + resolution + `asyncio.to_thread` delegation |

## Acceptance Criteria (epic-level)

- End-to-end on real cmd: `open_app("cmd.exe")` → `run_command(hwnd, "echo hello")` → output contains `hello`.
- `run_command(hwnd, "dir")` returns a directory listing via OCR.
- Slow command: with `wait_s=5`, output of a delayed command is captured; with `wait_s=0.5` it may be empty (documented behavior, not a bug).
- Window closed between focus and OCR → clean `error`, no traceback in response.

## Test Coverage (epic-level)

- `test_command_runner.py` — mocked keyboard/ocr/windows: call order (focus → type+enter → sleep → OCR), error short-circuit at each step, `wait_s` passthrough to `time.sleep`.
- `test_main.py` additions — endpoint happy path + primitive-failure path.
- Manual: the full spec workflow (open cmd → record → `type_text("echo hello")` → `run_command("dir")` → stop) once Epic 3 is available.

## Dependencies

- Epic 1 (models, auth).
- Epic 2 (`find_window`, `focus_window`).
- Epic 4 (`type_into_window`, `capture_window_text`).

## Risks / Notes

- OCR latency: capture takes ~0.3–1 s depending on region size; total `run_command` latency is `wait_s + OCR` — document in tool description.
- Full-screen terminals on scaled displays depend on Epic 2 DPI awareness — regression-check on 125%/150% scaling.

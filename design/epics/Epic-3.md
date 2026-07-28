# Epic 3: Recording Engine

> Spec: [`design/specs/mvp-1.md`](../specs/mvp-1.md) — Build Order step 3
> Depends on: Epic 1, Epic 2 (window resolution & geometry)
> Goal: record any window to H.264 MP4 via a dedicated capture thread; MP4 plays correctly and follows window moves.

## Overview

`src/recorder.py` implements `RecordingManager` + a per-session capture-loop thread. Screen grab via `mss`, encoding via `av` (bundled FFmpeg) with PTS-based timing. Patterns are **adapted, not copied** from `D:\yazilim\video_gen\devstudio` (AGPL-3.0 — see Risks). Two MCP tools: `start_recording`, `stop_recording`.

## Architecture

### Session model

- `start_recording(hwnd|title|process, client_area_only=false, fps=None)` resolves the window (Epic 2), creates a session `{session_id (uuid4), hwnd, thread, stop_event, output_path, started_at}`, and spawns the capture thread. `fps` defaults to `settings.fps` (30).
- `stop_recording(session_id)` sets the stop event, joins the thread (bounded timeout), and returns `{mp4_path, duration_s}`.
- One active session per hwnd: starting a second recording on the same hwnd returns `error: "already recording"` unless the first is stopped. Multiple different windows may record concurrently.
- `RecordingManager` holds `dict[str, Session]` guarded by a `threading.Lock` (endpoint runs in threadpool via `asyncio.to_thread`).

### Capture loop (per frame)

1. Guard: `IsWindow(hwnd)` → if false, record error on session and stop. `IsIconic(hwnd)` → skip frame (minimized).
2. Rect: `GetWindowRect` — or `GetClientRect` + `ClientToScreen` when `client_area_only=true` (crops title bar/borders for cleaner video + OCR). **Re-read every frame** → recording follows window moves automatically.
3. Grab: `sct.grab({'left','top','width','height'})` via a thread-local `mss.mss()` instance (mss is not thread-safe across threads — instantiate inside the loop thread).
4. Convert: raw BGRA → numpy → PIL `Image`.
5. **Fixed stream geometry:** video size locked at recording start; if the window resized, resize the frame with PIL to stream dims (prevents PyAV dimension mismatch).
6. Encode & write frame with PTS (below).
7. Loop gated by `threading.Event`; capture as fast as possible — PTS smooths playback (no sleep-based pacing required; a tiny yield is acceptable to avoid 100% CPU spin — decide in E3.S1).

### Encoding recipe (PyAV)

- Codec `h264`, pix_fmt `yuv420p`, CRF 23, preset `fast`.
- Stream `time_base` at the configured fps; `frame.pts = int(elapsed * fps)` from a monotonic clock at capture time.
- Output path: `{OUTPUT_DIR}/record_{session_id}.mp4`.
- `finally:` flush encoder (`stream.encode(None)`) + close container — even on window-closed or exception, the MP4 must be valid.
- Session exposes `error: str | None` and `frames_written: int` for the stop response / debugging.

### Failure semantics

- Window closed mid-recording → session ends with `error="window closed"`; `stop_recording` still returns the partial MP4 path + duration + error.
- Zero frames written (e.g. target minimized the whole time) → delete empty file, `error="no frames captured"`.

## Module Specification: `src/recorder.py` (sync core)

| Symbol | Behavior |
|---|---|
| `RecordingSession` (dataclass) | session_id, hwnd, client_area_only, fps, stop_event, thread, output_path, started_at, error, frames_written |
| `RecordingManager` | `start(hwnd, client_area_only, fps) -> session_id`, `stop(session_id) -> StopResult`, `active() -> list[str]`; lock-protected registry |
| `_capture_loop(session)` | frame loop as specified above |
| module singleton | `manager = RecordingManager()` imported by endpoints |

## MCP Tools

### `start_recording` — POST `/start-recording`

Resolve window (404-style `error` if not found) → `manager.start(...)` → `{session_id}`. Runs via `asyncio.to_thread` only for resolution; thread spawn itself is cheap.

### `stop_recording` — POST `/stop-recording`

`manager.stop(session_id)` → `{mp4_path, duration_s}`; unknown session → `error="unknown session_id"`.

## Stories

| ID | Title | Summary |
|---|---|---|
| E3.S1 | `recorder.py` — RecordingManager + capture loop + PyAV encoding | Session registry, mss grab, fixed geometry, PTS encode, guards, flush-on-exit |
| E3.S2 | `start_recording` endpoint | Resolution, duplicate-hwnd guard, session_id response |
| E3.S3 | `stop_recording` endpoint | Stop/join/flush, partial-recording semantics, unknown-session error |

## Acceptance Criteria (epic-level)

- Recording a cmd window for ~10 s yields a playable MP4 (h264/yuv420p) of roughly correct duration (±1 s) at configured fps.
- Moving the window mid-recording keeps the window in frame (region re-read per frame).
- Resizing the window mid-recording does not crash the encoder; output dimensions stay constant.
- `client_area_only=true` output excludes the title bar.
- Closing the window mid-recording: `stop_recording` returns partial MP4 + `error="window closed"`.
- Two concurrent sessions on different windows both produce valid MP4s; second session on same hwnd is rejected.

## Test Coverage (epic-level)

- `test_recorder.py` — mocked mss/av/win32: loop stop via event, frame resize on geometry change, PTS monotonicity, `IsWindow`-false abort path, flush-in-`finally`, duplicate-hwnd rejection, zero-frames cleanup.
- `test_main.py` additions — start/stop happy path with mocked manager; unknown session; window-not-found.
- Manual: record cmd while typing, play MP4; move & resize window during recording; minimize mid-recording (frames skipped, duration stays sane).

## Dependencies

- Epic 1 (models, settings `OUTPUT_DIR`/`FPS`, auth).
- Epic 2 (`find_window`, `get_window_rect`, `get_client_rect_screen`, `is_valid`).

## Risks / Notes

- **License:** devstudio is AGPL-3.0. We adapt the *pattern* (mss region grab + PTS encoding) and rewrite loop logic; do not copy code verbatim. If any capture code is ever copied directly, this project must be relicensed AGPL.
- Occlusion is an accepted trade-off — mss captures whatever is on top of the region.
- `av` wheel bundles FFmpeg; no system FFmpeg install required — note in README.

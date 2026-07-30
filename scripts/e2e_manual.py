"""Manual end-to-end integration test for the recorder-mcp server.

MANUAL ONLY — lives in scripts/ (outside pytest testpaths) and is not run in CI.

Requirements:
  - interactive Windows desktop session (a real cmd window is opened, focused, typed into),
  - Tesseract OCR installed and TESSERACT_CMD configured,
  - live server: `uvicorn src.main:app` started from the repo root,
  - MCP_API_TOKEN set in the environment or in .env.

Workflow (design/specs/mvp-1.md): open_app(cmd) -> start_recording ->
type_text("echo hello" + Enter) -> run_command("dir") -> capture_output ->
stop_recording. Asserts the recorded MP4 exists and is > 0 bytes, and that the
OCR text captured from the cmd window contains "hello".

Usage: python scripts/e2e_manual.py  (exit code 0 = pass, 1 = fail)
"""

import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = REPO_ROOT / ".env"

CMD_EXE = "cmd.exe"
ECHO_TEXT = "echo hello"
DIR_COMMAND = "dir"
OCR_NEEDLE = "hello"
REQUEST_TIMEOUT_S = 30.0
OPEN_TIMEOUT_S = 10.0
SETTLE_AFTER_OPEN_S = 1.0
SETTLE_AFTER_ECHO_S = 0.5
DIR_WAIT_S = 1.0


class StepError(Exception):
    """A workflow step or assertion failed."""


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_setting(dotenv: dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key) or dotenv.get(key) or default


def _call_tool(
    client: httpx.Client, step: str, path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        response = client.post(path, json=payload)
    except httpx.HTTPError as exc:
        raise StepError(f"{step}: request to {path} failed: {exc}") from exc
    if response.status_code != 200:
        raise StepError(f"{step}: HTTP {response.status_code} from {path}: {response.text}")
    data: dict[str, Any] = response.json()
    error = data.get("error")
    if error:
        raise StepError(f"{step}: tool returned error: {error}")
    print(f"  ok: {step}")
    return data


def _check_server(client: httpx.Client) -> None:
    try:
        response = client.get("/health")
    except httpx.HTTPError as exc:
        raise StepError(
            f"server unreachable ({exc}) — start it with 'uvicorn src.main:app' from the repo root"
        ) from exc
    if response.status_code != 200:
        raise StepError(f"/health returned HTTP {response.status_code}")
    print("  ok: server healthy")


def _assert_ocr_contains(text: str, needle: str) -> None:
    if needle.casefold() not in text.casefold():
        excerpt = text.strip().replace("\r", " ")[:300]
        raise StepError(f"OCR assertion failed: {needle!r} not in captured text: {excerpt!r}")
    print(f"  ok: OCR text contains {needle!r}")


def _assert_mp4(mp4_path_str: str) -> Path:
    if not mp4_path_str:
        raise StepError("stop_recording returned no mp4_path")
    mp4 = Path(mp4_path_str)
    if not mp4.is_absolute():
        mp4 = REPO_ROOT / mp4
    if not mp4.is_file():
        raise StepError(f"MP4 assertion failed: file not found: {mp4}")
    size = mp4.stat().st_size
    if size <= 0:
        raise StepError(f"MP4 assertion failed: file is empty: {mp4}")
    print(f"  ok: MP4 exists ({size} bytes): {mp4}")
    return mp4


def _cleanup(client: httpx.Client, hwnd: int | None, session_id: str | None) -> None:
    if session_id is not None:
        try:
            client.post("/stop-recording", json={"session_id": session_id})
        except httpx.HTTPError:
            pass
    if hwnd is not None:
        try:
            client.post("/type-text", json={"hwnd": hwnd, "text": "exit", "press_enter": True})
        except httpx.HTTPError:
            pass


def main() -> int:
    dotenv = _load_dotenv(DOTENV_PATH)
    token = _resolve_setting(dotenv, "MCP_API_TOKEN")
    if not token:
        print("FAIL: MCP_API_TOKEN not found in environment or .env")
        return 1
    host = _resolve_setting(dotenv, "HOST", "127.0.0.1")
    port = _resolve_setting(dotenv, "PORT", "8000")
    base_url = f"http://{host}:{port}"

    print(f"e2e_manual: cmd workflow against {base_url} (manual-only test)")
    hwnd: int | None = None
    session_id: str | None = None
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_S,
    ) as client:
        try:
            _check_server(client)
            opened = _call_tool(
                client,
                "open_app",
                "/open-app",
                {"path": CMD_EXE, "wait_timeout_s": OPEN_TIMEOUT_S},
            )
            hwnd = int(opened["hwnd"])
            time.sleep(SETTLE_AFTER_OPEN_S)
            started = _call_tool(client, "start_recording", "/start-recording", {"hwnd": hwnd})
            session_id = str(started["session_id"])
            _call_tool(
                client,
                "type_text",
                "/type-text",
                {"hwnd": hwnd, "text": ECHO_TEXT, "press_enter": True},
            )
            time.sleep(SETTLE_AFTER_ECHO_S)
            _call_tool(
                client,
                "run_command",
                "/run-command",
                {"hwnd": hwnd, "command": DIR_COMMAND, "wait_s": DIR_WAIT_S},
            )
            captured = _call_tool(client, "capture_output", "/capture-output", {"hwnd": hwnd})
            ocr_text = str(captured.get("text", ""))
            stopped = _call_tool(
                client, "stop_recording", "/stop-recording", {"session_id": session_id}
            )
            session_id = None
            _assert_ocr_contains(ocr_text, OCR_NEEDLE)
            mp4 = _assert_mp4(str(stopped["mp4_path"]))
        except StepError as exc:
            print(f"FAIL: {exc}")
            return 1
        finally:
            _cleanup(client, hwnd, session_id)
    print(f"PASS: MP4 recorded ({mp4.stat().st_size} bytes) and OCR contains {OCR_NEEDLE!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

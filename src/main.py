import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from subprocess import Popen

from fastapi import Depends, FastAPI
from fastapi_mcp import FastApiMCP
from fastapi_mcp.types import AuthConfig
from pyautogui import FailSafeException
from pywintypes import error as PyWinError

from src.auth import verify_mcp_token
from src.config import settings
from src.models import (
    CaptureOutputRequest,
    CaptureOutputResponse,
    ListWindowsRequest,
    ListWindowsResponse,
    OpenAppRequest,
    OpenAppResponse,
    RunCommandRequest,
    RunCommandResponse,
    StartRecordingRequest,
    StartRecordingResponse,
    StopRecordingRequest,
    StopRecordingResponse,
    TypeTextRequest,
    TypeTextResponse,
)
from src.windows import enumerate_windows, find_window, find_window_by_pid, focus_window

__all__ = ["app"]


def _check_tesseract() -> None:
    if not settings.tesseract_cmd.is_file():
        raise RuntimeError(
            f"Tesseract OCR binary not found at '{settings.tesseract_cmd}'. "
            "Install Tesseract OCR (UB Mannheim installer) or set TESSERACT_CMD "
            "in your .env to the full path of tesseract.exe."
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(_check_tesseract)
    yield


app = FastAPI(title="recorder-mcp", version="0.1.0", lifespan=lifespan)

mcp = FastApiMCP(
    app,
    auth_config=AuthConfig(dependencies=[Depends(verify_mcp_token)]),
)
mcp.mount_http()

_auth = [Depends(verify_mcp_token)]


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/list-windows",
    operation_id="list_windows",
    response_model=ListWindowsResponse,
    dependencies=_auth,
)
async def list_windows(body: ListWindowsRequest) -> ListWindowsResponse:
    windows = await asyncio.to_thread(enumerate_windows, visible_only=True)
    if body.title_filter is not None:
        title_needle = body.title_filter.casefold()
        windows = [w for w in windows if title_needle in w.title.casefold()]
    if body.process_filter is not None:
        process_needle = body.process_filter.casefold()
        windows = [w for w in windows if process_needle in w.process.casefold()]
    return ListWindowsResponse(windows=windows)


_OPEN_APP_POLL_INTERVAL_S = 0.1


def _open_app_sync(path: str, args: list[str], wait_timeout_s: float) -> OpenAppResponse:
    try:
        proc = Popen([path, *args])
    except OSError as exc:
        return OpenAppResponse(error=f"failed to launch '{path}': {exc}")

    deadline = time.monotonic() + wait_timeout_s
    title_hint = Path(path).stem
    while True:
        info = find_window_by_pid(proc.pid)
        if info is None and title_hint:
            info = find_window(title=title_hint)
        if info is not None:
            try:
                focus_window(info.hwnd)
            except (PyWinError, FailSafeException) as exc:
                return OpenAppResponse(
                    hwnd=info.hwnd,
                    pid=proc.pid,
                    title=info.title,
                    error=f"window found but focus failed: {exc}",
                )
            return OpenAppResponse(hwnd=info.hwnd, pid=proc.pid, title=info.title)
        if proc.poll() is not None:
            return OpenAppResponse(
                pid=proc.pid,
                error=(f"'{path}' exited with code {proc.returncode} before showing a window"),
            )
        if time.monotonic() >= deadline:
            return OpenAppResponse(
                pid=proc.pid,
                error=(
                    f"timed out after {wait_timeout_s:.1f}s waiting for a window "
                    f"from pid {proc.pid} (process left running)"
                ),
            )
        time.sleep(_OPEN_APP_POLL_INTERVAL_S)


@app.post(
    "/open-app",
    operation_id="open_app",
    response_model=OpenAppResponse,
    dependencies=_auth,
)
async def open_app(body: OpenAppRequest) -> OpenAppResponse:
    return await asyncio.to_thread(_open_app_sync, body.path, body.args, body.wait_timeout_s)


@app.post(
    "/start-recording",
    operation_id="start_recording",
    response_model=StartRecordingResponse,
    dependencies=_auth,
)
async def start_recording(body: StartRecordingRequest) -> StartRecordingResponse:
    return StartRecordingResponse(error="not implemented")


@app.post(
    "/stop-recording",
    operation_id="stop_recording",
    response_model=StopRecordingResponse,
    dependencies=_auth,
)
async def stop_recording(body: StopRecordingRequest) -> StopRecordingResponse:
    return StopRecordingResponse(error="not implemented")


@app.post(
    "/type-text",
    operation_id="type_text",
    response_model=TypeTextResponse,
    dependencies=_auth,
)
async def type_text(body: TypeTextRequest) -> TypeTextResponse:
    return TypeTextResponse(error="not implemented")


@app.post(
    "/capture-output",
    operation_id="capture_output",
    response_model=CaptureOutputResponse,
    dependencies=_auth,
)
async def capture_output(body: CaptureOutputRequest) -> CaptureOutputResponse:
    return CaptureOutputResponse(error="not implemented")


@app.post(
    "/run-command",
    operation_id="run_command",
    response_model=RunCommandResponse,
    dependencies=_auth,
)
async def run_command(body: RunCommandRequest) -> RunCommandResponse:
    return RunCommandResponse(error="not implemented")


mcp.setup_server()

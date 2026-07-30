import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from subprocess import Popen
from typing import Annotated

from fastapi import Body, Depends, FastAPI
from fastapi_mcp import FastApiMCP
from fastapi_mcp.types import AuthConfig
from pyautogui import FailSafeException
from pywintypes import error as PyWinError

from src.auth import verify_mcp_token
from src.config import settings
from src.keyboard import InvalidWindowError, type_into_window
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
    WindowTarget,
)
from src.ocr import WindowMinimizedError, capture_window_text
from src.recorder import AlreadyRecordingError, manager
from src.services.command_runner import run_command_sync
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
async def list_windows(
    body: Annotated[ListWindowsRequest | None, Body()] = None,
) -> ListWindowsResponse:
    body = body or ListWindowsRequest()
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


def _target_description(body: WindowTarget) -> str:
    if body.hwnd is not None:
        return f"hwnd={body.hwnd}"
    if body.title is not None:
        return f"title={body.title!r}"
    return f"process={body.process!r}"


@app.post(
    "/start-recording",
    operation_id="start_recording",
    response_model=StartRecordingResponse,
    dependencies=_auth,
)
async def start_recording(body: StartRecordingRequest) -> StartRecordingResponse:
    info = await asyncio.to_thread(
        find_window, hwnd=body.hwnd, title=body.title, process=body.process
    )
    if info is None:
        return StartRecordingResponse(error=f"window not found: {_target_description(body)}")
    try:
        session_id = manager.start(info.hwnd, body.client_area_only, body.fps or settings.fps)
    except AlreadyRecordingError:
        return StartRecordingResponse(error="already recording")
    return StartRecordingResponse(session_id=session_id)


@app.post(
    "/stop-recording",
    operation_id="stop_recording",
    response_model=StopRecordingResponse,
    dependencies=_auth,
)
async def stop_recording(body: StopRecordingRequest) -> StopRecordingResponse:
    result = await asyncio.to_thread(manager.stop, body.session_id)
    return StopRecordingResponse(
        mp4_path=result.mp4_path,
        duration_s=result.duration_s,
        error=result.error,
    )


@app.post(
    "/type-text",
    operation_id="type_text",
    response_model=TypeTextResponse,
    dependencies=_auth,
)
async def type_text(body: TypeTextRequest) -> TypeTextResponse:
    info = await asyncio.to_thread(
        find_window, hwnd=body.hwnd, title=body.title, process=body.process
    )
    if info is None:
        return TypeTextResponse(error=f"window not found: {_target_description(body)}")
    try:
        await asyncio.to_thread(type_into_window, info.hwnd, body.text, body.press_enter)
    except (InvalidWindowError, PyWinError, FailSafeException) as exc:
        return TypeTextResponse(error=f"typing failed: {exc}")
    return TypeTextResponse()


@app.post(
    "/capture-output",
    operation_id="capture_output",
    response_model=CaptureOutputResponse,
    dependencies=_auth,
)
async def capture_output(body: CaptureOutputRequest) -> CaptureOutputResponse:
    info = await asyncio.to_thread(
        find_window, hwnd=body.hwnd, title=body.title, process=body.process
    )
    if info is None:
        return CaptureOutputResponse(error=f"window not found: {_target_description(body)}")
    try:
        text = await asyncio.to_thread(capture_window_text, info.hwnd, True)
    except WindowMinimizedError as exc:
        return CaptureOutputResponse(error=str(exc))
    except PyWinError as exc:
        return CaptureOutputResponse(error=f"capture failed: {exc}")
    return CaptureOutputResponse(text=text)


@app.post(
    "/run-command",
    operation_id="run_command",
    response_model=RunCommandResponse,
    dependencies=_auth,
)
async def run_command(body: RunCommandRequest) -> RunCommandResponse:
    """Focus a window, type a command with Enter, wait, then OCR the window text.

    Total latency is approximately `wait_s` plus OCR time (~0.3-1s).
    """
    info = await asyncio.to_thread(
        find_window, hwnd=body.hwnd, title=body.title, process=body.process
    )
    if info is None:
        return RunCommandResponse(error=f"window not found: {_target_description(body)}")
    result = await asyncio.to_thread(
        run_command_sync, info.hwnd, body.command, body.wait_s
    )
    return RunCommandResponse(output=result.output, error=result.error)


mcp.setup_server()

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi_mcp import FastApiMCP
from fastapi_mcp.types import AuthConfig

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
    return ListWindowsResponse(error="not implemented")


@app.post(
    "/open-app",
    operation_id="open_app",
    response_model=OpenAppResponse,
    dependencies=_auth,
)
async def open_app(body: OpenAppRequest) -> OpenAppResponse:
    return OpenAppResponse(error="not implemented")


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

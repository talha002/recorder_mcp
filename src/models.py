from typing import Self

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "CaptureOutputRequest",
    "CaptureOutputResponse",
    "ListWindowsRequest",
    "ListWindowsResponse",
    "OpenAppRequest",
    "OpenAppResponse",
    "RunCommandRequest",
    "RunCommandResponse",
    "StartRecordingRequest",
    "StartRecordingResponse",
    "StopRecordingRequest",
    "StopRecordingResponse",
    "TypeTextRequest",
    "TypeTextResponse",
    "WindowInfo",
    "WindowTarget",
]


class WindowInfo(BaseModel):
    hwnd: int
    title: str
    process: str


class WindowTarget(BaseModel):
    hwnd: int | None = None
    title: str | None = None
    process: str | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> Self:
        provided = [
            name
            for name, value in (
                ("hwnd", self.hwnd),
                ("title", self.title),
                ("process", self.process),
            )
            if value is not None
        ]
        if len(provided) != 1:
            raise ValueError(
                "exactly one of 'hwnd', 'title', 'process' must be provided "
                f"(got {len(provided)}: {', '.join(provided) or 'none'})"
            )
        return self


class ListWindowsRequest(BaseModel):
    title_filter: str | None = None
    process_filter: str | None = None


class ListWindowsResponse(BaseModel):
    windows: list[WindowInfo] = Field(default_factory=list)
    error: str | None = None


class OpenAppRequest(BaseModel):
    path: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    wait_timeout_s: float = Field(default=10.0, gt=0)


class OpenAppResponse(BaseModel):
    hwnd: int | None = None
    pid: int | None = None
    title: str = ""
    error: str | None = None


class StartRecordingRequest(WindowTarget):
    client_area_only: bool = False
    fps: int | None = Field(default=None, gt=0)


class StartRecordingResponse(BaseModel):
    session_id: str = ""
    error: str | None = None


class StopRecordingRequest(BaseModel):
    session_id: str = Field(min_length=1)


class StopRecordingResponse(BaseModel):
    mp4_path: str = ""
    duration_s: float = 0.0
    error: str | None = None


class TypeTextRequest(WindowTarget):
    text: str
    press_enter: bool = False


class TypeTextResponse(BaseModel):
    error: str | None = None


class CaptureOutputRequest(WindowTarget):
    pass


class CaptureOutputResponse(BaseModel):
    text: str = ""
    error: str | None = None


class RunCommandRequest(WindowTarget):
    command: str = Field(min_length=1)
    wait_s: float = Field(default=1.0, ge=0.1, le=60)


class RunCommandResponse(BaseModel):
    output: str = ""
    error: str | None = None

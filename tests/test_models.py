from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

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
    WindowInfo,
)

TARGETED_MODELS: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (StartRecordingRequest, {}),
    (TypeTextRequest, {"text": "hello"}),
    (CaptureOutputRequest, {}),
    (RunCommandRequest, {"command": "dir"}),
]

ALL_RESPONSES: list[BaseModel] = [
    ListWindowsResponse(),
    OpenAppResponse(),
    StartRecordingResponse(),
    StopRecordingResponse(),
    TypeTextResponse(),
    CaptureOutputResponse(),
    RunCommandResponse(),
]


def test_list_windows_request_happy_path() -> None:
    req = ListWindowsRequest()
    assert req.title_filter is None
    assert req.process_filter is None
    req = ListWindowsRequest(title_filter="Notepad", process_filter="notepad.exe")
    assert req.title_filter == "Notepad"
    assert req.process_filter == "notepad.exe"


def test_open_app_request_happy_path() -> None:
    req = OpenAppRequest(path="notepad.exe")
    assert req.args == []
    assert req.wait_timeout_s == 10.0
    req = OpenAppRequest(path="notepad.exe", args=["a.txt"], wait_timeout_s=5.0)
    assert req.args == ["a.txt"]
    assert req.wait_timeout_s == 5.0


def test_open_app_request_rejects_empty_path() -> None:
    with pytest.raises(ValidationError):
        OpenAppRequest(path="")


def test_start_recording_request_happy_path() -> None:
    req = StartRecordingRequest(hwnd=123)
    assert req.client_area_only is False
    assert req.fps is None
    req = StartRecordingRequest(title="Notepad", client_area_only=True, fps=15)
    assert req.client_area_only is True
    assert req.fps == 15


def test_stop_recording_request_happy_path() -> None:
    req = StopRecordingRequest(session_id="abc")
    assert req.session_id == "abc"
    with pytest.raises(ValidationError):
        StopRecordingRequest(session_id="")


def test_type_text_request_happy_path() -> None:
    req = TypeTextRequest(process="notepad.exe", text="hello")
    assert req.text == "hello"
    assert req.press_enter is False
    req = TypeTextRequest(process="notepad.exe", text="hello", press_enter=True)
    assert req.press_enter is True


def test_capture_output_request_happy_path() -> None:
    req = CaptureOutputRequest(hwnd=7)
    assert req.hwnd == 7


def test_run_command_request_happy_path() -> None:
    req = RunCommandRequest(hwnd=7, command="ipconfig")
    assert req.command == "ipconfig"
    assert req.wait_s == 1.0
    req = RunCommandRequest(hwnd=7, command="ipconfig", wait_s=2.5)
    assert req.wait_s == 2.5


@pytest.mark.parametrize(("model", "extras"), TARGETED_MODELS)
def test_targeting_accepts_each_single_target(
    model: type[BaseModel], extras: dict[str, Any]
) -> None:
    for target in ({"hwnd": 1}, {"title": "t"}, {"process": "p"}):
        req = model(**{**extras, **target})
        values = [req.hwnd, req.title, req.process]  # type: ignore[attr-defined]
        assert sum(v is not None for v in values) == 1


@pytest.mark.parametrize(("model", "extras"), TARGETED_MODELS)
def test_targeting_rejects_no_target(model: type[BaseModel], extras: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="exactly one of"):
        model(**extras)


@pytest.mark.parametrize(("model", "extras"), TARGETED_MODELS)
def test_targeting_rejects_multiple_targets(
    model: type[BaseModel], extras: dict[str, Any]
) -> None:
    for combo in (
        {"hwnd": 1, "title": "t"},
        {"hwnd": 1, "process": "p"},
        {"title": "t", "process": "p"},
        {"hwnd": 1, "title": "t", "process": "p"},
    ):
        with pytest.raises(ValidationError, match="exactly one of"):
            model(**{**extras, **combo})


def test_window_info_reused_in_list_windows_response() -> None:
    window = WindowInfo(hwnd=1, title="Notepad", process="notepad.exe")
    resp = ListWindowsResponse(windows=[window])
    assert resp.windows == [window]
    assert resp.windows[0].hwnd == 1


@pytest.mark.parametrize("response", ALL_RESPONSES)
def test_response_serialization_includes_error_key(response: BaseModel) -> None:
    data = response.model_dump()
    assert "error" in data
    assert data["error"] is None
    assert response.model_dump_json()


@pytest.mark.parametrize("response", ALL_RESPONSES)
def test_response_accepts_error_message(response: BaseModel) -> None:
    resp = type(response)(**{**response.model_dump(), "error": "boom"})
    assert resp.model_dump()["error"] == "boom"

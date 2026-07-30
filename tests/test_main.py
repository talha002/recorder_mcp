import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from mcp.types import LATEST_PROTOCOL_VERSION

from src.config import settings
from src.keyboard import InvalidWindowError
from src.main import app
from src.models import WindowInfo
from src.ocr import WindowMinimizedError
from src.recorder import AlreadyRecordingError, StopResult
from src.services.command_runner import CommandResult
from tests.conftest import CMD_WINDOW, FIXED_WINDOW

EXPECTED_TOOLS = {
    "list_windows",
    "open_app",
    "start_recording",
    "stop_recording",
    "type_text",
    "capture_output",
    "run_command",
}

FAKE_WINDOWS = [
    FIXED_WINDOW,
    CMD_WINDOW,
    WindowInfo(hwnd=1003, title="cmd - build", process="cmd.exe"),
]


def _mcp_headers(session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": "Bearer test-token",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    return headers


def _mcp_handshake(client: TestClient) -> str:
    response = client.post(
        "/mcp",
        headers=_mcp_headers(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.0.0"},
            },
        },
    )
    assert response.status_code == 200, response.text
    session_id = response.headers["mcp-session-id"]

    notified = client.post(
        "/mcp",
        headers=_mcp_headers(session_id),
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert notified.status_code == 202, notified.text
    return session_id


def test_health_returns_200_without_auth(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_tool_route_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/list-windows", json={})
    assert response.status_code == 401


def test_tool_route_with_wrong_token_returns_401(client: TestClient) -> None:
    response = client.post(
        "/list-windows",
        headers={"Authorization": "Bearer wrong-token"},
        json={},
    )
    assert response.status_code == 401


def test_run_command_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/run-command", json={"hwnd": 1001, "command": "dir"})
    assert response.status_code == 401


def _mock_command_target(mock_find_window: Mock, monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock_find_window.return_value = CMD_WINDOW
    run_mock = Mock(return_value=CommandResult(output="C:\\> echo hello\r\nhello"))
    monkeypatch.setattr("src.main.run_command_sync", run_mock)
    return run_mock


def test_run_command_happy_path_returns_output(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_mock = _mock_command_target(mock_find_window, monkeypatch)

    response = client.post(
        "/run-command",
        headers=auth_headers,
        json={"hwnd": 1002, "command": "echo hello", "wait_s": 2.0},
    )

    assert response.status_code == 200
    assert response.json() == {"output": "C:\\> echo hello\r\nhello", "error": None}
    run_mock.assert_called_once_with(1002, "echo hello", 2.0)


def test_run_command_default_wait_s_forwarded(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_mock = _mock_command_target(mock_find_window, monkeypatch)

    response = client.post(
        "/run-command", headers=auth_headers, json={"hwnd": 1002, "command": "dir"}
    )

    assert response.status_code == 200
    run_mock.assert_called_once_with(1002, "dir", 1.0)


def test_run_command_window_not_found_returns_error(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_find_window.return_value = None
    run_mock = Mock()
    monkeypatch.setattr("src.main.run_command_sync", run_mock)

    response = client.post(
        "/run-command",
        headers=auth_headers,
        json={"title": "no-such-window", "command": "dir"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "output": "",
        "error": "window not found: title='no-such-window'",
    }
    run_mock.assert_not_called()


def test_run_command_primitive_failure_passthrough(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_mock = _mock_command_target(mock_find_window, monkeypatch)
    run_mock.return_value = CommandResult(error="capture failed: window minimized")

    response = client.post(
        "/run-command", headers=auth_headers, json={"hwnd": 1002, "command": "dir"}
    )

    assert response.status_code == 200
    assert response.json() == {"output": "", "error": "capture failed: window minimized"}


@pytest.mark.parametrize("wait_s", [0.0, 0.09, 60.1, 100])
def test_run_command_wait_s_out_of_bounds_returns_422(
    client: TestClient, auth_headers: dict[str, str], wait_s: float
) -> None:
    response = client.post(
        "/run-command",
        headers=auth_headers,
        json={"hwnd": 1002, "command": "dir", "wait_s": wait_s},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("wait_s", [0.1, 60])
def test_run_command_wait_s_bounds_accepted(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
    wait_s: float,
) -> None:
    run_mock = _mock_command_target(mock_find_window, monkeypatch)

    response = client.post(
        "/run-command",
        headers=auth_headers,
        json={"hwnd": 1002, "command": "dir", "wait_s": wait_s},
    )

    assert response.status_code == 200
    run_mock.assert_called_once_with(1002, "dir", wait_s)


def _mock_enumerate(mock_enum_windows: Mock) -> Mock:
    mock_enum_windows.return_value = list(FAKE_WINDOWS)
    return mock_enum_windows


def test_list_windows_returns_all_visible_windows(
    client: TestClient, auth_headers: dict[str, str], mock_enum_windows: Mock
) -> None:
    enumerate_mock = _mock_enumerate(mock_enum_windows)

    response = client.post("/list-windows", headers=auth_headers, json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["windows"] == [w.model_dump() for w in FAKE_WINDOWS]
    enumerate_mock.assert_called_once_with(visible_only=True)


def test_list_windows_title_filter(
    client: TestClient, auth_headers: dict[str, str], mock_enum_windows: Mock
) -> None:
    _mock_enumerate(mock_enum_windows)

    response = client.post(
        "/list-windows", headers=auth_headers, json={"title_filter": "NoTePaD"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert [w["hwnd"] for w in payload["windows"]] == [1001]


def test_list_windows_process_filter(
    client: TestClient, auth_headers: dict[str, str], mock_enum_windows: Mock
) -> None:
    _mock_enumerate(mock_enum_windows)

    response = client.post(
        "/list-windows", headers=auth_headers, json={"process_filter": "CMD"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert [w["hwnd"] for w in payload["windows"]] == [1002, 1003]


def test_list_windows_both_filters_combined(
    client: TestClient, auth_headers: dict[str, str], mock_enum_windows: Mock
) -> None:
    _mock_enumerate(mock_enum_windows)

    response = client.post(
        "/list-windows",
        headers=auth_headers,
        json={"title_filter": "build", "process_filter": "cmd.exe"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert [w["hwnd"] for w in payload["windows"]] == [1003]


def test_list_windows_no_match_returns_empty_list(
    client: TestClient, auth_headers: dict[str, str], mock_enum_windows: Mock
) -> None:
    _mock_enumerate(mock_enum_windows)

    response = client.post(
        "/list-windows", headers=auth_headers, json={"title_filter": "no-such-window"}
    )

    assert response.status_code == 200
    assert response.json() == {"windows": [], "error": None}


def _mock_popen(monkeypatch: pytest.MonkeyPatch, pid: int = 4321) -> tuple[Mock, Mock]:
    proc = Mock()
    proc.pid = pid
    proc.poll.return_value = None
    popen_mock = Mock(return_value=proc)
    monkeypatch.setattr("src.main.Popen", popen_mock)
    return popen_mock, proc


def test_open_app_happy_path_forwards_args_and_focuses(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    popen_mock, _ = _mock_popen(monkeypatch)
    by_pid_mock = Mock(return_value=CMD_WINDOW)
    monkeypatch.setattr("src.main.find_window_by_pid", by_pid_mock)
    find_mock = Mock(return_value=None)
    monkeypatch.setattr("src.main.find_window", find_mock)
    focus_mock = Mock()
    monkeypatch.setattr("src.main.focus_window", focus_mock)

    response = client.post(
        "/open-app",
        headers=auth_headers,
        json={"path": "cmd.exe", "args": ["/k", "echo hi"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "hwnd": 1002,
        "pid": 4321,
        "title": "Command Prompt",
        "error": None,
    }
    popen_mock.assert_called_once_with(["cmd.exe", "/k", "echo hi"])
    by_pid_mock.assert_called_with(4321)
    find_mock.assert_not_called()
    focus_mock.assert_called_once_with(1002)


def test_open_app_falls_back_to_title_match(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_popen(monkeypatch)
    monkeypatch.setattr("src.main.find_window_by_pid", Mock(return_value=None))
    mock_find_window.return_value = CMD_WINDOW
    focus_mock = Mock()
    monkeypatch.setattr("src.main.focus_window", focus_mock)

    response = client.post("/open-app", headers=auth_headers, json={"path": "cmd.exe"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["hwnd"] == 1002
    assert payload["pid"] == 4321
    mock_find_window.assert_called_with(title="cmd")
    focus_mock.assert_called_once_with(1002)


def test_open_app_executable_not_found_returns_error(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    popen_mock = Mock(side_effect=FileNotFoundError("no such file"))
    monkeypatch.setattr("src.main.Popen", popen_mock)

    response = client.post("/open-app", headers=auth_headers, json={"path": "no-such-app.exe"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["hwnd"] is None
    assert payload["pid"] is None
    assert "no-such-app.exe" in payload["error"]


def test_open_app_timeout_returns_error_and_leaves_process_running(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, proc = _mock_popen(monkeypatch)
    monkeypatch.setattr("src.main.find_window_by_pid", Mock(return_value=None))
    mock_find_window.return_value = None

    response = client.post(
        "/open-app",
        headers=auth_headers,
        json={"path": "cmd.exe", "wait_timeout_s": 0.3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["hwnd"] is None
    assert payload["pid"] == 4321
    assert "timed out" in payload["error"]
    proc.kill.assert_not_called()


def test_open_app_process_exits_before_window_returns_error(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, proc = _mock_popen(monkeypatch)
    proc.poll.return_value = 1
    proc.returncode = 1
    monkeypatch.setattr("src.main.find_window_by_pid", Mock(return_value=None))
    mock_find_window.return_value = None

    response = client.post("/open-app", headers=auth_headers, json={"path": "cmd.exe"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["hwnd"] is None
    assert payload["pid"] == 4321
    assert "exited with code 1" in payload["error"]


def test_start_recording_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/start-recording", json={"hwnd": 1001})
    assert response.status_code == 401


def _mock_recording(monkeypatch: pytest.MonkeyPatch, session_id: str = "sess-abc123") -> Mock:
    manager_mock = Mock()
    manager_mock.start.return_value = session_id
    monkeypatch.setattr("src.main.manager", manager_mock)
    return manager_mock


def test_start_recording_happy_path_returns_session_id(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager_mock = _mock_recording(monkeypatch)

    response = client.post(
        "/start-recording",
        headers=auth_headers,
        json={"hwnd": 1001, "client_area_only": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"session_id": "sess-abc123", "error": None}
    manager_mock.start.assert_called_once_with(1001, True, settings.fps)


def test_start_recording_fps_override_honored(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager_mock = _mock_recording(monkeypatch)

    response = client.post(
        "/start-recording", headers=auth_headers, json={"hwnd": 1001, "fps": 15}
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "sess-abc123"
    manager_mock.start.assert_called_once_with(1001, False, 15)


def test_start_recording_window_not_found_returns_error(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_find_window.return_value = None
    manager_mock = Mock()
    monkeypatch.setattr("src.main.manager", manager_mock)

    response = client.post(
        "/start-recording", headers=auth_headers, json={"title": "no-such-window"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == ""
    assert payload["error"] == "window not found: title='no-such-window'"
    manager_mock.start.assert_not_called()


def test_start_recording_duplicate_hwnd_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager_mock = _mock_recording(monkeypatch)
    manager_mock.start.side_effect = AlreadyRecordingError("already recording")

    response = client.post("/start-recording", headers=auth_headers, json={"hwnd": 1001})

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == ""
    assert payload["error"] == "already recording"


def test_stop_recording_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/stop-recording", json={"session_id": "sess-abc123"})
    assert response.status_code == 401


def test_stop_recording_happy_path_returns_mp4_and_duration(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager_mock = Mock()
    manager_mock.stop.return_value = StopResult(
        mp4_path="recordings/record_sess-abc123.mp4", duration_s=5.2
    )
    monkeypatch.setattr("src.main.manager", manager_mock)

    response = client.post(
        "/stop-recording", headers=auth_headers, json={"session_id": "sess-abc123"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "mp4_path": "recordings/record_sess-abc123.mp4",
        "duration_s": 5.2,
        "error": None,
    }
    manager_mock.stop.assert_called_once_with("sess-abc123")


def test_stop_recording_unknown_session_returns_error(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager_mock = Mock()
    manager_mock.stop.return_value = StopResult(error="unknown session_id")
    monkeypatch.setattr("src.main.manager", manager_mock)

    response = client.post(
        "/stop-recording", headers=auth_headers, json={"session_id": "does-not-exist"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"mp4_path": "", "duration_s": 0.0, "error": "unknown session_id"}


def test_stop_recording_session_error_passthrough(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager_mock = Mock()
    manager_mock.stop.return_value = StopResult(
        mp4_path="recordings/record_sess-abc123.mp4",
        duration_s=3.1,
        error="window closed",
    )
    monkeypatch.setattr("src.main.manager", manager_mock)

    response = client.post(
        "/stop-recording", headers=auth_headers, json={"session_id": "sess-abc123"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "mp4_path": "recordings/record_sess-abc123.mp4",
        "duration_s": 3.1,
        "error": "window closed",
    }


def test_stop_recording_re_stop_returns_unknown_session_error(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager_mock = Mock()
    manager_mock.stop.side_effect = [
        StopResult(mp4_path="recordings/record_sess-abc123.mp4", duration_s=5.2),
        StopResult(error="unknown session_id"),
    ]
    monkeypatch.setattr("src.main.manager", manager_mock)

    first = client.post(
        "/stop-recording", headers=auth_headers, json={"session_id": "sess-abc123"}
    )
    second = client.post(
        "/stop-recording", headers=auth_headers, json={"session_id": "sess-abc123"}
    )

    assert first.status_code == 200
    assert first.json()["error"] is None
    assert second.status_code == 200
    assert second.json()["error"] == "unknown session_id"


def test_type_text_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/type-text", json={"hwnd": 1001, "text": "hi"})
    assert response.status_code == 401


def _mock_typing_target(monkeypatch: pytest.MonkeyPatch) -> Mock:
    type_mock = Mock()
    monkeypatch.setattr("src.main.type_into_window", type_mock)
    return type_mock


def test_type_text_happy_path_types_into_resolved_window(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    type_mock = _mock_typing_target(monkeypatch)

    response = client.post(
        "/type-text", headers=auth_headers, json={"title": "notepad", "text": "hello"}
    )

    assert response.status_code == 200
    assert response.json() == {"error": None}
    type_mock.assert_called_once_with(1001, "hello", False)


def test_type_text_press_enter_forwarded(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    type_mock = _mock_typing_target(monkeypatch)

    response = client.post(
        "/type-text",
        headers=auth_headers,
        json={"hwnd": 1001, "text": "dir", "press_enter": True},
    )

    assert response.status_code == 200
    assert response.json() == {"error": None}
    type_mock.assert_called_once_with(1001, "dir", True)


def test_type_text_window_not_found_returns_error(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_find_window.return_value = None
    type_mock = Mock()
    monkeypatch.setattr("src.main.type_into_window", type_mock)

    response = client.post(
        "/type-text", headers=auth_headers, json={"title": "no-such-window", "text": "hi"}
    )

    assert response.status_code == 200
    assert response.json() == {"error": "window not found: title='no-such-window'"}
    type_mock.assert_not_called()


def test_type_text_typing_failure_returns_error(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    type_mock = _mock_typing_target(monkeypatch)
    type_mock.side_effect = InvalidWindowError("invalid window: hwnd 1001")

    response = client.post(
        "/type-text", headers=auth_headers, json={"hwnd": 1001, "text": "hi"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] == "typing failed: invalid window: hwnd 1001"


def test_capture_output_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/capture-output", json={"hwnd": 1001})
    assert response.status_code == 401


def _mock_capture_target(monkeypatch: pytest.MonkeyPatch) -> Mock:
    capture_mock = Mock(return_value="C:\\> echo hello\r\nhello")
    monkeypatch.setattr("src.main.capture_window_text", capture_mock)
    return capture_mock


def test_capture_output_happy_path_returns_ocr_text(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_find_window.return_value = CMD_WINDOW
    capture_mock = _mock_capture_target(monkeypatch)

    response = client.post("/capture-output", headers=auth_headers, json={"title": "cmd"})

    assert response.status_code == 200
    assert response.json() == {"text": "C:\\> echo hello\r\nhello", "error": None}
    capture_mock.assert_called_once_with(1002, True)


def test_capture_output_window_not_found_returns_error(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_find_window.return_value = None
    capture_mock = Mock()
    monkeypatch.setattr("src.main.capture_window_text", capture_mock)

    response = client.post(
        "/capture-output", headers=auth_headers, json={"title": "no-such-window"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "",
        "error": "window not found: title='no-such-window'",
    }
    capture_mock.assert_not_called()


def test_capture_output_minimized_window_returns_error(
    client: TestClient,
    auth_headers: dict[str, str],
    mock_find_window: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_find_window.return_value = CMD_WINDOW
    capture_mock = _mock_capture_target(monkeypatch)
    capture_mock.side_effect = WindowMinimizedError("window minimized")

    response = client.post("/capture-output", headers=auth_headers, json={"hwnd": 1002})

    assert response.status_code == 200
    assert response.json() == {"text": "", "error": "window minimized"}


def test_mcp_endpoint_without_token_returns_401(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert response.status_code == 401


def test_mcp_endpoint_with_wrong_token_returns_401(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer wrong-token",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert response.status_code == 401


def test_mcp_initialize_returns_protocol_version_and_server_info(
    client: TestClient,
) -> None:
    response = client.post(
        "/mcp",
        headers=_mcp_headers(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.0.0"},
            },
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["protocolVersion"] == LATEST_PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "recorder-mcp"
    assert result["serverInfo"]["version"]
    assert "tools" in result["capabilities"]


def test_mcp_handshake_lists_seven_tools(client: TestClient) -> None:
    session_id = _mcp_handshake(client)

    response = client.post(
        "/mcp",
        headers=_mcp_headers(session_id),
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    assert response.status_code == 200, response.text
    tools = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert tools == EXPECTED_TOOLS
    assert "health" not in tools


def test_mcp_tools_call_list_windows_returns_mocked_windows(
    client: TestClient, mock_enum_windows: Mock
) -> None:
    session_id = _mcp_handshake(client)
    enumerate_mock = _mock_enumerate(mock_enum_windows)

    response = client.post(
        "/mcp",
        headers=_mcp_headers(session_id),
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_windows", "arguments": {}},
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False
    content = result["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    payload = json.loads(content[0]["text"])
    assert payload["error"] is None
    assert payload["windows"] == [w.model_dump() for w in FAKE_WINDOWS]
    enumerate_mock.assert_called_once_with(visible_only=True)


def test_mcp_tools_call_list_windows_forwards_arguments(
    client: TestClient, mock_enum_windows: Mock
) -> None:
    session_id = _mcp_handshake(client)
    _mock_enumerate(mock_enum_windows)

    response = client.post(
        "/mcp",
        headers=_mcp_headers(session_id),
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_windows",
                "arguments": {"process_filter": "CMD"},
            },
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] is None
    assert [w["hwnd"] for w in payload["windows"]] == [1002, 1003]


def test_missing_tesseract_fails_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "tesseract_cmd", tmp_path / "missing" / "tesseract.exe")
    with pytest.raises(RuntimeError, match="TESSERACT_CMD"), TestClient(app):
        pass

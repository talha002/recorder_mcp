from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from mcp.types import LATEST_PROTOCOL_VERSION

from src.config import settings
from src.main import app
from src.models import WindowInfo
from src.recorder import AlreadyRecordingError

AUTH_HEADERS = {"Authorization": "Bearer test-token"}

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
    WindowInfo(hwnd=1001, title="Untitled - Notepad", process="notepad.exe"),
    WindowInfo(hwnd=1002, title="Command Prompt", process="cmd.exe"),
    WindowInfo(hwnd=1003, title="cmd - build", process="cmd.exe"),
]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    fake_tesseract = tmp_path / "tesseract.exe"
    fake_tesseract.touch()
    monkeypatch.setattr(settings, "tesseract_cmd", fake_tesseract)
    with TestClient(app) as test_client:
        yield test_client


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


def test_tool_route_with_token_returns_not_implemented(client: TestClient) -> None:
    response = client.post("/type-text", headers=AUTH_HEADERS, json={"hwnd": 1001, "text": "hi"})
    assert response.status_code == 200
    assert response.json()["error"] == "not implemented"


def _mock_enumerate(monkeypatch: pytest.MonkeyPatch) -> Mock:
    enumerate_mock = Mock(return_value=list(FAKE_WINDOWS))
    monkeypatch.setattr("src.main.enumerate_windows", enumerate_mock)
    return enumerate_mock


def test_list_windows_returns_all_visible_windows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    enumerate_mock = _mock_enumerate(monkeypatch)

    response = client.post("/list-windows", headers=AUTH_HEADERS, json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["windows"] == [w.model_dump() for w in FAKE_WINDOWS]
    enumerate_mock.assert_called_once_with(visible_only=True)


def test_list_windows_title_filter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_enumerate(monkeypatch)

    response = client.post(
        "/list-windows", headers=AUTH_HEADERS, json={"title_filter": "NoTePaD"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert [w["hwnd"] for w in payload["windows"]] == [1001]


def test_list_windows_process_filter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_enumerate(monkeypatch)

    response = client.post(
        "/list-windows", headers=AUTH_HEADERS, json={"process_filter": "CMD"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert [w["hwnd"] for w in payload["windows"]] == [1002, 1003]


def test_list_windows_both_filters_combined(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_enumerate(monkeypatch)

    response = client.post(
        "/list-windows",
        headers=AUTH_HEADERS,
        json={"title_filter": "build", "process_filter": "cmd.exe"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert [w["hwnd"] for w in payload["windows"]] == [1003]


def test_list_windows_no_match_returns_empty_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_enumerate(monkeypatch)

    response = client.post(
        "/list-windows", headers=AUTH_HEADERS, json={"title_filter": "no-such-window"}
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
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen_mock, _ = _mock_popen(monkeypatch)
    info = WindowInfo(hwnd=1002, title="Command Prompt", process="cmd.exe")
    by_pid_mock = Mock(return_value=info)
    monkeypatch.setattr("src.main.find_window_by_pid", by_pid_mock)
    find_mock = Mock(return_value=None)
    monkeypatch.setattr("src.main.find_window", find_mock)
    focus_mock = Mock()
    monkeypatch.setattr("src.main.focus_window", focus_mock)

    response = client.post(
        "/open-app",
        headers=AUTH_HEADERS,
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
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_popen(monkeypatch)
    monkeypatch.setattr("src.main.find_window_by_pid", Mock(return_value=None))
    info = WindowInfo(hwnd=1002, title="Command Prompt", process="cmd.exe")
    find_mock = Mock(return_value=info)
    monkeypatch.setattr("src.main.find_window", find_mock)
    focus_mock = Mock()
    monkeypatch.setattr("src.main.focus_window", focus_mock)

    response = client.post("/open-app", headers=AUTH_HEADERS, json={"path": "cmd.exe"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["hwnd"] == 1002
    assert payload["pid"] == 4321
    find_mock.assert_called_with(title="cmd")
    focus_mock.assert_called_once_with(1002)


def test_open_app_executable_not_found_returns_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen_mock = Mock(side_effect=FileNotFoundError("no such file"))
    monkeypatch.setattr("src.main.Popen", popen_mock)

    response = client.post("/open-app", headers=AUTH_HEADERS, json={"path": "no-such-app.exe"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["hwnd"] is None
    assert payload["pid"] is None
    assert "no-such-app.exe" in payload["error"]


def test_open_app_timeout_returns_error_and_leaves_process_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, proc = _mock_popen(monkeypatch)
    monkeypatch.setattr("src.main.find_window_by_pid", Mock(return_value=None))
    monkeypatch.setattr("src.main.find_window", Mock(return_value=None))

    response = client.post(
        "/open-app",
        headers=AUTH_HEADERS,
        json={"path": "cmd.exe", "wait_timeout_s": 0.3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["hwnd"] is None
    assert payload["pid"] == 4321
    assert "timed out" in payload["error"]
    proc.kill.assert_not_called()


def test_open_app_process_exits_before_window_returns_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, proc = _mock_popen(monkeypatch)
    proc.poll.return_value = 1
    proc.returncode = 1
    monkeypatch.setattr("src.main.find_window_by_pid", Mock(return_value=None))
    monkeypatch.setattr("src.main.find_window", Mock(return_value=None))

    response = client.post("/open-app", headers=AUTH_HEADERS, json={"path": "cmd.exe"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["hwnd"] is None
    assert payload["pid"] == 4321
    assert "exited with code 1" in payload["error"]


def test_start_recording_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/start-recording", json={"hwnd": 1001})
    assert response.status_code == 401


def _mock_recording(monkeypatch: pytest.MonkeyPatch, session_id: str = "sess-abc123") -> Mock:
    info = WindowInfo(hwnd=1001, title="Untitled - Notepad", process="notepad.exe")
    monkeypatch.setattr("src.main.find_window", Mock(return_value=info))
    manager_mock = Mock()
    manager_mock.start.return_value = session_id
    monkeypatch.setattr("src.main.manager", manager_mock)
    return manager_mock


def test_start_recording_happy_path_returns_session_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager_mock = _mock_recording(monkeypatch)

    response = client.post(
        "/start-recording",
        headers=AUTH_HEADERS,
        json={"hwnd": 1001, "client_area_only": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"session_id": "sess-abc123", "error": None}
    manager_mock.start.assert_called_once_with(1001, True, settings.fps)


def test_start_recording_fps_override_honored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager_mock = _mock_recording(monkeypatch)

    response = client.post("/start-recording", headers=AUTH_HEADERS, json={"hwnd": 1001, "fps": 15})

    assert response.status_code == 200
    assert response.json()["session_id"] == "sess-abc123"
    manager_mock.start.assert_called_once_with(1001, False, 15)


def test_start_recording_window_not_found_returns_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.main.find_window", Mock(return_value=None))
    manager_mock = Mock()
    monkeypatch.setattr("src.main.manager", manager_mock)

    response = client.post(
        "/start-recording", headers=AUTH_HEADERS, json={"title": "no-such-window"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == ""
    assert payload["error"] == "window not found: title='no-such-window'"
    manager_mock.start.assert_not_called()


def test_start_recording_duplicate_hwnd_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager_mock = _mock_recording(monkeypatch)
    manager_mock.start.side_effect = AlreadyRecordingError("already recording")

    response = client.post("/start-recording", headers=AUTH_HEADERS, json={"hwnd": 1001})

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == ""
    assert payload["error"] == "already recording"


def test_mcp_endpoint_without_token_returns_401(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert response.status_code == 401


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


def test_missing_tesseract_fails_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "tesseract_cmd", tmp_path / "missing" / "tesseract.exe")
    with pytest.raises(RuntimeError, match="TESSERACT_CMD"), TestClient(app):
        pass

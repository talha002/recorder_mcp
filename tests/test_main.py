from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from mcp.types import LATEST_PROTOCOL_VERSION

from src.config import settings
from src.main import app
from src.models import WindowInfo

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
    response = client.post("/open-app", headers=AUTH_HEADERS, json={"path": "cmd.exe"})
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

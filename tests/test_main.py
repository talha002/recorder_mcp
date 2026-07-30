from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.types import LATEST_PROTOCOL_VERSION

from src.config import settings
from src.main import app

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
    response = client.post("/list-windows", headers=AUTH_HEADERS, json={})
    assert response.status_code == 200
    assert response.json()["error"] == "not implemented"


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

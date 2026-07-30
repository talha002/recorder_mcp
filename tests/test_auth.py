from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.auth import verify_mcp_token


def _make_client() -> TestClient:
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(verify_mcp_token)])
    def probe() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_missing_header_returns_401() -> None:
    response = _make_client().get("/probe")
    assert response.status_code == 401


def test_wrong_token_returns_401() -> None:
    response = _make_client().get("/probe", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_non_bearer_scheme_returns_401() -> None:
    response = _make_client().get("/probe", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401


def test_correct_token_passes() -> None:
    response = _make_client().get("/probe", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}

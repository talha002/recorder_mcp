from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings


def test_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("FPS", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    s = Settings(mcp_api_token="token", _env_file=None)
    assert s.tesseract_cmd == Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    assert s.output_dir == Path("recordings")
    assert s.fps == 30
    assert s.host == "127.0.0.1"
    assert s.port == 8000


def test_env_file_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_API_TOKEN", raising=False)
    monkeypatch.delenv("FPS", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_API_TOKEN=from-file\nFPS=12\nPORT=9001\n", encoding="utf-8")
    s = Settings(_env_file=env_file)
    assert s.mcp_api_token == "from-file"
    assert s.fps == 12
    assert s.port == 9001


def test_env_var_beats_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_API_TOKEN=from-file\nFPS=12\n", encoding="utf-8")
    monkeypatch.setenv("FPS", "24")
    s = Settings(_env_file=env_file)
    assert s.fps == 24


def test_missing_token_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_API_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="mcp_api_token"):
        Settings(_env_file=tmp_path / "nonexistent.env")


def test_output_dir_created_on_demand(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "recordings"
    s = Settings(mcp_api_token="token", output_dir=target, _env_file=None)
    assert not target.exists()
    result = s.ensure_output_dir()
    assert target.is_dir()
    assert result == target

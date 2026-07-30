import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import av
import mss
import pyautogui
import pytesseract
import pytest
from fastapi.testclient import TestClient
from PIL import Image

os.environ.setdefault("MCP_API_TOKEN", "test-token")

from src.config import settings
from src.main import app, mcp
from src.models import WindowInfo

TEST_TOKEN = "test-token"
CANNED_OCR_TEXT = "recognized text"

FIXED_WINDOW = WindowInfo(hwnd=1001, title="Untitled - Notepad", process="notepad.exe")
CMD_WINDOW = WindowInfo(hwnd=1002, title="Command Prompt", process="cmd.exe")


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    fake_tesseract = tmp_path / "tesseract.exe"
    fake_tesseract.touch()
    monkeypatch.setattr(settings, "tesseract_cmd", fake_tesseract)
    with TestClient(app) as test_client:
        yield test_client
    if mcp._http_transport is not None:
        mcp._http_transport._manager_started = False


@pytest.fixture
def mock_find_window(monkeypatch: pytest.MonkeyPatch) -> Mock:
    find_mock = Mock(return_value=FIXED_WINDOW)
    monkeypatch.setattr("src.main.find_window", find_mock)
    return find_mock


@pytest.fixture
def mock_enum_windows(monkeypatch: pytest.MonkeyPatch) -> Mock:
    enumerate_mock = Mock(return_value=[FIXED_WINDOW])
    monkeypatch.setattr("src.main.enumerate_windows", enumerate_mock)
    return enumerate_mock


class FakeShot:
    def __init__(self, width: int, height: int, pixel: tuple[int, int, int, int]) -> None:
        self.width = width
        self.height = height
        self.bgra = bytes(pixel) * (width * height)


class FakeSct:
    def __init__(self, pixel: tuple[int, int, int, int] = (7, 3, 1, 255)) -> None:
        self.regions: list[dict[str, int]] = []
        self.closed = False
        self._pixel = pixel

    def grab(self, monitor: dict[str, Any]) -> FakeShot:
        region = {
            "left": int(monitor["left"]),
            "top": int(monitor["top"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
        }
        self.regions.append(region)
        return FakeShot(region["width"], region["height"], self._pixel)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def mock_mss(monkeypatch: pytest.MonkeyPatch) -> list[FakeSct]:
    instances: list[FakeSct] = []

    def factory(**kwargs: Any) -> FakeSct:
        sct = FakeSct()
        instances.append(sct)
        return sct

    monkeypatch.setattr(mss, "MSS", factory)
    return instances


class FakeFrame:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.pts = -1


class FakeStream:
    def __init__(self, codec: str, rate: int | None) -> None:
        self.codec = codec
        self.rate = rate
        self.width = 0
        self.height = 0
        self.pix_fmt = ""
        self.options: dict[str, str] = {}
        self.encoded: list[tuple[int, tuple[int, int]]] = []
        self.flushed = False

    def encode(self, frame: FakeFrame | None = None) -> list[object]:
        if frame is None:
            self.flushed = True
            return []
        self.encoded.append((frame.pts, frame.size))
        return [object()]


class FakeContainer:
    def __init__(self, path: str, mode: str) -> None:
        self.path = Path(path)
        self.mode = mode
        self.path.touch()
        self.streams: list[FakeStream] = []
        self.muxed = 0
        self.closed = False

    def add_stream(self, codec: str, rate: int | None = None, **kwargs: Any) -> FakeStream:
        stream = FakeStream(codec, rate)
        self.streams.append(stream)
        return stream

    def mux(self, packet: object) -> None:
        self.muxed += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def mock_av(monkeypatch: pytest.MonkeyPatch) -> list[FakeContainer]:
    containers: list[FakeContainer] = []

    def fake_open(file: str, mode: str = "r", **kwargs: Any) -> FakeContainer:
        container = FakeContainer(file, mode)
        containers.append(container)
        return container

    def from_ndarray(array: Any, format: str = "rgb24", **kwargs: Any) -> FakeFrame:
        return FakeFrame((int(array.shape[1]), int(array.shape[0])))

    monkeypatch.setattr(av, "open", fake_open)
    monkeypatch.setattr(av.VideoFrame, "from_ndarray", staticmethod(from_ndarray))
    return containers


@pytest.fixture
def mock_pytesseract(monkeypatch: pytest.MonkeyPatch) -> list[Image.Image]:
    received: list[Image.Image] = []

    def fake_image_to_string(img: Image.Image) -> str:
        received.append(img)
        return CANNED_OCR_TEXT

    monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)
    return received


@pytest.fixture
def mock_pyautogui(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        pyautogui,
        "write",
        lambda text, interval=0.0: calls.append(("write", text, interval)),
    )
    monkeypatch.setattr(pyautogui, "press", lambda key: calls.append(("press", key)))
    monkeypatch.setattr(pyautogui, "click", lambda x, y: calls.append(("click", (x, y))))
    return calls

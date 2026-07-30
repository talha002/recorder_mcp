from collections.abc import Callable
from typing import Any

import mss
import numpy as np
import pytesseract
import pytest
import win32gui
from PIL import Image

from src import ocr
from src.config import settings


@pytest.fixture
def fake_win32(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    state = {"iconic": False}
    monkeypatch.setattr(win32gui, "IsIconic", lambda hwnd: int(state["iconic"]))
    monkeypatch.setattr(
        ocr,
        "get_client_rect_screen",
        lambda hwnd: {"left": 5, "top": 25, "width": 60, "height": 40},
    )
    monkeypatch.setattr(
        ocr,
        "get_window_rect",
        lambda hwnd: {"left": 0, "top": 0, "width": 64, "height": 48},
    )
    return state


class _FakeShot:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.bgra = bytes((200, 200, 200, 255)) * (width * height)


class _FakeSct:
    def __init__(self) -> None:
        self.regions: list[dict[str, int]] = []
        self.closed = False

    def grab(self, monitor: dict[str, Any]) -> _FakeShot:
        region = {
            "left": int(monitor["left"]),
            "top": int(monitor["top"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
        }
        self.regions.append(region)
        return _FakeShot(region["width"], region["height"])

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_mss(monkeypatch: pytest.MonkeyPatch) -> list[_FakeSct]:
    instances: list[_FakeSct] = []

    def factory(**kwargs: Any) -> _FakeSct:
        sct = _FakeSct()
        instances.append(sct)
        return sct

    monkeypatch.setattr(mss, "MSS", factory)
    return instances


@pytest.fixture
def fake_tesseract(monkeypatch: pytest.MonkeyPatch) -> list[Image.Image]:
    received: list[Image.Image] = []

    def fake_image_to_string(img: Image.Image) -> str:
        received.append(img)
        return "recognized text"

    monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)
    return received


def _synthetic(bg: int, fg: int) -> Image.Image:
    arr = np.full((40, 80), bg, dtype=np.uint8)
    arr[10:30, 10:30] = fg
    return Image.fromarray(arr)


def _mean_luminance(img: Image.Image) -> float:
    return float(np.asarray(img, dtype=np.float64).mean())


def test_invert_if_dark_inverts_light_on_dark() -> None:
    result = ocr.invert_if_dark(_synthetic(bg=20, fg=240))
    assert _mean_luminance(result) > ocr.DARK_MEAN_LUMINANCE


def test_invert_if_dark_keeps_dark_on_light() -> None:
    img = _synthetic(bg=230, fg=20)
    result = ocr.invert_if_dark(img)
    assert result is img


def test_upscale_doubles_dimensions() -> None:
    img = Image.new("L", (10, 6), color=128)
    assert ocr.upscale(img).size == (20, 12)
    assert ocr.upscale(img, factor=3).size == (30, 18)


def test_to_grayscale_returns_l_mode() -> None:
    img = Image.new("RGB", (10, 6), color=(10, 200, 90))
    assert ocr.to_grayscale(img).mode == "L"


def test_otsu_threshold_output_dtype_and_range() -> None:
    result = ocr.otsu_threshold(_synthetic(bg=220, fg=30))
    arr = np.asarray(result)
    assert result.mode == "L"
    assert arr.dtype == np.uint8
    assert set(np.unique(arr)) <= {0, 255}


def test_otsu_threshold_splits_bimodal_image() -> None:
    arr = np.full((20, 40), 220, dtype=np.uint8)
    arr[:, :20] = 30
    result = np.asarray(ocr.otsu_threshold(Image.fromarray(arr)))
    assert (result[:, :20] == 0).all()
    assert (result[:, 20:] == 255).all()


def test_capture_pipeline_order(
    monkeypatch: pytest.MonkeyPatch,
    fake_win32: dict[str, bool],
    fake_mss: list[_FakeSct],
    fake_tesseract: list[Image.Image],
) -> None:
    calls: list[str] = []

    def spy(name: str) -> Callable[..., Image.Image]:
        def inner(img: Image.Image, *args: Any, **kwargs: Any) -> Image.Image:
            calls.append(name)
            return img

        return inner

    monkeypatch.setattr(ocr, "upscale", spy("upscale"))
    monkeypatch.setattr(ocr, "to_grayscale", spy("to_grayscale"))
    monkeypatch.setattr(ocr, "invert_if_dark", spy("invert_if_dark"))
    monkeypatch.setattr(ocr, "otsu_threshold", spy("otsu_threshold"))

    ocr.capture_window_text(100)
    assert calls == ["upscale", "to_grayscale", "invert_if_dark", "otsu_threshold"]


def test_capture_passes_preprocessed_image_to_tesseract(
    monkeypatch: pytest.MonkeyPatch,
    fake_win32: dict[str, bool],
    fake_mss: list[_FakeSct],
    fake_tesseract: list[Image.Image],
) -> None:
    sentinel = Image.new("L", (4, 4), color=0)
    monkeypatch.setattr(ocr, "otsu_threshold", lambda img: sentinel)

    text = ocr.capture_window_text(100)
    assert fake_tesseract == [sentinel]
    assert text == "recognized text"


def test_tesseract_cmd_wired_from_settings() -> None:
    assert pytesseract.pytesseract.tesseract_cmd == str(settings.tesseract_cmd)


def test_capture_minimized_window_raises_without_grab(
    fake_win32: dict[str, bool],
    fake_mss: list[_FakeSct],
    fake_tesseract: list[Image.Image],
) -> None:
    fake_win32["iconic"] = True
    with pytest.raises(ocr.WindowMinimizedError, match="window minimized"):
        ocr.capture_window_text(100)
    assert fake_mss == []
    assert fake_tesseract == []


def test_capture_uses_client_rect_by_default(
    fake_win32: dict[str, bool],
    fake_mss: list[_FakeSct],
    fake_tesseract: list[Image.Image],
) -> None:
    ocr.capture_window_text(100)
    assert fake_mss[0].regions == [{"left": 5, "top": 25, "width": 60, "height": 40}]
    assert fake_mss[0].closed is True


def test_capture_uses_window_rect_when_client_area_only_false(
    fake_win32: dict[str, bool],
    fake_mss: list[_FakeSct],
    fake_tesseract: list[Image.Image],
) -> None:
    ocr.capture_window_text(100, client_area_only=False)
    assert fake_mss[0].regions == [{"left": 0, "top": 0, "width": 64, "height": 48}]

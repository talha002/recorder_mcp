import time
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

import av
import mss
import pytest
import win32gui

from src import recorder
from src.config import settings
from src.recorder import RecordingManager


class _Win32State:
    def __init__(self) -> None:
        self.valid: dict[int, bool] = {100: True, 200: True}
        self.iconic: dict[int, bool] = {100: False, 200: False}
        self.rects: dict[int, tuple[int, int, int, int]] = {
            100: (0, 0, 64, 48),
            200: (500, 300, 564, 348),
        }
        self.client_rects: dict[int, tuple[int, int, int, int]] = {
            100: (0, 0, 60, 40),
            200: (0, 0, 60, 40),
        }
        self.client_origins: dict[int, tuple[int, int]] = {100: (5, 25), 200: (505, 325)}


@pytest.fixture
def output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    return tmp_path


@pytest.fixture
def fake_win32(monkeypatch: pytest.MonkeyPatch) -> _Win32State:
    state = _Win32State()
    monkeypatch.setattr(win32gui, "IsWindow", lambda hwnd: int(state.valid.get(hwnd, False)))
    monkeypatch.setattr(win32gui, "IsIconic", lambda hwnd: int(state.iconic.get(hwnd, False)))
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda hwnd: state.rects[hwnd])
    monkeypatch.setattr(win32gui, "GetClientRect", lambda hwnd: state.client_rects[hwnd])
    monkeypatch.setattr(win32gui, "ClientToScreen", lambda hwnd, pt: state.client_origins[hwnd])
    return state


class _FakeShot:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.bgra = bytes((7, 3, 1, 255)) * (width * height)


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


class _FakeFrame:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.pts = -1


class _FakeStream:
    def __init__(self, codec: str, rate: int | None) -> None:
        self.codec = codec
        self.rate = rate
        self.width = 0
        self.height = 0
        self.pix_fmt = ""
        self.options: dict[str, str] = {}
        self.encoded: list[tuple[int, tuple[int, int]]] = []
        self.flushed = False

    def encode(self, frame: _FakeFrame | None = None) -> list[object]:
        if frame is None:
            self.flushed = True
            return []
        self.encoded.append((frame.pts, frame.size))
        return [object()]


class _FakeContainer:
    def __init__(self, path: str, mode: str) -> None:
        self.path = Path(path)
        self.mode = mode
        self.path.touch()
        self.streams: list[_FakeStream] = []
        self.muxed = 0
        self.closed = False

    def add_stream(self, codec: str, rate: int | None = None, **kwargs: Any) -> _FakeStream:
        stream = _FakeStream(codec, rate)
        self.streams.append(stream)
        return stream

    def mux(self, packet: object) -> None:
        self.muxed += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_av(monkeypatch: pytest.MonkeyPatch) -> list[_FakeContainer]:
    containers: list[_FakeContainer] = []

    def fake_open(file: str, mode: str = "r", **kwargs: Any) -> _FakeContainer:
        container = _FakeContainer(file, mode)
        containers.append(container)
        return container

    def from_ndarray(array: Any, format: str = "rgb24", **kwargs: Any) -> _FakeFrame:
        return _FakeFrame((int(array.shape[1]), int(array.shape[0])))

    monkeypatch.setattr(av, "open", fake_open)
    monkeypatch.setattr(av.VideoFrame, "from_ndarray", staticmethod(from_ndarray))
    return containers


def _wait_for(predicate: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _encoded_count(containers: list[_FakeContainer]) -> int:
    if not containers or not containers[0].streams:
        return 0
    return len(containers[0].streams[0].encoded)


def test_start_stop_writes_frames_and_closes_cleanly(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)

    assert session_id in manager.active()
    assert _wait_for(lambda: _encoded_count(fake_av) >= 3)

    result = manager.stop(session_id)

    assert result.error is None
    assert result.duration_s > 0
    assert result.mp4_path == str(output_dir / f"record_{session_id}.mp4")
    stream = fake_av[0].streams[0]
    assert stream.codec == "h264"
    assert stream.rate == settings.fps
    assert (stream.width, stream.height) == (64, 48)
    assert stream.pix_fmt == "yuv420p"
    assert stream.options == {"crf": "23", "preset": "fast"}
    assert stream.flushed
    assert fake_av[0].closed
    assert fake_mss[0].closed
    assert manager.active() == []


def test_pts_are_strictly_increasing(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    assert _wait_for(lambda: _encoded_count(fake_av) >= 5)
    manager.stop(session_id)

    pts_values = [pts for pts, _ in fake_av[0].streams[0].encoded]
    assert pts_values[0] >= 0
    assert all(later > earlier for earlier, later in pairwise(pts_values))


def test_frames_resized_to_locked_geometry_on_window_resize(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    assert _wait_for(lambda: _encoded_count(fake_av) >= 1)

    fake_win32.rects[100] = (10, 10, 42, 27)
    assert _wait_for(lambda: _encoded_count(fake_av) >= 3)
    manager.stop(session_id)

    sizes = {size for _, size in fake_av[0].streams[0].encoded}
    assert sizes == {(64, 48)}


def test_window_closed_mid_recording_keeps_partial_and_flushes(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    assert _wait_for(lambda: _encoded_count(fake_av) >= 2)

    fake_win32.valid[100] = False
    assert _wait_for(lambda: bool(fake_av) and fake_av[0].closed)

    result = manager.stop(session_id)
    assert result.error == "window closed"
    assert len(fake_av[0].streams[0].encoded) >= 2
    assert fake_av[0].streams[0].flushed
    assert Path(result.mp4_path).exists()


def test_minimized_whole_time_yields_no_frames_error(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    fake_win32.iconic[100] = True
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    time.sleep(0.15)

    result = manager.stop(session_id)

    assert result.error == "no frames captured"
    assert fake_av == []
    assert not Path(result.mp4_path).exists()


def test_capture_failure_deletes_empty_file(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: _FakeStream, frame: _FakeFrame | None = None) -> list[object]:
        raise RuntimeError("encoder exploded")

    monkeypatch.setattr(_FakeStream, "encode", boom)
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    assert _wait_for(lambda: bool(fake_av) and fake_av[0].closed)

    result = manager.stop(session_id)
    assert result.error is not None and result.error.startswith("capture failed")
    assert not Path(result.mp4_path).exists()


def test_duplicate_hwnd_rejected_until_stopped(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)

    with pytest.raises(recorder.AlreadyRecordingError, match="already recording"):
        manager.start(hwnd=100)

    other_id = manager.start(hwnd=200)
    assert other_id != session_id
    manager.stop(other_id)
    manager.stop(session_id)

    restarted_id = manager.start(hwnd=100)
    manager.stop(restarted_id)


def test_concurrent_sessions_on_distinct_hwnds(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    manager = RecordingManager()
    first_id = manager.start(hwnd=100)
    second_id = manager.start(hwnd=200)
    assert _wait_for(lambda: len(fake_av) >= 2)
    assert _wait_for(
        lambda: all(len(c.streams) and len(c.streams[0].encoded) >= 2 for c in fake_av)
    )

    first = manager.stop(first_id)
    second = manager.stop(second_id)

    assert first.error is None and second.error is None
    assert first.mp4_path != second.mp4_path
    region_lefts = [{region["left"] for region in sct.regions} for sct in fake_mss]
    assert {0} in region_lefts
    assert {500} in region_lefts


def test_client_area_only_grabs_client_rect(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100, client_area_only=True)
    assert _wait_for(lambda: _encoded_count(fake_av) >= 1)
    manager.stop(session_id)

    assert fake_mss[0].regions[0] == {"left": 5, "top": 25, "width": 60, "height": 40}
    stream = fake_av[0].streams[0]
    assert (stream.width, stream.height) == (60, 40)


def test_custom_fps_is_used_for_stream_rate(
    output_dir: Path,
    fake_win32: _Win32State,
    fake_mss: list[_FakeSct],
    fake_av: list[_FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100, fps=10)
    assert _wait_for(lambda: _encoded_count(fake_av) >= 1)
    manager.stop(session_id)

    assert fake_av[0].streams[0].rate == 10


def test_stop_unknown_session_returns_error() -> None:
    result = RecordingManager().stop("does-not-exist")
    assert result.error == "unknown session_id"
    assert result.mp4_path == ""
    assert result.duration_s == 0.0


def test_module_singleton_is_recording_manager() -> None:
    assert isinstance(recorder.manager, RecordingManager)

import time
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path

import pytest
import win32gui

from src import recorder
from src.config import settings
from src.recorder import RecordingManager
from tests.conftest import FakeContainer, FakeFrame, FakeSct, FakeStream


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


def _wait_for(predicate: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _encoded_count(containers: list[FakeContainer]) -> int:
    if not containers or not containers[0].streams:
        return 0
    return len(containers[0].streams[0].encoded)


def test_start_stop_writes_frames_and_closes_cleanly(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)

    assert session_id in manager.active()
    assert _wait_for(lambda: _encoded_count(mock_av) >= 3)

    result = manager.stop(session_id)

    assert result.error is None
    assert result.duration_s > 0
    assert result.mp4_path == str(output_dir / f"record_{session_id}.mp4")
    stream = mock_av[0].streams[0]
    assert stream.codec == "h264"
    assert stream.rate == settings.fps
    assert (stream.width, stream.height) == (64, 48)
    assert stream.pix_fmt == "yuv420p"
    assert stream.options == {"crf": "23", "preset": "fast"}
    assert stream.flushed
    assert mock_av[0].closed
    assert mock_mss[0].closed
    assert manager.active() == []


def test_pts_are_strictly_increasing(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    assert _wait_for(lambda: _encoded_count(mock_av) >= 5)
    manager.stop(session_id)

    pts_values = [pts for pts, _ in mock_av[0].streams[0].encoded]
    assert pts_values[0] >= 0
    assert all(later > earlier for earlier, later in pairwise(pts_values))


def test_frames_resized_to_locked_geometry_on_window_resize(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    assert _wait_for(lambda: _encoded_count(mock_av) >= 1)

    fake_win32.rects[100] = (10, 10, 42, 27)
    assert _wait_for(lambda: _encoded_count(mock_av) >= 3)
    manager.stop(session_id)

    sizes = {size for _, size in mock_av[0].streams[0].encoded}
    assert sizes == {(64, 48)}


def test_window_closed_mid_recording_keeps_partial_and_flushes(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    assert _wait_for(lambda: _encoded_count(mock_av) >= 2)

    fake_win32.valid[100] = False
    assert _wait_for(lambda: bool(mock_av) and mock_av[0].closed)

    result = manager.stop(session_id)
    assert result.error == "window closed"
    assert len(mock_av[0].streams[0].encoded) >= 2
    assert mock_av[0].streams[0].flushed
    assert Path(result.mp4_path).exists()


def test_minimized_whole_time_yields_no_frames_error(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
) -> None:
    fake_win32.iconic[100] = True
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    time.sleep(0.15)

    result = manager.stop(session_id)

    assert result.error == "no frames captured"
    assert mock_av == []
    assert not Path(result.mp4_path).exists()


def test_capture_failure_deletes_empty_file(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: FakeStream, frame: FakeFrame | None = None) -> list[object]:
        raise RuntimeError("encoder exploded")

    monkeypatch.setattr(FakeStream, "encode", boom)
    manager = RecordingManager()
    session_id = manager.start(hwnd=100)
    assert _wait_for(lambda: bool(mock_av) and mock_av[0].closed)

    result = manager.stop(session_id)
    assert result.error is not None and result.error.startswith("capture failed")
    assert not Path(result.mp4_path).exists()


def test_duplicate_hwnd_rejected_until_stopped(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
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
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
) -> None:
    manager = RecordingManager()
    first_id = manager.start(hwnd=100)
    second_id = manager.start(hwnd=200)
    assert _wait_for(lambda: len(mock_av) >= 2)
    assert _wait_for(
        lambda: all(len(c.streams) and len(c.streams[0].encoded) >= 2 for c in mock_av)
    )

    first = manager.stop(first_id)
    second = manager.stop(second_id)

    assert first.error is None and second.error is None
    assert first.mp4_path != second.mp4_path
    region_lefts = [{region["left"] for region in sct.regions} for sct in mock_mss]
    assert {0} in region_lefts
    assert {500} in region_lefts


def test_client_area_only_grabs_client_rect(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100, client_area_only=True)
    assert _wait_for(lambda: _encoded_count(mock_av) >= 1)
    manager.stop(session_id)

    assert mock_mss[0].regions[0] == {"left": 5, "top": 25, "width": 60, "height": 40}
    stream = mock_av[0].streams[0]
    assert (stream.width, stream.height) == (60, 40)


def test_custom_fps_is_used_for_stream_rate(
    output_dir: Path,
    fake_win32: _Win32State,
    mock_mss: list[FakeSct],
    mock_av: list[FakeContainer],
) -> None:
    manager = RecordingManager()
    session_id = manager.start(hwnd=100, fps=10)
    assert _wait_for(lambda: _encoded_count(mock_av) >= 1)
    manager.stop(session_id)

    assert mock_av[0].streams[0].rate == 10


def test_stop_unknown_session_returns_error() -> None:
    result = RecordingManager().stop("does-not-exist")
    assert result.error == "unknown session_id"
    assert result.mp4_path == ""
    assert result.duration_s == 0.0


def test_module_singleton_is_recording_manager() -> None:
    assert isinstance(recorder.manager, RecordingManager)

import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import av
import mss
import numpy as np
import pywintypes
import win32gui
from av.container.output import OutputContainer
from av.error import FFmpegError
from av.video.stream import VideoStream
from mss.exception import ScreenShotError
from PIL import Image

from src.config import settings
from src.windows import get_client_rect_screen, get_window_rect, is_valid

__all__ = [
    "AlreadyRecordingError",
    "RecordingManager",
    "RecordingSession",
    "StopResult",
    "manager",
]

_FRAME_YIELD_S = 0.001
_MINIMIZED_POLL_S = 0.1
_STOP_JOIN_TIMEOUT_S = 5.0
_CAPTURE_ERRORS = (ScreenShotError, FFmpegError, OSError, ValueError, RuntimeError)


class AlreadyRecordingError(Exception):
    """Raised when a second recording is started on the same hwnd."""


@dataclass
class RecordingSession:
    session_id: str
    hwnd: int
    client_area_only: bool
    fps: int
    stop_event: threading.Event
    output_path: Path
    started_at: float
    thread: threading.Thread | None = None
    error: str | None = None
    frames_written: int = 0


@dataclass
class StopResult:
    mp4_path: str = ""
    duration_s: float = 0.0
    error: str | None = None


def _open_video_stream(
    session: RecordingSession, width: int, height: int
) -> tuple[OutputContainer, VideoStream]:
    container = av.open(str(session.output_path), mode="w")
    stream = container.add_stream("h264", rate=session.fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.options = {"crf": "23", "preset": "fast"}
    return container, stream


def _capture_loop(session: RecordingSession) -> None:
    sct = mss.MSS()
    container: OutputContainer | None = None
    stream: VideoStream | None = None
    locked_size: tuple[int, int] | None = None
    last_pts = -1
    try:
        while not session.stop_event.is_set():
            if not is_valid(session.hwnd):
                session.error = "window closed"
                break
            try:
                if win32gui.IsIconic(session.hwnd):
                    session.stop_event.wait(_MINIMIZED_POLL_S)
                    continue
                if session.client_area_only:
                    region = get_client_rect_screen(session.hwnd)
                else:
                    region = get_window_rect(session.hwnd)
            except pywintypes.error:
                session.error = "window closed"
                break
            if region["width"] < 2 or region["height"] < 2:
                session.stop_event.wait(_FRAME_YIELD_S)
                continue
            pts = int((time.monotonic() - session.started_at) * session.fps)
            if pts <= last_pts:
                session.stop_event.wait(_FRAME_YIELD_S)
                continue
            shot = sct.grab(region)
            img = Image.frombytes("RGB", (shot.width, shot.height), shot.bgra, "raw", "BGRX")
            if locked_size is None or container is None or stream is None:
                locked_size = (region["width"] // 2 * 2, region["height"] // 2 * 2)
                container, stream = _open_video_stream(session, *locked_size)
            if img.size != locked_size:
                img = img.resize(locked_size, Image.Resampling.BILINEAR)
            frame = av.VideoFrame.from_ndarray(np.asarray(img), format="rgb24")
            frame.pts = pts
            for packet in stream.encode(frame):
                container.mux(packet)
            session.frames_written += 1
            last_pts = pts
            session.stop_event.wait(_FRAME_YIELD_S)
    except _CAPTURE_ERRORS as exc:
        session.error = f"capture failed: {exc}"
    finally:
        try:
            if container is not None:
                try:
                    if stream is not None:
                        for packet in stream.encode(None):
                            container.mux(packet)
                except _CAPTURE_ERRORS as exc:
                    if session.error is None:
                        session.error = f"encoder flush failed: {exc}"
                finally:
                    container.close()
        finally:
            sct.close()
            if session.frames_written == 0:
                session.output_path.unlink(missing_ok=True)
                if session.error is None:
                    session.error = "no frames captured"


class RecordingManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, RecordingSession] = {}

    def start(self, hwnd: int, client_area_only: bool = False, fps: int | None = None) -> str:
        with self._lock:
            for existing in self._sessions.values():
                if existing.hwnd == hwnd:
                    raise AlreadyRecordingError("already recording")
            session_id = uuid.uuid4().hex
            session = RecordingSession(
                session_id=session_id,
                hwnd=hwnd,
                client_area_only=client_area_only,
                fps=fps if fps is not None else settings.fps,
                stop_event=threading.Event(),
                output_path=settings.ensure_output_dir() / f"record_{session_id}.mp4",
                started_at=time.monotonic(),
            )
            thread = threading.Thread(
                target=_capture_loop,
                args=(session,),
                name=f"recorder-{session_id[:8]}",
                daemon=True,
            )
            session.thread = thread
            self._sessions[session_id] = session
            thread.start()
            return session_id

    def stop(self, session_id: str) -> StopResult:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return StopResult(error="unknown session_id")
        session.stop_event.set()
        if session.thread is not None:
            session.thread.join(timeout=_STOP_JOIN_TIMEOUT_S)
        return StopResult(
            mp4_path=str(session.output_path),
            duration_s=time.monotonic() - session.started_at,
            error=session.error,
        )

    def active(self) -> list[str]:
        with self._lock:
            return list(self._sessions)


manager = RecordingManager()

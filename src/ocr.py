"""Grab a window region and extract its text via pytesseract (sync).

Preprocessing pipeline (small composable functions, individually testable):
``upscale`` (2x Lanczos) -> ``to_grayscale`` -> ``invert_if_dark`` ->
``otsu_threshold``. The invert-if-dark step handles dark themes (cmd,
VS Code dark): when the mean luminance is below 128 the image is inverted
so Tesseract always sees dark text on a light background. Otsu is
numpy-based; no OpenCV dependency.
"""

import mss
import numpy as np
import pytesseract
import win32gui
from PIL import Image, ImageOps

from src.config import settings
from src.windows import get_client_rect_screen, get_window_rect

__all__ = [
    "DARK_MEAN_LUMINANCE",
    "UPSCALE_FACTOR",
    "WindowMinimizedError",
    "capture_window_text",
    "invert_if_dark",
    "otsu_threshold",
    "to_grayscale",
    "upscale",
]

UPSCALE_FACTOR = 2
DARK_MEAN_LUMINANCE = 128

pytesseract.pytesseract.tesseract_cmd = str(settings.tesseract_cmd)


class WindowMinimizedError(Exception):
    """Raised when the target window is minimized (no grab possible)."""


def upscale(img: Image.Image, factor: int = UPSCALE_FACTOR) -> Image.Image:
    if factor == 1:
        return img
    return img.resize((img.width * factor, img.height * factor), Image.Resampling.LANCZOS)


def to_grayscale(img: Image.Image) -> Image.Image:
    return img.convert("L")


def invert_if_dark(img: Image.Image) -> Image.Image:
    if float(np.asarray(img, dtype=np.float64).mean()) < DARK_MEAN_LUMINANCE:
        return ImageOps.invert(img)
    return img


def otsu_threshold(img: Image.Image) -> Image.Image:
    gray = to_grayscale(img)
    arr = np.asarray(gray)
    hist = np.bincount(arr.ravel(), minlength=256).astype(np.float64)
    prob = hist / arr.size
    omega = np.cumsum(prob)
    levels = np.arange(256, dtype=np.float64)
    mu = np.cumsum(prob * levels)
    mu_total = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu_total * omega - mu) ** 2 / (omega * (1.0 - omega))
    threshold = int(np.argmax(np.nan_to_num(between)))
    binary = np.where(arr > threshold, 255, 0).astype(np.uint8)
    return Image.fromarray(binary)


def _preprocess(img: Image.Image) -> Image.Image:
    img = upscale(img)
    img = to_grayscale(img)
    img = invert_if_dark(img)
    return otsu_threshold(img)


def capture_window_text(hwnd: int, client_area_only: bool = True) -> str:
    if win32gui.IsIconic(hwnd):
        raise WindowMinimizedError("window minimized")
    if client_area_only:
        region = get_client_rect_screen(hwnd)
    else:
        region = get_window_rect(hwnd)
    sct = mss.MSS()
    try:
        shot = sct.grab(region)
        img = Image.frombytes("RGB", (shot.width, shot.height), shot.bgra, "raw", "BGRX")
    finally:
        sct.close()
    return str(pytesseract.image_to_string(_preprocess(img)))

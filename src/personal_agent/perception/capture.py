"""Screen capture (mss, physical pixels) with privacy masking. Images live in memory only; nothing here writes to disk."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

import mss
from PIL import Image, ImageDraw

from personal_agent.perception import windows
from personal_agent.settings.settings import ScreenCfg

Rect = tuple[int, int, int, int]  # left, top, right, bottom


class CaptureError(RuntimeError):
    """Nothing could be captured (no such window, empty region...)."""


class Blocked(CaptureError):
    """The target is on the privacy blocklist: refused, not captured."""


@dataclass
class Shot:
    image: Image.Image
    kind: str
    title: str
    process: str
    rect: Rect  # captured area in physical screen pixels: add rect[:2] to image coordinates to get screen coordinates
    ts: float
    masked: int = 0  # blocked windows that were blacked out of this image


def _grab(rect: Rect) -> Image.Image:
    left, top, right, bottom = rect
    if right - left < 2 or bottom - top < 2:
        raise CaptureError("the area to capture is empty")
    with mss.mss() as sct:
        raw = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def virtual_screen() -> Rect:
    with mss.mss() as sct:
        m = sct.monitors[0]
    return m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]


def monitor_rects() -> list[Rect]:
    with mss.mss() as sct:
        return [(m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]) for m in sct.monitors[1:]]


def _clip(r: Rect, bounds: Rect) -> Rect:
    return max(r[0], bounds[0]), max(r[1], bounds[1]), min(r[2], bounds[2]), min(r[3], bounds[3])


def mask_blocked(img: Image.Image, area: Rect, blocked: list[windows.WindowInfo]) -> int:
    """Black out every blocked window that overlaps the captured area. Returns how many were masked."""
    n, draw = 0, ImageDraw.Draw(img)
    for w in blocked:
        c = _clip(w.rect, area)
        if c[2] > c[0] and c[3] > c[1]:
            box = (c[0] - area[0], c[1] - area[1], c[2] - area[0] - 1, c[3] - area[1] - 1)
            draw.rectangle(box, fill=(24, 24, 28))
            draw.text((box[0] + 8, box[1] + 8), "private window", fill=(120, 120, 130))
            n += 1
    return n


def capture(kind: str, cfg: ScreenCfg, *, window: str | None = None, rect: Rect | None = None) -> Shot:
    """kind: screen | active_window | window (by title substring) | region (physical-pixel rect).

    Raises Blocked when the requested window itself is private; blocked windows that merely overlap the area are blacked out."""
    wins = windows.visible_windows()
    blocked = windows.blocked_windows(cfg, wins)
    title = process = ""
    if kind == "screen":
        area = monitor_rects()[0] if monitor_rects() else virtual_screen()
        fg = windows.foreground_window()
        title, process = (fg.title, fg.process) if fg else ("", "")
    elif kind in ("active_window", "window"):
        w = windows.foreground_window() if kind == "active_window" else (windows.find_window(window or "", wins) if window else None)
        if w is None:
            raise CaptureError("no such window" if kind == "window" else "no active window")
        if reason := windows.view_block_reason(w, cfg):
            raise Blocked(f"refused: {reason}")
        area, title, process = _clip(w.rect, virtual_screen()), w.title, w.process
    elif kind == "region":
        if rect is None:
            raise CaptureError("a region needs a rectangle")
        area = _clip(rect, virtual_screen())
    else:
        raise CaptureError(f"unknown capture kind '{kind}'")
    img = _grab(area)
    return Shot(img, kind, title, process, area, time.time(), mask_blocked(img, area, blocked))


def downscale(img: Image.Image, max_px: int) -> Image.Image:
    w, h = img.size
    s = max_px / max(w, h)
    return img if s >= 1 else img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.Resampling.LANCZOS)


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def to_jpeg(img: Image.Image, max_px: int = 1280, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    downscale(img, max_px).convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()

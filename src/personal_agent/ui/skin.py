"""Avatar skins. A skin is a folder with `manifest.json` mapping agent states to animations (see docs/development.md, Avatar skins).

    {"name": "mine", "size": [160, 160], "fallback": "idle",
     "states": {"idle": {"frames": ["idle_0.png", "idle_1.png"], "fps": 4},
                "thinking": {"gif": "think.gif"},
                "success": {"frames": ["yay.png"], "fps": 1, "loop": false},
                "error": null}}

A state with no animation falls back to `fallback`, then `idle`, then the built-in drawing, so a half-finished skin
still works. The built-in skin draws its frames with QPainter (no image files shipped).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QMovie, QPainter, QPen, QPixmap, QRadialGradient

from personal_agent.ui.avatar_state import STATES
from personal_agent.ui.theme import AVATAR_INK, STATE

BASE = 128
BUILTIN_FRAMES = 6
MAX_SIZE = 512
COLORS = STATE


@dataclass
class Animation:
    frames: list[QPixmap]
    fps: float = 4.0
    loop: bool = True


def draw_builtin(state: str, frame: int, size: int = BASE) -> QPixmap:
    """The default skin: a round blob whose face, colour and accessory depend on the state."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / BASE
    p.scale(s, s)
    n = BUILTIN_FRAMES
    bob = math.sin(frame / n * 2 * math.pi) * (1.5 if state != "sleeping" else 3.0)
    col = QColor(COLORS.get(state, COLORS["idle"]))
    body = QRectF(24, 34 + bob, 80, 74)
    g = QRadialGradient(QPointF(54, 52 + bob), 70)
    g.setColorAt(0, col.lighter(130))
    g.setColorAt(1, col.darker(115))
    p.setBrush(QBrush(g))
    p.setPen(QPen(col.darker(160), 2.5))
    p.drawEllipse(body)
    dark = QPen(QColor(AVATAR_INK), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(dark)
    p.setBrush(QColor(AVATAR_INK))
    eye_y = 64 + bob
    blink = frame == n - 1 and state in ("idle", "listening", "speaking")
    for ex in (46, 82):
        if state == "sleeping" or blink:
            p.drawLine(int(ex - 6), int(eye_y), int(ex + 6), int(eye_y))
        elif state == "error":
            p.drawLine(int(ex - 5), int(eye_y - 5), int(ex + 5), int(eye_y + 5))
            p.drawLine(int(ex - 5), int(eye_y + 5), int(ex + 5), int(eye_y - 5))
        elif state == "success":
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(ex - 6, eye_y - 4, 12, 10), 0, 180 * 16)
            p.setBrush(QColor(AVATAR_INK))
        else:
            r = 6.5 if state in ("listening", "awaiting_approval") else 5.0
            look = math.sin(frame / n * 2 * math.pi) * 2 if state in ("thinking", "working") else 0
            p.drawEllipse(QPointF(ex + look, eye_y), r, r)
    p.setBrush(Qt.BrushStyle.NoBrush)
    my = 86 + bob
    if state == "speaking":
        h = 3 + (frame % 3) * 3
        p.drawEllipse(QPointF(64, my + 2), 7, h)
    elif state in ("success", "idle", "listening"):
        p.drawArc(QRectF(52, my - 6, 24, 14), 200 * 16, 140 * 16)
    elif state in ("error", "awaiting_approval"):
        p.drawArc(QRectF(54, my, 20, 10), 20 * 16, 140 * 16)
    elif state == "sleeping":
        p.drawLine(58, int(my + 2), 70, int(my + 2))
    else:
        p.drawLine(56, int(my + 1), 72, int(my + 1))
    p.setPen(QPen(QColor(col.darker(130)), 3))
    if state == "thinking":
        for i in range(3):
            on = (frame // 2) % 3 == i
            p.setBrush(QColor(col.darker(130)) if on else Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(50 + i * 14, 20), 4, 4)
    elif state == "working":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(50, 4, 28, 28), int(frame / n * 360 * 16), 270 * 16)
    elif state == "awaiting_approval":
        p.setFont(QFont("Bahnschrift", 20, QFont.Weight.Bold))
        p.drawText(QRectF(54, 0 + (frame % 2) * 2, 22, 30), Qt.AlignmentFlag.AlignCenter, "!")
    elif state == "listening":
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(2):
            p.drawArc(QRectF(100 + k * 6, 56 - k * 4, 12 + k * 4, 24 + k * 8), -60 * 16, 120 * 16)
    elif state == "sleeping":
        p.setFont(QFont("Bahnschrift", 12 + (frame % 3) * 2, QFont.Weight.Bold))
        p.drawText(QRectF(86, 6, 30, 26), Qt.AlignmentFlag.AlignCenter, "z")
    elif state == "success":
        p.setBrush(QColor(col.darker(130)))
        for dx in (-34, 34):
            p.drawEllipse(QPointF(64 + dx, 28 + (frame % 2) * 3), 3, 3)
    p.end()
    return pm


def builtin_animation(state: str) -> Animation:
    fps = {"sleeping": 2, "idle": 3, "working": 8, "thinking": 5, "speaking": 8}.get(state, 4)
    return Animation([draw_builtin(state, i) for i in range(BUILTIN_FRAMES)], fps)


class Skin:
    def __init__(self, name: str, size: int, fallback: str, anims: dict[str, Animation | None], builtin: bool = False) -> None:
        self.name, self.size, self.fallback, self._anims, self.builtin = name, size, fallback, anims, builtin
        self.problems: list[str] = []

    def animation(self, state: str) -> Animation:
        for s in (state, self.fallback, "idle"):
            a = self._anims.get(s)
            if a and a.frames:
                return a
        return builtin_animation(state if state in STATES else "idle")

    @classmethod
    def default(cls) -> Skin:
        return cls("default", BASE, "idle", {s: builtin_animation(s) for s in STATES}, builtin=True)

    @classmethod
    def load(cls, folder: Path) -> Skin:
        """Load a skin folder. Problems never raise: they are collected in `.problems` and that state falls back."""
        mf = folder / "manifest.json"
        if not mf.is_file():
            raise FileNotFoundError(f"no manifest.json in {folder}")
        data: dict[str, Any] = json.loads(mf.read_text(encoding="utf-8"))
        size = int(min(max((data.get("size") or [BASE])[0], 32), MAX_SIZE))
        skin = cls(str(data.get("name", folder.name)), size, str(data.get("fallback", "idle")), {})
        for state, spec in (data.get("states") or {}).items():
            if state not in STATES and state != "custom":
                skin.problems.append(f"unknown state '{state}'")
                continue
            if not spec:
                continue
            try:
                skin._anims[state] = _load_animation(folder, spec, size)
            except (OSError, ValueError, KeyError) as e:
                skin.problems.append(f"{state}: {e}")
        if not skin._anims:
            skin.problems.append("no usable animation; using the built-in look")
        return skin


def _load_animation(folder: Path, spec: dict[str, Any], size: int) -> Animation:
    fps = float(spec.get("fps", 6))
    if not 0.2 <= fps <= 30:
        raise ValueError("fps must be between 0.2 and 30")
    if "builtin" in spec:
        return builtin_animation(str(spec["builtin"]))
    frames: list[QPixmap] = []
    if "gif" in spec:
        movie = QMovie(_safe(folder, spec["gif"]))
        if not movie.isValid():
            raise ValueError(f"cannot read {spec['gif']}")
        for i in range(min(movie.frameCount(), 240)):
            movie.jumpToFrame(i)
            frames.append(movie.currentPixmap())
        fps = float(spec.get("fps", 1000 / max(movie.nextFrameDelay(), 40)))
    else:
        for f in spec["frames"][:240]:
            pm = QPixmap(_safe(folder, f))
            if pm.isNull():
                raise ValueError(f"cannot read {f}")
            frames.append(pm)
    if not frames:
        raise ValueError("no frames")
    return Animation([f.scaledToHeight(size, Qt.TransformationMode.SmoothTransformation) if f.height() != size else f for f in frames],
                     fps, bool(spec.get("loop", True)))


def _safe(folder: Path, rel: str) -> str:
    """Skin files must live inside the skin folder (a manifest must not be able to read arbitrary paths)."""
    p = (folder / rel).resolve()
    if folder.resolve() not in p.parents:
        raise ValueError(f"'{rel}' is outside the skin folder")
    return str(p)


def skins_root() -> Path:
    from personal_agent.settings.settings import config_dir

    return config_dir() / "avatar"


def available_skins() -> list[str]:
    from personal_agent.settings.settings import data_dir

    found = {p.name for root in (data_dir() / "skins", skins_root()) if root.is_dir() for p in root.iterdir() if (p / "manifest.json").is_file()}
    return ["default"] + sorted(found - {"default"})


def load_skin(name: str) -> Skin:
    """Named skin from config/avatar/<name>/ (or %APPDATA%\\personal-agent\\skins\\<name>); 'default' is built in."""
    from personal_agent.settings.settings import data_dir

    if name != "default":
        for root in (data_dir() / "skins", skins_root()):
            if (root / name / "manifest.json").is_file():
                try:
                    return Skin.load(root / name)
                except (OSError, ValueError, KeyError) as e:
                    s = Skin.default()
                    s.problems.append(f"skin '{name}' failed to load: {e}")
                    return s
    return Skin.default()

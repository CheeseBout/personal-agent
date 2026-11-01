"""Mouse / keyboard control: one approved call = a short list of steps, each checked, highlighted and abortable.

Safety properties (all enforced in `run`, tested with a fake backend):
- at most `screen.max_actions` steps per call; unknown/malformed steps are rejected before anything moves
- the target window of EVERY step (also a click by coordinates) is checked: privacy blocklist, shells/system tools, the agent's own UI
- typing into password fields is refused; Win-key and Ctrl+Alt+Del combos are refused
- the target is outlined on screen for `highlight_ms` before each action, and the run needs a connected UI to show it
- the run stops at once when the kill switch engages or the user moves the mouse (`abort_move_px`)
UIA (Invoke pattern, element rectangles) is preferred; coordinates are the fallback.
"""

from __future__ import annotations

import ctypes
import math
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from personal_agent.perception import hooks, uia, windows
from personal_agent.settings.settings import ScreenCfg

Rect = tuple[int, int, int, int]
MAX_TEXT = 2000
MAX_WAIT_S = 5.0


class Refused(RuntimeError):
    """The step is not allowed (policy / privacy / safety). Nothing was done for this step."""


class Aborted(RuntimeError):
    """The run was stopped by the kill switch or by the user moving the mouse."""


class Action(BaseModel):
    op: Literal["click", "double_click", "right_click", "type", "key", "scroll", "wait", "focus"]
    window: str | None = Field(None, description="part of the target window's title (from screen_windows)")
    name: str | None = Field(None, description="element name (substring) as listed by ui_tree")
    control_type: str | None = Field(None, description="e.g. Button, Edit, MenuItem, CheckBox")
    automation_id: str | None = None
    index: int = Field(0, ge=0, description="which match to use when several elements fit")
    x: int | None = Field(None, description="absolute screen pixel (fallback when no element fits)")
    y: int | None = None
    text: str | None = Field(None, description="text to type (type)")
    keys: str | None = Field(None, description="key combo like 'ctrl+s', 'enter', 'tab' (key)")
    amount: int | None = Field(None, description="scroll wheel notches, negative = down (scroll)")
    seconds: float | None = Field(None, description="pause length (wait), at most 5")

    @model_validator(mode="after")
    def _shape(self) -> Action:
        has_el = any((self.name, self.control_type, self.automation_id))
        has_xy = self.x is not None and self.y is not None
        if self.op in ("click", "double_click", "right_click"):
            if not (has_xy or (self.window and has_el)):
                raise ValueError(f"{self.op} needs window + (name | control_type | automation_id), or x and y")
        elif self.op == "type":
            if not self.text or len(self.text) > MAX_TEXT:
                raise ValueError(f"type needs text of 1-{MAX_TEXT} characters")
            if not self.window:
                raise ValueError("type needs `window` (which window receives the text)")
        elif self.op == "key":
            if not self.keys or not self.window:
                raise ValueError("key needs keys and `window`")
            parse_keys(self.keys)
        elif self.op == "scroll":
            if self.amount is None or not -30 <= self.amount <= 30 or not (has_xy or self.window):
                raise ValueError("scroll needs amount (-30..30) and window or x/y")
        elif self.op == "wait":
            if self.seconds is None or not 0 < self.seconds <= MAX_WAIT_S:
                raise ValueError(f"wait needs seconds in (0, {MAX_WAIT_S}]")
        elif self.op == "focus" and not self.window:
            raise ValueError("focus needs window")
        return self


# ---- keys -----------------------------------------------------------------------------------------------------------------
_VK = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
       "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27}
_MODS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10}


def parse_keys(spec: str) -> list[int]:
    """'ctrl+shift+s' -> virtual-key codes (modifiers first). Refuses Win-key combos and Ctrl+Alt+Del."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts or len(parts) > 4:
        raise ValueError("key combo must have 1-4 keys")
    if any(p in ("win", "windows", "meta", "super", "cmd") for p in parts):
        raise ValueError("combos with the Windows key are not allowed (they open Run / system UI)")
    *mods, key = parts
    if any(m not in _MODS for m in mods):
        raise ValueError(f"unknown modifier in '{spec}'")
    if key in _VK:
        vk = _VK[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"unknown key '{key}'")
    if {"ctrl", "control"} & set(mods) and "alt" in mods and key in ("delete", "del"):
        raise ValueError("Ctrl+Alt+Del is not allowed")
    return [_MODS[m] for m in mods] + [vk]


# ---- backend ----------------------------------------------------------------------------------------------------------------
class Backend(Protocol):
    def list_windows(self) -> list[windows.WindowInfo]: ...
    def foreground(self) -> windows.WindowInfo | None: ...
    def window_at(self, x: int, y: int) -> windows.WindowInfo | None: ...
    def find_element(self, hwnd: int, a: Action) -> tuple[uia.Element, int]: ...
    def invoke(self, el: uia.Element) -> bool: ...
    def focus(self, hwnd: int) -> None: ...
    def click(self, x: int, y: int, button: str, double: bool) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press(self, vks: list[int]) -> None: ...
    def scroll(self, x: int, y: int, amount: int) -> None: ...
    def cursor(self) -> tuple[int, int]: ...


class Win32Backend:
    """SendInput + UI Automation. (Windows blocks input into elevated windows from a normal process: such steps just do nothing.)"""

    def list_windows(self) -> list[windows.WindowInfo]:
        return windows.visible_windows()

    def foreground(self) -> windows.WindowInfo | None:
        return windows.foreground_window()

    def window_at(self, x: int, y: int) -> windows.WindowInfo | None:
        u = ctypes.windll.user32
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.restype = wintypes.HWND
        h = u.WindowFromPoint(wintypes.POINT(x, y))
        return windows.info_for(int(u.GetAncestor(h, 2) or 0)) if h else None

    def find_element(self, hwnd: int, a: Action) -> tuple[uia.Element, int]:
        return uia.find(hwnd, a.name, a.control_type, a.automation_id, a.index)

    def invoke(self, el: uia.Element) -> bool:
        return uia.invoke(el)

    def focus(self, hwnd: int) -> None:
        """Bring a window to the front. Windows refuses plain SetForegroundWindow from a background process, so borrow the current
        foreground thread's input state for the call (no fake keystrokes)."""
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        fg = u.GetForegroundWindow()
        fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
        me = k.GetCurrentThreadId()
        attached = bool(fg_tid) and fg_tid != me and bool(u.AttachThreadInput(me, fg_tid, True))
        try:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u.AttachThreadInput(me, fg_tid, False)

    def cursor(self) -> tuple[int, int]:
        p = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def click(self, x: int, y: int, button: str, double: bool) -> None:
        ctypes.windll.user32.SetCursorPos(x, y)
        down, up = (0x0008, 0x0010) if button == "right" else (0x0002, 0x0004)
        for _ in range(2 if double else 1):
            _send([_mouse(down), _mouse(up)])
            time.sleep(0.05)

    def scroll(self, x: int, y: int, amount: int) -> None:
        ctypes.windll.user32.SetCursorPos(x, y)
        _send([_mouse(0x0800, amount * 120)])

    def type_text(self, text: str) -> None:
        events: list[Any] = []
        for ch in text:
            if ch == "\n":
                events += [_key(0x0D, 0), _key(0x0D, 2)]
                continue
            for unit in _utf16(ch):
                events += [_key(0, 0x0004, unit), _key(0, 0x0004 | 0x0002, unit)]  # KEYEVENTF_UNICODE (+KEYUP)
        for i in range(0, len(events), 40):  # small batches: some apps drop long bursts
            _send(events[i:i + 40])
            time.sleep(0.01)

    def press(self, vks: list[int]) -> None:
        _send([_key(v, 0) for v in vks] + [_key(v, 2) for v in reversed(vks)])


def _utf16(ch: str) -> list[int]:
    b = ch.encode("utf-16-le")
    return [int.from_bytes(b[i:i + 2], "little") for i in range(0, len(b), 2)]


if sys.platform == "win32":
    ULONG_PTR = ctypes.c_size_t

    class _KI(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class _MI(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("ki", _KI), ("mi", _MI)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _key(vk: int, flags: int, scan: int = 0) -> Any:
    return _INPUT(1, _U(ki=_KI(vk, scan, flags, 0, 0)))


def _mouse(flags: int, data: int = 0) -> Any:
    return _INPUT(0, _U(mi=_MI(0, 0, data & 0xFFFFFFFF, flags, 0, 0)))


def _send(events: list[Any]) -> None:
    arr = (_INPUT * len(events))(*events)
    ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))


# ---- the engine ----------------------------------------------------------------------------------------------------------------
def _dist(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Runner:
    def __init__(self, cfg: ScreenCfg, backend: Backend | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg, self.be, self._sleep = cfg, backend or Win32Backend(), sleep
        self.expected: tuple[int, int] | None = None

    # -- guards --
    def _guard(self) -> None:
        if hooks.killed():
            raise Aborted("the kill switch is engaged")
        cur = self.be.cursor()
        if self.expected is not None and _dist(cur, self.expected) > self.cfg.abort_move_px:
            raise Aborted("you moved the mouse, so the sequence was stopped")

    def _pause(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._guard()
            self._sleep(min(0.05, max(0.0, end - time.monotonic())))

    def _check_window(self, w: windows.WindowInfo | None, what: str) -> windows.WindowInfo:
        if w is None:
            raise Refused(f"{what}: no window there")
        if reason := windows.control_deny_reason(w, self.cfg, hooks.agent_name()):
            raise Refused(f"{what}: refused - {reason}")
        return w

    def _reachable(self, point: tuple[int, int], win: windows.WindowInfo) -> None:
        """A real mouse click lands on whatever is on top at that point: it must be the window we checked, not something covering it."""
        top = self.be.window_at(point[0], point[1])
        if top is None or top.hwnd != win.hwnd:
            raise Refused(f"'{win.title[:40]}' is covered by '{top.title[:40] if top else 'another window'}' at that point; bring it to the front first")

    def _to_front(self, win: windows.WindowInfo) -> None:
        """Keystrokes go to whatever is in front: make sure it is the window we checked, or send nothing."""
        for _ in range(8):
            self.be.focus(win.hwnd)
            self._pause(0.1)
            fg = self.be.foreground()
            if fg is not None and fg.hwnd == win.hwnd:
                if reason := windows.control_deny_reason(fg, self.cfg, hooks.agent_name()):  # title may have changed
                    raise Refused(f"window '{win.title[:40]}': refused - {reason}")
                return
        raise Refused(f"could not bring '{win.title[:40]}' to the front, so nothing was typed (Windows may be blocking focus changes)")

    def _window_by_title(self, title: str) -> windows.WindowInfo:
        w = windows.find_window(title, self.be.list_windows())
        if w is None:
            raise Refused(f"no visible window with '{title}' in its title (see screen_windows)")
        return w

    # -- one step --
    def step(self, a: Action) -> str:
        self._guard()
        if a.op == "wait":
            self._pause(float(a.seconds or 0))
            return f"waited {a.seconds}s"
        target: Rect
        label: str
        el: uia.Element | None = None
        win: windows.WindowInfo
        if a.x is not None and a.y is not None and not a.window:
            win = self._check_window(self.be.window_at(a.x, a.y), f"point ({a.x},{a.y})")
            target, label, point = (a.x - 12, a.y - 12, a.x + 12, a.y + 12), f"({a.x},{a.y}) in '{win.title[:40]}'", (a.x, a.y)
        else:
            win = self._check_window(self._window_by_title(a.window or ""), f"window '{a.window}'")
            if any((a.name, a.control_type, a.automation_id)):
                el, n = self.be.find_element(win.hwnd, a)
                target, label = el.rect, f"{el.control_type} '{el.name[:40]}' in '{win.title[:40]}'" + (f" (match {a.index + 1} of {n})" if n > 1 else "")
                point = el.center
            elif a.x is not None and a.y is not None:
                self._check_window(self.be.window_at(a.x, a.y), f"point ({a.x},{a.y})")
                target, label, point = (a.x - 12, a.y - 12, a.x + 12, a.y + 12), f"({a.x},{a.y})", (a.x, a.y)
            else:
                target, label, point = win.rect, f"window '{win.title[:40]}'", ((win.rect[0] + win.rect[2]) // 2, (win.rect[1] + win.rect[3]) // 2)
        if a.op == "type" and el is not None and el.password:
            raise Refused("typing into a password field is not allowed")
        hooks.emit({"type": "highlight", "rect": list(target), "label": f"{a.op}: {label}", "ms": self.cfg.highlight_ms})
        self._pause(self.cfg.highlight_ms / 1000)
        self._guard()
        if a.op == "focus":
            self.be.focus(win.hwnd)
            result = f"focused {label}"
        elif a.op in ("click", "double_click", "right_click"):
            if a.op == "click" and el is not None and self.be.invoke(el):
                result = f"invoked {label}"
            else:
                if a.x is None or a.window:  # a bare (x, y) click was already checked through window_at above
                    self._reachable(point, win)
                self.be.click(point[0], point[1], "right" if a.op == "right_click" else "left", a.op == "double_click")
                self.expected = point
                result = f"{a.op.replace('_', ' ')}ed {label}" if a.op != "click" else f"clicked {label}"
        elif a.op == "type":
            self._to_front(win)
            if el is not None:  # focus the field itself
                self._reachable(point, win)
                self.be.click(point[0], point[1], "left", False)
                self.expected = point
            fg = self.be.foreground()
            if fg is None or fg.hwnd != win.hwnd:
                raise Refused(f"the front window changed to '{fg.title[:40] if fg else '?'}' before typing; nothing was typed")
            self.be.type_text(a.text or "")
            result = f"typed {len(a.text or '')} characters into {label}"
        elif a.op == "key":
            self._to_front(win)
            if el is not None:  # shortcuts act on the focused control: focus the named element first (e.g. Ctrl+A in a text field)
                self._reachable(point, win)
                self.be.click(point[0], point[1], "left", False)
                self.expected = point
            self.be.press(parse_keys(a.keys or ""))
            result = f"pressed {a.keys} in '{win.title[:40]}'"
        else:  # scroll
            self._reachable(point, win)
            self.be.scroll(point[0], point[1], int(a.amount or 0))
            self.expected = point
            result = f"scrolled {a.amount} at {label}"
        return result

    def run(self, actions: list[Action]) -> list[str]:
        if not 1 <= len(actions) <= self.cfg.max_actions:
            raise Refused(f"one approval covers 1-{self.cfg.max_actions} steps; got {len(actions)}")
        if not self.cfg.control:
            raise Refused("agent control is switched off in Settings -> Screen")
        if hooks.observers() < 1:
            raise Refused("no UI is connected to show the highlight and let you abort; open agent-ui first")
        self.expected = self.be.cursor()
        log: list[str] = []
        for i, a in enumerate(actions, 1):
            try:
                log.append(f"{i}. {self.step(a)}")
            except (Refused, Aborted, uia.UiaError) as e:
                log.append(f"{i}. STOPPED: {e}")
                if isinstance(e, Aborted):
                    hooks.emit({"type": "control_aborted", "reason": str(e)})
                break
        return log

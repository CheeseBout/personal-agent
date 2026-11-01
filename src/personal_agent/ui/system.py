"""Windows integration: autostart (HKCU Run key), global hotkeys (RegisterHotKey) and process stats for Settings -> About."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "personal-agent-ui"
MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
_NAMED = {"space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "home": 0x24, "end": 0x23, "pause": 0x13}


# ---- autostart --------------------------------------------------------------------------------------------------------
def autostart_command() -> str:
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{w if os.path.exists(w) else exe}" -m personal_agent.ui'


def set_autostart(enabled: bool, key_path: str = RUN_KEY, name: str = APP_NAME) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
        if enabled:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass


def autostart_enabled(key_path: str = RUN_KEY, name: str = APP_NAME) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            winreg.QueryValueEx(k, name)
        return True
    except (OSError, ImportError):
        return False


# ---- hotkeys ---------------------------------------------------------------------------------------------------------
def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+k' -> (modifiers, virtual-key). At least one modifier is required (a bare key would steal typing)."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"hotkey '{spec}' needs a modifier and a key")
    *mods, key = parts
    if not mods or any(m not in MOD for m in mods):
        raise ValueError(f"unknown modifier in '{spec}'")
    if key in _NAMED:
        vk = _NAMED[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"unknown key in '{spec}'")
    m = 0
    for x in mods:
        m |= MOD[x]
    return m | NOREPEAT, vk


class Hotkeys:
    """Registers global hotkeys and dispatches WM_HOTKEY from the Qt event loop. No-op off Windows.

    Install with `app.installNativeEventFilter(hk.qt_filter)`. A key already taken by another program is reported in
    `failed` instead of raising (the app keeps working, the user can pick another key in Settings)."""

    def __init__(self) -> None:
        self.failed: dict[str, str] = {}
        self._ids: dict[int, Callable[[], None]] = {}
        self._names: dict[str, int] = {}
        self.qt_filter: Any = None
        if sys.platform == "win32":
            from PySide6.QtCore import QAbstractNativeEventFilter

            outer = self

            class _Filter(QAbstractNativeEventFilter):
                def nativeEventFilter(self, event_type: Any, message: Any) -> tuple[bool, int]:
                    return outer._handle(event_type, message), 0

            self.qt_filter = _Filter()

    def _handle(self, event_type: Any, message: Any) -> bool:
        if bytes(event_type) != b"windows_generic_MSG":
            return False
        from ctypes import wintypes

        msg = wintypes.MSG.from_address(int(message))
        cb = self._ids.get(int(msg.wParam)) if msg.message == WM_HOTKEY else None
        if cb is None:
            return False
        cb()
        return True

    def register(self, name: str, spec: str, callback: Callable[[], None]) -> bool:
        if sys.platform != "win32":
            return False
        import ctypes

        try:
            mods, vk = parse_hotkey(spec)
        except ValueError as e:
            self.failed[name] = str(e)
            return False
        hid = len(self._names) + 1
        if not ctypes.windll.user32.RegisterHotKey(None, hid, mods, vk):
            self.failed[name] = f"{spec} is already used by another program"
            return False
        self._ids[hid], self._names[name] = callback, hid
        return True

    def clear(self) -> None:
        if sys.platform == "win32":
            import ctypes

            for hid in self._ids:
                ctypes.windll.user32.UnregisterHotKey(None, hid)
        self._ids.clear()
        self._names.clear()
        self.failed.clear()


# ---- process stats ----------------------------------------------------------------------------------------------------
def process_stats(pid: int | None) -> dict[str, float] | None:
    """CPU % (since the last call for this pid) and resident memory in MB; None if the process is gone."""
    if pid is None:
        return None
    try:
        import psutil

        p = _PROCS.get(pid) or _PROCS.setdefault(pid, psutil.Process(pid))
        return {"cpu": p.cpu_percent(None), "rss_mb": round(p.memory_info().rss / 1_048_576, 1)}
    except Exception:  # noqa: BLE001 - psutil missing, process exited or access denied: show "n/a"
        _PROCS.pop(pid, None)
        return None


_PROCS: dict[int, Any] = {}

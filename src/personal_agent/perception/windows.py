"""Windows enumeration and the privacy / safety rules that decide which windows may be seen or controlled.

Two different rules (config `screen:` in default.yaml):
- VIEW block (`block_titles`, `block_processes`): password managers, banking, sign-in pages... never captured, read or controlled.
  Full-screen captures black these windows out instead of failing.
- CONTROL deny (extra, not configurable away): terminals / shells / registry editor / task manager / system settings (they would bypass
  the shell policy or the T3 rules) and **the agent's own UI** (otherwise the agent could click "Approve" on its own approval card).
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

from personal_agent.settings.settings import ScreenCfg

if sys.platform == "win32":
    try:  # physical-pixel coordinates everywhere (mss captures physical pixels); harmless if Qt already chose
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        pass

CONTROL_DENY_PROCESSES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "powershell_ise.exe", "windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe",
    "wsl.exe", "bash.exe", "mintty.exe", "regedit.exe", "taskmgr.exe", "mmc.exe", "systemsettings.exe", "control.exe", "msiexec.exe",
    "gpedit.msc", "secpol.msc", "lusrmgr.msc", "diskmgmt.msc", "eventvwr.exe", "services.msc", "winver.exe", "sihost.exe",
}
CONTROL_DENY_TITLES = ("approval center", "trung tâm phê duyệt", "user account control", "windows security")

# pids of the agent's own processes (daemon + every connected UI): registered at runtime, never controllable
_own_pids: set[int] = {os.getpid()}


def register_own_pid(pid: int) -> None:
    _own_pids.add(int(pid))


def own_pids() -> frozenset[int]:
    return frozenset(_own_pids)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str  # lower-case exe name, e.g. "chrome.exe"
    pid: int
    rect: tuple[int, int, int, int]  # left, top, right, bottom in physical pixels

    @property
    def size(self) -> tuple[int, int]:
        return self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]


def _process_name(pid: int) -> str:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        return os.path.basename(buf.value).lower() if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        k32.CloseHandle(h)


def info_for(hwnd: int) -> WindowInfo | None:
    if not hwnd or sys.platform != "win32":
        return None
    u = ctypes.windll.user32
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    r = wintypes.RECT()
    if not u.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return WindowInfo(int(hwnd), buf.value, _process_name(pid.value), int(pid.value), (r.left, r.top, r.right, r.bottom))


def foreground_window() -> WindowInfo | None:
    return info_for(ctypes.windll.user32.GetForegroundWindow()) if sys.platform == "win32" else None


def visible_windows() -> list[WindowInfo]:
    """Top-level, visible, non-minimised, titled windows (front-to-back order as Windows enumerates them)."""
    if sys.platform != "win32":
        return []
    u = ctypes.windll.user32
    out: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[untyped-decorator]
    def cb(hwnd: int, _: int) -> bool:
        if u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd):
            w = info_for(hwnd)
            if w and w.title and w.size[0] > 1 and w.size[1] > 1 and w.title not in ("Program Manager", "Windows Input Experience"):
                out.append(w)
        return True

    u.EnumWindows(cb, 0)
    return out


def find_window(title_part: str, windows: list[WindowInfo] | None = None) -> WindowInfo | None:
    """First visible window whose title contains `title_part` (case-insensitive)."""
    t = title_part.casefold()
    return next((w for w in (windows if windows is not None else visible_windows()) if t in w.title.casefold()), None)


# ---- rules (pure: easy to test) ------------------------------------------------------------------------------------------
def view_block_reason(w: WindowInfo, cfg: ScreenCfg) -> str | None:
    title = w.title.casefold()
    if any(p.casefold() in title for p in cfg.block_titles if p.strip()):
        return "its title matches the privacy blocklist"
    if w.process and w.process.casefold() in {p.casefold() for p in cfg.block_processes}:
        return f"{w.process} is on the privacy blocklist"
    return None


def control_deny_reason(w: WindowInfo, cfg: ScreenCfg, agent_name: str = "") -> str | None:
    if r := view_block_reason(w, cfg):
        return r
    if w.pid in _own_pids:
        return "that is the agent's own window (it must never operate its own approval UI)"
    if w.process in CONTROL_DENY_PROCESSES:
        return f"{w.process} is a shell / system tool: the agent may not type into it"
    title = w.title.casefold()
    if any(t in title for t in CONTROL_DENY_TITLES) or (agent_name and agent_name.casefold() in title):
        return "that window looks like an approval or security prompt"
    return None


def blocked_windows(cfg: ScreenCfg, windows: list[WindowInfo] | None = None) -> list[WindowInfo]:
    return [w for w in (windows if windows is not None else visible_windows()) if view_block_reason(w, cfg)]

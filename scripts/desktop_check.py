"""Opens the REAL UI on your desktop for about a minute against a throw-away daemon (temp data dir; needs API keys in ./.env).

Run from the repo root:  python -m uv run python scripts/desktop_check.py
It shows the avatar, sends one tiny real question (about $0.0005), presses the global hotkeys with synthetic keystrokes (the talk
test records ~1.5 s of microphone audio and discards it: nothing is uploaded) and prints PASS/FAIL per check. Screenshots go to a temp folder.
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

TMP = tempfile.mkdtemp(prefix="pa-desktop-shots-")  # screenshots land here
HOME = tempfile.mkdtemp(prefix="pa-desktop-")
os.environ.update({"PERSONAL_AGENT_HOME": HOME, "PERSONAL_AGENT_TOKEN": "desktop-test-token"})
os.environ.setdefault("PERSONAL_AGENT_ENV_FILE", os.path.join(os.getcwd(), ".env"))
os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from personal_agent.settings.settings import load_settings
from personal_agent.ui.app import UiApp
from personal_agent.ui.client import DaemonClient
from personal_agent.ui.i18n import set_language

R: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    R.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)


def pump(sec: float) -> None:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait(cond, sec: float = 20.0) -> bool:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND


def press(*keys: int) -> None:
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(k, 0, 2, 0)


daemon = subprocess.Popen([sys.executable, "-m", "personal_agent.core.main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
s = load_settings()
set_language("vi")
client = DaemonClient(s.server.host, s.server.port, "desktop-test-token")
ui = UiApp(app, s, client)
client.start()
try:
    check("tray icon is visible", ui.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable())
    check("connected to the daemon", wait(lambda: client.is_connected and bool(ui._last_settings) and ui.daemon_pid), detail=f"daemon pid {ui.daemon_pid}")
    check("no first-run wizard (keys found)", not getattr(ui, "_wizard_shown", False))
    check("global hotkeys registered", not ui.hotkeys.failed, detail=str(ui.hotkeys.failed) or "kill, talk, chat")

    # ---- avatar on the real desktop ----
    a = ui.avatar
    pump(0.6)
    check("avatar window is visible", a.isVisible(), detail=f"{a.geometry().getRect()} on screen {a.screen().name()}")
    scr = a.screen()
    check("avatar fully on screen", scr.availableGeometry().contains(a.frameGeometry()))
    hwnd = int(a.winId())
    cx, cy = a.x() + a.width() // 2, a.y() + a.height() // 2 + 8
    ratio = scr.devicePixelRatio()

    def top_window_at(x: int, y: int) -> int:
        h = user32.WindowFromPoint(wintypes.POINT(int(x * ratio), int(y * ratio)))
        return int(user32.GetAncestor(h, 2) or 0)  # GA_ROOT

    check("clicking the avatar body hits the avatar", top_window_at(cx, cy) == hwnd)
    check("clicking a transparent corner passes through", top_window_at(a.x() + 1, a.y() + 1) != hwnd, detail="WindowFromPoint under the avatar")
    ex = user32.GetWindowLongW(wintypes.HWND(hwnd), -20)
    check("avatar is topmost + tool window (no taskbar button)", bool(ex & 0x8) and bool(ex & 0x80), detail=hex(ex))

    # real pixels of the desktop around the avatar: transparent corners must show what is behind, not black/white boxes
    shot = scr.grabWindow(0, a.x() - 20, a.y() - 40, a.width() + 40, a.height() + 60)
    shot.save(os.path.join(TMP, "desktop_avatar.png"))

    # ---- a real conversation through the UI ----
    states: list[str] = []
    ui.send_user("17 nhân 23 bằng bao nhiêu? Chỉ cần ra con số.", [])
    t0 = time.monotonic()
    while ui.session.busy and time.monotonic() - t0 < 60:
        pump(0.05)
        if not states or states[-1] != a.state:
            states.append(a.state)
    check("agent answered through the real daemon + real models", ui.session.transcript[-1].kind == "agent" and "391" in ui.session.transcript[-1].text,
          detail=f"{time.monotonic() - t0:.1f}s, states seen: {states}, answer {ui.session.transcript[-1].text[:40]!r}")
    check("avatar went thinking -> success", "thinking" in states and a.state in ("success", "idle"))
    ui.show_chat()
    pump(0.5)
    ui.chat.grab().save(os.path.join(TMP, "desktop_chat.png"))
    check("chat window shows the answer (real fonts)", "391" in ui.chat.browser.toPlainText())

    # ---- approvals ----
    row = {"call_id": "d1", "thread_id": "default", "tool": "file_write", "tier": "T2", "risk": "medium", "reason": "cần bạn duyệt",
           "args": {"path": "a.txt", "content": "x"}, "preview": "--- before\n+++ after\n@@ -1 +1 @@\n-cũ\n+mới", "expires_at": time.time() + 500}
    ui.on_event({"type": "approval_request", "thread_id": "default", "approvals": [row]})
    pump(0.6)
    check("approval opens the center and avatar shows awaiting_approval", ui.approvals.isVisible() and a.state == "awaiting_approval")
    check("sticky speech bubble is shown", ui.bubble.isVisible())
    ui.approvals.grab().save(os.path.join(TMP, "desktop_approvals.png"))
    shot2 = scr.grabWindow(0, a.x() - 150, a.y() - 90, a.width() + 300, a.height() + 130)
    shot2.save(os.path.join(TMP, "desktop_bubble.png"))
    ui.on_event({"type": "approvals", "items": []})
    ui.approvals.hide()
    pump(0.3)

    # ---- hotkeys pressed for real (synthetic keystrokes -> RegisterHotKey -> Qt) ----
    VK_CTRL, VK_ALT = 0x11, 0x12
    ui.chat.hide()
    press(VK_CTRL, VK_ALT, ord("A"))
    check("Ctrl+Alt+A opens the chat", wait(lambda: ui.chat.isVisible(), 3))
    mic_ok = True
    press(VK_CTRL, VK_ALT, 0x20)
    started = wait(lambda: ui.recorder.active, 3)
    check("Ctrl+Alt+Space starts listening (avatar: listening)", started and wait(lambda: a.state == "listening", 2), detail="records the mic ~1.5 s, NOT uploaded")
    if started:
        ui.client.send = lambda p: True  # discard: never upload the test recording
        pump(1.5)
        lv = ui.recorder._heard
        press(VK_CTRL, VK_ALT, 0x20)
        check("Ctrl+Alt+Space stops listening", wait(lambda: not ui.recorder.active, 3))
        check("microphone delivered audio", len(ui.recorder._buf) > 16000, detail=f"{len(ui.recorder._buf) / 32000:.1f} s captured, speech heard: {lv}")
        client_send = DaemonClient.send.__get__(client)
        ui.client.send = client_send
    press(VK_CTRL, VK_ALT, ord("K"))
    check("Ctrl+Alt+K triggers the kill switch", wait(lambda: ui.session.killed, 5))
    client.send({"type": "release_kill"})
    check("kill switch can be released", wait(lambda: not ui.session.killed, 5))

    # ---- footprint ----
    import psutil

    me = psutil.Process()
    check("UI memory (real platform)", True, detail=f"{me.memory_info().rss / 1048576:.0f} MB RSS")
finally:
    client.stop()
    ui.hotkeys.clear()
    for w in (ui.chat, ui.approvals, ui.manager, ui.settings, ui.quick, ui.bubble, ui.avatar):
        w.hide()
    ui.tray.hide()
    daemon.terminate()
    pump(0.2)
bad = [r for r in R if not r[1]]
print(f"\n{len(R) - len(bad)}/{len(R)} checks passed")

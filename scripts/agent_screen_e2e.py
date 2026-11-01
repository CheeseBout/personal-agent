"""Phase 8 end to end on your REAL desktop: UI + daemon + real models + real screen.

Run from the repo root:  python -m uv run python scripts/agent_screen_e2e.py   (needs API keys in ./.env; costs a few cents)
It presses the capture / watch hotkeys, then asks the agent to click the Save button of a small test window. You must see:
the approval card first (nothing happens before you approve), then an outline around the button, then the click.
Do not touch the mouse while it runs.
"""

import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="pa-e2e-")
os.environ.update({"PERSONAL_AGENT_HOME": HOME, "PERSONAL_AGENT_TOKEN": "e2e-token"})
os.environ.setdefault("PERSONAL_AGENT_ENV_FILE", os.path.join(os.getcwd(), ".env"))
os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtWidgets import QApplication

from personal_agent.settings.settings import load_settings
from personal_agent.ui.app import UiApp
from personal_agent.ui.approvals import ThreadBox
from personal_agent.ui.client import DaemonClient
from personal_agent.ui.i18n import set_language

R: list[bool] = []
app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)
user32 = ctypes.windll.user32


def check(name: str, ok: bool, detail: str = "") -> None:
    R.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def wait(cond, sec: float = 20.0) -> bool:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


def hotkey(*keys: int) -> None:
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(k, 0, 2, 0)


SEEN: dict[int, list[str]] = {}


def target(title: str, x: int, y: int) -> subprocess.Popen[str]:
    p = subprocess.Popen([sys.executable, str(Path(__file__).with_name("_screen_target.py")), title, str(x), str(y)],
                         stdout=subprocess.PIPE, text=True, encoding="utf-8")
    assert p.stdout and p.stdout.readline().strip() == "READY"
    box = SEEN.setdefault(p.pid, [])
    threading.Thread(target=lambda: [box.append(ln.strip()) for ln in iter(p.stdout.readline, "")], daemon=True).start()  # type: ignore[union-attr]
    return p


daemon = subprocess.Popen([sys.executable, "-m", "personal_agent.core.main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
s = load_settings()
set_language("en")
client = DaemonClient(s.server.host, s.server.port, "e2e-token")
ui = UiApp(app, s, client)
client.start()
procs: list[subprocess.Popen[str]] = []
try:
    check("UI connected to a fresh daemon", wait(lambda: client.is_connected and bool(ui._last_settings) and ui.daemon_pid, 40))
    tg = target("PA Screen Test", 120, 160)
    procs.append(tg)
    time.sleep(1)

    # ---- on-demand capture by hotkey ----
    ui.chat.hide()
    hotkey(0x11, 0x12, ord("S"))  # Ctrl+Alt+S
    check("Ctrl+Alt+S captures the screen into the chat as an image", wait(lambda: ui.chat.isVisible() and len(ui.chat._files) == 1, 8),
          str([f["name"] for f in ui.chat._files]))
    if ui.chat._files:
        check("the capture is a real PNG/JPEG of a plausible size", len(ui.chat._files[0]["data"]) > 20_000 and ui.chat._files[0]["mime"].startswith("image/"))
    ui.chat._files.clear()

    # ---- continuous watching by hotkey ----
    hotkey(0x11, 0x12, ord("W"))  # Ctrl+Alt+W
    check("Ctrl+Alt+W starts watching: red badge + red dot + daemon state", wait(lambda: ui.screen_state == "on" and ui.watch_badge.isVisible() and ui.avatar._watching, 8))
    time.sleep(2.5)  # baseline frame
    procs.append(target("PA Change Test", 700, 300))  # a new window = a meaningful change
    check("a new window is noticed as a screen change", wait(lambda: "screen changed" in ui.tray.toolTip(), 12), ui.tray.toolTip())
    hotkey(0x11, 0x12, ord("W"))
    check("Ctrl+Alt+W again stops watching and clears the indicators", wait(lambda: ui.screen_state == "off" and not ui.watch_badge.isVisible() and not ui.avatar._watching, 8))
    procs[-1].terminate()

    # ---- the agent clicks a button, only after approval ----
    print("... asking the agent to click the Save button (real models, ~30-90 s) ...", flush=True)
    before = list(SEEN[tg.pid])
    ui.send_user("There is a window titled 'PA Screen Test'. Click its 'Save' button, then tell me it is done.", [])
    got_card = wait(lambda: bool(ui.session.approvals), 120)
    card = next(iter(ui.session.approvals.values()), {})
    check("the agent asked for approval of ui_act (and nothing clicked yet)", got_card and card.get("tool") == "ui_act" and "CLICKED" not in SEEN[tg.pid],
          f"tool={card.get('tool')} args={str(card.get('args'))[:120]}")
    if got_card and card.get("tool") == "ui_act":
        check("the approval card names the step and says how to abort", "Save" in str(card.get("preview")) and "kill switch" in str(card.get("preview")))
        check("avatar shows awaiting_approval", ui.avatar.state == "awaiting_approval")
        box = ui.approvals.widget().findChild(ThreadBox)
        box.cards[0].radios["approve"].setChecked(True)
        box.submit.click()
        check("outline appears around the target before the click", wait(lambda: ui.highlight.isVisible(), 30), f"{ui.highlight.geometry().getRect()}")
        # If the model needs a second attempt it must ask again: approve up to 2 more cards (each is shown to you in the same way)
        def answered() -> bool:
            if ui.session.approvals and not ui.approvals.widget().findChild(ThreadBox) is None:
                pending = ui.approvals.widget().findChild(ThreadBox)
                if pending.submit.isEnabled():
                    print("    (another approval card: approving)", flush=True)
                    pending.cards[0].radios["approve"].setChecked(True)
                    pending.submit.click()
            return bool(ui.session.transcript) and ui.session.transcript[-1].kind == "agent"

        check("the click reaches the window after approval", wait(lambda: "CLICKED" in SEEN[tg.pid] or answered(), 60) and wait(lambda: "CLICKED" in SEEN[tg.pid], 20))
        done = wait(answered, 90)
        check("the agent reports back", done, (ui.session.transcript[-1].text[:80] if ui.session.transcript else ""))
    else:
        print("   transcript:", [(e.kind, e.text[:100]) for e in ui.session.transcript][-4:])
    print("   transcript tail:", [(e.kind, e.text[:110]) for e in ui.session.transcript][-6:], flush=True)
finally:
    client.send({"type": "screen_mode", "action": "off"})
    for p in procs:
        p.terminate()
    ui.hotkeys.clear()
    client.stop()
    for w in (ui.chat, ui.approvals, ui.manager, ui.settings, ui.quick, ui.bubble, ui.avatar, ui.highlight, ui.watch_badge):
        w.hide()
    ui.tray.hide()
    daemon.terminate()
print(f"\n{sum(R)}/{len(R)} checks passed")

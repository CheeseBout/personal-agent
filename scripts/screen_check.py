"""Phase 8 on your REAL desktop: capture, privacy masking, OCR, UI Automation, vision, and mouse/keyboard control.

Run from the repo root:  python -m uv run python scripts/screen_check.py
It opens two small test windows ("PA Screen Test" and a "KeePass test window" that the privacy blocklist must hide), then uses the
agent's own screen tools on them. It WILL move your mouse and type into the test window for a few seconds, and makes one small
vision-model call (about $0.001, needs API keys in ./.env). Do not touch the mouse while it runs (that is one of the checks).
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("PERSONAL_AGENT_ENV_FILE", os.path.join(os.getcwd(), ".env"))
import ctypes

from personal_agent.perception import capture, control, hooks, ocr, uia, windows
from personal_agent.settings.settings import load_settings
from personal_agent.tools import screen_tools

R: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    R.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


SEEN: dict[int, list[str]] = {}


def start(title: str, x: int, y: int) -> subprocess.Popen[str]:
    p = subprocess.Popen([sys.executable, str(Path(__file__).with_name("_screen_target.py")), title, str(x), str(y)], stdout=subprocess.PIPE, text=True, encoding="utf-8")
    assert p.stdout and p.stdout.readline().strip() == "READY"
    box = SEEN.setdefault(p.pid, [])
    threading.Thread(target=lambda: [box.append(ln.strip()) for ln in iter(p.stdout.readline, "")], daemon=True).start()  # type: ignore[union-attr]
    return p


def lines(p: subprocess.Popen[str], seconds: float = 1.2) -> list[str]:
    """Output the target printed since the last call."""
    time.sleep(seconds)
    box = SEEN[p.pid]
    out, box[:] = list(box), []
    return out


target = start("PA Screen Test", 120, 120)
private = start("KeePass test window", 760, 120)  # side by side: it must not cover the target here
cfg = load_settings().screen
events: list[dict] = []
hooks.emit, hooks.observers = events.append, (lambda: 1)  # pretend one UI is connected
try:
    time.sleep(1.0)
    wins = windows.visible_windows()
    w = windows.find_window("PA Screen Test", wins)
    check("test window found by title", w is not None, str(w.rect) if w else "")
    check("privacy blocklist flags the 'KeePass' window", windows.view_block_reason(windows.find_window("KeePass test", wins), cfg) is not None)
    check("screen_windows hides the private window's title", "KeePass test" not in screen_tools.screen_windows.invoke({}) and "(private window)" in screen_tools.screen_windows.invoke({}))

    # ---- capture + masking ----
    shot = capture.capture("screen", cfg)
    check("full-screen capture works and masks the private window", shot.image.width > 500 and shot.masked >= 1, f"{shot.image.size}, masked {shot.masked}")
    try:
        capture.capture("window", cfg, window="KeePass test")
        check("capturing a private window is refused", False)
    except capture.Blocked:
        check("capturing a private window is refused", True)
    uia.set_focus(w.hwnd)  # a window capture shows what is on screen there: bring the target to the front so nothing covers it
    time.sleep(0.6)
    ws = capture.capture("window", cfg, window="PA Screen Test")
    check("window capture", ws.title == "PA Screen Test" and ws.image.width > 200, str(ws.image.size))

    # ---- OCR ----
    lang, found = ocr.recognize(ws.image, origin=ws.rect[:2])
    hit = next((ln for ln in found if "Save the report" in str(ln["text"])), None)
    check("local OCR reads the label and gives screen coordinates", hit is not None, f"{lang}, {hit or [ln['text'] for ln in found]}")

    # ---- UI Automation ----
    els, trunc = uia.walk(w.hwnd)
    names = {(e.control_type, e.name) for e in els}
    check("UIA lists the Save button and both edit fields", ("Button", "Save") in names and sum(e.control_type == "Edit" for e in els) >= 2, f"{len(els)} elements")
    check("UIA flags the password field and never shows its value", any(e.password for e in els) and all(not e.value for e in els if e.password))
    tree = screen_tools.ui_tree.invoke({"window": "PA Screen Test"})
    check("ui_tree tool output is marked untrusted", tree.startswith("[UNTRUSTED SCREEN CONTENT"))
    check("ui_tree refuses the private window", screen_tools.ui_tree.invoke({"window": "KeePass test"}).startswith("REFUSED"))

    # ---- control ----
    print("... control checks start in 3 s: please do NOT touch the mouse or keyboard until the end ...", flush=True)
    time.sleep(3)
    ctypes.windll.user32.SetCursorPos(wins[0].rect[2] - 40 if wins else 800, 40)  # park the pointer out of the way

    class Retrying(control.Runner):
        """A real person touching the mouse aborts a run (that is a feature): retry once so this check measures the code, not you."""

        def run(self, actions: list[control.Action]) -> list[str]:
            for attempt in (1, 2):
                log = super().run(actions)
                if not any("moved the mouse" in ln for ln in log) or attempt == 2:
                    return log
                print("    (the pointer was moved during the run - retrying once)", flush=True)
                time.sleep(1)
            return log

    r = Retrying(cfg)
    log = r.run([control.Action(op="click", window="PA Screen Test", name="Save", control_type="Button")])
    got = lines(target)
    check("click on the Save button (UIA invoke)", "CLICKED" in got and "invoked" in log[0], f"{log} {got}")
    log = r.run([control.Action(op="type", window="PA Screen Test", name="Notes", control_type="Edit", text="xin chào 123")])
    got = lines(target)
    check("typing into the Notes field (Vietnamese + digits)", any(g == "TEXT:xin chào 123" for g in got), f"{got[-1:]}")
    log = r.run([control.Action(op="type", window="PA Screen Test", name="Password", control_type="Edit", text="hunter2")])
    got = lines(target)
    check("typing into the password field is refused", "STOPPED" in log[0] and not any(g.startswith("PASSWORD:") for g in got), log[0])
    log = r.run([control.Action(op="click", window="KeePass test", name="Save")])
    check("controlling the private window is refused", "STOPPED" in log[0] and "blocklist" in log[0], log[0])
    log = r.run([control.Action(op="key", window="PA Screen Test", name="Notes", control_type="Edit", keys="ctrl+a"), control.Action(op="type", window="PA Screen Test", text="X")])  # no element: type into the focused field, keep the selection
    got = lines(target)
    check("keyboard shortcut then typing replaces the text", "TEXT:X" in got, str(got[-2:]))
    check("a highlight was announced before each action", sum(e["type"] == "highlight" for e in events) >= 4, f"{sum(e['type'] == 'highlight' for e in events)} highlights, last log {log}")

    # ---- a window covering the target: a real mouse click must be refused ----
    private.terminate()
    private = start("KeePass test window", 150, 150)  # now it sits on top of the target
    time.sleep(0.8)
    nf = control.Runner(cfg.model_copy(update={"block_titles": [], "block_processes": []}))  # (not blocked here: test the covering rule itself)
    nf.be.invoke = lambda el: False  # type: ignore[method-assign]  # force the mouse fallback
    log = nf.run([control.Action(op="click", window="PA Screen Test", name="Save", control_type="Button")])
    check("a click is refused when another window covers the target", "covered by" in log[0] and "CLICKED" not in lines(target, 0.5), log[0])
    private.terminate()

    # ---- abort when the user moves the mouse ----
    def user_moves_mouse() -> None:
        time.sleep(0.9)
        ctypes.windll.user32.SetCursorPos(5, 5)

    t = threading.Thread(target=user_moves_mouse)
    cfg2 = cfg.model_copy(update={"highlight_ms": 100})
    t.start()
    log = control.Runner(cfg2).run([control.Action(op="wait", seconds=3), control.Action(op="click", window="PA Screen Test", name="Save")])
    t.join()
    got = lines(target, 0.5)
    check("moving the mouse aborts the sequence before the click", "moved the mouse" in log[0] and "CLICKED" not in got, log[0])

    # ---- vision (one real call) ----
    t0 = time.perf_counter()
    out = screen_tools.screen_describe.invoke({"question": "Which button labels and text fields are visible in the PA Screen Test window? Answer in one sentence.", "target": "window:PA Screen Test"})
    check("vision model describes the window and the answer is marked untrusted", out.startswith("[UNTRUSTED") and "save" in out.lower(), f"{time.perf_counter() - t0:.1f}s {out[40:140]!r}")
    check("the UI was told the agent looked (screen_viewed)", any(e["type"] == "screen_viewed" for e in events))
finally:
    for p in (target, private):
        p.terminate()
print(f"\n{sum(R)}/{len(R)} checks passed")

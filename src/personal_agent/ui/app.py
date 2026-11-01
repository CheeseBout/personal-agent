"""`agent-ui`: tray + chat + approval center + manager/settings + avatar + voice, all talking to the daemon over WebSocket.

The UI holds no agent state: closing it never stops a running task, and on reconnect the daemon re-sends pending approvals."""

from __future__ import annotations

import argparse
import base64
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QFileSystemWatcher, QLockFile, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from personal_agent.settings.secrets import get_api_token
from personal_agent.settings.settings import Settings, load_settings
from personal_agent.ui import daemon
from personal_agent.ui.approvals import ApprovalCenter
from personal_agent.ui.avatar import AvatarWindow, SpeechBubble
from personal_agent.ui.avatar_state import AvatarState
from personal_agent.ui.chat import ChatWindow, QuickInput
from personal_agent.ui.client import DaemonClient
from personal_agent.ui.i18n import set_language, tr
from personal_agent.ui.model import Session
from personal_agent.ui.pages import ManagerWindow
from personal_agent.ui.screen import (
    HighlightOverlay,
    RegionSelector,
    WatchIndicator,
    capture_attachment,
)
from personal_agent.ui.settings_dialog import FirstRunWizard, SettingsDialog
from personal_agent.ui.skin import draw_builtin, load_skin, skins_root
from personal_agent.ui.system import Hotkeys
from personal_agent.ui.voice_io import Recorder, Speaker

PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")


class UiApp:
    def __init__(self, qapp: QApplication, settings: Settings, client: DaemonClient, thread: str = "default",
                 with_avatar: bool = True, with_hotkeys: bool = True) -> None:
        self.qapp, self.cfg, self.client, self.thread = qapp, settings, client, thread
        self.daemon_pid: int | None = None
        self.session = Session(thread, settings.agent.name)
        self.state = AvatarState(settings.ui.sleep_after_s)
        self.voice_mode = "chat"  # where a transcript goes: "chat" (into the box) or "send" (straight to the agent)
        self._wizard_shown = False
        self._last_settings: dict[str, Any] = {}

        self.chat = ChatWindow(self.session)
        self.approvals = ApprovalCenter(self.session)
        self.manager = ManagerWindow(client.send)
        self.settings = SettingsDialog(client.send, lambda: self.daemon_pid)
        self.quick = QuickInput()
        self.bubble = SpeechBubble()
        self.recorder = Recorder(settings.voice.stt.max_seconds)
        self.highlight = HighlightOverlay()
        self.watch_badge = WatchIndicator()
        self.region = RegionSelector()
        self.screen_state = "off"
        self._watch_actions: list[QAction] = []
        self.speaker = Speaker()
        self.hotkeys = Hotkeys()
        self.avatar = AvatarWindow(load_skin(settings.ui.skin), settings.ui.avatar_scale, settings.ui.avatar_always_on_top)
        self.with_avatar = with_avatar
        self._state_timer = QTimer()
        self._state_timer.setSingleShot(True)
        self._state_timer.timeout.connect(self._refresh_state)
        self._skin_watch = QFileSystemWatcher()
        self._skin_debounce = QTimer()
        self._skin_debounce.setSingleShot(True)
        self._skin_debounce.timeout.connect(self._reload_skin)
        self._skin_watch.fileChanged.connect(lambda _p: self._skin_debounce.start(300))
        self._skin_watch.directoryChanged.connect(lambda _p: self._skin_debounce.start(300))

        self.tray = QSystemTrayIcon(self._icon())
        self._wire()
        self._build_menus()
        self.apply_ui()
        if with_hotkeys:
            self._register_hotkeys()
        self.tray.show()

    # ---- wiring --------------------------------------------------------------------------------------------------------
    def _wire(self) -> None:
        c = self.client
        c.message.connect(self.on_event)
        c.connected.connect(self.on_connected)
        c.disconnected.connect(self.on_disconnected)
        self.chat.submitted.connect(self.send_user)
        self.chat.stop_requested.connect(self.kill)
        self.chat.record_toggled.connect(lambda on: self.start_recording("chat") if on else self.recorder.stop())
        self.quick.submitted.connect(self.send_user)
        self.approvals.decided.connect(self.send_decisions)
        self.settings.ui_changed.connect(lambda: c.send({"type": "settings_get"}))
        self.avatar.single_clicked.connect(lambda: self.quick.open_at(self.avatar))
        self.avatar.double_clicked.connect(self.show_chat)
        self.avatar.files_dropped.connect(lambda paths: self.quick.open_at(self.avatar, paths))
        self.bubble.clicked.connect(self._bubble_clicked)
        self.tray.activated.connect(lambda r: self.show_chat() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.recorder.finished.connect(self._recorded)
        self.watch_badge.clicked.connect(lambda: self.client.send({"type": "screen_mode", "action": "off"}))
        self.region.selected.connect(lambda r: self.capture("region", tuple(r)))
        self.region.cancelled.connect(lambda: self.say(tr("Capture cancelled.", "Đã hủy chụp."), ms=2000))
        self.recorder.failed.connect(lambda m: self.say(m, ms=3000))
        self.speaker.speaking_changed.connect(self._speaking)
        self.speaker.failed.connect(lambda m: self.say(tr("Cannot speak: {m}", "Không đọc được: {m}", m=m), ms=4000))

    def _action(self, menu: QMenu, en: str, vi: str, fn: Any) -> QAction:
        a = QAction(tr(en, vi), menu)
        a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        return a

    def _build_menus(self) -> None:
        def build() -> QMenu:
            m = QMenu()
            self._action(m, "Open chat", "Mở chat", self.show_chat)
            self.approval_action = self._action(m, "Approvals", "Phê duyệt", self.show_approvals)
            sub = m.addMenu(tr("Manage", "Quản lý"))
            for key, en, vi in (("memory", "Memory", "Bộ nhớ"), ("rag", "Documents", "Tài liệu"), ("mcp", "Tools / MCP", "Công cụ / MCP"),
                                ("schedules", "Schedules", "Lịch chạy"), ("rules", "Remembered approvals", "Quy tắc đã nhớ"),
                                ("audit", "Audit log", "Nhật ký"), ("usage", "Usage", "Mức dùng"), ("policy", "Policy", "Chính sách")):
                self._action(sub, en, vi, lambda k=key: self.manager.open_page(k))
            self._action(m, "Settings", "Cài đặt", self.show_settings)
            m.addSeparator()
            cap = m.addMenu(tr("Capture", "Chụp màn hình"))
            self._action(cap, "Whole screen", "Toàn màn hình", lambda: self.capture("screen"))
            self._action(cap, "Active window (in 2 s)", "Cửa sổ đang dùng (sau 2 giây)", self.capture_window_soon)
            self._action(cap, "Select an area…", "Chọn một vùng…", self.capture_region)
            self._watch_actions.append(self._action(m, "Watch the screen", "Theo dõi màn hình", self.toggle_watch))
            self._action(m, "Stop speaking", "Ngừng đọc", self.speaker.stop)
            self._action(m, "Pause agent (kill switch)", "Tạm dừng agent (công tắc khẩn)", self.kill)
            self._action(m, "Resume agent", "Cho agent chạy lại", lambda: self.client.send({"type": "release_kill"}))
            return m

        self.tray_menu = build()
        self.tray_menu.addSeparator()
        self._action(self.tray_menu, "Show / hide avatar", "Hiện / ẩn avatar", self.toggle_avatar)
        self._action(self.tray_menu, "Quit the UI (the agent keeps running)", "Thoát giao diện (agent vẫn chạy)", self.quit)
        self.tray.setContextMenu(self.tray_menu)
        self.avatar_menu = build()
        self._action(self.avatar_menu, "Hide avatar", "Ẩn avatar", self.toggle_avatar)
        self._action(self.avatar_menu, "Quit the UI", "Thoát giao diện", self.quit)
        self.avatar.set_menu(self.avatar_menu)

    @staticmethod
    def _icon() -> QIcon:
        return QIcon(draw_builtin("idle", 0, 64))

    # ---- connection ---------------------------------------------------------------------------------------------------------
    def on_connected(self) -> None:
        self.state.connected = True
        for m in ({"type": "settings_get"}, {"type": "usage"}, {"type": "about", "ui_pid": os.getpid()},  # the pid keeps the agent off our windows
                  {"type": "screen_mode", "action": "status"}):
            self.client.send(m)
        self._refresh_state()
        self.tray.setToolTip(self.session.agent_name)

    def on_disconnected(self) -> None:
        self.state.connected = False
        self._refresh_state()
        self.tray.setToolTip(f"{self.session.agent_name} — {tr('offline', 'mất kết nối')}")

    # ---- events from the daemon --------------------------------------------------------------------------------------------
    def on_event(self, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        before = set(self.session.approvals)
        changed = self.session.apply(ev)
        self.state.pending_approvals = len(self.session.approvals)
        self.state.on_event(ev)
        self.chat.refresh(changed)
        self.manager.on_event(ev)
        if "approvals" in changed:
            self.approvals.refresh()
            new = set(self.session.approvals) - before
            self.approval_action.setText(tr("Approvals ({n})", "Phê duyệt ({n})", n=len(self.session.approvals)))
            if new:
                tool = self.session.approvals[min(new)]["tool"]
                self.say(tr("Needs your approval: {t}", "Cần bạn duyệt: {t}", t=tool), sticky=True)
                self.tray.showMessage(self.session.agent_name, tr("Needs your approval: {t}", "Cần bạn duyệt: {t}", t=tool))
                self.show_approvals()
            elif not self.session.approvals:
                self.bubble.hide()
        if "name" in changed:
            self.apply_name()
        if t == "settings":
            self.on_settings(ev)
        elif t == "secrets":
            self.client.send({"type": "settings_get"})
        elif t == "test_role":
            self.settings.on_test_result(ev)
            if self._wizard_shown and hasattr(self, "wizard"):
                self.wizard.on_test_result(ev)
        elif t == "about":
            self.daemon_pid = ev.get("pid")
        elif t == "transcript":
            self.on_transcript(str(ev.get("text", "")))
        elif t == "final":
            self.say(str(ev.get("text", "")).strip().splitlines()[0][:120] if ev.get("text") else "", ms=5000)
            self.client.send({"type": "usage"})  # keep the budget meters current after every answer
            if self.cfg.voice.tts.enabled and ev.get("text"):
                self.speaker.speak(str(ev["text"]), self.cfg.voice.tts)
        elif t == "error" and ev.get("code") not in ("approval_pending",):
            self.say(f"⚠ {ev.get('message', '')}", ms=5000)
        elif t == "killed":
            self.speaker.stop()
        elif t == "schedule_run":
            self.tray.showMessage(self.session.agent_name, str(ev.get("status", "")))
        elif t == "screen_mode":
            self.on_screen_mode(ev)
        elif t == "screen_change":
            self.tray.setToolTip(f"{self.session.agent_name} — " + tr("screen changed ×{n}", "màn hình đổi ×{n}", n=ev.get("changes", 0)))
        elif t == "screen_viewed":
            self.say("👁 " + tr("{n} looked at your screen ({k})", "{n} đã xem màn hình ({k})", n=self.session.agent_name, k=ev.get("kind", "")), ms=3500)
        elif t == "highlight":
            self.highlight.show_rect(tuple(ev["rect"]), str(ev.get("label", "")), int(ev.get("ms", 700)))
        elif t == "control_aborted":
            self.say("⛔ " + str(ev.get("reason", "")), ms=6000)
            self.tray.showMessage(self.session.agent_name, "⛔ " + str(ev.get("reason", "")))
        self._refresh_state()

    def on_settings(self, ev: dict[str, Any]) -> None:
        self._last_settings = ev
        self.settings.load(ev)
        self.cfg.ui = type(self.cfg.ui).model_validate(ev["ui"])
        self.cfg.voice = type(self.cfg.voice).model_validate(ev["voice"])
        self.cfg.agent.language = ev["agent"]["language"]
        self.session.agent_name = ev["agent"]["name"]
        self.apply_ui()
        self.apply_name()
        if hasattr(self, "wizard"):
            self.wizard.models = ev["models"]
        if not self._wizard_shown and not any(ev["secrets"].get(k) for k in PROVIDER_KEYS):
            self.show_wizard()

    def show_wizard(self) -> None:
        self._wizard_shown = True
        self.wizard = FirstRunWizard(self.client.send)
        self.wizard.models = self._last_settings.get("models", {})
        self.wizard.language_chosen.connect(self._wizard_language)
        self.wizard.show()

    def _wizard_language(self, lang: str) -> None:
        self.client.send({"type": "settings_set", "patch": {"ui": {"language": lang}, "agent": {"language": lang}}})

    # ---- applying settings -----------------------------------------------------------------------------------------------------
    def apply_name(self) -> None:
        n = self.session.agent_name
        self.chat.retranslate()
        self.tray.setToolTip(n)
        self.avatar.set_label(n)
        self.approvals.setWindowTitle(f"{n} — {tr('Approvals', 'Phê duyệt')}")

    def apply_ui(self) -> None:
        u = self.cfg.ui
        set_language(u.language)
        for w in (self.chat, self.manager, self.settings, self.quick):
            w.retranslate()
        self.avatar.set_scale(u.avatar_scale)
        if self.avatar._skin.name != u.skin:
            self.avatar.set_skin(load_skin(u.skin))
            self._watch_skin()
        if self.avatar.always_on_top != u.avatar_always_on_top:
            self.avatar.set_always_on_top(u.avatar_always_on_top, show=self.avatar.isVisible())
        self.avatar.set_wander(u.avatar_wander)
        self.state.sleep_after_s = u.sleep_after_s
        self.recorder.max_seconds = self.cfg.voice.stt.max_seconds
        if self.with_avatar and u.avatar_enabled:
            if not self.avatar.isVisible():
                self.avatar.place_default()
                self.avatar.show()
            self.avatar.keep_on_screen()
        else:
            self.avatar.hide()
        self.avatar.set_label(self.session.agent_name)
        self._watch_skin()

    def _watch_skin(self) -> None:
        old = self._skin_watch.files() + self._skin_watch.directories()
        if old:
            self._skin_watch.removePaths(old)
        folder = skins_root() / self.cfg.ui.skin
        if self.cfg.ui.skin != "default" and folder.is_dir():
            self._skin_watch.addPath(str(folder))
            self._skin_watch.addPaths([str(p) for p in folder.iterdir() if p.is_file()])

    def _reload_skin(self) -> None:
        self.avatar.set_skin(load_skin(self.cfg.ui.skin))
        self._watch_skin()

    def _register_hotkeys(self) -> None:
        self.hotkeys.clear()
        hk = self.cfg.ui.hotkeys
        self.hotkeys.register("kill", hk.get("kill", ""), self.kill)
        self.hotkeys.register("talk", hk.get("talk", ""), self.toggle_talk)
        self.hotkeys.register("chat", hk.get("chat", ""), self.show_chat)
        self.hotkeys.register("capture", hk.get("capture", ""), lambda: self.capture("screen"))
        self.hotkeys.register("watch", hk.get("watch", ""), self.toggle_watch)
        if self.hotkeys.qt_filter is not None:
            self.qapp.installNativeEventFilter(self.hotkeys.qt_filter)
        for name, why in self.hotkeys.failed.items():
            self.tray.showMessage(self.session.agent_name, tr("Hotkey '{n}' unavailable: {w}", "Phím tắt '{n}' không dùng được: {w}", n=name, w=why))

    # ---- state / bubble -----------------------------------------------------------------------------------------------------------
    def _refresh_state(self) -> None:
        self.avatar.set_state(self.state.current())
        wait = self.state.next_change_in()
        if wait is not None:
            self._state_timer.start(int(wait * 1000) + 50)  # one timer, only while a change is scheduled

    def say(self, text: str, ms: int = 4000, sticky: bool = False) -> None:
        if text and self.avatar.isVisible():
            self.bubble.say(text, self.avatar, ms, sticky)

    def _bubble_clicked(self) -> None:
        self.show_approvals() if self.session.approvals else self.show_chat()

    # ---- user actions --------------------------------------------------------------------------------------------------------------
    def send_user(self, text: str, files: list[dict[str, Any]]) -> None:
        self.speaker.stop()
        names = [f["name"] for f in files]
        self.session.user_says(text or "🎤", names)
        self.state.user_sent()
        payload = {"type": "user", "thread_id": self.thread, "text": text, "attachments": files}
        if not self.client.send(payload):
            self.session.apply({"type": "error", "code": "offline", "message": tr("The agent is not running or not reachable yet.", "Agent chưa chạy hoặc chưa kết nối được.")})
        self.chat.refresh()
        self._refresh_state()

    def send_decisions(self, thread_id: str, decisions: dict[str, Any]) -> None:
        if self.client.send({"type": "approve", "thread_id": thread_id, "decisions": decisions}):
            for cid in decisions:
                self.session.approvals.pop(cid, None)
            self.session.busy = True
            self.state.pending_approvals = len(self.session.approvals)
            self.state.busy = True
            self.approvals.refresh()
            self.chat.refresh({"busy", "approvals"})
            self._refresh_state()

    def kill(self) -> None:
        self.speaker.stop()
        self.client.send({"type": "kill"})

    def show_chat(self) -> None:
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        self.chat.composer.setFocus()

    def show_approvals(self) -> None:
        self.approvals.refresh()
        self.approvals.show()
        self.approvals.raise_()

    def show_settings(self) -> None:
        self.client.send({"type": "settings_get"})
        self.settings.show()
        self.settings.raise_()

    def toggle_avatar(self) -> None:
        self.avatar.setVisible(not self.avatar.isVisible())
        self.bubble.hide()

    # ---- screen ---------------------------------------------------------------------------------------------------------------------
    def capture(self, kind: str, rect: tuple[int, int, int, int] | None = None) -> None:
        """Capture (privacy-masked) and attach to the chat; you decide whether to send it."""
        from personal_agent.perception import capture as cap

        try:
            att, masked, title = capture_attachment(kind, self.cfg.screen, rect)
        except cap.Blocked as e:
            return self.say("🔒 " + str(e), ms=5000)
        except (cap.CaptureError, OSError, ValueError) as e:
            return self.say(f"⚠ {e}", ms=5000)
        self.show_chat()
        self.chat.add_attachment(att)
        note = tr(" ({n} private window hidden)", " (đã ẩn {n} cửa sổ riêng tư)", n=masked) if masked else ""
        self.say("📎 " + tr("Captured: {t}", "Đã chụp: {t}", t=(title or tr("screen", "màn hình"))[:40]) + note, ms=3500)

    def capture_window_soon(self) -> None:
        self.say(tr("Click the window you want in 2 s…", "Bấm vào cửa sổ cần chụp trong 2 giây…"), ms=2000)
        QTimer.singleShot(2200, lambda: self.capture("active_window"))

    def capture_region(self) -> None:
        self.region.start()

    def toggle_watch(self) -> None:
        on = self.screen_state == "off"
        self.client.send({"type": "screen_mode", "action": "on" if on else "off"})

    def on_screen_mode(self, ev: dict[str, Any]) -> None:
        self.screen_state = str(ev.get("state", "off"))
        self.watch_badge.set_state(self.screen_state, float(ev.get("minutes_left") or 0), bool(ev.get("private")))
        self.avatar.set_watching(self.screen_state == "on")
        for a in self._watch_actions:
            a.setText(tr("Stop watching the screen", "Dừng theo dõi màn hình") if self.screen_state != "off" else tr("Watch the screen", "Theo dõi màn hình"))
        if ev.get("reason") == "timeout":
            self.say(tr("Screen watching switched itself off.", "Đã tự tắt theo dõi màn hình."), ms=5000)
        elif self.screen_state == "on" and not ev.get("private"):
            self.say(tr("Watching the screen (nothing is saved). Click the red badge to stop.", "Đang theo dõi màn hình (không lưu gì). Bấm huy hiệu đỏ để dừng."), ms=4500)

    # ---- voice ------------------------------------------------------------------------------------------------------------------------
    def toggle_talk(self) -> None:
        """Global hotkey: first press starts listening, second press (or silence) stops and sends what was said."""
        if self.recorder.active:
            self.recorder.stop()
        else:
            self.start_recording("send")

    def start_recording(self, mode: str) -> None:
        self.speaker.stop()  # talking over the agent interrupts it
        self.voice_mode = mode
        if self.recorder.start():
            self.state.listening = True
            self._refresh_state()
            self.say(tr("Listening…", "Đang nghe…"), sticky=True)

    def _recorded(self, wav: bytes) -> None:
        self.state.listening = False
        self.bubble.hide()
        self.chat.mic_btn.setChecked(False)
        self.say(tr("Transcribing…", "Đang chuyển giọng nói thành chữ…"), ms=15000)
        if not self.client.send({"type": "transcribe", "data": base64.b64encode(wav).decode("ascii"), "mime": "audio/wav"}):
            self.say(tr("Not connected.", "Chưa kết nối."), ms=3000)
        self._refresh_state()

    def on_transcript(self, text: str) -> None:
        self.bubble.hide()
        if not text:
            return self.say(tr("I did not catch that.", "Mình chưa nghe rõ."), ms=3000)
        if self.voice_mode == "send":
            self.send_user(text, [])
        else:
            self.chat.composer.insertPlainText(text)
            self.show_chat()

    def _speaking(self, on: bool) -> None:
        self.state.speaking = on
        self._refresh_state()

    # ---- lifecycle ---------------------------------------------------------------------------------------------------------------------------
    def quit(self) -> None:
        self.speaker.stop()
        self.client.send({"type": "screen_mode", "action": "off"})  # never leave watching on behind a closed UI
        self.hotkeys.clear()
        self.client.stop()
        self.tray.hide()
        self.qapp.quit()


def main() -> None:
    ap = argparse.ArgumentParser(description="Desktop UI for the agent daemon (tray, chat, approvals, avatar, voice).")
    ap.add_argument("--thread", default="default")
    ap.add_argument("--no-avatar", action="store_true")
    ap.add_argument("--no-daemon", action="store_true", help="do not start the daemon when it is not running")
    a = ap.parse_args()
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)  # lives in the tray
    lock = QLockFile(str(Path(tempfile.gettempdir()) / "personal-agent-ui.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print("agent-ui is already running (look for the tray icon).")
        return
    s = load_settings()
    set_language(s.ui.language)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("warning: no system tray available; use the avatar or run with a tray-capable shell.", file=sys.stderr)
    if not a.no_daemon and not os.environ.get("PERSONAL_AGENT_NO_DAEMON") and not daemon.is_up(s.server.host, s.server.port):
        daemon.start_daemon()
    client = DaemonClient(s.server.host, s.server.port, get_api_token())
    ui = UiApp(qapp, s, client, a.thread, with_avatar=not a.no_avatar)
    client.start()
    code = qapp.exec()
    lock.unlock()
    del ui
    raise SystemExit(code)


if __name__ == "__main__":
    main()


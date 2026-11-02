"""Settings dialog (General, Models, Keys, Voice, Budgets, About) and the first-run wizard."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from personal_agent.ui import system
from personal_agent.ui.i18n import tr
from personal_agent.ui.skin import available_skins

Send = Callable[[dict[str, Any]], Any]
KEY_HELP = {"ANTHROPIC_API_KEY": "Anthropic (Claude)", "GOOGLE_API_KEY": "Google (Gemini)", "OPENAI_API_KEY": "OpenAI",
            "OPENROUTER_API_KEY": "OpenRouter", "LANGFUSE_PUBLIC_KEY": "Langfuse public key",
            "LANGFUSE_SECRET_KEY": "Langfuse secret key", "LANGFUSE_BASE_URL": "Langfuse URL"}


class SettingsDialog(QDialog):
    ui_changed = Signal()  # UI-side preference changed (language, avatar, skin...): the app re-applies it

    def __init__(self, send: Send, daemon_pid: Callable[[], int | None]) -> None:
        super().__init__()
        self._send, self._pid = send, daemon_pid
        self.data: dict[str, Any] = {}
        self.resize(640, 520)
        self.tabs = QTabWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        self.status = QLabel()
        lay.addWidget(self.status)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        box.accepted.connect(self.save)
        box.rejected.connect(self.hide)
        box.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save)
        lay.addWidget(box)
        self._stats = QTimer(self)
        self._stats.timeout.connect(self._refresh_stats)
        self.tabs.currentChanged.connect(lambda _i: self._sync_stats())
        self._build()

    # ---- layout ---------------------------------------------------------------------------------------------------------
    def _build(self) -> None:
        g = QWidget()
        f = QFormLayout(g)
        self.name = QLineEdit()
        self.ui_lang, self.agent_lang = QComboBox(), QComboBox()
        for c in (self.ui_lang, self.agent_lang):
            c.addItems(["auto", "vi", "en"])
        self.autostart = QCheckBox()
        self.avatar_on, self.on_top, self.wander = QCheckBox(), QCheckBox(), QCheckBox()
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.5, 3.0)
        self.scale.setSingleStep(0.1)
        self.skin = QComboBox()
        self.skin.addItems(available_skins())
        self.folder_btn = QPushButton()
        self.folder_btn.clicked.connect(self._add_folder)
        self.hotkeys = QLabel()
        self.rows_general = [("Agent name", "Tên agent", self.name), ("Interface language", "Ngôn ngữ giao diện", self.ui_lang),
                             ("Agent reply language", "Ngôn ngữ agent trả lời", self.agent_lang),
                             ("Start with Windows", "Khởi động cùng Windows", self.autostart), ("Show avatar", "Hiện avatar", self.avatar_on),
                             ("Avatar always on top", "Avatar luôn ở trên", self.on_top), ("Avatar wanders when idle", "Avatar đi dạo khi rảnh", self.wander),
                             ("Avatar size", "Kích thước avatar", self.scale), ("Skin", "Skin", self.skin),
                             ("Folders the agent may use", "Thư mục agent được dùng", self.folder_btn), ("Hotkeys", "Phím tắt", self.hotkeys)]
        self._general_form = f
        self._labels: list[tuple[QLabel, str, str]] = []
        for en, vi, w in self.rows_general:
            lbl = QLabel()
            self._labels.append((lbl, en, vi))
            f.addRow(lbl, w)
        self.tabs.addTab(g, "")

        m = QWidget()
        self.models_form = QFormLayout(m)
        self.model_rows: dict[str, tuple[QComboBox, QLineEdit, QPushButton, QLabel]] = {}
        self.tabs.addTab(m, "")

        k = QWidget()
        self.keys_form = QFormLayout(k)
        self.key_rows: dict[str, tuple[QLabel, QLineEdit]] = {}
        self.tabs.addTab(k, "")

        v = QWidget()
        vf = QFormLayout(v)
        self.stt_provider, self.stt_model = QComboBox(), QLineEdit()
        self.stt_provider.addItems(["google", "openai", "local"])
        self.tts_on = QCheckBox()
        self.voice_vi, self.voice_en, self.rate = QLineEdit(), QLineEdit(), QLineEdit()
        self.voice_rows = [("Speech recognition", "Nhận dạng giọng nói", self.stt_provider), ("Recognition model", "Mô hình nhận dạng", self.stt_model),
                           ("Read answers aloud", "Đọc câu trả lời", self.tts_on), ("Vietnamese voice", "Giọng tiếng Việt", self.voice_vi),
                           ("English voice", "Giọng tiếng Anh", self.voice_en), ("Speech rate (+10%, -20%)", "Tốc độ (+10%, -20%)", self.rate)]
        self._voice_labels: list[tuple[QLabel, str, str]] = []
        for en, vi, w in self.voice_rows:
            lbl = QLabel()
            self._voice_labels.append((lbl, en, vi))
            vf.addRow(lbl, w)
        self.tabs.addTab(v, "")

        b = QWidget()
        bf = QFormLayout(b)
        self.cap_tokens = QSpinBox()
        self.cap_tokens.setRange(10_000, 1_000_000_000)
        self.cap_tokens.setSingleStep(100_000)
        self.cap_cost = QDoubleSpinBox()
        self.cap_cost.setRange(0.01, 10_000)
        self.cap_cost.setDecimals(2)
        self.cap_labels = (QLabel(), QLabel())
        bf.addRow(self.cap_labels[0], self.cap_tokens)
        bf.addRow(self.cap_labels[1], self.cap_cost)
        self.tabs.addTab(b, "")

        sc = QWidget()
        scf = QFormLayout(sc)
        self.agent_view, self.agent_control = QCheckBox(), QCheckBox()
        self.block_titles, self.block_procs = QPlainTextEdit(), QPlainTextEdit()
        self.block_titles.setFixedHeight(90)
        self.block_procs.setFixedHeight(70)
        self.watch_interval, self.watch_threshold = QDoubleSpinBox(), QSpinBox()
        self.watch_interval.setRange(0.5, 60)
        self.watch_interval.setSingleStep(0.5)
        self.watch_threshold.setRange(1, 64)
        self.watch_minutes, self.max_actions = QSpinBox(), QSpinBox()
        self.watch_minutes.setRange(1, 480)
        self.max_actions.setRange(1, 20)
        self.screen_rows = [("Let the agent look at the screen", "Cho agent xem màn hình", self.agent_view),
                            ("Let the agent use mouse/keyboard (each use needs approval)", "Cho agent dùng chuột/bàn phím (mỗi lần cần duyệt)", self.agent_control),
                            ("Private window titles (one per line, never captured or controlled)", "Tiêu đề cửa sổ riêng tư (mỗi dòng một mục, không bao giờ chụp/điều khiển)", self.block_titles),
                            ("Private programs (exe names)", "Chương trình riêng tư (tên exe)", self.block_procs),
                            ("Watching: seconds between captures", "Theo dõi: số giây giữa các lần chụp", self.watch_interval),
                            ("Watching: change sensitivity (1-64, lower = more sensitive)", "Theo dõi: độ nhạy thay đổi (1-64, thấp = nhạy hơn)", self.watch_threshold),
                            ("Watching: switches itself off after (minutes)", "Theo dõi: tự tắt sau (phút)", self.watch_minutes),
                            ("Steps one approval may cover", "Số bước tối đa mỗi lần duyệt", self.max_actions)]
        self._screen_labels: list[tuple[QLabel, str, str]] = []
        for en, vi, w in self.screen_rows:
            lbl = QLabel()
            lbl.setWordWrap(True)
            self._screen_labels.append((lbl, en, vi))
            scf.addRow(lbl, w)
        self.tabs.addTab(sc, "")

        a = QWidget()
        af = QFormLayout(a)
        self.about_ui, self.about_daemon, self.about_lf = QLabel(), QLabel(), QLabel()
        self.about_labels = (QLabel(), QLabel(), QLabel())
        af.addRow(self.about_labels[0], self.about_ui)
        af.addRow(self.about_labels[1], self.about_daemon)
        af.addRow(self.about_labels[2], self.about_lf)
        self.tabs.addTab(a, "")
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("Settings", "Cài đặt"))
        for i, (en, vi) in enumerate((("General", "Chung"), ("Models", "Mô hình"), ("API keys", "Khóa API"), ("Voice", "Giọng nói"),
                                      ("Budgets", "Ngân sách"), ("Screen", "Màn hình"), ("About", "Giới thiệu"))):
            self.tabs.setTabText(i, tr(en, vi))
        for lbl, en, vi in self._labels + self._voice_labels + self._screen_labels:
            lbl.setText(tr(en, vi))
        self.folder_btn.setText(tr("Add a folder…", "Thêm thư mục…"))
        self.cap_labels[0].setText(tr("Daily token cap", "Giới hạn token mỗi ngày"))
        self.cap_labels[1].setText(tr("Daily cost cap (USD)", "Giới hạn chi phí mỗi ngày (USD)"))
        for lbl, (en, vi) in zip(self.about_labels, (("This window (UI)", "Cửa sổ này (UI)"), ("Daemon", "Daemon"), ("Langfuse", "Langfuse")), strict=True):
            lbl.setText(tr(en, vi))

    # ---- data in ---------------------------------------------------------------------------------------------------------
    def load(self, ev: dict[str, Any]) -> None:
        self.data = ev
        self.name.setText(ev["agent"]["name"])
        self.agent_lang.setCurrentText(ev["agent"]["language"])
        ui = ev["ui"]
        self.ui_lang.setCurrentText(ui["language"])
        self.avatar_on.setChecked(ui["avatar_enabled"])
        self.on_top.setChecked(ui["avatar_always_on_top"])
        self.wander.setChecked(ui["avatar_wander"])
        self.scale.setValue(ui["avatar_scale"])
        self.skin.setCurrentText(ui["skin"])
        self.autostart.setChecked(system.autostart_enabled())
        self.hotkeys.setText("   ".join(f"{k}: {v}" for k, v in ui["hotkeys"].items()))
        st, tts = ev["voice"]["stt"], ev["voice"]["tts"]
        self.stt_provider.setCurrentText(st["provider"])
        self.stt_model.setText(st["model"])
        self.tts_on.setChecked(tts["enabled"])
        self.voice_vi.setText(tts["voice_vi"])
        self.voice_en.setText(tts["voice_en"])
        self.rate.setText(tts["rate"])
        self.cap_tokens.setValue(int(ev["budgets"]["daily_max_tokens"]))
        self.cap_cost.setValue(float(ev["budgets"]["daily_max_cost_usd"]))
        self.about_lf.setText(tr("on", "bật") if ev["observability"]["langfuse_enabled"] and
                              ev["secrets"].get("LANGFUSE_PUBLIC_KEY") else tr("off (no keys or disabled)", "tắt (thiếu khóa hoặc bị tắt)"))
        sc = ev.get("screen")
        if sc:
            self.agent_view.setChecked(sc["agent_view"])
            self.agent_control.setChecked(sc["control"])
            self.block_titles.setPlainText(os.linesep.join(sc["block_titles"]))
            self.block_procs.setPlainText(os.linesep.join(sc["block_processes"]))
            self.watch_interval.setValue(sc["watch_interval_s"])
            self.watch_threshold.setValue(sc["watch_change_threshold"])
            self.watch_minutes.setValue(sc["watch_max_minutes"])
            self.max_actions.setValue(sc["max_actions"])
        self._load_models(ev)
        self._load_keys(ev["secrets"])

    def _load_models(self, ev: dict[str, Any]) -> None:
        for role, (prov, model, btn, res) in list(self.model_rows.items()):
            if role not in ev["models"]:
                for w in (prov, model, btn, res):
                    w.deleteLater()
                del self.model_rows[role]
        for role, ref in ev["models"].items():
            if role not in self.model_rows:
                prov, model, btn, res = QComboBox(), QLineEdit(), QPushButton(), QLabel()
                prov.addItems(ev["providers"])
                btn.clicked.connect(lambda _=False, r=role: self._test(r))
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                for w in (prov, model, btn, res):
                    h.addWidget(w)
                self.models_form.addRow(role, row)
                self.model_rows[role] = (prov, model, btn, res)
            prov, model, btn, res = self.model_rows[role]
            prov.setCurrentText(ref["provider"])
            model.setText(ref["model"])
            btn.setText(tr("Test", "Thử"))

    def _load_keys(self, secrets: dict[str, bool]) -> None:
        for name, ok in secrets.items():
            if name not in self.key_rows:
                lbl, edit = QLabel(), QLineEdit()
                edit.setEchoMode(QLineEdit.EchoMode.Password)
                edit.setPlaceholderText(tr("paste a new value to replace it", "dán giá trị mới để thay"))
                save = QPushButton(tr("Save key", "Lưu khóa"))
                save.setProperty("role", "primary")
                save.clicked.connect(lambda _=False, n=name, e=edit: self._save_key(n, e))
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(lbl)
                h.addWidget(edit, 1)
                h.addWidget(save)
                self.keys_form.addRow(KEY_HELP.get(name, name), row)
                self.key_rows[name] = (lbl, edit)
            self.key_rows[name][0].setText("✔ " + tr("set", "đã có") if ok else "✖ " + tr("not set", "chưa có"))

    # ---- actions ---------------------------------------------------------------------------------------------------------
    def _save_key(self, name: str, edit: QLineEdit) -> None:
        if edit.text().strip():
            self._send({"type": "secret_set", "name": name, "value": edit.text()})
            edit.clear()  # the value leaves the UI as soon as it is sent

    def _test(self, role: str) -> None:
        self.model_rows[role][3].setText("…")
        self._send({"type": "test_role", "role": role})

    def on_test_result(self, ev: dict[str, Any]) -> None:
        row = self.model_rows.get(str(ev.get("role")))
        if row:
            row[3].setText("✔" if ev.get("ok") else f"✖ {ev.get('code')}")
            row[3].setToolTip(str(ev.get("message", "")))

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Choose a folder the agent may read and write", "Chọn thư mục agent được đọc/ghi"),
                                             str(Path.home() / "Documents"))
        if d:
            self._send({"type": "policy_add_path", "path": os.path.normpath(d)})

    def save(self) -> None:
        if not self.data:
            return
        d, patch = self.data, {}
        models: dict[str, Any] = {}
        for role, (prov, model, _b, _r) in self.model_rows.items():
            cur = d["models"][role]
            ch = {k: v for k, v in (("provider", prov.currentText()), ("model", model.text().strip())) if v != cur[k]}
            if ch:
                models[role] = ch
        if models:
            patch["models"] = models
        if self.agent_lang.currentText() != d["agent"]["language"]:
            patch["agent"] = {"language": self.agent_lang.currentText()}
        ui_new: dict[str, Any] = {"language": self.ui_lang.currentText(), "avatar_enabled": self.avatar_on.isChecked(),
                  "avatar_always_on_top": self.on_top.isChecked(), "avatar_wander": self.wander.isChecked(),
                  "avatar_scale": round(self.scale.value(), 2), "skin": self.skin.currentText()}
        if ch := {k: v for k, v in ui_new.items() if v != d["ui"][k]}:
            patch["ui"] = ch
        stt, tts = d["voice"]["stt"], d["voice"]["tts"]
        voice: dict[str, Any] = {}
        if s := {k: v for k, v in (("provider", self.stt_provider.currentText()), ("model", self.stt_model.text().strip())) if v != stt[k]}:
            voice["stt"] = s
        if t := {k: v for k, v in (("enabled", self.tts_on.isChecked()), ("voice_vi", self.voice_vi.text().strip()),
                                    ("voice_en", self.voice_en.text().strip()), ("rate", self.rate.text().strip())) if v != tts[k]}:
            voice["tts"] = t
        if voice:
            patch["voice"] = voice
        caps = {k: v for k, v in (("daily_max_tokens", self.cap_tokens.value()), ("daily_max_cost_usd", round(self.cap_cost.value(), 2)))
                if v != d["budgets"][k]}
        if caps:
            patch["budgets"] = caps
        if "screen" in d:
            def lines(w: QPlainTextEdit) -> list[str]:
                return [ln.strip() for ln in w.toPlainText().splitlines() if ln.strip()]

            new_screen: dict[str, Any] = {"agent_view": self.agent_view.isChecked(), "control": self.agent_control.isChecked(),
                          "block_titles": lines(self.block_titles), "block_processes": lines(self.block_procs),
                          "watch_interval_s": round(self.watch_interval.value(), 1), "watch_change_threshold": self.watch_threshold.value(),
                          "watch_max_minutes": self.watch_minutes.value(), "max_actions": self.max_actions.value()}
            if ch := {k: v for k, v in new_screen.items() if v != d["screen"][k]}:
                patch["screen"] = ch
        try:
            if self.autostart.isChecked() != system.autostart_enabled():
                system.set_autostart(self.autostart.isChecked())
        except OSError as e:
            self.status.setText(str(e))
        if self.name.text().strip() != d["agent"]["name"]:
            self._send({"type": "set_agent_name", "name": self.name.text().strip()})
        if patch:
            self._send({"type": "settings_set", "patch": patch})
            self.status.setText(tr("Saved.", "Đã lưu."))
        self.ui_changed.emit()

    # ---- About: live CPU / RAM, polled only while that tab is visible -------------------------------------------------------
    def showEvent(self, e: object) -> None:
        super().showEvent(e)  # type: ignore[arg-type]
        self._sync_stats()

    def hideEvent(self, e: object) -> None:
        super().hideEvent(e)  # type: ignore[arg-type]
        self._stats.stop()

    def _sync_stats(self) -> None:
        if self.isVisible() and self.tabs.currentIndex() == self.tabs.count() - 1:
            self._refresh_stats()
            self._stats.start(2000)
        else:
            self._stats.stop()

    def _refresh_stats(self) -> None:
        for label, pid in ((self.about_ui, os.getpid()), (self.about_daemon, self._pid())):
            s = system.process_stats(pid)
            label.setText(f"CPU {s['cpu']:.1f}% · RAM {s['rss_mb']:.0f} MB" if s else tr("n/a", "không có"))


class FirstRunWizard(QWizard):
    """Language -> provider key (+ connection test) -> workspace folder. Everything goes through the daemon API."""

    language_chosen = Signal(str)

    def __init__(self, send: Send) -> None:
        super().__init__()
        self._send = send
        self.models: dict[str, Any] = {}
        self.setWindowTitle(tr("Welcome", "Chào mừng"))
        p1, p2, p3 = QWizardPage(), QWizardPage(), QWizardPage()
        l1 = QVBoxLayout(p1)
        p1.setTitle(tr("Language", "Ngôn ngữ"))
        self.lang = QComboBox()
        self.lang.addItem("Tiếng Việt", "vi")
        self.lang.addItem("English", "en")
        self.lang.currentIndexChanged.connect(lambda _i: self.language_chosen.emit(self.lang.currentData()))
        l1.addWidget(QLabel(tr("Choose the interface language.", "Chọn ngôn ngữ giao diện.")))
        l1.addWidget(self.lang)
        l2 = QVBoxLayout(p2)
        p2.setTitle(tr("Connect a model provider", "Kết nối nhà cung cấp mô hình"))
        self.provider = QComboBox()
        self.provider.addItems(["anthropic", "google", "openai", "openrouter"])
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.test_btn, self.test_result = QPushButton(tr("Save key and test", "Lưu khóa và thử")), QLabel()
        self.test_btn.setProperty("role", "primary")
        self.test_btn.clicked.connect(self._save_and_test)
        for w in (QLabel(tr("The key is stored in Windows Credential Manager, never in a file.", "Khóa được lưu trong Windows Credential Manager, không lưu vào tệp.")),
                  self.provider, self.key, self.test_btn, self.test_result):
            l2.addWidget(w)
        l3 = QVBoxLayout(p3)
        p3.setTitle(tr("Workspace", "Thư mục làm việc"))
        self.folder_label = QLabel(tr("The agent may only touch the folders you allow. Default: Documents\\agent-workspace.",
                                      "Agent chỉ được đụng vào thư mục bạn cho phép. Mặc định: Documents\\agent-workspace."))
        self.folder_label.setWordWrap(True)
        self.folder_btn = QPushButton(tr("Add a folder…", "Thêm thư mục…"))
        self.folder_btn.clicked.connect(self._folder)
        self.folder_result = QLabel()
        for w in (self.folder_label, self.folder_btn, self.folder_result):
            l3.addWidget(w)
        for p in (p1, p2, p3):
            self.addPage(p)

    def _save_and_test(self) -> None:
        names = {"anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY", "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
        if self.key.text().strip():
            self._send({"type": "secret_set", "name": names[self.provider.currentText()], "value": self.key.text()})
            self.key.clear()
        role = next((r for r, m in self.models.items() if m["provider"] == self.provider.currentText()), None)
        if role is None:
            self.test_result.setText(tr("Key saved. No model uses this provider yet; pick it under Settings -> Models.",
                                   "Đã lưu khóa. Chưa có mô hình nào dùng nhà cung cấp này; chọn trong Cài đặt -> Mô hình."))
            return
        self.test_result.setText("…")
        self._send({"type": "test_role", "role": role})

    def on_test_result(self, ev: dict[str, Any]) -> None:
        self.test_result.setText("✔ " + tr("Connected", "Kết nối được") if ev.get("ok") else f"✖ {ev.get('code')}: {ev.get('message')}")

    def _folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Choose a folder", "Chọn thư mục"), str(Path.home() / "Documents"))
        if d:
            self._send({"type": "policy_add_path", "path": os.path.normpath(d)})
            self.folder_result.setText(f"✔ {d}")

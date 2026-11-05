"""Windows rendered offscreen: skins, avatar, chat, approval center, manager pages, settings dialog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDropEvent, QImage, QPixmap, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QPushButton

from conftest import pump
from personal_agent.ui.approvals import ApprovalCenter, ThreadBox
from personal_agent.ui.avatar import AvatarWindow, SpeechBubble, snap_to_edges
from personal_agent.ui.avatar_state import STATES
from personal_agent.ui.chat import ChatWindow, QuickInput, render_markdown
from personal_agent.ui.i18n import set_language
from personal_agent.ui.main_window import MainWindow
from personal_agent.ui.model import Session
from personal_agent.ui.pages import ManagerWindow
from personal_agent.ui.settings_dialog import FirstRunWizard, SettingsDialog
from personal_agent.ui.skin import Skin, draw_builtin, load_skin


@pytest.fixture(autouse=True)
def english() -> None:
    set_language("en")


def png(path: Path, color: str = "#ff0000", size: int = 32) -> None:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    assert pm.save(str(path))


# ---- skins ----------------------------------------------------------------------------------------------------------------------
def test_builtin_skin_draws_every_state_with_visible_pixels(qapp: QApplication) -> None:
    skin = Skin.default()
    for st in STATES:
        anim = skin.animation(st)
        assert len(anim.frames) > 1 and anim.fps > 0
        img = anim.frames[0].toImage()
        assert any(img.pixelColor(x, y).alpha() > 0 for x in range(0, img.width(), 8) for y in range(0, img.height(), 8))
    assert draw_builtin("sleeping", 0).toImage() != draw_builtin("error", 0).toImage()


def test_custom_skin_loads_frames_falls_back_and_reports_problems(qapp: QApplication, tmp_path: Path) -> None:
    for i in range(3):
        png(tmp_path / f"idle{i}.png", size=48)
    png(tmp_path / "ok.png")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "name": "mine", "size": [48, 48], "fallback": "idle",
        "states": {"idle": {"frames": ["idle0.png", "idle1.png", "idle2.png"], "fps": 5},
                   "success": {"frames": ["ok.png"], "fps": 1, "loop": False},
                   "thinking": {"frames": ["missing.png"]},                      # broken: reported, falls back
                   "error": {"frames": ["../outside.png"]},                     # path traversal: refused
                   "working": {"frames": ["ok.png"], "fps": 999},               # bad fps: reported
                   "dancing": {"frames": ["ok.png"]},                           # unknown state: reported
                   "sleeping": None}}), encoding="utf-8")
    skin = Skin.load(tmp_path)
    assert len(skin.animation("idle").frames) == 3 and skin.animation("success").loop is False
    assert skin.animation("thinking") is skin.animation("idle")  # missing state -> fallback state
    problems = " | ".join(skin.problems)
    assert "thinking" in problems and "outside the skin folder" in problems and "fps" in problems and "dancing" in problems


def test_a_skin_with_no_manifest_or_no_usable_animation_never_breaks_the_avatar(qapp: QApplication, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Skin.load(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({"states": {"idle": {"frames": ["nope.png"]}}}), encoding="utf-8")
    skin = Skin.load(tmp_path)
    assert skin.problems and len(skin.animation("thinking").frames) > 1  # built-in look is the last fallback
    assert load_skin("does-not-exist").name == "default"


# ---- avatar ------------------------------------------------------------------------------------------------------------------------
def test_snap_to_edges_and_stays_on_screen(qapp: QApplication) -> None:
    from PySide6.QtCore import QRect

    area = QRect(0, 0, 1920, 1040)
    assert snap_to_edges(QRect(10, 500, 100, 100), area) == QPoint(0, 500)
    assert snap_to_edges(QRect(1815, 935, 100, 100), area) == QPoint(1820, 940)
    assert snap_to_edges(QRect(500, 500, 100, 100), area) == QPoint(500, 500)
    assert snap_to_edges(QRect(-80, -80, 100, 100), area) == QPoint(0, 0)  # dragged off screen -> pulled back


def test_avatar_animates_only_while_visible_and_follows_state(qapp: QApplication) -> None:
    a = AvatarWindow(Skin.default(), 1.0)
    assert not a._timer.isActive()  # hidden: no timer at all
    a.show()
    assert a._timer.isActive() and a.state == "idle"
    a.set_state("working")
    assert a.state == "working" and a._timer.interval() == int(1000 / a._anim.fps)
    a.hide()
    assert not a._timer.isActive() and not a._wander_timer.isActive()
    a.show()
    a.set_scale(2.0)
    assert a.width() == 256 and not a.mask().isEmpty()  # per-pixel click-through mask is applied
    a.set_scale(99)
    assert a.width() == 384  # clamped to 3x


def test_a_one_frame_skin_runs_no_timer_and_one_shot_stops_on_its_last_frame(qapp: QApplication, tmp_path: Path) -> None:
    for n in ("a", "b"):
        png(tmp_path / f"{n}.png", size=64)
    (tmp_path / "manifest.json").write_text(json.dumps({"size": [64, 64], "states": {
        "idle": {"frames": ["a.png"]}, "success": {"frames": ["a.png", "b.png"], "fps": 30, "loop": False}}}), encoding="utf-8")
    a = AvatarWindow(Skin.load(tmp_path))
    a.show()
    assert not a._timer.isActive()
    a.set_state("success")
    pump(200)
    assert a._frame == 1 and not a._timer.isActive()


def test_click_vs_double_click_and_drag(qapp: QApplication) -> None:
    a = AvatarWindow(Skin.default())
    a.show()
    a.move(100, 100)
    clicks, doubles, moves = [], [], []
    a.single_clicked.connect(lambda: clicks.append(1))
    a.double_clicked.connect(lambda: doubles.append(1))
    a.moved.connect(lambda p: moves.append(p))
    c = QPoint(a.width() // 2, a.height() // 2)
    QTest.mouseClick(a, Qt.MouseButton.LeftButton, pos=c)
    pump(QApplication.doubleClickInterval() + 80)
    assert clicks == [1] and not doubles
    QTest.mouseDClick(a, Qt.MouseButton.LeftButton, pos=c)
    pump(QApplication.doubleClickInterval() + 80)
    assert doubles == [1] and clicks == [1]  # a double click does not also count as a single click
    QTest.mousePress(a, Qt.MouseButton.LeftButton, pos=c)
    QTest.mouseMove(a, c + QPoint(60, 30))
    QTest.mouseRelease(a, Qt.MouseButton.LeftButton, pos=c + QPoint(60, 30))
    pump(50)
    assert moves and clicks == [1]  # dragging is not a click


def test_dragging_the_avatar_closes_an_empty_quick_box_but_keeps_one_with_text(qapp: QApplication) -> None:
    a, q = AvatarWindow(Skin.default()), QuickInput()
    a.drag_started.connect(q.close_if_empty)
    a.show()
    a.move(100, 100)
    c = QPoint(a.width() // 2, a.height() // 2)

    def drag() -> None:
        QTest.mousePress(a, Qt.MouseButton.LeftButton, pos=c)
        QTest.mouseMove(a, c + QPoint(60, 30))
        QTest.mouseRelease(a, Qt.MouseButton.LeftButton, pos=c + QPoint(60, 30))

    q.open_at(a)
    assert q.isVisible()
    drag()
    assert not q.isVisible()  # opened by a click, then the avatar was dragged without typing: gone at once
    q.open_at(a)
    q.edit.setPlainText("half a thought")
    drag()
    assert q.isVisible()  # a draft is never thrown away
    q.hide()


def test_dropping_files_on_the_avatar_emits_their_paths(qapp: QApplication, tmp_path: Path) -> None:
    a = AvatarWindow(Skin.default())
    a.show()
    got: list[Any] = []
    a.files_dropped.connect(got.append)
    f = tmp_path / "doc.txt"
    f.write_text("x")
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(f)), QUrl("https://example.com/x")])
    ev = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    a.dropEvent(ev)
    assert len(got) == 1 and [Path(p) for p in got[0]] == [f]  # remote URLs are ignored


def test_speech_bubble_shows_and_hides(qapp: QApplication) -> None:
    a = AvatarWindow(Skin.default())
    a.show()
    b = SpeechBubble()
    b.say("hello", a, ms=60)
    assert b.isVisible()
    pump(200)
    assert not b.isVisible()
    b.say("approve?", a, sticky=True)
    pump(100)
    assert b.isVisible()


# ---- chat -----------------------------------------------------------------------------------------------------------------------------
def test_chat_renders_markdown_sends_with_enter_and_blocks_while_approvals_wait(qapp: QApplication) -> None:
    s = Session("t", "Mimi")
    w = ChatWindow(s)
    sent: list[Any] = []
    w.submitted.connect(lambda t, f: sent.append((t, f)))
    s.user_says("hello")
    s.apply({"type": "final", "text": "# Title\n\n**bold** text", "used": {"steps": 1, "tokens": 5, "cost": 0.0001}})
    w.refresh()
    md = render_markdown(s)
    assert "**Mimi**" in md and "hello" in md
    assert "Title" in w.browser.toPlainText() and "bold text" in w.browser.toPlainText()
    w.composer.setPlainText("second")
    QTest.keyClick(w.composer, Qt.Key.Key_Return)
    assert sent == [("second", [])] and w.composer.toPlainText() == ""
    w.composer.setPlainText("a")
    QTest.keyClick(w.composer, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)  # Shift+Enter = new line
    assert len(sent) == 1 and "\n" in w.composer.toPlainText()
    s.apply({"type": "approvals", "items": [{"call_id": "c", "thread_id": "t", "tool": "shell", "args": "{}"}]})
    w.refresh({"approvals", "busy"})
    w.composer.setPlainText("blocked")
    QTest.keyClick(w.composer, Qt.Key.Key_Return)
    assert len(sent) == 1 and "approval" in w.status.text().lower()


def test_chat_attachments_from_files_and_pasted_images(qapp: QApplication, tmp_path: Path) -> None:
    w = ChatWindow(Session())
    sent: list[Any] = []
    w.submitted.connect(lambda t, f: sent.append((t, f)))
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * 5_000_001)
    w.add_files([str(f), str(big)])
    assert "too large" not in w.status.text() or "5 MB" in w.status.text()
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(QColor("#00ff00"))
    w.composer.image_pasted.emit(img)
    assert [a["name"] for a in w._files] == ["notes.txt", "pasted-2.png"] and "notes.txt" in w.chips.text()
    QTest.mouseClick(w.send_btn, Qt.MouseButton.LeftButton)
    text, files = sent[0]
    assert text == "" and files[1]["mime"] == "image/png" and files[0]["mime"] == "text/plain"
    assert w.chips.text() == ""


def test_chat_refuses_more_than_four_files_or_fifteen_megabytes(qapp: QApplication, tmp_path: Path) -> None:
    w = ChatWindow(Session())
    files = []
    for i in range(3):
        f = tmp_path / f"big{i}.bin"
        f.write_bytes(b"0" * 4_900_000)
        files.append(str(f))
    w.add_files(files)
    assert len(w._files) == 3 and "4 files" not in w.status.text()
    extra = tmp_path / "more.bin"
    extra.write_bytes(b"0" * 1_000_000)
    w.add_files([str(extra)])  # 14.7 MB + 1 MB > 15 MB
    assert len(w._files) == 3 and "15 MB" in w.status.text()


def test_closing_the_chat_window_only_hides_it(qapp: QApplication) -> None:
    w = ChatWindow(Session())
    w.show()
    w.close()
    assert not w.isVisible()


def test_quick_input_sends_text_and_dropped_files(qapp: QApplication, tmp_path: Path) -> None:
    a = AvatarWindow(Skin.default())
    a.show()
    q = QuickInput()
    got: list[Any] = []
    q.submitted.connect(lambda t, f: got.append((t, [x["name"] for x in f])))
    f = tmp_path / "a.md"
    f.write_text("# x")
    q.open_at(a, [str(f)])
    q.edit.setPlainText("summarise")
    QTest.keyClick(q.edit, Qt.Key.Key_Return)
    assert got == [("summarise", ["a.md"])] and not q.isVisible()
    q.open_at(a)
    QTest.keyClick(q, Qt.Key.Key_Escape)
    assert not q.isVisible()


# ---- approval center ------------------------------------------------------------------------------------------------------------------------
def approval(**kw: Any) -> dict[str, Any]:
    return {"call_id": "c1", "thread_id": "t1", "tool": "file_write", "tier": "T2", "risk": "medium", "reason": "needs your approval",
            "args": json.dumps({"path": "a.txt", "content": "x"}), "preview": "--- before\n+++ after\n@@ -1 +1 @@\n-old\n+new", **kw}


def test_approval_center_builds_decisions_from_the_cards(qapp: QApplication) -> None:
    s = Session()
    s.apply({"type": "approvals", "items": [approval(), approval(call_id="c2", tool="shell", tainted=1)]})
    ac = ApprovalCenter(s)
    got: list[Any] = []
    ac.decided.connect(lambda tid, d: got.append((tid, d)))
    box = ac.widget().findChild(ThreadBox)
    cards = box.cards
    assert "(2)" in ac.windowTitle() and cards[1].a["tainted"]
    box.submit.click()
    assert "choose an answer" in box.error.text() and not got  # nothing chosen yet
    cards[0].radios["remember"].setChecked(True)
    cards[1].radios["reject"].setChecked(True)
    cards[1].reason.setText("not now")
    box.submit.click()
    assert got == [("t1", {"c1": {"action": "approve", "remember": True}, "c2": {"action": "reject", "feedback": "not now"}})]
    assert not box.submit.isEnabled()  # one answer per request


def test_editing_the_arguments_approves_the_edited_call_and_t3_needs_confirm(qapp: QApplication) -> None:
    s = Session()
    s.apply({"type": "approvals", "items": [approval(tier="T3", risk="critical")]})
    ac = ApprovalCenter(s)
    got: list[Any] = []
    ac.decided.connect(lambda tid, d: got.append(d))
    box = ac.widget().findChild(ThreadBox)
    card = box.cards[0]
    card.radios["approve"].setChecked(True)
    box.submit.click()
    assert "CONFIRM" in box.error.text() and not got
    card.confirm.setText("CONFIRM")
    card.args.setPlainText(json.dumps({"path": "b.txt", "content": "y"}))
    box.submit.click()
    assert got == [{"c1": {"action": "edit", "args": {"path": "b.txt", "content": "y"}}}]


def test_approval_center_keeps_half_filled_answers_on_unrelated_refresh(qapp: QApplication) -> None:
    s = Session()
    s.apply({"type": "approvals", "items": [approval()]})
    ac = ApprovalCenter(s)
    ac.widget().findChild(ThreadBox).cards[0].reason.setText("typing…")
    ac.refresh()  # same pending set
    assert ac.widget().findChild(ThreadBox).cards[0].reason.text() == "typing…"
    s.apply({"type": "approvals", "items": []})
    ac.refresh()
    assert ac.widget().findChild(ThreadBox) is None and "(0)" in ac.windowTitle()


# ---- manager pages ------------------------------------------------------------------------------------------------------------------------
def bare(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in m.items() if k != "rid"} for m in msgs]


def confirm(monkeypatch: Any, yes: bool = True) -> list[str]:
    """Answer the yes/no boxes without showing them; returns the texts that were asked."""
    asked: list[str] = []

    def q(_p: Any, _title: str, text: str, *_a: Any) -> Any:
        asked.append(text)
        return QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", q)
    return asked


def mem_page(m: ManagerWindow, items: list[dict[str, Any]]) -> Any:
    m.on_event({"type": "memory", "items": items})
    return m.pages["memory"][0]


def row_btns(page: Any, row: int) -> list[QPushButton]:
    return page.table.cellWidget(row, 4).findChildren(QPushButton)


ITEMS = [{"id": 4, "kind": "fact", "status": "active", "text": "likes tea"},
         {"id": 5, "kind": "pref", "status": "proposed", "text": "my password is hunter2", "sensitive": 1},
         {"id": 6, "kind": "fact", "status": "active", "text": "lives in Hanoi"}]


def test_manager_pages_fill_from_events_and_send_row_actions(qapp: QApplication, monkeypatch: Any) -> None:
    asked = confirm(monkeypatch)
    sent: list[dict[str, Any]] = []
    m = ManagerWindow(sent.append)
    page = mem_page(m, [{"id": 4, "kind": "fact", "status": "proposed", "text": "likes tea", "sensitive": True}])
    assert page.table.rowCount() == 1 and page.table.item(0, 2).text().endswith("!")
    row_btns(page, 0)[1].click()
    assert len(asked) == 1 and "sensitive" in asked[0]  # approving a sensitive memory asks first
    mem_page(m, page.rows)  # a finished action rebuilds the row
    row_btns(page, 0)[2].click()
    assert len(asked) == 2 and "likes tea" in asked[1]  # deleting asks first and shows what goes
    assert bare(sent) == [{"type": "memory_update", "id": 4, "status": "active"}, {"type": "memory_delete", "id": 4}]
    sent.clear()
    m.on_event({"type": "memory_deleted", "ok": True})  # a change re-requests the list
    assert bare(sent) == [{"type": "memory_list"}]
    m.on_event({"type": "mcp", "servers": {"fs": {"enabled": True, "status": "ok"}}})
    assert m.pages["mcp"][0].table.item(0, 0).text() == "fs"
    m.on_event({"type": "usage", "today": {"tokens": 10, "cost_usd": 0.5, "calls": 2}, "caps": {"tokens": 100, "cost_usd": 1.0},
                "langfuse": False, "report": {"days": [{"day": "2026-11-01", "tokens": 10, "cost_usd": 0.5, "llm_calls": 2, "llm_error_rate": 0.0,
                                                        "llm_avg_ms": 120, "tool_calls": 1, "tool_success_rate": None}],
                                              "models": [{"model": "m", "calls": 2, "errors": 0, "avg_ms": 120}], "tools": []}})
    u = m.usage
    assert u.days.item(0, 4).text() == "0%" and u.days.item(0, 7).text() == "-" and u.cost.value() == 500
    m.on_event({"type": "policy", "text": "default_tier: T3\n", "saved": True})
    assert m.policy.editor.toPlainText().startswith("default_tier") and m.policy.status.text() == "Saved."
    sent.clear()
    m.policy.save_btn.click()
    assert sent[0]["type"] == "policy_set" and sent[0]["text"].startswith("default_tier")


def test_declining_the_confirmation_sends_nothing_and_plain_approve_does_not_ask(qapp: QApplication, monkeypatch: Any) -> None:
    asked = confirm(monkeypatch, yes=False)
    sent: list[dict[str, Any]] = []
    m = ManagerWindow(sent.append)
    page = mem_page(m, ITEMS)
    row_btns(page, 1)[1].click()  # approve the sensitive one
    row_btns(page, 0)[2].click()  # delete
    assert sent == [] and len(asked) == 2 and all(b.isEnabled() for b in row_btns(page, 0) + row_btns(page, 1))
    row_btns(page, 2)[1].click()  # approve a non-sensitive one: no extra question
    assert len(asked) == 2 and bare(sent) == [{"type": "memory_update", "id": 6, "status": "active"}]


def test_a_row_stays_locked_until_its_own_reply_and_survives_filtering(qapp: QApplication, monkeypatch: Any) -> None:
    confirm(monkeypatch)
    sent: list[dict[str, Any]] = []
    m = ManagerWindow(sent.append)
    page = mem_page(m, ITEMS)
    btns = row_btns(page, 0)
    btns[2].click()
    rid = sent[0]["rid"]
    assert bare(sent) == [{"type": "memory_delete", "id": 4}] and not any(b.isEnabled() for b in row_btns(page, 0))
    assert row_btns(page, 0)[2].text() == "Working…"
    assert all(b.isEnabled() for b in row_btns(page, 1))  # other rows stay usable
    m.on_event({"type": "error", "message": "unrelated"})  # no request id: not ours, nothing is unlocked
    page.search.setText("zzz")
    page.search.setText("")  # filtering rebuilds the rows; the pending one is still locked
    assert not any(b.isEnabled() for b in row_btns(page, 0)) and row_btns(page, 0)[2].text() == "Working…"
    page._timeout(rid)  # no answer in time: ask for the truth, do not let a second click repeat it
    assert sent[-1]["type"] == "memory_list" and not any(b.isEnabled() for b in row_btns(page, 0))
    m.on_event({"type": "error", "rid": rid, "message": "boom"})  # the daemon refused exactly this request
    assert all(b.isEnabled() for b in row_btns(page, 0)) and row_btns(page, 0)[2].text() == "Delete"
    row_btns(page, 0)[2].click()
    mem_page(m, ITEMS[1:])  # a new list is the truth of the daemon and rebuilds the table
    assert page.table.rowCount() == 2 and all(b.isEnabled() for b in row_btns(page, 0))


def test_a_stale_list_reply_is_dropped(qapp: QApplication, monkeypatch: Any) -> None:
    confirm(monkeypatch)
    sent: list[dict[str, Any]] = []
    m = ManagerWindow(sent.append)
    page = mem_page(m, ITEMS)
    page.refresh()
    old = sent[-1]["rid"]
    row_btns(page, 0)[2].click()  # an action supersedes the list request still in flight
    m.on_event({"type": "memory", "items": ITEMS, "rid": old})
    assert page.table.rowCount() == 3 and not row_btns(page, 0)[2].isEnabled()  # ignored: it would bring the deleted row back
    m.on_event({"type": "memory_deleted", "ok": True, "id": 4, "rid": sent[-1]["rid"]})
    fresh = sent[-1]["rid"]
    assert sent[-1]["type"] == "memory_list" and fresh > old
    m.on_event({"type": "memory", "items": ITEMS[1:], "rid": fresh})
    assert page.table.rowCount() == 2


def test_results_are_reported_after_the_list_reloads(qapp: QApplication) -> None:
    sent: list[dict[str, Any]] = []
    m = ManagerWindow(sent.append)
    page = mem_page(m, ITEMS)

    def settle(ev: dict[str, Any]) -> str:
        m.on_event(ev)
        m.on_event({"type": "memory", "items": ITEMS, "rid": sent[-1]["rid"]})
        return m.status.text()
    assert settle({"type": "memory_deleted", "ok": True}) == "Deleted."
    assert settle({"type": "memory_deleted", "ok": False}).startswith("!")
    assert settle({"type": "memory_updated", "ok": True, "status": "active"}) == "Saved."
    assert "approval" in settle({"type": "memory_updated", "ok": True, "status": "proposed"})
    assert settle({"type": "memory_updated", "ok": False}).startswith("!")
    assert page.table.rowCount() == 3


def test_memory_table_searches_filters_sorts_and_explains_when_empty(qapp: QApplication) -> None:
    m = ManagerWindow(lambda _p: None)
    page = m.pages["memory"][0]
    assert not page.empty_label.isHidden() and "No memories yet" in page.empty_label.text() and page.table.isHidden()
    mem_page(m, ITEMS)
    assert page.empty_label.isHidden() and page.table.rowCount() == 3
    assert [page.facet_box.itemText(i) for i in range(3)] == ["All", "active", "proposed"]
    page.search.setText("TEA")
    assert page.table.rowCount() == 1 and page.table.item(0, 3).text() == "likes tea"
    page.search.setText("nothing like this")
    assert page.table.rowCount() == 0 and page.empty_label.text() == "No matches."
    page.search.setText("")
    page.facet_box.setCurrentIndex(page.facet_box.findData("proposed"))
    assert page.table.rowCount() == 1 and page.table.item(0, 0).text() == "5"
    mem_page(m, ITEMS)  # a reload keeps the chosen filter
    assert page.facet_box.currentData() == "proposed" and page.table.rowCount() == 1
    page.facet_box.setCurrentIndex(0)
    page.table.horizontalHeader().sectionClicked.emit(0)
    page.table.horizontalHeader().sectionClicked.emit(0)  # second click: descending
    assert [page.table.item(r, 0).text() for r in range(3)] == ["6", "5", "4"]
    page.table.horizontalHeader().sectionClicked.emit(3)  # sorts by text
    assert [page.table.item(r, 3).text() for r in range(3)] == ["likes tea", "lives in Hanoi", "my password is hunter2"]


def test_memory_edit_sends_the_new_text_and_ignores_cancel_or_no_change(qapp: QApplication, monkeypatch: Any) -> None:
    sent: list[dict[str, Any]] = []
    m = ManagerWindow(sent.append)
    m.on_event({"type": "memory", "items": [{"id": 4, "kind": "fact", "status": "active", "text": "likes tea"}]})
    edit = m.pages["memory"][0].table.cellWidget(0, 4).findChildren(QPushButton)[0]
    for answer in ((" likes green tea ", True), ("likes tea", True), ("other", False), ("   ", True), ("x" * 2001, True)):
        monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *_a, answer=answer: answer)
        monkeypatch.setattr(QMessageBox, "warning", lambda *_a: None)
        edit.click()
    assert bare(sent) == [{"type": "memory_update", "id": 4, "text": "likes green tea"}]


def test_language_switch_retranslates_the_manager(qapp: QApplication) -> None:
    w = MainWindow(ChatWindow(Session()), ManagerWindow(lambda _p: None), SettingsDialog(lambda _m: None, lambda: None), "Mimi")
    w.open("memory")
    set_language("vi")
    w.retranslate()
    w.manager.retranslate()
    assert w.rail.buttons["memory"].text() == "Bộ nhớ" and w.page_title.text() == "Bộ nhớ" and w.windowTitle() == "Mimi"


# ---- settings dialog / wizard ---------------------------------------------------------------------------------------------------------------
def settings_event() -> dict[str, Any]:
    return {"type": "settings", "agent": {"name": "Mimi", "language": "auto"}, "providers": ["anthropic", "google", "ollama"],
            "models": {"fast": {"provider": "google", "model": "gemini-3.5-flash-lite"}, "planner": {"provider": "anthropic", "model": "claude-haiku-5-5"}},
            "budgets": {"daily_max_tokens": 1_000_000, "daily_max_cost_usd": 2.0, "max_cost_usd": 0.5, "max_tokens": 150000},
            "voice": {"stt": {"provider": "google", "model": "g", "max_seconds": 120},
                      "tts": {"enabled": False, "voice_vi": "vi", "voice_en": "en", "rate": "+0%", "max_chars": 600}},
            "ui": {"language": "auto", "avatar_enabled": True, "skin": "default", "avatar_scale": 1.0, "avatar_always_on_top": True,
                   "avatar_wander": False, "sleep_after_s": 300, "hotkeys": {"kill": "ctrl+alt+k"}},
            "rag_embedding": {}, "observability": {"langfuse_enabled": True},
            "secrets": {"ANTHROPIC_API_KEY": True, "GOOGLE_API_KEY": False, "LANGFUSE_PUBLIC_KEY": False}}


def test_settings_dialog_sends_only_what_changed_and_never_shows_key_values(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr("personal_agent.ui.system.autostart_enabled", lambda *a, **k: False)
    d = SettingsDialog(sent.append, lambda: None)
    d.load(settings_event())
    d.save()
    assert sent == []  # nothing changed -> nothing sent
    d.model_rows["fast"][1].setText("gemini-3.5-flash")
    d.name.setText("Bé Na")
    d.scale.setValue(1.5)
    d.tts_on.setChecked(True)
    d.cap_cost.setValue(3.0)
    d.save()
    assert {"type": "set_agent_name", "name": "Bé Na"} in sent
    patch = next(m for m in sent if m["type"] == "settings_set")["patch"]
    assert patch == {"models": {"fast": {"model": "gemini-3.5-flash"}}, "ui": {"avatar_scale": 1.5},
                     "voice": {"tts": {"enabled": True}}, "budgets": {"daily_max_cost_usd": 3.0}}
    sent.clear()
    lbl, edit = d.key_rows["GOOGLE_API_KEY"]
    assert "not set" in lbl.text() and d.key_rows["ANTHROPIC_API_KEY"][0].text().startswith("✔")
    edit.setText("AIza-secret-value")
    edit.parent().findChildren(QPushButton)[0].click()
    assert sent == [{"type": "secret_set", "name": "GOOGLE_API_KEY", "value": "AIza-secret-value"}] and edit.text() == ""
    d.model_rows["planner"][2].click()
    assert sent[-1] == {"type": "test_role", "role": "planner"}
    d.on_test_result({"role": "planner", "ok": False, "code": "no_api_key", "message": "m"})
    assert "no_api_key" in d.model_rows["planner"][3].text()


def test_settings_about_tab_polls_only_while_visible(qapp: QApplication) -> None:
    d = SettingsDialog(lambda _m: None, lambda: None)
    d.show()
    assert not d._stats.isActive()  # General tab is showing
    d.tabs.setCurrentIndex(d.tabs.count() - 1)
    assert d._stats.isActive() and "RAM" in d.about_ui.text()
    d.tabs.setCurrentIndex(0)
    assert not d._stats.isActive()
    d.tabs.setCurrentIndex(d.tabs.count() - 1)
    d.hide()
    assert not d._stats.isActive()


def test_wizard_saves_the_key_then_tests_a_role_of_that_provider(qapp: QApplication) -> None:
    sent: list[dict[str, Any]] = []
    w = FirstRunWizard(sent.append)
    w.models = {"fast": {"provider": "google", "model": "x"}, "planner": {"provider": "anthropic", "model": "y"}}
    w.provider.setCurrentText("anthropic")
    w.key.setText("sk-ant-test")
    w.test_btn.click()
    assert sent == [{"type": "secret_set", "name": "ANTHROPIC_API_KEY", "value": "sk-ant-test"}, {"type": "test_role", "role": "planner"}]
    assert w.key.text() == ""
    w.on_test_result({"ok": True})
    assert "Connected" in w.test_result.text()
    w.provider.setCurrentText("openrouter")
    w.test_btn.click()
    assert "No model uses this provider" in w.test_result.text()


def test_chat_quit_button_and_shortcut_emit_quit_requested(qapp: QApplication) -> None:
    w = ChatWindow(Session())
    got: list[int] = []
    w.quit_requested.connect(lambda: got.append(1))
    w.quit_btn.click()
    w.findChildren(QShortcut)[0].activated.emit()  # offscreen windows are never active, so keyClick cannot trigger it
    assert got == [1, 1]


def test_chat_attachment_can_be_removed_with_x(qapp: QApplication) -> None:
    w = ChatWindow(Session("t", "Mimi"))
    w._files = [{"name": "a.png", "data": "AA"}, {"name": "b<i>.png", "data": "AA"}]
    w.refresh({"busy"})
    assert w.chips.text().count("✕") == 2 and "b&lt;i&gt;.png" in w.chips.text()
    w._remove_file("rm:0")
    assert [f["name"] for f in w._files] == ["b<i>.png"]
    w._remove_file("rm:0")
    assert w._files == [] and w.chips.text() == ""


def test_quick_input_closes_itself_when_nothing_is_typed(qapp: QApplication) -> None:
    a = AvatarWindow(Skin.default())
    a.show()
    q = QuickInput()
    q.open_at(a)
    assert q._idle.isActive() and q._idle.interval() == 5000
    q.edit.setPlainText("x")
    assert not q._idle.isActive()
    q._idle.timeout.emit()
    assert not q.isVisible()


def test_documents_page_state_jobs_and_reports(qapp: QApplication, monkeypatch: Any) -> None:
    asked = confirm(monkeypatch)
    sent: list[dict[str, Any]] = []
    m = ManagerWindow(sent.append)
    page = m.pages["rag"][0]
    m.on_event({"type": "rag", "items": [{"source": "C:/d/a.md", "kind": "md", "chunks": 3, "updated": 1.0}], "watching": False, "paused": False})
    assert page.table.rowCount() == 1 and not page.sync_btn.isEnabled() and not page.pause_btn.isEnabled()  # nothing watched
    m.on_event({"type": "rag", "items": page.rows, "watching": True, "paused": True})
    assert page.sync_btn.isEnabled() and "Resume" in page.pause_btn.text() or "Tiếp" in page.pause_btn.text()
    page.pause_btn.click()
    assert sent[-1] == {"type": "rag_resume"}
    page.sync_btn.click()
    assert asked and sent[-1]["type"] == "rag_sync" and not page.add_file.isEnabled()  # confirmed, and locked while indexing
    rid = sent[-1]["rid"]
    m.on_event({"type": "rag_synced", "rid": rid, "folders": 1, "report": ["ingested: a.md (3 chunks)", "failed: b.pdf: boom"]})
    assert page.add_file.isEnabled() and "b.pdf" in page.output.toPlainText() and "!" in m._note
    m.on_event({"type": "rag_synced", "rid": 0, "folders": 0, "report": []})
    assert "!" in m._note
    row_btns_rag = page.table.cellWidget(0, 4).findChildren(QPushButton)
    row_btns_rag[0].click()
    assert "not deleted" in asked[-1] or "không bị xóa" in asked[-1]
    assert sent[-1] == {"type": "rag_remove", "source": "C:/d/a.md", "rid": sent[-1]["rid"]}
    page.query.setText("tea")
    page.search_btn.click()
    srid = sent[-1]["rid"]
    m.on_event({"type": "rag_results", "rid": srid, "note": "", "hits": [{"cite": "a.md", "text": "green", "score": 0.9}]})
    assert "a.md" in page.output.toPlainText() and page.search_btn.isEnabled()
    page.search_btn.click()
    m.on_event({"type": "error", "rid": sent[-1]["rid"], "message": "x"})
    assert page.search_btn.isEnabled()  # a refused search unlocks the button


def test_mcp_page_shows_reload_state_confirms_and_locks_while_reloading(qapp: QApplication, monkeypatch: Any) -> None:
    asked = confirm(monkeypatch)
    sent: list[dict[str, Any]] = []
    m = ManagerWindow(sent.append)
    page = m.pages["mcp"][0]
    srv = {"enabled": True, "status": "ok (1 tools)", "kind": "stdio", "target": "npx -y thing", "env": ["TOKEN"], "reload_needed": False,
           "tools": [{"name": "fs_read", "tier": "T0"}], "skipped": [{"name": "fs_bad", "reason": "invalid tier 'T9'"}]}
    off = {**srv, "enabled": False, "status": "disabled", "tools": [], "skipped": [], "reload_needed": True}
    m.on_event({"type": "mcp", "servers": {"fs": srv, "old": off}, "reload_needed": True})
    assert page.table.rowCount() == 2
    assert "reload needed" in page.table.item(1, 4).text() or "cần tải lại" in page.table.item(1, 4).text()
    assert "not applied" in page.state.text() or "chưa được áp dụng" in page.state.text()

    def labels(r: int) -> list[str]:
        return [b.toolTip().split(":")[0] for b in page.table.cellWidget(r, 6).findChildren(QPushButton)]

    assert labels(0) == ["Disable", "Tools", "Remove"] and labels(1) == ["Enable", "Tools", "Remove"]  # only the action that applies
    page.table.cellWidget(0, 6).findChildren(QPushButton)[1].click()  # Tools: shows tiers and skipped, sends nothing
    assert "[T0] fs_read" in page.output.toPlainText() and "invalid tier" in page.output.toPlainText() and not sent
    page.table.cellWidget(1, 6).findChildren(QPushButton)[0].click()  # Enable asks first and says what would run
    assert asked and "npx -y thing" in asked[-1] and sent[-1]["type"] == "mcp_set" and sent[-1]["enabled"] is True
    page.reload_btn.click()
    assert "npx -y thing" in asked[-1] and sent[-1]["type"] == "mcp_reload" and not page.reload_btn.isEnabled()  # locked, with a busy label
    m.on_event({"type": "mcp", "rid": sent[-1]["rid"], "reloaded": True, "reload_needed": False,
                "servers": {"fs": {**srv, "status": "error: boom"}, "old": off}})
    assert page.reload_btn.isEnabled() and "boom" in page.output.toPlainText() and "!" in m.status.text()

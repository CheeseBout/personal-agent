from pathlib import Path

import pytest

from personal_agent.threads import ThreadStore, entry_for, new_id, title_from


@pytest.fixture
def store(tmp_path: Path) -> ThreadStore:
    return ThreadStore(tmp_path / "t.db")


def test_first_user_message_names_the_thread_and_a_rename_sticks(store: ThreadStore) -> None:
    store.add("a", "user", "Tóm tắt giúp mình báo cáo tuần này\nchi tiết hơn")
    assert store.threads()[0]["title"] == "Tóm tắt giúp mình báo cáo tuần này"
    store.add("a", "user", "another")  # a later message must not rename it
    assert store.rename("a", "  Báo cáo   tuần ") and store.threads()[0]["title"] == "Báo cáo tuần"
    store.add("a", "user", "yet another")
    assert store.threads()[0]["title"] == "Báo cáo tuần"
    with pytest.raises(ValueError):
        store.rename("a", "   ")
    assert store.rename("missing", "x") is False


def test_title_is_cut_at_a_word_boundary() -> None:
    t = title_from("word " * 30)
    assert t.endswith("…") and len(t) <= 51 and not t[:-1].endswith(" ")
    assert title_from("short") == "short" and title_from("") == ""


def test_list_orders_pinned_first_then_newest_and_hides_scheduled(store: ThreadStore) -> None:
    for tid in ("a", "b", "sched-1"):
        store.add(tid, "user", tid)
    assert [t["id"] for t in store.threads()] == ["b", "a"]
    assert store.pin("a", True)
    assert [t["id"] for t in store.threads()] == ["a", "b"]
    assert "sched-1" in [t["id"] for t in store.threads(scheduled=True)]
    assert store.threads()[0]["count"] == 1


def test_history_pages_from_the_newest_end(store: ThreadStore) -> None:
    for i in range(7):
        store.add("a", "user" if i % 2 == 0 else "agent", f"m{i}")
    page = store.history("a", limit=3)
    assert [e["text"] for e in page["entries"]] == ["m4", "m5", "m6"] and page["more"] is True
    older = store.history("a", before=page["entries"][0]["id"], limit=3)
    assert [e["text"] for e in older["entries"]] == ["m1", "m2", "m3"] and older["more"] is True
    oldest = store.history("a", before=older["entries"][0]["id"], limit=3)
    assert [e["text"] for e in oldest["entries"]] == ["m0"] and oldest["more"] is False
    assert store.history("nope")["entries"] == []


def test_search_ignores_case_and_vietnamese_accents_and_can_filter(store: ThreadStore) -> None:
    store.add("a", "user", "Đường đi tới Hà Nội")
    store.add("a", "agent", "Báo cáo doanh thu quý 4 đã xong")
    store.add("b", "user", "xem file", {"attachments": ["bc.pdf"]}, "attach")
    store.add("b", "event", "approval requested: file_write", {"approval": True}, "approval")
    assert [h["thread_id"] for h in store.search("duong di")] == ["a"]
    assert [h["thread_id"] for h in store.search("BAO CAO doanh")] == ["a"]
    assert store.search("bao cao quý") and not store.search("khong co")
    assert [h["thread_id"] for h in store.search("", flag="attach")] == ["b"]
    assert [h["thread_id"] for h in store.search("file", flag="approval")] == ["b"]
    assert store.search("") == []
    assert len(store.search("duong")[0]["text"]) <= 142
    assert store.search('"; DROP TABLE entries; --') == []  # query text is never SQL or FTS syntax


def test_delete_removes_messages_and_their_search_hits(store: ThreadStore) -> None:
    store.add("a", "user", "secret plan")
    store.add("a", "agent", "ok")
    store.add("b", "user", "keep me")
    assert store.delete("a") == 2
    assert store.search("secret") == [] and not store.exists("a") and store.exists("b")
    assert store.history("a")["entries"] == []


def test_secrets_are_redacted_before_they_are_stored(store: ThreadStore) -> None:
    from personal_agent.observability.logging import register_secret

    register_secret("sk-live-abcdef123456")
    store.add("a", "user", "my key is sk-live-abcdef123456 ok")
    assert "sk-live-abcdef123456" not in store.history("a")["entries"][0]["text"]
    assert store.search("abcdef123456") == []


def test_export_markdown_keeps_the_conversation_and_mutes_the_noise(store: ThreadStore) -> None:
    store.add("a", "user", "hello")
    store.add("a", "event", "tool: file_read {}")
    store.add("a", "agent", "hi there")
    md = store.export_markdown("a")
    assert md.startswith("# hello") and "**You**" in md and "hi there" in md and "> tool: file_read" in md
    with pytest.raises(ValueError):
        store.export_markdown("zzz")


def test_entry_for_mirrors_the_ui_wording() -> None:
    assert entry_for({"type": "intent", "intent": "task", "confidence": 0.9}) == ("event", "intent: task (90%)", {}, "")
    assert entry_for({"type": "final", "text": "done", "stopped": "budget", "used": {"steps": 1}}) == ("agent", "done (stopped: budget)", {"used": {"steps": 1}}, "")
    assert entry_for({"type": "tool_call", "name": "file_read", "args": {"path": "a"}})[1] == 'tool: file_read {"path":"a"}'
    assert entry_for({"type": "approval_request", "approvals": [{"tool": "file_write"}]})[3] == "approval"
    assert entry_for({"type": "token", "text": "x"}) is None and entry_for({"type": "compacted"})[0] == "note"


def test_new_ids_are_valid_thread_ids() -> None:
    import re

    assert re.fullmatch(r"[\w\-]{1,64}", new_id()) and new_id() != new_id()

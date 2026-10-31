from pathlib import Path

from personal_agent.sandbox.backup import new_apply_id, write_with_backup
from personal_agent.tools.files import file_write, undo_change


def aid_of(msg: str) -> str:
    return msg.split("undo_change('")[1].split("'")[0]


def test_write_new_then_undo_deletes_it(env: Path) -> None:
    msg = file_write.invoke({"path": "n.txt", "content": "hello"})
    assert (env / "n.txt").read_text() == "hello"
    assert undo_change.invoke({"apply_id": aid_of(msg)}).startswith("restored")
    assert not (env / "n.txt").exists()


def test_overwrite_then_undo_restores_previous_content(env: Path) -> None:
    (env / "f.txt").write_text("precious")
    msg = file_write.invoke({"path": "f.txt", "content": "clobbered"})
    assert (env / "f.txt").read_text() == "clobbered"
    undo_change.invoke({"apply_id": aid_of(msg)})
    assert (env / "f.txt").read_text() == "precious"
    assert undo_change.invoke({"apply_id": aid_of(msg)}).startswith("ERROR")  # not twice


def test_write_outside_scope_is_refused(env: Path, tmp_path: Path) -> None:
    out = file_write.invoke({"path": str(tmp_path / "outside.txt"), "content": "x"})
    assert out.startswith("ERROR: access denied") and not (tmp_path / "outside.txt").exists()
    assert file_write.invoke({"path": "..\\up.txt", "content": "x"}).startswith("ERROR: access denied")
    assert file_write.invoke({"path": "ads.txt:stream", "content": "x"}).startswith("ERROR: access denied")
    assert file_write.invoke({"path": "big.txt", "content": "x" * 1_000_001}).startswith("ERROR")


def test_agent_cannot_touch_its_own_data_dir(env: Path) -> None:
    from personal_agent.settings.settings import data_dir

    target = data_dir() / "config.yaml"
    assert file_write.invoke({"path": str(target), "content": "x"}).startswith("ERROR: access denied")


def test_undo_rejects_bad_ids(env: Path) -> None:
    for bad in ("../../x", "zzzz", "", "a" * 40):
        assert undo_change.invoke({"apply_id": bad}).startswith("ERROR")


def test_failed_write_leaves_original_intact(env: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (env / "keep.txt").write_text("safe")
    import os

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    try:
        write_with_backup(env / "keep.txt", b"new", new_apply_id())
    except OSError:
        pass
    assert (env / "keep.txt").read_text() == "safe"  # atomic replace: a failure never truncates the file

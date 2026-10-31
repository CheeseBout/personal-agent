import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from personal_agent.sandbox import manager as mgr
from personal_agent.sandbox.backup import new_apply_id, undo
from personal_agent.sandbox.manager import DockerSandbox, SandboxUnavailable
from personal_agent.sandbox.staging import Staging
from personal_agent.tools.registry import policy
from personal_agent.tools.sandbox_tools import (
    sandbox_apply,
    sandbox_changes,
    sandbox_run,
    sandbox_stage,
    staging,
)


def st(env: Path, tmp_path: Path) -> Staging:
    return Staging(tmp_path / "stg", policy(), env)


def by_rel(s: Staging) -> dict[str, Any]:
    return {c.rel: c for c in s.changes()}


# ---- staging / diff / apply / undo (no Docker needed) -------------------------------------------------------
def test_stage_copies_and_originals_stay_untouched(env: Path, tmp_path: Path) -> None:
    (env / "a.txt").write_text("one\ntwo\n")
    (env / "sub").mkdir()
    (env / "sub" / "b.txt").write_text("bee")
    s = st(env, tmp_path)
    assert sorted(s.stage(["a.txt", "sub"])) == ["a.txt", "sub/b.txt"]
    (s.work / "a.txt").write_text("changed\n")
    assert (env / "a.txt").read_text() == "one\ntwo\n"  # work happens on copies only


def test_changes_classify_added_modified_deleted_with_diffs(env: Path, tmp_path: Path) -> None:
    (env / "a.txt").write_text("one\ntwo\n")
    (env / "gone.txt").write_text("bye")
    s = st(env, tmp_path)
    s.stage(["a.txt", "gone.txt"])
    (s.work / "a.txt").write_text("one\nTWO\n")
    (s.work / "gone.txt").unlink()
    (s.work / "new.md").write_text("# new")
    c = by_rel(s)
    assert {k: v.status for k, v in c.items()} == {"a.txt": "modified", "gone.txt": "deleted", "new.md": "added"}
    assert "-two" in c["a.txt"].diff and "+TWO" in c["a.txt"].diff
    assert Path(c["new.md"].target) == env / "sandbox-output" / "new.md"  # new files land in the output folder


def test_apply_subset_backs_up_and_undo_restores_everything(env: Path, tmp_path: Path) -> None:
    (env / "a.txt").write_text("original")
    (env / "gone.txt").write_text("keep me")
    s = st(env, tmp_path)
    s.stage(["a.txt", "gone.txt"])
    (s.work / "a.txt").write_text("modified")
    (s.work / "gone.txt").unlink()
    (s.work / "new.md").write_text("new")
    aid = new_apply_id()
    done = s.apply(["a.txt", "gone.txt"], aid)  # per-file approval: new.md is NOT applied
    assert (env / "a.txt").read_text() == "modified" and not (env / "gone.txt").exists()
    assert not (env / "sandbox-output" / "new.md").exists() and len(done) == 2
    assert undo(aid) and (env / "a.txt").read_text() == "original" and (env / "gone.txt").read_text() == "keep me"
    with pytest.raises(FileNotFoundError):
        undo(aid)  # only once


def test_apply_all_then_undo_removes_created_files(env: Path, tmp_path: Path) -> None:
    s = st(env, tmp_path)
    (s.work / "out.txt").write_text("fresh")
    aid = new_apply_id()
    s.apply(None, aid)
    assert (env / "sandbox-output" / "out.txt").read_text() == "fresh"
    assert not s.changes() or all(c.rel != "out.txt" for c in s.changes() if c.status != "conflict")
    undo(aid)
    assert not (env / "sandbox-output" / "out.txt").exists()


def test_conflict_when_host_file_changed_after_staging(env: Path, tmp_path: Path) -> None:
    (env / "a.txt").write_text("v1")
    s = st(env, tmp_path)
    s.stage(["a.txt"])
    (s.work / "a.txt").write_text("sandbox edit")
    (env / "a.txt").write_text("user edited meanwhile")
    c = by_rel(s)["a.txt"]
    assert c.status == "conflict"
    s.apply(None, new_apply_id())
    assert (env / "a.txt").read_text() == "user edited meanwhile"  # never clobbers newer host content


def test_stage_rejects_paths_outside_scope_and_big_files(env: Path, tmp_path: Path) -> None:
    s = st(env, tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    with pytest.raises(ValueError, match="outside the allowed"):
        s.stage([str(outside)])
    with pytest.raises(ValueError, match="not found"):
        s.stage(["nope.txt"])
    (env / "big.bin").write_bytes(b"x" * 6_000_000)
    with pytest.raises(ValueError, match="larger than"):
        s.stage(["big.bin"])


def test_links_made_inside_the_container_are_ignored(env: Path, tmp_path: Path) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("TOP SECRET")
    s = st(env, tmp_path)
    try:
        os.symlink(secret, s.work / "innocent.txt")
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    assert s.changes() == []  # a link to a host file must never be copied out as if it were sandbox output


def test_junction_made_inside_the_container_cannot_leak_host_files(env: Path, tmp_path: Path) -> None:
    secret_dir = tmp_path / "host-secrets"
    secret_dir.mkdir()
    (secret_dir / "key.txt").write_text("TOP SECRET")
    s = st(env, tmp_path)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(s.work / "link"), str(secret_dir)], capture_output=True, check=False)
    if r.returncode != 0:
        pytest.skip("cannot create a junction")
    assert s.changes() == []  # content behind a junction is outside the work dir: ignored, never copied out
    (env / "real.txt").write_text("ok")
    assert s.stage(["real.txt"]) == ["real.txt"]


def test_apply_target_is_policy_checked_at_apply_time(env: Path, tmp_path: Path) -> None:
    (env / "a.txt").write_text("x")
    s = st(env, tmp_path)
    s.stage(["a.txt"])
    (s.work / "a.txt").write_text("y")
    m = s._manifest_data()
    m["a.txt"]["origin"] = str(tmp_path / "elsewhere.txt")  # tampered manifest pointing outside the workspace
    s._save(m)
    assert by_rel(s)["a.txt"].status == "blocked"
    s.apply(None, new_apply_id())
    assert not (tmp_path / "elsewhere.txt").exists()


def test_apply_tier_is_t1_only_for_new_files_in_the_output_folder(env: Path, tmp_path: Path) -> None:
    from personal_agent.tools.registry import load_tools, meta_for

    load_tools()
    meta = meta_for("sandbox_apply")
    assert meta is not None and meta.tier_fn is not None
    s = staging()
    (env / "a.txt").write_text("x")
    (s.work / "new.md").write_text("n")
    assert meta.tier_fn({}) == "T1"  # only an added file going to sandbox-output/
    s.stage(["a.txt"])
    (s.work / "a.txt").write_text("changed")
    assert meta.tier_fn({}) == "T2"  # touches an existing file -> approval
    assert meta.tier_fn({"files": ["new.md"]}) == "T1"  # per-file selection is honoured
    assert "a.txt" in (meta.preview_fn({}) if meta.preview_fn else "")


# ---- docker command construction & lifecycle (fake runner: no engine needed) ------------------------------------
class FakeDocker:
    def __init__(self, running: str = "") -> None:
        self.calls: list[list[str]] = []
        self.running = running  # e.g. "true false" -> container up, network off

    def __call__(self, args: list[str], **_: Any) -> "subprocess.CompletedProcess[bytes]":
        self.calls.append(args)
        verb = args[1]
        out = b""
        rc = 0
        if verb == "inspect":
            out, rc = (self.running.encode(), 0) if self.running else (b"", 1)
        if verb == "image":
            rc = 0
        return subprocess.CompletedProcess(args, rc, out, b"")

    def verbs(self) -> list[str]:
        return [c[1] for c in self.calls]


CFG = {"image": "img:1", "cpus": 1, "memory": "512m", "pids": 128, "idle_timeout_s": 300, "exec_timeout_s": 60}


def test_container_is_locked_down(tmp_path: Path) -> None:
    args = DockerSandbox(lambda: CFG, tmp_path / "work").run_args(network=False)
    joined = " ".join(args)
    for must in ("--network none", "--cap-drop ALL", "--security-opt no-new-privileges", "--read-only",
                 "--user 1000:1000", "--memory 512m", "--cpus 1", "--pids-limit 128", "--rm"):
        assert must in joined, must
    assert args.count("-v") == 1 and args[args.index("-v") + 1].endswith(":/work:rw")  # the ONLY mount
    assert "--privileged" not in args and "docker.sock" not in joined and "--pid" not in args
    assert "--network bridge" in " ".join(DockerSandbox(lambda: CFG, tmp_path).run_args(network=True))


def test_exec_starts_lazily_and_reuses_a_matching_container(tmp_path: Path) -> None:
    fake = FakeDocker()
    sb = DockerSandbox(lambda: CFG, tmp_path, runner=fake)  # type: ignore[arg-type]
    assert fake.calls == []  # constructing it does nothing
    sb.exec("echo hi", 10)
    assert fake.verbs().count("run") == 1 and "exec" in fake.verbs()
    fake.running = "true false"
    fake.calls.clear()
    sb.exec("echo again", 10)
    assert "run" not in fake.verbs()  # already up with the right network mode
    fake.running = "true false"
    fake.calls.clear()
    sb.exec("curl x", 10, network=True)  # needs a different network mode -> recreated
    assert fake.verbs().count("run") == 1 and fake.verbs().count("rm") == 1
    sb.stop()


def test_idle_timeout_removes_the_container(tmp_path: Path) -> None:
    fake = FakeDocker()
    sb = DockerSandbox(lambda: {**CFG, "idle_timeout_s": 0.2}, tmp_path, runner=fake)  # type: ignore[arg-type]
    sb.exec("echo hi", 10)
    fake.calls.clear()
    time.sleep(0.8)
    assert ["docker", "rm", "-f", mgr.NAME] in fake.calls


def test_stop_never_touches_docker_if_it_was_never_started(tmp_path: Path) -> None:
    fake = FakeDocker()
    DockerSandbox(lambda: CFG, tmp_path, runner=fake).stop()  # type: ignore[arg-type]
    assert fake.calls == []


def test_missing_docker_binary_is_reported_cleanly(tmp_path: Path) -> None:
    def no_docker(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError

    sb = DockerSandbox(lambda: CFG, tmp_path, runner=no_docker)
    assert sb.available() is False
    with pytest.raises(SandboxUnavailable, match="not running"):
        sb.exec("echo hi", 5)


def test_engine_down_gives_a_helpful_tool_error_not_a_crash(env: Path) -> None:
    from personal_agent.tools.sandbox_tools import sandbox

    if sandbox().available():
        pytest.skip("Docker is running here; this checks the engine-down path")
    out = sandbox_run.invoke({"command": "echo hi"})
    assert out.startswith("ERROR: sandbox unavailable") and "Docker" in out


# ---- tools wired to staging -------------------------------------------------------------------------------------------
def test_tools_stage_changes_apply_roundtrip(env: Path) -> None:
    (env / "r.txt").write_text("a\n")
    assert "staged 1" in sandbox_stage.invoke({"paths": ["r.txt"]})
    (staging().work / "r.txt").write_text("a\nb\n")
    assert "modified" in sandbox_changes.invoke({}) and "+b" in sandbox_changes.invoke({})
    out = sandbox_apply.invoke({"files": None})
    assert (env / "r.txt").read_text() == "a\nb\n" and "undo_change" in out
    assert sandbox_stage.invoke({"paths": ["../../etc"]}).startswith("ERROR")


# ---- real Docker (run with: pytest -m docker; skipped automatically when the engine is down) ---------------------------
@pytest.fixture
def real_sandbox(env: Path) -> Any:
    from personal_agent.tools.sandbox_tools import sandbox

    sb = sandbox()
    if not sb.available():
        pytest.skip("Docker engine is not running")
    yield sb
    sb.stop()


@pytest.mark.docker
def test_real_container_isolation(real_sandbox: Any, env: Path) -> None:
    rc, out = real_sandbox.exec("id -u && echo hello > /work/made.txt && cat /work/made.txt", 30)
    assert rc == 0 and out.split() == ["1000", "hello"]  # non-root, can write /work
    assert (staging().work / "made.txt").read_text().strip() == "hello"  # visible to the host-side diff
    assert real_sandbox.exec("touch /etc/x", 10)[0] != 0  # read-only root filesystem
    assert real_sandbox.exec("python -c \"import urllib.request as u; u.urlopen('http://1.1.1.1', timeout=3)\"", 15)[0] != 0
    rc, _ = real_sandbox.exec("ls /mnt /host 2>&1; test -e /var/run/docker.sock", 10)
    assert rc != 0  # no docker socket, no host mounts
    ch = {c.rel: c.status for c in staging().changes()}
    assert ch == {"made.txt": "added"}


@pytest.mark.docker
def test_real_container_timeout_kills_runaway_commands(real_sandbox: Any) -> None:
    started = time.time()
    rc, _ = real_sandbox.exec("while true; do :; done", 3)
    assert rc != 0 and time.time() - started < 20

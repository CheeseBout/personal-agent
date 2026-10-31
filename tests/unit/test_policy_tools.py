from pathlib import Path

import pytest

from personal_agent.policy.engine import PolicyEngine, ToolMeta
from personal_agent.tools import web
from personal_agent.tools.files import file_read
from personal_agent.tools.registry import policy
from personal_agent.tools.shell import shell


def test_unknown_tool_and_high_tiers_denied() -> None:
    p = PolicyEngine()
    assert not p.authorize(None, None).allowed
    assert not p.authorize("x", ToolMeta(tier="T2")).allowed
    assert not p.authorize("x", ToolMeta(tier="T3")).allowed
    assert p.authorize("x", ToolMeta(tier="T0")).allowed


def test_path_scope_and_traversal(env: Path) -> None:
    p = policy()
    assert p.check_path(env / "a.txt").allowed
    assert not p.check_path(env / ".." / "secret.txt").allowed
    assert not p.check_path("C:\\Windows\\System32\\drivers\\etc\\hosts").allowed


def test_file_read(env: Path) -> None:
    (env / "a.txt").write_text("xin chào", encoding="utf-8")
    assert file_read.invoke({"path": "a.txt"}) == "xin chào"
    assert file_read.invoke({"path": str(env / "missing.txt")}).startswith("ERROR")
    assert "denied" in file_read.invoke({"path": str(env / ".." / "x.txt")})
    (env / "big.txt").write_text("x" * 50_000)
    assert "truncated" in file_read.invoke({"path": "big.txt", "max_chars": 50_000})


@pytest.mark.parametrize(
    "cmd",
    [
        "del a.txt",  # not allowlisted
        "echo a; del b",  # chaining
        "echo hi > out.txt",  # redirection
        "echo $env:USERPROFILE",  # variable expansion
        "Get-ChildItem C:\\Windows",  # path outside scope
        "dir ..\\..",  # traversal
        "git diff --output=x.txt",  # writes a file
        "git status",  # git reads repo config that can execute helpers
        "echo hello",  # PowerShell alias/cmdlet surface is gone
        "dir",
        "where C:\\Windows\\notepad.exe",  # path argument not matched by the pattern
        "whoami /all",  # extra args are not in the pattern
        "hostname -s",
    ],
)
def test_shell_refuses(cmd: str) -> None:
    assert shell.invoke({"command": cmd}).startswith("ERROR: command refused")


def test_shell_runs_allowlisted_as_argv() -> None:
    out = shell.invoke({"command": "hostname"})
    assert out.startswith("exit=0") and len(out) > len("exit=0\n")
    assert shell.invoke({"command": "where python"}).startswith("exit=")


@pytest.mark.parametrize(
    "bad",
    ["a.txt:stream", "a.txt.", "a.txt ", "\\\\server\\share\\a.txt", "\\\\?\\C:\\Windows\\win.ini", "x\\.. \\a.txt"],
)
def test_windows_path_forms_denied(env: Path, bad: str) -> None:
    assert not policy().check_path(str(env) + "\\" + bad if not bad.startswith("\\\\") else bad).allowed


def test_junction_out_of_workspace_denied(env: Path, tmp_path: Path) -> None:
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.txt").write_text("top secret")
    link = env / "link"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False)
    if r.returncode != 0:
        pytest.skip("cannot create junction")
    assert "denied" in file_read.invoke({"path": "link\\s.txt"})


def test_executable_inside_workspace_is_never_run(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = env / "where.exe"
    fake.write_text("not a real binary")
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: str(fake))
    out = shell.invoke({"command": "where python"})
    assert out.startswith("ERROR: command refused") and "workspace" in out


def test_more_secret_formats_redacted(env: Path) -> None:
    (env / "k.txt").write_text(
        "aws AKIAIOSFODNN7EXAMPLE gh ghp_abcdefghijklmnopqrstuvwx\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----"
    )
    out = file_read.invoke({"path": "k.txt"})
    assert not any(s in out for s in ("AKIAIOSF", "ghp_abcdef", "MIIEabc"))


def test_tool_output_is_redacted(env: Path) -> None:
    (env / "k.txt").write_text("key=sk-abcdefghijklmnop1234")
    assert "abcdefghij" not in file_read.invoke({"path": "k.txt"})


def test_web_search_marks_untrusted_and_survives_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "PROVIDER", lambda q, n: [{"title": "T", "href": "http://x", "body": "B"}])
    out = web.web_search.invoke({"query": "q"})
    assert "UNTRUSTED" in out and "http://x" in out

    def boom(q: str, n: int) -> list[dict[str, str]]:
        raise TimeoutError

    monkeypatch.setattr(web, "PROVIDER", boom)
    assert web.web_search.invoke({"query": "q"}).startswith("ERROR")

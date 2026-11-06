from pathlib import Path

import pytest

from personal_agent.policy.engine import PolicyEngine, ToolMeta
from personal_agent.tools import web
from personal_agent.tools.files import file_read
from personal_agent.tools.registry import load_tools, meta_for, policy
from personal_agent.tools.shell import shell


def test_tier_decisions() -> None:
    p = PolicyEngine()
    assert p.evaluate("x", None, {}).action == "deny"  # unknown tool
    assert p.evaluate("x", ToolMeta(tier="T0"), {}).action == "allow"
    assert p.evaluate("x", ToolMeta(tier="T1"), {}).action == "allow"
    assert p.evaluate("x", ToolMeta(tier="T2"), {}).action == "approve"
    assert p.evaluate("x", ToolMeta(tier="T3"), {}).action == "deny"  # disabled by default


def test_taint_escalates_t1_side_effects_but_not_reads() -> None:
    p = PolicyEngine()
    assert p.evaluate("x", ToolMeta(tier="T1", side_effects=True), {}, tainted=True).action == "approve"
    assert p.evaluate("x", ToolMeta(tier="T1", side_effects=False), {}, tainted=True).action == "allow"
    assert p.evaluate("x", ToolMeta(tier="T0"), {}, tainted=True).action == "allow"


def test_args_dependent_tier_and_rate_limit(env: Path) -> None:
    p = PolicyEngine()
    m = ToolMeta(tier="T1", tier_fn=lambda a: "T2" if a.get("network") else "T1")
    assert p.evaluate("sb", m, {"network": False}).action == "allow"
    assert p.evaluate("sb", m, {"network": True}).action == "approve"
    p.cfg["rate_limits"]["per_tool_per_minute"] = 3  # cfg is cached per mtime: mutate in place for the test
    assert [p.evaluate("r", ToolMeta(tier="T0"), {}).action for _ in range(5)] == ["allow"] * 3 + ["deny"] * 2


def test_domain_allowlist() -> None:
    p = PolicyEngine()
    p.cfg["network"]["domain_allowlist"] = ["example.com"]
    assert p.check_domain("https://api.example.com/x").allowed
    assert not p.check_domain("https://evilexample.com/").allowed
    assert not p.check_domain("https://example.com.evil.io/").allowed


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
        "git diff --output=x.txt",
        "format c: /q",
        "reg delete HKLM\\Software\\x /f",
        "Remove-Item C:\\Users\\x -Recurse -Force",
        "powershell -c Invoke-Expression 'calc'",
        "Set-ExecutionPolicy Unrestricted",
        "vssadmin delete shadows /all",
    ],
)
def test_shell_hard_denies_even_if_a_human_would_approve(cmd: str) -> None:
    meta = meta_for("shell")
    assert meta is not None and meta.tier_fn is not None and meta.tier_fn({"command": cmd}) == "T3"  # gate: denied
    assert shell.invoke({"command": cmd}).startswith("ERROR: command refused")  # and the tool itself refuses


@pytest.mark.parametrize(
    ("cmd", "tier"),
    [("hostname", "T1"), ("whoami", "T1"), ("where python", "T1"), ("echo hello", "T2"), ("whoami /all", "T2"),
     ("echo a; del b", "T2"), ("echo $env:USERPROFILE", "T2"), ("Get-ChildItem C:\\Windows", "T2")],
)
def test_shell_tier_classification(cmd: str, tier: str) -> None:
    meta = meta_for("shell")
    assert meta is not None and meta.tier_fn is not None and meta.tier_fn({"command": cmd}) == tier


def test_shell_runs_allowlisted_as_argv() -> None:
    load_tools()
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


def _tool(name: str):
    load_tools()
    m = meta_for(name)
    assert m is not None
    return m


def test_outside_the_allowed_folders_asks_a_human_instead_of_refusing(env: Path, tmp_path: Path) -> None:
    out = tmp_path / "elsewhere"
    out.mkdir()
    (out / "a.txt").write_text("hello")
    p = policy()
    # inside: reads are automatic, writes need approval (unchanged)
    assert p.evaluate("file_read", _tool("file_read"), {"path": "x.txt"}).action == "allow"
    assert p.evaluate("file_write", _tool("file_write"), {"path": "x.txt", "content": ""}).tier == "T2"
    # outside: every file tool needs approval, with a scope that can be remembered for reads only
    for name, args in [("file_read", {"path": str(out / "a.txt")}), ("file_list", {"path": str(out)}),
                       ("file_search", {"query": "h", "path": str(out)}), ("rag_ingest", {"path": str(out)}),
                       ("file_delete", {"path": str(out / "a.txt")}),
                       ("file_move", {"source": "x.txt", "destination": str(out / "b.txt")})]:
        g = p.evaluate(name, _tool(name), args)
        assert (g.action, g.tier) == ("approve", "T2"), name
    m = _tool("file_read")
    assert m.scope_fn is not None and m.scope_fn({"path": str(out / "a.txt")}) is not None
    assert m.scope_fn({"path": "x.txt"}) is None  # inside the workspace: nothing to remember
    assert _tool("file_delete").scope_fn is None and _tool("file_move").scope_fn is None


def test_denied_locations_stay_denied_and_the_tool_refuses_without_an_approval(env: Path, tmp_path: Path) -> None:
    out = tmp_path / "elsewhere"
    out.mkdir()
    (out / "a.txt").write_text("hello")
    p = policy()
    assert p.evaluate("file_read", _tool("file_read"), {"path": r"C:\Windows\win.ini"}).action == "deny"
    assert "denied" in file_read.invoke({"path": str(out / "a.txt")})  # no gate, no grant: refused
    with p.granted([str(out / "a.txt")]):  # what `guard` does once a human approved
        assert file_read.invoke({"path": str(out / "a.txt")}) == "hello"
        assert "denied" in file_read.invoke({"path": str(out / "b.txt")})  # the grant covers only that path
    assert "denied" in file_read.invoke({"path": str(out / "a.txt")})  # and ends with the call

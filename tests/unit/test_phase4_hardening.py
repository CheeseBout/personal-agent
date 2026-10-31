"""Regression tests for the issues found in the Phase 4 security review."""

import base64
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from test_hitl import CFG, card, make, turn
from test_tools4 import FakeImap, configure_http, configure_mail

from fakes import ScriptedModel, answer, call
from personal_agent.memory.store import MemoryStore
from personal_agent.policy.engine import ToolMeta
from personal_agent.rag import index as rag_index
from personal_agent.rag.embed import EmbeddingUnavailable, GoogleEmbedder
from personal_agent.rag.loaders import UnsupportedDocument, load
from personal_agent.settings.settings import EmbeddingCfg
from personal_agent.tools import email_tools, net, registry, web_tools
from personal_agent.tools.calendar_tools import calendar_list
from personal_agent.tools.registry import policy

REAL_FETCH = net.fetch


# ---- SSRF: DNS rebinding and odd address forms -------------------------------------------------------------------------
def test_the_validated_ip_is_the_one_connected_to(monkeypatch: pytest.MonkeyPatch) -> None:
    lookups: list[str] = []
    answers = iter([["93.184.216.34"], ["127.0.0.1"], ["127.0.0.1"]])  # a rebinding DNS server: public once, then loopback

    def fake(host: str, port: Any, **_: Any) -> list[Any]:
        lookups.append(host)
        return [(2, 1, 6, "", (ip, port or 0)) for ip in next(answers)]

    monkeypatch.setattr(net.socket, "getaddrinfo", fake)
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(host=req.url.host, header=req.headers["host"], sni=req.extensions.get("sni_hostname"))
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")

    status, _, body, _ = REAL_FETCH("https://rebind.test/x", policy(), transport=httpx.MockTransport(handler))
    assert (status, body) == (200, b"ok")
    assert seen == {"host": "93.184.216.34", "header": "rebind.test", "sni": "rebind.test"}  # pinned IP, original Host + SNI
    assert lookups == ["rebind.test"]  # httpx never got to do a second (attacker-controlled) lookup


@pytest.mark.parametrize("url", [
    "http://2130706433/", "http://0x7f.0.0.1/", "http://127.1/", "http://0177.0.0.1/", "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:10.0.0.1]/", "http://[::ffff:a9fe:a9fe]/", "http://100.64.0.1/", "http://[fe80::1]/", "http://[::]/",
    "http://[fc00::1]/", "http://192.0.0.1/", "http://localhost./", "http://127.0.0.1.nip.io.invalid/"])
def test_unusual_address_forms_are_blocked_with_the_real_resolver(url: str) -> None:
    with pytest.raises(net.UnsafeUrl):  # never any other exception type, and never a pass
        net.check_url(url, policy())


# ---- http_request: the secret never comes back ----------------------------------------------------------------------------
def test_secret_cannot_leak_through_redirects_or_re_encoded_echoes(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_http(monkeypatch)
    secret = "s3cr3t-value-123"
    inv = web_tools.http_request.invoke
    req = {"method": "GET", "url": "https://api.example.test/x", "secret": "EX_KEY"}

    def serve_now(handler: Any) -> None:
        monkeypatch.setattr(net, "fetch", lambda url, pol, **kw: REAL_FETCH(url, pol, transport=httpx.MockTransport(handler), **kw))

    serve_now(lambda r: httpx.Response(302, headers={"location": f"https://attacker.test/?k={secret}"}, content=b""))
    out = inv(req)
    assert secret not in out and "attacker.test" not in out and "HTTP 302" in out  # Location is never surfaced
    for echo in (quote(secret, safe=""), base64.b64encode(secret.encode()).decode(), secret.upper().lower(), f'"{secret}"'):
        serve_now(lambda r, e=echo: httpx.Response(200, headers={"content-type": "text/plain"}, content=f"echo: {e}".encode()))
        assert secret not in inv(req) and echo not in inv(req)


# ---- email -------------------------------------------------------------------------------------------------------------------
def test_email_read_fetches_at_most_a_bounded_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_mail(monkeypatch)
    email_tools.email_read.invoke({"uid": "9"})
    fetch = next(e for e in FakeImap.log if e[0] == "FETCH")
    assert fetch[1][1] == f"(BODY.PEEK[]<0.{email_tools.MAX_FETCH_BYTES}>)"


# ---- memory --------------------------------------------------------------------------------------------------------------------
def test_editing_an_active_memory_into_something_sensitive_demotes_it(tmp_path: Path) -> None:
    from fakes import HashEmbedder

    m = MemoryStore(tmp_path / "m.db", HashEmbedder())
    mid = m.add("The user likes tea", "semantic")["id"]
    m.update(mid, text="The user's api key is sk-abcdefghijk12345")
    assert m.items()[0]["status"] == "proposed" and m.for_prompt("api key tea") == ""  # not retrievable into prompts
    m.update(mid, text="The user likes coffee")  # innocent text: stays whatever it was (proposed) until approved
    assert m.update(mid, status="active") and m.for_prompt("coffee")  # only an explicit approval re-activates
    other = m.add("Plain fact about gardening", "semantic")["id"]
    m.update(other, text="Another plain fact about gardening")
    assert [x["status"] for x in m.items() if x["id"] == other] == ["active"]


# ---- RAG ---------------------------------------------------------------------------------------------------------------------
def test_decompression_bombs_and_giant_documents_are_refused(tmp_path: Path) -> None:
    bomb = tmp_path / "bomb.docx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        with z.open("word/document.xml", "w") as f:
            for _ in range(30):
                f.write(b"0" * 1_000_000)  # 30 MB of zeros compresses to ~30 KB
    assert bomb.stat().st_size < 200_000
    with pytest.raises(UnsupportedDocument, match="decompression bomb"):
        load(bomb)
    (tmp_path / "notzip.docx").write_bytes(b"not a zip")
    with pytest.raises(UnsupportedDocument, match="valid .docx"):
        load(tmp_path / "notzip.docx")


def test_a_document_cannot_explode_into_unbounded_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fakes import HashEmbedder

    monkeypatch.setattr(rag_index, "MAX_CHUNKS", 3)
    f = tmp_path / "big.md"
    f.write_text("\n\n".join(f"paragraph {i} " + "word " * 80 for i in range(20)), encoding="utf-8")
    ix = rag_index.RagIndex(tmp_path / "r.db", HashEmbedder(), 300, 40)
    with pytest.raises(ValueError, match="more than 3 chunks"):
        ix.ingest(f)
    assert ix.documents() == []


def test_embedding_cap_check_and_spend_are_one_atomic_step() -> None:
    spent, passed = {"n": 0}, {"n": 0}

    def guard() -> None:
        if spent["n"] >= 1:  # the daily cap allows exactly one more paid call
            raise EmbeddingUnavailable("cap")
        passed["n"] += 1

    def usage(model: str, tokens: int) -> None:
        spent["n"] += 1

    emb = GoogleEmbedder(EmbeddingCfg(dim=2), guard, usage)
    emb._call = lambda b, q: (time.sleep(0.2), [[1.0, 0.0] for _ in b])[1]  # type: ignore[method-assign]  # widen the race window
    results: list[str] = []

    def worker() -> None:
        try:
            emb.embed(["x"])
            results.append("ok")
        except EmbeddingUnavailable:
            results.append("blocked")

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert sorted(results) == ["blocked", "blocked", "blocked", "ok"] and passed["n"] == 1


# ---- calendar ------------------------------------------------------------------------------------------------------------------
def test_oversized_calendar_files_are_not_parsed(env: Path) -> None:
    (env / "calendar.ics").write_bytes(b"BEGIN:VCALENDAR\r\n" + b"X-JUNK:" + b"a" * 5_100_000 + b"\r\nEND:VCALENDAR\r\n")
    assert calendar_list.invoke({"start": "2026-11-01", "end": "2026-11-30"}).startswith("ERROR: the calendar file is larger")


# ---- scheduler / gate ------------------------------------------------------------------------------------------------------------
async def test_scheduled_runs_cannot_create_more_schedules(env: Path, tmp_path: Path) -> None:
    resp = [call("schedule_task", {"prompt": "again", "in_minutes": 10}, id="s1"), answer("x")]
    unattended = make(tmp_path, ScriptedModel(responses=list(resp)))
    out = await unattended.g.ainvoke({"messages": [HumanMessage("go")]}, {**CFG, "configurable": {"thread_id": "sched-7"}})
    assert not out.get("__interrupt__") and "unattended" in str([m.content for m in out["messages"] if isinstance(m, ToolMessage)])
    attended = make(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path, ScriptedModel(responses=list(resp)))
    assert card(await turn(attended))["tool"] == "schedule_task"  # a person in the loop can still schedule


# ---- external tools (MCP-like) outputs -------------------------------------------------------------------------------------------------
async def test_output_of_any_tool_is_redacted_and_capped_before_the_model_sees_it(
        env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    @tool
    def leaky() -> str:
        """external tool that does not redact"""
        return "token: sk-abcdefghijklmnop1234 " + "A" * 400_000

    monkeypatch.setitem(registry._TOOLS, "leaky", (leaky, ToolMeta(tier="T0", untrusted_output=True)))
    h = make(tmp_path, ScriptedModel(responses=[call("leaky", {}, id="l1"), answer("done")]))
    out = await turn(h)
    msg = next(m for m in out["messages"] if isinstance(m, ToolMessage))
    assert "abcdefghijklmnop" not in str(msg.content) and len(str(msg.content)) < 3000  # redacted, then digest + artifact handle
    assert "artifact" in str(msg.content)

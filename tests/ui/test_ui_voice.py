"""Spoken answers (edge-tts mocked), recorder behaviour and global hotkeys."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from conftest import pump
from personal_agent.settings.settings import TtsCfg
from personal_agent.ui.system import Hotkeys
from personal_agent.ui.voice_io import Recorder, Speaker


def wait_for(cond: Any, ms: int = 5000) -> bool:
    waited = 0
    while waited < ms:
        if cond():
            return True
        pump(25)
        waited += 25
    return bool(cond())


class FakeComm:
    spoken: list[tuple[str, str, str]] = []

    def __init__(self, text: str, voice: str, rate: str = "+0%") -> None:
        FakeComm.spoken.append((text, voice, rate))

    async def save(self, path: str) -> None:
        Path(path).write_bytes(b"not really an mp3")  # the player reports InvalidMedia and the queue moves on


@pytest.fixture
def fake_tts(monkeypatch: pytest.MonkeyPatch) -> type[FakeComm]:
    FakeComm.spoken = []
    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FakeComm))
    return FakeComm


def test_speaker_reads_sentences_in_the_right_voice_then_reports_done_and_cleans_up(qapp: QApplication, fake_tts: type[FakeComm]) -> None:
    sp = Speaker()
    states: list[bool] = []
    sp.speaking_changed.connect(states.append)
    cfg = TtsCfg(rate="+10%")
    sp.speak("# Xin chào\n\nHôm nay trời đẹp. Bạn khỏe không?", cfg)
    assert states == [True] and sp.speaking
    assert wait_for(lambda: states == [True, False])
    assert [v for _, v, _ in fake_tts.spoken] == [cfg.voice_vi] * 2 and fake_tts.spoken[0][2] == "+10%"
    assert not sp.speaking and sp._tmp is None  # temp files removed
    sp.speak("Hello there. How are you?", cfg)
    assert wait_for(lambda: states[-2:] == [True, False] and len(states) == 4)
    assert fake_tts.spoken[-1][1] == cfg.voice_en


def test_new_speech_or_stop_interrupts_the_old_one(qapp: QApplication, fake_tts: type[FakeComm]) -> None:
    sp = Speaker()
    states: list[bool] = []
    sp.speaking_changed.connect(states.append)
    sp.speak("One. Two. Three. Four.", TtsCfg())
    sp.stop()
    assert not sp.speaking and states[-1] is False
    n = len(fake_tts.spoken)
    pump(200)
    assert len(fake_tts.spoken) <= n + 1  # the worker notices the new generation and stops synthesising
    sp.stop()  # stopping twice is harmless
    assert states.count(False) == 1


def test_speaker_reports_a_missing_engine_instead_of_crashing(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "edge_tts", None)  # import fails
    sp = Speaker()
    errors: list[str] = []
    states: list[bool] = []
    sp.failed.connect(errors.append)
    sp.speaking_changed.connect(states.append)
    sp.speak("Hello", TtsCfg())
    assert wait_for(lambda: bool(errors) and states[-1:] == [False])
    assert not sp.speaking


def test_empty_or_code_only_answers_are_not_spoken(qapp: QApplication, fake_tts: type[FakeComm]) -> None:
    sp = Speaker()
    sp.speak("```py\nprint(1)\n```", TtsCfg())
    sp.speak("", TtsCfg())
    assert not sp.speaking and fake_tts.spoken == []


def test_recorder_without_a_microphone_fails_cleanly(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtMultimedia import QAudioDevice, QMediaDevices

    monkeypatch.setattr(QMediaDevices, "defaultAudioInput", staticmethod(lambda: QAudioDevice()))
    r = Recorder()
    errors: list[str] = []
    r.failed.connect(errors.append)
    assert r.start() is False and errors == ["no microphone found"] and not r.active
    r.stop()  # stopping when idle is a no-op


def test_recorder_stops_after_silence_and_on_the_length_cap(qapp: QApplication) -> None:
    r = Recorder(max_seconds=1)
    out: list[bytes] = []
    errs: list[str] = []
    r.finished.connect(out.append)
    r.failed.connect(errs.append)

    class Dev:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        def readAll(self) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

    class Src:
        def stop(self) -> None: ...

    loud, quiet = b"\xff\x3f" * 1600, b"\x00\x00" * 1600  # 0.1 s each at 16 kHz
    r._src, r.active, r._io = Src(), True, Dev([loud] * 3 + [quiet] * 16)  # type: ignore[assignment]
    for _ in range(19):
        r._read()
        if not r.active:
            break
    assert not r.active and len(out) == 1 and out[0][:4] == b"RIFF"  # stopped by 1.5 s of silence after speech
    r2 = Recorder(max_seconds=1)
    r2.failed.connect(errs.append)
    r2._src, r2.active, r2._io = Src(), True, Dev([quiet])  # type: ignore[assignment]
    r2._read()
    r2.stop()
    assert errs == ["recording too short"]


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey is Windows only")
def test_hotkeys_register_conflicts_are_reported_and_cleared(qapp: QApplication) -> None:
    hk = Hotkeys()
    fired: list[int] = []
    assert hk.register("a", "ctrl+alt+shift+f23", lambda: fired.append(1))
    assert not hk.register("dup", "ctrl+alt+shift+f23", lambda: None)  # same key twice: second is refused, not fatal
    assert "already used" in hk.failed["dup"]
    assert not hk.register("bad", "nonsense", lambda: None) and "needs a modifier" in hk.failed["bad"]
    hk.clear()
    assert hk.failed == {}
    assert hk.register("again", "ctrl+alt+shift+f23", lambda: None)  # the key was really released
    hk.clear()

"""Microphone recording (push-to-talk / toggle, with optional silence stop) and spoken answers (edge-tts, interruptible).

Text-to-speech runs in the UI process: it is free (edge-tts) and playback needs the sound device anyway. Speech-to-text is a
daemon call (`transcribe`) so the same path handles recordings, audio attachments and cost metering."""

from __future__ import annotations

import asyncio
import io
import math
import re
import shutil
import tempfile
import threading
import wave
from array import array
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import (
    QAudioFormat,
    QAudioOutput,
    QAudioSource,
    QMediaDevices,
    QMediaPlayer,
)

from personal_agent.graph.lang import detect_lang
from personal_agent.settings.settings import TtsCfg

RATE = 16000
SPEECH_RMS = 600  # above this a chunk counts as speech
SILENCE_STOP_S = 1.5


# ---- pure helpers (tested) ------------------------------------------------------------------------------------------------
def pcm_to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def rms(pcm: bytes) -> float:
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    return math.sqrt(sum(x * x for x in a) / len(a)) if a else 0.0


_MD = [(re.compile(r"```.*?```", re.DOTALL), " "), (re.compile(r"`([^`]*)`"), r"\1"), (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),
       (re.compile(r"^#{1,6}\s*", re.MULTILINE), ""), (re.compile(r"[*_~>]+"), ""), (re.compile(r"^\s*[-+]\s+", re.MULTILINE), ""),
       (re.compile(r"https?://\S+"), " "), (re.compile(r"\s+"), " ")]


def strip_markdown(text: str) -> str:
    for pat, rep in _MD:
        text = pat.sub(rep, text)
    return text.strip()


def split_sentences(text: str, max_len: int = 220) -> list[str]:
    """Short chunks so playback starts after the first sentence is synthesised (streaming feel) and stop is quick."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?…。])\s+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        while len(p) > max_len:
            cut = p.rfind(" ", 0, max_len)
            cut = cut if cut > 40 else max_len
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def pick_voice(text: str, cfg: TtsCfg) -> str:
    return cfg.voice_vi if detect_lang(text) == "vi" else cfg.voice_en


# ---- recorder -----------------------------------------------------------------------------------------------------------
class Recorder(QObject):
    finished = Signal(bytes)  # a complete WAV
    failed = Signal(str)
    level = Signal(float)

    def __init__(self, max_seconds: int = 120, stop_on_silence: bool = True) -> None:
        super().__init__()
        self.max_seconds, self.stop_on_silence = max_seconds, stop_on_silence
        self._src: QAudioSource | None = None
        self._io: object = None
        self._buf = bytearray()
        self._heard = False
        self._quiet = 0.0
        self.active = False

    def start(self) -> bool:
        dev = QMediaDevices.defaultAudioInput()
        if dev.isNull():
            self.failed.emit("no microphone found")
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._src = QAudioSource(dev, fmt, self)
        self._buf, self._heard, self._quiet, self.active = bytearray(), False, 0.0, True
        self._io = self._src.start()
        self._io.readyRead.connect(self._read)
        return True

    def _read(self) -> None:
        chunk = bytes(self._io.readAll())  # type: ignore[attr-defined]
        if not chunk:
            return
        self._buf += chunk
        lvl, secs = rms(chunk), len(chunk) / 2 / RATE
        self.level.emit(lvl)
        if lvl > SPEECH_RMS:
            self._heard, self._quiet = True, 0.0
        elif self._heard:
            self._quiet += secs
        if len(self._buf) / 2 / RATE >= self.max_seconds or (self.stop_on_silence and self._quiet >= SILENCE_STOP_S):
            self.stop()

    def stop(self) -> None:
        if not self.active or self._src is None:
            return
        self.active = False
        self._src.stop()
        self._src = None
        if len(self._buf) / 2 / RATE >= 0.4:
            self.finished.emit(pcm_to_wav(bytes(self._buf)))
        else:
            self.failed.emit("recording too short")


# ---- spoken answers --------------------------------------------------------------------------------------------------------
class Speaker(QObject):
    speaking_changed = Signal(bool)
    failed = Signal(str)
    _ready = Signal(str, int)
    _done = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._player = QMediaPlayer(self)
        self._out = QAudioOutput(self)
        self._player.setAudioOutput(self._out)
        self._player.mediaStatusChanged.connect(self._status)
        self._ready.connect(self._enqueue)
        self._done.connect(self._synth_finished)
        self._gen = 0
        self._queue: list[str] = []
        self._playing = False
        self._synth_done = True
        self._tmp: Path | None = None

    @property
    def speaking(self) -> bool:
        return self._playing or not self._synth_done

    def speak(self, markdown: str, cfg: TtsCfg) -> None:
        text = strip_markdown(markdown)[: cfg.max_chars]
        if not text:
            return
        self.stop()
        self._gen += 1
        self._tmp = Path(tempfile.mkdtemp(prefix="agent-tts-"))
        self._synth_done = False
        self.speaking_changed.emit(True)
        threading.Thread(target=self._synth, args=(split_sentences(text), pick_voice(text, cfg), cfg.rate, self._gen, self._tmp),
                         daemon=True).start()

    def _synth(self, sentences: list[str], voice: str, rate: str, gen: int, tmp: Path) -> None:
        try:
            import edge_tts

            for i, s in enumerate(sentences):
                if gen != self._gen:
                    return
                path = tmp / f"{i}.mp3"
                asyncio.run(edge_tts.Communicate(s, voice, rate=rate).save(str(path)))
                if gen == self._gen:
                    self._ready.emit(str(path), gen)
        except Exception as e:  # noqa: BLE001 - offline / voice missing / extra not installed: report, never crash the UI
            if gen == self._gen:
                self.failed.emit(f"{type(e).__name__}: {str(e)[:120]}")
        finally:
            self._done.emit(gen)

    def _enqueue(self, path: str, gen: int) -> None:
        if gen != self._gen:
            return
        self._queue.append(path)
        if not self._playing:
            self._next()

    def _synth_finished(self, gen: int) -> None:
        if gen == self._gen:
            self._synth_done = True
            self._maybe_finish()

    def _next(self) -> None:
        if not self._queue:
            self._playing = False
            self._maybe_finish()
            return
        self._playing = True
        self._player.setSource(QUrl.fromLocalFile(self._queue.pop(0)))
        self._player.play()

    def _status(self, st: QMediaPlayer.MediaStatus) -> None:
        if st in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            self._next()

    def _maybe_finish(self) -> None:
        if not self._playing and not self._queue and self._synth_done:
            self.speaking_changed.emit(False)
            self._cleanup()

    def stop(self) -> None:
        """Interrupt now (user pressed stop, started talking, or a new answer arrived)."""
        was = self.speaking
        self._gen += 1
        self._queue.clear()
        self._playing, self._synth_done = False, True
        self._player.stop()
        self._cleanup()
        if was:
            self.speaking_changed.emit(False)

    def _cleanup(self) -> None:
        if self._tmp is not None:
            self._player.setSource(QUrl())
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

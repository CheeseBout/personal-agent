"""Speech-to-text. Provider is configurable (`voice.stt`): Gemini audio (default), OpenAI whisper, or local faster-whisper.

Returns (text, input_tokens, output_tokens) so the caller can meter cloud usage like any other model call.
"""

from __future__ import annotations

import io
import wave
from typing import Any

from personal_agent.settings.secrets import get_secret
from personal_agent.settings.settings import SttCfg

MAX_BYTES = 10_000_000
PROMPT = ("Transcribe this audio verbatim. The language is Vietnamese or English; keep the language that is spoken and keep "
          "Vietnamese diacritics. Output only the transcript, no comments. If there is no speech, output nothing.")


class SttError(RuntimeError):
    pass


def wav_seconds(audio: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio)) as w:
            return float(w.getnframes() / float(w.getframerate()))
    except (wave.Error, EOFError):
        return None


def check_audio(audio: bytes, mime: str, cfg: SttCfg) -> None:
    if not audio or len(audio) > MAX_BYTES:
        raise SttError("audio is empty or larger than 10 MB")
    if not mime.startswith("audio/"):
        raise SttError("not an audio file")
    secs = wav_seconds(audio) if mime in ("audio/wav", "audio/x-wav", "audio/wave") else None
    if secs is not None and secs > cfg.max_seconds:
        raise SttError(f"recording is longer than {cfg.max_seconds} s")


def transcribe(audio: bytes, mime: str, cfg: SttCfg) -> tuple[str, int, int]:
    check_audio(audio, mime, cfg)
    if cfg.provider == "google":
        return _google(audio, mime, cfg)
    if cfg.provider == "openai":
        return _openai(audio, cfg)
    if cfg.provider == "local":
        return _local(audio, cfg)
    raise SttError(f"unknown stt provider '{cfg.provider}'")


def _google(audio: bytes, mime: str, cfg: SttCfg) -> tuple[str, int, int]:
    key = get_secret("GOOGLE_API_KEY")
    if not key:
        raise SttError("GOOGLE_API_KEY is not set")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)  # keep the reference alive for the whole request
    parts: list[Any] = [types.Part.from_bytes(data=audio, mime_type=mime), PROMPT]
    r = client.models.generate_content(model=cfg.model, contents=parts)
    u = r.usage_metadata
    return (r.text or "").strip(), int(getattr(u, "prompt_token_count", 0) or 0), int(getattr(u, "candidates_token_count", 0) or 0)


def _openai(audio: bytes, cfg: SttCfg) -> tuple[str, int, int]:
    key = get_secret("OPENAI_API_KEY")
    if not key:
        raise SttError("OPENAI_API_KEY is not set")
    from openai import OpenAI

    r = OpenAI(api_key=key).audio.transcriptions.create(model=cfg.model, file=("audio.wav", audio))
    return r.text.strip(), 0, 0


_local_model: object = None


def _local(audio: bytes, cfg: SttCfg) -> tuple[str, int, int]:
    global _local_model
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise SttError("install the 'voice-local' extra to use local speech recognition") from e
    if _local_model is None:
        _local_model = WhisperModel(cfg.model if cfg.model.startswith(("tiny", "base", "small")) else "small", compute_type="int8")
    segments, _ = _local_model.transcribe(io.BytesIO(audio))  # type: ignore[attr-defined]
    return " ".join(s.text.strip() for s in segments).strip(), 0, 0

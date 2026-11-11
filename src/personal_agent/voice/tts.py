"""Spoken answers: markdown -> short sentences -> mp3 (edge-tts, free, needs internet). The UI only plays what this returns."""

from __future__ import annotations

import re

from personal_agent.graph.lang import detect_lang
from personal_agent.settings.settings import TtsCfg

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


def plan(markdown: str, cfg: TtsCfg) -> tuple[list[str], str]:
    """What to say and in which voice: (sentences, voice). No sentences = nothing worth speaking."""
    text = strip_markdown(markdown)[: cfg.max_chars]
    return split_sentences(text), pick_voice(text, cfg)


async def synth(sentence: str, voice: str, rate: str) -> bytes:
    """One sentence as mp3 bytes. Raises ImportError when the `voice` extra is not installed."""
    import edge_tts

    out = bytearray()
    async for chunk in edge_tts.Communicate(sentence, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            out += chunk["data"]
    return bytes(out)

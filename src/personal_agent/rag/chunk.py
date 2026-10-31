"""Structure-aware chunking: never crosses a heading/page boundary, keeps a small overlap inside a section."""

from __future__ import annotations

import re
from dataclasses import dataclass

from personal_agent.rag.loaders import Section


@dataclass
class Chunk:
    text: str
    heading: str
    page: int | None

    @property
    def indexed(self) -> str:
        """What is embedded / full-text indexed: the heading path gives the passage its context."""
        return f"{self.heading}\n{self.text}" if self.heading else self.text


def _pieces(text: str, size: int) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:  # a single huge paragraph: split at a sentence/space boundary
            cut = max(para.rfind(". ", 0, size), para.rfind(" ", 0, size), size // 2)
            out.append(para[:cut + 1].strip())
            para = para[cut + 1:].strip()
        if para:
            out.append(para)
    return out


def chunk(sections: list[Section], size: int = 900, overlap: int = 120) -> list[Chunk]:
    out: list[Chunk] = []
    for s in sections:
        cur = ""
        for piece in _pieces(s.text, size):
            if cur and len(cur) + len(piece) + 2 > size:
                out.append(Chunk(cur, s.heading, s.page))
                cur = (cur[-overlap:].split(" ", 1)[-1] + "\n\n" + piece) if overlap else piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
        if cur.strip():
            out.append(Chunk(cur, s.heading, s.page))
    return out

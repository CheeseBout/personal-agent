"""Document loaders: .md .txt .docx .pdf -> sections that keep the heading path and page number (for citations)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED = {".md", ".markdown", ".txt", ".docx", ".pdf"}
MAX_PDF_PAGES = 500
MAX_BYTES = 50_000_000


@dataclass
class Section:
    text: str
    heading: str = ""
    page: int | None = None


class UnsupportedDocument(ValueError):
    pass


def load(path: Path) -> list[Section]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedDocument(f"unsupported file type '{ext}' (supported: {', '.join(sorted(SUPPORTED))})")
    if path.stat().st_size > MAX_BYTES:
        raise UnsupportedDocument("file is larger than 50 MB")
    if ext in (".md", ".markdown"):
        return _markdown(path.read_text(encoding="utf-8", errors="replace"))
    if ext == ".txt":
        return [Section(path.read_text(encoding="utf-8", errors="replace"))]
    return _docx(path) if ext == ".docx" else _pdf(path)


def _markdown(text: str) -> list[Section]:
    out: list[Section] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    in_code = False

    def flush() -> None:
        if "".join(buf).strip():
            out.append(Section("\n".join(buf).strip(), " > ".join(h for _, h in stack)))
        buf.clear()

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2)))
        else:
            buf.append(line)
    flush()
    return out


MAX_UNZIPPED = 200_000_000
MAX_RATIO = 200


def _check_zip(path: Path) -> None:
    """Refuse decompression bombs BEFORE python-docx expands the archive into memory."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as e:
        raise UnsupportedDocument("not a valid .docx file") from e
    total = sum(i.file_size for i in infos)
    if total > MAX_UNZIPPED or (path.stat().st_size and total / path.stat().st_size > MAX_RATIO) or len(infos) > 5000:
        raise UnsupportedDocument("the document expands to an unreasonable size (possible decompression bomb)")


def _docx(path: Path) -> list[Section]:
    _check_zip(path)
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    out: list[Section] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush() -> None:
        if "".join(buf).strip():
            out.append(Section("\n".join(buf).strip(), " > ".join(h for _, h in stack)))
        buf.clear()

    for child in doc.element.body.iterchildren():  # document order: paragraphs and tables interleaved
        if child.tag.endswith("}p"):
            p = Paragraph(child, doc)
            style = (p.style.name if p.style is not None else "") or ""
            m = re.match(r"Heading (\d)", style)
            if m and p.text.strip():
                flush()
                level = int(m.group(1))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, p.text.strip()))
            elif p.text.strip():
                buf.append(p.text)
        elif child.tag.endswith("}tbl"):
            for row in Table(child, doc).rows:
                buf.append(" | ".join(c.text.strip() for c in row.cells))
    flush()
    return out


def _pdf(path: Path) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise UnsupportedDocument("the PDF is encrypted")
    out = []
    for i, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        text = (page.extract_text() or "").strip()
        if text:
            out.append(Section(text, page=i))
    if not out:  # shortcut: no OCR fallback yet (scanned PDFs), add one if scanned documents matter
        raise UnsupportedDocument("no extractable text (a scanned PDF? OCR is not available)")
    return out

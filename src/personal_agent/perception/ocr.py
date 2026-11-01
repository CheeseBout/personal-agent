"""Local OCR with the Windows built-in engine (Windows.Media.Ocr via PowerShell): free, offline, no extra dependency.

Languages come from the user's Windows profile (install the Vietnamese language pack for Vietnamese text). Coordinates are
returned in absolute screen pixels so a result can be used directly as a click target.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from personal_agent.perception.capture import to_png
from personal_agent.tools import procs

_SCRIPT = r"""
param([string]$path)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $null = $t.Wait(-1); $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $eng) { Write-Output '{"error":"no OCR language installed"}'; exit 0 }
$res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
$lines = foreach ($l in $res.Lines) {
  $x1 = [double]::MaxValue; $y1 = [double]::MaxValue; $x2 = 0.0; $y2 = 0.0
  foreach ($w in $l.Words) { $r = $w.BoundingRect
    if ($r.X -lt $x1) { $x1 = $r.X }; if ($r.Y -lt $y1) { $y1 = $r.Y }
    if ($r.X + $r.Width -gt $x2) { $x2 = $r.X + $r.Width }; if ($r.Y + $r.Height -gt $y2) { $y2 = $r.Y + $r.Height } }
  [pscustomobject]@{ text = $l.Text; x = [int]$x1; y = [int]$y1; w = [int]($x2 - $x1); h = [int]($y2 - $y1) }
}
[pscustomobject]@{ lang = $eng.RecognizerLanguage.LanguageTag; lines = @($lines) } | ConvertTo-Json -Compress -Depth 4
"""
MAX_PIXELS = 4000  # the engine refuses very large bitmaps


class OcrError(RuntimeError):
    pass


def parse(raw: str) -> dict[str, Any]:
    """PowerShell's JSON can carry raw control characters inside strings (found on a real window): tolerate them."""
    data = json.loads(raw.strip() or "{}", strict=False)
    return data if isinstance(data, dict) else {}


def _clean(text: str) -> str:
    return "".join(c for c in text if c.isprintable() or c == " ").strip()


def recognize(img: Image.Image, origin: tuple[int, int] = (0, 0), timeout: float = 25) -> tuple[str, list[dict[str, int | str]]]:
    """OCR an image -> (recognizer language tag, lines). `origin` is added to every coordinate (pass the captured rect's
    top-left to get screen pixels). Text in a language without an installed OCR pack comes out garbled: use the vision model then."""
    scale = 1.0
    if max(img.size) > MAX_PIXELS:
        scale = MAX_PIXELS / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    with tempfile.TemporaryDirectory(prefix="agent-ocr-") as d:
        png = Path(d) / "in.png"
        png.write_bytes(to_png(img))
        script = Path(d) / "ocr.ps1"
        script.write_text(_SCRIPT, encoding="utf-8")
        try:
            rc, out, err = procs.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), str(png)],
                                     Path(d), timeout)
        except subprocess.TimeoutExpired as e:
            raise OcrError("OCR timed out") from e
    if rc != 0:
        raise OcrError(err.decode("utf-8", errors="replace")[:200] or "OCR failed")
    data = parse(out.decode("utf-8", errors="replace"))
    if "error" in data:
        raise OcrError(str(data["error"]))
    lines = data.get("lines") or []
    if isinstance(lines, dict):  # ConvertTo-Json collapses a one-element array
        lines = [lines]
    inv = 1 / scale
    return str(data.get("lang", "?")), [
        {"text": _clean(str(ln["text"])), "x": int(ln["x"] * inv) + origin[0], "y": int(ln["y"] * inv) + origin[1],
         "w": int(ln["w"] * inv), "h": int(ln["h"] * inv)} for ln in lines if _clean(str(ln.get("text", "")))]

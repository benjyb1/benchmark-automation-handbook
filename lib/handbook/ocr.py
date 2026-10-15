"""The words in a picture, read by the Mac itself.

Apple Vision, run through JavaScript for Automation (`osascript -l JavaScript vision_ocr.js`), is
built into macOS: free, offline, nothing to install, and it reads English, French, German, Spanish
and more, with language correction on and its accurate setting. The script cuts a big picture into
pieces (see vision_ocr.js), so a poster 4,500 pixels wide is read in full; this module joins the
pieces and sets the words back into rows, a tab between table cells.

Where the Mac's Vision is not there (another system) tesseract is used if it is installed. With
neither, `recognise` returns None and the caller keeps the picture and says plainly that its words
could not be read.

`recognise(path)` is the one call: {"text", "engine", "confidence", "pieces"} or None. Tests patch
`engine` and `recognise`.
"""
from __future__ import annotations
import json
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("vision_ocr.js")
TIMEOUT_S = 240
_OSASCRIPT = "/usr/bin/osascript"


def have(tool: str) -> bool:
    """Whether a command-line tool is installed (tests patch this)."""
    return shutil.which(tool) is not None


def engine() -> str | None:
    """What reads pictures here: "vision" (macOS), else "tesseract" if installed, else None."""
    if sys.platform == "darwin" and (Path(_OSASCRIPT).is_file() or have("osascript")) and SCRIPT.is_file():
        return "vision"
    return "tesseract" if have("tesseract") else None


def missing_note() -> str:
    return ("No program on this computer can read the words in a picture (the Mac's built-in reader was not "
            "found, and tesseract is not installed), so the picture was kept but its text could not be read.")


# --- running the readers -----------------------------------------------------------------------

def _vision(path: Path) -> dict | None:
    osascript = _OSASCRIPT if Path(_OSASCRIPT).is_file() else (shutil.which("osascript") or "osascript")
    try:
        proc = subprocess.run([osascript, "-l", "JavaScript", str(SCRIPT), str(path)],
                              capture_output=True, text=True, timeout=TIMEOUT_S)
        got = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    if not isinstance(got, dict) or got.get("error") or not isinstance(got.get("passes"), list):
        return None
    pieces = merge_passes(got["passes"])
    return {"text": assemble(pieces), "engine": "vision", "pieces": len(pieces),
            "confidence": round(statistics.fmean(p["c"] for p in pieces), 3) if pieces else None}


def _tesseract(path: Path) -> dict | None:
    try:
        proc = subprocess.run(["tesseract", str(path), "stdout", "--psm", "3"], capture_output=True,
                              text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = "\n".join(line.rstrip() for line in proc.stdout.splitlines()).strip()
    return {"text": text, "engine": "tesseract", "pieces": len([ln for ln in text.splitlines() if ln.strip()]),
            "confidence": None}


def recognise(path: Path) -> dict | None:
    """The words in the picture at `path`: {"text", "engine", "confidence" (0 to 1, or None), "pieces"}.
    None when there is no reader or it could not open the file."""
    kind = engine()
    if kind == "vision":
        return _vision(Path(path))
    if kind == "tesseract":
        return _tesseract(Path(path))
    return None


# --- joining the pieces ------------------------------------------------------------------------

def _overlap(a: dict, b: dict) -> float:
    """How much of the smaller box lies inside the other, 0 to 1."""
    w = min(a["r"], b["r"]) - max(a["l"], b["l"])
    h = min(a["bot"], b["bot"]) - max(a["top"], b["top"])
    if w <= 0 or h <= 0:
        return 0.0
    small = min((a["r"] - a["l"]) * (a["bot"] - a["top"]), (b["r"] - b["l"]) * (b["bot"] - b["top"]))
    return (w * h) / small if small > 0 else 0.0


def _dedupe(pieces: list[dict]) -> list[dict]:
    """A piece found twice (in the overlap of two cuts, or by both passes) once: the longer reading wins."""
    pieces = sorted(pieces, key=lambda p: (p["top"], p["l"]))
    kept: list[dict] = []
    for p in pieces:
        twin = None
        for i in range(len(kept) - 1, -1, -1):
            k = kept[i]
            if p["top"] - k["top"] > 400:           # sorted by top: nothing earlier can overlap
                break
            if p["top"] < k["bot"] and _overlap(p, k) >= 0.5:
                twin = i
                break
        if twin is None:
            kept.append(p)
        elif len(p["t"]) > len(kept[twin]["t"]):
            kept[twin] = p
    return kept


def merge_passes(passes: list[list[dict]]) -> list[dict]:
    """The pieces of every pass as one list. The first pass is kept whole; from each later pass only
    the pieces that fall where the earlier ones found nothing (a figure the first cut dropped) are
    added. Pieces with no letters or figures are dropped."""
    merged: list[dict] = []
    for n, found in enumerate(passes):
        found = [p for p in found if isinstance(p, dict) and any(ch.isalnum() for ch in str(p.get("t")))]
        if n == 0:
            merged = _dedupe(found)
            continue
        fresh = []
        for p in sorted(found, key=lambda p: (p["top"], p["l"])):
            near = [m for m in merged if abs(m["top"] - p["top"]) < 400 and _overlap(p, m) >= 0.4]
            if not near:
                fresh.append(p)
        merged = _dedupe(merged + fresh)
    return merged


def assemble(pieces: list[dict]) -> str:
    """The text of a list of pieces ({"t", "l", "r", "top", "bot"}, top-left origin): the words in
    reading order. A table (many lines of three or more separate cells) is set row by row with a tab
    between cells; running text is read column by column when the page has two columns."""
    pieces = [p for p in pieces if str(p.get("t") or "").strip()]
    if not pieces:
        return ""
    med = statistics.median(p["bot"] - p["top"] for p in pieces) or 1.0
    rows = _rows(pieces, med)
    wide = sum(1 for r in rows if len(r) >= 3 and _has_gap(r, med))
    if rows and wide / len(rows) >= 0.3:
        return "\n".join(_row_text(r, med) for r in rows)
    # Running text: every piece on its own line, one column after the other.
    split = _gutter(pieces)
    groups = [pieces] if split is None else [[p for p in pieces if p["r"] <= split + 2],
                                            [p for p in pieces if p["r"] > split + 2]]
    out = []
    for group in groups:
        for row in _rows(group, med):
            out.append(_row_text(row, med, tabs=False))
        out.append("")
    return "\n".join(out).strip()


def _rows(pieces: list[dict], med: float) -> list[list[dict]]:
    """The pieces grouped into rows by the middle of each, top to bottom, each row left to right."""
    rows: list[dict] = []
    for p in sorted(pieces, key=lambda p: (p["top"] + p["bot"]) / 2):
        yc = (p["top"] + p["bot"]) / 2
        height = p["bot"] - p["top"]
        for row in rows[-3:]:
            if abs(yc - row["yc"]) < 0.5 * min(height, row["h"]) + 3:
                row["items"].append(p)
                n = len(row["items"])
                row["yc"] = (row["yc"] * (n - 1) + yc) / n
                row["h"] = min(row["h"], height)
                break
        else:
            rows.append({"yc": yc, "h": height, "items": [p]})
    return [sorted(r["items"], key=lambda p: p["l"]) for r in rows]


def _has_gap(row: list[dict], med: float) -> bool:
    return any(b["l"] - a["r"] > 1.5 * med for a, b in zip(row, row[1:]))


def _row_text(row: list[dict], med: float, tabs: bool = True) -> str:
    parts = [row[0]["t"].strip()]
    for a, b in zip(row, row[1:]):
        parts.append((("\t" if tabs and b["l"] - a["r"] > 1.5 * med else " ") + b["t"].strip()))
    return "".join(parts)


def _gutter(pieces: list[dict]) -> float | None:
    """The x of the empty band between two columns of running text, or None for one column: a band at
    least 2% of the page's width that no piece crosses, in the middle of the page, with at least a
    fifth of the pieces on each side."""
    left, right = min(p["l"] for p in pieces), max(p["r"] for p in pieces)
    width = right - left
    if width <= 0 or len(pieces) < 8:
        return None
    spans = sorted((p["l"], p["r"]) for p in pieces)
    best, edge = None, spans[0][1]
    for l, r in spans[1:]:
        if l - edge >= 0.02 * width and 0.3 * width <= edge - left <= 0.7 * width:
            if best is None or l - edge > best[1] - best[0]:
                best = (edge, l)
        edge = max(edge, r)
    if best is None:
        return None
    side = sum(1 for p in pieces if p["r"] <= best[0] + 2)
    if side < len(pieces) / 5 or len(pieces) - side < len(pieces) / 5:
        return None
    return best[0]

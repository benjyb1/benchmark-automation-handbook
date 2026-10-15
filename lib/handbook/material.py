"""The owner's set-up material: the files dropped into the Set-up tab (`inbox/uploads/`), read as text
for the page's Claude (the fit check) and for the background set-up draft.

Every kind in readers.py is read here as it is for a document (a PDF page by page, pictures by OCR), with two
richer readings of the owner's own: Excel (.xlsx) sheet by sheet as tab-separated rows, and web pages with
their pictures' names and their links (see `_xlsx` and `_html`).
"""
from __future__ import annotations
import html
import re
from pathlib import Path

from . import fetch, readers
from .folder import Project

FOLDER = ("inbox", "uploads")
KINDS = dict(readers.OWNER_LABELS)
# A scorecard can be the whole of the owner's material (EggTrack's is 602 rows), and the draft takes
# its row list from it, so a sheet is read whole up to this many rows; the page's chat reads it in parts.
ROWS_PER_SHEET = 5000


def files(project: Project) -> list[Path]:
    folder = project.path.joinpath(*FOLDER)
    return sorted(p for p in folder.glob("*--*") if p.is_file()) if folder.is_dir() else []


def name_of(path: Path) -> str:
    """The file's name as the owner gave it (without the upload id)."""
    return path.name.split("--", 1)[-1]


def _cell(cell) -> str:
    """A cell as the owner sees it in Excel: a share formatted as a percentage reads 73%, not 0.73, and a
    date reads 2025-07-31, not a timestamp."""
    v = getattr(cell, "value", None)
    if v is None:
        return ""
    fmt = str(getattr(cell, "number_format", "") or "")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and "%" in fmt:
        return f"{round(v * 100, 4):g}%"
    if hasattr(v, "isoformat"):
        return v.date().isoformat() if hasattr(v, "date") and not any(c in fmt.lower() for c in "hs") else v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def xlsx_sheets(path: Path) -> list[tuple[str, str]]:
    """[(sheet title, its rows as tab-separated lines)] for every sheet with something in it."""
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    out = []
    for sheet in book.worksheets:
        lines = []
        for n, row in enumerate(sheet.iter_rows()):
            if n >= ROWS_PER_SHEET:
                lines.append(f"(sheet continues beyond {ROWS_PER_SHEET} rows)")
                break
            cells = [_cell(c) for c in row]
            if any(cells):
                lines.append("\t".join(cells).rstrip())
        if lines:
            out.append((sheet.title, "\n".join(lines)))
    return out


def _xlsx(path: Path) -> str:
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    out = []
    for sheet in book.worksheets:
        lines, width, more = [], 0, False
        for n, row in enumerate(sheet.iter_rows()):
            if n >= ROWS_PER_SHEET:
                more = True
                break
            cells = [_cell(c) for c in row]
            if any(cells):
                lines.append("\t".join(cells).rstrip())
                width = max(width, max(i + 1 for i, c in enumerate(cells) if c))
        out.append(f"## Sheet: {sheet.title} ({len(lines)}{'+' if more else ''} rows, {width} columns)")
        out.extend(lines)
        if more:
            out.append(f"(sheet continues beyond {ROWS_PER_SHEET} rows)")
    return "\n".join(out)


def _html(data: bytes) -> str:
    """A saved web page as the owner sees it, for set-up only: `fetch.html_text` with each picture as its alt text
    or file name and each link followed by where it goes, and a closing note when the page builds content with a
    script, which a saved copy does not hold (VACI's tables)."""
    raw = fetch.decode_html(data)
    scripts = re.findall(r"(?is)<script[^>]*>(.*?)</script>", raw)
    text = fetch.html_text(raw, links=True, pictures=True)
    script_chars = sum(len(x) for x in scripts)
    if script_chars > 2000 and script_chars > len(text):
        text += ("\n\n(Note: this page builds part of its content with a script, so tables or figures drawn "
                 "that way are missing from this saved copy. Ask the owner for them as a file, or read the "
                 "page's data another way.)")
    return text


def read_text(path: Path) -> str:
    """The whole file as text; raises ValueError for a kind it cannot read."""
    ext = path.suffix.lower()
    if ext == ".xlsx":
        return _xlsx(path)
    if ext in (".html", ".htm"):
        return _html(path.read_bytes())
    if ext in (".txt", ".md", ".csv"):
        return fetch.decode_html(path.read_bytes())
    return readers.text_of(path)


def listing(project: Project) -> list[dict]:
    """[{id, name, kind, chars}] for every uploaded file (chars None when it cannot be read)."""
    out = []
    for p in files(project):
        try:
            chars = len(read_text(p))
        except Exception:  # noqa: BLE001 (a damaged file is listed, not fatal)
            chars = None
        out.append({"id": p.name.split("--")[0], "name": name_of(p), "kind": KINDS.get(p.suffix.lower(), "other"),
                    "chars": chars})
    return out


def read(project: Project, file_id: str, start: int = 0, size: int = 20000) -> dict:
    """One file's text from `start`, at most `size` characters: {id, name, text, start, end, total}."""
    path = next((p for p in files(project) if p.name.split("--")[0] == file_id), None)
    if path is None:
        raise ValueError(f"There is no uploaded file {file_id!r}.")
    text = read_text(path)
    start = max(0, int(start))
    end = min(len(text), start + max(1000, min(int(size), 60000)))
    return {"id": file_id, "name": name_of(path), "text": text[start:end], "start": start, "end": end,
            "total": len(text)}

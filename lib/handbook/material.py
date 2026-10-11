"""The owner's set-up material: the files dropped into the Set-up tab (`inbox/uploads/`), read as text
for the page's Claude (the fit check) and for the background set-up draft.

PDFs through `prepare.pdfium_read`, Excel (.xlsx) sheet by sheet as tab-separated rows, Word (.docx)
paragraph by paragraph from its XML, web pages through `fetch.extract_text_html`, and text as it is.
Old .xls and .doc files are not read: the owner saves them as .xlsx or .docx.
"""
from __future__ import annotations
import html
import re
import zipfile
from pathlib import Path

from . import fetch, prepare
from .folder import Project

FOLDER = ("inbox", "uploads")
KINDS = {".pdf": "PDF", ".xlsx": "Excel", ".docx": "Word", ".csv": "table", ".txt": "text", ".md": "text",
         ".html": "web page", ".htm": "web page"}
ROWS_PER_SHEET = 400


def files(project: Project) -> list[Path]:
    folder = project.path.joinpath(*FOLDER)
    return sorted(p for p in folder.glob("*--*") if p.is_file()) if folder.is_dir() else []


def name_of(path: Path) -> str:
    """The file's name as the owner gave it (without the upload id)."""
    return path.name.split("--", 1)[-1]


def _xlsx(path: Path) -> str:
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    out = []
    for sheet in book.worksheets:
        out.append(f"## Sheet: {sheet.title}")
        for n, row in enumerate(sheet.iter_rows(values_only=True)):
            if n >= ROWS_PER_SHEET:
                out.append(f"(sheet continues beyond {ROWS_PER_SHEET} rows)")
                break
            cells = ["" if v is None else str(v).strip() for v in row]
            if any(cells):
                out.append("\t".join(cells).rstrip())
    return "\n".join(out)


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    paras = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p))
        paras.append(html.unescape(text))
    return "\n".join(t for t in paras if t.strip())


def read_text(path: Path) -> str:
    """The whole file as text; raises ValueError for a kind it cannot read."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        if prepare.pdf_reader() != "pdfium":
            raise ValueError("PDF reading needs the tool's launcher.")
        texts, _ = prepare.pdfium_read(path)
        return "\n".join(f"[page {i}]\n{t}" for i, t in enumerate(texts, 1))
    if ext == ".xlsx":
        return _xlsx(path)
    if ext == ".docx":
        return _docx(path)
    if ext in (".html", ".htm"):
        return fetch.extract_text_html(path.read_bytes())
    if ext in (".txt", ".md", ".csv"):
        return fetch.decode_html(path.read_bytes())
    raise ValueError(f"{ext or 'This kind of'} files can't be read: save it as PDF, Word (.docx) or Excel (.xlsx).")


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

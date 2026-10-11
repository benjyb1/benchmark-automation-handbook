"""Reading a saved document into `pages.json`: one entry per PDF page, one page for a web page or a
text file.

`guess_language`, `_pdf_pages` and `_pdf_dates` are copied from scripts/prep.py (here
`pdf_page_count` and `pdf_info`); `decode_html` lives in fetch.py. PDFs are read with pypdfium2
(PDFium, the reader in Chrome), page by page, with no other program to install. Without it (the tool
run with a bare `python3` rather than its launcher) poppler is used if it is installed: `pdfinfo`
for the page count, then `pdftotext -f N -l N -layout` for each page. On the Pecking Order fixture
pypdfium2 found 813 of 828 cited quotes against poppler's 797, lost none that poppler found, and was
twelve times faster.
"""
from __future__ import annotations
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import fetch
from .folder import HandbookError, read_json, write_json
from .quotecheck import capture_id
from .shapes import is_date

try:
    import pypdfium2 as _pdfium
except ImportError:                     # run without the launcher: poppler, if it is installed
    _pdfium = None

PDF_MISSING = ("PDF text needs pypdfium2. Run the tool through its launcher (lib/hb), which "
               "installs it, or install poppler (brew install poppler).")
KINDS = {".pdf": "pdf", ".html": "html", ".htm": "html", ".txt": "text"}

# A saved web page whose text is no more than this, or that asks for JavaScript, is a shell: the
# words the finder saw were drawn by a script and are not in the copy. In the Pecking Order dev
# fixture five such shells were cited by 40 answers, every one of them then a quote not found.
STUB_CHARS = 120
_NEEDS_SCRIPT = re.compile(r"enable javascript|javascript (?:is )?(?:required|disabled)|"
                           r"you need to enable javascript|please enable javascript", re.I)


def is_stub(texts) -> bool:
    """Whether a document's page texts are a script shell rather than the page's words."""
    text = " ".join(t or "" for t in texts).strip()
    return len(text) <= STUB_CHARS or bool(_NEEDS_SCRIPT.search(text))

# Small stopword tables; script detection handles the non-Latin cases. (Copied from scripts/prep.py.)
_STOP = {
    "en": {"the", "and", "of", "to", "in", "that", "with", "for", "you", "your", "is", "are"},
    "es": {"de", "la", "que", "el", "en", "y", "los", "las", "una", "por", "con", "para", "son"},
    "pt": {"de", "que", "não", "uma", "para", "com", "os", "as", "por", "mais", "dos", "das", "se", "ao"},
    "fr": {"le", "la", "les", "des", "et", "une", "pour", "dans", "est", "que", "du", "au", "aux"},
    "de": {"und", "der", "die", "das", "nicht", "mit", "ist", "sie", "ein", "eine", "auch", "für"},
    "it": {"il", "di", "che", "della", "per", "con", "una", "sono", "gli", "alla", "del", "nel"},
    "nl": {"de", "het", "een", "van", "en", "niet", "met", "zijn", "voor", "dat", "ook", "je"},
    "sv": {"och", "att", "det", "som", "för", "med", "inte", "är", "till", "den", "av", "på"},
    "da": {"og", "at", "det", "som", "til", "med", "ikke", "af", "på", "der", "den", "kan"},
    "no": {"og", "det", "som", "til", "med", "ikke", "av", "på", "den", "kan", "å", "er"},
    "fi": {"ja", "on", "että", "ei", "myös", "tai", "kun", "sekä", "voi", "ovat", "mutta"},
    "pl": {"i", "w", "nie", "na", "się", "jest", "z", "do", "oraz", "które", "jak", "lub"},
    "tr": {"ve", "bir", "bu", "için", "ile", "olarak", "da", "de", "gibi", "daha", "olan"},
}


def guess_language(text: str) -> str:
    """Heuristic: script first, then stopword hits among the first few thousand words.
    Returns a language code, or "unknown"."""
    if not text or not text.strip():
        return "unknown"
    sample = text[:20000]
    if re.search(r"[؀-ۿ]", sample):
        return "ar"
    if re.search(r"[぀-ヿ]", sample):
        return "ja"
    if re.search(r"[가-힯]", sample):
        return "ko"
    if re.search(r"[一-鿿]", sample):
        return "zh"
    if re.search(r"[Ѐ-ӿ]", sample):
        return "ru"          # Cyrillic: Russian, Bulgarian, Macedonian, Mongolian all land here
    if re.search(r"[฀-๿]", sample):
        return "th"
    if re.search(r"[Ⴀ-ჿ]", sample):
        return "ka"
    if re.search(r"[֐-׿]", sample):
        return "he"
    words = re.findall(r"[^\W\d_]+", sample.lower(), flags=re.UNICODE)[:4000]
    if not words:
        return "unknown"
    best, best_hits = "unknown", 0
    for lang, stops in _STOP.items():
        hits = sum(1 for w in words if w in stops)
        if hits > best_hits:
            best, best_hits = lang, hits
    return best if best_hits >= 3 else "unknown"


# --- PDFs ---------------------------------------------------------------------------------------

def have(tool: str) -> bool:
    """Whether a command-line tool is installed (tests patch this)."""
    return shutil.which(tool) is not None


def pdf_reader() -> str | None:
    """What reads PDFs here: "pdfium" (pypdfium2), else "poppler" (pdftotext and pdfinfo), else None."""
    if _pdfium is not None:
        return "pdfium"
    return "poppler" if have("pdftotext") and have("pdfinfo") else None


# PDFium marks a word broken at the end of a line with \x02 (poppler prints a hyphen) and gives
# other control characters for glyphs it cannot map to a letter; poppler drops those.
_PDFIUM_JUNK = re.compile(r"[\x00-\x01\x03-\x08\x0b\x0c\x0e-\x1f]")


def _pdfium_date(value: str) -> str:
    """A PDF date ("D:20250301120000+00'00'") in the ISO form `pdf_date` reads; anything else as given."""
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2}))?", value or "")
    if not m:
        return value
    day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return f"{day}T{m.group(4)}:{m.group(5)}:{m.group(6)}" if m.group(4) else day


def pdfium_read(path: Path) -> tuple[list[str], dict]:
    """(the text of each page, CreationDate/ModDate/Title/Producer) with pypdfium2. No pages when
    the PDF cannot be opened (damaged, or locked with a password)."""
    try:
        pdf = _pdfium.PdfDocument(str(path))
    except _pdfium.PdfiumError:
        return [], {}
    try:
        texts = []
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            text = textpage.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n")
            texts.append(_PDFIUM_JUNK.sub("", text.replace("\x02", "-")).rstrip())
            textpage.close()
            page.close()
        meta = pdf.get_metadata_dict(skip_empty=True)
    finally:
        pdf.close()
    info = {key: _pdfium_date(meta[key]) if key.endswith("Date") else meta[key]
            for key in ("CreationDate", "ModDate", "Title", "Producer") if meta.get(key)}
    return texts, info


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def _pdfinfo(path: Path) -> str:
    return _run(["pdfinfo", "-isodates", str(path)]).stdout


def pdf_page_count(path: Path) -> int:
    """The number of pages a PDF has (0 when pdfinfo cannot read it)."""
    m = re.search(r"^Pages:\s+(\d+)", _pdfinfo(path), flags=re.M)
    return int(m.group(1)) if m else 0


def pdf_info(path: Path) -> dict:
    """CreationDate, ModDate, Title and Producer from the PDF's metadata, where present."""
    out = _pdfinfo(path)
    info = {}
    for key in ("CreationDate", "ModDate", "Title", "Producer"):
        m = re.search(rf"^{key}:\s+(.+)$", out, flags=re.M)
        if m:
            info[key] = m.group(1).strip()
    return info


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def pdf_date(info: dict) -> str | None:
    """The PDF's own date as YYYY-MM-DD (creation date, else modified date), or None. Reads the
    ISO form pdfinfo gives with -isodates and the older "Sat Mar  1 12:00:00 2025" form."""
    for key in ("CreationDate", "ModDate"):
        value = info.get(key) or ""
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
        if m:
            day = m.group(0)
        else:
            m = re.search(r"\b([A-Za-z]{3})\w*\s+(\d{1,2})\s+[\d:]+\s+(\d{4})\b", value)
            month = _MONTHS.get(m.group(1).lower()) if m else None
            if not month:
                continue
            day = f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"
        if is_date(day):
            return day
    return None


def pdf_page_texts(path: Path, pages: int) -> list[str]:
    """The text of each page, layout kept: `pdftotext -f N -l N -layout` once per page."""
    texts = []
    for n in range(1, pages + 1):
        got = _run(["pdftotext", "-f", str(n), "-l", str(n), "-layout", "-q", str(path), "-"])
        if got.returncode != 0:
            raise HandbookError(f"pdftotext could not read page {n} of {path.name}.")
        texts.append(got.stdout.replace("\f", "").rstrip())
    return texts


# --- one document -------------------------------------------------------------------------------

def find_original(project, doc: str) -> Path | None:
    """The saved copy of a document: meta.json's `file`, else `original.*` in its folder."""
    folder = project.doc_dir(doc)
    named = (read_json(folder / "meta.json", {}) or {}).get("file")
    if named and (folder / named).is_file():
        return folder / named
    found = sorted(p for p in folder.glob("original.*") if p.suffix != ".tmp")
    return found[0] if found else None


def pending_documents(project) -> list[str]:
    """Ids of documents with a saved copy that is not yet read (no pages.json, or not kept)."""
    return [doc for doc, meta in project.documents().items()
            if find_original(project, doc) is not None
            and not ((project.doc_dir(doc) / "pages.json").is_file() and meta.get("kept") is True)]


def unsaved_documents(project) -> list[str]:
    """Ids of documents known by address but with no saved copy and no text."""
    return [doc for doc in project.documents()
            if find_original(project, doc) is None and not (project.doc_dir(doc) / "pages.json").is_file()]


def prepare_document(project, doc: str) -> dict:
    """Read a document's saved copy into pages.json and update meta.json (`kept`, `file`, `pages`,
    `language`, `capture`, and `date` with `date_basis` "pdf metadata" when the PDF carries one
    and no other date was set by the owner or read from the page). The pages are also kept as
    `captures/<capture id>.json`, named by their content, so every text a scorer was ever given
    stays on file and the quote check can read what the scorer read after the copy changes.
    Returns {"doc", "kind", "pages", "empty_pages", "language", "date", "capture"}. Raises
    HandbookError."""
    folder = project.doc_dir(doc)
    meta = read_json(folder / "meta.json", None)
    if not isinstance(meta, dict):
        raise HandbookError(f"There is no document {doc} in this project.")
    path = find_original(project, doc)
    if path is None:
        raise HandbookError(f"{doc} has no saved copy to read.")
    kind = KINDS.get(path.suffix.lower())
    if kind is None:
        raise HandbookError(f"{doc}: the handbook cannot read {path.suffix or 'this kind of'} files "
                            "yet. Save it as a PDF or a text file and add that.")
    pdf_date_found = None
    if kind == "pdf":
        reader = pdf_reader()
        if reader is None:
            raise HandbookError(PDF_MISSING)
        try:
            if reader == "pdfium":
                texts, info = pdfium_read(path)
            else:
                count = pdf_page_count(path)
                texts, info = (pdf_page_texts(path, count), pdf_info(path)) if count > 0 else ([], {})
            if not texts:
                raise HandbookError(f"{doc}: the PDF could not be read (damaged, or locked with a password).")
            pdf_date_found = pdf_date(info)
        except subprocess.TimeoutExpired:
            raise HandbookError(f"{doc}: reading the PDF took too long, so it was skipped. It may be very "
                                "large or damaged; try it again with --doc, or save a smaller copy.") from None
    elif kind == "html":
        texts = [fetch.extract_text_html(path.read_bytes())]
    else:
        texts = [fetch.decode_html(path.read_bytes()).strip()]
    pages = [{"page": i, "text": text} for i, text in enumerate(texts, 1)]
    language = guess_language("\n".join(texts))
    capture = capture_id(texts)

    # The pages being replaced may have gone into a kit before captures were recorded: file them
    # first, so the quote check can still read what that scorer read.
    old_pages = (read_json(folder / "pages.json", {}) or {}).get("pages") or []
    old_pages = [p for p in old_pages if isinstance(p, dict)]
    if old_pages:
        old_id = capture_id(p.get("text") or "" for p in old_pages)
        if not (folder / "captures" / f"{old_id}.json").is_file():
            write_json(folder / "captures" / f"{old_id}.json", {"pages": old_pages})
    write_json(folder / "pages.json", {"pages": pages})            # pages first, then meta
    if not (folder / "captures" / f"{capture}.json").is_file():   # content-addressed: never overwritten
        write_json(folder / "captures" / f"{capture}.json", {"pages": pages})
    meta["kept"], meta["file"], meta["pages"], meta["capture"] = True, path.name, len(pages), capture
    if language != "unknown":
        meta["language"] = language
    if pdf_date_found and (meta.get("date") is None or meta.get("date_basis") in (None, "pdf metadata",
                                                                                   "http header")):
        meta["date"], meta["date_basis"] = pdf_date_found, "pdf metadata"
    write_json(folder / "meta.json", meta)
    return {"doc": doc, "kind": kind, "pages": len(pages),
            "empty_pages": sum(1 for t in texts if not t.strip()),
            "language": meta.get("language"), "date": meta.get("date"), "capture": capture,
            "stub": kind == "html" and is_stub(texts)}

"""Reading a saved document into `pages.json`: one entry per page (a PDF page, a slide, a sheet), one
page for a web page, a picture or a text file.

The reading itself, kind by kind, is readers.py; this module files the result: `pages.json`, the
pictures kept for the scorer (`images/`), the capture, and `meta.json` (`kept`, `file`, `pages`,
`language`, `text_source` "ocr" when words were read from a picture, and the PDF's own date).
`guess_language` and `pdf_date` are copied from scripts/prep.py. PDFs are read with pypdfium2 (PDFium,
the reader in Chrome), with poppler as the fallback (see readers.py); on the Pecking Order fixture
pypdfium2 found 813 of 828 cited quotes against poppler's 797, lost none that poppler found, and was
twelve times faster.
"""
from __future__ import annotations
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import fetch, readers
from .folder import HandbookError, read_json, write_json
from .quotecheck import capture_id
from .shapes import is_date
# The PDF reading moved to readers.py; these names stay importable from here.
from .readers import (PDF_MISSING, have, pdf_info, pdf_page_count, pdf_page_texts, pdf_reader,  # noqa: F401
                      pdfium_read, _pdfium_date)

# A saved web page whose text is no more than this, or that asks for JavaScript, is a shell: the
# words the finder saw were drawn by a script and are not in the copy. In the Pecking Order dev
# fixture five such shells were cited by 40 answers, every one of them then a quote not found.
STUB_CHARS = 120
_NEEDS_SCRIPT = re.compile(r"enable javascript|javascript (?:is )?(?:required|disabled)|"
                           r"you need to enable javascript|please enable javascript", re.I)


# A web page or text file longer than this is split into pages of about this size, at a sentence or
# line break. Measured 15 Oct 2026: pages of 10,000 characters raised scorer cost (the Animal Protection
# Index re-test +33%, Seafood Watch's Norway kit +27%: more pages, more steps), so only very long pages
# (Brazil's Constitution, 669,000 characters) are split, into pages of 100,000.
PAGE_CHARS = 100_000


def split_long(text: str, size: int = PAGE_CHARS) -> list[str]:
    """`text` as one page, or as pages of at most about `size` characters cut after a full stop or a
    line break (a space when there is neither), with nothing lost or added but the cut's whitespace."""
    text = text or ""
    if len(text) <= size * 1.5:
        return [text]
    pages, start = [], 0
    while len(text) - start > size * 1.5:
        window = text[start:start + size]
        cut = max(window.rfind(". "), window.rfind("\n"), window.rfind("? "), window.rfind("! "))
        if cut < size // 2:
            cut = window.rfind(" ")
        if cut < size // 2:
            cut = size - 1
        pages.append(text[start:start + cut + 1].strip())
        start += cut + 1
    pages.append(text[start:].strip())
    return [pg for pg in pages if pg]


def is_stub(texts) -> bool:
    """Whether a document's page texts are a script shell rather than the page's words."""
    text = " ".join(t or "" for t in texts).strip()
    # "JavaScript is disabled" on a page with its words is a banner, not a shell (ALI, 14 Oct 2026: both
    # ASC standards, 340 KB and 675 KB of text, were thrown away for it and an older capture kept).
    return len(text) <= STUB_CHARS or (len(text) <= 20_000 and bool(_NEEDS_SCRIPT.search(text)))

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
    # A script decides only when it is a real share of the letters: one Cyrillic word in a language menu
    # marked every EUR-Lex page Russian, and a few kana made a page Japanese (15 Oct 2026).
    letters = max(1, len(re.findall(r"[^\W\d_]", sample)))

    def share(pattern: str) -> float:
        return len(re.findall(pattern, sample)) / letters
    if share(r"[぀-ヿ]") > 0.05:
        return "ja"
    for pattern, code in ((r"[؀-ۿ]", "ar"), (r"[가-힯]", "ko"), (r"[一-鿿]", "zh"), (r"[Ѐ-ӿ]", "ru"),
                          (r"[฀-๿]", "th"), (r"[Ⴀ-ჿ]", "ka"), (r"[֐-׿]", "he")):
        if share(pattern) > 0.2:
            return code   # Cyrillic: Russian, Bulgarian, Macedonian, Mongolian all land on "ru"
    words = re.findall(r"[^\W\d_]+", sample.lower(), flags=re.UNICODE)[:4000]
    if not words:
        return "unknown"
    best, best_hits = "unknown", 0
    for lang, stops in _STOP.items():
        hits = sum(1 for w in words if w in stops)
        if hits > best_hits:
            best, best_hits = lang, hits
    return best if best_hits >= 3 else "unknown"


# --- dates ---------------------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


# Dates a PDF tool writes when it has none: the turn of 2000 (Webflow's copies of Mizuho's reports carry
# 1999-12-31T23:00:00Z, midnight in Central Europe; BankTrack, 20 Oct 2026) and the Unix epoch. PDF itself
# dates from 1993, so anything earlier is not a real file date either.
PLACEHOLDER_DATES = ("1999-12-31", "2000-01-01", "1970-01-01")
EARLIEST_PDF = "1993-01-01"


def pdf_date(info: dict) -> str | None:
    """The PDF's own date as YYYY-MM-DD (creation date, else modified date), or None. Reads the
    ISO form pdfinfo gives with -isodates and the older "Sat Mar  1 12:00:00 2025" form. A placeholder
    date (PLACEHOLDER_DATES, or before EARLIEST_PDF) is no date."""
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
        if is_date(day) and day not in PLACEHOLDER_DATES and day >= EARLIEST_PDF:
            return day
    return None


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


IMAGES = "images"       # documents/<doc>/images/: the pictures kept for the scorer (see write_pictures)


def picture_name(page: int, piece: int, pieces: int) -> str:
    """images/p004.jpg, or images/p004-2.jpg for the second of several pieces of one tall picture."""
    return f"{IMAGES}/p{page:03d}{'' if pieces == 1 else f'-{piece}'}.jpg"


def write_pictures(folder: Path, pictures: dict) -> dict:
    """Write the pictures a reading kept ({page: [JPEG bytes]}) to `images/` (replacing any earlier set) and
    return {page: [names relative to the document's folder]}."""
    target = folder / IMAGES
    if target.is_dir():
        shutil.rmtree(target)
    names = {}
    for page, pieces in sorted(pictures.items()):
        for n, data in enumerate(pieces, 1):
            name = picture_name(page, n, len(pieces))
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
            names.setdefault(page, []).append(name)
    return names


def prepare_document(project, doc: str) -> dict:
    """Read a document's saved copy into pages.json and update meta.json (`kept`, `file`, `pages`,
    `language`, `capture`, `text_source` "ocr" and `ocr_pages` when words were read from a picture, and
    `date` with `date_basis` "pdf metadata" when the PDF carries one and no other date was set by the owner
    or read from the page). The pages are also kept as `captures/<capture id>.json`, named by their
    content, so every text a scorer was ever given stays on file and the quote check can read what the
    scorer read after the copy changes. A page read from a picture carries `ocr: true`, and the pictures
    kept for the scorer are in `images/` and named in the page's `images`.
    Returns {"doc", "kind", "pages", "empty_pages", "language", "date", "capture", "stub", "ocr_pages",
    "pictures", "unread_pictures", "notes", "ocr_quality"}. Raises HandbookError."""
    folder = project.doc_dir(doc)
    meta = read_json(folder / "meta.json", None)
    if not isinstance(meta, dict):
        raise HandbookError(f"There is no document {doc} in this project.")
    path = find_original(project, doc)
    if path is None:
        raise HandbookError(f"{doc} has no saved copy to read.")
    if readers.kind_for_name(path.name) is None:
        raise HandbookError(f"{doc}: " + (readers.cannot_read(path.name) or
                                          f"the tool cannot read {path.suffix or 'this kind of'} files. It reads "
                                          f"{readers.READABLE_WORDS} files."))
    try:
        reading = readers.read(path, evidence=True)
    except HandbookError as e:
        if str(e) == PDF_MISSING:
            raise                                     # said once, not once per PDF
        raise HandbookError(f"{doc}: {e}") from None
    except subprocess.TimeoutExpired:
        raise HandbookError(f"{doc}: reading the {readers.LABELS.get(readers.kind_for_name(path.name), 'file')} "
                            "took too long, so it was skipped. It may be very large or damaged; try it again "
                            "with --doc, or save a smaller copy.") from None
    kind, texts = reading.kind, reading.pages
    pdf_date_found = pdf_date(reading.info) if kind == "pdf" else None
    pages = [{"page": i, "text": text} for i, text in enumerate(texts, 1)]
    names = write_pictures(folder, reading.pictures)
    for n in reading.ocr_pages:
        pages[n - 1]["ocr"] = True
    for n, files in names.items():
        pages[n - 1]["images"] = files
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
    if reading.ocr_pages:
        meta["text_source"], meta["ocr_pages"] = "ocr", list(reading.ocr_pages)
    else:
        meta.pop("ocr_pages", None)
        if meta.get("text_source") == "ocr":
            meta.pop("text_source")
    if language != "unknown":
        meta["language"] = language
    if pdf_date_found and str(meta.get("fetched") or "")[:10] and pdf_date_found >= str(meta["fetched"])[:10]:
        # A PDF stamped when it was downloaded (a repository or viewer regenerating it: the SAMS review,
        # CPF's 2021 report, 14 Oct 2026) says nothing about when it was written.
        pdf_date_found = None
    if pdf_date_found and (meta.get("date") is None or meta.get("date_basis") in (None, "pdf metadata",
                                                                                   "http header")):
        meta["date"], meta["date_basis"] = pdf_date_found, "pdf metadata"
    elif meta.get("date_basis") == "pdf metadata" and (meta.get("date") in PLACEHOLDER_DATES
                                                       or str(meta.get("date") or "") < EARLIEST_PDF):
        meta["date"], meta["date_basis"] = None, None       # a placeholder kept by an older tool
    write_json(folder / "meta.json", meta)
    return {"doc": doc, "kind": kind, "pages": len(pages),
            "empty_pages": sum(1 for t in texts if not t.strip()),
            "language": meta.get("language"), "date": meta.get("date"), "capture": capture,
            "stub": kind == "html" and is_stub(texts), "ocr_pages": len(reading.ocr_pages),
            "pictures": sum(len(v) for v in names.values()), "unread_pictures": reading.unread_pictures,
            "notes": reading.notes, "ocr_quality": reading.ocr_quality}

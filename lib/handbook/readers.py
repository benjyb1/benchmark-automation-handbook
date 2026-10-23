"""One reader for every kind of evidence file: what kinds there are, how to tell them apart, and how to
turn each into pages of text.

Three entry points take files: `hb fetch` (bytes and a content type from the web), `hb add-document`
and the Set-up tab's uploads (a name and bytes from the owner). They all ask this module, so a kind
added here works in all of them. `kind_of(...)` says what a file is, `read(path)` returns a `Reading`
(its pages, which of them were read from a picture, the pictures worth showing a scorer, and notes for
the owner), and `UPLOAD_ACCEPT` is the list of extensions an upload box offers.

Kinds (the order of `KINDS`): pdf, image (png, jpg, webp, gif, heic, tiff), html, text (txt, md, csv,
tsv), sheet (xlsx), json, xml, docx, pptx, doc, rtf, odt, ods, odp, xls, ppt, eml, msg. Video and audio
are not read yet: `not_yet` says so in plain words. The readers for the Office and mail kinds live in
office.py; PDFs are read with pypdfium2 (PDFium, the reader in Chrome) page by page, with poppler as the
fallback when the tool is run without its launcher.

Pictures: an image file is read with the Mac's own text reader (ocr.py). A PDF page with no text of its
own is drawn and read the same way, and so is a page that is mostly picture (a grid of logos) when it has
little text of its own. Such text is marked as read by OCR (`Reading.ocr_pages`, `text_source` "ocr" in
meta.json) because it can hold a wrong letter. For each image, and each PDF page that is mostly picture or
was scanned, a small JPEG is kept (`Reading.pictures`) so the scorer can look at it where its position or
look is the evidence.
"""
from __future__ import annotations
import io
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import edition, ocr
from .folder import HandbookError

try:
    import pypdfium2 as _pdfium
except ImportError:                     # run without the launcher: poppler, if it is installed
    _pdfium = None

try:
    from PIL import Image as _Image
except ImportError:                     # the launcher installs Pillow; a bare python3 may not have it
    _Image = None

PDF_MISSING = ("PDF text needs pypdfium2. Run the tool through its launcher (lib/hb), which "
               "installs it, or install poppler (brew install poppler).")

# --- the kinds ---------------------------------------------------------------------------------

# kind -> (what the owner calls it, the extension the original is saved with, extensions it is
# recognised by). The extension of an image is the file's own (see `stored_extension`).
KINDS = {
    "pdf": ("PDF", ".pdf", (".pdf",)),
    "image": ("picture", None, (".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif", ".tif", ".tiff")),
    "html": ("web page", ".html", (".html", ".htm", ".xhtml")),
    "text": ("text", ".txt", (".txt", ".text", ".md", ".csv", ".tsv")),
    "sheet": ("spreadsheet", ".xlsx", (".xlsx", ".xlsm")),
    "docx": ("Word document", ".docx", (".docx",)),
    "pptx": ("PowerPoint file", ".pptx", (".pptx",)),
    "json": ("JSON data", ".json", (".json", ".jsonl")),
    "xml": ("XML data", ".xml", (".xml", ".svg")),
    "doc": ("Word document (old)", ".doc", (".doc",)),
    "rtf": ("rich text file", ".rtf", (".rtf",)),
    "odt": ("OpenDocument text", ".odt", (".odt",)),
    "ods": ("OpenDocument spreadsheet", ".ods", (".ods",)),
    "odp": ("OpenDocument presentation", ".odp", (".odp",)),
    "xls": ("Excel file (old)", ".xls", (".xls",)),
    "eml": ("email", ".eml", (".eml",)),
    "msg": ("Outlook email", ".msg", (".msg",)),
}
# The one kind with a name that is recognised and refused: PowerPoint 97-2003 has no reader that needs nothing
# installed, so the owner is asked to save it as .pptx.
PPT_REFUSAL = ("Old PowerPoint (.ppt) files cannot be read. Open it in PowerPoint, choose File, Save As, "
               "PowerPoint Presentation (.pptx), and add that file instead.")
READABLE_WORDS = "PDF, picture, web page, Word, Excel, PowerPoint, OpenDocument, rich text, JSON, XML, email or text"
LABELS = {kind: label for kind, (label, _, _) in KINDS.items()}
EXTENSIONS = {kind: ext for kind, (_, ext, _) in KINDS.items() if ext}
IMAGE_EXTENSIONS = KINDS["image"][2]
_BY_SUFFIX = {suffix: kind for kind, (_, _, suffixes) in KINDS.items() for suffix in suffixes}

VIDEO_AUDIO = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg", ".mp3", ".m4a",
               ".wav", ".aac", ".ogg", ".flac", ".wma", ".aif", ".aiff")
NOT_YET = ("Video and audio files cannot be read yet. If it has a transcript or captions, save those as a "
           "text file and add that.")


def cannot_read(name: str = "", content_type: str = "", content: bytes = b"") -> str | None:
    """Why a file is refused before it is filed, in words the owner can act on: video and audio (not yet), old
    PowerPoint (save as .pptx). None for anything else (an unknown kind is the caller's to describe)."""
    if Path(name or "").suffix.lower() == ".ppt" or _ole_kind(content) == "ppt":
        return PPT_REFUSAL
    return not_yet(name, content_type)


def not_yet(name: str = "", content_type: str = "") -> str | None:
    """The plain-words refusal for a video or audio file (by its name or content type), else None."""
    ctype = (content_type or "").lower()
    if Path(name or "").suffix.lower() in VIDEO_AUDIO or ctype.startswith(("video/", "audio/")):
        return NOT_YET
    return None


# What the Set-up tab calls each kind of file it was given, by extension.
_OWNER_WORD = {"pdf": "PDF", "image": "picture", "html": "web page", "sheet": "Excel", "docx": "Word",
               "pptx": "PowerPoint", "json": "data", "xml": "data", "doc": "Word", "rtf": "rich text",
               "odt": "OpenDocument", "ods": "OpenDocument", "odp": "OpenDocument", "xls": "Excel",
               "eml": "email", "msg": "email"}
OWNER_LABELS = {suffix: _OWNER_WORD.get(kind) or ("table" if suffix in (".csv", ".tsv") else "text")
                for suffix, kind in _BY_SUFFIX.items()}

# The content type a browser sends for each extension, so an upload is saved under the right name. The
# server (serve.UPLOAD_TYPES), both upload boxes (util.js HB.uploadTypes) and the tests read this one list.
UPLOAD_MIME = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic", ".heif": "image/heif",
    ".tif": "image/tiff", ".tiff": "image/tiff",
    ".html": "text/html", ".htm": "text/html", ".xhtml": "application/xhtml+xml",
    ".txt": "text/plain", ".text": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".json": "application/json", ".jsonl": "application/x-ndjson", ".xml": "application/xml", ".svg": "image/svg+xml",
    ".doc": "application/msword", ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text", ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation", ".xls": "application/vnd.ms-excel",
    ".eml": "message/rfc822", ".msg": "application/vnd.ms-outlook",
}
# Other content types for the same extensions that browsers and servers send.
MIME_ALIASES = {"text/xml": ".xml", "text/rtf": ".rtf", "text/json": ".json", "application/x-msg": ".msg",
                "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm"}


def kind_for_name(name: str) -> str | None:
    """The kind a file name's extension says, or None."""
    return _BY_SUFFIX.get(Path(name or "").suffix.lower())


def upload_accept() -> str:
    """The extensions an upload box offers, ".pdf,.png,..." (the viewer's lists are checked against it)."""
    return ",".join(sorted(_BY_SUFFIX))


# --- telling kinds apart from bytes ------------------------------------------------------------

def image_type(content: bytes) -> str | None:
    """"png", "jpg", "gif", "webp", "tiff" or "heic" from a file's first bytes, else None."""
    head = content[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heim", b"heis"):
        return "heic"
    return None


def stored_extension(kind: str, content: bytes = b"", name: str = "") -> str:
    """The extension `original<ext>` is saved with: fixed for most kinds, the picture's own for an image."""
    if kind == "image":
        found = image_type(content)
        if found:
            return "." + found
        suffix = Path(name or "").suffix.lower()
        return {".jpeg": ".jpg", ".tif": ".tiff", ".heif": ".heic"}.get(suffix, suffix) \
            if suffix in IMAGE_EXTENSIONS else ".jpg"
    return EXTENSIONS[kind]


_ODF = {b"application/vnd.oasis.opendocument.text": "odt", b"application/vnd.oasis.opendocument.spreadsheet": "ods",
        b"application/vnd.oasis.opendocument.presentation": "odp"}
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# The names of the streams inside an old Office file, in UTF-16 as the file stores them.
_OLE_STREAMS = ((b"W\x00o\x00r\x00d\x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t", "doc"),
                (b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k", "xls"),
                (b"P\x00o\x00w\x00e\x00r\x00P\x00o\x00i\x00n\x00t\x00 \x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t", "ppt"),
                (b"_\x00_\x00s\x00u\x00b\x00s\x00t\x00g\x001\x00.\x000\x00_", "msg"))
_GENERIC = ("application/octet-stream", "binary/octet-stream", "application/binary", "application/download",
            "application/force-download", "application/x-download")


def _zip_kind(content: bytes) -> str | None:
    """The kind of a zip-packed file (a Word, Excel, PowerPoint or OpenDocument file) from the names inside."""
    import zipfile
    if content[:4] != b"PK\x03\x04":
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = set(z.namelist())
            if "word/document.xml" in names:
                return "docx"
            if "ppt/presentation.xml" in names:
                return "pptx"
            if "xl/workbook.xml" in names:
                return "sheet"
            if "mimetype" in names:
                return _ODF.get(z.read("mimetype").strip())
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return None
    return None


def _ole_kind(content: bytes) -> str | None:
    """The kind of an old Office file (.doc, .xls, .ppt, Outlook .msg) from the streams it holds."""
    if not content.startswith(_OLE_MAGIC):
        return None
    for stream, kind in _OLE_STREAMS:
        if stream in content:
            return kind
    return None


def _known(kind: str | None) -> str | None:
    return kind if kind in KINDS else None


def kind_of(content: bytes, content_type: str = "", name: str = "") -> str | None:
    """What a file the web or the owner gave is: one of the kinds above, or None for what cannot be
    read. Bytes decide first (a PDF, a picture, a packed Office file), then the content type, then
    the name."""
    if b"%PDF-" in content[:1024]:
        return "pdf"
    if image_type(content):
        return "image"
    packed = _zip_kind(content) or _ole_kind(content)
    if packed:
        return _known(packed)
    ctype = (content_type or "").lower()
    if "spreadsheetml" in ctype or (content[:2] == b"PK" and b"xl/" in content[:4096]):
        return "sheet"
    if ctype == "image/svg+xml":
        return _known("xml")
    if ctype.startswith("image/"):
        return None
    if "html" in ctype:
        return "html"
    head = content[:2048].lower()
    generic = not ctype or ctype in _GENERIC
    if content[:64].lstrip()[:5] == b"{\\rtf":
        return _known("rtf")
    if "json" in ctype:
        return _known("json")
    if ctype in ("application/xml", "text/xml") or (ctype.endswith("+xml") and "html" not in ctype) or \
            (generic and head.lstrip().startswith(b"<?xml") and b"<html" not in head):
        return _known("xml")
    if ctype == "message/rfc822":
        return _known("eml")
    if ctype.startswith("text/"):
        named = kind_for_name(name)
        return named if named in ("json", "xml", "rtf") else "text"       # a .json file served as text/plain
    if b"<html" in head or b"<!doctype html" in head:   # e-Gov serves its pages as octet-stream
        return "html"
    if not generic:
        return None
    # no (or only a generic) content type: look at the name, then at the bytes
    named = kind_for_name(name)
    if named:
        return named
    if content[:2] in (b"PK", b"\x1f\x8b") or content.startswith(_OLE_MAGIC):
        return None                                     # a zip, gzip or old Office file we have no reader for
    return None if b"\x00" in content[:2048] else "text"


# --- what a reading is -------------------------------------------------------------------------

@dataclass
class Reading:
    kind: str
    pages: list[str]
    info: dict = field(default_factory=dict)            # a PDF's own dates and title
    ocr_pages: list[int] = field(default_factory=list)  # page numbers (from 1) whose words came from a picture
    pictures: dict = field(default_factory=dict)        # page number -> [JPEG bytes] worth showing a scorer
    notes: list[str] = field(default_factory=list)      # plain facts for the owner
    ocr_quality: float | None = None                    # the reader's own confidence over the OCR'd text
    unread_pictures: int = 0                            # pictures whose words could not be read (no reader)
    poor_pages: list[int] = field(default_factory=list)  # pages whose words were read from a picture poorly


# The kinds office.py reads, and the function for each.
OFFICE_READERS = {"docx": "read_docx", "pptx": "read_pptx", "json": "read_json", "xml": "read_xml",
                  "odt": "read_odt", "ods": "read_ods", "odp": "read_odp", "xls": "read_xls", "eml": "read_eml",
                  "msg": "read_msg"}
TEXTUTIL_KINDS = ("doc", "rtf")             # read by the Mac's textutil (or, for rtf, a plain reader)


# --- pictures for the scorer -------------------------------------------------------------------

MAX_EDGE = 1568             # Claude shrinks anything larger than this on its long side, so none is sent larger
MAX_SEGMENTS = 4            # a very tall or wide picture is cut into at most this many pieces
SEGMENT_ASPECT = 2.2        # ... when its long side is more than this many times its short side
JPEG_QUALITY = 82


def _open_image(source) -> "_Image.Image | None":
    """The picture as Pillow sees it (its own rotation applied), or None. A HEIC file Pillow cannot
    open is converted by the Mac's sips first."""
    if _Image is None:
        return None
    _Image.MAX_IMAGE_PIXELS = 400_000_000
    try:
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        im = _Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001 (not a picture Pillow knows)
        converted = _sips_jpeg(source)
        if converted is None:
            return None
        try:
            im = _Image.open(io.BytesIO(converted))
            im.load()
        except Exception:  # noqa: BLE001
            return None
    try:
        from PIL import ImageOps
        im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001
        pass
    return im


def _sips_jpeg(source) -> bytes | None:
    """A JPEG of any picture the Mac can open (HEIC, WebP, TIFF), through the built-in sips."""
    sips = shutil.which("sips") or ("/usr/bin/sips" if os.path.isfile("/usr/bin/sips") else None)
    if not sips:
        return None
    with tempfile.TemporaryDirectory(prefix="hb-sips-") as tmp:
        src = Path(source) if not isinstance(source, bytes) else None
        if src is None:
            src = Path(tmp) / "in.bin"
            src.write_bytes(source)
        out = Path(tmp) / "out.jpg"
        try:
            proc = subprocess.run([sips, "-s", "format", "jpeg", str(src), "--out", str(out)],
                                  capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.read_bytes() if proc.returncode == 0 and out.is_file() else None


def kit_pictures(source) -> list[bytes]:
    """The picture at `source` (a path or bytes) as JPEGs for a scorer to look at: one, no more than
    MAX_EDGE across, or for a very tall or wide picture up to MAX_SEGMENTS pieces in order (a menu poster
    13,000 pixels tall is cut in two, so each piece is legible). [] when the picture cannot be opened or
    Pillow is not there."""
    im = _open_image(source)
    if im is None:
        return []
    im = im.convert("RGB")
    w, h = im.size
    if min(w, h) < 1:
        return []
    long_side, short_side = max(w, h), min(w, h)
    pieces = 1
    if long_side / short_side > SEGMENT_ASPECT:
        pieces = min(MAX_SEGMENTS, max(2, round(long_side / (short_side * 1.5))))
    out = []
    for i in range(pieces):
        if pieces == 1:
            part = im
        else:
            overlap = int(long_side * 0.02)
            start = max(0, int(long_side * i / pieces) - (overlap if i else 0))
            end = min(long_side, int(long_side * (i + 1) / pieces) + (overlap if i < pieces - 1 else 0))
            part = im.crop((0, start, w, end)) if h >= w else im.crop((start, 0, end, h))
        scale = min(1.0, MAX_EDGE / max(part.size))
        if scale < 1.0:
            part = part.resize((max(1, round(part.size[0] * scale)), max(1, round(part.size[1] * scale))),
                               _Image.LANCZOS)
        buf = io.BytesIO()
        part.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
        out.append(buf.getvalue())
    return out


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a picture, or None (Pillow is not there, or it is not a picture)."""
    if _Image is None:
        return None
    try:
        return _Image.open(io.BytesIO(data)).size
    except Exception:  # noqa: BLE001
        return None


def embedded_picture(data: bytes) -> dict | None:
    """The words in a picture that sits inside another file (a screenshot pasted into a Word document): what
    `ocr.recognise` gives, or None when there is no reader."""
    with tempfile.TemporaryDirectory(prefix="hb-pic-") as tmp:
        path = Path(tmp) / ("picture." + (image_type(data) or "png"))
        path.write_bytes(data)
        return ocr.recognise(path)


# --- images ------------------------------------------------------------------------------------

def read_image(path: Path) -> Reading:
    """One page: the words in the picture (OCR), and the picture itself for the scorer."""
    got = ocr.recognise(path)
    text = (got or {}).get("text", "").strip()
    reading = Reading("image", [text], pictures={1: kit_pictures(path)})
    reading.pictures = {n: p for n, p in reading.pictures.items() if p}
    if got is None:
        reading.unread_pictures = 1
        reading.notes.append(ocr.missing_note())
    elif text:
        reading.ocr_pages, reading.ocr_quality = [1], got.get("confidence")
        reading.notes.append(f"Read the words in the picture with {_engine_name(got['engine'])}; a letter or "
                             "figure can be wrong, so the picture is kept for the scorer to look at.")
    else:
        reading.notes.append("The picture has no words the reader could make out; it is kept for the scorer to look at.")
    return reading


def _engine_name(engine: str) -> str:
    return "the Mac's own text reader (Apple Vision)" if engine == "vision" else engine


# --- PDFs --------------------------------------------------------------------------------------

# PDFium marks a word broken at the end of a line with \x02 (poppler prints a hyphen) and gives
# other control characters for glyphs it cannot map to a letter; poppler drops those.
_PDFIUM_JUNK = re.compile(r"[\x00-\x01\x03-\x08\x0b\x0c\x0e-\x1f]")

# A page is read as a picture when it has hardly any text of its own (a scan), or when it is mostly
# picture with little text: a grid of logos, a chart, a photograph with a caption.
SCAN_CHARS = 25               # less text than this: a scan or a blank page
LITTLE_TEXT = 1000            # a picture page has less text than this
SPARSE_TEXT = 300             # ... or less than this when its picture is drawn with lines, not images
COVER_SHARE = 0.35            # a picture covering this share of the page
MANY_IMAGES = 8               # or this many separate pictures (logos)
MANY_SHAPES = 40              # or this many drawn shapes on a nearly empty page (a chart)
MAX_OCR_PAGES = 300           # OCR is read for at most this many pages of one document (Kenya's budget: 124 scans)
MAX_PAGE_PICTURES = 40        # and at most this many page pictures are kept for one document,
MAX_POOR_PICTURES = 40        # and this many more for pages whose words were read poorly (es-imco-bipe, 21 Oct 2026:
POOR_OCR = 0.8                # a budget's image-only salary tables came through garbled with no picture)
RENDER_LONG_EDGE = 2600       # a page is drawn about 200 dpi for reading its words


def have(tool: str) -> bool:
    """Whether a command-line tool is installed (tests patch this)."""
    return shutil.which(tool) is not None


def pdf_reader() -> str | None:
    """What reads PDFs here: "pdfium" (pypdfium2), else "poppler" (pdftotext and pdfinfo), else None."""
    if _pdfium is not None:
        return "pdfium"
    return "poppler" if have("pdftotext") and have("pdfinfo") else None


def _pdfium_date(value: str) -> str:
    """A PDF date ("D:20250301120000+00'00'") in the ISO form `pdf_date` reads; anything else as given."""
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2}))?", value or "")
    if not m:
        return value
    day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return f"{day}T{m.group(4)}:{m.group(5)}:{m.group(6)}" if m.group(4) else day


def _page_features(page) -> dict:
    """How much of the page is picture: {"cover" (share of its area), "images", "shapes"}."""
    import pypdfium2.raw as pdfium_c
    width, height = page.get_size()
    cover, images, shapes = 0.0, 0, 0
    try:
        for obj in page.get_objects(max_depth=3):
            if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                images += 1
                left, bottom, right, top = obj.get_bounds()
                cover += max(0.0, min(right, width) - max(left, 0)) * max(0.0, min(top, height) - max(bottom, 0))
            elif obj.type in (pdfium_c.FPDF_PAGEOBJ_PATH, pdfium_c.FPDF_PAGEOBJ_SHADING):
                shapes += 1
    except Exception:  # noqa: BLE001 (an object PDFium cannot describe: the text alone decides)
        pass
    return {"cover": min(1.0, cover / (width * height)) if width * height else 0.0,
            "images": images, "shapes": shapes}


def is_picture_page(chars: int, features: dict) -> bool:
    """Whether a page that has `chars` characters of text and these features is mostly picture: little
    text, and one big picture, many pictures, or (on a nearly empty page) many drawn shapes."""
    if not features:
        return False
    if chars < LITTLE_TEXT and (features["cover"] >= COVER_SHARE or features["images"] >= MANY_IMAGES):
        return True
    return chars < SPARSE_TEXT and features["shapes"] >= MANY_SHAPES


def _render_pdfium(page) -> "_Image.Image | None":
    if _Image is None:
        return None
    width, height = page.get_size()
    scale = max(1.0, min(4.0, RENDER_LONG_EDGE / max(width, height, 1)))
    try:
        return page.render(scale=scale).to_pil().convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _ocr_image(im: "_Image.Image") -> dict | None:
    with tempfile.TemporaryDirectory(prefix="hb-ocr-") as tmp:
        path = Path(tmp) / "page.png"
        im.save(path)
        return ocr.recognise(path)


def _new_lines(ocr_text: str, text: str) -> str:
    """The lines of `ocr_text` that the page's own text does not already hold (a logo's wordmark, a
    chart's labels), so a page read both ways is not said twice."""
    have_text = re.sub(r"\W+", " ", text.casefold())
    out = []
    for line in ocr_text.splitlines():
        key = re.sub(r"\W+", " ", line.casefold()).strip()
        if key and key not in have_text:
            out.append(line)
    return "\n".join(out)


def pdf_unreadable(path: Path) -> str:
    """Why a PDF would not open, in a sentence: cut short in the download (no end-of-file marker in its last
    kilobytes; InfluenceMap, sweep round 3: a 5,242,880-byte copy of a longer report was called damaged), else
    damaged or locked."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - 2048))
            tail = f.read()
    except OSError:
        return "the PDF could not be read (damaged, or locked with a password)."
    if size and b"%%EOF" not in tail:
        return (f"the PDF stops part way ({size:,} bytes and no end-of-file marker): the download was cut short. "
                "Fetch it again, or save it from a browser and add it.")
    return "the PDF could not be read (damaged, or locked with a password)."


def pdfium_read(path: Path) -> tuple[list[str], dict]:
    """(the text of each page, CreationDate/ModDate/Title/Producer) with pypdfium2, and no more. No
    pages when the PDF cannot be opened (damaged, or locked with a password). `read_pdf` is the same
    reading with scanned and picture pages added."""
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
    return texts, _pdf_info(meta)


def _pdf_info(meta: dict) -> dict:
    return {key: _pdfium_date(meta[key]) if key.endswith("Date") else meta[key]
            for key in ("CreationDate", "ModDate", "Title", "Producer") if meta.get(key)}


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


def pdf_page_texts(path: Path, pages: int) -> list[str]:
    """The text of each page, layout kept: `pdftotext -f N -l N -layout` once per page."""
    texts = []
    for n in range(1, pages + 1):
        got = _run(["pdftotext", "-f", str(n), "-l", str(n), "-layout", "-q", str(path), "-"])
        if got.returncode != 0:
            raise HandbookError(f"pdftotext could not read page {n} of {path.name}.")
        texts.append(got.stdout.replace("\f", "").rstrip())
    return texts


def _render_poppler(path: Path, n: int) -> "_Image.Image | None":
    if _Image is None or not have("pdftoppm"):
        return None
    with tempfile.TemporaryDirectory(prefix="hb-pdf-") as tmp:
        prefix = Path(tmp) / "page"
        try:
            proc = subprocess.run(["pdftoppm", "-r", "200", "-f", str(n), "-l", str(n), "-png", str(path),
                                   str(prefix)], capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return None
        found = sorted(Path(tmp).glob("page*.png"))
        if proc.returncode != 0 or not found:
            return None
        im = _Image.open(found[0])
        im.load()
        return im.convert("RGB")


def read_pdf(path: Path, scans: bool = True) -> Reading:
    """A PDF as pages of text. A page with no text of its own is drawn and read as a picture (OCR); a page
    that is mostly picture with little text is drawn too, its picture kept for the scorer and the words
    in it added to its text. Raises HandbookError when no PDF reader is installed or the file cannot be
    opened. With `scans` False (a document read as evidence in the free edition) no page is read from a
    picture: the reading says how many pages were left unread."""
    reader = pdf_reader()
    if reader is None:
        raise HandbookError(PDF_MISSING)
    pdf = None
    if reader == "pdfium":
        try:
            pdf = _pdfium.PdfDocument(str(path))
        except _pdfium.PdfiumError:
            raise HandbookError(pdf_unreadable(path)) from None
    try:
        if pdf is not None:
            texts, features, info = [], [], {}
            for i in range(len(pdf)):
                page = pdf[i]
                textpage = page.get_textpage()
                text = textpage.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n")
                texts.append(_PDFIUM_JUNK.sub("", text.replace("\x02", "-")).rstrip())
                textpage.close()
                # Only a page with some text but not a full page of it can be "mostly picture".
                features.append(_page_features(page) if SCAN_CHARS <= len(texts[-1].strip()) < LITTLE_TEXT else None)
                page.close()
            info = _pdf_info(pdf.get_metadata_dict(skip_empty=True))
        else:
            count = pdf_page_count(path)
            texts, features, info = (pdf_page_texts(path, count), [None] * count, pdf_info(path)) if count > 0 \
                else ([], [], {})
        if not texts:
            raise HandbookError(pdf_unreadable(path))
        reading = Reading("pdf", texts, info=info)
        if scans:
            _read_pictures_of_pdf(path, pdf, reading, features)
        else:
            blank = sum(1 for t in texts if len(t.strip()) < SCAN_CHARS)
            if blank:
                reading.notes.append(edition.scanned_pages_note(blank))
    finally:
        if pdf is not None:
            pdf.close()
    return reading


def _read_pictures_of_pdf(path: Path, pdf, reading: Reading, features: list) -> None:
    """Fill in the OCR and the pictures for the pages that need them (see `read_pdf`)."""
    texts = reading.pages
    wanted = []        # (index, scanned?) for each page to draw
    for i, text in enumerate(texts):
        chars = len(text.strip())
        if chars < SCAN_CHARS:
            wanted.append((i, True))
        elif is_picture_page(chars, features[i]):
            wanted.append((i, False))
    if not wanted:
        return
    engine = ocr.engine()
    scores, drawn, kept, cut_off, poor_kept = [], 0, 0, 0, 0     # poor_kept: pictures kept past the cap for poor pages
    for i, scanned in wanted:
        if drawn >= MAX_OCR_PAGES:
            cut_off += 1
            continue
        if pdf is not None:
            page = pdf[i]
            try:
                im = _render_pdfium(page)
            finally:
                page.close()
        else:
            im = _render_poppler(path, i + 1)
        if im is None:
            continue
        drawn += 1
        got = _ocr_image(im) if engine else None
        words = (got or {}).get("text", "").strip()
        if got is None and scanned:
            reading.unread_pictures += 1
        if words:
            if scanned:
                texts[i] = words
                reading.ocr_pages.append(i + 1)
                scores.append(got.get("confidence"))
            else:
                extra = _new_lines(words, texts[i])
                if extra.strip():
                    texts[i] = texts[i].rstrip() + "\n" + extra
                    reading.ocr_pages.append(i + 1)
                    scores.append(got.get("confidence"))
        poor = bool(words) and (got or {}).get("confidence") is not None and got["confidence"] < POOR_OCR
        if poor:
            reading.poor_pages.append(i + 1)      # its words may be garbled: the scorer should see the picture
        if (words or not scanned or got is None) and (kept < MAX_PAGE_PICTURES or (poor and poor_kept < MAX_POOR_PICTURES)):
            pictures = kit_pictures(_png_bytes(im))
            if pictures:
                reading.pictures[i + 1] = pictures
                if kept < MAX_PAGE_PICTURES:
                    kept += 1
                else:
                    poor_kept += 1
    scores = [s for s in scores if s is not None]
    reading.ocr_quality = round(sum(scores) / len(scores), 3) if scores else None
    if reading.ocr_pages:
        reading.notes.append(f"Read the words on {_plural(len(reading.ocr_pages), 'page')} from a picture with "
                             f"{_engine_name(engine)} (page{'s' if len(reading.ocr_pages) != 1 else ''} "
                             f"{_numbers(reading.ocr_pages)}); a letter or figure can be wrong.")
    if reading.unread_pictures:
        reading.notes.append(ocr.missing_note() + f" ({_plural(reading.unread_pictures, 'scanned page')}.)")
    if cut_off:
        reading.notes.append(f"Only the first {MAX_OCR_PAGES} picture pages were read; {_plural(cut_off, 'page')} "
                             "later in the file were not.")


def _png_bytes(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _numbers(pages: list[int], limit: int = 8) -> str:
    shown = ", ".join(str(p) for p in pages[:limit])
    return shown + (f" and {len(pages) - limit} more" if len(pages) > limit else "")


# --- reading a file ----------------------------------------------------------------------------

def read(path: Path, kind: str | None = None, evidence: bool = False) -> Reading:
    """The pages of the saved file at `path`. `kind` defaults to what its extension says. Raises
    HandbookError, in words the owner can act on, for a kind it cannot read. `evidence` is True when the
    file is a document the scorers will quote (prepare), False for the owner's set-up material, which
    is read in every format in both editions: the free edition reads only PDFs (without their scanned
    pages), web pages and text as evidence."""
    from . import fetch
    path = Path(path)
    kind = kind or kind_for_name(path.name)
    if kind is None:
        why = not_yet(path.name)
        raise HandbookError(why or f"The tool cannot read {path.suffix or 'this kind of'} files yet. "
                                   "Save it as a PDF or a text file and add that.")
    if evidence:
        refusal = edition.evidence_refusal(kind)
        if refusal:
            raise HandbookError(refusal)
    if kind == "pdf":
        return read_pdf(path, scans=not evidence or edition.is_full())
    if kind == "image":
        return read_image(path)
    if kind in OFFICE_READERS:
        from . import office
        return getattr(office, OFFICE_READERS[kind])(path)
    if kind in TEXTUTIL_KINDS:
        from . import office
        return office.read_textutil(path, kind)
    if kind == "html":
        return Reading(kind, _split_long(fetch.extract_text_html(path.read_bytes())))
    if kind == "sheet":
        from .material import xlsx_sheets
        try:
            return Reading(kind, [f"Sheet: {title}\n{text}" for title, text in xlsx_sheets(path)])
        except Exception as e:  # noqa: BLE001 (a damaged workbook)
            raise HandbookError(f"the spreadsheet could not be read ({type(e).__name__}).") from None
    if kind == "text":
        return Reading(kind, _split_long(fetch.decode_html(path.read_bytes()).strip()))
    raise HandbookError(f"The tool cannot read {kind} files yet.")


def _split_long(text: str) -> list[str]:
    from .prepare import split_long
    return split_long(text)


def text_of(path: Path) -> str:
    """The whole file as one text for the owner's set-up material: pages in order, a "[page N]" line
    before each when there is more than one. Raises ValueError for a file that cannot be read."""
    try:
        reading = read(path)
    except HandbookError as e:
        raise ValueError(str(e)) from None
    if len(reading.pages) == 1 and reading.kind != "pdf":
        return reading.pages[0]
    return "\n".join(f"[page {i}]\n{t}" for i, t in enumerate(reading.pages, 1))

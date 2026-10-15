"""Readers for Word, PowerPoint, OpenDocument, old Office, JSON, XML, RTF and email files: each takes a path
and returns a `readers.Reading` (pages of text, which pages hold words read from a picture, the pictures
worth showing a scorer, notes for the owner).

Standard library only, except where a pure Python package is named (xlrd for old .xls, olefile for old .ppt and
Outlook .msg, both fetched by the launcher). Nothing here needs a program the owner has not got: the Mac's own
`textutil` converts .doc, .rtf and .odt.

Pages: a slide is a page (PowerPoint, OpenDocument presentations), a sheet is a page (Excel, OpenDocument
spreadsheets), and a Word document is paged where Word itself broke it (the page breaks it saved, a hard page
break, a section that starts on a new page), or is one page when it holds none. A very long page is cut by
`prepare.split_long` as any other.

Pictures inside a Word or PowerPoint file are read like an image file (OCR) when they are big enough to hold
words: their words go into the page where the picture sits, marked `[Picture: ...]`, and the picture is kept
for the scorer.
"""
from __future__ import annotations
import html
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .folder import HandbookError

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

MIN_PICTURE_PIXELS = 60_000     # a picture smaller than this (a logo, a bullet) is not read
MAX_PICTURES = 30               # pictures read in one file
MAX_UNZIPPED = 400_000_000      # a packed file that unpacks to more than this is refused (a zip bomb)


def _reading(kind: str, pages: list[str], **kw):
    from .readers import Reading
    return Reading(kind, pages if any(p.strip() for p in pages) else [""], **kw)


def _open(path: Path, what: str) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise HandbookError(f"the {what} could not be opened (damaged, or not really a {what}).") from None
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
        z.close()
        raise HandbookError(f"the {what} unpacks to more than {MAX_UNZIPPED // 1_000_000} MB, so it was not read.")
    return z


_PREFIXES = {"w": W[1:-1], "r": R[1:-1], "a": A[1:-1], "p": P[1:-1], "wp": WP[1:-1], "mc": MC[1:-1],
             "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape", "v": "urn:schemas-microsoft-com:vml"}


def _parse(data: bytes):
    """The XML of a part. A generator that left a prefix undeclared (a hand-made file) is read as though the
    usual prefixes were declared, so its words are not lost; None when it is not XML at all."""
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        pass
    text = data.decode("utf-8", "replace")
    m = re.search(r"<(?![?!])([\w:.-]+)((?:\s[^>]*?)?)(/?)>", text)
    if not m:
        return None
    missing = "".join(f' xmlns:{k}="{v}"' for k, v in _PREFIXES.items() if f"{k}:" in text and f"xmlns:{k}=" not in text)
    try:
        return ET.fromstring((text[:m.end(1)] + missing + text[m.end(1):]).encode("utf-8"))
    except ET.ParseError:
        return None


def _xml(z: zipfile.ZipFile, name: str):
    try:
        return _parse(z.read(name))
    except KeyError:
        return None


def _rels(z: zipfile.ZipFile, part: str) -> dict:
    """{relationship id: (target, type, external?)} of the part's .rels file; targets are made absolute inside the zip."""
    folder, _, base = part.rpartition("/")
    root = _xml(z, f"{folder + '/' if folder else ''}_rels/{base}.rels")
    out = {}
    if root is None:
        return out
    for rel in root.iter():
        if not rel.tag.endswith("Relationship"):
            continue
        target, external = rel.get("Target") or "", rel.get("TargetMode") == "External"
        if not external:
            target = _resolve(folder, target)
        out[rel.get("Id")] = (target, (rel.get("Type") or "").rsplit("/", 1)[-1], external)
    return out


def _resolve(folder: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = folder.split("/") if folder else []
    for piece in target.split("/"):
        if piece == "..":
            parts = parts[:-1]
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


class Pictures:
    """The pictures met while reading one file: each big enough one is read (OCR) once, its words returned to
    put in the page, and the picture kept for the scorer (at most readers.MAX_PAGE_PICTURES)."""

    def __init__(self):
        from . import readers
        self.readers = readers
        self.found = 0
        self.by_page: dict[int, list[bytes]] = {}
        self.ocr_pages: set[int] = set()
        self.scores: list[float] = []
        self.skipped_unread = 0
        self.kept = 0

    def words(self, data: bytes, page: int, alt: str = "") -> str:
        """The text to set where the picture sits: "[Picture: <alt text>; words in it: ...]", or "" for a picture too small."""
        r = self.readers
        if not r.image_type(data) or self.found >= MAX_PICTURES:
            return f"[Picture: {alt}]" if alt else ""
        size = r.image_size(data)
        if size and size[0] * size[1] < MIN_PICTURE_PIXELS:
            return f"[Picture: {alt}]" if alt else ""
        self.found += 1
        got = r.embedded_picture(data)
        words = (got or {}).get("text", "").strip()
        if got is None:
            self.skipped_unread += 1
        if words:
            self.ocr_pages.add(page)
            if got.get("confidence") is not None:
                self.scores.append(got["confidence"])
        if (words or got is None) and self.kept < r.MAX_PAGE_PICTURES:
            jpegs = r.kit_pictures(data)
            if jpegs:
                self.by_page.setdefault(page, []).extend(jpegs)
                self.kept += 1
        bits = ([alt] if alt else []) + ([" / ".join(ln.strip() for ln in words.splitlines() if ln.strip())] if words else [])
        return f"[Picture: {'; words in it: '.join(bits)}]" if bits else ""

    def finish(self, reading) -> None:
        """Put what was found into the Reading."""
        reading.pictures = {n: v for n, v in self.by_page.items() if v}
        reading.ocr_pages = sorted(self.ocr_pages)
        reading.unread_pictures = self.skipped_unread
        reading.ocr_quality = round(sum(self.scores) / len(self.scores), 3) if self.scores else None
        if self.ocr_pages:
            reading.notes.append(f"Read the words in the pictures on {len(self.ocr_pages)} page"
                                 f"{'s' if len(self.ocr_pages) != 1 else ''} with the Mac's own text reader; a letter "
                                 "or figure can be wrong.")
        if self.skipped_unread:
            reading.notes.append("Some pictures in the file hold words that could not be read (no text reader on this "
                                 "computer); the pictures are kept to look at.")


# --- Word (.docx) ------------------------------------------------------------------------------

_BREAK = "\f"           # a page break inside a paragraph's text, split on after the walk


def _w_text(el, rels: dict, ctx: dict) -> str:
    """The text of a paragraph or run-level element, in order: tabs, line breaks, page breaks ("\\f"), links
    ("words (https://...)"), tracked insertions kept and deletions dropped, pictures read."""
    out = []
    for child in el:
        tag = child.tag
        if tag == f"{W}r":
            out.append(_w_text(child, rels, ctx))
        elif tag == f"{W}t":
            out.append(child.text or "")
        elif tag == f"{W}tab":
            out.append("\t")
        elif tag in (f"{W}br", f"{W}cr"):
            out.append(_BREAK if child.get(f"{W}type") == "page" else "\n")
        elif tag == f"{W}lastRenderedPageBreak":
            out.append(_BREAK)
        elif tag == f"{W}noBreakHyphen":
            out.append("-")
        elif tag == f"{W}hyperlink":
            words = _w_text(child, rels, ctx)
            rid = next((v for k, v in child.attrib.items() if k.endswith("}id")), None)   # r:id, whatever the file calls r
            url = rels.get(rid, ("", "", False))
            out.append(f"{words} ({html.unescape(url[0])})" if rid and url[2] and words.strip() else words)
        elif tag in (f"{W}ins", f"{W}smartTag", f"{W}sdtContent", f"{W}fldSimple", f"{W}customXml", f"{W}sdt"):
            out.append(_w_text(child, rels, ctx))
        elif tag in (f"{W}drawing", f"{W}pict", f"{W}object"):
            out.append(_w_picture(child, rels, ctx))
        elif tag == f"{MC}AlternateContent":          # a text box or shape: its first choice, not the fallback twin
            choice = child.find(f"{MC}Choice")
            if choice is not None:
                out.append(_w_text(choice, rels, ctx))
    return "".join(out)


def _w_picture(el, rels: dict, ctx: dict) -> str:
    """What a drawing holds: the words of a text box in it, and each picture (see `Pictures.words`)."""
    out = []
    for box in el.iter(f"{W}txbxContent"):
        words = [_w_text(p, rels, ctx).replace("\n", " ").strip() for p in box.iter(f"{W}p")]
        if any(words):
            out.append("\n" + "\n".join(w for w in words if w) + "\n")
    for blip in el.iter(f"{A}blip"):
        rid = blip.get(f"{R}embed")
        alt = ""
        for pr in el.iter(f"{WP}docPr"):
            alt = (pr.get("descr") or pr.get("title") or "").strip()
            break
        target = rels.get(rid, ("", "", False))[0]
        try:
            data = ctx["zip"].read(target) if target else b""
        except KeyError:
            data = b""
        text = ctx["pictures"].words(data, ctx["page"](), alt) if data else (f"[Picture: {alt}]" if alt else "")
        if text:
            out.append("\n" + text + "\n")
    return "".join(out)


def _w_block(el, rels: dict, ctx: dict, lines: list) -> None:
    """Add a paragraph or a table (rows with a tab between cells) to `lines`, with "\\f" where a page ends."""
    if el.tag == f"{W}p":
        ppr = el.find(f"{W}pPr")
        if ppr is not None and ppr.find(f"{W}pageBreakBefore") is not None:
            lines.append(_BREAK)
        text = _w_text(el, rels, ctx)
        if text.strip(" \t") or _BREAK in text:
            lines.append(text)
        if ppr is not None:
            sect = ppr.find(f"{W}sectPr")
            if sect is not None:
                kind = sect.find(f"{W}type")
                if kind is None or kind.get(f"{W}val") in ("nextPage", "oddPage", "evenPage"):
                    lines.append(_BREAK)
    elif el.tag == f"{W}tbl":
        for row in el.iter(f"{W}tr"):
            cells = []
            for cell in row.findall(f"{W}tc"):
                inner = []
                for p in cell.iter(f"{W}p"):
                    t = _w_text(p, rels, ctx).replace("\n", " ").strip()
                    if t:
                        inner.append(t)
                cells.append(" ".join(inner))
            if any(cells):
                lines.append("\t".join(cells).rstrip())
    elif el.tag in (f"{W}sdt", f"{W}sdtContent", f"{W}customXml", f"{W}ins"):
        for child in el:
            _w_block(child, rels, ctx, lines)


def _w_notes(z: zipfile.ZipFile, name: str, tag: str, rels: dict, ctx: dict) -> list[str]:
    root = _xml(z, name)
    out = []
    if root is None:
        return out
    for note in root.iter(f"{W}{tag}"):
        if note.get(f"{W}type") in ("separator", "continuationSeparator", "continuationNotice"):
            continue
        text = " ".join(t for t in (_w_text(p, rels, ctx).replace("\n", " ").replace(_BREAK, " ").strip()
                                    for p in note.iter(f"{W}p")) if t)
        if text:
            out.append(f"{note.get(f'{W}id')}. {text}")
    return out


def read_docx(path: Path):
    """A Word document as pages: where Word broke each page (saved page breaks, hard breaks, sections), tables as
    rows with tabs, links followed by where they go, footnotes and endnotes after the last page, pictures read."""
    z = _open(path, "Word file")
    with z:
        root = _xml(z, "word/document.xml")
        body = root.find(f"{W}body") if root is not None else None
        if body is None:
            raise HandbookError("the Word file could not be read (it has no document part).")
        rels = _rels(z, "word/document.xml")
        pictures = Pictures()
        lines: list = []
        state = {"zip": z, "pictures": pictures, "page": lambda: 1 + "".join(lines).count(_BREAK)}
        for el in body:
            _w_block(el, rels, state, lines)
        notes = _w_notes(z, "word/footnotes.xml", "footnote", _rels(z, "word/footnotes.xml"), state)
        notes += _w_notes(z, "word/endnotes.xml", "endnote", _rels(z, "word/endnotes.xml"), state)
    pages = [re.sub(r"\n{3,}", "\n\n", chunk).strip() for chunk in "\n".join(lines).split(_BREAK)]
    pages = [p for p in pages if p]
    if notes:
        pages = pages or [""]
        pages[-1] = (pages[-1] + "\n\nNotes\n" + "\n".join(notes)).strip()
    reading = _reading("docx", pages)
    pictures.finish(reading)
    # a picture's page number counted breaks so far, which may have been empty pages dropped above: keep only real pages
    reading.pictures = {n: v for n, v in reading.pictures.items() if n <= len(reading.pages)}
    reading.ocr_pages = [n for n in reading.ocr_pages if n <= len(reading.pages)]
    return reading


# --- PowerPoint (.pptx) ------------------------------------------------------------------------

def _a_paragraphs(el, rels: dict) -> list[str]:
    """The paragraphs under `el` (a text body), runs joined, a link followed by where it goes."""
    out = []
    for p in el.iter(f"{A}p"):
        bits = []
        for node in p:
            if node.tag in (f"{A}r", f"{A}fld"):
                t = node.find(f"{A}t")
                words = t.text if t is not None and t.text else ""
                link = node.find(f"{A}rPr/{A}hlinkClick")
                if link is not None and words.strip():
                    target = rels.get(link.get(f"{R}id"), ("", "", False))
                    if target[2] and target[0].startswith("http"):
                        words = f"{words} ({html.unescape(target[0])})"
                bits.append(words)
            elif node.tag == f"{A}br":
                bits.append("\n")
        text = "".join(bits).strip()
        if text:
            out.append(text)
    return out


def _p_shapes(tree, z: zipfile.ZipFile, rels: dict, page: int, pictures: "Pictures", lines: list) -> None:
    """The slide's shapes in order: text, tables (rows with tabs), groups, and pictures read."""
    for el in tree:
        tag = el.tag
        if tag == f"{P}sp":
            body = el.find(f"{P}txBody")
            if body is not None:
                ph = el.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                if ph is not None and ph.get("type") in ("sldNum", "dt", "ftr", "hdr"):
                    continue
                lines.extend(_a_paragraphs(body, rels))
        elif tag == f"{P}graphicFrame":
            for row in el.iter(f"{A}tr"):
                cells = [" ".join(_a_paragraphs(cell, rels)) for cell in row.findall(f"{A}tc")]
                if any(cells):
                    lines.append("\t".join(cells).rstrip())
        elif tag == f"{P}grpSp":
            _p_shapes(el, z, rels, page, pictures, lines)
        elif tag == f"{P}pic":
            blip = el.find(f"{P}blipFill/{A}blip")
            props = el.find(f"{P}nvPicPr/{P}cNvPr")
            alt = ((props.get("descr") or "") if props is not None else "").strip()
            data = b""
            if blip is not None:
                target = rels.get(blip.get(f"{R}embed"), ("", "", False))[0]
                try:
                    data = z.read(target) if target else b""
                except KeyError:
                    data = b""
            text = pictures.words(data, page, alt) if data else (f"[Picture: {alt}]" if alt else "")
            if text:
                lines.append(text)


def _slide_order(z: zipfile.ZipFile) -> list[str]:
    """The slide parts in the order the presentation shows them."""
    root = _xml(z, "ppt/presentation.xml")
    rels = _rels(z, "ppt/presentation.xml")
    order = []
    if root is not None:
        for sld in root.iter(f"{P}sldId"):
            target = rels.get(sld.get(f"{R}id"), ("", "", False))[0]
            if target and target in z.namelist():
                order.append(target)
    if not order:
        order = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                       key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    return order


def read_pptx(path: Path):
    """A PowerPoint file as pages, one per slide: the slide's text in order, tables as rows with tabs, links followed
    by where they go, then the speaker notes; pictures read."""
    z = _open(path, "PowerPoint file")
    with z:
        slides = _slide_order(z)
        if not slides:
            raise HandbookError("the PowerPoint file has no slides.")
        pictures, pages = Pictures(), []
        for n, part in enumerate(slides, 1):
            root = _xml(z, part)
            if root is None:
                pages.append("")
                continue
            rels = _rels(z, part)
            lines: list = []
            tree = root.find(f"{P}cSld/{P}spTree")
            if tree is not None:
                _p_shapes(tree, z, rels, n, pictures, lines)
            for target, kind, _ in rels.values():
                if kind == "notesSlide":
                    notes = _xml(z, target)
                    spoken = []
                    if notes is not None:
                        for sp in notes.iter(f"{P}sp"):
                            ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                            body = sp.find(f"{P}txBody")
                            if body is not None and ph is not None and ph.get("type") == "body":
                                spoken.extend(_a_paragraphs(body, {}))
                    if spoken:
                        lines.append("Speaker notes: " + " ".join(spoken))
            pages.append("\n".join(lines).strip())
    reading = _reading("pptx", pages)
    pictures.finish(reading)
    return reading


# --- JSON and XML ------------------------------------------------------------------------------

MIN_READABLE_CHARS = 40         # a string value this long, of at least five words, counts as readable text
MAX_READABLE = 150              # and at most this many of them are set first


def _decode(data: bytes) -> str:
    """Bytes of a data file as text: a byte order mark, an XML declaration's encoding, UTF-8, then cp1252."""
    from . import fetch
    head = data[:200]
    m = re.search(rb"""<\?xml[^>]*encoding\s*=\s*["']([\w.:-]+)""", head)
    if m and not data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode(m.group(1).decode("ascii"), errors="replace")
        except LookupError:
            pass
    return fetch.decode_html(data)


def _readable(path_text: str, text: str) -> str | None:
    """"path: words" for a string long enough to be sentences (markup in it taken out), else None."""
    from . import fetch
    if "<" in text and ">" in text:
        text = fetch.html_text(text)
    text = " ".join(text.split())
    if len(text) >= MIN_READABLE_CHARS and len(text.split()) >= 5:
        return f"{path_text}: {text}"
    return None


def _json_strings(node, path: str = "") -> list[str]:
    out = []
    if isinstance(node, str):
        got = _readable(path or "(value)", node)
        if got:
            out.append(got)
    elif isinstance(node, dict):
        for k, v in node.items():
            out.extend(_json_strings(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_json_strings(v, f"{path}[{i}]"))
    return out


def _with_readable(readable: list[str], body: str, notes: list[str]) -> list[str]:
    """The readable sentences first (so a scorer sees them before the structure), then the data itself."""
    from .prepare import split_long
    text = body
    if readable:
        shown = readable[:MAX_READABLE]
        if len(readable) > MAX_READABLE:
            notes.append(f"The file holds {len(readable)} longer passages of text; the first {MAX_READABLE} are set "
                         "first, and all of them are in the data below.")
        text = "Readable text\n" + "\n".join(shown) + "\n\nData\n" + body
    return split_long(text)


def read_json(path: Path):
    """A JSON file: the longer passages of text first (each with its place in the data), then the data indented,
    one value to a line. A file of one JSON value per line (JSON Lines) is read as a list."""
    text = _decode(path.read_bytes()).lstrip("\ufeff")
    try:
        data = json.loads(text)
    except ValueError as first:
        rows = []
        for line in text.splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    raise HandbookError(f"the file is not valid JSON ({first}); fix it or save it as a text file.") from None
        if len(rows) < 2:
            raise HandbookError(f"the file is not valid JSON ({first}); fix it or save it as a text file.") from None
        data = rows
    notes: list[str] = []
    pages = _with_readable(_json_strings(data), json.dumps(data, indent=2, ensure_ascii=False), notes)
    return _reading("json", pages, notes=notes)


def _xml_strings(root) -> list[str]:
    out = []

    def tag(el) -> str:
        return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""

    def walk(el, path: str) -> None:
        here = f"{path}/{tag(el)}" if path else tag(el)
        if len(el) == 0:
            got = _readable(here, el.text or "")
            if got:
                out.append(got)
        for child in el:
            if isinstance(child.tag, str):
                walk(child, here)
    walk(root, "")
    return out


def read_xml(path: Path):
    """An XML file as it is written, one tag to a line when it came on one line, with the longer passages of
    text set first (each with its place in the file). A file that is not well formed is read as markup text."""
    from . import fetch
    from .prepare import split_long
    data = path.read_bytes()
    text = _decode(data).lstrip("\ufeff")
    notes: list[str] = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        notes.append("The file is not well-formed XML, so its text was taken out of the markup.")
        return _reading("xml", split_long(fetch.html_text(text)), notes=notes)
    body = text if text.count("\n") >= 5 else re.sub(r">\s*<", ">\n<", text)
    return _reading("xml", _with_readable(_xml_strings(root), body, notes), notes=notes)


# --- email (.eml, Outlook .msg) ---------------------------------------------------------------

def _email_page(headers: dict, body: str, attachments: list[str]) -> str:
    lines = [f"{k}: {v}" for k, v in headers.items() if v]
    out = "\n".join(lines) + "\n\n" + body.strip()
    if attachments:
        out += "\n\nAttachments (not read here; save one and add it as a document): " + "; ".join(attachments)
    return out.strip()


def read_eml(path: Path):
    """An email: From, To, Cc, Date and Subject, then the message in plain text (or its web-page version with the
    links followed by where they go), then the names of any attachments."""
    from email import policy
    from email.parser import BytesParser
    from . import fetch
    from .prepare import split_long
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    headers = {k: " ".join(str(msg.get(k) or "").split()) for k in ("From", "To", "Cc", "Date", "Subject")}
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            content = ""
        body = fetch.html_text(content, links=True) if part.get_content_type() == "text/html" else content
    names = [a.get_filename() or "(unnamed)" for a in msg.iter_attachments()]
    if not any(headers.values()) and not body.strip():
        raise HandbookError("the email file could not be read (no headers or message found).")
    return _reading("eml", split_long(_email_page(headers, body, names)))


def _ole_text(ole, prop: str, storage: str = "") -> str:
    """A text property of an Outlook message (or of one attachment's storage): the Unicode form (001F) if the file
    has it, else the 8-bit one (001E)."""
    for suffix, codec in (("001F", "utf-16-le"), ("001E", "cp1252")):
        name = f"{storage + '/' if storage else ''}__substg1.0_{prop}{suffix}"
        if ole.exists(name):
            return ole.openstream(name).read().decode(codec, "replace").rstrip("\x00").strip()
    return ""


def _msg_date(ole) -> str:
    """The time the message was sent or delivered, from the property stream, as 2026-10-15 09:30 (or "")."""
    import struct
    from datetime import datetime, timedelta, timezone
    if not ole.exists("__properties_version1.0"):
        return ""
    data = ole.openstream("__properties_version1.0").read()
    found = {}
    for off in range(32, len(data) - 15, 16):
        tag, _, value = struct.unpack_from("<IIQ", data, off)
        if tag & 0xFFFF == 0x0040:                      # a time property: its id is the high half
            found[tag >> 16] = value
    for prop in (0x0039, 0x0E06):
        if found.get(prop):
            when = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=found[prop] // 10)
            return when.strftime("%Y-%m-%d %H:%M")
    return ""


def read_msg(path: Path):
    """An Outlook message (.msg), through olefile: sender, recipients, date, subject, the message text (or its
    web-page version) and the names of its attachments."""
    from . import fetch
    from .prepare import split_long
    try:
        import olefile
    except ImportError:
        raise HandbookError("Outlook messages (.msg) need olefile, which the tool's launcher installs. Run the tool "
                            "through lib/hb, or save the message as .eml and add that.") from None
    try:
        ole = olefile.OleFileIO(str(path))
    except Exception:  # noqa: BLE001 (olefile raises OSError or its own errors for a damaged file)
        raise HandbookError("the Outlook message could not be opened (damaged, or not really a .msg).") from None
    with ole:
        sender = _ole_text(ole, "0C1A")
        address = _ole_text(ole, "5D01") or _ole_text(ole, "0C1F")
        headers = {"From": f"{sender} <{address}>" if sender and address and address != sender else sender or address,
                   "To": _ole_text(ole, "0E04"), "Cc": _ole_text(ole, "0E03"), "Date": _msg_date(ole),
                   "Subject": _ole_text(ole, "0037")}
        body = _ole_text(ole, "1000")
        if not body.strip() and ole.exists("__substg1.0_10130102"):
            raw = ole.openstream("__substg1.0_10130102").read()
            body = fetch.html_text(raw.decode("utf-8", "replace"), links=True)
        names = []
        for entry in ole.listdir(streams=False, storages=True):
            if len(entry) == 1 and entry[0].startswith("__attach_version1.0_"):
                names.append(_ole_text(ole, "3707", entry[0]) or _ole_text(ole, "3704", entry[0]) or "(unnamed)")
    if not any(headers.values()) and not body.strip():
        raise HandbookError("the Outlook message could not be read (no sender, subject or message found).")
    return _reading("msg", split_long(_email_page(headers, body, names)))


# --- old Word and rich text, through the Mac's textutil ---------------------------------------

def _textutil(path: Path, fmt: str) -> str | None:
    """`textutil -convert FMT -stdout`: macOS converts .doc, .rtf and the like itself. None where it is not there."""
    exe = shutil.which("textutil") or ("/usr/bin/textutil" if Path("/usr/bin/textutil").is_file() else None)
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "-convert", fmt, "-encoding", "UTF-8", "-stdout", str(path)],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None


def _link_words(pages: list[str], html_text: str) -> list[str]:
    """Follow each link's words, where the pages hold them, with where it goes ("words (https://...)"): the text
    conversion drops the link, the web-page one keeps it."""
    for url, words in re.findall(r"""<a\b[^>]*?href\s*=\s*["'](https?://[^"']+)["'][^>]*>(.*?)</a>""", html_text, flags=re.S | re.I):
        words = " ".join(html.unescape(re.sub(r"<[^>]+>", "", words)).split())
        url = html.unescape(url)
        if not words or url in words:
            continue
        for i, page in enumerate(pages):
            if words in page and f"{words} (" not in page:
                pages[i] = page.replace(words, f"{words} ({url})", 1)
                break
    return pages


def _rtf_plain(data: bytes) -> str:
    """The words of an RTF file without the Mac's textutil: control words dropped, \\par a new line, \\page a page
    break, \\'e9 and \\u8364 their characters, the field and font tables skipped."""
    text = data.decode("latin-1")
    text = re.sub(r"\{\\\*[^{}]*(\{[^{}]*\}[^{}]*)*\}", lambda m: "" if "fldinst" in m.group(0) else m.group(0), text)
    text = re.sub(r"\{\\(?:fonttbl|colortbl|stylesheet|info)(?:[^{}]|\{[^{}]*\})*\}", "", text)

    def uni(m):
        n = int(m.group(1))
        return chr(n + 65536 if n < 0 else n)
    text = re.sub(r"\\u(-?\d+)\??", uni, text)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), text)
    text = re.sub(r"\\(par|line)\b ?", "\n", text)
    text = re.sub(r"\\page\b ?", "\f", text)
    text = re.sub(r"\\tab\b ?", "\t", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = re.sub(r"\\[^a-zA-Z]", "", text)
    return re.sub(r"[{}]", "", text)


def read_textutil(path: Path, kind: str):
    """An old Word (.doc) or rich text (.rtf) file, converted by textutil (pages where it marks a page break, links
    followed by where they go). Without textutil an .rtf is read directly; a .doc is not."""
    text = _textutil(path, "txt")
    if text is None:
        if kind == "rtf":
            text = _rtf_plain(path.read_bytes())
            html_text = ""
        else:
            raise HandbookError("Reading an old Word file (.doc) needs the Mac's textutil, which was not found. "
                                "Open it in Word and save it as .docx, then add that.")
    else:
        html_text = _textutil(path, "html") or ""
    from .prepare import split_long
    pages = [re.sub(r"\n{3,}", "\n\n", p).strip() for p in text.split("\f")]
    pages = _link_words([p for p in pages if p], html_text) if html_text else [p for p in pages if p]
    if not pages:
        pages = [""]
    return _reading(kind, [chunk for page in pages for chunk in split_long(page)])


# --- OpenDocument (.odt, .ods, .odp) -----------------------------------------------------------

T = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
XLINK = "{http://www.w3.org/1999/xlink}"
STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
PRES = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"
ROWS_PER_SHEET = 5000


def _odf_content(path: Path, what: str):
    z = _open(path, what)
    with z:
        root = _xml(z, "content.xml")
    if root is None:
        raise HandbookError(f"the {what} could not be read (it has no content part).")
    return root


def _odf_inline(el) -> str:
    """The words of a paragraph: spaces, tabs and line breaks, links followed by where they go, soft page breaks as "\\f"."""
    out = [el.text or ""]
    for child in el:
        tag = child.tag
        if tag == f"{T}s":
            out.append(" " * int(child.get(f"{T}c") or 1))
        elif tag == f"{T}tab":
            out.append("\t")
        elif tag == f"{T}line-break":
            out.append("\n")
        elif tag == f"{T}soft-page-break":
            out.append(_BREAK)
        elif tag == f"{T}note":
            pass
        elif tag == f"{T}a":
            words = _odf_inline(child)
            url = child.get(f"{XLINK}href") or ""
            out.append(f"{words} ({url})" if url.startswith("http") and words.strip() and url not in words else words)
        else:
            out.append(_odf_inline(child))
        out.append(child.tail or "")
    return "".join(out)


def _break_styles(root) -> set:
    names = set()
    for style in root.iter(f"{STYLE}style"):
        props = style.find(f"{STYLE}paragraph-properties")
        if props is not None and props.get(f"{FO}break-before") == "page":
            names.add(style.get(f"{STYLE}name"))
    return names


def _odf_cell_text(cell) -> str:
    kind = cell.get(f"{OFFICE}value-type")
    if kind in ("float", "currency"):
        v = float(cell.get(f"{OFFICE}value") or 0)
        return str(int(v)) if v.is_integer() else str(v)
    if kind == "percentage":
        return f"{round(float(cell.get(f'{OFFICE}value') or 0) * 100, 4):g}%"
    if kind == "date":
        return (cell.get(f"{OFFICE}date-value") or "").replace("T00:00:00", "")
    if kind == "boolean":
        return (cell.get(f"{OFFICE}boolean-value") or "").upper()
    return " ".join(" ".join(_odf_inline(p) for p in cell.findall(f"{T}p")).split())


def _odf_rows(table, links: bool = True) -> list[str]:
    rows = []
    for row in table.iter(f"{TABLE}table-row"):
        cells = []
        for cell in row:
            if cell.tag not in (f"{TABLE}table-cell", f"{TABLE}covered-table-cell"):
                continue
            text = _odf_cell_text(cell)
            repeat = min(int(cell.get(f"{TABLE}number-columns-repeated") or 1), 100 if text else 1)
            cells.extend([text] * repeat)
        line = "\t".join(cells).rstrip()
        if line.strip():
            rows.extend([line] * min(int(row.get(f"{TABLE}number-rows-repeated") or 1), 50))
    return rows


def _odf_blocks(el, lines: list, breaks: set) -> None:
    for child in el:
        tag = child.tag
        if tag in (f"{T}p", f"{T}h"):
            if child.get(f"{T}style-name") in breaks:
                lines.append(_BREAK)
            text = _odf_inline(child)
            if text.strip():
                lines.append(text)
        elif tag == f"{TABLE}table":
            lines.extend(_odf_rows(child))
        elif tag in (f"{T}list", f"{T}list-item", f"{T}list-header", f"{T}section", f"{T}table-of-content",
                     f"{T}index-body", f"{OFFICE}text", f"{DRAW}frame", f"{DRAW}text-box", f"{DRAW}g",
                     f"{DRAW}custom-shape", f"{DRAW}page", f"{OFFICE}presentation"):
            _odf_blocks(child, lines, breaks)


def read_odt(path: Path):
    """An OpenDocument text file: paragraphs, headings, lists and tables (rows with tabs), links followed by where
    they go; paged where the file marks a page break."""
    root = _odf_content(path, "OpenDocument text file")
    body = root.find(f"{OFFICE}body/{OFFICE}text")
    lines: list = []
    if body is not None:
        _odf_blocks(body, lines, _break_styles(root))
    pages = [re.sub(r"\n{3,}", "\n\n", chunk).strip() for chunk in "\n".join(lines).split(_BREAK)]
    return _reading("odt", [p for p in pages if p])


def read_ods(path: Path):
    """An OpenDocument spreadsheet: a page per sheet, a row per line, a tab between cells (as for .xlsx)."""
    root = _odf_content(path, "OpenDocument spreadsheet")
    pages = []
    for table in root.iter(f"{TABLE}table"):
        rows = _odf_rows(table)
        if rows:
            more = len(rows) > ROWS_PER_SHEET
            body = "\n".join(rows[:ROWS_PER_SHEET]) + (f"\n(sheet continues beyond {ROWS_PER_SHEET} rows)" if more else "")
            pages.append(f"Sheet: {table.get(f'{TABLE}name') or len(pages) + 1}\n{body}")
    return _reading("ods", pages)


def read_odp(path: Path):
    """An OpenDocument presentation: a page per slide (its text, tables as rows, links followed by where they go),
    then the speaker notes."""
    root = _odf_content(path, "OpenDocument presentation")
    pages = []
    for page in root.iter(f"{DRAW}page"):
        lines: list = []
        for child in page:
            if child.tag == f"{PRES}notes":
                notes = []
                _odf_blocks(child, notes, set())
                if notes:
                    lines.append("Speaker notes: " + " ".join(notes))
            else:
                _odf_blocks([child], lines, set())
        pages.append("\n".join(lines).strip())
    return _reading("odp", pages)


# --- old Excel (.xls), through xlrd ------------------------------------------------------------

def xls_sheets(path: Path) -> list[tuple[str, str]]:
    """[(sheet name, its rows as tab-separated lines)] for every sheet with something in it, read with xlrd:
    a share formatted as a percentage reads 73%, a date reads 2025-07-31, a whole number has no ".0"."""
    try:
        import xlrd
    except ImportError:
        raise HandbookError("Old Excel (.xls) files need xlrd, which the tool's launcher installs. Run the tool "
                            "through lib/hb, or save the file as .xlsx and add that.") from None
    try:
        try:
            book = xlrd.open_workbook(str(path), formatting_info=True)
        except NotImplementedError:
            book = xlrd.open_workbook(str(path))
    except Exception as e:  # noqa: BLE001 (damaged, or locked with a password)
        raise HandbookError(f"the Excel file could not be read ({type(e).__name__}: damaged, or locked with a "
                            "password).") from None

    def show(cell) -> str:
        t, v = cell.ctype, cell.value
        if t in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
            return ""
        if t == xlrd.XL_CELL_DATE:
            when = xlrd.xldate_as_datetime(v, book.datemode)
            return when.date().isoformat() if not (when.hour or when.minute or when.second) else when.isoformat()
        if t == xlrd.XL_CELL_BOOLEAN:
            return "TRUE" if v else "FALSE"
        if t == xlrd.XL_CELL_ERROR:
            return xlrd.error_text_from_code.get(v, "#ERROR")
        if t == xlrd.XL_CELL_NUMBER:
            fmt = ""
            try:
                fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
            except (AttributeError, IndexError, KeyError):
                pass
            if "%" in fmt:
                return f"{round(v * 100, 4):g}%"
            return str(int(v)) if float(v).is_integer() else str(v)
        return str(v).strip()
    out = []
    for sheet in book.sheets():
        lines = []
        for n in range(sheet.nrows):
            if n >= ROWS_PER_SHEET:
                lines.append(f"(sheet continues beyond {ROWS_PER_SHEET} rows)")
                break
            cells = [show(c) for c in sheet.row(n)]
            if any(cells):
                lines.append("\t".join(cells).rstrip())
        if lines:
            out.append((sheet.name, "\n".join(lines)))
    return out


def read_xls(path: Path):
    return _reading("xls", [f"Sheet: {title}\n{text}" for title, text in xls_sheets(path)])

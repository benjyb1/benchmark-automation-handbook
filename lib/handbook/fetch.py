"""Fetching a document politely, reading its text, and filing it under a project's `documents/`.

`fetch_bytes` and `extract_text_html` are copied from scripts/retrieve.py (`fetch` and
`extract_text_html`), and `decode_html` from scripts/prep.py. Standard library only: the original
used `requests` and `trafilatura` when installed; this uses urllib and a plain tag strip, so it
behaves the same on every machine.

A document is a folder `documents/<doc>/` holding `original.<ext>`, `meta.json` and, once
`prepare` has read it, `pages.json`. Until `prepare` runs the document is "not kept" in meta.json
(`kept` false, `file` null), which is the state the shape checks allow; `prepare` fills `file`.
"""
from __future__ import annotations
import gzip
import hashlib
import html
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .folder import HandbookError, now_stamp, write_json

USER_AGENT = "Mozilla/5.0 (compatible; HandbookFetcher/0.1; a benchmark's document collection)"
TIMEOUT_S = 30.0
MAX_BYTES = 25_000_000      # guidelines and reports can be large PDFs; bound pathological responses
TOO_LARGE = "File is larger than 25 MB and was not saved"       # said when a response passes MAX_BYTES
RETRY_WAIT_S = 2.0
EXTENSIONS = {"pdf": ".pdf", "html": ".html", "text": ".txt"}


# --- fetching -----------------------------------------------------------------------------------

@dataclass
class FetchResult:
    url: str
    status_code: int                  # 0 when the request never completed
    content: bytes
    content_type: str                 # lower case, without parameters
    failure: Optional[str] = None     # None on success; else "http-404" or "request-error: X"

    @property
    def ok(self) -> bool:
        return self.failure is None and self.status_code == 200 and bool(self.content)


def _get_once(url: str, timeout: float) -> FetchResult:
    """One GET. Never raises: trouble comes back as a FetchResult with `failure` set."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read(MAX_BYTES + 1)          # one byte over tells us the file is too big
            status = getattr(resp, "status", 200) or 200
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if len(content) > MAX_BYTES:
                return FetchResult(url, status, b"", ctype, failure="too-large")
            if (resp.headers.get("content-encoding") or "").lower() == "gzip" or content[:2] == b"\x1f\x8b":
                try:                                    # the archive serves some pages gzip-encoded
                    content = gzip.decompress(content)
                except (OSError, EOFError):
                    pass
    except urllib.error.HTTPError as exc:
        return FetchResult(url, exc.code, b"", "", failure=f"http-{exc.code}")
    except Exception as exc:  # noqa: BLE001  (URLError, socket timeout, bad scheme)
        return FetchResult(url, 0, b"", "", failure=f"request-error: {type(exc).__name__}")
    return FetchResult(url, status, content, ctype, failure=None if status == 200 else f"http-{status}")


def fetch_bytes(url: str, timeout: float = TIMEOUT_S, retries: int = 1) -> FetchResult:
    """Polite GET: a real User-Agent, a 30-second timeout and one retry when the site did not
    answer, said it was busy (429) or failed (5xx). Anything else (404, 403) is final."""
    result = _get_once(url, timeout)
    for _ in range(retries):
        if result.ok or not (result.status_code in (0, 429) or result.status_code >= 500):
            break
        time.sleep(RETRY_WAIT_S)
        result = _get_once(url, timeout)
    return result


def describe_failure(result: FetchResult) -> str:
    if result.failure == "too-large":
        return TOO_LARGE[0].lower() + TOO_LARGE[1:]
    if result.failure and result.failure.startswith("http-"):
        return f"the site answered {result.failure[5:]}"
    if result.failure:
        return "the site did not answer (it timed out or refused the connection)"
    return "the site sent nothing back"


# --- reading text -------------------------------------------------------------------------------

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w:.-]+)""", re.I)
# Browsers read these labels as the wider encoding; Python's narrow codecs choke on the extra
# characters real pages use.
_CHARSET_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "shift_jis": "cp932",
                    "shift-jis": "cp932", "x-sjis": "cp932", "iso-8859-1": "cp1252",
                    "latin1": "cp1252", "us-ascii": "cp1252"}


def decode_html(data: bytes) -> str:
    """HTML bytes to text in the page's own encoding: BOM, then the meta charset, then UTF-8,
    then cp1252. Reading every page as UTF-8 garbles Shift_JIS, GB2312 or windows-1251 pages, and
    the quote check then finds none of their quotes."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    m = _META_CHARSET.search(data[:8192])
    if m:
        label = m.group(1).decode("ascii", "ignore").lower()
        try:
            return data.decode(_CHARSET_ALIASES.get(label, label), errors="replace")
        except LookupError:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def extract_text_html(content: str | bytes, url: str = "") -> str:
    """The text of an HTML page: comments, scripts and styles dropped, tags stripped, entities
    decoded, whitespace collapsed to single spaces."""
    if isinstance(content, bytes):
        content = decode_html(content)
    if not content:
        return ""
    text = re.sub(r"(?s)<!--.*?-->", " ", content)
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def html_title(content: str | bytes) -> str:
    """The page's <title>, whitespace collapsed and cut to 200 characters; "" when it has none."""
    if isinstance(content, bytes):
        content = decode_html(content)
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", content or "")
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:200] if m else ""


def title_from_name(name: str) -> str:
    """A readable title from a file name or address ending: no extension, separators as spaces."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", urllib.parse.unquote(name).strip())
    stem = re.sub(r"[-_+\s]+", " ", stem).strip()
    return stem[:1].upper() + stem[1:]


def title_from_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return title_from_name(parts.path.rstrip("/").rsplit("/", 1)[-1]) or parts.netloc or url


def kind_of(content: bytes, content_type: str = "") -> Optional[str]:
    """"pdf", "html" or "text" for what the handbook can read; None for anything else."""
    if b"%PDF-" in content[:1024]:
        return "pdf"
    ctype = (content_type or "").lower()
    if "html" in ctype:
        return "html"
    if ctype.startswith("text/"):
        return "text"
    if ctype:
        return None
    head = content[:2048].lower()                       # no content type: look at the bytes
    if b"<html" in head or b"<!doctype html" in head:
        return "html"
    return None if b"\x00" in content[:2048] else "text"


# --- ids ----------------------------------------------------------------------------------------

def doc_id_for_url(url: str) -> str:
    """`d-` and the first ten characters of the SHA-1 of the address, tidied so that the same page
    gets the same id: outer spaces, the case of the scheme and host, a #fragment and one trailing
    slash on the path are ignored. The path's case and the ?query are kept."""
    return "d-" + hashlib.sha1(_tidy_url(url).encode("utf-8")).hexdigest()[:10]


def _tidy_url(url: str) -> str:
    url = url.strip()
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    userinfo, at, host = parts.netloc.rpartition("@")
    path = parts.path[:-1] if parts.path.endswith("/") else parts.path
    return urllib.parse.urlunsplit((parts.scheme.lower(), f"{userinfo}{at}{host.lower()}", path,
                                    parts.query, ""))


def doc_id_for_bytes(data: bytes) -> str:
    """`d-` and the first ten characters of the SHA-1 of a provided file's bytes."""
    return "d-" + hashlib.sha1(data).hexdigest()[:10]


# --- rows ---------------------------------------------------------------------------------------

def _some(items, limit=15) -> str:
    items = list(items)
    return ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def resolve_rows(project, rows=None, group=None) -> list[str]:
    """The row ids named by `rows` (ids) and `group` ("LABEL=VALUE", every row whose label
    matches, ignoring case), in `units.json` order. Raises HandbookError for an unknown row,
    unknown label or a group that matches nothing."""
    units = project.read("units").get("units") or []
    ids = [u.get("id") for u in units]
    chosen = set()
    unknown = [r for r in (rows or []) if r not in ids]
    if unknown:
        raise HandbookError(f"No row called {_some(unknown)}. The rows are: {_some(ids) or 'none yet'}.")
    chosen.update(rows or [])
    if group is not None:
        label, sep, value = group.partition("=")
        label, value = label.strip(), value.strip()
        if not sep or not label or not value:
            raise HandbookError(f"A group looks like LABEL=VALUE, for example brand=Acme. Got {group!r}.")
        labels = project.read("project").get("labels") or []
        if label not in labels:
            raise HandbookError(f"There is no label called {label}. The labels are: "
                                f"{_some(labels) or 'none yet'}.")
        matching = [u["id"] for u in units
                    if str((u.get("labels") or {}).get(label, "")).strip().casefold() == value.casefold()]
        if not matching:
            values = sorted({str((u.get("labels") or {}).get(label)) for u in units
                             if (u.get("labels") or {}).get(label)})
            raise HandbookError(f"No row has {label} = {value}. The values for {label} are: "
                                f"{_some(values) or 'none'}.")
        chosen.update(matching)
    return [i for i in ids if i in chosen]


def add_to_found(project, rows, doc_id: str) -> list[str]:
    """Put `doc_id` in `found/<row>.json` for each row; returns the rows it was newly added to."""
    added = []
    for row in rows:
        found = project.found(row)
        found["row"] = row
        docs = found.setdefault("documents", [])
        if doc_id not in docs:
            docs.append(doc_id)
            project.write_found(row, found)
            added.append(row)
    return added


# --- filing a document --------------------------------------------------------------------------

def new_meta(doc_id: str, *, title: str, how: str, sha1: str, host_type: str, url=None,
             archive_url=None, provided: bool = False) -> dict:
    """A meta.json for a document just saved: not yet read, so `kept` is false and `file` null."""
    return {"id": doc_id, "title": title, "publisher": None, "url": url, "archive_url": archive_url,
            "kept": False, "date": None, "date_basis": None, "fetched": now_stamp(), "how": how,
            "provided_by_owner": provided, "language": None, "host_type": host_type, "file": None,
            "sha1": sha1, "pages": None}


_NOT_FOUND_TITLE = re.compile(r"\b(?:not found|404|page (?:not|cannot be) found|error)\b", re.I)


def misleading_title(title: str, text: str) -> bool:
    """A page titled like an error ("Not Found | joejuice.com") whose text is a real page: in the
    Pecking Order fixture a scorer believed such a title and scored a page holding the policy as
    no evidence on all 14 questions."""
    from .prepare import STUB_CHARS
    return bool(title and _NOT_FOUND_TITLE.search(title)) and len((text or "").strip()) > 4 * STUB_CHARS


def store_document(project, meta: dict, data: bytes, extension: str) -> Path:
    """Write `documents/<id>/original<extension>`, then meta.json (its presence marks the
    document as filed). Returns the folder."""
    folder = project.doc_dir(meta["id"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"original{extension}"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    write_json(folder / "meta.json", meta)
    return folder


def has_document(project, doc_id: str) -> bool:
    return (project.doc_dir(doc_id) / "meta.json").is_file()


# --- weak sources -------------------------------------------------------------------------------
# A document is weak when its meta has `weak: {"why": ...}` (the finder's `fetch --weak`, or
# `mark-weak`), or when nobody marked it and it sits on a third-party site. `weak: false` records
# that someone cleared it, which also overrides the third-party rule. What the owner provided is
# never weak.

AUTO_WEAK_WHY = "A copy on a third-party site, not the organisation's own"


def clean_why(text) -> str:
    """The reason as one line: whitespace collapsed. Raises HandbookError when it is empty."""
    why = re.sub(r"\s+", " ", str(text or "")).strip()
    if not why:
        raise HandbookError("Say in one line why the source is weak, for example \"A news report, "
                            "not the organisation's own words\".")
    return why


def weak_why(meta) -> Optional[str]:
    """Why the document counts as a weak source, or None when it does not."""
    if not isinstance(meta, dict) or meta.get("provided_by_owner") is True:
        return None
    mark = meta.get("weak")
    if mark is False:
        return None
    if isinstance(mark, dict) and isinstance(mark.get("why"), str) and mark["why"].strip():
        return mark["why"].strip()
    return AUTO_WEAK_WHY if meta.get("host_type") == "third_party" else None


def is_weak(meta) -> bool:
    return weak_why(meta) is not None


def _meta_for_marking(project, doc_id: str) -> dict:
    meta = project.documents().get(doc_id)
    if meta is None:
        raise HandbookError(f"There is no document called {doc_id} in the project.")
    if meta.get("provided_by_owner") is True:
        raise HandbookError(f"{doc_id} ({meta.get('title') or 'untitled'}) was provided by the owner, "
                            "and documents the owner provided are never counted as weak.")
    return meta


def mark_weak(project, doc_id: str, why: str) -> dict:
    """Record `weak: {"why"}` on a document's meta.json and return the meta."""
    meta = _meta_for_marking(project, doc_id)
    meta["weak"] = {"why": clean_why(why)}
    write_json(project.doc_dir(doc_id) / "meta.json", meta)
    return meta


def clear_weak(project, doc_id: str) -> dict:
    """Record `weak: false` (checked, not weak; overrides the third-party rule) and return the meta."""
    meta = _meta_for_marking(project, doc_id)
    meta["weak"] = False
    write_json(project.doc_dir(doc_id) / "meta.json", meta)
    return meta

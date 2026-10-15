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

from . import readers
from .folder import HandbookError, now_stamp, write_json

USER_AGENT = "Mozilla/5.0 (compatible; HandbookFetcher/0.1; a benchmark's document collection)"
# Some government sites refuse any agent that is not a browser (India's mha.gov.in, 14 Oct 2026): a
# 403 is tried once more as the browser a person would read the page in.
BROWSER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/126.0 Safari/537.36")
TIMEOUT_S = 30.0
# Guidelines and reports can be large PDFs (Eroski's annual report is 25.2 MB); bound pathological responses.
MAX_BYTES = 60_000_000
TOO_LARGE = "File is larger than 60 MB and was not saved"       # said when a response passes MAX_BYTES
RETRY_WAIT_S = 2.0
EXTENSIONS = readers.EXTENSIONS          # the extension each kind is saved with (a picture keeps its own)
# Where the Mac keeps its trusted certificates: the tool's own Python may not know them all
# (7-Eleven's site, 14 Oct 2026: refused by the tool, fetched by curl).
SYSTEM_CERTS = "/etc/ssl/cert.pem"


# --- fetching -----------------------------------------------------------------------------------

@dataclass
class FetchResult:
    url: str
    status_code: int                  # 0 when the request never completed
    content: bytes
    content_type: str                 # lower case, without parameters
    failure: Optional[str] = None     # None on success; else "http-404" or "request-error: X"
    final_url: Optional[str] = None   # where the request ended up after redirects, when known

    @property
    def ok(self) -> bool:
        return self.failure is None and self.status_code == 200 and bool(self.content)


def _cert_failure(exc) -> bool:
    import ssl
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _landed_home(asked: str, final: str) -> bool:
    """Whether a link to a page deep in a site was sent to the site's home page instead (CPF's old
    report links, 14 Oct 2026: five addresses saved as five copies of the new home page)."""
    if not final or final == asked:
        return False
    homes = ("", "index.html", "index.htm", "index.php", "home", "en", "en/home")
    a, f = urllib.parse.urlsplit(asked), urllib.parse.urlsplit(final)
    a_path, f_path = a.path.strip("/").lower(), f.path.strip("/").lower()
    same_host = a.netloc.lower().removeprefix("www.") == f.netloc.lower().removeprefix("www.")
    if same_host and (a_path == f_path or a_path in homes):
        return False                   # a slash or www gained, or a home page asked for (code review)
    return f_path in homes and not f.query


def _get_once(url: str, timeout: float, context=None, agent: str = USER_AGENT) -> FetchResult:
    """One GET. Never raises: trouble comes back as a FetchResult with `failure` set. A certificate
    the tool's Python cannot check is tried once more against the Mac's own trusted certificates."""
    req = urllib.request.Request(url, headers={"User-Agent": agent, "Accept": "*/*"})
    try:
        opened = (urllib.request.urlopen(req, timeout=timeout, context=context) if context is not None
                  else urllib.request.urlopen(req, timeout=timeout))
        with opened as resp:
            content = resp.read(MAX_BYTES + 1)          # one byte over tells us the file is too big
            status = getattr(resp, "status", 200) or 200
            final = resp.geturl() if hasattr(resp, "geturl") else url
            if _landed_home(url, final):
                return FetchResult(url, 0, b"", "", failure="redirected-home")
            final_url = final if final and final != url else None
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
        if _cert_failure(exc):
            if context is None and os.path.isfile(SYSTEM_CERTS):
                import ssl
                return _get_once(url, timeout, ssl.create_default_context(cafile=SYSTEM_CERTS), agent)
            return _curl(url, timeout) or FetchResult(url, 0, b"", "", failure="ssl-cert")
        return FetchResult(url, 0, b"", "", failure=f"request-error: {type(exc).__name__}")
    return FetchResult(url, status, content, ctype, failure=None if status == 200 else f"http-{status}",
                       final_url=final_url)


def _curl(url: str, timeout: float) -> Optional[FetchResult]:
    """The Mac's own curl, which checks certificates with the system's store: INAO's site failed the
    tool's Python even with the Mac's certificate file, and curl fetched it in under a second (ALI,
    14 Oct 2026). None when curl is missing or fails too."""
    import shutil
    import subprocess
    curl = shutil.which("curl") or ("/usr/bin/curl" if os.path.isfile("/usr/bin/curl") else None)
    if not curl:
        return None
    try:
        proc = subprocess.run([curl, "-sSL", "--proto", "=http,https", "--proto-redir", "=http,https",
                               "--max-time", str(int(timeout)), "--max-filesize", str(MAX_BYTES),
                               "-A", BROWSER_AGENT, "-w", "\n%{http_code} %{content_type} %{url_effective}",
                               "--url", url],
                              capture_output=True, timeout=timeout + 10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 63:                         # curl: the file is over --max-filesize
        return FetchResult(url, 0, b"", "", failure="too-large")
    body, _, tail = proc.stdout.rpartition(b"\n")
    parts = tail.decode("ascii", "replace").split(" ", 2)
    if proc.returncode != 0 or not parts[0].isdigit():
        return None
    if len(body) > MAX_BYTES:                         # no length sent, so curl could not stop it
        return FetchResult(url, 0, b"", "", failure="too-large")
    status, ctype = int(parts[0]), (parts[1] if len(parts) > 1 else "").split(";")[0].strip().lower()
    if len(parts) > 2 and _landed_home(url, parts[2].strip()):
        return FetchResult(url, 0, b"", "", failure="redirected-home")
    return FetchResult(url, status, body if status == 200 else b"", ctype,
                       failure=None if status == 200 and body else f"http-{status}")


def fetch_bytes(url: str, timeout: float = TIMEOUT_S, retries: int = 1) -> FetchResult:
    """Polite GET: a real User-Agent, a 30-second timeout and one retry when the site did not
    answer, said it was busy (429) or failed (5xx). Anything else (404, 403) is final."""
    url = url.strip().replace(" ", "%20")            # an address copied with spaces (ALI, 14 Oct 2026)
    result = _get_once(url, timeout)
    if result.status_code == 403:
        result = _get_once(url, timeout, agent=BROWSER_AGENT)
    if result.status_code == 403:
        # Some sites refuse Python's own connection whatever it calls itself (carrefour.com, OWA test,
        # 15 Oct 2026) but serve the Mac's curl.
        result = _curl(url, timeout) or result
    for _ in range(retries):
        if result.ok or not (result.status_code in (0, 429) or result.status_code >= 500):
            break
        time.sleep(RETRY_WAIT_S)
        result = _get_once(url, timeout)
    return result


def describe_failure(result: FetchResult) -> str:
    if result.failure == "too-large":
        return TOO_LARGE[0].lower() + TOO_LARGE[1:]
    if result.failure == "ssl-cert":
        return "the site's security certificate could not be checked"
    if result.failure == "redirected-home":
        return "the site sent the address to its home page, so the page is no longer there"
    if result.failure == "http-202":
        return "the site answered 202 (it often means a check for robots, with no page sent)"
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


_BLOCK = r"(?:p|div|br|li|ul|ol|h[1-6]|tr|table|section|article|header|footer|dt|dd|blockquote|pre|hr|form|nav|aside|main|figure|figcaption|details|summary|fieldset|caption|thead|tbody|tfoot)"


def html_text(content: str | bytes, links: bool = False, pictures: bool = False) -> str:
    """The text of an HTML page as a reader sees it: a line for each block (paragraph, heading, list item, table
    row, line break), a tab between table cells, runs of spaces collapsed inside a line and empty lines left out.
    Comments, scripts and styles are dropped and entities decoded. `links`: each link followed by where it goes
    ("the BAP standard (https://bap.example/std)"). `pictures`: each picture as its alt text or file name (ACES
    gives each camp's level only as a logo)."""
    if isinstance(content, bytes):
        content = decode_html(content)
    if not content:
        return ""
    body = re.sub(r"(?s)<!--.*?-->", " ", content)
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    if pictures:
        def picture(m):
            tag = m.group(0)
            alt = re.search(r"""alt\s*=\s*["']([^"']*)["']""", tag, re.I)
            src = re.search(r"""src\s*=\s*["']([^"']*)["']""", tag, re.I)
            name = (alt.group(1).strip() if alt else "") or (src.group(1).rsplit("/", 1)[-1].split("?")[0] if src else "")
            return f" [picture: {html.unescape(name)}] " if name else " "
        body = re.sub(r"(?is)<img\b[^>]*>", picture, body)
    if links:
        def link(m):
            href = html.unescape(m.group(1))
            return f"{m.group(2)} ({href})" if href.startswith("http") and href not in m.group(2) else m.group(2)
        body = re.sub(r"""(?is)<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", link, body)
    body = re.sub(rf"(?is)</?{_BLOCK}\b[^>]*>", "\n", body)
    body = re.sub(r"(?is)</t[dh]\s*>", "\t", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    lines = [re.sub(r"[ \u00a0\r\f\v]+", " ", ln).strip(" ") for ln in body.split("\n")]
    lines = ["\t".join(c.strip() for c in ln.split("\t")).strip("\t") for ln in lines]
    return "\n".join(ln for ln in lines if ln.strip())


def extract_text_html(content: str | bytes, url: str = "") -> str:
    """The text of an HTML page, a line for each paragraph, heading, list item, table row and line break (see
    `html_text`). It used to run every line together; the quote check does not care, but a scorer reading a
    page of rules or a table does."""
    return html_text(content)


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


def kind_of(content: bytes, content_type: str = "", name: str = "") -> Optional[str]:
    """What the handbook can read the file as: "pdf", "image", "html", "text", "sheet" and the other
    kinds in readers.py; None for anything else. One list for fetch, add-document and the Set-up uploads."""
    return readers.kind_of(content, content_type, name)


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
    """Put `doc_id` in `found/<row>.json` for each row; returns the rows it was newly added to. A row it
    was taken off (`found --remove`) is skipped: re-fetching the page must not put it back (ACES re-test,
    15 Oct 2026: a page giving the camp's ACES level came back on the next fetch)."""
    added = []
    for row in rows:
        found = project.found(row)
        if doc_id in (found.get("removed") or []):
            continue
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


def held_back_copy(project, data: bytes) -> Optional[str]:
    """The name of the owner's held-back file that `data` is a copy of, or None. OWA (15 Oct 2026): the
    tracker's whole key, a Google Sheet export served from docs.google.com, was fetched as a document,
    byte for byte the file the owner gave as last results."""
    held = getattr(project, "held_back", None)
    if held is None or not held.is_dir():
        return None
    digest = hashlib.sha1(data).hexdigest()
    squeeze = (lambda b: re.sub(rb"\s+", b"", b))
    small = squeeze(data) if len(data) < 30_000_000 else None
    for f in held.rglob("*"):
        if f.is_file() and f.suffix.lower() != ".json":
            raw = f.read_bytes()
            if hashlib.sha1(raw).hexdigest() == digest or (small is not None and small and squeeze(raw) == small):
                return f.name
    return None


def store_document(project, meta: dict, data: bytes, extension: str) -> Path:
    """Write `documents/<id>/original<extension>`, then meta.json (its presence marks the
    document as filed). Returns the folder. Refuses a copy of the owner's held-back results."""
    copy = held_back_copy(project, data)
    if copy:
        raise HandbookError(f"This file is a copy of last edition's results the owner gave ({copy}), which "
                            "scorers must never see, so it was not saved.")
    folder = project.doc_dir(meta["id"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"original{extension}"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    write_json(folder / "meta.json", meta)
    return folder


def set_as_at(project, doc_id: str, when: str) -> None:
    """Record the date a document speaks as at, given by whoever filed it (`date_basis` "owner"):
    it outranks the PDF's own date, which is often when the file was saved, not what it states
    (ALDF's compendium: saved 2026-01-24, the law as at 1 September 2025)."""
    from .folder import read_json
    from .shapes import is_date
    if not is_date(when):
        raise HandbookError(f"--date must be a date like 2025-09-01, not {when!r}.")
    path = project.doc_dir(doc_id) / "meta.json"
    meta = read_json(path)
    meta["date"], meta["date_basis"] = when, "owner"
    write_json(path, meta)


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

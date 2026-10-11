"""The words of a web page that draws them with a script, and the archive's copy of a page.

`hb fetch` saves what the server sends. For a page built by JavaScript that is a shell ("Please
enable JavaScript"), and in the Pecking Order dev fixture eleven such shells were cited by 55
answers, every one of them then a quote the checker could not find. So when the text of a fetched
page is a shell (`prepare.is_stub`), fetch tries, in order:

1. `render_text(url)`: open the page in a headless browser and take the words it shows. Playwright
   is not a dependency of the handbook; it is used when some Python on the machine has it
   (`HANDBOOK_RENDER_PYTHON`, else the interpreters in RENDER_PYTHONS, else this one), through a
   small script run as a subprocess. With none, this step is skipped and says so.
2. `archive_copy(url, before)`: the Wayback Machine's capture nearest `before` (the cut-off date),
   fetched raw (`id_`), when its text is not a shell too.
3. Otherwise the document is filed as not kept, with the reason in meta.json (`not_kept_why`).

Standard library only here; the browser lives in the other interpreter.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from typing import Optional

from . import fetch

RENDER_PYTHONS = ("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",)
RENDER_TIMEOUT_S = 90
ARCHIVE_CDX = ("https://web.archive.org/cdx/search/cdx?url={url}&output=json&filter=statuscode:200"
               "&limit=-{limit}")
ARCHIVE_RAW = "https://web.archive.org/web/{stamp}id_/{url}"       # id_: the page as captured, no toolbar
ARCHIVE_PAGE = "https://web.archive.org/web/{stamp}/{url}"         # the page as the archive shows it

# Run in the interpreter that has Playwright: print the page's visible text as JSON (one string),
# or nothing and exit 3 when the page cannot be opened.
_SCRIPT = r"""
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
with sync_playwright() as p:
    try:
        browser = p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
    except Exception:
        try:
            page.goto(url, wait_until="load", timeout=60000)
        except Exception:
            browser.close(); sys.exit(3)
    page.wait_for_timeout(1500)
    text = page.evaluate("() => document.body ? document.body.innerText : ''")
    title = page.title()
    browser.close()
print(json.dumps({"title": title, "text": text}))
"""


def _has_playwright(python: str) -> bool:
    try:
        return subprocess.run([python, "-c", "import playwright.sync_api"], capture_output=True,
                              timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def renderer() -> Optional[str]:
    """The interpreter to render with, or None when no Python here has Playwright."""
    named = os.environ.get("HANDBOOK_RENDER_PYTHON")
    for python in ([named] if named else []) + list(RENDER_PYTHONS) + [sys.executable]:
        if python and (os.path.isfile(python) or shutil.which(python)) and _has_playwright(python):
            return python
    return None


def render_text(url: str, python: Optional[str] = None) -> Optional[dict]:
    """`{"title", "text"}` of the page as a browser shows it, or None when there is no renderer or
    the page would not open. `text` is whitespace-collapsed."""
    python = python or renderer()
    if python is None:
        return None
    try:
        got = subprocess.run([python, "-c", _SCRIPT, url], capture_output=True, text=True,
                             timeout=RENDER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if got.returncode != 0:
        return None
    try:
        out = json.loads(got.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None
    return {"title": re.sub(r"\s+", " ", out.get("title") or "").strip()[:200],
            "text": re.sub(r"\s+", " ", out.get("text") or "").strip()}


def archive_captures(url: str, before: Optional[str] = None, limit: int = 3) -> list[str]:
    """Timestamps of the Wayback Machine's last `limit` captures of `url` that answered 200, on or
    before `before` (YYYY-MM-DD, else today), latest first. The CDX index is asked, not the
    availability API: for the Pecking Order's KFC France pages the latter listed nothing while
    the index held captures."""
    to = (before or "").replace("-", "")[:8]
    query = ARCHIVE_CDX.format(url=urllib.parse.quote(url, safe=""), limit=limit) + (f"&to={to}" if to else "")
    probe = fetch.fetch_bytes(query, retries=2)          # the index answers empty now and then
    if not probe.ok or not probe.content.strip():
        probe = fetch.fetch_bytes(query, retries=1)
    if not probe.ok:
        return []
    try:
        rows = json.loads(probe.content.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return []
    stamps = [r[1] for r in rows[1:] if isinstance(r, list) and len(r) > 1 and re.fullmatch(r"\d{14}", str(r[1]))]
    return sorted(stamps, reverse=True)


def recover(url: str, before: Optional[str] = None, python: Optional[str] = None) -> dict:
    """What to keep for a page whose fetched text is a shell. Tries the live page rendered, then the
    archive's raw capture, then that capture rendered. Returns
    `{"kind": "text"|"html"|None, "content": bytes, "title", "text_source": "rendered"|"archive"|
    "archive_rendered"|None, "archive_url", "tried": [...], "why": ...}`; `kind` None means nothing
    readable was found and `why` says what was tried, for meta.json's `not_kept_why`."""
    tried = []
    out = {"kind": None, "content": b"", "title": "", "text_source": None, "archive_url": None, "tried": tried}
    python = python or renderer()
    if python is None:
        tried.append("no browser to render with (no Python here has Playwright)")
    else:
        got = render_text(url, python)
        if got is None:
            tried.append("the live page would not open in a browser")
        elif is_shell(got["text"]):
            tried.append("the live page rendered to nothing")
        else:
            out.update(kind="text", content=got["text"].encode("utf-8"), title=got["title"], text_source="rendered")
            return out
    stamps = archive_captures(url, before)
    if not stamps:
        tried.append("the archive has no capture of it")
    for stamp in stamps[:2]:
        raw = fetch.fetch_bytes(ARCHIVE_RAW.format(stamp=stamp, url=url))
        if raw.ok and not is_shell(fetch.extract_text_html(raw.content)):
            out.update(kind="html", content=raw.content, title=fetch.html_title(raw.content),
                       text_source="archive", archive_url=ARCHIVE_PAGE.format(stamp=stamp, url=url))
            return out
        tried.append(f"the archive's {stamp[:8]} capture is a shell too")
        if python is not None:
            got = render_text(ARCHIVE_PAGE.format(stamp=stamp, url=url), python)
            if got is not None and not is_shell(got["text"]):
                out.update(kind="text", content=got["text"].encode("utf-8"), title=got["title"],
                           text_source="archive_rendered", archive_url=ARCHIVE_PAGE.format(stamp=stamp, url=url))
                return out
            tried.append(f"the archive's {stamp[:8]} capture rendered to nothing")
    out["why"] = "The page draws its words with a script and no readable copy was found: " + "; ".join(tried) + "."
    return out


def is_shell(text: str) -> bool:
    """Whether a page's text is a script shell or an error page rather than its words."""
    from .prepare import is_stub                     # prepare imports fetch; keep the cycle lazy
    return is_stub([text]) or bool(re.search(
        r"page (?:you (?:were|are) looking for )?(?:was |has |could not be |cannot be )?(?:not found|moved|"
        r"doesn't exist|does not exist|found)|site can.t be reached", text or "", re.I))


def archive_copy(url: str, before: Optional[str] = None) -> Optional[fetch.FetchResult]:
    """The raw page of the Wayback Machine's latest capture of `url` on or before `before`, as a
    FetchResult whose `url` is the archive address; None when there is no capture or it could not
    be fetched."""
    for stamp in archive_captures(url, before):
        got = fetch.fetch_bytes(ARCHIVE_RAW.format(stamp=stamp, url=url))
        if got.ok:
            return got
    return None

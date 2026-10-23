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
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from . import fetch

RENDER_PYTHONS = ("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",)
RENDER_TIMEOUT_S = 90
ARCHIVE_CDX = ("https://web.archive.org/cdx/search/cdx?url={url}&output=json&filter=statuscode:200"
               "&limit=-{limit}")
ARCHIVE_RAW = "https://web.archive.org/web/{stamp}id_/{url}"       # id_: the page as captured, no toolbar
ARCHIVE_PAGE = "https://web.archive.org/web/{stamp}/{url}"         # the page as the archive shows it

# Run in the page before its text is taken: open every <details>, every section a button marks
# aria-expanded="false" (accordions, "read more"), and show each tab's panel in turn. A page that keeps its
# words behind these shows none of them in innerText (Common Sense's privacy policies: 7 of 9 misses). The
# words of every state are joined, each line once, the open page first. Clicks are skipped on links that
# lead elsewhere, menus and dialogs. Returns the text.
EXPAND_JS = r"""
async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const seen = new Set();
  const lines = [];
  const take = () => {
    const text = document.body ? document.body.innerText : '';
    for (const raw of text.split('\n')) {
      const t = raw.trim();
      if (t && !seen.has(t)) { seen.add(t); lines.push(t); }
    }
  };
  const safe = (el) => {
    if (el.matches('a[href]')) {
      const href = el.getAttribute('href') || '';
      if (!(href.startsWith('#') || href.startsWith('javascript'))) return false;
    }
    return !el.hasAttribute('aria-haspopup') || el.getAttribute('aria-haspopup') === 'false';
  };
  for (const d of document.querySelectorAll('details:not([open])')) d.open = true;
  for (let round = 0; round < 3; round++) {
    const closed = [...document.querySelectorAll('[aria-expanded="false"]')].filter(safe).slice(0, 80);
    if (!closed.length) break;
    for (const el of closed) { try { el.click(); } catch (e) { /* a button that will not click */ } }
    await sleep(150);
    for (const d of document.querySelectorAll('details:not([open])')) d.open = true;
  }
  // Buttons that say "More information", "Read more" and the like, with no aria-expanded to go by (TikTok's
  // Community Guidelines, 21 Oct 2026): clicked, then a moment for what they open to arrive.
  // One round only: a page that draws its buttons again after a click would have them clicked shut by a second.
  const MORE = /^\s*\+?\s*(?:more information|more info|more details|read more|show more|see more|view more|expand(?: all)?|show all|show details)\s*[+›>…]*\s*$/i;
  const more = [...document.querySelectorAll('button, [role="button"], summary')]
    .filter((b) => MORE.test(b.innerText || '') && safe(b)).slice(0, 80);
  for (const b of more) { try { b.click(); } catch (e) { /* a button that will not click */ } }
  if (more.length) await sleep(1500);
  for (const panel of document.querySelectorAll('[role="tabpanel"]')) {
    panel.hidden = false;
    if (getComputedStyle(panel).display === 'none') panel.style.display = 'block';
  }
  take();
  const tabs = [...document.querySelectorAll('[role="tab"]')].filter((t) => t.getAttribute('aria-selected') !== 'true' && safe(t)).slice(0, 40);
  for (const tab of tabs) {
    try { tab.click(); } catch (e) { continue; }
    await sleep(120);
    take();
  }
  return lines.join('\n');
}
"""

# Run in the interpreter that has Playwright: print the page's visible text as JSON (one string),
# or nothing and exit 3 when the page cannot be opened.
_SCRIPT = r"""
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
EXPAND_JS = sys.argv[2]
with sync_playwright() as p:
    try:
        browser = p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        browser = p.chromium.launch(headless=True)
    # Asked for as Chrome asks, not "HeadlessChrome": some sites abort that (Hamburg's law portal, 21 Oct 2026).
    probe = browser.new_page()
    agent = probe.evaluate("() => navigator.userAgent").replace("HeadlessChrome", "Chrome")
    probe.close()
    page = browser.new_page(user_agent=agent)
    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
    except Exception:
        try:
            page.goto(url, wait_until="load", timeout=60000)
        except Exception:
            browser.close(); sys.exit(3)
    page.wait_for_timeout(1500)
    try:
        text = page.evaluate(EXPAND_JS)
    except Exception:
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
    title = page.title()
    try:
        links = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).slice(0, 500)"
                              ".map(a => [a.href, (a.innerText || a.title || '').trim().slice(0, 80)])")
    except Exception:
        links = []
    browser.close()
print(json.dumps({"title": title, "text": text, "links": links}))
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
    the page would not open. `text` keeps its lines (runs of spaces collapsed, blank lines dropped)."""
    python = python or renderer()
    if python is None:
        return None
    try:
        got = subprocess.run([python, "-c", _SCRIPT, url, EXPAND_JS], capture_output=True, text=True,
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
            "links": [x for x in out.get("links") or [] if isinstance(x, list) and len(x) == 2],
            "text": "\n".join(ln for ln in (re.sub(r"[ \t\u00a0\r\f\v]+", " ", x).strip()
                                           for x in (out.get("text") or "").split("\n")) if ln)}


def archive_captures(url: str, before: Optional[str] = None, limit: int = 3) -> list[str]:
    """Timestamps of the Wayback Machine's last `limit` captures of `url` that answered 200, on or
    before `before` (YYYY-MM-DD, else today), latest first. The CDX index is asked, not the
    availability API: for the Pecking Order's KFC France pages the latter listed nothing while
    the index held captures."""
    global LAST_ARCHIVE_STATE
    if archive_resting():
        LAST_ARCHIVE_STATE = "busy"
        return []
    to = (before or "").replace("-", "")[:8]
    query = ARCHIVE_CDX.format(url=urllib.parse.quote(url, safe=""), limit=limit) + (f"&to={to}" if to else "")
    probe = fetch.fetch_bytes(query, retries=2)          # the index answers empty now and then
    if probe.status_code != 429 and (not probe.ok or not probe.content.strip()):
        probe = fetch.fetch_bytes(query, retries=1)
    if probe.status_code == 429:                         # still too busy after fetch's waits
        LAST_ARCHIVE_STATE = "busy"
        _rest_archive()
        return []
    if not probe.ok:
        LAST_ARCHIVE_STATE = "unreachable"
        return []
    try:
        rows = json.loads(probe.content.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        LAST_ARCHIVE_STATE = "unreachable"
        return []
    stamps = [r[1] for r in rows[1:] if isinstance(r, list) and len(r) > 1 and re.fullmatch(r"\d{14}", str(r[1]))]
    LAST_ARCHIVE_STATE = "found" if stamps else "none"
    return sorted(stamps, reverse=True)


# What the last archive_captures call met: "found", "none" (the index answered with no capture before
# the date), "busy" (it kept answering 429, too many requests) or "unreachable" (it did not answer).
# ChickenTrack (15 Oct 2026): an archive that refused connections was reported as having no copy.
LAST_ARCHIVE_STATE = "none"

# After the archive has stayed busy through fetch's waits, every `hb fetch` (each its own process) leaves
# it alone for ARCHIVE_REST_S, instead of waiting a minute per document again (BankTrack, 20 Oct 2026).
ARCHIVE_REST_S = 600
ARCHIVE_REST_FILE = Path(tempfile.gettempdir()) / "handbook-web-archive-busy"


def _rest_archive() -> None:
    try:
        ARCHIVE_REST_FILE.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def archive_resting() -> bool:
    """Whether the archive said it was too busy within the last ARCHIVE_REST_S seconds."""
    try:
        return time.time() - float(ARCHIVE_REST_FILE.read_text(encoding="utf-8").strip() or 0) < ARCHIVE_REST_S
    except (OSError, ValueError):
        return False


# What near_copy met at each capture of the last address it looked at, so archive_why_not says what happened
# (glaad-smsi, 21 Oct 2026: "no copy from before the cut-off (its latest is from 2026-03-08)" for a cut-off of
# 30 April, when the capture before it would not load).
NEAR_TRIED: tuple = ("", [])


def _day(stamp: str) -> str:
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"


def archive_why_not(url: str, before: Optional[str]) -> str:
    """Why the archive gave no usable copy, in words: unreachable, busy, what near_copy met at each capture
    from before the cut-off, no capture at all, or only captures after the cut-off (with the latest)."""
    if LAST_ARCHIVE_STATE == "unreachable":
        return "the web archive did not answer (try again later)"
    if LAST_ARCHIVE_STATE == "busy":
        return "the web archive was too busy to answer (it said 429, too many requests; try again later)"
    if NEAR_TRIED[0] == url and NEAR_TRIED[1]:
        return "; ".join(NEAR_TRIED[1])
    if before:
        later = archive_captures(url, None, limit=1)
        if later and later[0][:8] <= before.replace("-", "")[:8]:
            return (f"the web archive's latest copy, from {_day(later[0])}, is before the cut-off "
                    + ("but could not be used" if _near(later[0], before) else "but more than a year before it"))
        if later:
            return f"the web archive has no copy from before the cut-off (its latest is from {_day(later[0])})"
    return "the web archive has no copy of it"


ARCHIVE_NEAR_DAYS = 365   # an archive capture this close before a past cut-off is preferred to today's page


def _near(stamp: str, before: Optional[str]) -> bool:
    """Whether a capture (YYYYMMDDhhmmss) falls within ARCHIVE_NEAR_DAYS before `before`."""
    import datetime as _dt
    try:
        cut = _dt.date.fromisoformat(before)
        got = _dt.date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
    except (TypeError, ValueError):
        return False
    return 0 <= (cut - got).days <= ARCHIVE_NEAR_DAYS


def _past(before: Optional[str]) -> bool:
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(before) < _dt.date.today()
    except (TypeError, ValueError):
        return False


def recover(url: str, before: Optional[str] = None, python: Optional[str] = None,
            prefer_archive: Optional[bool] = None) -> dict:
    """What to keep for a page whose fetched text is a shell. When the cut-off (`before`) has passed,
    first the archive's raw capture from the year before it, since that is the page as it stood
    when the documents had to be out, not as it is today (`prefer_archive`, by default whether the
    cut-off has passed). Then the live page rendered, then the archive's raw capture, then that
    capture rendered. Returns
    `{"kind": "text"|"html"|"pdf"|None, "content": bytes, "title", "text_source": "rendered"|"archive"|
    "archive_rendered"|None, "archive_url", "tried": [...], "why": ...}`; `kind` None means nothing
    readable was found and `why` says what was tried, for meta.json's `not_kept_why`."""
    tried = []
    out = {"kind": None, "content": b"", "title": "", "text_source": None, "archive_url": None, "tried": tried}
    if prefer_archive is None:
        prefer_archive = _past(before)
    if prefer_archive and before:
        for stamp in [x for x in archive_captures(url, before) if _near(x, before)][:2]:
            raw = fetch.fetch_bytes(ARCHIVE_RAW.format(stamp=stamp, url=url))
            if raw.ok and fetch.kind_of(raw.content, raw.content_type) == "pdf":     # the file the link is for
                out.update(kind="pdf", content=raw.content, title="", text_source="archive",
                           archive_url=ARCHIVE_PAGE.format(stamp=stamp, url=url))
                return out
            if raw.ok and not is_shell(fetch.extract_text_html(raw.content)):
                out.update(kind="html", content=raw.content, title=fetch.html_title(raw.content),
                           text_source="archive", archive_url=ARCHIVE_PAGE.format(stamp=stamp, url=url))
                return out
            if python or renderer():
                got = render_text(ARCHIVE_PAGE.format(stamp=stamp, url=url), python or renderer())
                if got is not None and not is_shell(got["text"]):
                    out.update(kind="text", content=got["text"].encode("utf-8"), title=got["title"],
                               text_source="archive_rendered", archive_url=ARCHIVE_PAGE.format(stamp=stamp, url=url))
                    return out
            tried.append(f"the archive's {stamp[:8]} capture, near the cut-off, is a shell")
    python = python or renderer()
    if python is None:
        tried.append("no browser to render with (no Python here has Playwright)")
    else:
        got = render_text(url, python)
        if got is None:
            tried.append("the live page would not open in a browser")
        elif is_challenge(got["text"].encode("utf-8")):
            tried.append("the live page showed a robot check")
        elif is_shell(got["text"]):
            tried.append("the live page rendered to nothing")
        else:
            out.update(kind="text", content=got["text"].encode("utf-8"), title=got["title"], text_source="rendered",
                       links=got.get("links") or [])
            return out
    stamps = archive_captures(url, before)
    if not stamps:
        tried.append("the archive has no capture of it")
    for stamp in stamps[:2]:
        raw = fetch.fetch_bytes(ARCHIVE_RAW.format(stamp=stamp, url=url))
        if raw.ok and fetch.kind_of(raw.content, raw.content_type) == "pdf":
            out.update(kind="pdf", content=raw.content, title="", text_source="archive",
                       archive_url=ARCHIVE_PAGE.format(stamp=stamp, url=url))
            return out
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


_CHALLENGE = re.compile(rb"(?i)(captcha|cf-chl|challenge-platform|challenge validation|just a moment\.\.\.|"
                        rb"verify you are (?:a )?human|are you a robot|"
                        rb"checking your browser|ddos protection|access denied|request rejected|radware|"
                        rb"bot (?:detection|protection))")


def is_challenge(content: bytes) -> bool:
    """Whether a short page is a robot check or a refusal rather than the document."""
    return len(content) < 60_000 and bool(_CHALLENGE.search(content[:60_000]))


# A short page that is only a cookie banner or a location picker is not the menu (Patty Battles,
# 15 Oct 2026: Whataburger's and three Burger King pages, 316 to 896 characters, kept as menus).
_GATE = re.compile(r"(?:select|choose|pick|find|enter) (?:a|your) (?:location|restaurant|store|address|zip)|"
                   r"to (?:view|see) (?:the |our )?menu|we use cookies|cookie (?:settings|preferences|policy)|"
                   r"accept (?:all )?cookies|enable location|"
                   # Firewall blocks, login walls and 'needs JavaScript' notices in other languages (Global
                   # Tobacco Index, 15 Oct 2026: all three were kept as documents).
                   r"requested url was rejected|access denied|request rejected|support id is|"
                   r"(?:log|sign) ?in to (?:continue|view|access)|please (?:log|sign) ?in|"
                   r"javascript (?:ist )?(?:erforderlich|deaktiviert)|activer (?:le )?javascript|"
                   r"javascript (?:est )?(?:désactivé|requis)|habilite (?:o |el )?javascript", re.I)
GATE_CHARS = 2000
# A country, region or language chooser served in place of the document (Beyond Compliance, 15 Oct 2026: a .pdf
# link gave 9,447 characters starting "Please select your country / region", kept as the document). It is a list
# of short names, so it can run far past GATE_CHARS: it is a gate when it opens with the prompt and is mostly
# short lines.
_SELECTOR = re.compile(r"(?:select|choose|pick) (?:your |a |the )?(?:country|region|language|market|location|site)|"
                       r"country\s*/\s*region|region\s*/\s*language|(?:country|region) selector", re.I)


def _mostly_short_lines(text: str) -> bool:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return len(lines) >= 15 and sum(1 for ln in lines if len(ln) <= 40) >= 0.7 * len(lines)


_HIDES = re.compile(rb"""(?i)aria-expanded=["']false|<details[\s>]|class=["'][^"']*\b(?:accordion|collaps)|"""
                    rb""">\s*(?:<[^>]+>\s*)*(?:more information|read more|show more|see more|view more)\s*<""")


def hides_sections(content: bytes) -> bool:
    """Whether a web page keeps some of its words behind buttons, accordions or tabs (a browser opens them:
    EXPAND_JS). TikTok's Community Guidelines (21 Oct 2026) came through at 6,000 of 19,000 characters."""
    return bool(_HIDES.search(content[:3_000_000]))


# A page that is only a site's menus and a document's contents list, with none of the document's own text
# (de-transparenzranking, 22 Oct 2026: Hamburg's law portal saved 145 words of links and section headings and
# passed the empty-page check). Bounded: only a page of at most NAV_WORDS words, with at least NAV_LINES lines,
# whose lines of BODY_LINE words or more (sentences) hold under NAV_BODY_SHARE of its words.
NAV_WORDS, NAV_LINES, BODY_LINE, NAV_BODY_SHARE = 1500, 8, 10, 0.25


def navigation_only(text: str) -> bool:
    """Whether a page's text is menus and a contents list rather than the document's text."""
    lines = [ln.split() for ln in (text or "").splitlines() if ln.strip()]
    words = sum(len(ln) for ln in lines)
    if not words or words > NAV_WORDS or len(lines) < NAV_LINES:
        return False
    return sum(len(ln) for ln in lines if len(ln) >= BODY_LINE) < NAV_BODY_SHARE * words


_ANCHOR = re.compile(r"(?is)<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>")
_PRINT = re.compile(r"(?i)(?:\.pdf(?:$|[?#])|\b(?:pdf|print|printable|printversion|druck|drucken|druckansicht|"
                    r"druckversion|imprimer|imprimir|stampa|afdrukken)\b)")
PRINT_TRIES = 2          # links followed at most, for one page


def page_links(content: bytes | str, url: str) -> list[list[str]]:
    """[[absolute address, link text]] for the links in an HTML page, at most 500."""
    if isinstance(content, bytes):
        content = fetch.decode_html(content)
    out = []
    for href, inner in _ANCHOR.findall(content or "")[:500]:
        text = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", inner)).strip()[:80]
        out.append([urllib.parse.urljoin(url, href.strip()), text])
    return out


def _site(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return ".".join(host.split(".")[-2:])


def print_view(url: str, links) -> Optional[dict]:
    """The PDF or print view of the same document a page of menus links to, on the same site:
    `{"kind": "pdf"|"html", "content", "url", "title"}`, or None. PDFs first; PRINT_TRIES links at most."""
    seen, picks = {url.split("#")[0]}, []
    for href, text in links or []:
        href = str(href).split("#")[0]
        if not href.startswith(("http://", "https://")) or href in seen or _site(href) != _site(url):
            continue
        if _PRINT.search(urllib.parse.urlsplit(href).path + "?" + urllib.parse.urlsplit(href).query) or _PRINT.search(str(text)):
            seen.add(href)
            picks.append(href)
    picks.sort(key=lambda h: 0 if urllib.parse.urlsplit(h).path.lower().endswith(".pdf") else 1)
    for href in picks[:PRINT_TRIES]:
        got = fetch.fetch_bytes(href)
        if not got.ok:
            continue
        name = urllib.parse.urlsplit(href).path.rsplit("/", 1)[-1]
        kind = fetch.kind_of(got.content, got.content_type, name)
        if kind == "pdf":
            return {"kind": "pdf", "content": got.content, "url": href, "title": ""}
        if kind == "html":
            text = fetch.extract_text_html(got.content)
            if not is_shell(text) and not navigation_only(text):
                return {"kind": "html", "content": got.content, "url": href, "title": fetch.html_title(got.content)}
    return None


def is_shell(text: str) -> bool:
    """Whether a page's text is a script shell, a gate (cookie banner, location or country chooser) or an error
    page rather than its words."""
    from .prepare import is_stub                     # prepare imports fetch; keep the cycle lazy
    if len((text or "").strip()) < GATE_CHARS and (_GATE.search(text or "") or _SELECTOR.search(text or "")):
        return True
    if _SELECTOR.search((text or "")[:400]) and _mostly_short_lines(text):
        return True
    return is_stub([text]) or bool(re.search(
        r"page (?:you (?:were|are) looking for )?(?:was |has |could not be |cannot be )?(?:not found|moved|"
        r"doesn't exist|does not exist|found)|site can.t be reached|"
        r"^\W*(?:fatal error|parse error|internal server error|service unavailable|bad gateway)\b", text or "", re.I))


# Within this many days of the cut-off, today's page is close enough: no archive copy is looked for and it
# is not marked as today's version.
TODAY_GRACE_DAYS = 31


def wants_dated_copy(before: Optional[str]) -> bool:
    """Whether the cut-off is far enough past that today's page may not be the one that stood then."""
    import datetime as _dt
    try:
        return (_dt.date.today() - _dt.date.fromisoformat(before)).days > TODAY_GRACE_DAYS
    except (TypeError, ValueError):
        return False


def near_copy(url: str, before: Optional[str]) -> Optional[fetch.FetchResult]:
    """The archive's capture of a page from before a past cut-off, when one is nearer the cut-off than
    today is and is not a shell, robot check or error page: the page as it stood then. None otherwise
    (`archive_why_not` then says why). Its `url` is the archive address."""
    import datetime as _dt
    global NEAR_TRIED, LAST_ARCHIVE_STATE
    met: list[str] = []
    NEAR_TRIED = (url, met)
    if not wants_dated_copy(before):
        return None
    cut = _dt.date.fromisoformat(before)
    stamps = archive_captures(url, before, limit=4)
    near = [x for x in stamps if _near(x, before)][:3]          # a shell or a dead capture: the next older one
    if stamps and not near:
        met.append(f"the web archive's latest copy before the cut-off is from {_day(stamps[0])}, more than a year "
                   "before it")
    for stamp in near:
        got = _dt.date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        if (cut - got) >= (_dt.date.today() - cut):
            met.append(f"the archive's {_day(stamp)} copy is further from the cut-off than today's page")
            continue                     # today's page is nearer the cut-off than this capture
        raw = fetch.fetch_bytes(ARCHIVE_RAW.format(stamp=stamp, url=url))
        if raw.status_code == 429:           # the index answered but the capture stayed busy
            LAST_ARCHIVE_STATE = "busy"
            _rest_archive()
            return None
        if not raw.ok:
            met.append(f"the archive's {_day(stamp)} copy, before the cut-off, would not load ({fetch.describe_failure(raw)})")
            continue
        if fetch.kind_of(raw.content, raw.content_type) == "html" and (
                is_shell(fetch.extract_text_html(raw.content)) or is_challenge(raw.content)):
            met.append(f"the archive's {_day(stamp)} copy, before the cut-off, is a script shell or a robot check")
            continue
        return fetch.FetchResult(ARCHIVE_PAGE.format(stamp=stamp, url=url), 200, raw.content, raw.content_type)
    return None


def archive_copy(url: str, before: Optional[str] = None) -> Optional[fetch.FetchResult]:
    """The raw page of the Wayback Machine's latest capture of `url` on or before `before`, as a
    FetchResult whose `url` is the archive address; None when there is no capture or it could not
    be fetched."""
    for stamp in archive_captures(url, before, limit=6):
        got = fetch.fetch_bytes(ARCHIVE_RAW.format(stamp=stamp, url=url))
        # A capture of a robot check or an error is not the page (Planalto's 30-31 Dec 2019 captures,
        # 14 Oct 2026): try an earlier one.
        if got.ok and not (fetch.kind_of(got.content, got.content_type) == "html"
                           and (is_shell(fetch.extract_text_html(got.content)) or is_challenge(got.content))):
            return got
    return None

"""hb probe --project DIR --row ROW [--json]: try a row's data sources rather than read about them.

An open-data benchmark, 30 Oct 2026: whether procurement data carries company identifiers, links to tender documents or comes as
a bulk download agreed with the benchmark's researchers in 0 of 3 countries on several questions, and Brazil's portal was
scored as working when its links were broken and its API hard to use. The scorer read pages that describe the
data; nobody opened it. A probe opens it lightly: for each saved list or portal page of the row, up to
LINKS of its file and record links are requested, and each data file (a saved one, or one those links lead to)
has its first PEEK_BYTES read for its columns or fields. What it saw is filed for the row as a dated text
document, "Probe of ...", which the scorer quotes like any other page, so a Yes on a data feature rests on
what was tried, not on what a page claims.

A project turns it on with "probe": true in project.json; the finder then probes each row after its last
round (cmd_find.run_finder). Nothing here calls a model.
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections import Counter

from . import fetch
from .cli import command_parser, open_project
from .folder import HandbookError

LINKS = 10                   # links tried per saved page
PEEK_BYTES = 2_000_000       # read of a data file: enough for its header and a sample of records
TIMEOUT_S = 20
PAUSE_S = 2.0              # between two requests, so no site is asked fast
REFUSED = (202, 401, 403, 429, 503)   # what robot filters answer; not a broken link
DATA_ENDS = (".csv", ".json", ".xlsx", ".xls", ".ods", ".xml", ".zip")
DATA_HINTS = re.compile(r"(?i)\b(?:api|download|export|bulk|dataset|datos|dados|donnees|ocds|csv|json|xlsx)\b")
_KEY = re.compile(r'"([A-Za-z_][\w.-]{0,40})"\s*:')
OCDS_SAMPLE = 1000           # releases summarised from the part of an OCDS file read
ARCHIVE_KINDS = 5            # kinds of record page taken from the web archive when a site does not answer
ARCHIVE_EACH = 2             # captures of each kind
ARCHIVE_INDEX = ("https://web.archive.org/cdx/search/cdx?url={url}&matchType=prefix&output=json&fl=original,timestamp"
                 "&filter=statuscode:200&filter=mimetype:text/html&collapse=urlkey&from={start}&to={end}&limit=2000")
RECORD_TEXT = 1500           # characters kept from a record page that opened (its label and value lines)
_API = re.compile(r"(?i)\b(?:ocds|api)\b|/api/|/ocds")
_YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")


def peek(url: str, limit: int = PEEK_BYTES, tries: int = 3) -> dict:
    """{"status", "type", "bytes", "head"}: the first `limit` bytes of `url` (a ranged GET), or the failure. A site
    that says how long to wait (HTTP 429 with Retry-After, up to a minute) is asked again after that wait (the open-data benchmark
    Kenya, 5 Nov 2026: the national OCDS API allows 60 requests a minute and said to retry after 12 seconds)."""
    req = urllib.request.Request(url, headers={"User-Agent": fetch.USER_AGENT, "Range": f"bytes=0-{limit - 1}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            head = resp.read(limit)
            return {"status": getattr(resp, "status", 200) or 200, "bytes": len(head), "head": head,
                    "type": (resp.headers.get("content-type") or "").split(";")[0].strip().lower(),
                    "size": resp.headers.get("content-range", "").rpartition("/")[2] or resp.headers.get("content-length")}
    except urllib.error.HTTPError as exc:
        wait = str(exc.headers.get("retry-after") or "") if exc.headers else ""
        if exc.code == 429 and wait.isdigit() and int(wait) <= 60 and tries > 1:
            time.sleep(int(wait) + 1)
            return peek(url, limit, tries - 1)
        return {"status": exc.code, "bytes": 0, "head": b"", "type": ""}
    except Exception as exc:  # noqa: BLE001  (timeout, refused, bad address)
        return {"status": 0, "bytes": 0, "head": b"", "type": "", "error": type(exc).__name__}


def fields_of(head: bytes, ctype: str = "", name: str = "") -> str | None:
    """What a data file holds, in words, from its first bytes: a CSV's columns, a JSON file's field names, a
    spreadsheet's first row. None when it is not a data file this can read."""
    low = name.lower()
    if head[:2] == b"\x1f\x8b" or low.endswith(".gz"):
        # An open-data benchmark, 5 Nov 2026: OCP's yearly OCDS files are .jsonl.gz; the part read is unpacked as far as it goes
        try:
            head = zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(head, PEEK_BYTES * 20)
        except zlib.error:
            return None
        low, ctype = re.sub(r"\.t?gz$", "", low), ""
        if low.endswith(".tar") or ".csv.tar" in low:
            return "A compressed archive of several files (its contents were not listed)"
    if low.endswith((".jsonl", ".ndjson")) or "json" in ctype or low.endswith(".json") or head.lstrip()[:1] in (b"{", b"["):
        said = ocds_summary(head.decode("utf-8", "replace"))
        if said:
            return said
    if low.endswith(".csv") or "csv" in ctype:
        text = head.decode("utf-8-sig", "replace")
        lines = text.splitlines()
        if not lines:
            return None
        try:
            dialect = csv.Sniffer().sniff(lines[0])
        except csv.Error:
            dialect = csv.excel
        cols = next(csv.reader([lines[0]], dialect), [])
        first = next(csv.reader(lines[1:2], dialect), []) if len(lines) > 1 else []
        shown = "; first row: " + ", ".join(f"{c.strip()}={v.strip()[:40]}" for c, v in zip(cols[:25], first)) if first else ""
        return f"CSV with {len(cols)} columns: " + ", ".join(c.strip() for c in cols[:60]) + \
               f"; {max(0, len(lines) - 1)} rows in the part read" + shown
    if low.endswith((".json", ".jsonl", ".ndjson")) or "json" in ctype:
        keys = []
        for k in _KEY.findall(head.decode("utf-8", "replace")):
            if k not in keys:
                keys.append(k)
        return ("JSON with fields: " + ", ".join(keys[:60])) if keys else None
    if low.endswith((".xlsx", ".ods")) or "spreadsheet" in ctype or head[:2] == b"PK":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(head), read_only=True)
            ws = wb.worksheets[0]
            row = next(ws.iter_rows(max_row=1, values_only=True), ())
            return f"Spreadsheet, first sheet's columns: " + ", ".join(str(c) for c in row if c is not None)[:1500]
        except (zipfile.BadZipFile, KeyError, OSError, ValueError, StopIteration):
            return "Spreadsheet, too large to read in part (its columns were not seen)"
        except ImportError:
            return None
    return None


def _objects(text: str):
    """The complete JSON objects at the start of `text`, which may be cut off part way: the lines of a JSON-lines
    file, or the items of a package's "releases" or "records" list, as far as they are whole."""
    dec = json.JSONDecoder()
    stripped = text.lstrip()
    if stripped.startswith("{") and "\n{" in stripped[:PEEK_BYTES]:
        first = stripped.split("\n", 1)[0]
        try:
            json.loads(first)
            for line in stripped.splitlines():
                try:
                    yield json.loads(line)
                except ValueError:
                    return
            return
        except ValueError:
            pass
    m = re.search(r'"(releases|records)"\s*:\s*\[', text)
    if not m:
        try:
            yield json.loads(text)
        except ValueError:
            pass
        return
    i = m.end()
    while True:
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text) or text[i] != "{":
            return
        try:
            obj, i = dec.raw_decode(text, i)
        except ValueError:
            return
        yield obj


def _releases(objs):
    """OCDS releases from objects that are releases, records (their compiled release) or release packages."""
    for o in objs:
        if not isinstance(o, dict):
            continue
        if isinstance(o.get("releases"), list) and "ocid" not in o:
            yield from (r for r in o["releases"] if isinstance(r, dict))
        elif isinstance(o.get("compiledRelease"), dict):
            yield o["compiledRelease"]
        elif "ocid" in o:
            yield o


def ocds_summary(text: str) -> str | None:
    """What the first OCDS_SAMPLE releases of an OCDS file hold, in counts and example values, or None when the text
    is not OCDS. An open-data benchmark, 4 Nov 2026: Kenya's API lists "identifier" for every party, yet every scheme is the portal's
    own "Bidders"; field names alone would have passed as proof of company identifiers."""
    rel = []
    for r in _releases(_objects(text)):
        rel.append(r)
        if len(rel) >= OCDS_SAMPLE:
            break
    if not rel:
        return None
    n = len(rel)

    def share(test):
        k = sum(1 for r in rel if test(r))
        return f"{k} of {n}"

    def lst(r, *path):
        x = r
        for p in path:
            x = x.get(p) if isinstance(x, dict) else None
        return x if isinstance(x, list) else []

    def docs_with_url(r):
        stages = [r.get("planning"), r.get("tender")] + lst(r, "awards") + lst(r, "contracts")
        return [d for st in stages if isinstance(st, dict) for d in st.get("documents") or [] if isinstance(d, dict) and d.get("url")]

    def has_value(x):
        return isinstance(x, dict) and isinstance(x.get("value"), dict) and x["value"].get("amount") not in (None, "")

    schemes, with_id, sup = Counter(), 0, 0
    for r in rel:
        for p in lst(r, "parties"):
            if isinstance(p, dict) and set(p.get("roles") or []) & {"supplier", "tenderer"}:
                sup += 1
                ident = p.get("identifier") or {}
                schemes[str(ident.get("scheme") or "none given")] += 1
                with_id += bool(ident.get("id"))
    dates = sorted(str(r.get("date"))[:10] for r in rel if r.get("date"))
    ocids = [r.get("ocid") for r in rel if r.get("ocid")]
    prefix = Counter("-".join(str(o).split("-")[:2]) for o in ocids).most_common(1)
    tp = next((r["tender"]["tenderPeriod"] for r in rel if isinstance(r.get("tender"), dict)
               and isinstance(r["tender"].get("tenderPeriod"), dict)), None)
    title = next((str(r["tender"]["title"]) for r in rel if isinstance(r.get("tender"), dict) and r["tender"].get("title")), "")
    award_ids = {str(a.get("id")) for r in rel for a in lst(r, "awards") if isinstance(a, dict)}
    linked = sum(1 for r in rel for c in lst(r, "contracts") if isinstance(c, dict) and str(c.get("awardID")) in award_ids)
    contracts = sum(len(lst(r, "contracts")) for r in rel)
    parts = [
        f"OCDS data; the first {n} releases in the part read were counted"
        + (f" (release dates {dates[0]} to {dates[-1]})" if dates else ""),
        f"with an ocid {len(ocids)} of {n}" + (f" (prefix {prefix[0][0]}, for example {ocids[0]})" if prefix else ""),
        f"with planning {share(lambda r: bool(r.get('planning')))}",
        f"with a tender {share(lambda r: isinstance(r.get('tender'), dict))}",
        f"with a tender period {share(lambda r: isinstance((r.get('tender') or {}).get('tenderPeriod'), dict))}"
        + (f" (for example {json.dumps(tp)[:120]})" if tp else ""),
        f"with a tender value {share(lambda r: has_value(r.get('tender')))}",
        f"with a tender title {share(lambda r: bool((r.get('tender') or {}).get('title')))}"
        + (f" (for example \"{title[:80]}\")" if title else ""),
        f"with a tender description {share(lambda r: bool((r.get('tender') or {}).get('description')))}",
        f"with tender items {share(lambda r: bool(lst(r, 'tender', 'items')))}",
        f"with awards {share(lambda r: bool(lst(r, 'awards')))}",
        f"with an award value {share(lambda r: any(has_value(a) for a in lst(r, 'awards')))}",
        f"with contracts {share(lambda r: bool(lst(r, 'contracts')))}"
        + (f" ({linked} of {contracts} contracts carry an awardID that matches an award in the same release)" if contracts else ""),
        f"with a contract period {share(lambda r: any(isinstance(c, dict) and c.get('period') for c in lst(r, 'contracts')))}",
        f"with implementation transactions {share(lambda r: any(lst(c, 'implementation', 'transactions') for c in lst(r, 'contracts') if isinstance(c, dict)))}",
        f"with any document that has a url {share(lambda r: bool(docs_with_url(r)))}",
    ]
    if sup:
        parts.append(f"{sup} supplier or tenderer parties, {with_id} with an identifier id; identifier schemes: "
                     + ", ".join(f"{k} ({v})" for k, v in schemes.most_common(6)))
    else:
        parts.append("no party has the role supplier or tenderer")
    lang = Counter(str(r.get("language")) for r in rel if r.get("language")).most_common(3)
    if lang:
        parts.append("language " + ", ".join(f"{k} ({v})" for k, v in lang))
    return "; ".join(parts)


def _study_years(project) -> set[int]:
    """The years of the study period, read from the project's cut-off (the cut-off's year and the two before)."""
    cut = str(project.read("project").get("cutoff") or "")[:4]
    return {int(cut) - 2, int(cut) - 1, int(cut)} if cut.isdigit() else set()


def candidates(project, doc: str) -> list[dict]:
    """The links of a saved page worth trying: data files first, then links whose words or address look like
    data (an API, a download, an export), then record pages in the page's own section, LINKS at most."""
    from .cmd_find import page_links, shared_links
    meta = project.documents().get(doc) or {}
    own = str(meta.get("url") or "")
    host = urllib.parse.urlsplit(own).hostname
    section = urllib.parse.urlsplit(own).path.rsplit("/", 1)[0] + "/"
    menus = shared_links(project)
    links = [l for l in page_links(project, doc) if l["url"].rstrip("/") != own.rstrip("/") and l["url"] not in menus]

    years = _study_years(project)
    # a page offering one file per year and format (OCP's Data Registry, the open-data benchmark Kenya, 5 Nov 2026: 3 formats a year
    # since 2018): only the study period's years are worth a try, and one file each, JSON first
    def year_of(l):
        found = _YEAR.findall(urllib.parse.unquote(l["url"]))
        return int(found[-1]) if found else None
    fmt = lambda l: next((i for i, e in enumerate((".jsonl.gz", ".json", ".jsonl", ".csv", ".xlsx")) if e in l["url"].lower()), 9)
    yearly = [l for l in links if year_of(l) and DATA_HINTS.search(l["url"]) and "download" in l["url"].lower()]
    if len(yearly) >= 6:
        keep = {}
        for l in sorted(yearly, key=fmt):
            y = year_of(l)
            if y in years and y not in keep:
                keep[y] = l["url"]
        links = [l for l in links if l not in yearly or l["url"] in keep.values()]

    def rank(l):
        parts = urllib.parse.urlsplit(l["url"])
        path = parts.path.lower()
        if path.endswith(DATA_ENDS) or path.endswith(".gz"):
            return 0
        # an OCDS feed or an API, wherever it is linked from (the open-data benchmark Kenya, 5 Nov 2026: the registry page names the
        # national feed it reads from), before a page that only mentions downloads
        if _API.search(l["url"]) or _API.search(l["text"]):
            return 0
        if DATA_HINTS.search(l["text"]) or DATA_HINTS.search(l["url"]):
            return 1
        if len(section) > 1:
            return 2 if path.startswith(section) and len(path) > len(section) else 3
        # a site's front page (Senegal's procurement portal, 2 Nov 2026): its records are anywhere on the same site
        return 2 if parts.hostname == host and path.strip("/") else 3
    picked = [l for l in sorted(links, key=rank) if rank(l) < 3]
    return picked[:LINKS]


def _own_words(texts: list[list[str]], run: int = 6) -> list[list[str]]:
    """Each record page's words without those a site's pages share (its header, menus and footer): a word goes when
    a run of `run` words around it is also on more than half of the other pages that opened. One page alone is kept
    whole."""
    if len(texts) < 2:
        return texts
    grams = [{tuple(w[i:i + run]) for i in range(max(0, len(w) - run + 1))} for w in texts]
    out = []
    for k, w in enumerate(texts):
        others = [g for j, g in enumerate(grams) if j != k]
        shared = [False] * len(w)
        for i in range(max(0, len(w) - run + 1)):
            if sum(tuple(w[i:i + run]) in g for g in others) * 2 > len(others):
                for j in range(i, i + run):
                    shared[j] = True
        out.append([x for x, s in zip(w, shared) if not s])
    return out


ROBOTS_SAID_NO = -1          # honest_peek's status for an address robots.txt asks tools not to open
ROBOTS_SILENT = -2           # honest_peek's status when the site did not answer for its robots.txt


def honest_peek(url: str, limit: int = PEEK_BYTES) -> dict:
    """peek as this tool says it is (fetch.USER_AGENT), and only where the site's robots.txt allows it (`hb probe
    --access`): a refusal is the answer, never asked again as a browser."""
    from . import access
    state = access.robots_state(url)
    if state != "allowed":
        return {"status": ROBOTS_SILENT if state == access.UNREACHABLE else ROBOTS_SAID_NO, "bytes": 0, "head": b"", "type": ""}
    return access.get(url, limit=limit, timeout=TIMEOUT_S)


def describe_data(head: bytes, ctype: str = "", name: str = "") -> str | None:
    """fields_of, read as data where it can be (`hb probe --access`): an OCDS file's counts, a JSON answer's records
    and how many of them fill each field, a CSV's columns with how many rows fill each and the dates they span."""
    from . import access
    low = name.lower()
    if head[:2] == b"\x1f\x8b" or low.endswith(".gz"):
        return fields_of(head, ctype, name)
    stripped = head.lstrip()[:1]
    if "json" in ctype or low.endswith((".json", ".jsonl", ".ndjson")) or stripped in (b"{", b"["):
        said = ocds_summary(head.decode("utf-8", "replace"))
        if said:
            return said
        try:
            said = access.records_summary(json.loads(access.decode_text(head)))
        except ValueError:
            said = None
        if said:
            return "JSON with " + said
    if low.endswith((".csv", ".txt")) or "csv" in ctype:
        said = access.csv_summary(head)
        if said:
            return said
    return fields_of(head, ctype, name)


ARCHIVE_LINKS = 3            # refused or silent data links of one page read from the web archive instead


def _data_like(url: str, text: str = "") -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    return path.endswith(DATA_ENDS) or path.endswith(".gz") or bool(_API.search(url) or DATA_HINTS.search(url)
                                                                     or DATA_HINTS.search(text or ""))


def _tried_lines(tried, peek_fn, describe=None, archive=None) -> tuple[list[str], int, int]:
    """One line per link tried, and how many opened and how many were refused or not answered. `describe` reads a
    data file (fields_of when not given); `archive(url)`, when given, is the web archive's capture of a data link the
    site refused or did not answer ({"stamp", "head", "type"} or None), ARCHIVE_LINKS at most."""
    describe = describe or fields_of
    seen, opened, refused, archived = [], 0, 0, 0
    for i, l in enumerate(tried, 1):
        if i > 1 and peek_fn in (peek, honest_peek):
            time.sleep(PAUSE_S)
        got = peek_fn(l["url"])
        ok = got["status"] in (200, 206) and got["bytes"] > 0
        opened += ok
        refused += got["status"] in REFUSED or not got["status"]
        what = describe(got["head"], got["type"], urllib.parse.urlsplit(l["url"]).path) if ok else None
        words = None
        if ok and what is None and ("html" in (got["type"] or "") or got["head"][:200].lstrip().lower().startswith(b"<")):
            # a record page (an open-data benchmark, 5 Nov 2026): its label and value lines are what a reader quotes, not "it opened"
            words = fetch.extract_text_html(got["head"], l["url"]).split()
        if ok:
            said = f"opened ({got['type'] or 'unknown type'}" + (f", {got['size']} bytes" if got.get("size") else "") + ")"
        elif got["status"] == ROBOTS_SAID_NO:
            said = "not opened: the site's robots.txt asks tools not to open this address"
        elif got["status"] == ROBOTS_SILENT:
            said = ("not opened: the site did not answer this tool when asked for its robots.txt, so nothing more was "
                    "asked of it")
        elif got["status"] in REFUSED:
            # a robot filter's answer (Brazil's transparency portal, 2 Nov 2026: 202 on every link): it says nothing
            # about whether the link works for a person, so it must not read as a broken link
            said = (f"refused this tool (HTTP {got['status']}, a check for robots), so whether it works for a person "
                    "is not known")
        elif not got["status"]:
            # no answer at all (Senegal's procurement portal timed out from here, 2 Nov 2026): the site, not the link
            said = "the site did not answer this tool (it timed out or refused the connection), so whether it works is not known"
        else:
            said = f"did not open (HTTP {got['status']})"
        if (archive is not None and archived < ARCHIVE_LINKS
                and (got["status"] in REFUSED or not got["status"] or got["status"] in (ROBOTS_SAID_NO, ROBOTS_SILENT))
                and _data_like(l["url"], l.get("text") or "")):
            # the site refused, did not answer or asks tools to keep out: the web archive's capture, never a second try
            # at the site
            cap = archive(l["url"])
            archived += 1
            if cap:
                from .access import day_of
                cap_what = describe(cap["head"], cap["type"], urllib.parse.urlsplit(l["url"]).path)
                if cap_what is None and "html" in (cap["type"] or ""):
                    own = " ".join(fetch.extract_text_html(cap["head"], l["url"]).split())
                    cap_what = f"its text begins: \"{own[:RECORD_TEXT]}\"" if len(own.split()) > 15 else None
                said += (f"; the web archive's capture of {day_of(cap['stamp'])} was read instead"
                         + (f": {cap_what}" if cap_what else ", but it holds nothing this tool can read as data"))
            else:
                said += "; the web archive has no capture of it from the study period"
        seen.append((i, l, said, what, words))
    # the words a site's pages share are its menus (the open-data benchmark Kenya, 5 Nov 2026: the Treasury's pages each began with the
    # same 60 words after their title), so each page is shown by its own words
    pages = [w for *_, w in seen if w is not None]
    own_of = dict(zip(map(id, pages), _own_words(pages)))
    lines = []
    for i, l, said, what, words in seen:
        if words is not None:
            own = " ".join(own_of[id(words)])
            what = (f"Its text begins: \"{own[:RECORD_TEXT]}\"" if len(own.split()) > 15 else
                    "It drew no text of its own (a page a script fills in)")
        lines.append(f"Link {i}, \"{l['text']}\" ({l['url']}): {said}" + (f". {what}." if what else "."))
    return lines, opened, refused


def kind_of_address(url: str) -> str:
    """A record page's kind: its path with numbers as #, and the query's words that are not numbers or ids
    (marchespublics.sn: "option=com_attribution&task=view_texte" for every award notice)."""
    parts = urllib.parse.urlsplit(url)
    words = sorted((k.lower(), v) for k, v in urllib.parse.parse_qsl(parts.query) if v and not re.search(r"\d", v))
    return re.sub(r"\d+", "#", parts.path) + ("?" + "&".join(f"{k}={v}" for k, v in words) if words else "")


def archived_records(site: str, start: str, end: str, get=None, pause: float = 3.0) -> list[dict]:
    """[{"url", "stamp", "head", "type"}]: up to ARCHIVE_EACH archived captures, taken between `start` and `end`
    (YYYYMMDD), of each of the ARCHIVE_KINDS commonest kinds of page under `site`, record pages (an address with an
    id) first. Senegal, 2 Nov 2026: the procurement portal did not answer this tool, while the web archive held
    some 300 of its record pages from the study period, which no finder could list."""
    from . import render
    get = get or (lambda u: fetch.fetch_bytes(u, retries=2))
    index = get(ARCHIVE_INDEX.format(url=urllib.parse.quote(site, safe=""), start=start, end=end))
    try:
        rows = json.loads(index.content.decode("utf-8", "replace"))[1:] if index.ok else []
    except ValueError:
        rows = []
    kinds: dict[str, list] = {}
    for r in rows:
        if isinstance(r, list) and len(r) > 1:
            kinds.setdefault(kind_of_address(r[0]), []).append((r[0], r[1]))
    has_id = lambda k: bool(re.search(r"[?&][^=]+=\d", kinds[k][0][0]) or "#" in k)
    order = sorted((k for k in kinds if len(kinds[k]) >= 2), key=lambda k: (not has_id(k), -len(kinds[k])))
    out = []
    for k in order[:ARCHIVE_KINDS]:
        picks = sorted(kinds[k], key=lambda x: x[1], reverse=True)          # the latest captures in the period
        for url, stamp in picks[:ARCHIVE_EACH]:
            if out:
                time.sleep(pause)                        # the archive is asked slowly
            got = get(render.ARCHIVE_RAW.format(stamp=stamp, url=url))
            if got.ok:
                out.append({"url": url, "stamp": stamp, "head": got.content[:PEEK_BYTES], "type": got.content_type})
    return out


ACCESS_PAGES = 6             # saved portal pages of one row opened in a browser (`hb probe --access`)
ACCESS_LINKS = 4             # pages linked from them that drew no text of their own, opened in a browser too
SCRIPTED_WORDS = 250         # a saved page with fewer words than this is taken as one a script fills in
ACCESS_ARCHIVE_RENDERS = 2   # of a site that did not answer the browser, captures opened in the web archive
ACCESS_FILES = 4             # data files named by one rendered page tried
ACCESS_APIS = 6              # API addresses named in the row's saved documents tried
PAGE_TEXT = 1500             # characters of a rendered page's text shown when it showed no table or list
_PORTAL_WORDS = re.compile(r"(?i)procur|tender|march[eé]|compra|contrat|licita|adjudic|concurso|award|achat|aquisi|"
                           r"adquisi|ocds|open.?data|datos|dados|donn[eé]es|etender|portal|anuncio|avis|appel|plan|"
                           r"attribution|statisti|resultado|journal|notice|supplier|fournisseur|proveedor|fornecedor")


def _unit_name(project, row: str) -> str:
    unit = next((u for u in project.read("units").get("units") or [] if u.get("id") == row), {})
    return str(unit.get("name") or row.replace("-", " ").title())


def _doc_text(project, doc: str) -> str:
    pages = project.pages(doc)
    if pages:
        return "\n".join(str(p.get("text") or "") for p in pages)
    original = next(iter(sorted(project.doc_dir(doc).glob("original.*"))), None)
    if original is None:
        return ""
    if original.suffix.lower() in (".html", ".htm"):
        from .render import page_links as links_of
        raw = original.read_bytes()
        return fetch.extract_text_html(raw) + "\n" + " ".join(u for u, _ in links_of(raw, str(project.documents().get(doc, {}).get("url") or "")))
    if original.suffix.lower() in (".txt", ".md", ".json"):
        return original.read_text("utf-8", "replace")
    return ""


def _scripted(text: str) -> bool:
    """Whether a saved page's text looks like a page a script fills in: a shell, menus only, or very few words."""
    from .render import is_shell, navigation_only
    return is_shell(text) or navigation_only(text) or len(text.split()) < SCRIPTED_WORDS


def _render_order(project, docs: dict, mine: list[str], skip: set, named: bool = False) -> list[str]:
    """The row's saved portal and data pages worth opening in a browser, ACCESS_PAGES at most: those whose saved copy
    is a shell, menus or a few words, as a script draws the rest. A page whose words were saved is read as it is. With
    `named` (the pages to probe were named, hb probe --docs) any such page is taken, whatever its title says (Botswana's
    procurement system calls its front page "Please wait...")."""
    def score(d):
        meta = docs[d]
        return len(_PORTAL_WORDS.findall(f"{meta.get('url') or ''} {meta.get('title') or ''}"))
    pages = []
    for d in mine:
        url = str(docs[d].get("url") or "")
        original = next(iter(sorted(project.doc_dir(d).glob("original.*"))), None)
        if d in skip or not url.startswith(("http://", "https://")) or original is None:
            continue
        if original.suffix.lower() not in (".html", ".htm", ".txt") or urllib.parse.urlsplit(url).path.lower().endswith(
                (".pdf", ".doc", ".docx") + DATA_ENDS):
            continue
        if (score(d) or named) and _scripted(_doc_text(project, d)):
            pages.append((-score(d), d))
    return [d for _, d in sorted(pages)][:ACCESS_PAGES]


def _rendered_lines(url: str, cap: dict | None, day: str, window: tuple[str, str], getter, where: str = "") -> tuple[list[str], list[dict]]:
    """What a page showed in the browser, as lines for the probe, and the data files it named (as links to try)."""
    from . import access, render
    lines, files = [], []
    if cap is None:
        return ["It could not be opened in a browser here (no Python on this machine has Playwright)."], []
    if cap.get("error"):
        return [f"Opened in a browser{where}: it would not open ({cap['error']})."], []
    text = cap.get("text") or ""
    if (cap.get("status") or 200) in REFUSED or render.is_challenge(text.encode("utf-8")):
        return [f"Opened in a browser{where}: the site answered with "
                + (f"HTTP {cap['status']}" if (cap.get('status') or 200) in REFUSED else "a robot check")
                + ", a refusal, so it was left there."], []
    lines.append(f"Opened in a browser{where} on {day}, as a headless browser that said so, with nothing clicked or "
                 f"filled in: \"{(cap.get('title') or '').strip()[:100]}\", {len(text):,} characters of text.")
    shown = 0
    for t in cap.get("tables") or []:
        said = access.table_summary(t)
        if said and shown < 3:
            lines.append(f"The page showed {said}.")
            shown += 1
    samples, docs_tried = 0, 0
    for ans in cap.get("answers") or []:
        try:
            body = json.loads(ans.get("body") or "")
        except ValueError:
            body = None
        said = access.records_summary(body) if body is not None else (
            access.csv_summary(ans["body"].encode("utf-8")) if "csv" in (ans.get("type") or "") else None)
        for u in access.data_urls_in(body if body is not None else ans.get("body") or ""):
            if u not in [f["url"] for f in files]:
                # a list of files the page offers (Mexico's Compras MX, 9 Nov 2026: its yearly contract files are named
                # only in the JSON its script loads)
                files.append({"url": u, "text": "named in what the page's script loaded"})
        if not said:
            continue
        shown += 1
        lines.append(f"Its own script asked its server for a list ({ans['method']} {ans['url'][:300]}"
                     + (f", sent {ans['post'][:200]}" if ans.get("post") else "") + f") and got {said}.")
        for what, url2, post2 in access.samples_for(ans, *window):
            if samples >= access.SAMPLES_PER_PAGE:
                break
            samples += 1
            time.sleep(access.PAUSE_S)
            got = getter(url2, data=post2.encode("utf-8") if ans["method"] == "POST" else None,
                         headers={"Content-Type": "application/json"} if ans["method"] == "POST" else None) \
                if access.robots_allows(url2, getter) else {"status": ROBOTS_SAID_NO, "bytes": 0, "head": b"", "type": ""}
            if got["status"] in (200, 206) and got["bytes"]:
                try:
                    said2 = access.records_summary(json.loads(access.decode_text(got["head"])))
                except ValueError:
                    said2 = None
                lines.append(f"The same list {what} by this tool directly ({url2[:300]}"
                             + (f", sent {post2[:200]}" if ans["method"] == "POST" else "") + "): "
                             + (said2 or "it answered, but with no records") + ".")
            elif got["status"] == ROBOTS_SAID_NO:
                lines.append(f"The same list was not {what}: the site's robots.txt asks tools not to.")
            elif got["status"] in REFUSED:
                lines.append(f"The same list {what} by this tool directly: the site answered HTTP {got['status']}, a "
                             "refusal, which was left as it is.")
            else:
                lines.append(f"The same list {what} by this tool directly: "
                             + (f"HTTP {got['status']}" if got["status"] else "no answer") + ".")
        if body is not None and docs_tried < 2:
            for u in access.document_urls(body, url):
                if u not in [f["url"] for f in files] and docs_tried < 2:
                    files.append({"url": u, "text": "a document a record of that list links to"})
                    docs_tried += 1
    for href, label in cap.get("links") or []:
        if access._DATA_FILE.search(urllib.parse.urlsplit(str(href)).path) and href not in [f["url"] for f in files]:
            files.append({"url": href, "text": str(label)[:80] or "a data file the page links to"})
    if not any(l.startswith("Its own script asked") for l in lines) and text.strip():
        # no list was read (the page carries its records in its own HTML, as Mozambique's UFSA does): its words, from
        # where its content starts
        lines.append(f"Its text, from where its content starts: \"{access.body_excerpt(text, PAGE_TEXT * 2)}\"")
    return lines, files


def _open_in_browser(url: str, render_fn, silent_hosts: dict, day: str, window, getter, robots_getter=None):
    """Lines for a page opened in the browser, and the data files it named. A site that did not answer the browser is
    not asked again for its other pages; up to ACCESS_ARCHIVE_RENDERS of its pages are opened from the web archive's
    capture before the cut-off instead."""
    from . import access as acc, render
    host = urllib.parse.urlsplit(url).hostname or ""
    state = acc.robots_state(url, getter)
    if state == "disallowed":
        return ["It was not opened in a browser: the site's robots.txt asks tools not to open this page."], []
    if state == acc.UNREACHABLE:
        silent_hosts.setdefault(host, 0)
        cap = {"error": "the site did not answer this tool when asked for its robots.txt"}
    else:
        cap = None
    if host not in silent_hosts:
        cap = render_fn(url)
        if cap is not None and cap.get("error"):
            silent_hosts[host] = 0          # archive captures opened for this site so far
    if host not in silent_hosts:
        return _rendered_lines(url, cap, day, window, getter)
    shown = [f"Opened in a browser: it would not open ({cap['error']})." if cap is not None else
             f"Not opened in a browser: {host} did not answer the browser for an earlier page."]
    if silent_hosts[host] >= ACCESS_ARCHIVE_RENDERS:
        return shown, []
    stamps = render.archive_captures(url, window[1], limit=3)
    if not stamps:
        return shown + ["The web archive has no capture of it from before the cut-off to open instead."], []
    silent_hosts[host] += 1
    more, files = _rendered_lines(url, render_fn(render.ARCHIVE_PAGE.format(stamp=stamps[0], url=url)), day, window,
                                  getter, where=f" from the web archive's capture of {acc.day_of(stamps[0])}")
    return shown + more, files


def _registry_docs(project, row: str, registry_fn, save_fn) -> list[str]:
    """Save the row's national publications in OCP's Data Registry (a page that names the official source and offers
    its files a year at a time) and file them for the row; their ids."""
    from .cmd_fetch import save_url
    out = []
    for pub in registry_fn(_unit_name(project, row)):
        try:
            out.append((save_fn or save_url)(project, pub["url"], [row], how="search", say=lambda *a, **k: None))
        except (HandbookError, OSError):
            continue
    return out


def _api_block(project, mine: list[str], window: tuple[str, str], peek_fn, describe, archive, getter) -> list[str]:
    """Lines for the APIs and API descriptions named in the row's saved documents: a description is read for the
    lists it offers and one request is made to each (for the study period, a small page); any other API address is
    tried as a link."""
    from . import access
    seen, lines, links = [], [], []
    for d in mine:
        for u in access.api_addresses(_doc_text(project, d)):
            if u not in [x for x, _ in seen]:
                seen.append((u, d))
    for u, d in seen[:ACCESS_APIS]:
        if re.search(r"(?i)swagger\.json|openapi\.json|api-docs|openapi\.ya?ml", u):
            got = peek_fn(u)
            source = "the site"
            if not (got["status"] in (200, 206) and got["bytes"]) and archive is not None:
                cap = archive(u)
                if cap:
                    got = {"status": 200, "bytes": len(cap["head"]), "head": cap["head"], "type": cap["type"]}
                    source = f"the web archive's capture of {access.day_of(cap['stamp'])}"
            try:
                spec = json.loads(access.decode_text(got["head"])) if got["status"] in (200, 206) else None
            except ValueError:
                spec = None
            if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
                lines.append(f"The API description {u} (named in {d}) could not be read"
                             + (f" (HTTP {got['status']})." if got["status"] else " (no answer)."))
                continue
            title = str((spec.get("info") or {}).get("title") or "an API")
            lines.append(f"The API description {u} (named in {d}), read from {source}: \"{title}\", offering "
                         + ", ".join(f"GET {p}" for p, o in spec["paths"].items() if isinstance(o, dict) and "get" in o)[:600] + ".")
            for sample in access.openapi_samples(spec, u, *window):
                links.append({"url": sample, "text": f"a request built from {title}'s description"})
        else:
            links.append({"url": u, "text": f"named in {d}"})
    if links:
        more, opened, refused = _tried_lines(links, peek_fn, describe, archive)
        lines.append(f"{opened} of {len(links)} API requests and addresses opened"
                     + (f"; {refused} refused this tool or did not answer it." if refused else "."))
        lines += [m.replace("Link ", "Request ", 1) for m in more]
    return lines


def probe_row(project, row: str, peek_fn=peek, today: str | None = None, extra: list[str] | None = None,
              only: list[str] | None = None, archive_fn=archived_records, access: bool = False, render_fn=None,
              registry_fn=None, save_fn=None, archive_exact_fn=None, getter=None) -> str | None:
    """Probe the row's saved data sources and file what was seen as one text document for the row; its id, or
    None when the row has nothing to probe. `extra` are addresses a person gave to try as well (hb probe --url),
    for example a national API that a portal's own scripts call; `only` limits the saved pages tried to these ids.

    `access` (hb probe --access, or "probe_access": true in project.json) reaches the data itself (access.py): the
    row's national feeds in OCP's Data Registry are saved and tried, its portal pages are opened in a headless
    browser and the lists their scripts load are read as records and asked for the study period, the APIs its saved
    documents name are tried, and a data link the site refuses or does not answer is read from the web archive.
    Every request says what it is, and robots.txt is followed with or without `access`. `render_fn`, `registry_fn`, `save_fn`,
    `archive_exact_fn` and `getter` stand in for the browser, the registry, hb fetch, the archive and the network."""
    from . import access as acc
    docs = project.documents()
    describe, archive = fields_of, None
    window = acc.study_window(project.cutoff_for([row]))
    peek_fn = honest_peek if peek_fn is peek else peek_fn     # robots.txt is followed on every probe, not only --access
    if access:
        describe = describe_data
        getter = getter or acc.get
        render_fn = render_fn or acc.render_capture
        archive_exact_fn = archive_exact_fn or acc.archive_exact
        archive = lambda u: archive_exact_fn(u, f"{int(window[1][:4]) - 2}{window[1][4:]}", window[1])  # noqa: E731
        registered = _registry_docs(project, row, registry_fn or acc.registry_publications, save_fn)
        docs = project.documents()
        if only:
            only = list(only) + [d for d in registered if d not in only]
    else:
        registered = []
    # every filed copy, read or not: the finder probes straight after its last round, before `hb prepare` has read
    # what it saved (Senegal, 2 Nov 2026: nothing was probed because no new document was "kept" yet)
    mine = [d for d in project.found(row).get("documents") or []
            if isinstance(d, str) and d in docs and not docs[d].get("probe_of") and not docs[d].get("not_kept_why")
            and next(iter(project.doc_dir(d).glob("original.*")), None) is not None and (not only or d in only)]
    day = today or fetch.now_stamp()[:10]
    to_render = set(_render_order(project, docs, mine, set(registered), named=bool(only))) if access else set()
    silent_hosts: dict[str, int] = {}
    script_links: list[dict] = []
    blocks = []
    for d in mine:
        meta = docs[d]
        name = urllib.parse.urlsplit(str(meta.get("url") or "")).path.rsplit("/", 1)[-1]
        original = next(iter(sorted(project.doc_dir(d).glob("original.*"))), None)
        lines = []
        if original is not None and original.suffix.lower() in DATA_ENDS:
            what = describe(original.read_bytes()[:PEEK_BYTES], "", original.name)
            if what:
                lines.append(f"The saved file itself: {what}.")
        if d in registered:
            lines.append("Found in OCP's Data Registry for this country by this tool; the page names the official "
                         "source the registry retrieves the data from, and offers its files a year at a time.")
        # every saved page with links, not only those saved as HTML (the open-data benchmark Kenya, 4 Nov 2026: PPIP and eGP were saved
        # as text and got no tries)
        tried = candidates(project, d) if original is not None and original.suffix.lower() not in DATA_ENDS else []
        shown = []
        if d in to_render:
            got_lines, files = _open_in_browser(str(meta.get("url") or ""), render_fn, silent_hosts, day, window, getter)
            shown += got_lines
            urls = acc.rank_by_years([f["url"] for f in files], _study_years(project))[:ACCESS_FILES]
            tried = [next(f for f in files if f["url"] == u) for u in urls] + [t for t in tried if t["url"] not in urls]
        more, opened, refused = _tried_lines(tried, peek_fn, describe, archive)
        if access:
            script_links += [t for t, m in zip(tried, more) if "It drew no text of its own" in m
                             and t["url"] not in [x["url"] for x in script_links]]
        lines += shown
        at = len(lines)
        lines += more
        unanswered = sum(1 for m in more if "the site did not answer this tool" in m)
        if tried and unanswered == len(tried) and archive_fn is not None:
            site = urllib.parse.urlsplit(str(meta.get("url") or ""))
            cut = (project.cutoff_for([row]) or "").replace("-", "")[:8]
            if site.hostname and cut:
                start = f"{int(cut[:4]) - 2}{cut[4:]}"
                got = archive_fn(f"{site.hostname}{site.path.rsplit('/', 1)[0]}/", start, cut)
                if got:
                    fake = {g["url"]: g for g in got}
                    arch, _, _ = _tried_lines([{"text": f"archived {g['stamp'][:4]}-{g['stamp'][4:6]}-{g['stamp'][6:8]}",
                                                "url": g["url"]} for g in got],
                                              lambda u, limit=0: {"status": 200, "bytes": len(fake[u]["head"]),
                                                                  "head": fake[u]["head"], "type": fake[u]["type"]},
                                              describe)
                    lines.append(f"The site did not answer, so {len(got)} of its record pages captured by the web "
                                 f"archive between {start[:4]}-{start[4:6]}-{start[6:]} and {cut[:4]}-{cut[4:6]}-{cut[6:]} "
                                 "were read instead (the archive's copy, dated by its capture):")
                    lines += [m.replace("Link ", "Archived page ", 1).replace(": opened (", ": read from the archive (", 1)
                              for m in arch]
        if tried:
            lines.insert(at, f"{opened} of {len(tried)} links tried opened"
                         + (f"; {refused} refused this tool or did not answer it, which is not the same as broken."
                            if refused else "."))
        if lines:
            blocks.append(f"## {meta.get('title') or d}\nSaved from {meta.get('url') or 'the owner'} ({d}).\n" + "\n".join(lines))
    if access and script_links:
        # pages the saved pages link to that drew no text of their own: a script fills them in, so a browser is asked
        script_links.sort(key=lambda l: -len(_PORTAL_WORDS.findall(f"{l['url']} {l['text']}")))
        bare = lambda u: str(u).split("#")[0].rstrip("/")  # noqa: E731  (an anchor is the same page)
        rendered = {bare(docs[d].get("url") or "") for d in to_render}
        lines, picked = [], []
        for l in script_links:
            if bare(l["url"]) not in rendered and bare(l["url"]) not in [bare(x["url"]) for x in picked]:
                picked.append(l)
        for l in picked[:ACCESS_LINKS]:
            got_lines, files = _open_in_browser(l["url"], render_fn, silent_hosts, day, window, getter)
            lines.append(f"### \"{l['text']}\" ({l['url']})")
            lines += got_lines
            if files:
                more, opened, refused = _tried_lines(files[:ACCESS_FILES], peek_fn, describe, archive)
                lines += more
        if lines:
            blocks.append("## Linked pages a script fills in, opened in a browser\n" + "\n".join(lines))
    if access:
        api = _api_block(project, mine, window, peek_fn, describe, archive, getter)
        if api:
            blocks.append("## APIs named in the saved documents\n" + "\n".join(api))
    if extra:
        tried = [{"text": "given to the probe", "url": u} for u in extra]
        lines, opened, refused = _tried_lines(tried, peek_fn, describe, archive)
        blocks.append(f"## Addresses given to the probe\n{opened} of {len(tried)} opened.\n" + "\n".join(lines))
    if not blocks:
        return None
    text = (f"# Probe of {len(blocks)} saved source{'s' if len(blocks) != 1 else ''}, {day}\n\n"
            f"What this tool saw on {day} when it opened links on the saved pages below and read the first part of "
            "their data files. A link that opened was requested and answered; the columns or fields listed are "
            "those in the part of the file read."
            + (" A page \"opened in a browser\" was opened as a headless browser that said so, where the site's "
               "robots.txt allows it, with nothing clicked; the lists its own script loaded are read as records (how "
               "many, which fields are filled in how many of them, the dates they span), and were asked again for "
               f"the study period ({window[0]} to {window[1]}) or for their oldest page as a person paging back would. "
               "A site's refusal was left as it is; the web archive's copy, dated by its capture, is read instead."
               if access else "")
            + "\n\n" + "\n\n".join(blocks) + "\n")
    data = text.encode("utf-8")
    doc = fetch.doc_id_for_bytes(data)
    if not fetch.has_document(project, doc):
        first = docs[mine[0]] if mine else {"url": (extra or [None])[0]}
        meta = fetch.new_meta(doc, title=f"Probe of {row}'s data sources, {day}", how="search",
                              url=str(first.get("url") or "") or None, host_type=first.get("host_type") or "none",
                              sha1=hashlib.sha1(data).hexdigest())
        meta["probe_of"] = [d for d in mine if any(d in b for b in blocks)]
        fetch.store_document(project, meta, data, ".txt")
    fetch.add_to_found(project, [row], doc)
    return doc


def main(argv) -> int:
    parser = command_parser("probe", "Open a row's data sources lightly and file what was seen.")
    parser.add_argument("--row", required=True)
    parser.add_argument("--url", action="append", default=None, metavar="ADDRESS",
                        help="an address to try as well, for example a national API (repeat for more)")
    parser.add_argument("--docs", default=None, metavar="DOC,...", help="try only these saved documents of the row")
    parser.add_argument("--access", action="store_true",
                        help="reach the data itself: OCP's Data Registry, the portal pages opened in a headless browser "
                             "and their lists read as records, the APIs the documents name, the web archive for a site "
                             "that refuses (also on when project.json has \"probe_access\": true)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    if args.row not in {u.get("id") for u in project.read("units").get("units") or []}:
        raise HandbookError(f"There is no row {args.row} in units.json.")
    doc = probe_row(project, args.row, extra=args.url, only=args.docs.split(",") if args.docs else None,
                    access=args.access or bool(project.read("project").get("probe_access")))
    print(json.dumps({"probe": doc}) if args.json else
          (f"Filed the probe as {doc}. Run hb prepare to read it." if doc else "Nothing to probe for this row."))
    return 0

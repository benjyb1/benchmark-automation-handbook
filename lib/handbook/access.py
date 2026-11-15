"""Reaching a row's data for itself, for `hb probe --access` (cmd_probe.probe_row with access=True).

An open-data benchmark, 4-5 Nov 2026: procurement was where the tool most often found nothing on new countries. Benin's, Angola's and
Mozambique's portals draw their tender lists with scripts, so the saved pages were empty shells; Mexico's open data
page names its yearly contract files only in the data its own script loads; South Africa's national feed is
republished by OCP's Data Registry, which no find had saved. So, for the row's saved portal pages, this:

1. opens each page in a headless browser (render.py's interpreter) and keeps what a person would see: its text, its
   tables, and the data its own script asked its server for (JSON or CSV), read as records, not as words;
2. finds the row's national feeds in OCP's Data Registry (`registry_publications`), and API guides named in the row's
   saved documents (`api_addresses`, `openapi_samples`), so their files can be sampled;
3. samples the records of the study period: a list the page loaded is asked again for the study period's dates, or
   for its oldest page, as a person would page back (`date_variant`, `last_page_variant`);
4. reads every data file or answer as data: how many records, which fields are filled in how many of them, the dates
   they span (`records_summary`, `csv_summary`);
5. when a site refuses or does not answer, reads the web archive's capture from the study period instead
   (`archive_exact`).

What it never does: work round a refusal. The browser and every request say what they are (the browser's own
headless agent, fetch.USER_AGENT); robots.txt is honoured (`robots_allows`); a 401, 403, 429 or robot check is the
answer, written down as such, and the web archive is the only way round it. No CAPTCHA is touched.

Standard library only; the browser lives in render.py's interpreter.
"""
from __future__ import annotations
import csv
import datetime as _dt
import html
import io
import json
import re
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Callable, Optional

from . import fetch

AGENT = fetch.USER_AGENT          # every request says it is this tool
ROBOT_NAME = "HandbookFetcher"    # the name robots.txt is asked about
TIMEOUT_S = 25
PAUSE_S = 2.0                     # between two requests to one site
REFUSED = (401, 403, 429)
RENDER_TIMEOUT_S = 150
MAX_BODY = 1_500_000              # of each answer a rendered page's script received
MAX_BODIES = 30
SAMPLES_PER_PAGE = 3              # study-period or oldest-page requests made for one rendered page
RECORDS_SEEN = 200                # records of one list counted
FIELDS_SHOWN = 30
REGISTRY = "https://data.open-contracting.org"
REGISTRY_SEARCH = REGISTRY + "/en/search/"
REGISTRY_TAKE = 2

_DATE = re.compile(r"(?<!\d)((?:19|20)\d\d)-(\d\d)-(\d\d)|(?<!\d)(\d\d)/(\d\d)/((?:19|20)\d\d)(?!\d)")
# a list's "from" and "to" dates: a compound name ending a key (dateFrom, filterToDate, fecha_inicio), or a short
# word that is the whole key or its last part (from, desde, du), never the end of another word ("objeto", "contrato")
_FROM = re.compile(r"(?i)(?:date_?from|from_?date|filter_?from_?date|start_?date|date_?start|fecha_?(?:inicio|inicial|desde)|"
                   r"data_?(?:inicio|inicial|de)|date_?debut|date_?min|periodo_?inicio)$|"
                   r"^(?:dt|data|date|fecha)[a-z_]*?(?:inicio|inicial|desde|from|start|debut)$|"
                   r"(?:^|[_.\-])(?:from|since|desde|du|de|inicio|start)$")
_TO = re.compile(r"(?i)(?:date_?to|to_?date|filter_?to_?date|end_?date|date_?end|fecha_?(?:fin|final|hasta)|"
                 r"data_?(?:fim|final|ate)|date_?fin|date_?max|periodo_?fim)$|"
                 r"^(?:dt|data|date|fecha)[a-z_]*?(?:fim|final|hasta|to|end|ate)$|"
                 r"(?:^|[_.\-])(?:to|until|hasta|au|ate|fim|fin|end)$")
_PAGE = re.compile(r"(?i)^(?:page|pagenumber|page_number|pagina|numeropagina|pageindex|page_index|p|pg|paginaatual)$")
_START = re.compile(r"(?i)^(?:idisplaystart|start|offset|skip|from_?row)$")
_SIZE = re.compile(r"(?i)^(?:size|pagesize|page_size|limit|per_?page|idisplaylength|length|rows|tamanhopagina|"
                   r"registrosporpagina|itensporpagina|itemsperpage|items_per_page|quantidade)$")
_TOTAL = re.compile(r"(?i)^(?:total|totalelements|total_elements|totalrecords|itotalrecords|itotaldisplayrecords|"
                    r"recordstotal|totalregistros|totalitems|total_count|totalcount|count|quantidadetotalitens|"
                    r"totalresults|total_results|numfound)$")
_PAGES = re.compile(r"(?i)^(?:totalpages|total_pages|pagecount|page_count|numpages|lastpage|last_page)$")
# words of the fields a list of procurement records carries, in the languages met so far; a site's menus and
# translations score none of them
_RECORD_WORDS = re.compile(
    r"(?i)(?:date|fecha|data|montant|amount|value|valor|monto|importe|price|prix|preco|precio|supplier|fournisseur|"
    r"proveedor|fornecedor|contractor|vendor|award|attribut|adjudic|tender|appel|licita|concurso|contrat|contract|"
    r"objet|objeto|object|title|titre|titulo|buyer|acheteur|comprador|entidad|entidade|autorite|procur|march|"
    r"ocid|ref|numero|number|status|estado|statut|procedimento|procedure|procedimiento|deadline|limite|epc|"
    r"rfc|nif|ninea|ifu|cnpj|currency|moneda|moeda|devise)")
_DATA_FILE = re.compile(r"(?i)\.(?:csv|xlsx?|ods|json|jsonl|ndjson|xml|zip|gz)(?:$|[?#])")
_URL = re.compile(r"https?://[^\s\"'<>\\)\]]+")
_API_WORDS = re.compile(r"(?i)swagger|openapi|api-docs|/api/|/ocds|ocds[-_]api|/v\d+/[a-z]")
_NATIONAL = re.compile(r"(?i)procur|contrat|contract|compras|tender|treasury|hacienda|finance|finanzas|financas|"
                       r"march[eé]s|achats|adquisi|aquisi|ppa\b|ppra|ppda|bpp|dncp|sercop|osce|chilecompra")
_LOCAL = re.compile(r"(?i)\b(?:state|estado|municip|county|city|ciudad|province|provincia|district|region|"
                    r"departamento|prefecture|governorate)\b")


# ---------------------------------------------------------------------------------------------------------------
# Asking politely


def get(url: str, data: Optional[bytes] = None, headers: Optional[dict] = None, limit: int = 2_000_000,
        timeout: float = TIMEOUT_S) -> dict:
    """{"status", "type", "bytes", "head", "size", "error"?}: the first `limit` bytes of `url` asked for as this tool
    (a ranged GET, or a POST of `data`). A 429 with a Retry-After of a minute or less is waited out once; anything
    else is the answer."""
    hdrs = {"User-Agent": AGENT, "Accept": "*/*", **(headers or {})}
    if data is None:
        hdrs.setdefault("Range", f"bytes=0-{limit - 1}")
    for attempt in range(2):
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                head = resp.read(limit)
                return {"status": getattr(resp, "status", 200) or 200, "bytes": len(head), "head": head,
                        "type": (resp.headers.get("content-type") or "").split(";")[0].strip().lower(),
                        "size": resp.headers.get("content-range", "").rpartition("/")[2] or resp.headers.get("content-length")}
        except urllib.error.HTTPError as exc:
            wait = str(exc.headers.get("retry-after") or "") if exc.headers else ""
            if exc.code == 429 and wait.isdigit() and int(wait) <= 60 and attempt == 0:
                time.sleep(int(wait) + 1)
                continue
            return {"status": exc.code, "bytes": 0, "head": b"", "type": "", "size": None}
        except Exception as exc:  # noqa: BLE001  (timeout, refused, bad address)
            return {"status": 0, "bytes": 0, "head": b"", "type": "", "size": None, "error": type(exc).__name__}
    return {"status": 429, "bytes": 0, "head": b"", "type": "", "size": None}


_ROBOTS: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
UNREACHABLE = "unreachable"


def robots_state(url: str, getter: Callable = None) -> str:
    """"allowed" or "disallowed" by the site's robots.txt for this tool, or UNREACHABLE when the site did not answer
    for its robots.txt in two tries (nothing more is then asked of it, RFC 9309). No robots.txt (a 4xx) allows all."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "disallowed"
    if parts.hostname == "web.archive.org":
        return "allowed"
    key = f"{parts.scheme}://{parts.netloc}"
    if key not in _ROBOTS:
        got = {"status": 0}
        for wait in (TIMEOUT_S, 45):
            got = (getter or get)(key + "/robots.txt", limit=500_000, timeout=wait)
            if got["status"] and got["status"] < 500:
                break
        parser = urllib.robotparser.RobotFileParser()
        text = got.get("head", b"").decode("utf-8", "replace") if got["status"] in (200, 206) else ""
        if got["status"] in (200, 206) and "<html" not in text[:500].lower():
            parser.parse(text.splitlines())
        elif got["status"] and got["status"] < 500:
            parser.parse([])                              # none, or a page in its place: nothing is asked
        else:
            parser = None                                 # the site did not answer at all
        _ROBOTS[key] = parser
    if _ROBOTS[key] is None:
        return UNREACHABLE
    return "allowed" if _ROBOTS[key].can_fetch(ROBOT_NAME, url) else "disallowed"


def robots_allows(url: str, getter: Callable = None) -> bool:
    """Whether the site's robots.txt lets this tool open `url` (robots_state is "allowed")."""
    return robots_state(url, getter) == "allowed"


def study_window(cutoff: Optional[str], days: int = 365) -> tuple[str, str]:
    """(start, end) as YYYY-MM-DD: the year before the cut-off (today when there is none)."""
    try:
        end = _dt.date.fromisoformat(str(cutoff)[:10])
    except (TypeError, ValueError):
        end = _dt.date.today()
    return (end - _dt.timedelta(days=days)).isoformat(), end.isoformat()


# ---------------------------------------------------------------------------------------------------------------
# Reading answers as data


def _flat(rec: dict, prefix: str = "", depth: int = 0) -> dict:
    out = {}
    for k, v in rec.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and depth < 1:
            out.update(_flat(v, key + ".", depth + 1))
        elif isinstance(v, list):
            out[key] = v if v else None
        else:
            out[key] = v
    return out


def _filled(v) -> bool:
    return v not in (None, "", [], {}, "null", "None", "-", "N/A", "n/a")


def _dates(values) -> list[str]:
    out = []
    for v in values:
        if isinstance(v, (int, float)) and 9.4e11 < v < 4.1e12:             # epoch milliseconds, 2000 to 2099
            out.append(_dt.datetime.fromtimestamp(v / 1000, tz=_dt.timezone.utc).date().isoformat())
            continue
        m = _DATE.search(str(v)) if v is not None else None
        if m:
            out.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m.group(1) else f"{m.group(6)}-{m.group(5)}-{m.group(4)}")
    return out


def _lists(obj, depth: int = 0):
    """Every list of objects inside `obj`, with the dict that holds it (for its totals)."""
    if depth > 5:
        return
    if isinstance(obj, list):
        if obj and sum(isinstance(x, dict) for x in obj) >= max(1, len(obj) // 2):
            yield obj, None
        for x in obj[:3]:
            yield from _lists(x, depth + 1)
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and v and sum(isinstance(x, dict) for x in v) >= max(1, len(v) // 2):
                yield v, obj
            elif isinstance(v, (dict, list)):
                yield from _lists(v, depth + 1)


def _record_score(records: list) -> int:
    keys = set()
    for r in records[:20]:
        if isinstance(r, dict):
            keys |= set(_flat(r))
    return sum(1 for k in keys if _RECORD_WORDS.search(k.rsplit(".", 1)[-1]))


def _total(obj, holder) -> Optional[int]:
    for scope in (holder, obj):
        if not isinstance(scope, dict):
            continue
        stack = [scope]
        while stack:
            d = stack.pop()
            for k, v in d.items():
                if _TOTAL.match(str(k)) and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                    return int(v)
                if isinstance(v, (int, str)) and _TOTAL.match(str(k)) and str(v).isdigit():
                    return int(v)
                if isinstance(v, dict) and len(stack) < 20:
                    stack.append(v)
    return None


def best_records(obj) -> tuple[list, Optional[int]]:
    """The list of records in a JSON answer most like procurement records, and the total the server says it holds
    (None when it does not say). ([], None) when there is no such list: a site's menus and translations are not
    records (they carry no date, value, supplier or reference fields)."""
    best, best_score, holder = [], 0, None
    for lst, hold in _lists(obj):
        score = _record_score(lst)
        if score > best_score or (score == best_score and len(lst) > len(best)):
            best, best_score, holder = lst, score, hold
    if best_score < 3:
        return [], None
    return [r for r in best if isinstance(r, dict)], _total(obj, holder)


def _example(v) -> str:
    if isinstance(v, list):
        return f"a list of {len(v)}"
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s[:60] + ("..." if len(s) > 60 else "")


def fields_line(rows: list[dict], shown: int = FIELDS_SHOWN) -> str:
    """'field (k of n, e.g. "x")' for the fields of flattened records, the most often filled first."""
    n = len(rows)
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    counts = {k: sum(1 for r in rows if _filled(r.get(k))) for k in keys}
    order = sorted(keys, key=lambda k: -counts[k])
    parts = []
    for k in order[:shown]:
        ex = next((r[k] for r in rows if _filled(r.get(k))), None)
        parts.append(f"{k} ({counts[k]} of {n}" + (f", e.g. \"{_example(ex)}\")" if ex is not None else ")"))
    more = len(order) - shown
    return "; ".join(parts) + (f"; and {more} more fields" if more > 0 else "")


def date_span(rows: list[dict]) -> str:
    """'dates in FIELD from A to B' for the date field with the most values, or ''."""
    best = None
    keys = {k for r in rows for k in r}
    for k in keys:
        ds = _dates(r.get(k) for r in rows)
        if len(ds) >= max(1, len(rows) // 3) and (best is None or len(ds) > len(best[1])):
            best = (k, ds)
    if not best:
        return ""
    ds = sorted(best[1])
    return f"dates in {best[0]} run from {ds[0]} to {ds[-1]}"


def records_summary(obj) -> Optional[str]:
    """What a JSON answer holds, as records: how many, of how many the server says it has, which fields are filled
    in how many, and the dates they span. None when it holds no list of records."""
    if isinstance(obj, (str, bytes)):
        try:
            obj = json.loads(obj)
        except (ValueError, TypeError):
            return None
    recs, total = best_records(obj)
    if not recs:
        return None
    rows = [_flat(r) for r in recs[:RECORDS_SEEN]]
    head = f"{len(recs)} records" + (f" (the server says it holds {total:,})" if total and total > len(recs) else "")
    span = date_span(rows)
    return f"{head}{'; ' + span if span else ''}; fields: {fields_line(rows)}"


def decode_text(data: bytes) -> str:
    """Bytes as text: UTF-8 when they are, else Windows-1252 (Mexico's CompraNet files)."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        cut = data[:-4] if len(data) > 4 else data            # a part read may stop inside a character
        try:
            return cut.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("cp1252", "replace")


def csv_summary(head: bytes, max_rows: int = 2000) -> Optional[str]:
    """A CSV's columns, how many rows of the part read fill each, and the dates it spans; None when it is not one."""
    text = decode_text(head)
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    if len(lines) > 2 and not text.endswith(("\n", "\r")):
        lines = lines[:-1]                                     # the last line was cut off by the part read
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO("\n".join(lines[:max_rows + 1])), dialect))
    cols = [c.strip() for c in rows[0]]
    if len(cols) < 2:
        return None
    seen = {}
    for c in cols:                                            # repeated column names are told apart
        seen[c] = seen.get(c, 0) + 1
    names, used = [], {}
    for c in cols:
        used[c] = used.get(c, 0) + 1
        names.append(c if seen[c] == 1 else f"{c} ({used[c]})")
    recs = [dict(zip(names, (v.strip() for v in r))) for r in rows[1:] if len(r) >= len(cols) // 2]
    if not recs:
        return f"CSV with {len(cols)} columns: " + ", ".join(cols[:80]) + "; no whole row in the part read"
    span = date_span(recs)
    return (f"CSV with {len(cols)} columns and {len(recs):,} rows in the part read"
            + (f"; {span}" if span else "") + f"; columns, with how many of those rows fill them: {fields_line(recs, 80)}")


def table_summary(table: dict) -> Optional[str]:
    """A table a rendered page showed: its column headings, how many rows it showed and the first few."""
    head = [re.sub(r"\s+", " ", str(h)).strip()[:60] for h in (table.get("head") or [])[:20]]
    rows = [[re.sub(r"\s+", " ", str(c)).strip()[:70] for c in r[:12]] for r in table.get("rows") or [] if any(str(c).strip() for c in r)]
    values = {c for r in rows for c in r if c}
    if len(values) < 3:
        return None                                    # the same banner or menu repeated
    # a table that lays out the page (a banner, a menu) is not data: data has at least three columns and differs by row
    if not rows or (table.get("count") or len(rows)) < 2 or max(len(head), max(len(r) for r in rows)) < 3 \
            or len({tuple(r) for r in rows}) < min(2, len(rows)) or sum(1 for c in (rows[0] if rows else []) if c) < 2:
        return None
    shown = "; ".join(" | ".join(c for c in r) for r in rows[:5])
    dates = table.get("dates")
    return (f"a table with {table.get('count') or len(rows):,} rows" + (f" and columns {', '.join(h for h in head if h)}" if any(head) else "")
            + (f"; the dates in its cells run from {dates[0]} to {dates[1]}" if dates else "")
            + f"; its first rows: {shown}")


def body_excerpt(text: str, limit: int = 1500) -> str:
    """A rendered page's text from where its content starts (the first line with a date, an amount or a sentence),
    after its menus, `limit` characters at most, lines joined by " | "."""
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in (text or "").split("\n")]
    lines = [ln for ln in lines if ln]
    start = next((i for i, ln in enumerate(lines) if _DATE.search(ln) or re.search(r"\d{4}-\d\d-\d\d|\d[\d .,]{4,}\d", ln)
                  or len(ln.split()) >= 8), 0)
    return " | ".join(lines[max(0, start - 3):])[:limit]


def data_urls_in(obj, base: str = "") -> list[str]:
    """Addresses of data files named inside a JSON answer or a page's links (a CSV, a spreadsheet, a JSON or zip
    file, or a download), in their order, each once."""
    out = []
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    for u in _URL.findall(text):
        u = html.unescape(u).rstrip(".,;")
        if (_DATA_FILE.search(urllib.parse.urlsplit(u).path) or re.search(r"(?i)download|descarga|telecharg", u)) and u not in out:
            out.append(u)
    return out


_DOC_FIELD = re.compile(r"(?i)fichier|file|document|doc|anexo|annexe|pdf|url|link|lien|arquivo|archivo")


def document_urls(obj, base: str = "", limit: int = 2) -> list[str]:
    """Up to `limit` addresses of documents (a notice, a tender file, a contract) that the records of a JSON answer link
    to, from fields named for files or documents: trying one shows whether a record's documents open."""
    recs, _ = best_records(obj)
    out = []
    for r in recs[:50]:
        for k, v in _flat(r).items():
            if not isinstance(v, str) or not _DOC_FIELD.search(k.rsplit(".", 1)[-1]):
                continue
            u = v.strip()
            if not u.startswith(("http://", "https://")) and base and re.search(r"(?i)\.(?:pdf|docx?|zip)$", u):
                u = urllib.parse.urljoin(base, u)
            if u.startswith(("http://", "https://")) and not re.search(r"(?i)\.(?:jpe?g|png|gif|svg|webp)$", u) and u not in out:
                out.append(u)
                break
        if len(out) >= limit:
            break
    return out


def rank_by_years(urls: list[str], years: set[int]) -> list[str]:
    """Data files of the study period's years first (the latest year first), then the rest in their order."""
    def year(u):
        ys = [int(y) for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", urllib.parse.unquote(u))]
        return ys[-1] if ys else None
    return sorted(urls, key=lambda u: (0, -year(u)) if year(u) in years else (1, 0))


# ---------------------------------------------------------------------------------------------------------------
# Asking a list again for the study period, or for its oldest page


def _shape_date(old: str, day: str) -> str:
    old = str(old or "")
    if re.fullmatch(r"\d\d/\d\d/\d{4}.*", old):
        return f"{day[8:10]}/{day[5:7]}/{day[:4]}"
    if "T" in old:
        return day + "T00:00:00"
    return day


def _swap_dates(pairs: list[tuple[str, str]], start: str, end: str) -> Optional[list[tuple[str, str]]]:
    froms = [i for i, (k, _) in enumerate(pairs) if _FROM.search(k.rsplit(".", 1)[-1])]
    tos = [i for i, (k, _) in enumerate(pairs) if _TO.search(k.rsplit(".", 1)[-1])]
    if not froms or not tos:
        return None
    out = list(pairs)
    for i in froms:
        out[i] = (out[i][0], _shape_date(out[i][1], start))
    for i in tos:
        out[i] = (out[i][0], _shape_date(out[i][1], end))
    return out


def date_variant(url: str, post: str, start: str, end: str) -> Optional[tuple[str, str]]:
    """(url, post body) asking the same list for `start` to `end`, when it takes a from and a to date in its query
    or its JSON body; None when it takes no dates."""
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    swapped = _swap_dates(q, start, end)
    if swapped is not None:
        return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(swapped))), post
    try:
        body = json.loads(post) if post else None
    except ValueError:
        body = None
    if isinstance(body, dict):
        flat = [(k, v) for k, v in body.items() if not isinstance(v, (dict, list))]
        swapped = _swap_dates([(k, str(v) if v is not None else "") for k, v in flat], start, end)
        if swapped is not None:
            new = dict(body)
            new.update({k: v for (k, v), (_, before) in zip(swapped, flat)
                        if _FROM.search(k) or _TO.search(k)})        # only the dates change; the rest keep their types
            return url, json.dumps(new)
    return None


def _total_pages(obj, size: Optional[int]) -> Optional[int]:
    if not isinstance(obj, dict):
        return None
    for k, v in obj.items():
        if _PAGES.match(str(k)) and isinstance(v, int) and v > 0:
            return v
    total = _total(obj, None)
    if total and size:
        return max(1, -(-total // size))
    return None


def last_page_variant(url: str, post: str, answer) -> Optional[tuple[str, str]]:
    """(url, post body) asking the same list for its last page (most lists are newest first, so that is the
    oldest), when it is paged and says how long it is; None otherwise."""
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    try:
        body = json.loads(post) if post else None
    except ValueError:
        body = None
    for where, pairs in (("query", q), ("body", [(k, str(v)) for k, v in body.items()] if isinstance(body, dict) else [])):
        size = next((int(v) for k, v in pairs if _SIZE.match(k) and str(v).isdigit() and int(v) > 0), None)
        pages = _total_pages(answer, size)
        total = _total(answer, None) if isinstance(answer, dict) else None
        for i, (k, v) in enumerate(pairs):
            new = None
            if _PAGE.match(k) and str(v).isdigit() and pages and pages > 1:
                new = str(pages - 1 + int(v)) if int(v) in (0, 1) else None        # 0- or 1-based, as the list is
            elif _START.match(k) and str(v).isdigit() and total and size and total > size:
                new = str(max(0, total - size))
            if new is None or new == str(v):
                continue
            if where == "query":
                q2 = list(q)
                q2[i] = (k, new)
                return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q2))), post
            b2 = dict(body)
            b2[k] = int(new)
            return url, json.dumps(b2)
    return None


def samples_for(answer: dict, start: str, end: str) -> list[tuple[str, str, str]]:
    """[(what, url, post body)]: the requests that ask a list a rendered page loaded for the study period and for its
    oldest page, as a person paging back would; [] for an answer that is not a list of records."""
    try:
        body = json.loads(answer.get("body") or "")
    except ValueError:
        return []
    if not best_records(body)[0]:
        return []
    out = []
    dated = date_variant(answer["url"], answer.get("post") or "", start, end)
    if dated:
        out.append((f"asked again for {start} to {end}", *dated))
    last = last_page_variant(answer["url"], answer.get("post") or "", body)
    if last:
        out.append(("asked for its last page (most lists put their oldest records there)", *last))
    return out


# ---------------------------------------------------------------------------------------------------------------
# The browser


# Run in render.py's interpreter: open the page as the headless browser it is (its own agent, which says
# "HeadlessChrome"), keep the JSON and CSV answers its script receives, its tables, text and links, and print them as
# one JSON object. Nothing on the page is clicked or filled in.
_CAPTURE = r"""
import json, sys
from playwright.sync_api import sync_playwright
cfg = json.loads(sys.stdin.read())
got = []
def keep(r):
    try:
        ct = (r.headers.get("content-type") or "").lower()
        if r.request.resource_type not in ("xhr", "fetch") and not ("json" in ct or "csv" in ct):
            return
        if not any(x in ct for x in ("json", "csv", "text/plain")) or len(got) >= cfg["max_bodies"]:
            return
        body = r.body()[:cfg["max_body"]]
        got.append({"url": r.url, "method": r.request.method, "status": r.status, "type": ct,
                    "post": (r.request.post_data or "")[:4000], "body": body.decode("utf-8", "replace")})
    except Exception:
        pass
out = {"url": cfg["url"]}
with sync_playwright() as p:
    try:
        browser = p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on("response", keep)
    resp = None
    try:
        resp = page.goto(cfg["url"], wait_until="networkidle", timeout=60000)
    except Exception as exc:
        try:
            resp = page.goto(cfg["url"], wait_until="load", timeout=45000)
        except Exception as exc2:
            out["error"] = (type(exc2).__name__ + ": " + str(exc2).splitlines()[0])[:200]
    if "error" not in out:
        page.wait_for_timeout(2500)
        out["status"] = resp.status if resp is not None else None
        out["title"] = page.title()
        try:
            out["text"] = page.evaluate("() => document.body ? document.body.innerText : ''")[:40000]
        except Exception:
            out["text"] = ""
        try:
            out["tables"] = page.evaluate('''() => Array.from(document.querySelectorAll('table'))
                .filter(t => !t.querySelector('table')).slice(0, 120).map(t => {
                const rows = Array.from(t.querySelectorAll(':scope > tbody > tr, :scope > thead > tr, :scope > tr'));
                const cells = r => Array.from(r.querySelectorAll(':scope > th, :scope > td')).map(c => (c.innerText || '').trim().slice(0, 80));
                const head = rows.length ? cells(rows[0]) : [];
                const days = [];
                for (const r of rows.slice(1, 5000)) {
                    for (const m of (r.innerText || '').matchAll(/(\d{4})-(\d\d)-(\d\d)|(\d\d)\/(\d\d)\/(\d{4})/g)) {
                        days.push(m[1] ? `${m[1]}-${m[2]}-${m[3]}` : `${m[6]}-${m[5]}-${m[4]}`);
                    }
                }
                days.sort();
                return {head: head, count: Math.max(0, rows.length - 1), rows: rows.slice(1, 9).map(cells),
                        dates: days.length ? [days[0], days[days.length - 1], days.length] : null};
            }).filter(t => t.count > 0).sort((a, b) => b.count - a.count).slice(0, 8)''')
        except Exception:
            out["tables"] = []
        try:
            out["links"] = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).slice(0, 600)"
                                         ".map(a => [a.href, (a.innerText || a.title || '').trim().slice(0, 80)])")
        except Exception:
            out["links"] = []
        out["agent"] = page.evaluate("() => navigator.userAgent")
    browser.close()
out["answers"] = got
print(json.dumps(out))
"""


def render_capture(url: str, python: Optional[str] = None) -> Optional[dict]:
    """What a headless browser saw at `url`: {"status", "title", "text", "tables", "links", "answers", "agent"} or
    {"error"} when the page would not open; None when no Python here has Playwright."""
    from . import render
    python = python or render.renderer()
    if python is None:
        return None
    cfg = {"url": url, "max_body": MAX_BODY, "max_bodies": MAX_BODIES}
    try:
        got = subprocess.run([python, "-c", _CAPTURE], input=json.dumps(cfg), capture_output=True, text=True,
                             timeout=RENDER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return {"url": url, "error": "the browser gave up waiting for the page"}
    try:
        return json.loads(got.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"url": url, "error": "the browser could not open the page"}


# ---------------------------------------------------------------------------------------------------------------
# Feeds and API guides


def _plain(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def registry_publications(country: str, getter: Callable = None, page: Optional[str] = None) -> list[dict]:
    """[{"url", "title"}]: the OCP Data Registry's publications for `country` by a national procurement or finance
    body (a state's, a city's or a civil society group's are left out), REGISTRY_TAKE at most. `page` is the search
    page's HTML when already read."""
    if page is None:
        got = (getter or get)(REGISTRY_SEARCH, limit=8_000_000)
        if got["status"] not in (200, 206):
            return []
        page = got["head"].decode("utf-8", "replace")
    want = _plain(country)
    out = []
    # each publication's card: its title, then (further on) the link to its page
    for m in re.finditer(r">\s*([^<>]{3,200}?):\s*([^<>]{2,250}?)\s*<", page):
        if _plain(html.unescape(m.group(1))) != want:
            continue
        link = re.search(r'href="(/[a-z]{2}/publication/\d+)"', page[m.end():m.end() + 20000])
        if not link:
            continue
        title = html.unescape(f"{m.group(1).strip()}: {m.group(2).strip()}")
        publisher = html.unescape(m.group(2))
        if not _NATIONAL.search(publisher) or _LOCAL.search(publisher):
            continue
        url = REGISTRY + link.group(1)
        if url not in [o["url"] for o in out]:
            out.append({"url": url, "title": title})
    return out[:REGISTRY_TAKE]


def api_addresses(text: str) -> list[str]:
    """Addresses in a text that are an API, an OCDS feed or an API's description (Swagger, OpenAPI), each once; a
    Swagger page gives the address of the description it shows."""
    out = []
    for u in _URL.findall(text or ""):
        u = html.unescape(u).rstrip(".,;:")
        if not _API_WORDS.search(u):
            continue
        if re.search(r"(?i)/swagger(?:/index\.html|/ui/?|/)?$", u):
            base = re.sub(r"(?i)(/swagger).*$", r"\1", u)
            u = base + "/v1/swagger.json"
        if u not in out:
            out.append(u)
    return out[:12]


def openapi_samples(spec: dict, spec_url: str, start: str, end: str, size: int = 50) -> list[str]:
    """For an API's OpenAPI or Swagger description: one request per list it offers (a GET with no path parameter),
    asking for `start` to `end` where it takes dates and a small page where it takes a page size, at most three."""
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
        return []
    parts = urllib.parse.urlsplit(spec_url)
    base = f"{parts.scheme}://{parts.netloc}"
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and str(servers[0].get("url") or "").startswith("http"):
        base = str(servers[0]["url"]).rstrip("/")
    elif spec.get("host"):
        base = f"{(spec.get('schemes') or [parts.scheme])[0]}://{spec['host']}{spec.get('basePath') or ''}".rstrip("/")
    out = []
    for path, ops in spec["paths"].items():
        op = ops.get("get") if isinstance(ops, dict) else None
        if not isinstance(op, dict) or "{" in path:
            continue
        q = []
        for prm in (op.get("parameters") or []) + (ops.get("parameters") or []):
            if not isinstance(prm, dict) or prm.get("in") != "query":
                continue
            name = str(prm.get("name") or "")
            fmt = str((prm.get("schema") or prm).get("format") or "")
            if _FROM.search(name):
                q.append((name, start + ("T00:00:00" if "time" in fmt else "")))
            elif _TO.search(name):
                q.append((name, end + ("T00:00:00" if "time" in fmt else "")))
            elif _PAGE.match(name):
                q.append((name, "1"))
            elif _SIZE.match(name):
                q.append((name, str(size)))
        out.append(base + path + ("?" + urllib.parse.urlencode(q) if q else ""))
        if len(out) >= 3:
            break
    return out


# ---------------------------------------------------------------------------------------------------------------
# The web archive, for a site that refuses or does not answer

ARCHIVE_EXACT = ("https://web.archive.org/cdx/search/cdx?url={url}&output=json&fl=timestamp,mimetype"
                 "&filter=statuscode:200&from={start}&to={end}&limit=-3")


def archive_exact(url: str, start: str, end: str, getter: Callable = None) -> Optional[dict]:
    """{"stamp", "head", "type"}: the web archive's latest capture of exactly `url` taken between `start` and `end`
    (YYYY-MM-DD), or None."""
    from . import render
    getter = getter or get
    index = getter(ARCHIVE_EXACT.format(url=urllib.parse.quote(url, safe=""), start=start.replace("-", ""),
                                        end=end.replace("-", "")), limit=200_000)
    try:
        rows = json.loads(index["head"].decode("utf-8", "replace"))[1:] if index["status"] in (200, 206) else []
    except ValueError:
        rows = []
    for row in sorted((r for r in rows if isinstance(r, list) and r), key=lambda r: r[0], reverse=True)[:2]:
        got = getter(render.ARCHIVE_RAW.format(stamp=row[0], url=url))
        if got["status"] in (200, 206) and got["bytes"]:
            return {"stamp": row[0], "head": got["head"], "type": got["type"]}
    return None


def day_of(stamp: str) -> str:
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"

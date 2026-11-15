"""The record behind a map viewer or data catalogue page, read from the site's own data service.

An open-data benchmark, 2 Nov 2026: in two Kenya land use runs the finder reached the benchmark's own source, RCMRD's "Kenya Land Use 2010"
layer, and four more land use layers, and fetch filed every one as not kept: the pages draw their map with a
script, so it saw a shell or the site's menus. Behind each page is a public record in plain JSON (who made the
data, when, how many records, its fields, its licence, whether it can be downloaded), which is what the land use
questions ask about. `read(url, content)` knows the page from its address and HTML, asks the platform's own API
for that record and writes it out as text the scorer can quote. Platforms: ArcGIS Hub sites, ArcGIS Online and
portal items (a web map's layers too), ArcGIS REST services, GeoNode and CKAN. A front page or search page of
such a site becomes a list of its records, matching the search words when the address has some.

Nothing here calls a model. Standard library only.
"""
from __future__ import annotations
import datetime as _dt
import html as _html
import json
import re
import urllib.parse
from typing import Callable, Optional

from . import fetch, render

TIMEOUT_S = 25
LIST_MAX = 60            # records listed for a front page or a search
LAYERS_MAX = 5           # a web map's layers read
FIELDS_MAX = 80
TEXT_MAX = 1500          # characters of one description
ARCGIS_SHARING = "https://www.arcgis.com/sharing/rest"
HUB_API = "https://hub.arcgis.com/api/v3"

_HEX = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?:_(\d+))?(?![0-9a-f])")
_THUMB = re.compile(r"(https?://[\w.:-]+(?:/[\w.-]+)*?/sharing/rest)/content/items/([0-9a-f]{32})(?:_(\d+))?/info/")
_SITE_ID = re.compile(r'"siteId"\s*:\s*"([0-9a-f]{32})"')
_HUB = re.compile(r"hubcdn|opendata-ui|\"hubSite\"")
_GEONODE = re.compile(r"(?i)/static/geonode/|geonode-mapstore-client|<meta[^>]+geonode")
_CKAN = re.compile(r"(?i)<meta[^>]+generator[^>]+ckan|/base/css/main\.css|data-module=\"[^\"]*\"[^>]*ckan|\bckan\b")
_TAGS = re.compile(r"(?s)<[^>]+>")
# The pages of a site that list its records: its front page, its search, its list of layers or datasets.
LIST_PAGES = ("", "search", "explore", "datasets", "layers", "catalogue", "maps", "home")


def _get_json(url: str):
    """The JSON a data service gives for `url`, or None when it did not answer with JSON."""
    got = fetch.fetch_bytes(url, timeout=TIMEOUT_S, retries=0)
    if not got.ok:
        return None
    try:
        return json.loads(got.content.decode("utf-8", "replace"))
    except ValueError:
        return None


def _html_of(content) -> str:
    if isinstance(content, bytes):
        return content[:2_000_000].decode("utf-8", "replace")
    return (content or "")[:2_000_000]


def platform(url: str, content=b"") -> Optional[str]:
    """Which data platform drew this page ("ArcGIS Hub", "ArcGIS", "ArcGIS REST", "GeoNode", "CKAN"), or None.
    From the address and the HTML only: nothing is fetched."""
    parts = urllib.parse.urlsplit(url)
    path, host, page = parts.path.lower(), (parts.hostname or "").lower(), _html_of(content)
    if "/rest/services" in path:
        return "ArcGIS REST"
    if _HUB.search(page) or host.endswith(".hub.arcgis.com"):
        return "ArcGIS Hub"
    if (host.endswith("arcgis.com") or "/home/item.html" in path or "/apps/" in path) and \
            (_HEX.search(parts.query) or _HEX.search(path)) and ("arcgis" in page.lower() or host.endswith("arcgis.com")):
        return "ArcGIS"
    if re.search(r"/(?:layers|datasets)/[\w.-]*:", path) or _GEONODE.search(page):
        return "GeoNode"
    if _CKAN.search(page):
        return "CKAN"
    return None


def read(url: str, content=b"", get: Optional[Callable] = None) -> Optional[dict]:
    """The record behind a map viewer or data catalogue page: {"text", "title", "platform", "source"} (`source`:
    the data service address read), or None when the page is not one, its service did not answer, or (CKAN) the
    page already holds its record as text. The page's own words follow the record when they are more than a
    shell or menus."""
    get = get or _get_json
    name = platform(url, content)
    if name is None:
        return None
    page = _html_of(content)
    words = fetch.extract_text_html(content) if page else ""
    page_fails = not words or render.is_shell(words) or render.navigation_only(words)
    if name == "CKAN" and not page_fails:
        return None
    got = {"ArcGIS Hub": _hub, "ArcGIS": _portal_item, "ArcGIS REST": _rest,
           "GeoNode": _geonode, "CKAN": _ckan}[name](url, page, get)
    if not got:
        return None
    title, lines, source = got
    head = [f"{title}", f"Read from the site's data service ({name}), because the page draws its record with a "
            "script.", f"Page: {url}", f"Data service: {source}", ""]
    text = "\n".join(head + [ln for ln in lines if ln is not None])
    if not page_fails:
        text += "\n\nThe page's own words:\n" + words.strip()
    return {"text": text + "\n", "title": title, "platform": name, "source": source}


# ---- small helpers ---------------------------------------------------------------------------------------------

def _day(value) -> Optional[str]:
    """A date as YYYY-MM-DD from epoch milliseconds, epoch seconds, a YYYYMMDD number (ArcGIS metadata) or an ISO
    string; None when it is not one."""
    if isinstance(value, int) and 19000101 <= value <= 21001231:
        value = str(value)
    elif isinstance(value, (int, float)) and value > 0:
        secs = value / 1000 if value > 10_000_000_000 else value
        try:
            return _dt.datetime.fromtimestamp(secs, _dt.timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    if isinstance(value, str) and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


def _plain(text, limit: int = TEXT_MAX) -> str:
    """Words of a short HTML snippet."""
    if not text:
        return ""
    out = re.sub(r"\s+", " ", _html.unescape(_TAGS.sub(" ", str(text)))).strip()
    return out if len(out) <= limit else out[:limit].rsplit(" ", 1)[0] + " ..."


def _line(label: str, value) -> Optional[str]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, bool):
        value = "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v not in (None, ""))
        if not value:
            return None
    return f"{label}: {value}"


def _count(n) -> Optional[str]:
    return f"{n:,}" if isinstance(n, int) else None


def _query_of(url: str, *names) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    frag = urllib.parse.parse_qs(urllib.parse.urlsplit(urllib.parse.urlsplit(url).fragment).query)
    for n in names:
        for src in (q, frag):
            if src.get(n) and src[n][0].strip():
                return src[n][0].strip()
    return ""


def _origin(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _list_lines(total, records, words: str) -> list:
    """A catalogue list: each record's title, kind, publisher, dates and address, then a short summary."""
    shown = len(records)
    said = f" matching \"{words}\"" if words else ""
    lines = [f"Records{said}: {total if total is not None else shown} in all, the first {shown} listed." if
             (total or 0) > shown else f"Records{said}: {shown}.", ""]
    for r in records:
        bits = [r.get("title") or "(untitled)"] + [x for x in (r.get("kind"), r.get("by"),
                                                               f"updated {r['updated']}" if r.get("updated") else None,
                                                               f"created {r['created']}" if r.get("created") else None) if x]
        lines.append("- " + " | ".join(bits))
        if r.get("url"):
            lines.append(f"  {r['url']}")
        if r.get("summary"):
            lines.append(f"  {_plain(r['summary'], 300)}")
    if not records:
        lines.append("None.")
    return lines


# ---- ArcGIS ----------------------------------------------------------------------------------------------------

_ESRI_TYPES = {"esriFieldTypeOID": "object id", "esriFieldTypeString": "text", "esriFieldTypeInteger": "integer",
               "esriFieldTypeSmallInteger": "small integer", "esriFieldTypeDouble": "number",
               "esriFieldTypeSingle": "number", "esriFieldTypeDate": "date", "esriFieldTypeGeometry": "geometry",
               "esriFieldTypeGlobalID": "global id", "esriFieldTypeGUID": "id"}
_GEOMETRY = {"esriGeometryPolygon": "polygons (areas)", "esriGeometryPoint": "points",
             "esriGeometryPolyline": "lines", "esriGeometryMultipoint": "points"}
WEB_MAPS = ("Web Map", "Web Mapping Application", "Dashboard", "StoryMap", "Web Experience", "Hub Site Application",
            "Application", "Web Scene")


def _fields(fields, definitions=None) -> list:
    if not fields:
        return []
    definitions = definitions or {}
    out = [f"Fields ({len(fields)}):"]
    for f in fields[:FIELDS_MAX]:
        name = f.get("name") or f.get("attribute") or "?"
        alias = f.get("alias") or f.get("attribute_label")
        kind = _ESRI_TYPES.get(f.get("type"), f.get("type") or f.get("attribute_type") or "")
        about = definitions.get(name) or f.get("description")
        coded = (f.get("domain") or {}).get("codedValues") if isinstance(f.get("domain"), dict) else None
        bits = f"- {name}" + (f" ({alias})" if alias and alias != name else "") + (f", {kind}" if kind else "")
        if about:
            bits += f": {_plain(about, 200)}"
        if coded:
            bits += "; codes " + ", ".join(f"{c.get('code')} = {c.get('name')}" for c in coded[:40])
        out.append(bits)
    if len(fields) > FIELDS_MAX:
        out.append(f"... and {len(fields) - FIELDS_MAX} more")
    return out


def _walk(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            if not str(k).startswith("@_Sync"):
                yield from _walk(v, path + (str(k),))
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, path)
    else:
        yield path, node


# What an ArcGIS metadata record says that the land use and data questions ask about, by its element names.
_META = (("idPurp", "Purpose"), ("idCredit", "Credit"), ("useLimit", "Use limits"), ("othConsts", "Other constraints"),
         ("accConsts", "Access constraints"), ("rpOrgName", "Responsible organisation"), ("rpIndName", "Contact"),
         ("formatName", "File format"), ("CreaDate", "Data created"), ("ModDate", "Data last changed"),
         ("mdDateSt", "Metadata date"), ("pubDate", "Published"), ("reviseDate", "Revised"), ("createDate", "Created"),
         ("statement", "Lineage"), ("srcDesc", "Source"))


def _arcgis_metadata(meta) -> tuple[list, dict]:
    """Lines from an ArcGIS metadata record, and its field definitions by field name."""
    if not isinstance(meta, dict):
        return [], {}
    lines, seen, defs, last_label = [], set(), {}, None
    for path, value in _walk(meta):
        if path and path[-1] == "attrlabl" or path[-2:-1] == ("attrlabl",):
            last_label = str(value)
        if (path[-1] == "attrdef" or path[-2:-1] == ("attrdef",)) and last_label:
            defs.setdefault(last_label, str(value))
        for key, label in _META:
            if key in path and value not in (None, "") and not str(value).startswith("<ProjectedCoordinateSystem"):
                shown = _day(value) if "Date" in key else None
                text = shown or _plain(value, 400)
                if (label, text) not in seen and text:
                    seen.add((label, text))
                    lines.append(f"{label}: {text}")
                break
        if path[-2:] == ("languageCode", "@_value") and ("Language", value) not in seen:
            seen.add(("Language", value))
            lines.append(f"Language: {value}")
    return lines, defs


def _layer_lines(service_url: str, get) -> list:
    """What an ArcGIS REST layer or service says of itself, with the layer's record count."""
    info = get(service_url.rstrip("/") + "?f=json")
    if not isinstance(info, dict) or info.get("error"):
        return [f"The service at {service_url} did not answer."]
    lines = [_line("Service name", info.get("name") or info.get("mapName")),
             _line("Service type", info.get("type")),
             _line("Service description", _plain(info.get("description") or info.get("serviceDescription"))),
             _line("Copyright and source", _plain(info.get("copyrightText"), 400)),
             _line("Geometry", _GEOMETRY.get(info.get("geometryType"), info.get("geometryType"))),
             _line("What it can do", info.get("capabilities")),
             _line("Formats it answers in", info.get("supportedQueryFormats")),
             _line("Most records per request", info.get("maxRecordCount")),
             _line("Data last edited", _day((info.get("editingInfo") or {}).get("dataLastEditDate")
                                            or (info.get("editingInfo") or {}).get("lastEditDate")))]
    time = info.get("timeInfo") or {}
    if time.get("timeExtent"):
        lines.append(_line("Time covered", " to ".join(d for d in map(_day, time["timeExtent"]) if d)))
    if info.get("fields"):
        n = get(service_url.rstrip("/") + "/query?where=1%3D1&returnCountOnly=true&f=json")
        if isinstance(n, dict) and isinstance(n.get("count"), int):
            lines.append(f"Records (rows): {n['count']:,}, counted by asking the service")
        lines += _fields(info["fields"])
    elif info.get("layers"):
        lines.append("Layers: " + "; ".join(f"{l.get('id')} {l.get('name')}" for l in info["layers"][:40]))
    if info.get("bandCount"):
        lines.append(_line("Image bands", info.get("bandCount")))
    return lines


def _hub_lines(a: dict, get, sharing: str) -> tuple[str, list]:
    meta_lines, defs = _arcgis_metadata(a.get("metadata"))
    title = a.get("name") or a.get("title") or "Untitled record"
    ext = (a.get("extent") or {}).get("coordinates")
    lic = a.get("licenseInfo") or (a.get("structuredLicense") or {}).get("type")
    kind = a.get("type") or a.get("hubType")
    lines = [_line("Kind", " ".join(x for x in (kind, f"({a['content']})" if a.get("content") and a["content"] != kind
                                                 else "") if x)),
             _line("Publisher (source)", a.get("source") or a.get("orgName")),
             _line("Organisation", a.get("orgName")),
             _line("Owner account", a.get("owner")),
             _line("Contact", (a.get("orgContactEmail") or "").replace("mailto:", "")),
             _line("Record created", _day(a.get("created"))),
             _line("Record last changed", _day(a.get("modified"))),
             _line("Item last changed", _day(a.get("itemModified"))),
             _line("Records (rows)", _count(a.get("recordCount"))),
             _line("Geometry", _GEOMETRY.get(a.get("geometryType"), a.get("geometryType"))),
             _line("Area covered (longitude, latitude)", f"{ext[0][0]}, {ext[0][1]} to {ext[1][0]}, {ext[1][1]}"
                   if isinstance(ext, list) and len(ext) == 2 else None),
             _line("Access", a.get("access")),
             _line("Marked as open data", a.get("openData") if isinstance(a.get("openData"), bool) else None),
             _line("Downloadable", "yes (the site's own flag: the page's Download panel offers the data as files)"
                   if a.get("downloadable") is True else
                   ("no" if a.get("downloadable") is False else None)),
             _line("Formats the service answers in", a.get("supportedQueryFormats")),
             _line("What the service can do", a.get("serverCapabilities")),
             _line("Live data address (API)", f"{a['url']} (query it at {a['url'].rstrip('/')}/query)"
                   if a.get("url") and "/rest/services/" in a["url"] else a.get("url")),
             _line("Licence", _plain(lic, 600) if lic and lic != "none" else "none stated"),
             _line("Access and use", _plain(a.get("accessInformation"), 400)),
             _line("Language", a.get("culture") or a.get("language")),
             _line("Tags", a.get("tags")),
             _line("Categories", a.get("categories")),
             _line("Summary", _plain(a.get("snippet"))),
             _line("Description", _plain(a.get("description") or a.get("searchDescription")))]
    extra = a.get("additionalResources") or []
    if extra:
        lines.append("Other resources: " + "; ".join(f"{r.get('name') or ''} {r.get('url')}".strip() for r in extra[:10]))
    if meta_lines:
        lines += ["", "From its metadata record:"] + meta_lines
    lines += [""] + _fields(a.get("fields"), defs)
    if (a.get("type") or "") in WEB_MAPS:
        item = str(a.get("itemId") or a.get("id") or "").split("_")[0]
        lines += _webmap_layers(item, sharing, get)
    return title, lines


def _webmap_layers(item: str, sharing: str, get) -> list:
    """The layers a web map or app shows, each read as its own record (LAYERS_MAX at most)."""
    data = get(f"{sharing}/content/items/{item}/data?f=json") if item else None
    layers = (data or {}).get("operationalLayers") if isinstance(data, dict) else None
    if not layers:
        return []
    out = ["", f"The map shows {len(layers)} layer{'s' if len(layers) != 1 else ''}:"]
    for layer in layers[:LAYERS_MAX]:
        out += ["", f"Layer: {layer.get('title') or layer.get('id')}"]
        if layer.get("itemId"):
            item_json = get(f"{sharing}/content/items/{layer['itemId']}?f=json")
            if isinstance(item_json, dict) and not item_json.get("error"):
                out += [x for x in _item_core(item_json) if x]
        if layer.get("url"):
            out.append(f"Layer address: {layer['url']}")
            out += _layer_lines(layer["url"], get)
    if len(layers) > LAYERS_MAX:
        out.append(f"... and {len(layers) - LAYERS_MAX} more layers, not read")
    return out


def _item_core(item: dict) -> list:
    return [_line("Kind", item.get("type")),
            _line("Owner account", item.get("owner")),
            _line("Created", _day(item.get("created"))),
            _line("Last changed", _day(item.get("modified"))),
            _line("Credits (source)", _plain(item.get("accessInformation"), 400)),
            _line("Licence", _plain(item.get("licenseInfo"), 600) or "none stated"),
            _line("Access", item.get("access")),
            _line("Tags", item.get("tags")),
            _line("Summary", _plain(item.get("snippet"))),
            _line("Description", _plain(item.get("description")))]


def _item_lines(item: dict, sharing: str, get) -> tuple[str, list]:
    title = item.get("title") or item.get("name") or "Untitled item"
    lines = [x for x in _item_core(item) if x]
    lines.append(_line("Languages", item.get("languages") or item.get("culture")))
    if item.get("url"):
        lines += ["", f"Service address: {item['url']}"] + _layer_lines(item["url"], get)
    if item.get("type") in WEB_MAPS:
        lines += _webmap_layers(item.get("id"), sharing, get)
    return title, lines


def _sharing_root(url: str, page: str) -> str:
    m = _THUMB.search(page)
    if m:
        return m.group(1)
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if host.endswith("arcgis.com"):
        return ARCGIS_SHARING
    prefix = re.split(r"/(?:home|apps)/", p.path, 1)[0]
    return f"{_origin(url)}{prefix}/sharing/rest"


def _ids_of(url: str, page: str) -> list[tuple[str, Optional[str]]]:
    """The ArcGIS item ids a page is about: from its address first, then from its HTML's item thumbnails (never
    the Hub site's own id)."""
    p = urllib.parse.urlsplit(url)
    found = [(m.group(1), m.group(2)) for m in _HEX.finditer(p.path + "?" + p.query + "#" + p.fragment)]
    if not found:
        site = set(_SITE_ID.findall(page))
        found = [(m.group(2), m.group(3)) for m in _THUMB.finditer(page) if m.group(2) not in site]
    out = []
    for pair in found:
        if pair not in out:
            out.append(pair)
    return out


def _hub(url: str, page: str, get):
    p = urllib.parse.urlsplit(url)
    origin, sharing = _origin(url), _sharing_root(url, page)
    segs = [s for s in p.path.split("/") if s]
    record_page = len(segs) >= 2 and segs[0] in ("datasets", "maps", "documents", "apps", "content", "items")
    if not record_page:
        # Only a front page or the site's search lists its records; any other page (About, a story) is left alone.
        return _hub_list(url, get) if len(segs) <= 1 and (segs or [""])[0] in LIST_PAGES else None
    for item, layer in _ids_of(url, page)[:2]:
        for api in (f"{origin}/api/v3", HUB_API):
            for rid in ([f"{item}_{layer}"] if layer else [item, f"{item}_0"]):
                got = get(f"{api}/datasets/{rid}")
                data = (got or {}).get("data") if isinstance(got, dict) else None
                if isinstance(data, dict) and data.get("attributes"):
                    title, lines = _hub_lines(data["attributes"], get, sharing)
                    return title, lines, f"{api}/datasets/{rid}"
        item_json = get(f"{sharing}/content/items/{item}?f=json")
        if isinstance(item_json, dict) and item_json.get("id"):
            title, lines = _item_lines(item_json, sharing, get)
            return title, lines, f"{sharing}/content/items/{item}?f=json"
    slug = segs[1]
    if "::" in slug:
        got = get(f"{origin}/api/v3/datasets?filter%5Bslug%5D={urllib.parse.quote(slug)}")
        data = (got or {}).get("data") if isinstance(got, dict) else None
        if data and isinstance(data[0], dict) and data[0].get("attributes"):
            title, lines = _hub_lines(data[0]["attributes"], get, sharing)
            return title, lines, f"{origin}/api/v3/datasets?filter[slug]={slug}"
    return None


def _hub_list(url: str, get):
    """A Hub site's front page or search: its records, through the site's search API."""
    origin, words = _origin(url), _query_of(url, "q", "query", "search")
    q = f"&q={urllib.parse.quote(words)}" if words else ""
    api = f"{origin}/api/search/v1/collections/all/items?limit={LIST_MAX}{q}"
    got = get(api)
    feats = (got or {}).get("features") if isinstance(got, dict) else None
    if feats is None:
        return None
    records = []
    for f in feats:
        pr = f.get("properties") or {}
        records.append({"title": pr.get("title"), "kind": pr.get("type"), "by": pr.get("source") or pr.get("owner"),
                        "updated": _day(pr.get("modified")), "created": _day(pr.get("created")),
                        "url": f"{origin}/datasets/{f.get('id')}" if f.get("id") else None,
                        "summary": pr.get("snippet") or pr.get("description")})
    title = f"Catalogue of {urllib.parse.urlsplit(url).hostname}" + (f", search \"{words}\"" if words else "")
    return title, _list_lines(got.get("numberMatched"), records, words), api


def _portal_item(url: str, page: str, get):
    sharing = _sharing_root(url, page)
    for item, _layer in _ids_of(url, page)[:2]:
        item_json = get(f"{sharing}/content/items/{item}?f=json")
        if isinstance(item_json, dict) and item_json.get("id"):
            title, lines = _item_lines(item_json, sharing, get)
            return title, lines, f"{sharing}/content/items/{item}?f=json"
    return None


def _rest(url: str, page: str, get):
    base = re.sub(r"/query$", "", url.split("?")[0].rstrip("/"))
    info = get(base + "?f=json")
    if not isinstance(info, dict) or info.get("error"):
        return None
    if "services" in info or "folders" in info:        # a services directory
        lines = [_line("Folders", info.get("folders"))]
        lines.append(f"Services ({len(info.get('services') or [])}):")
        lines += [f"- {s.get('name')} ({s.get('type')}): {base}/{str(s.get('name')).split('/')[-1]}/{s.get('type')}"
                  for s in (info.get("services") or [])[:LIST_MAX]]
        return f"ArcGIS services at {base}", lines, base + "?f=json"
    title = info.get("name") or info.get("mapName") or info.get("documentInfo", {}).get("Title") or base
    return title, _layer_lines(base, get), base + "?f=json"


# ---- GeoNode ---------------------------------------------------------------------------------------------------

def _geonode_name(path: str) -> Optional[str]:
    """The layer's name (its 'alternate', workspace:name) from a GeoNode page address."""
    m = re.search(r"/(?:layers|datasets)/([^/?#]+)", path)
    if not m:
        return None
    name = urllib.parse.unquote(m.group(1))
    parts = name.split(":")
    return ":".join(parts[-2:]) if len(parts) >= 2 else name


def _geonode_lines(d: dict) -> tuple[str, list]:
    title = d.get("title") or d.get("name") or "Untitled layer"
    owner = d.get("owner") if isinstance(d.get("owner"), dict) else {}
    lic = d.get("license") if isinstance(d.get("license"), dict) else {}
    cat = d.get("category") if isinstance(d.get("category"), dict) else {}
    lines = [_line("Kind", " ".join(x for x in (d.get("resource_type"), d.get("subtype")) if x)),
             _line("Name", d.get("alternate") or d.get("typename")),
             _line("Owner", " ".join(x for x in (owner.get("first_name"), owner.get("last_name")) if x)
                   or owner.get("username") or d.get("owner__username")),
             _line("Attribution", d.get("attribution")),
             _line(f"Date ({d.get('date_type')})" if d.get("date_type") else "Date", _day(d.get("date"))),
             _line("Record created", _day(d.get("created"))),
             _line("Record last changed", _day(d.get("last_updated"))),
             _line("Data covers", " to ".join(x for x in (_day(d.get("temporal_extent_start")),
                                                           _day(d.get("temporal_extent_end"))) if x)),
             _line("Category", cat.get("gn_description") or d.get("category__gn_description")),
             _line("Licence", lic.get("name") or lic.get("identifier") or d.get("license")),
             _line("Restrictions", (d.get("restriction_code_type") or {}).get("identifier")
                   if isinstance(d.get("restriction_code_type"), dict) else d.get("restriction_code_type")),
             _line("Other constraints", _plain(d.get("constraints_other"), 400)),
             _line("Language", d.get("language")),
             _line("Update frequency", d.get("maintenance_frequency")),
             _line("Edition", d.get("edition")),
             _line("Projection", d.get("srid")),
             _line("Published", d.get("is_published") if isinstance(d.get("is_published"), bool) else None),
             _line("Keywords", [k.get("name") if isinstance(k, dict) else k for k in d.get("keywords") or []]),
             _line("Regions", [r.get("name") if isinstance(r, dict) else r for r in d.get("regions") or []]),
             _line("Summary", _plain(d.get("abstract"))),
             _line("Purpose", _plain(d.get("purpose"), 600)),
             _line("Data quality", _plain(d.get("data_quality_statement"), 600)),
             _line("Supplemental information", _plain(d.get("supplemental_information"), 600)),
             _line("Download address", d.get("download_url") or d.get("distribution_url"))]
    links = [l for l in d.get("links") or [] if isinstance(l, dict)]
    downloads = [l for l in links if l.get("link_type") in ("data", "original")]
    if downloads:
        lines.append("Downloads offered: " + "; ".join(f"{l.get('name') or l.get('extension')} ({l.get('extension') or l.get('mime')}): {l.get('url')}"
                                                        for l in downloads[:20]))
    services = [l for l in links if str(l.get("link_type", "")).startswith("OGC:")]
    if services:
        lines.append("Map and data services: " + "; ".join(f"{l.get('link_type')} {l.get('url')}" for l in services[:6])
                     + (" (WFS gives the features themselves, not a picture of them)" if any(
                         "WFS" in str(l.get("link_type")) for l in services) else ""))
    perms = d.get("perms")
    if isinstance(perms, list):
        # The permissions the API reports are those of whoever asked: here a visitor who is not signed in.
        lines.append("A visitor who is not signed in may download it: " + ("yes" if "download_resourcebase" in perms else "no")
                     + " (the record's permissions)")
    meta_formats = sorted({str(l.get("name")) for l in links if l.get("link_type") == "metadata" and l.get("name")})
    if meta_formats:
        lines.append("Metadata records offered: " + ", ".join(meta_formats))
    lines += [""] + _fields([a for a in d.get("attribute_set") or [] if isinstance(a, dict)])
    return title, lines


def _geonode(url: str, page: str, get):
    p = urllib.parse.urlsplit(url)
    origin, name = _origin(url), _geonode_name(p.path)
    pk = re.search(r"/(?:dataset|layer)s?/(\d+)", p.path + p.fragment)
    if name or pk:
        tries = ([f"{origin}/api/v2/datasets/{pk.group(1)}", f"{origin}/api/v2/layers/{pk.group(1)}"] if pk else []) + \
                ([f"{origin}/api/v2/datasets?filter%7Balternate%7D={urllib.parse.quote(name)}",
                  f"{origin}/api/v2/layers?filter%7Balternate%7D={urllib.parse.quote(name)}",
                  f"{origin}/api/layers/?typename={urllib.parse.quote(name)}"] if name else [])
        for api in tries:
            got = get(api)
            if not isinstance(got, dict):
                continue
            rec = got.get("dataset") or got.get("layer")
            if rec is None:
                many = got.get("datasets") or got.get("layers") or got.get("objects") or []
                rec = many[0] if many else None
            if isinstance(rec, dict) and (rec.get("title") or rec.get("name")):
                title, lines = _geonode_lines(rec)
                return title, lines, api
        return None
    if p.path.strip("/").split("/")[0] not in LIST_PAGES:
        return None                      # a GeoNode page that is not a layer, nor a list of them (a document, About)
    words = _query_of(url, "title__icontains", "q", "search")
    for api, key, total in ((f"{origin}/api/v2/datasets?page_size={LIST_MAX}", "datasets", "total"),
                            (f"{origin}/api/v2/layers?page_size={LIST_MAX}", "layers", "total"),
                            (f"{origin}/api/layers/?limit={LIST_MAX}", "objects", None)):
        if words:
            api += (f"&search={urllib.parse.quote(words)}" if "/v2/" in api else f"&title__icontains={urllib.parse.quote(words)}")
        got = get(api)
        many = got.get(key) if isinstance(got, dict) else None
        if many is None:
            continue
        records = [{"title": d.get("title"), "kind": d.get("subtype") or d.get("resource_type") or "layer",
                    "by": (d.get("owner") or {}).get("username") if isinstance(d.get("owner"), dict) else d.get("owner__username"),
                    "updated": _day(d.get("last_updated")), "created": _day(d.get("date")),
                    "url": d.get("detail_url") if str(d.get("detail_url") or "").startswith("http")
                    else (origin + d["detail_url"] if d.get("detail_url") else None),
                    "summary": d.get("abstract")} for d in many if isinstance(d, dict)]
        n = got.get(total) if total else (got.get("meta") or {}).get("total_count")
        title = f"Catalogue of {p.hostname}" + (f", search \"{words}\"" if words else "")
        return title, _list_lines(n, records, words), api
    return None


# ---- CKAN ------------------------------------------------------------------------------------------------------

def _ckan(url: str, page: str, get):
    p = urllib.parse.urlsplit(url)
    m = re.match(r"(.*?)/dataset(?:/([^/?#]+))?", p.path)
    if not m and p.path.strip("/").count("/"):
        return None                      # not a dataset, nor a list or front page (an organisation, About)
    prefix = m.group(1) if m else p.path.rstrip("/")
    api = f"{_origin(url)}{prefix}/api/3/action"
    name = m.group(2) if m else None
    if name:
        got = get(f"{api}/package_show?id={urllib.parse.quote(name)}")
        d = (got or {}).get("result") if isinstance(got, dict) and got.get("success") else None
        if not isinstance(d, dict):
            return None
        org = d.get("organization") if isinstance(d.get("organization"), dict) else {}
        lines = [_line("Publisher", org.get("title")),
                 _line("Author", d.get("author")),
                 _line("Maintainer", d.get("maintainer")),
                 _line("Record created", _day(d.get("metadata_created"))),
                 _line("Record last changed", _day(d.get("metadata_modified"))),
                 _line("Licence", d.get("license_title") or d.get("license_id")),
                 _line("Version", d.get("version")),
                 _line("Tags", [t.get("name") for t in d.get("tags") or [] if isinstance(t, dict)]),
                 _line("Description", _plain(d.get("notes")))]
        for e in d.get("extras") or []:
            if isinstance(e, dict) and e.get("value") not in (None, ""):
                lines.append(_line(str(e.get("key")).replace("_", " ").capitalize(), _plain(e.get("value"), 300)))
        res = [r for r in d.get("resources") or [] if isinstance(r, dict)]
        lines += ["", f"Files and links ({len(res)}):"]
        for r in res[:40]:
            lines.append("- " + " | ".join(x for x in (r.get("name") or "(unnamed)", r.get("format"),
                                                       f"changed {_day(r.get('last_modified'))}" if _day(r.get("last_modified")) else None,
                                                       f"{r['size']:,} bytes" if isinstance(r.get("size"), int) else None,
                                                       r.get("url")) if x))
        return d.get("title") or name, lines, f"{api}/package_show?id={name}"
    words = _query_of(url, "q")
    got = get(f"{api}/package_search?rows={LIST_MAX}" + (f"&q={urllib.parse.quote(words)}" if words else ""))
    d = (got or {}).get("result") if isinstance(got, dict) and got.get("success") else None
    if not isinstance(d, dict):
        return None
    records = [{"title": r.get("title"), "kind": ", ".join(sorted({str(x.get("format")) for x in r.get("resources") or []
                                                                  if isinstance(x, dict) and x.get("format")})) or None,
                "by": (r.get("organization") or {}).get("title") if isinstance(r.get("organization"), dict) else None,
                "updated": _day(r.get("metadata_modified")), "created": _day(r.get("metadata_created")),
                "url": f"{_origin(url)}{prefix}/dataset/{r.get('name')}", "summary": r.get("notes")}
               for r in d.get("results") or [] if isinstance(r, dict)]
    title = f"Catalogue of {p.hostname}" + (f", search \"{words}\"" if words else "")
    return title, _list_lines(d.get("count"), records, words), f"{api}/package_search"

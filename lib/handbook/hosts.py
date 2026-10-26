"""Which kind of web address a document came from, judged from the address alone.

Copied from frontend/hosts.py, with PERIPHERAL_HOSTS, REPOSITORY_HOSTS and host_of copied from
scripts/retrieve.py so the handbook stands alone. The result goes in a document's `host_type`:
issuer | repository | third_party | test_server | none.
"""
from __future__ import annotations

import re
import urllib.parse

# Hosts a document should not be taken from as the primary copy: programme and project sites,
# mirrors, encyclopaedias. (Copied from scripts/retrieve.py.)
PERIPHERAL_HOSTS = (
    "mdgfund.org", "sdgfund.org", "wikipedia.org", "scribd.com",
    "researchgate.net", "academia.edu",
)
# Recognised authoritative repositories. (Copied from scripts/retrieve.py.)
REPOSITORY_HOSTS = ("fao.org", "who.int")


def host_of(url: str) -> str:
    """Lower-case host, www. stripped, port dropped. A pragmatic identity for a source;
    not a public-suffix registrable domain. (Copied from scripts/retrieve.py.)"""
    netloc = urllib.parse.urlsplit(url if "//" in url else "//" + url).netloc.lower()
    host = netloc.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


# Extras beyond the lists above (copied from frontend/hosts.py).
EXTRA_THIRD_PARTY = (
    "wiredhealthresources.net",   # a health library that republishes other bodies' guides
    # Press-release platforms and trade press: a company's release as someone else publishes it.
    "mynewsdesk.com",             # press-release platform
    "thenewsmarket.com",          # press-release and media platform
    "prwave.ro",                  # Romanian press-release platform
    "tendancehotellerie.fr",      # French hotel and restaurant trade press
)
EXTRA_REPOSITORY = (
    "company-information.service.gov.uk",   # Companies House: a registry copy of a company's filing
    "regnskaber.virk.dk",                   # the Danish business registry: a registry copy of a filing
)

# Web archives. A web.archive.org/web/<stamp>/<original> copy is judged by the page it copies;
# any other address on an archive (archive.today and its mirrors, a Wayback address with no
# timestamp, a search) is a copy on someone else's site: third party.
ARCHIVE_HOSTS = ("archive.org", "archive.today", "archive.ph", "archive.is", "archive.li",
                 "archive.vn", "archive.fo", "archive.md", "webarchive.nationalarchives.gov.uk")

TEST_PREFIXES = ("test.", "staging.")

# web.archive.org/web/<stamp>/<original>: <stamp> is digits, optionally followed by a flag such as
# id_ or if_; <original> may lack its scheme.
# The UK Government Web Archive keeps the same shape: webarchive.nationalarchives.gov.uk/ukgwa/<stamp>/<original>.
_WEB_ARCHIVE = re.compile(r"(?:https?://)?(?:web\.archive\.org/web|webarchive\.nationalarchives\.gov\.uk(?:/ukgwa)?)"
                          r"/\d+[a-z_]*/(?P<original>\S.*)", re.IGNORECASE)

# A host is dot-separated labels of letters, digits and inner hyphens, ending in a name (not a
# number), or an IPv4 address. Text that gives none ("N/A", "URL not recorded", a list of
# document names) names no host.
_LABEL = r"[^\W_](?:(?:[^\W_]|-)*[^\W_])?"
_HOSTNAME = re.compile(rf"(?:{_LABEL}\.)+{_LABEL}")
_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# Without a scheme, "Guide.pdf" is a file name, not a host on a "pdf" domain. (None of these is a
# top-level domain, so an address without a scheme on a real domain is never mistaken for one.)
# These are the kinds of file a link can lead to; readers.py is what the tool can read of them (every
# kind here but ppt, which it asks to be saved as pptx, and video and audio, which it does not read yet).
FILE_EXTENSIONS = ("pdf", "doc", "docx", "odt", "rtf", "txt", "csv", "tsv", "xls", "xlsx", "xlsm", "ods", "json",
                   "xml", "htm", "html", "ppt", "pptx", "odp", "png", "jpg", "jpeg", "gif", "webp", "heic",
                   "heif", "tif", "tiff", "eml", "msg", "mp4", "mp3", "m4a", "wav")


def _matches(host: str, suffixes) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def _host(text: str) -> str:
    """The host an address names (lower case, www. dropped), or "" when the text names none."""
    try:
        host = host_of(text)
    except ValueError:
        # urlsplit rejects square brackets it cannot read as an IPv6 address, as in a document
        # title ("Report [2023 edition]"), and characters that normalise to "/", "?", "#", "@" or
        # ":" (a full-width slash). Text it cannot parse names no host.
        return ""
    if _IPV4.fullmatch(host):
        return host
    if not _HOSTNAME.fullmatch(host):
        return ""
    last = host.rsplit(".", 1)[1]
    if last.isdigit() or (not _SCHEME.match(text) and last in FILE_EXTENSIONS):
        return ""
    return host


def _type_of(host: str) -> str:
    if host.startswith(TEST_PREFIXES):
        return "test_server"
    if _matches(host, (*REPOSITORY_HOSTS, *EXTRA_REPOSITORY)):
        return "repository"
    if _matches(host, (*PERIPHERAL_HOSTS, *EXTRA_THIRD_PARTY, *ARCHIVE_HOSTS)):
        return "third_party"
    return "issuer"


def classify_host(url):
    """(host_type, host, archive_url) for a document's web address.

    host_type is one of issuer | repository | third_party | test_server | none. The first rule that
    fits wins: no address, or text that names no host ("N/A", a document's name, "Report [2023
    edition]") -> "none"; a test. or staging. host -> "test_server"; a repository host ->
    "repository"; a peripheral, other third-party or web archive host -> "third_party"; anything
    else -> "issuer". A web.archive.org/web/<stamp>/<original> copy is judged by the page it
    copies: `host` is that page's host. `archive_url` is the archive address as given whenever the
    address is on a web archive, and None otherwise.
    """
    text = (url or "").strip()
    archived = _WEB_ARCHIVE.fullmatch(text)
    copied = _host(archived.group("original")) if archived else ""     # the copied page's host
    if copied:
        return _type_of(copied), copied, text
    host = _host(text) if text else ""
    if not host:
        return "none", "", None
    return _type_of(host), host, (text if _matches(host, ARCHIVE_HOSTS) else None)


def on_results_site(url, sites) -> str | None:
    """The results site `url` is on (one of `sites`, the hosts where the benchmark publishes its
    results, subdomains included), judging a web-archive copy by the page it copies; None when it is
    on none of them."""
    text = (url or "").strip()
    archived = _WEB_ARCHIVE.fullmatch(text)
    page = archived.group("original") if archived else text
    host = _host(page)
    if not host:
        return None
    path = urllib.parse.urlsplit(page if "://" in page else "https://" + page).path.lower()
    for site, site_host, site_path in site_parts(sites):
        if (host == site_host or host.endswith("." + site_host)) \
                and (not site_path or path.startswith("/" + site_path)):
            return site
    return None


def site_parts(sites) -> list[tuple[str, str, str]]:
    """(site, host, path) for each of a project's results sites, tidied: no scheme, no www., lower
    case; path is "" for a whole host. A site may name a path ("aldf.org/us-state-rankings"): only
    that part of the host holds the results, so the owner's own other documents there stay usable
    (ALDF re-test, 15 Oct 2026)."""
    parts = []
    for site in sites or []:
        site = str(site).strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.")
        site_host, _, site_path = site.partition("/")
        if site_host:
            parts.append((site, site_host, site_path.rstrip("/")))
    return parts

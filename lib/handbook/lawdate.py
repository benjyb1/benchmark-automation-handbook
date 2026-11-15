"""The date of a law, from the law's own text, not from the copy that holds it.

A document's date is the date of the copy by default: the PDF's metadata, the words a web page prints about
itself, the day the archive captured it, a date in its address (prepare.py). For a law that is the wrong date.
Malawi's Data Protection Act 2024 (assented 31 January 2024, published 2 February 2024) was saved twice on 5 Nov
2026 and both copies were left out of the kit as dated after the 1 September 2024 cut-off: a law firm's PDF dated
2025-09-01 by its metadata, and the Authority's download page dated 2026-04-13 by its "Last Updated" line. Seven
data protection answers then had no evidence for a law the open-data benchmark scores in full.

So a legal text is dated by its enactment: the latest assent, enactment, promulgation or gazette date in its
opening words. A copy that says it holds the law as amended or consolidated later is dated by that later date, so
a later version of an old law does not pass as the old law. A page whose title is just a law's name and year but
which holds no dated law text (the download page) is dated by that year alone: it is left out only when the year
is after the cut-off's year (kit.late_documents). Nothing here is used for a document the owner or the finder
dated, or for one that does not read as a law (a bill, a draft, a policy, a report, news).
"""
from __future__ import annotations
import re

_EN = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                   "september", "october", "november", "december"), 1)}
_FR = {m: i for i, m in enumerate(("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                                   "septembre", "octobre", "novembre", "décembre"), 1)}
_ES = {m: i for i, m in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                   "septiembre", "octubre", "noviembre", "diciembre"), 1)}
_PT = {m: i for i, m in enumerate(("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
                                   "setembro", "outubro", "novembro", "dezembro"), 1)}
MONTHS = {**_PT, **_ES, **_FR, **_EN, "fevrier": 2, "aout": 8, "decembre": 12, "marco": 3, "setiembre": 9}
_MON = "(" + "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True)) + ")"
_DAY = r"(\d{1,2})(?:st|nd|rd|th|er|º|°)?"
_DATES = [
    # 31st January, 2024 / 19 juin 2013 / 17 de octubre de 2012 / 14 de agosto de 2018
    (re.compile(_DAY + r"(?:\s+day)?(?:\s+of)?\s+(?:de\s+)?" + _MON + r",?\s+(?:de\s+|del\s+)?(\d{4})", re.I), "dmy"),
    # January 31, 2024
    (re.compile(_MON + r"\s+" + _DAY + r",?\s+(\d{4})", re.I), "mdy"),
    # 2024-01-31
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    # 31/01/2024, 31.01.2024
    (re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b"), "num"),
]

# Words that put a date on the law itself: assent, enactment, promulgation, publication in the gazette.
_MAKING = re.compile(
    r"(?i)(\bI\s+assent\b|date\s+of\s+assent|assented\s+to|\bassent\b|\benacted\b|\bpassed\s+by\b|gazett|"
    r"\(published\b|\bpublished\s+(?:in|on)\b|\bpromulgu|\bpromulga|\bsanciona|\bsancion|\bpublicad[oa]|"
    r"journal\s+officiel|diario\s+oficial|di[aá]rio\s+(?:oficial|da\s+rep[uú]blica)|\bfait\s+[àa]\b|\bdado\s+en\b|"
    r"^\s*(?:loi|décret|decret|ordonnance|ley|lei|decreto)\s+n?[°ºo.]*\s*[\d./-]+\s*,?\s*(?:du|de|del)\b)", re.M)
# Words that say the copy holds the law as changed later.
_LATER = re.compile(
    r"(?i)(as\s+amended\s+by|amended\s+by|consolidated\s+(?:as\s+(?:at|of)|to)|version\s+(?:as\s+at|from|of)|"
    r"as\s+at\b|revised\s+edition|up\s+to\s+date\s+as\s+(?:at|of)|modifi[ée]e?\s+par|reformad[oa]\s+por|"
    r"modificad[oa]\s+por|alterad[oa]\s+(?:por|pela|pelo)|[uú]ltima\s+reforma|reda[çc][ãa]o\s+dada\s+pel[ao]|"
    r"inclu[íi]d[oa]\s+pel[ao]|adicionad[oa]\s+por|compl[ée]t[ée]e?\s+par|\bactualizad[oa]s?\b|\batualizad[oa]s?\b)")
# The same, with only a year: "(Redação dada pela Lei nº 13.853, de 2019)", "as amended by Act No. 5 of 2025".
_LATER_YEAR = re.compile(
    r"(?i)(amended\s+by|revised\s+edition|reda[çc][ãa]o\s+dada\s+pel[ao]|inclu[íi]d[oa]\s+pel[ao]|"
    r"alterad[oa]\s+pel[ao]|revogad[oa]\s+pel[ao]|reformad[oa]\s+por|adicionad[oa]\s+por|modificad[oa]\s+por|"
    r"derogad[oa]\s+por|modifi[ée]e?\s+par|abrog[ée]e?\s+par|compl[ée]t[ée]e?\s+par)[^\n)]{0,80}?\b((?:19|20)\d{2})\b")
# A law's own heading at the start of a line ("Loi n° 2013-450 du 19 juin 2013 ..."): two or more different ones in
# the opening words make a list of laws, not a law.
_HEADING = re.compile(r"(?im)^\s*(?:loi|ley|lei|décret|decreto|ordonnance|act)\s+n?[°ºo.]*\s*([\d./-]*\d)")
# A legal text announces itself in its opening words.
_LAW_TEXT = re.compile(
    r"(?i)(\bI\s+assent\b|date\s+of\s+assent|be\s+it\s+enacted|enacted\s+by\s+(?:the\s+)?parliament|"
    r"arrangement\s+of\s+sections|short\s+title\s+and\s+commencement|\ban\s+act\s+to\b|"
    r"l['’]assembl[ée]e\s+nationale\s+a\s+(?:d[ée]lib[ée]r[ée]\s+et\s+)?adopt|le\s+pr[ée]sident\s+de\s+la\s+r[ée]publique\s+promulgue|"
    r"el\s+congreso\s+de\b.{0,40}decreta|\bdecreta\s*:|fa[çc]o\s+saber\s+que|\bsanciono\b|"
    r"^\s*(?:loi|ley|lei|décret|decreto|ordonnance)\s+n?[°ºo.]*\s*[\d./-]+)", re.M)
_LAW_WORD = r"(?:Act|Law|Code|Decree|Ordinance|Proclamation|Statute|Regulations|Loi|Ordonnance|Décret|Ley|Lei|Decreto)"
# The title is the law's name and its year, and nothing else: "Data Protection Act, 2024", "Act No. 3 of 2024".
_NAME = re.compile(r"(?i)^\s*(?:the\s+)?[\w’'(),. -]{0,90}?\b" + _LAW_WORD + r"\b[\s,]*(?:\(?\s*no\.?\s*\d+\s+of\s+)?"
                   r"((?:19|20)\d{2})\s*\)?\s*$")
_NOT_LAW = re.compile(r"(?i)\b(bill|draft|projet|proyecto|projeto|policy|strategy|guidelines?|guidance|report|"
                      r"summary|commentary|analysis|overview|explained|guide|notice|press|news|amendment\s+bill)\b")
# The cover of a Laws.Africa copy (ULII, KenyaLaw, SAFLII and others): "Legislation as at 6 March 2015 ... PDF created
# on 17 March 2026". The "as at" date is the version of the law the copy holds (Uganda's PFM Act 2015, rehearsal,
# 5 Nov 2026: dated 2026 by its PDF and left out, six public finance cells lost).
_VERSION = re.compile(r"(?i)(legislation\s+as\s+at|l[ée]gislation\s+[àa]\s+jour\s+au|legisla[çc][ãa]o\s+em)\s*:?\s*")
COVER = 1500        # the cover page's words
HEAD = 4000         # the opening words a law dates itself in
LAW_OPENING = 800   # a law's text starts with its enacting words, its number or its arrangement of sections
NEAR = 160          # how far after such a word its date may stand ("I assent / name / President / 31st January, 2024")


def dates_in(text: str) -> list[tuple[int, str]]:
    """(position, YYYY-MM-DD) of each full date in `text`."""
    out = []
    for pattern, order in _DATES:
        for m in pattern.finditer(text):
            g = m.groups()
            try:
                if order == "dmy":
                    d, mo, y = int(g[0]), MONTHS[g[1].lower()], int(g[2])
                elif order == "mdy":
                    mo, d, y = MONTHS[g[0].lower()], int(g[1]), int(g[2])
                elif order == "iso":
                    y, mo, d = int(g[0]), int(g[1]), int(g[2])
                else:
                    d, mo, y = int(g[0]), int(g[1]), int(g[2])
            except (KeyError, ValueError):
                continue
            if 1800 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                out.append((m.start(), f"{y:04d}-{mo:02d}-{d:02d}"))
    return sorted(out)


def _near_words(text: str, words: re.Pattern, dates: list[tuple[int, str]]) -> list[str]:
    """The first date after each of `words`, when it stands within NEAR characters: Kenya's "Date of Assent: 8th
    November, 2019 / Date of Commencement: 25th November, 2019" gives the assent, not the commencement."""
    out = []
    for m in words.finditer(text):
        after = [d for pos, d in dates if 0 <= pos - m.end() <= NEAR]
        if after:
            out.append(after[0])
    return out


def law_name_year(title: str) -> int | None:
    """The year in a title that is a law's name and nothing else; None otherwise. The part before a site's name
    (" – ", " | ", " - ") is what is read."""
    first = re.split(r"\s+[–—|-]\s+", str(title or ""), maxsplit=1)[0]
    if _NOT_LAW.search(first):
        return None
    m = _NAME.match(first)
    return int(m.group(1)) if m else None


def law_date(title: str, text: str, copy_date: str | None = None) -> dict | None:
    """What dates a legal text, or None when the document does not read as one.
    {"date": YYYY-MM-DD, "said": "made"|"later version"} from the law's own words, or {"year": YYYY, "said": "name"}
    when only its title names it. `copy_date` (the date of the copy, or the day it was saved) bounds it: a date
    after it is not the law's."""
    text = str(text or "")
    head = text[:HEAD]
    cover = head[:COVER]
    for m in _VERSION.finditer(cover):
        when = [d for pos, d in dates_in(cover) if 0 <= pos - m.end() <= 5]
        if when and (not copy_date or when[0] <= copy_date):
            return {"date": when[0], "said": "version"}       # amendments applied are in it; later ones are not
    named = law_name_year(title)
    if _NOT_LAW.search(re.split(r"\s+[–—|-]\s+", str(title or ""), maxsplit=1)[0]) and not _LAW_TEXT.search(head):
        return None
    if len(set(_HEADING.findall(head))) >= 2:
        return None
    out = None
    # the opening of the document is a law's, or its title names one (a page that only lists laws is not dated so)
    if _LAW_TEXT.search(head[:LAW_OPENING]) or (re.search(r"(?i)\b" + _LAW_WORD + r"\b", str(title or ""))
                                               and _LAW_TEXT.search(head)):
        dates = dates_in(head)
        made = [d for d in _near_words(head, _MAKING, dates) if not copy_date or d <= copy_date]
        if made:
            out = {"date": max(made), "said": "made"}
    if out is None and named:
        out = {"year": named, "said": "name"}
    if out is None:
        return None
    # A copy that holds the law as amended or consolidated later speaks as at that later date.
    later = [d for d in _near_words(text, _LATER, dates_in(text)) if not copy_date or d <= copy_date]
    years = [int(m.group(2)) for m in _LATER_YEAR.finditer(text)]
    since = out.get("date") or f"{out['year']:04d}-12-31"
    ends = [f"{y:04d}-12-31" for y in years if not copy_date or f"{y:04d}" <= copy_date[:4]]
    latest = max(later + [min(e, copy_date) if copy_date else e for e in ends], default=None)
    if latest and latest > since:
        return {"date": latest, "said": "later version"}
    return out

"""Where a quote sits in a law or a code: its section, article or paragraph (a "locator", such as
"s. 4(4)" or "art. 35(1)"), found with plain code from the heading above the quote.

    locator(pages, page, quote, title="") -> str | None     # pages: {page number: text}
    backfill(project, rows=None, write=False) -> dict       # every evidence item without a section

Lawyers cite sections, not pages (the Centre for Law and Democracy, 19 Oct 2026). The scorer now
records `section` itself (kit.py); this fills it in for answers scored before that, at no cost in
tokens. It walks up from the quote's line, over earlier pages too, to the nearest heading:
- a heading in words: "Section 1.5 ...", "Article 15", "Regulation 3", "Rule 4", "Paragraph 12";
- a numbered heading: "4. Right to information" (short, no "shall", no closing comma or semicolon),
and on the way it keeps the nearest subsection ("(4)", or "1." in a constitution printed with
numbered clauses) and the nearest lettered paragraph ("(a)", "a."). A definition line ("citizen"
means ...) ends the search for those, since the lettered items above it belong to another term.
Where no heading is found, the item is left without one and listed as unplaced: Claude can place
those (a background job), or the owner can leave them on the page number.
"""
from __future__ import annotations
import re

from .folder import Project

BACK_PAGES = 8                  # how many earlier pages to search for a heading
HEADING_MAX = 90                # a numbered heading is a short line

WORDS = {"section": "s.", "sec": "s.", "article": "art.", "art": "art.", "regulation": "reg.",
         "reg": "reg.", "rule": "r.", "paragraph": "para.", "para": "para.", "clause": "cl."}
NAMED = re.compile(r"^\s*(section|sec|article|art|regulation|reg|rule|paragraph|para|clause)\.?\s+"
                   r"(\d+[A-Z]?(?:\.\d+[A-Z]?)*|[IVXLC]+)\b(.*)$", re.I)
NUMBERED = re.compile(r"^\s*(\d+[A-Z]?)\.\s*([A-Z][^\n]*)$")
INLINE = re.compile(r"^\s*(\d+[A-Z]?(?:\.\d+)?)(?:\s+|\.\s*)\((\d+[A-Z]?(?:\.\d+)?)\)\s+[A-Z\"“]")   # "6. (1) Subject to..." or "4 (1) Subject..."
SENTENCE = re.compile(r"^\s*(\d+[A-Z]?)\.\s+[A-Z]")                          # "7. An information holder shall..."
SUB = re.compile(r"^\s*\((\d+[A-Z]?(?:\.\d+)?)\)\s")
CLAUSE = re.compile(r"^\s*(\d+)\.\s+\S")
LETTER = re.compile(r"^\s*\(?([a-z])[.)]\s")
DEFINITION = re.compile(r"^\s*[\"“][^\"”]{1,80}[\"”]\s+(means|includes|has|shall have|in relation to)\b", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _quote_line(text: str, quote: str) -> int | None:
    """The index of the line where `quote` starts in `text`, ignoring spacing and case."""
    lines = text.split("\n")
    flat, starts = "", []
    for line in lines:
        starts.append(len(flat))
        flat += _norm(line) + " "
    q = _norm(quote)
    if not q:
        return None
    at = flat.find(q)
    if at < 0:
        at = flat.find(q[:40]) if len(q) > 40 else -1
    if at < 0:
        return None
    line = 0
    for i, s in enumerate(starts):
        if s <= at:
            line = i
    return line


def _heading(line: str, after: str = "") -> tuple[str, str] | None:
    """(word, number) when `line` is a section or article heading, else None. `after` is the next
    line: a numbered line that runs on into a lower-case one is a wrapped clause, not a heading."""
    m = NAMED.match(line)
    if m:
        return WORDS[m.group(1).lower()], m.group(2)
    m = NUMBERED.match(line)
    if m:
        rest = m.group(2).strip()
        if (len(line.strip()) <= HEADING_MAX and not re.match(r"\s*[a-z]", after or "") and not re.search(r"\bshall\b|\bmay\b|\bmust\b", rest, re.I)
                and not rest.endswith((",", ";", ":", "-", "—")) and (not rest.endswith(".") or rest.endswith("etc."))
                and not re.search(r"\b[a-z]\.\s", rest)):
            return "", m.group(1)
    return None


def locator(pages: dict, page: int, quote: str, title: str = "") -> str | None:
    """The section, article or paragraph a quote sits in, such as "s. 4(4)", or None."""
    text = pages.get(page)
    if not isinstance(text, str):
        return None
    start = _quote_line(text, quote)
    if start is None:
        return None
    letter = sub = None
    frozen = False
    constitution = "constitution" in (title or "").lower()   # its numbered lines are clauses, not sections
    # "7. An information holder shall..." starts a section only in a statute printed "6. (1) ...":
    # elsewhere (a web page, a judgment, a report) a numbered sentence is just a list item.
    statute = sum(1 for t in pages.values() if isinstance(t, str)
                  for line in t.split("\n") if INLINE.match(line)) >= 3                               # a definition line: lettered items above it are another term's
    numbers = sorted(n for n in pages if isinstance(n, int) and n <= page)[::-1][:BACK_PAGES + 1]
    for n in numbers:
        lines = (pages.get(n) or "").split("\n")
        idx = range(start, -1, -1) if n == page else range(len(lines) - 1, -1, -1)
        for i in idx:
            line = lines[i]
            head = _heading(line, lines[i + 1] if i + 1 < len(lines) else "")
            if head:
                word, number = head
                if not word:
                    word = "art." if constitution else "s."
                # constituteproject prints a clause "1." under the heading; "35. Access..." is the article
                return word + " " + number + (f"({sub})" if sub else "") + (f"({letter})" if letter else "")
            m = INLINE.match(line)
            if m:
                return ("art. " if constitution else "s. ") + m.group(1) + f"({sub or m.group(2)})" + (f"({letter})" if letter else "")
            if not constitution and SENTENCE.match(line):
                if not statute:
                    return None              # a numbered list in a page that is not a statute: no section to give
                return "s. " + SENTENCE.match(line).group(1) + (f"({sub})" if sub else "") + (f"({letter})" if letter else "")
            if DEFINITION.match(line):
                frozen = True                    # the lettered items below belong to this term
                letter = sub = None
                continue
            if frozen:
                continue
            m = SUB.match(line)
            if m and sub is None:
                sub = m.group(1)
                continue
            m = CLAUSE.match(line) if constitution else None
            if m and sub is None:
                sub = m.group(1)
                continue
            m = LETTER.match(line)
            if m and sub is None and letter is None:
                letter = m.group(1)
    return None


def backfill(project: Project, rows=None, write: bool = False) -> dict:
    """Finds a section for every evidence item that has none, in `rows` (all rows when None).
    With `write`, saves them into the answers. Returns {"placed", "unplaced": [{row, question,
    doc, page, quote}], "already", "rows"}."""
    docs = project.documents()
    texts: dict[str, dict] = {}
    out = {"placed": 0, "unplaced": [], "already": 0, "rows": []}
    wanted = None if rows is None else set(rows)
    for u in project.read("units").get("units", []):
        row = u.get("id") if isinstance(u, dict) else None
        if not isinstance(row, str) or (wanted is not None and row not in wanted):
            continue
        answers = project.answers(row)
        cells = answers.get("cells") or {}
        changed = False
        for qid, c in cells.items():
            for ev in (c or {}).get("evidence") or []:
                if not isinstance(ev, dict) or ev.get("page") is None or not ev.get("quote"):
                    continue
                if ev.get("section"):
                    out["already"] += 1
                    continue
                doc = ev.get("doc")
                if doc not in texts:
                    texts[doc] = {p.get("page"): p.get("text") for p in project.pages(doc)} if doc in docs else {}
                where = locator(texts[doc], ev["page"], ev["quote"], (docs.get(doc) or {}).get("title") or "")
                if where:
                    out["placed"] += 1
                    if write:
                        ev["section"], ev["section_by"] = where, "code"
                        changed = True
                else:
                    out["unplaced"].append({"row": row, "question": qid, "doc": doc, "page": ev["page"],
                                            "quote": ev["quote"][:160]})
        if changed:
            project.write_answers(row, answers)
            out["rows"].append(row)
    return out

"""One read: score a kit in a single Claude call with no tools, the row's pages and questions in the message.

    fits(kit) -> bool                       # small enough to send whole (else read in parts)
    plan(kit) -> {"mode", "parts", "estimate", "why"}   # what hb score will do: one read, parts or the full scorer
    choose(kit, model, effort) -> (model, effort)
    run(kit, model=None, effort=None) -> result   # writes KIT/answers.json; the result is receipt-shaped

The full scorer (cmd_score.run_scorer) is a conversation: every search or page it opens is another turn,
and every turn re-sends Claude Code's own instructions (about 7,000 tokens) and everything read so far.
Measured 15 to 19 Oct 2026 on the RTI Rating: Liberia 10 turns and 292,000 tokens, Canada 84 turns and
5.4 million, for documents of 150,000 and 530,000 characters. Here plain code reads the kit instead: one
message holds a short brief (reading.md, written by hb kit), the questions and rulings, and every page,
and Claude answers them all at once. The launch carries a one-line system prompt in place of Claude Code's
(about 900 tokens in all, against 7,100) and no tools, so nothing outside the message can be read.
Then plain code checks every quote (quotecheck.py, as the scorer's own check does); quotes that are not
word for word go back once, with only the pages they cite, to be copied again.

Measured 21 Oct 2026 against known answers (Opus 5.5; the old scorer's figures from earlier runs):
RTI Liberia and Kenya 2.4 times fewer tokens, the same agreement with CLD (87 exact and 116 within a point
of 122, against 90 and 117); Sierra Leone 175,000 tokens against 1.08 million, 39 exact against 37;
TranspariMED CTIS 26/28 as before at 6,400 tokens an answer against 35,000; Pecking Order gate rows
4,600 an answer against 35,000 at high effort, 1.0 and 1.7 points off WAP's published overall in two runs
(the old scorer: 0.7). Prompt caching is turned off for the call (DISABLE_PROMPT_CACHING): one call is
never re-read, and Claude Code otherwise writes the whole message to its one-hour cache at twice the
input price.

Documents that many rows share (a roll-call vote lists every member) are cut to what concerns this row:
a long page keeps its opening, the lines that name the row, and for each question the two stretches that
share most of its words (`trim`). LCV vote records went from about 75,000 tokens a row to 15,000, all
answers still right. Cutting a row's own documents to the pages that match each question was tried and
lost answers (RTI Kenya 36 exact against 40; Pecking Order bands 0/3), so a row's own documents go whole.

Questions marked `"reading": "lookup"` (a value read straight off a record, list or table) are answered by
the cheaper model when every question in the kit is one: on LCV votes and CTIS checks Haiku 5.5 gave the
same answers as Opus 5.5 for about a fiftieth of the price. Anything else is read by MODEL at
ONE_READ_EFFORT: on the Pecking Order low effort drifted (3.0 points off) and high came closest.

A kit too long for one message is read in parts (`parts`), not handed to the full scorer. Sweep round 3
(21 Oct 2026) showed the cliff that used to sit at LIMIT: a kit 1% over it (TI Chemring) cost 693,000
tokens in the full scorer against 177,000 for one just under, and a university's 468,000 characters
cost 3.4 million over 29 turns, each re-reading the same pages. Now the documents are cut into parts
that each fit, whole documents where they can and page ranges of a long one where they cannot, and
every part is read with all the questions. Each reading is told which documents sit in the other parts,
so it does not call them missing. The answers are then put together by plain code: a question answered
in one part takes that answer; the same answer in several parts keeps every quote; parts that disagree
go to one more short call that sees only the pages the answers cite (`reconcile`). The cost is about
the documents once plus the brief and questions once per part.

A single document longer than LONG_DOC characters (a 1,100-page budget book, a whole digital code, a
compendium decree holding one chapter on data protection) is not sent whole even in parts: an open-data benchmark's Kenya
land and budget kit, 9.8 million characters over 20 documents, would take 45 parts and 8.4 million
tokens that way (29 Oct 2026), more than the full scorer's 2.4 million. Such a document is read
focused instead: one short call first sees the questions, each long document's opening pages (its
contents) and a map of the rest, one line a page (a run of pages opening with the same line is one
entry), and chooses the pages the questions need (`focus`, at most MAX_LOOK over all long documents).
The unit chosen is a piece of at most PIECE characters (a PDF page, or a cut of a web page saved as
one 100,000-character page), so the choice is even across documents however they were saved. The reading
then gets the short documents whole, the chosen pieces of the long ones with the map of the rest, and may
ask once for up to MORE_PAGES more for the cells still open ("want" beside the cells): that call carries
only the added pieces and the first answers, not the whole kit again (Benin, 29 Oct 2026: the third call
re-sent everything and cost as much as the reading). Choosing by keyword was the method that lost answers above; here the model chooses from the
document's own headings, and can ask again.
"""
from __future__ import annotations
import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from . import conflicts
from .claude_cli import LOOKUP_MODEL, MODEL, ONE_READ_EFFORT
from .folder import HandbookError, read_json, write_json
from .quotecheck import check_kit, kit_pages, mend_doc_ids

BRIEF = "reading.md"                 # written by hb kit next to brief.md
SYSTEM = ("You score one row of a benchmark from the documents in the message. Use only those documents, "
          "never outside knowledge. Reply with one JSON object and nothing else.")
CHARS_PER_TOKEN = 3.0                # a little under the usual 4: law, tables and XML cut into more tokens
LIMIT = 200_000                      # estimated tokens in the message; a bigger kit goes to the full scorer
LOOKUP_LIMIT = 150_000               # the cheaper model reads less at once
# Questions per call, so the answers stay well inside one reply: about 200 output tokens a cell, 24,000 for
# 120, against reply limits of 128,000. It was 80, and an open-data benchmark's 83- and 89-question rows (29 Oct 2026) then read
# every document twice for the last few questions. Over this the questions go in equal chunks (`_chunks`).
MAX_QUESTIONS = 120
LONG_DOC = 300_000                   # characters: a document longer than this is read focused (see the docstring)
PIECE = 5000                         # characters: the unit chosen from a long document (a page, or a cut of a long one)
MAP_CHARS = 90                       # characters of a piece's opening line shown on the map
OPEN_PIECES = 6                      # a long document's first pieces (its contents) always go whole
MAX_LOOK = 60                        # pieces chosen over all long documents for one reading
MORE_PAGES = 40                      # pieces the reading may then ask for, once
TIMEOUT_S = 1800
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
# Trimming a shared document's long page (see the module docstring).
BIG = 8000                           # characters: a page longer than this is trimmed
HEAD = 1500                          # characters of the page's opening kept
AROUND = 8                           # lines kept either side of a line that names the row (1 chunk of 40 words)
STOP = set("the and for with that this are any not from have has was were which shall its their there these those "
           "into such other than more less each may all can will one two per out been being what when where who "
           "how does did under also only about between within".split())


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zà-ÿ0-9]{3,}", (text or "").lower()) if w not in STOP]


def name_words(name: str) -> set[str]:
    """The words of a row's name a page would name it by: 'Cory Booker (D-NJ)' -> {'cory', 'booker'}."""
    return {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", name or "")}


def trim(text: str, names: set[str], question_words: list[set[str]]) -> str:
    """A long page cut to its opening, the stretches that name the row (with AROUND lines either side) and,
    for each question, the two stretches sharing most of its words. A stretch is a line, or 40 words for a
    page with few lines (a web page saved as one line). Gaps show as [...]. A short page comes back whole."""
    if len(text) <= BIG:
        return text
    units, joiner, around = text.split("\n"), "\n", AROUND
    if len(units) < 90:
        w = text.split()
        units, joiner, around = [" ".join(w[i:i + 40]) for i in range(0, len(w), 40)], " ", 1
    keep, size = set(), 0
    for i, u in enumerate(units):
        if size > HEAD:
            break
        keep.add(i)
        size += len(u)
    for i, u in enumerate(units):
        low = u.lower()
        if any(re.search(r"\b" + re.escape(n) + r"\b", low) for n in names):
            keep.update(range(max(0, i - around), min(len(units), i + around + 1)))
    unit_words = [set(words(u)) for u in units]
    common = {x for x, c in Counter(x for s in unit_words for x in s).items() if c > len(units) / 2}
    for qw in question_words:
        qw = qw - common
        best = sorted(((len(qw & s), i) for i, s in enumerate(unit_words)), reverse=True)[:2]
        keep.update(i for n, i in best if n >= 2)
    out, last = [], -1
    for i in sorted(keep):
        if i != last + 1:
            out.append("[...]")
        out.append(units[i])
        last = i
    if last < len(units) - 1:
        out.append("[...]")
    return joiner.join(out)


def _questions(kit: Path) -> dict:
    data = read_json(kit / "questions.json", {}) or {}
    if not isinstance(data.get("questions"), list):
        raise HandbookError(f"{kit} has no questions.json with questions in it. Build it with hb kit first.")
    return data


def _page_texts(kit: Path, names: set[str], question_words: list[set[str]]) -> list[tuple[str, dict, list]]:
    """[(doc id, meta, [(page number, text)])] for every document with page files, shared ones trimmed."""
    docs = kit / "docs"
    out = []
    for folder in sorted(p for p in docs.iterdir() if p.is_dir()) if docs.is_dir() else []:
        meta = read_json(folder / "meta.json", {}) or {}
        pages = sorted(folder.glob("p*.txt"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
        if not pages:
            continue
        texts = []
        for p in pages:
            text = p.read_text(encoding="utf-8")
            if meta.get("shared"):
                text = trim(text, names, question_words)
            texts.append((int(p.stem[1:]), text))
        out.append((folder.name, meta, texts))
    return out


def _head(doc: str, meta: dict) -> str:
    # An undated document says so: the day it was saved is shown as that, never as its date (RSF, 21 Oct 2026).
    shown = dict(meta, date=meta.get("date") or "unknown")
    attrs = " ".join(f'{k}="{v}"' for k, v in shown.items()
                     if k in ("title", "publisher", "date", "saved_on", "language") and v)
    return f'<document id="{doc}" {attrs}>'


def _page(n: int, text: str) -> str:
    return f'<page n="{n}">\n{text}\n</page>'


def _documents(kit: Path, names: set[str], question_words: list[set[str]], only: dict | None = None,
               maps: list[str] | None = None, cut: dict | None = None) -> str:
    """Every document with page files, as <document> blocks of <page> blocks, shared ones trimmed.
    `only` {doc id: set of page numbers} keeps just those pages (one part of a long kit, or the chosen
    pages of a long document); `cut` {doc: {page: piece indexes}} keeps only those pieces of a page; a
    document in `maps` also carries the map of what was left out."""
    out = []
    for doc, meta, texts in _page_texts(kit, names, question_words):
        every = texts
        if only is not None:
            texts = [(n, t) for n, t in texts if n in only.get(doc, ())]
            if not texts:
                continue
        kept = (cut or {}).get(doc) or {}
        out.append(_head(doc, meta))
        out += [_page(n, _render(t, kept.get(n))) for n, t in texts]
        if maps and doc in maps:
            sent = {n: kept.get(n) if n in kept else None for n, _ in texts}
            rest = _left_out([(n, t) for n, t in every if n not in sent or sent[n] is not None],
                             {n: v for n, v in sent.items() if v is not None})
            if rest:
                out += ["<map>"] + _map_lines(rest) + ["</map>"]
        out.append("</document>")
    return "\n".join(out)


def long_docs(kit: Path) -> list[str]:
    """Documents too long to send whole (over LONG_DOC characters of page text), in kit order."""
    return [doc for doc, meta, texts in _page_texts(kit, set(), []) if sum(len(t) for _, t in texts) > LONG_DOC]


def _pieces(text: str) -> list[str]:
    """A page in pieces of at most PIECE characters, cut between lines (or between words when the page is a
    few long lines, a web page saved as one). A short page is one piece."""
    if len(text) <= PIECE:
        return [text]
    units, joiner = text.split("\n"), "\n"
    if len(units) < 20 or max(len(u) for u in units) > PIECE:
        w = text.split()
        units, joiner = [" ".join(w[i:i + 40]) for i in range(0, len(w), 40)], " "
    out, cur, size = [], [], 0
    for u in units:
        if cur and size + len(u) + 1 > PIECE:
            out.append(joiner.join(cur))
            cur, size = [], 0
        cur.append(u)
        size += len(u) + 1
    if cur:
        out.append(joiner.join(cur))
    return out


def _label(n: int, i: int, total: int) -> str:
    return f"p{n}" if total == 1 else f"p{n}/{i + 1}"


def _parse_label(item) -> tuple[int, int | None] | None:
    """"p12" or 12 -> (12, None); "p12/3" -> (12, 2); anything else None."""
    m = re.fullmatch(r"p?(\d+)(?:/(\d+))?", str(item).strip())
    if not m:
        return None
    return int(m.group(1)), (int(m.group(2)) - 1 if m.group(2) else None)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            return line[:MAP_CHARS]
    return "(empty)"


def _map_lines(entries: list[tuple[str, str]]) -> list[str]:
    """One line a piece ("p57: PART B FIGURES"), a run of consecutive pieces opening alike as one line."""
    runs: list[list] = []
    for label, line in entries:
        if runs and runs[-1][2] == line:
            runs[-1][1] = label
        else:
            runs.append([label, label, line])
    return [(a if a == b else f"{a} to {b}") + f": {line}" for a, b, line in runs]


def _render(text: str, idxs: set | None) -> str:
    """The page's text, or only the pieces in `idxs` with [...] where pieces are left out."""
    pieces = _pieces(text)
    if idxs is None or set(idxs) >= set(range(len(pieces))):
        return text
    out, last = [], -1
    for i in sorted(i for i in idxs if 0 <= i < len(pieces)):
        if i != last + 1:
            out.append("[...]")
        out.append(pieces[i])
        last = i
    if last < len(pieces) - 1:
        out.append("[...]")
    return "\n".join(out)


def _left_out(texts: list, kept: dict | None) -> list[tuple[str, str]]:
    """(label, opening line) of every piece of these pages not in `kept` ({page: set of piece indexes})."""
    out = []
    for n, t in texts:
        pieces = _pieces(t)
        have = (kept or {}).get(n)
        for i, piece in enumerate(pieces):
            if have is None or i not in have:
                out.append((_label(n, i, len(pieces)), _first_line(piece)))
    return out


def page_map(doc: str, meta: dict, texts: list) -> str:
    """A long document for the look call: its opening pieces whole, then the map of the rest."""
    opening = _opening(texts)
    shown = [_page(n, _render(t, opening[n])) for n, t in texts if n in opening]
    return "\n".join([_head(doc, meta)[:-1] + f' pages="{len(texts)}">'] + shown
                     + ["<map>"] + _map_lines(_left_out(texts, opening)) + ["</map>", "</document>"])


def _opening(texts: list) -> dict[int, set[int]]:
    """{page: pieces} of a long document's first OPEN_PIECES pieces (its title page and contents)."""
    out, room = {}, OPEN_PIECES
    for n, t in texts:
        k = len(_pieces(t))
        if room <= 0:
            break
        out[n] = set(range(min(k, room)))
        room -= min(k, room)
    return out


def _add(keep: dict, have: dict, doc: str, item, room: int) -> int:
    """Add the piece(s) a map label names to keep[doc]; returns the room left (a page named whole adds every
    piece of it)."""
    got = _parse_label(item)
    if not got or got[0] not in have.get(doc, {}):
        return room
    n, i = got
    idxs = range(have[doc][n]) if i is None else ([i] if i < have[doc][n] else [])
    for i in idxs:
        if i in keep[doc].get(n, ()):
            continue
        if room <= 0:
            return room
        keep[doc].setdefault(n, set()).add(i)
        room -= 1
    return room


def chosen(kit: Path, pages: dict) -> dict[str, dict[int, set[int]]]:
    """The pieces to read of each long document, {doc: {page: piece indexes}}: its opening always, then those
    in `pages` ({doc: [map labels]}, the reply to the map) in the order given, at most MAX_LOOK over all."""
    have, keep = {}, {}
    for doc, _, texts in _page_texts(kit, set(), []):
        if sum(len(t) for _, t in texts) > LONG_DOC:
            have[doc] = {n: len(_pieces(t)) for n, t in texts}
            keep[doc] = _opening(texts)
    room = MAX_LOOK
    for doc, items in (pages or {}).items():
        doc = str(doc)
        if doc in keep and isinstance(items, list):
            for item in items:
                room = _add(keep, have, doc, item, room)
    return keep


def _only(kit: Path, keep: dict) -> dict[str, set[int]]:
    """{doc: pages} for every document: all of a short one, the pages `keep` names of a long one."""
    return {doc: (set(keep[doc]) if doc in keep else {n for n, _ in texts})
            for doc, _, texts in _page_texts(kit, set(), [])}


LOOK = ("\nBefore the reading. The documents with a <map> are too long to send whole; the others will be sent whole. "
        "Choose the parts of each mapped document that these questions need: at most {max_look} map entries over all "
        "of them. A map shows the document's opening whole, then every other part by its opening line, labelled by "
        "page (p57) or by page and piece (p3/4 is the fourth piece of page 3); a run of parts opening alike is one "
        "entry, and naming its first label takes only that part. Go by the contents and the headings: for a law, "
        "the pages holding the articles the questions ask about; for a budget book or a register, the summary "
        "tables and one worked example, not every entry. When two documents are copies of each other, choose from "
        'one. Reply with only {{"pages": {{"<document id>": ["p12", "p13", "p3/4"]}}}}, nothing else.\n')


def _look_text(kit: Path, qids: list[str] | None) -> str:
    data, qs, rulings, qwords, names = _asked(kit, qids)
    longs = set(long_docs(kit))
    blocks = []
    for doc, meta, texts in _page_texts(kit, names, qwords):
        blocks.append(page_map(doc, meta, texts) if doc in longs
                      else _head(doc, meta)[:-1] + f' pages="{len(texts)}" sent="whole, at the reading"/>')
    return (_brief(kit) + "\n<questions>\n" + json.dumps({**rulings, "questions": qs}, ensure_ascii=False)
            + "\n</questions>\n\n<documents>\n" + "\n".join(blocks) + "\n</documents>\n" + LOOK.format(max_look=MAX_LOOK))


def parse_pages(text: str) -> dict:
    """{doc: [labels]} from a look reply, {} when it holds none."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    body = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        got = json.loads(body)
    except ValueError:
        return {}
    pages = got.get("pages") if isinstance(got, dict) else None
    return pages if isinstance(pages, dict) else {}


def focus(kit: Path, qids: list[str] | None, model: str, effort: str | None, results: list) -> dict:
    """The look call: the pieces to read of each long document (`chosen`). A reply that names none is asked
    once more; a second such reply raises HandbookError (reading the opening alone would miss answers)."""
    for go in (1, 2):
        r = ask(_look_text(kit, qids), model, effort, kit)
        results.append(r)
        pages = parse_pages(r.get("result"))
        if pages:
            r["look"] = pages           # kept in the receipt: what the look chose of each long document
            return chosen(kit, pages)
    raise HandbookError(f"The look at {kit.name}'s long documents named no pages to read, twice.")


def focus_note(kit: Path, keep: dict) -> str:
    """What the reading is told about the long documents it sees only chosen pages of."""
    titles = {doc: (meta.get("title") or doc) for doc, meta, _ in _page_texts(kit, set(), [])}
    counts = {doc: len(t) for doc, _, t in _page_texts(kit, set(), [])}
    named = [f"{doc} ({titles.get(doc)}, {_span(set(pages))} of {counts.get(doc)})" for doc, pages in keep.items()]
    return ("\nThese documents are too long to send whole, so only chosen parts of each are here (their opening, and "
            "the parts a look at their contents picked; [...] marks a cut inside a page), with a map of the rest by "
            "opening line: " + "; ".join(named)
            + ". Answer from what is here. When another part of one of them would settle a cell (the map says where), "
            "answer what you can and add, beside \"cells\", \"want\": {\"<document id>\": [map labels]}, at most "
            f"{MORE_PAGES} entries over all; they will be sent once more for the cells still open. Never name one of "
            "these documents as missing.")


def _more(kit: Path, keep: dict, wants: list) -> dict[str, dict[int, set[int]]]:
    """The pieces the reading asked for ("want" in its replies) that exist and were not sent, at most MORE_PAGES,
    added to `keep`; returns what was added."""
    have = {doc: {n: len(_pieces(t)) for n, t in texts} for doc, _, texts in _page_texts(kit, set(), []) if doc in keep}
    before = {doc: {n: set(v) for n, v in pages.items()} for doc, pages in keep.items()}
    room = MORE_PAGES
    for want in wants:
        for doc, items in (want or {}).items():
            doc = str(doc)
            if doc in keep and isinstance(items, list):
                for item in items:
                    room = _add(keep, have, doc, item, room)
    added = {}
    for doc, pages in keep.items():
        for n, idxs in pages.items():
            new = idxs - before[doc].get(n, set())
            if new:
                added.setdefault(doc, {})[n] = new
    return added


def _more_text(kit: Path, questions: list[str], cells: dict, added: dict) -> str:
    """The message for the pages a focused reading asked for: the brief, the cells still open with their first
    answers, and only the added pieces (the short documents and chosen pages were read already)."""
    data, qs, rulings, qwords, names = _asked(kit, questions)
    only = {doc: set(pages) for doc, pages in added.items()}
    return (_brief(kit) + "\nThese questions were answered from the row's other pages already (the answers are below, "
            "some with no evidence or unsure). You asked for more of the long documents: only those parts are here, "
            "[...] marking cuts inside a page. Give one answer per question again: keep an answer unless these parts "
            "settle or change it, quoting these parts where they do. Reply with only {\"cells\": {...}} for these "
            "questions.\n<questions>\n" + json.dumps({**rulings, "questions": qs}, ensure_ascii=False)
            + "\n</questions>\n<first_answers>\n" + json.dumps({q: cells.get(q) for q in questions}, ensure_ascii=False)
            + "\n</first_answers>\n\n<documents>\n" + _documents(kit, names, qwords, only, None, added) + "\n</documents>\n")


def _settled(cell) -> bool:
    return isinstance(cell, dict) and cell.get("state") in ANSWERED and not cell.get("unsure")


def _asked(kit: Path, qids: list[str] | None) -> tuple[dict, list, dict, list, set]:
    """(questions file, the questions asked, {"guidance"?, "rulings"} they name, question words, row name words)."""
    data = _questions(kit)
    qs = [q for q in data["questions"] if qids is None or q.get("id") in qids]
    asked = {q.get("id") for q in qs}
    guidance = [g for g in data.get("guidance") or [] if asked & set(g.get("questions") or [])]
    rulings = {**({"guidance": guidance} if guidance else {}),
               "rulings": [r for r in data.get("rulings") or [] if asked & set(r.get("questions") or [])]}
    qwords = [set(words(" ".join([q.get("short") or "", q.get("wording") or ""]))) for q in qs]
    names = name_words((data.get("row") or {}).get("name") or "")
    return data, qs, rulings, qwords, names


def _brief(kit: Path) -> str:
    brief = (kit / BRIEF).read_text(encoding="utf-8") if (kit / BRIEF).is_file() else ""
    if not brief:
        raise HandbookError(f"{kit} has no {BRIEF}. Build the kit again with hb kit.")
    return brief


def message(kit: Path, qids: list[str] | None = None, part: dict | None = None, note: str = "",
            maps: list[str] | None = None, cut: dict | None = None) -> str:
    """The one message: the brief, the questions (those in `qids`, else all) with the rulings they name,
    and the documents (only the pages in `part`, with `note` saying what is in the other parts, or what the
    chosen pages of the long documents in `maps` are)."""
    data, qs, rulings, qwords, names = _asked(kit, qids)
    brief = _brief(kit)
    return (brief + (note + "\n" if note else "") + "\n<questions>\n"
            + json.dumps({**rulings, "questions": qs}, ensure_ascii=False)
            + "\n</questions>\n\n<documents>\n" + _documents(kit, names, qwords, part, maps, cut) + "\n</documents>\n")


def estimate(kit: Path) -> int:
    """Tokens the message is likely to hold (characters / CHARS_PER_TOKEN)."""
    return int(len(message(kit)) / CHARS_PER_TOKEN)


NOTE_ROOM = 3000                     # tokens kept free in each part for the note on the other parts


def parts(kit: Path, qids: list[str] | None = None, limit: int | None = None, only: dict | None = None,
          cut: dict | None = None) -> list[dict]:
    """The kit's pages cut into parts that each fit one message with the brief and the questions:
    [{doc id: set of page numbers}]. A document stays whole when it fits in the part being filled, or
    starts a new part when it fits in one; a document longer than a part is cut into page ranges. One
    part (every page) when the kit fits. `only` {doc: pages} cuts only those pages into parts."""
    limit = limit or LIMIT
    data, qs, rulings, qwords, names = _asked(kit, qids)
    overhead = len(message(kit, qids, part={})) / CHARS_PER_TOKEN + NOTE_ROOM
    room = max(limit - overhead, limit // 4) * CHARS_PER_TOKEN       # characters of pages per part
    out, current, size = [], {}, 0.0
    for doc, meta, texts in _page_texts(kit, names, qwords):
        if only is not None:
            texts = [(n, t) for n, t in texts if n in only.get(doc, ())]
            if not texts:
                continue
        kept = (cut or {}).get(doc) or {}
        sizes = [len(_page(n, _render(t, kept.get(n)))) + 1 for n, t in texts]
        whole = sum(sizes) + len(_head(doc, meta)) + 13
        if current and size + whole > room and whole <= room:
            out.append(current)
            current, size = {}, 0.0
        if size + whole <= room:
            current[doc] = {n for n, _ in texts}
            size += whole
            continue
        for (n, _), k in zip(texts, sizes):          # too long for one part: page ranges
            if current and size + k > room:
                out.append(current)
                current, size = {}, 0.0
            current.setdefault(doc, set()).add(n)
            size += k + (len(_head(doc, meta)) + 13 if len(current[doc]) == 1 else 0)
    if current or not out:
        out.append(current)
    return out


def _span(pages: set) -> str:
    """{1, 2, 3, 7} -> "pages 1 to 3, 7"."""
    nums, runs = sorted(pages), []
    for n in nums:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    words_ = [f"{a}" if a == b else f"{a} to {b}" for a, b in runs]
    return ("page " if len(nums) == 1 else "pages ") + ", ".join(words_)


def part_note(kit: Path, all_parts: list[dict], k: int) -> str:
    """What a reading of part k (0-based) is told about the documents in the other parts."""
    titles = {doc: (meta.get("title") or doc) for doc, meta, _ in _page_texts(kit, set(), [])}
    counts = {doc: len(t) for doc, _, t in _page_texts(kit, set(), [])}
    elsewhere = []
    for j, part in enumerate(all_parts):
        if j == k:
            continue
        for doc, pages in part.items():
            whole = len(pages) == counts.get(doc)
            elsewhere.append(f"{doc} ({titles.get(doc)}" + ("" if whole else f", {_span(pages)}") + ")")
    return (f"\nThe row's documents are too long for one message, so they are read in {len(all_parts)} parts and the "
            f"answers put together afterwards. This is part {k + 1}. Answer every question from the pages here; when "
            "nothing here supports an answer, give no_evidence. The other parts hold: " + "; ".join(elsewhere)
            + ". Never name one of those as missing.")


def lookup_only(kit: Path) -> bool:
    qs = _questions(kit)["questions"]
    return bool(qs) and all(q.get("reading") == "lookup" for q in qs)


def fits(kit: Path) -> bool:
    """Whether the kit can be read in one go: a brief for it, and a message under the limit. Size alone: a kit
    that does not fit is read in parts (`plan` says when it goes to the full scorer instead)."""
    if not (kit / BRIEF).is_file():
        return False
    return estimate(kit) <= (LOOKUP_LIMIT if lookup_only(kit) else LIMIT)


def needs_pictures(kit: Path) -> bool:
    """Whether the kit holds pictures of pages whose words were read poorly (kit.PICTURES): the full scorer can
    look at them, while one read, whole or in parts, sends words only (es-imco-bipe, 21 Oct 2026)."""
    return bool((read_json(kit / "pictures.json", {}) or {}).get("poor"))


def plan(kit: Path) -> dict:
    """How `hb score` will read the kit, before it starts: {"mode": "one read" | "parts" | "full scorer",
    "parts", "estimate", "why"} (estimate: tokens of message, summed over the parts; why, for the full scorer:
    "careful" when the project scores carefully, "pictures" when the kit holds pictures to look at)."""
    if not (kit / BRIEF).is_file():
        return {"mode": "full scorer", "parts": 0, "estimate": None, "why": "careful"}
    if needs_pictures(kit):
        return {"mode": "full scorer", "parts": 0, "estimate": None, "why": "pictures"}
    if fits(kit):
        return {"mode": "one read", "parts": 1, "estimate": estimate(kit)}
    longs = long_docs(kit)
    if longs:
        # The look, then a reading of the short documents, the opening pages and about MAX_LOOK chosen pages.
        keep = chosen(kit, {})
        look = len(_look_text(kit, None)) / CHARS_PER_TOKEN
        read = (len(message(kit, None, _only(kit, keep), maps=longs, cut=keep)) + MAX_LOOK * PIECE) / CHARS_PER_TOKEN
        return {"mode": "one read, focused", "parts": max(1, math.ceil(read / LIMIT)), "estimate": int(look + read),
                "long": len(longs)}
    data = _questions(kit)
    qids = [str(q.get("id")) for q in data["questions"]]
    total, n = 0, 0
    for chunk in _chunks(qids):
        cut = parts(kit, chunk)
        n += len(cut)
        total += sum(len(message(kit, chunk, part)) for part in cut) / CHARS_PER_TOKEN + NOTE_ROOM * len(cut) / 4
    return {"mode": "parts", "parts": n, "estimate": int(total)}


def choose(kit: Path, model: str | None = None, effort: str | None = None) -> tuple[str, str | None]:
    """(model, effort): what was asked for, else the cheaper model for a kit of lookups that fits it, else
    MODEL at ONE_READ_EFFORT."""
    if model:
        return model, effort
    if lookup_only(kit) and estimate(kit) <= LOOKUP_LIMIT:
        return LOOKUP_MODEL, effort
    return MODEL, effort or ONE_READ_EFFORT


def command(model: str, effort: str | None) -> list[str]:
    """A print-mode session with no tools, no MCP servers, hooks, skills or slash commands, and our own
    one-line system prompt; the message comes on stdin."""
    cmd = ["claude", "-p", "--system-prompt", SYSTEM, "--tools", "", "--safe-mode", "--strict-mcp-config",
           "--mcp-config", '{"mcpServers":{}}', "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "json", "--model", model]
    return cmd + (["--effort", effort] if effort else [])


def ask(text: str, model: str, effort: str | None, cwd: Path) -> dict:
    """One call; its JSON result. HandbookError when claude is missing or the call fails."""
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so no scorer can be started this way.")
    try:
        done = subprocess.run(command(model, effort), input=text, capture_output=True, text=True, cwd=cwd,
                              timeout=TIMEOUT_S, env={**os.environ, "DISABLE_PROMPT_CACHING": "1"})
    except subprocess.TimeoutExpired:
        raise HandbookError(f"Claude took more than {TIMEOUT_S // 60} minutes to read {cwd.name} and was stopped.")
    try:
        result = json.loads(done.stdout)
    except ValueError:
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        raise HandbookError(f"The reading of {cwd.name} ended without a result" + (f": {tail[-1][:200]}" if tail else "."))
    if not isinstance(result, dict) or result.get("is_error"):
        raise HandbookError(f"The reading of {cwd.name} failed: {str((result or {}).get('result') or '')[:200]}")
    return result


def _chunks(qids: list[str]) -> list[list[str] | None]:
    """The questions in as few equal chunks as keep each within MAX_QUESTIONS; [None] (every question) when
    they fit one call."""
    if len(qids) <= MAX_QUESTIONS:
        return [None]
    n = math.ceil(len(qids) / MAX_QUESTIONS)
    size = math.ceil(len(qids) / n)
    return [qids[i:i + size] for i in range(0, len(qids), size)]


def parse_reply(text: str) -> dict:
    """The JSON object in a reply (bare, or in a ```json fence). HandbookError when there is none."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    body = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        got = json.loads(body)
    except ValueError:
        raise HandbookError("Claude's reply was not the answers file it was asked for.")
    if not isinstance(got, dict) or not isinstance(got.get("cells"), dict):
        raise HandbookError("Claude's reply has no cells in it.")
    return got


def _repair_text(kit: Path, problems: list[dict], cells: dict) -> str:
    pages: dict[str, set] = {}
    for p in problems:
        if p.get("doc") and (kit / "docs" / str(p["doc"])).is_dir():
            m = re.search(r"is on page (\d+)", p.get("why") or "")
            pages.setdefault(p["doc"], set()).update(
                n for n in (p.get("page"), int(m.group(1)) if m else None) if isinstance(n, int))
    texts = kit_pages(kit)
    blocks = []
    for doc, numbers in sorted(pages.items()):
        blocks.append(f'<document id="{doc}">')
        blocks += [f'<page n="{n}">\n{texts[doc][n]}\n</page>' for n in sorted(numbers) if n in texts.get(doc, {})]
        blocks.append("</document>")
    asked = sorted({p["question"] for p in problems}, key=str)
    return ("These answers have quotes that are not word for word on the page they cite:\n"
            + json.dumps(problems, ensure_ascii=False) + "\nThe answers:\n"
            + json.dumps({q: cells.get(q) for q in asked}, ensure_ascii=False)
            + "\nWrite these cells again, copying each quote character for character from a page below (and citing "
              "that page), at least five words. Keep each value unless the page shows it is wrong. Reply with only "
              "{\"cells\": {...}}.\n<documents>\n" + "\n".join(blocks) + "\n</documents>\n")


ANSWERED = ("answered", "not_applicable")


def _same(a: dict, b: dict) -> bool:
    return a.get("state") == b.get("state") and a.get("value") == b.get("value") and a.get("figure") == b.get("figure")


def merge(found: list[dict]) -> tuple[dict, list[str]]:
    """Put the answers of the parts together: ({question: cell}, [questions the parts disagree on]).
    A question answered in one part takes that answer; the same answer in several keeps every quote
    (and stays unsure only when every part was unsure) and every part's contradiction log; no answer
    anywhere keeps the first no_evidence cell that says what is missing, else the first."""
    out, split = {}, []
    qids = list(dict.fromkeys(q for cells in found for q in cells))
    for q in qids:
        cands = [c[q] for c in found if isinstance(c.get(q), dict)]
        got = [c for c in cands if c.get("state") in ANSWERED]
        if not got:
            out[q] = next((c for c in cands if (c.get("unsure") or {}).get("missing")), cands[0]) if cands else {}
        elif len(got) == 1:
            out[q] = got[0]
        elif all(_same(got[0], c) for c in got[1:]):
            cell, seen = dict(got[0]), set()
            evidence = []
            for c in got:
                for e in c.get("evidence") or []:
                    key = (e.get("doc"), e.get("page"), e.get("quote")) if isinstance(e, dict) else None
                    if key not in seen:
                        seen.add(key)
                        evidence.append(e)
            cell["evidence"] = evidence
            cell["unsure"] = got[0].get("unsure") if all(c.get("unsure") for c in got) else None
            log = conflicts.merge_logs(*(c.get("conflicts") for c in got))   # what each part saw disagree
            if log is not None:
                cell["conflicts"] = log
            out[q] = cell
        else:
            split.append(q)
            out[q] = got[0]
    return out, split


def _reconcile_text(kit: Path, questions: list[str], found: list[dict]) -> str:
    """One more message for the questions the parts answered differently: the brief, those questions, each
    part's answer, and only the pages those answers cite."""
    data, qs, rulings, qwords, names = _asked(kit, questions)
    cited: dict[str, set] = {}
    answers = {}
    for q in questions:
        answers[q] = []
        for k, cells in enumerate(found):
            c = cells.get(q)
            if isinstance(c, dict) and c.get("state") in ANSWERED:
                answers[q].append({"part": k + 1, **c})
                for e in c.get("evidence") or []:
                    if isinstance(e, dict) and isinstance(e.get("page"), int) and e.get("doc"):
                        cited.setdefault(str(e["doc"]), set()).add(e["page"])
    return (_brief(kit) + "\nThe row's documents were read in parts, and these questions were answered differently in "
            "different parts. Each part's answer is below with the pages it cites. Weigh them together and give one "
            "answer per question, as the rules above say, quoting those pages. Reply with only {\"cells\": {...}} for "
            "these questions.\n<questions>\n" + json.dumps({**rulings, "questions": qs}, ensure_ascii=False)
            + "\n</questions>\n<part_answers>\n" + json.dumps(answers, ensure_ascii=False) + "\n</part_answers>\n"
            + "\n<documents>\n" + _documents(kit, names, qwords, cited) + "\n</documents>\n")


def _ask_cells(text: str, qids: list[str], model: str, effort: str | None, kit: Path, results: list,
               tries: int = 1, wants: list | None = None) -> dict:
    """One call's cells for `qids`; a reply that is not an answers file is asked again `tries` more times.
    `wants`, when given, collects the pages the reply asks for ("want", a focused reading)."""
    while True:
        r = ask(text, model, effort, kit)
        results.append(r)
        try:
            got = parse_reply(r.get("result"))
            if wants is not None and isinstance(got.get("want"), dict):
                wants.append(got["want"])
            return {str(q): c for q, c in got["cells"].items() if str(q) in qids}
        except HandbookError:
            if tries <= 0:
                raise
            tries -= 1


def _read(kit: Path, qids: list[str], model: str, effort: str | None, results: list, info: dict) -> dict:
    """Every question in `qids` read once over the kit's pages, in parts when they do not fit one message:
    {question: cell}. info["parts"] counts the parts read."""
    chunk = qids if len(qids) < len(_questions(kit)["questions"]) else None
    if fits(kit):
        info["parts"] += 1
        return _ask_cells(message(kit, chunk), qids, model, effort, kit, results, tries=0)
    longs, only, note = long_docs(kit), None, ""
    if longs:
        info["focused"] = True
        keep = focus(kit, chunk, model, effort, results)
        only, note = _only(kit, keep), focus_note(kit, keep)
        text = message(kit, chunk, only, note, maps=longs, cut=keep)
        if len(text) / CHARS_PER_TOKEN <= LIMIT:
            info["parts"] += 1
            wants: list = []
            cells = _ask_cells(text, qids, model, effort, kit, results, wants=wants)
            more, open_ = _more(kit, keep, wants), [q for q in qids if not _settled(cells.get(q))]
            if more and open_:
                try:
                    cells.update(_ask_cells(_more_text(kit, open_, cells, more), open_, model, effort, kit, results,
                                            tries=0))
                except HandbookError:
                    pass            # the cells keep their first answers
            return cells
    else:
        keep = None
    cut = parts(kit, chunk, only=only, cut=keep)
    info["parts"] += len(cut)
    # A part whose reply is not an answers file is asked once more: falling back to the full scorer for a
    # kit this long is what cost 3.4 million tokens (sweep round 3).
    found = [_ask_cells(message(kit, chunk, part, part_note(kit, cut, k) + note, cut=keep), qids, model, effort, kit,
                        results) for k, part in enumerate(cut)]
    cells, split = merge(found)
    if split:
        try:
            cells.update(_ask_cells(_reconcile_text(kit, split, found), split, model, effort, kit, results, tries=0))
        except HandbookError:
            pass                    # each disagreeing question keeps the first part's answer
    return cells


def run(kit: Path, model: str | None = None, effort: str | None = None) -> dict:
    """Read the kit in one call (one per MAX_QUESTIONS questions), or in parts when it is too long (`parts`),
    check the quotes, send failing quotes back once, write KIT/answers.json and return a receipt-shaped
    result: usage summed over the calls, num_turns the number of calls, `mode` "one read" or "one read in
    parts", `parts`, `quote_problems` the quotes still not on their page. A reply that is not an answers file
    raises HandbookError (the caller can fall back to the full scorer)."""
    model, effort = choose(kit, model, effort)
    data = _questions(kit)
    qids = [str(q.get("id")) for q in data["questions"]]
    whole = fits(kit)
    results, cells, info = [], {}, {"parts": 0}
    for chunk in _chunks(qids):
        cells.update(_read(kit, chunk or qids, model, effort, results, info))
    answers = {"row": (data.get("row") or {}).get("id"), "cells": cells}
    known = set(kit_pages(kit))
    mend_doc_ids(cells, known)           # a document id typed one character off is the kit's document
    for c in cells.values():
        conflicts.mend_docs(c.get("conflicts") if isinstance(c, dict) else None, known)
    write_json(kit / "answers.json", answers)
    problems = check_kit(kit)
    if problems:
        try:
            r = ask(_repair_text(kit, problems, cells), model, effort, kit)
            results.append(r)
            fixed = parse_reply(r.get("result"))["cells"]
            for q, c in fixed.items():          # a re-copied quote keeps the log the first answer gave
                if isinstance(c, dict) and "conflicts" not in c and isinstance(cells.get(q), dict) \
                        and "conflicts" in cells[q]:
                    c["conflicts"] = cells[q]["conflicts"]
            cells.update({q: c for q, c in fixed.items() if q in {p["question"] for p in problems}})
            mend_doc_ids(cells, known)
            for c in cells.values():
                conflicts.mend_docs(c.get("conflicts") if isinstance(c, dict) else None, known)
            write_json(kit / "answers.json", answers)
            problems = check_kit(kit)
        except HandbookError:
            pass                    # the quote check after ingest names what is still wrong
    usage = {k: sum(int((r.get("usage") or {}).get(k) or 0) for r in results) for k in USAGE_KEYS}
    states = Counter(c.get("state") for c in cells.values() if isinstance(c, dict))
    unsure = sum(1 for c in cells.values() if isinstance(c, dict) and c.get("unsure"))
    mode = "one read" if whole else ("one read, focused" if info.get("focused") else "one read in parts")
    return {"type": "result", "subtype": "success", "is_error": False, "mode": mode, "model": model,
            "effort": effort, "num_turns": len(results), "usage": usage,
            "total_cost_usd": round(sum(float(r.get("total_cost_usd") or 0) for r in results), 4),
            "quote_problems": len(problems),
            "parts": None if whole else info["parts"],
            "look": [r["look"] for r in results if r.get("look")] or None,     # a focused reading's chosen pieces
            "result": (f"Answered {len(cells)} of {len(qids)} questions in one reading"
                       + ("" if whole else (f" of chosen pages, as {len(long_docs(kit))} document(s) are too long to send "
                                            f"whole" + (f", in {info['parts']} parts" if info["parts"] > 1 else "")
                                            if info.get("focused") else
                                            f" in {info['parts']} parts (the documents are too long for one message)"))
                       + f": {states.get('no_evidence', 0)} "
                       f"with no evidence, {unsure} marked unsure"
                       + (f", {len(problems)} quote{'s' if len(problems) != 1 else ''} still not word for word"
                          if problems else "") + ".")}

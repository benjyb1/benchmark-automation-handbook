"""The contradiction log: what a reader saw disagree behind one answer, which side the answer went with and why,
and the figures worked out from it by plain code, with no model call and no one marking anything by hand.

Built 9 Nov 2026 for a cross-source comparison on an open-data benchmark. The reader keeps a small log per answer
instead of a person checking each one: when two documents here (or two passages of one) would give the question
different answers, the cell carries one entry for that disagreement.

    "conflicts": [{"sides": [{"doc": "d-...", "page": 4, "says": "No duty to report breaches.", "kind": "law"},
                             {"doc": "d-...", "page": 2, "says": "Art. 38 requires notice to the authority.",
                              "kind": "law"}],
                   "went_with": "d-...", "why": "newer_version", "note": "Law 19670 (2018) amends Law 18331."}]

`[]` means the reader was asked for the log and saw nothing disagree. A cell with no `conflicts` key was read
before the log existed, or by a reader not asked for it: it is left out of every figure, never counted as a cell
where nothing disagreed, so old answers load and count as they did.

A side's `kind` follows a general order of evidence, best
first, which a project's own rules can override: the law itself from an official source; the government's own records, datasets and reports (and the tool's
probe of them); a government page describing a law, dataset or activity; an exact copy of an official text on
another site; a national news report; anything else. `draft` (a bill or draft not passed) sits outside that order:
it never outranks a text in force. `why` is the reader's reason for the side it went with; `unresolved` (with
`went_with` null) is a disagreement it could not settle, which should leave the cell unsure.

A malformed entry is dropped when the answers are stored (`clean`), never a reason to refuse a row's answers:
the log is a record kept beside the answer, and a reader's slip in it must not throw the answers away.

`figures(records)` gives the metrics: the flag rate (cells with a disagreement among cells logged) per indicator
and per row; how often a disagreement ends in an unsure answer, against cells without one; how often one is left
unresolved; which kind of source wins (the reader's own tags) and whether the winner is the better kind in the evidence
order; and, from the documents' own dates by plain code, whether the answer went with the copy dated inside the
study period over one outside it, and with the later of two dated versions. `report(figs)` puts them in words.
"""
from __future__ import annotations
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

KINDS = ("law", "government_record", "government_page", "official_copy", "news", "other", "draft")
RANK = {k: i for i, k in enumerate(KINDS[:6], start=1)}       # the evidence order, 1 best; a draft has no place in it
KIND_WORDS = {"law": "the law itself", "government_record": "a government record or dataset",
              "government_page": "a government page describing it", "official_copy": "a copy on another site",
              "news": "a news report", "other": "another source", "draft": "a draft"}
WHY = ("source_order", "in_force", "in_period", "newer_version", "more_specific", "rule", "unresolved")
WHY_WORDS = {"source_order": "the better kind of source", "in_force": "the text in force over a draft",
             "in_period": "the one dated inside the study period", "newer_version": "the later version",
             "more_specific": "the more specific text", "rule": "a coding rule or ruling",
             "unresolved": "could not choose"}
SAYS_CHARS = 300            # a side's own words on what it gives, cut to this
NOTE_CHARS = 300
MAX_CONFLICTS = 6           # entries kept per cell
MAX_SIDES = 4               # sides kept per entry

# What the reader is told, in the one-read brief (kit.reading_text) and the full scorer's (kit.brief_text).
BRIEF = ("conflicts: the log of evidence that disagrees. Give [] when the documents agree, or only one speaks to the "
         "question. When two documents here, or two passages of one, would give this question different answers, add one entry for each such disagreement. That includes a "
         "text you did not follow because another here repeals, amends, replaces or outdates it, or because a copy's "
         "date and the law's own date disagree: log it even when the choice is plain. Each entry is "
         "{\"sides\": [{\"doc\": its id, \"page\": its page or null, \"says\": a few words of your own on what it "
         "gives, \"kind\": what it is}, ...one for each side], \"went_with\": the id of the document the answer "
         "follows, or null when you cannot choose, \"why\": the reason, \"note\": one short sentence}. kind is law (the "
         "law or regulation itself, from an official source), government_record (the government's own records, "
         "datasets, portal pages or reports, or the tool's probe of them), government_page (a government page "
         "describing a law, dataset or activity), official_copy (an exact copy of an official text on another site), "
         "news (a news report), other (NGO, academic, company or law firm pages) or draft (a bill or draft not "
         "passed). Unless the rules say otherwise, prefer them in that order, the law first; a draft never outweighs a text in force. why is "
         "source_order, in_force (a text in force over a draft), in_period (dated inside the study period over one "
         "outside it), newer_version (a later version or amendment over an earlier one), more_specific, rule (a coding "
         "rule or ruling decides it) or unresolved. When you cannot choose, give went_with null and why unresolved, "
         "and mark the cell unsure. Copies of one text that say the same thing are not a disagreement.")


# --- the log on one answer -------------------------------------------------------------------------

def _text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit] if value else None


def _page(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def clean(raw, docs: dict | None = None) -> tuple[list | None, int]:
    """(the log as it is stored, how many entries or sides were dropped). None when the reader gave no log
    (the key absent or null). `docs` {doc id: set of page numbers}, when given, is what the reader could cite: a
    side naming another document is dropped, and a page that is not one of its pages becomes null. An entry left
    with fewer than two sides is dropped; `went_with` that names no side becomes null; a `kind` or `why` not in
    KINDS or WHY becomes null, except that a null `went_with` always reads as unresolved."""
    if raw is None:
        return None, 0
    if not isinstance(raw, list):
        return [], 1
    kept, dropped = [], 0
    for item in raw:
        if not isinstance(item, dict) or len(kept) >= MAX_CONFLICTS:
            dropped += 1
            continue
        sides = []
        for s in item.get("sides") or [] if isinstance(item.get("sides"), list) else []:
            doc = s.get("doc") if isinstance(s, dict) else None
            if not isinstance(doc, str) or not doc.strip() or (docs is not None and doc not in docs) \
                    or len(sides) >= MAX_SIDES:
                dropped += 1
                continue
            page = _page(s.get("page"))
            if docs is not None and page is not None and docs.get(doc) and page not in docs[doc]:
                page = None
            sides.append({"doc": doc, "page": page, "says": _text(s.get("says"), SAYS_CHARS) or "",
                          "kind": s.get("kind") if s.get("kind") in KINDS else None})
        if len(sides) < 2:
            dropped += 1
            continue
        went = item.get("went_with")
        went = went if isinstance(went, str) and went in {s["doc"] for s in sides} else None
        why = item.get("why") if item.get("why") in WHY else None
        if went is None:
            why = "unresolved"
        entry = {"sides": sides, "went_with": went, "why": why}
        note = _text(item.get("note"), NOTE_CHARS)
        if note:
            entry["note"] = note
        kept.append(entry)
    return kept, dropped


def problems(log, where: str) -> list[str]:
    """What is wrong with a stored log, as plain sentences ([] when fine or absent): the shape check of
    shapes.check_answers. The same rules `clean` applies, so a log it kept always passes."""
    if log is None:
        return []
    if not isinstance(log, list):
        return [f"{where}: conflicts must be a list"]
    out = []
    for i, e in enumerate(log, start=1):
        w = f"{where}: conflict {i}"
        if not isinstance(e, dict):
            out.append(f"{w} must be an object")
            continue
        sides = e.get("sides") if isinstance(e.get("sides"), list) else []
        if len(sides) < 2:
            out.append(f"{w}: sides must list at least two sides")
        for j, s in enumerate(sides, start=1):
            ws = f"{w}: side {j}"
            if not isinstance(s, dict):
                out.append(f"{ws} must be an object")
                continue
            if not isinstance(s.get("doc"), str) or not s["doc"].strip():
                out.append(f"{ws}: doc must be a document id")
            if s.get("page") is not None and _page(s.get("page")) is None:
                out.append(f"{ws}: page must be a whole number of 1 or more, or null")
            if not isinstance(s.get("says", ""), str):
                out.append(f"{ws}: says must be text")
            if s.get("kind") is not None and s.get("kind") not in KINDS:
                out.append(f"{ws}: kind must be one of {', '.join(KINDS)}, or null")
        went = e.get("went_with")
        if went is not None and went not in {s.get("doc") for s in sides if isinstance(s, dict)}:
            out.append(f"{w}: went_with must be the doc of one of its sides, or null")
        if e.get("why") is not None and e.get("why") not in WHY:
            out.append(f"{w}: why must be one of {', '.join(WHY)}, or null")
        if e.get("note") is not None and not isinstance(e.get("note"), str):
            out.append(f"{w}: note must be text")
    return out


def mend_docs(log, known) -> int:
    """Put right, in place, side docs and went_with one character off a kit document's id (as quotecheck's
    mend_doc_ids does for evidence). Returns how many were mended."""
    from .quotecheck import near_doc
    mended = 0
    for e in log if isinstance(log, list) else []:
        if not isinstance(e, dict):
            continue
        for s in e.get("sides") or [] if isinstance(e.get("sides"), list) else []:
            right = near_doc(s.get("doc"), known) if isinstance(s, dict) else None
            if right:
                s["doc"] = right
                mended += 1
        right = near_doc(e.get("went_with"), known)
        if right:
            e["went_with"] = right
            mended += 1
    return mended


def merge_logs(*logs) -> list | None:
    """The logs of several readings of one question put together (the parts of a long kit that gave the same
    answer): every entry once, by its sides' documents and the side it went with. None when no reading kept one."""
    have = [log for log in logs if isinstance(log, list)]
    if not have:
        return None
    out, seen = [], set()
    for log in have:
        for e in log:
            if not isinstance(e, dict):
                continue
            key = (tuple(sorted(str(s.get("doc")) for s in e.get("sides") or [] if isinstance(s, dict))),
                   e.get("went_with"))
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


# --- records: the cells the figures are worked out over --------------------------------------------

def indicator_of(question: dict | None) -> str:
    """The indicator a question belongs to: its `indicator`, else its section without a part suffix
    ("DPL-EX" -> "DPL"), else its id's letters ("D4" -> "D")."""
    q = question or {}
    if isinstance(q.get("indicator"), str) and q["indicator"]:
        return q["indicator"]
    section = q.get("section")
    if isinstance(section, str) and section:
        return section.split("-")[0]
    m = re.match(r"[A-Za-z]+", str(q.get("id") or ""))
    return m.group(0) if m else "?"


def record(row: str, row_name: str | None, qid: str, cell: dict, question: dict | None, docs: dict,
           cutoff: str | None) -> dict | None:
    """One cell as the figures read it, or None when it carries no log. `docs` {doc id: meta} gives each side's
    date, title and weak mark; `cutoff` the end of the study period (ISO date)."""
    if not isinstance(cell, dict) or not isinstance(cell.get("conflicts"), list):
        return None
    return {"row": row, "row_name": row_name or row, "question": qid, "indicator": indicator_of(question),
            "state": cell.get("state"), "value": cell.get("value"), "unsure": bool(cell.get("unsure")),
            "conflicts": cell["conflicts"],
            "docs": {s["doc"]: _doc_facts(docs.get(s["doc"])) for e in cell["conflicts"] if isinstance(e, dict)
                     for s in e.get("sides") or [] if isinstance(s, dict) and isinstance(s.get("doc"), str)},
            "cutoff": cutoff}


DRAFT_WORDS = re.compile(r"(?i)\b(draft|bill|projet de loi|avant-projet|proyecto de ley|anteproyecto|anteprojeto|"
                         r"projeto de lei|for comment)\b")


def _doc_facts(meta) -> dict:
    """What plain code can tell of a document from its meta.json: its date (the law's own date when the copy was
    dated as a law), whether its title marks it a draft, whether it counts as a weak source."""
    from .fetch import is_weak
    meta = meta if isinstance(meta, dict) else {}
    when = meta.get("date")
    law = meta.get("law") if isinstance(meta.get("law"), dict) else {}
    if not isinstance(when, str) and isinstance(law.get("year"), int):
        when = str(law["year"])
    return {"date": when if isinstance(when, str) else None,
            "draft": bool(DRAFT_WORDS.search(str(meta.get("title") or ""))),
            "weak": is_weak(meta)}


def _when(value: str | None) -> tuple[date, date] | None:
    """(earliest, latest) day a date of the form YYYY, YYYY-MM or YYYY-MM-DD could mean; None when unreadable."""
    if not isinstance(value, str):
        return None
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", value)
    if not m:
        return None
    y, mo, d = int(m.group(1)), m.group(2), m.group(3)
    try:
        if d:
            day = date(y, int(mo), int(d))
            return day, day
        if mo:
            first = date(y, int(mo), 1)
            nxt = date(y + (int(mo) == 12), int(mo) % 12 + 1, 1)
            return first, date.fromordinal(nxt.toordinal() - 1)
        return date(y, 1, 1), date(y, 12, 31)
    except ValueError:
        return None


def _in_period(value: str | None, cutoff: str | None) -> bool | None:
    """True when dated on or before the cut-off, False when after it, None when either is unknown or a year
    straddles it."""
    span, cut = _when(value), _when(cutoff)
    if span is None or cut is None:
        return None
    if span[1] <= cut[0]:
        return True
    if span[0] > cut[0]:
        return False
    return None


def project_records(project) -> list[dict]:
    """Every logged cell of a project folder (answers/<row>.json), in units.json order."""
    proj = project.read("project")
    cutoff = proj.get("cutoff") if isinstance(proj.get("cutoff"), str) else None
    questions = {q.get("id"): q for q in project.read("questions").get("questions") or [] if isinstance(q, dict)}
    docs = project.documents()
    out = []
    for unit in project.read("units").get("units") or []:
        if not isinstance(unit, dict) or not unit.get("id"):
            continue
        row = unit["id"]
        row_cut = unit.get("cutoff") if isinstance(unit.get("cutoff"), str) else cutoff
        for qid, cell in (project.answers(row).get("cells") or {}).items():
            r = record(row, unit.get("name"), qid, cell, questions.get(qid), docs, row_cut)
            if r:
                out.append(r)
    return out


def kit_records(kit: Path, answers: dict | None = None, cutoff: str | None = None) -> list[dict]:
    """Every logged cell of a kit's answers (KIT/answers.json, or `answers`), with the kit's questions and the
    meta.json of its documents. A kit does not record the cut-off, so it is given (`cutoff`)."""
    import json
    kit = Path(kit)
    answers = answers if answers is not None else json.loads((kit / "answers.json").read_text(encoding="utf-8"))
    qfile = json.loads((kit / "questions.json").read_text(encoding="utf-8"))
    questions = {q.get("id"): q for q in qfile.get("questions") or [] if isinstance(q, dict)}
    row = qfile.get("row") or {}
    docs = {}
    for folder in sorted((kit / "docs").iterdir()) if (kit / "docs").is_dir() else []:
        if (folder / "meta.json").is_file():
            docs[folder.name] = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    out = []
    for qid, cell in (answers.get("cells") or {}).items():
        r = record(str(row.get("id") or answers.get("row") or kit.name), row.get("name"), qid, cell,
                   questions.get(qid), docs, cutoff)
        if r:
            out.append(r)
    return out


# --- the figures -----------------------------------------------------------------------------------

def _same_kind(entry: dict, a: str, b: str) -> bool:
    """Whether the reader gave the sides on documents a and b the same kind, and neither is a draft: two
    versions of one sort of text, where the later is the one to follow. Untagged sides count as the same."""
    kinds = {s.get("doc"): s.get("kind") for s in entry.get("sides") or [] if isinstance(s, dict)}
    ka, kb = kinds.get(a), kinds.get(b)
    return "draft" not in (ka, kb) and (ka is None or kb is None or ka == kb)


def _rate(n: int, d: int) -> dict:
    return {"n": n, "of": d, "share": round(n / d, 3) if d else None}


def figures(records: list[dict]) -> dict:
    """The metrics over logged cells (see the module docstring). Nothing here calls a model."""
    logged = len(records)
    flagged = [r for r in records if r["conflicts"]]
    clear = [r for r in records if not r["conflicts"]]
    entries = [(r, e) for r in flagged for e in r["conflicts"] if isinstance(e, dict)]

    def by(key):
        groups: dict[str, list] = defaultdict(list)
        for r in records:
            groups[r[key]].append(r)
        return {k: _rate(sum(1 for r in v if r["conflicts"]), len(v)) for k, v in sorted(groups.items())}

    why = Counter(e.get("why") or "none" for _, e in entries)
    unresolved = sum(1 for _, e in entries if not e.get("went_with"))

    # Which kind wins, by the reader's own tags; and whether it is the better kind in the evidence order.
    wins: Counter = Counter()
    beats: Counter = Counter()          # (winner kind, loser kind)
    order = Counter()                   # followed / against / same rank
    draft = Counter()                   # went with the draft / with the text in force, when one side is a draft
    for _, e in entries:
        went = e.get("went_with")
        if not went:
            continue
        sides = [s for s in e.get("sides") or [] if isinstance(s, dict)]
        won = [s for s in sides if s.get("doc") == went]
        lost = [s for s in sides if s.get("doc") != went]
        wk = won[0].get("kind") if won else None
        wins[wk or "untagged"] += 1
        for s in lost:
            beats[(wk or "untagged", s.get("kind") or "untagged")] += 1
            if wk in RANK and s.get("kind") in RANK:
                order["followed" if RANK[wk] < RANK[s["kind"]] else "against" if RANK[wk] > RANK[s["kind"]]
                      else "same_rank"] += 1
            if (wk == "draft") != (s.get("kind") == "draft"):
                draft["with_draft" if wk == "draft" else "with_in_force"] += 1

    # By plain code, from the documents' own dates: inside the study period over outside it, and later over earlier.
    period, newer, code_draft = Counter(), Counter(), Counter()
    for r, e in entries:
        went = e.get("went_with")
        if not went:
            continue
        facts = r["docs"]
        others = {s.get("doc") for s in e.get("sides") or [] if isinstance(s, dict)} - {went}
        mine = facts.get(went) or {}
        for other in others:
            theirs = facts.get(other) or {}
            a, b = _in_period(mine.get("date"), r["cutoff"]), _in_period(theirs.get("date"), r["cutoff"])
            if a is not None and b is not None and a != b:
                period["in_period" if a else "outside"] += 1
            elif a and b and not (mine.get("draft") or theirs.get("draft")) and _same_kind(e, went, other):
                # two dated texts of one kind inside the period (two versions of a law, say): the later or the earlier
                ma, mb = _when(mine.get("date")), _when(theirs.get("date"))
                if ma and mb and (ma[1] < mb[0] or mb[1] < ma[0]):
                    newer["newer" if ma[0] > mb[1] else "older"] += 1
            if mine.get("draft") != theirs.get("draft"):
                code_draft["with_draft" if mine.get("draft") else "with_in_force"] += 1

    return {
        "logged": logged, "with_conflict": len(flagged), "entries": len(entries),
        "flag_rate": _rate(len(flagged), logged),
        "by_indicator": by("indicator"), "by_row": by("row_name"),
        "unsure": {"with_conflict": _rate(sum(r["unsure"] for r in flagged), len(flagged)),
                   "without": _rate(sum(r["unsure"] for r in clear), len(clear))},
        "unresolved": _rate(unresolved, len(entries)),
        "unresolved_not_unsure": sum(1 for r, e in entries if not e.get("went_with") and not r["unsure"]),
        "why": dict(why.most_common()),
        "winner_kind": dict(wins.most_common()),
        "beats": {f"{a} over {b}": n for (a, b), n in beats.most_common()},
        "source_order": {**dict(order), "share_followed": round(order["followed"] / (order["followed"] + order["against"]), 3)
                         if order["followed"] + order["against"] else None},
        "draft": dict(draft), "draft_by_title": dict(code_draft),
        "dates": {"period": dict(period), "versions": dict(newer)},
    }


def _pct(rate: dict) -> str:
    return "none logged" if not rate["of"] else f"{rate['n']} of {rate['of']} ({round(100 * rate['share'])}%)"


def report(figs: dict) -> str:
    """The figures in plain words, one line each."""
    if not figs["logged"]:
        return ("No answer here carries a contradiction log yet: the answers were read before it existed. Score "
                "them again to log what disagrees.")
    lines = [f"Answers with a log: {figs['logged']}. Sources disagreed behind {_pct(figs['flag_rate'])}, "
             f"{figs['entries']} disagreement(s) in all."]
    lines.append("By indicator: " + "; ".join(f"{k} {_pct(v)}" for k, v in figs["by_indicator"].items()) + ".")
    lines.append("By row: " + "; ".join(f"{k} {_pct(v)}" for k, v in figs["by_row"].items()) + ".")
    u = figs["unsure"]
    lines.append(f"Unsure: {_pct(u['with_conflict'])} answers with a disagreement, against {_pct(u['without'])} "
                 "without one.")
    if figs["entries"]:
        lines.append(f"Left unresolved: {_pct(figs['unresolved'])}"
                     + (f", {figs['unresolved_not_unsure']} of them in an answer not marked unsure"
                        if figs["unresolved_not_unsure"] else "") + ".")
        lines.append("Why the side was chosen: " + "; ".join(f"{WHY_WORDS.get(k, k)} {n}" for k, n in figs["why"].items())
                     + ".")
        if figs["winner_kind"]:
            lines.append("Kind of source that won: " + "; ".join(f"{KIND_WORDS.get(k, k)} {n}"
                                                                   for k, n in figs["winner_kind"].items()) + ".")
        so = figs["source_order"]
        if so.get("followed") or so.get("against"):
            lines.append(f"Evidence order (the law first, anything else last): followed in {so.get('followed', 0)}, went "
                         f"against it in {so.get('against', 0)}, same kind {so.get('same_rank', 0)}.")
        d = figs["draft"]
        if d:
            lines.append(f"Draft against a text in force: went with the text in force {d.get('with_in_force', 0)}, "
                         f"with the draft {d.get('with_draft', 0)}.")
        p, v = figs["dates"]["period"], figs["dates"]["versions"]
        if p:
            lines.append(f"By the documents' dates: went with the one inside the study period {p.get('in_period', 0)}, "
                         f"outside it {p.get('outside', 0)}.")
        if v:
            lines.append(f"Two dated texts of one kind inside the period: went with the later {v.get('newer', 0)}, the "
                         f"earlier {v.get('older', 0)}.")
    return "\n".join(lines)

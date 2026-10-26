"""Cell flags, the second-read queue and the second-search queue.

`run_checks(project)` recomputes `checks/<row>.json` for every scored row; the flags are
`quote_not_found`, `after_cutoff`, `group_differs`, `judgement`, `reads_disagree` (see Shapes in
the plan), `weak_source` (every document the cell cites is weak: see `fetch.weak_why`) and
`document_changed` (the saved copy of a cited document is no longer the capture the answer was
scored from), `owner_changed` (the owner corrected the answer; see corrections.py) and
`todays_version` (a web page cited was kept as it is today, long after the cut-off, with no archive
copy from before it: see cmd_fetch) and `needs_document` (the scorer needed a document it did not have and
the answer took the question's no-evidence value: map-equality, 21 Oct 2026, scored harmful laws the finder
had named but could not fetch at "no such law", the best score). A quote is matched against the capture its answer names (`evidence[].capture`, put
there by `ingest` from the kit), so a document that changed after scoring is not a quote problem;
the cell is marked out of date instead, and `hb todo` lists it for a rescore.
`second_reads(project)` lists the cells a second reader should look at, and
`record_second_read(...)` stores the result. `second_finds(project)` lists the rows that still
need their one second search.

Everything here reads only the project folder; `run_checks` writes `checks/` and the
`out_of_date` mark.
"""
from __future__ import annotations
import re
from datetime import date

from . import answers
from .fetch import is_weak
from .folder import HandbookError, ID_RE, Project
# The grounding test lives in quotecheck.py, which the kit also carries, so the scorer's own check
# and this one are the same code. The names stay importable from here.
from .quotecheck import (MIN_RUN, SHORT_MIN, capture_id, is_grounded, is_grounded_ocr,  # noqa: F401
                         longest_run, normalise, source_join, tokens)
from .render import TODAY_GRACE_DAYS
from .shapes import FLAGS, is_date

# A second read is only worth its cost where rows of one group answered the same question
# differently from the same documents. Quote, date and single-document flags did not predict
# fixable mistakes in earlier runs.
SECOND_READ_TRIGGERS = {"group_differs", "holds_back"}
# On a staircase score sheet (a band needs whole sections met), an answer below the top in a section the
# next band needs holds the row below it: one wrong No low down caps the level (TPI, 21 Oct 2026: MQ10
# alone took Tesco from 5 to 3). Those answers get their second read first.
FIRST_READS = {"holds_back"}

SCORED_STATES = ("answered", "no_evidence")      # the cells that carry evidence checks and flags


def _source_join(pages: list[dict]) -> str:
    """The matching text of a list of pages (`pages.json` entries): see `quotecheck.source_join`."""
    return source_join((p.get("text") or "") for p in pages if isinstance(p, dict))


# --- reading the folder ------------------------------------------------------------------------

class _Documents:
    """Document metadata, each kept document's matching text (its current pages, or a named
    capture) and the id of its current capture, read once."""

    def __init__(self, project: Project):
        self.project = project
        self.meta = project.documents()
        self._src: dict[tuple, str | None] = {}
        self._current: dict[str, str | None] = {}

    def kept(self, doc: str) -> bool:
        meta = self.meta.get(doc)
        return bool(meta) and bool(meta.get("kept"))

    def current_capture(self, doc: str) -> str | None:
        """The id of the document's pages as they are now: meta.json's `capture`, else worked out
        from pages.json (a document read before captures were recorded)."""
        if doc not in self._current:
            cid = (self.meta.get(doc) or {}).get("capture")
            if not isinstance(cid, str):
                pages = self.project.pages(doc)
                cid = capture_id(p.get("text") or "" for p in pages if isinstance(p, dict)) if pages else None
            self._current[doc] = cid
        return self._current[doc]

    def pages(self, doc: str, capture: str | None = None) -> list[dict]:
        """The pages `source` reads: the current ones, or the named capture's when that is another one."""
        if capture and capture != self.current_capture(doc):
            return self.project.capture(doc, capture) or []
        return self.project.pages(doc)

    def has_ocr(self, doc: str, capture: str | None = None) -> bool:
        """Whether any page of the document (as it was scored) holds words read from a picture."""
        return any(isinstance(p, dict) and p.get("ocr") for p in self.pages(doc, capture))

    def found(self, doc: str, quote: str, capture: str | None = None) -> bool | None:
        """Whether the quote is on the document as it was scored: word for word, or, on a page read from a
        picture (OCR), word for word but for a slip of a letter. None when the capture is not on file."""
        src = self.source(doc, capture)
        if src is None:
            return None
        text = normalise(quote)
        if is_grounded(text, src):
            return True
        if not self.has_ocr(doc, capture):
            return False
        ocr_text = source_join(p.get("text") or "" for p in self.pages(doc, capture)
                               if isinstance(p, dict) and p.get("ocr"))
        return is_grounded_ocr(text, ocr_text)

    def source(self, doc: str, capture: str | None = None) -> str | None:
        """The matching text of `doc`: its current pages, or the capture `capture` when that is
        not the current one. None when that capture is not on file, so there is nothing to
        match against."""
        key = (doc, capture if capture and capture != self.current_capture(doc) else None)
        if key not in self._src:
            if key[1] is None:
                self._src[key] = _source_join(self.project.pages(doc))
            else:
                pages = self.project.capture(doc, key[1])
                self._src[key] = _source_join(pages) if pages else None
        return self._src[key]


def _order(project: Project, ids) -> list[str]:
    """`ids` with the units of units.json first, in file order, then the rest sorted."""
    ids = set(ids)
    units = [u.get("id") for u in project.read("units").get("units", []) if isinstance(u, dict)]
    return [u for u in units if u in ids] + sorted(ids - set(units))


def _stems(folder) -> set[str]:
    if not folder.is_dir():
        return set()
    return {f.stem for f in folder.glob("*.json") if f.is_file() and ID_RE.fullmatch(f.stem)}


def _question_order(project: Project) -> dict[str, int]:
    return {q.get("id"): i for i, q in enumerate(project.read("questions").get("questions", []))
            if isinstance(q, dict)}


def _sorted_ids(qids, order: dict[str, int]) -> list[str]:
    return sorted(qids, key=lambda q: (order.get(q, len(order)), str(q)))


def _scored_cells(project: Project, row: str) -> dict[str, dict]:
    """The row's answered and no_evidence cells."""
    cells = project.answers(row).get("cells") or {}
    return {qid: c for qid, c in cells.items() if isinstance(c, dict) and c.get("state") in SCORED_STATES}


def scored_rows(project: Project) -> list[str]:
    """Rows with at least one answered or no_evidence cell, in unit order."""
    rows = [r for r in _stems(project.path / "answers") if _scored_cells(project, r)]
    return _order(project, rows)


def unchecked_rows(project: Project) -> list[str]:
    """Scored rows that have no `checks/<row>.json` yet (so `hb check` has not run for them)."""
    have = _stems(project.path / "checks")
    return [r for r in scored_rows(project) if r not in have]


def _cited(cell: dict) -> list[str]:
    return [e["doc"] for e in cell.get("evidence") or []
            if isinstance(e, dict) and isinstance(e.get("doc"), str)]


def _group_value(unit: dict | None, label):
    if not label or not isinstance(unit, dict):
        return None
    value = (unit.get("labels") or {}).get(label)
    return None if value in (None, "") else value


# --- the flags ---------------------------------------------------------------------------------

def _after_cutoff(meta: dict | None, cutoff: str | None) -> bool:
    """Whether the document is dated after the cut-off. A date given only as a year is after it
    only when that year is later than the cut-off's year, since the document may be from earlier
    in that year."""
    if not meta or not cutoff:
        return False
    when = meta.get("date")
    if isinstance(when, str) and re.fullmatch(r"\d{4}", when):
        return int(when) > date.fromisoformat(cutoff).year
    if not is_date(when):
        return False
    return date.fromisoformat(when) > date.fromisoformat(cutoff)


def _todays_version(meta: dict | None, cutoff: str | None) -> bool:
    """Whether the document is a web page kept as it was on the day it was fetched, long after the
    cut-off, with no archive copy from before it (cmd_fetch, `todays_version`). The owner dating it
    on or before the cut-off clears it; a date the finder gave does not (BankTrack, 20 Oct 2026: a
    finder dated BBVA's 2020 statement 2023-12-01)."""
    today = (meta or {}).get("todays_version")
    if not isinstance(today, dict) or not cutoff or not is_date(today.get("fetched")):
        return False
    if (date.fromisoformat(today["fetched"]) - date.fromisoformat(cutoff)).days <= TODAY_GRACE_DAYS:
        return False
    when = meta.get("date")
    return not (meta.get("date_basis") == "owner" and is_date(when)
                and date.fromisoformat(when) <= date.fromisoformat(cutoff))


def _same_value(a, b) -> bool:
    """Equal and of the same type, so 3.0 is not 3 and True is not 1."""
    return a == b and type(a) is type(b)


def compute_checks(project: Project) -> dict[str, dict]:
    """`{row: checks-file dict}` for every scored row, computed fresh from the answers, the
    documents and the project. A recorded `second_read` is carried over with `agrees` worked out
    again against the current answer, unless the answer has changed since it was recorded, in which
    case the second read is dropped and the cell is queued for another. Nothing is written."""
    proj = project.read("project")
    cutoff = proj.get("cutoff") if is_date(proj.get("cutoff")) else None
    group_label = (proj.get("group_labels") or [None])[0]
    units = {u.get("id"): u for u in project.read("units").get("units", []) if isinstance(u, dict)}
    docs = _Documents(project)
    order = _question_order(project)
    qmap = {q.get("id"): q for q in project.read("questions").get("questions", []) if isinstance(q, dict)}

    def same(qid, a, b) -> bool:
        q = qmap.get(qid)
        return answers.same_answer(q, a, b) if answers.is_number(q) else _same_value(a, b)

    cells = {row: _scored_cells(project, row) for row in scored_rows(project)}
    flags: dict[str, dict[str, set]] = {row: {qid: set() for qid in cs} for row, cs in cells.items()}
    from .recheck import held_pages
    gone = held_pages(docs.meta)          # pages most of whose text went at a re-check, waiting for the owner

    for row, row_cells in cells.items():
        for qid, c in row_cells.items():
            mine = flags[row][qid]
            if c.get("judgement") is True:
                mine.add("judgement")
            if isinstance(c.get("owner"), dict):
                mine.add("owner_changed")     # the owner's answer; the bundle says whether Claude agreed
            # A row may carry its own cut-off (each ACES camp's audit, each Seafood Watch assessment).
            row_cutoff = (units.get(row) or {}).get("cutoff")
            row_cutoff = row_cutoff if is_date(row_cutoff) else cutoff
            for doc in dict.fromkeys(_cited(c)):
                if _after_cutoff(docs.meta.get(doc), row_cutoff):
                    mine.add("after_cutoff")
                elif _todays_version(docs.meta.get(doc), row_cutoff):
                    mine.add("todays_version")    # no second search for it: the finder cannot reach the archive either
            if any(doc in gone for doc in _cited(c)):
                mine.add("page_gone")
            if _cited(c) and all(is_weak(docs.meta.get(doc)) for doc in _cited(c)):
                mine.add("weak_source")
            doubt = c.get("unsure")
            blank = (qmap.get(qid) or {}).get("no_evidence_value")
            if (isinstance(doubt, dict) and doubt.get("why") == "document" and not doubt.get("handled")
                    and (c.get("state") == "no_evidence" or (blank is not None and _same_value(c.get("value"), blank)))):
                mine.add("needs_document")    # scored as if there were nothing, for want of a document the owner can add
            for e in c.get("evidence") or []:
                if not isinstance(e, dict) or not isinstance(e.get("doc"), str) or not docs.kept(e["doc"]):
                    continue                  # no saved copy, or no meta.json (a shape error): skip
                scored = e.get("capture") if isinstance(e.get("capture"), str) else None
                if scored and scored != docs.current_capture(e["doc"]):
                    mine.add("document_changed")      # the saved copy is not the text this was scored from
                if docs.found(e["doc"], e.get("quote") or "", scored) is False:
                    mine.add("quote_not_found")   # (None: the capture it was scored from is gone, nothing to match)

    # group_differs: same first-group-label value, same question, a cited document in common,
    # different values. Every such cell is flagged, whichever row was scored first.
    members: dict[tuple, list] = {}
    for row, row_cells in cells.items():
        group = _group_value(units.get(row), group_label)
        if group is None:
            continue
        for qid, c in row_cells.items():
            cited = set(_cited(c))
            if c.get("state") == "answered" and cited:
                members.setdefault((group, qid), []).append((row, cited, c.get("value")))
    for (_, qid), group_members in members.items():
        for i, (row_a, docs_a, value_a) in enumerate(group_members):
            for row_b, docs_b, value_b in group_members[i + 1:]:
                if docs_a & docs_b and (not same(qid, value_a, value_b) if answers.is_number(qmap.get(qid))
                                        else value_a != value_b):
                    flags[row_a][qid].add("group_differs")
                    flags[row_b][qid].add("group_differs")

    _holds_back(project, cells, flags, units, qmap)

    out = {}
    for row, row_cells in cells.items():
        old = project.checks(row)
        old_cells = old.get("cells") if isinstance(old.get("cells"), dict) else {}
        written = {}
        for qid in _sorted_ids(row_cells, order):
            previous = old_cells.get(qid)
            second = previous.get("second_read") if isinstance(previous, dict) else None
            second = second if isinstance(second, dict) else None
            if second is not None:
                current = row_cells[qid].get("value")
                if "first" in second and not _same_value(second["first"], current):
                    second = None             # the cell was scored again: this read is of an old answer
                else:
                    second = {**second, "agrees": same(qid, second.get("value"), current)}
            mine = flags[row][qid]
            if second is not None and second.get("agrees") is False:
                mine.add("reads_disagree")
            written[qid] = {"flags": [f for f in FLAGS if f in mine], "second_read": second}
        row_flags = old.get("row_flags") if isinstance(old.get("row_flags"), list) else []
        out[row] = {"row": row, "row_flags": row_flags, "cells": written}
    return out


def _holds_back(project: Project, cells: dict, flags: dict, units: dict, qmap: dict) -> None:
    """Flag holds_back on each answer below its top in a section the row's next band needs met in full,
    when the row's score already reaches that band (so only those answers keep it out)."""
    from .scoresheet import _full, row_totals
    sheet = project.read("scoresheet") or {}
    gated = [g for g in sheet.get("bands") or [] if isinstance(g, dict)
             and any(isinstance(b, dict) and b.get("requires_full") for b in g.get("bands") or [])]
    if not gated or sheet.get("computable") is False:
        return
    for row, row_cells in cells.items():
        totals = row_totals(project, units.get(row) or row, project.answers(row).get("cells") or {})
        if not totals or totals["partial"]:
            continue
        full = _full(totals["sections"])
        for group in gated:
            of = group.get("of")
            score = totals["overall"] if of == "overall" else (totals["sections"].get(of) or {}).get("percent")
            ordered = sorted((b for b in group.get("bands") or [] if isinstance(b, dict)),
                             key=lambda b: b.get("min", 0), reverse=True)
            names = [b.get("name") for b in ordered]
            current = (totals.get("bands") or {}).get(of)
            if current not in names or names.index(current) == 0 or score is None:
                continue
            nxt = ordered[names.index(current) - 1]
            if not isinstance(nxt.get("min"), (int, float)) or nxt["min"] > score:
                continue
            missing = set(nxt.get("requires_full") or []) - full
            for qid, c in row_cells.items():
                q = qmap.get(qid)
                if q is not None and q.get("section") in missing and (c.get("points") or 0) < answers.top_points(q):
                    flags[row][qid].add("holds_back")


def run_checks(project: Project) -> dict:
    """Recompute and write `checks/<row>.json` for every scored row, and mark every cell flagged
    `document_changed` out of date in `answers/`, so `hb todo` lists it for a rescore from the
    current copy. Returns `{"rows_expected", "rows", "cells", "flags": {flag: cells with it},
    "flagged": [{"row", "question", "flags"}], "second_reads_waiting"}`."""
    computed = compute_checks(project)
    counts = dict.fromkeys(FLAGS, 0)
    flagged, cell_count = [], 0
    for row, data in computed.items():
        project.write_checks(row, data)
        changed = [qid for qid, c in data["cells"].items() if "document_changed" in c["flags"]]
        if changed:
            answers = project.answers(row)
            for qid in changed:
                answers["cells"][qid]["out_of_date"] = True
            project.write_answers(row, answers)
        for qid, c in data["cells"].items():
            cell_count += 1
            for f in c["flags"]:
                counts[f] += 1
            if c["flags"]:
                flagged.append({"row": row, "question": qid, "flags": c["flags"]})
    waiting = sum(len(g["rows"]) for g in second_reads(project))
    return {"rows_expected": len(project.read("units").get("units", [])), "rows": len(computed),
            "cells": cell_count, "flags": counts, "flagged": flagged, "second_reads_waiting": waiting}


# --- second reads ------------------------------------------------------------------------------

def second_reads(project: Project) -> list[dict]:
    """The cells that need a second read: a trigger flag and no recorded second read, grouped by
    question and group value so one second reader sees the group's rows side by side. Reads the
    stored `checks/` files, so run `hb check` first. Shape:
    `[{"question", "group": "label=value", "rows": [row, ...]}]`."""
    proj = project.read("project")
    group_label = (proj.get("group_labels") or [None])[0]
    units = {u.get("id"): u for u in project.read("units").get("units", []) if isinstance(u, dict)}
    order = _question_order(project)
    waiting: dict[tuple, list] = {}
    first: set = set()
    for row in _order(project, _stems(project.path / "checks")):
        for qid, c in (project.checks(row).get("cells") or {}).items():
            if not isinstance(c, dict) or c.get("second_read"):
                continue
            if SECOND_READ_TRIGGERS & set(c.get("flags") or []):
                group = _group_value(units.get(row), group_label)
                label = f"{group_label}={group}" if group is not None else ""
                waiting.setdefault((qid, label), []).append(row)
                if FIRST_READS & set(c.get("flags") or []):
                    first.add((qid, label))
    keys = sorted(waiting, key=lambda k: (k not in first, order.get(k[0], len(order)), str(k[0]), k[1]))
    return [{"question": q, "group": g, "rows": waiting[(q, g)]} for q, g in keys]


def _level_values(question: dict) -> list:
    return [lv.get("value") for lv in (question.get("answer") or {}).get("levels") or []
            if isinstance(lv, dict)]


def record_second_read(project: Project, row: str, run: str, data) -> list[dict]:
    """Store a second reader's answers file for one row. Each cell becomes
    `second_read: {"value", "agrees", "run", "first"}` in `checks/<row>.json`, `agrees` meaning the
    value equals the stored answer's value (`first`, kept so a later re-score is noticed: `hb check`
    drops a second read whose `first` is no longer the answer), and `reads_disagree` is set or
    cleared on that cell. Values match by type as well as by equality (3.0 is not 3). The
    file has the scorer's answers shape (`{"row", "cells": {qid: {"state", "value", ...}}}`). Any
    problem raises HandbookError and nothing is stored. Returns `[{"question", "value",
    "agrees"}]`."""
    if not isinstance(data, dict) or not isinstance(data.get("cells"), dict) or not data["cells"]:
        raise HandbookError('The second-read file must be an object with a "cells" object that has '
                            "at least one answer in it.")
    if data.get("row") not in (None, row):
        raise HandbookError(f"The second-read file is for {data.get('row')!r}, not {row!r}.")
    questions = {q["id"]: q for q in project.questions_for(row)}
    stored = project.answers(row).get("cells") or {}
    problems, results = [], []
    for qid, c in data["cells"].items():
        q = questions.get(qid)
        if q is None:
            problems.append(f"question {qid!r} is not one of the questions that apply to {row}")
            continue
        if not isinstance(c, dict) or c.get("state") not in ("answered", "no_evidence", "not_applicable"):
            problems.append(f"question {qid}: state must be answered, no_evidence or not_applicable")
            continue
        state, value = c["state"], c.get("value")
        if state == "not_applicable":
            value = None
        elif state == "no_evidence":
            value = q.get("no_evidence_value") if value is None else value
            if not _same_value(value, q.get("no_evidence_value")):
                problems.append(f"question {qid}: no_evidence takes the value {q.get('no_evidence_value')!r}")
                continue
        elif not (any(_same_value(value, v) for v in _level_values(q)) or answers.figure_ok(q, value)):
            problems.append(f"question {qid}: {value!r} is not one of its answers ({answers.describe(q)})")
            continue
        mine = stored.get(qid)
        if not isinstance(mine, dict) or mine.get("state") not in SCORED_STATES:
            problems.append(f"question {qid}: {row} has no answer for it, so there is nothing to compare")
            continue
        agrees = (answers.same_answer(q, value, mine.get("value")) if answers.is_number(q)
                  else _same_value(value, mine.get("value")))
        results.append({"question": qid, "value": value, "agrees": agrees,
                        "first": mine.get("value")})
    if problems:
        raise HandbookError(f"Nothing was stored. The second-read file for {row} has "
                            f"{len(problems)} problem{'s' if len(problems) != 1 else ''}:\n  "
                            + "\n  ".join(problems))
    checks = project.checks(row)
    cells = checks.get("cells") if isinstance(checks.get("cells"), dict) else {}
    for r in results:
        entry = cells.get(r["question"])
        entry = entry if isinstance(entry, dict) else {"flags": [], "second_read": None}
        flags = {f for f in entry.get("flags") or [] if f != "reads_disagree"}
        if not r["agrees"]:
            flags.add("reads_disagree")
        entry["flags"] = [f for f in FLAGS if f in flags]
        entry["second_read"] = {"value": r["value"], "agrees": r["agrees"], "run": run,
                                "first": r["first"]}
        cells[r["question"]] = entry
    project.write_checks(row, {"row": row,
                               "row_flags": checks.get("row_flags") if isinstance(checks.get("row_flags"), list) else [],
                               "cells": cells})
    return [{k: r[k] for k in ("question", "value", "agrees")} for r in results]


# --- second searches ---------------------------------------------------------------------------

def second_finds(project: Project) -> list[dict]:
    """The rows that still need their one second search: `found` has `expected_not_found` entries,
    or an answer has `unsure.why == "document"` and is not handled, and `second_search` is null.
    Shape: `[{"row", "why": ["expected_not_found", "unsure_document", "after_cutoff"], "questions": [qid, ...]}]`
    (`questions` are the cells unsure about a document or citing one dated after the cut-off)."""
    order = _question_order(project)
    rows = _order(project, {u.get("id") for u in project.read("units").get("units", [])
                            if isinstance(u, dict) and isinstance(u.get("id"), str)
                            and ID_RE.fullmatch(u["id"])}
                  | _stems(project.path / "answers") | _stems(project.path / "found"))
    out = []
    for row in rows:
        if project.found(row).get("second_search") is not None:
            continue
        why = []
        if project.found(row).get("expected_not_found"):
            why.append("expected_not_found")
        doubts = [qid for qid, c in (project.answers(row).get("cells") or {}).items()
                  if isinstance(c, dict) and isinstance(c.get("unsure"), dict)
                  and c["unsure"].get("why") == "document" and not c["unsure"].get("handled")]
        if doubts:
            why.append("unsure_document")
        # An answer resting on a document dated after the cut-off needs the version current at the
        # cut-off (the fish scorecard, 14 Oct 2026: such cells never prompted a second search).
        late = [qid for qid, c in (project.checks(row).get("cells") or {}).items()
                if isinstance(c, dict) and "after_cutoff" in (c.get("flags") or [])]
        if late:
            why.append("after_cutoff")
        if why:
            out.append({"row": row, "why": why, "questions": _sorted_ids(list(dict.fromkeys(doubts + late)), order)})
    return out

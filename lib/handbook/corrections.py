"""The owner's corrections: "here is the evidence" and/or "the answer should be X" on one cell.

The viewer's Correct this saves one document per round to the `corrections` collection (shapes:
`check_corrections`); the host exports them as `{"corrections": [...]}` and `apply` handles each
id once, recording every round in `corrections.json`:

1. With evidence, the code checks the quote word for word (quotecheck, the test the scorer's own
   check uses) against the source: a document already saved for the row, at the page given or
   elsewhere in it; a link, fetched and filed for the row first; or an upload the host saved
   locally (`evidence.local`), filed the same way. A new document is read (`prepare`) at once, so
   it is saved and captured before anyone quotes it.
2. The source must count: the company's own (not weak, not on another organisation's site) and
   dated no later than the cut-off.
3. A round that passes records what Claude must re-read (`reread`); one that fails gets Claude's
   verdict straight away, from the code (`verdict.by` "code"), and needs no re-read.

The owner's word is final: an answer the owner gave goes into the cell now (`owner` on the cell;
see `ingest.owner_cell`), whatever Claude later finds. `record` stores the blind re-read (a kit for
that row and question, scored with `hb score`) as Claude's verdict: agree, or disagree with a
reason. A round with evidence and no answer takes Claude's re-read as the answer, with the owner's
quote added. `keep: true` keeps an answer Claude disagreed with. A newer round on the same cell
supersedes an older one still waiting for its re-read, so that one is never re-read.

Nothing is written for a correction that is skipped (malformed, an unknown row or question, an
upload not fetched yet): its id is not recorded, so it is tried again next time.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone
from pathlib import Path

from . import answers as answer_kinds, fetch
from .checks import _after_cutoff
from .cmd_add_document import add_file
from .cmd_fetch import save_url
from .folder import HandbookError, Project
from .ingest import (_is_english, _problem_list, _same, _stored_cell, kit_captures, merge_owner,
                     owner_cell, validate_answers)
from .kit import rules_for, unit
from .prepare import prepare_document
from .quotecheck import is_grounded, longest_run, normalise, source_join
from .shapes import check_answers, check_corrections, is_date

PUBLIC_EVIDENCE = ("quote", "doc", "page", "link", "file")      # what the viewer sees of the evidence


def _stamp(now: datetime) -> str:
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _title(meta: dict, doc: str) -> str:
    return f"‘{meta.get('title')}’" if meta.get("title") else doc


def _level(question: dict, value):
    for lv in (question.get("answer") or {}).get("levels") or []:
        if _same(lv.get("value"), value):
            return lv
    return None


# --- the checks the code makes -------------------------------------------------------------------

def find_quote(pages: list[dict], quote: str, page: int | None = None) -> int | None:
    """The page the quote is word for word on: `page` when it is there, else the page of the
    document holding the longest run of its words, when the document as a whole holds it. None
    when it is not word for word in the document."""
    numbered = [(p.get("page") if isinstance(p.get("page"), int) else i, p.get("text") or "")
                for i, p in enumerate(pages, start=1) if isinstance(p, dict)]
    q = normalise(quote)
    for n, text in numbered:
        if n == page and is_grounded(q, source_join([text])):
            return n
    if not is_grounded(q, source_join(t for _, t in numbered)):
        return None
    return max(numbered, key=lambda nt: longest_run(q, source_join([nt[1]])))[0]


def not_admissible(meta: dict, cutoff: str | None) -> str | None:
    """Why the document cannot count as evidence, or None when it can."""
    if _after_cutoff(meta, cutoff):
        return (f"{_title(meta, meta.get('id'))} is dated {meta.get('date')}, after the cut-off "
                f"({cutoff}), so it cannot count for this edition.")
    why = fetch.weak_why(meta)
    if why is None and meta.get("host_type") == "third_party":
        why = fetch.AUTO_WEAK_WHY
    if why:
        return f"{_title(meta, meta.get('id'))} is not the company's own words ({why[0].lower() + why[1:]})."
    return None


def evidence_item(meta: dict, doc: str, page: int, quote: str) -> dict:
    """The owner's quote as an evidence item, bound to the document's current capture."""
    language = meta.get("language")
    return {"doc": doc, "page": page, "quote": quote,
            "quote_en": quote if _is_english(language) else None,
            "language": language, "capture": meta.get("capture")}


class _Skip(Exception):
    """A correction that cannot be handled now; nothing about it is recorded."""


def _source(project: Project, c: dict, row: str, notes: list) -> tuple[str | None, str | None]:
    """(doc, None) for the correction's document, filed for the row and read; (None, why) when the
    link did not load or the document cannot be read. Raises _Skip for a document not saved for
    the row or an upload the host has not fetched."""
    ev = c.get("evidence") or {}
    if ev.get("doc"):
        doc = ev["doc"]
        if doc not in (project.found(row).get("documents") or []):
            raise _Skip(f"Document {doc} is not saved for {row}.")
    elif ev.get("link"):
        try:
            doc = save_url(project, ev["link"], [row], "provided", say=notes.append)
        except HandbookError as e:
            return None, f"Claude could not open the link. {e}"
    else:
        local = ev.get("local")
        if not local:
            raise _Skip("The uploaded file has not been fetched yet: read it with the Artifact tool "
                        "(path = the asset id) and put the saved file's path in evidence.local.")
        try:
            doc = add_file(project, Path(local).expanduser(), [row],
                           fetch.title_from_name(ev.get("file") or "") or None, say=notes.append)
        except HandbookError as e:
            return None, f"The uploaded file could not be filed. {e}"
    meta = project.documents().get(doc) or {}
    if not meta.get("kept") or not project.pages(doc):
        try:
            prepare_document(project, doc)
        except HandbookError as e:
            return None, f"The document could not be read, so the quote could not be checked. {e}"
    if not project.pages(doc):
        return None, f"{_title(project.documents().get(doc) or {}, doc)} has no saved text, so the quote could not be checked."
    return doc, None


# --- applying a file of corrections ------------------------------------------------------------

def _handle(project: Project, c: dict, stamp: str, rounds: list) -> dict:
    """Check one correction and record it; returns its round. Raises _Skip."""
    row, qid = c["row"], c["qid"]
    try:
        the_unit = unit(project, row)
    except HandbookError as e:
        raise _Skip(str(e)) from None
    question = {q["id"]: q for q in project.questions_for(the_unit)}.get(qid)
    if question is None:
        raise _Skip(f"Question {qid} does not apply to {row}, or is not in questions.json.")
    value = c.get("value")
    if value is not None and _level(question, value) is None and not answer_kinds.figure_ok(question, value):
        raise _Skip(f"Question {qid} has no level {value!r} (its levels are {answer_kinds.describe(question)}).")
    answers = project.answers(row)
    cells = answers.setdefault("cells", {})
    current = cells.get(qid) if isinstance(cells.get(qid), dict) else None
    entry = {**copy.deepcopy(c), "handled": stamp, "found": None, "reread": None, "verdict": None}

    if c.get("keep"):
        owner = (current or {}).get("owner")
        if not (isinstance(owner, dict) and owner.get("agreed") is False):
            raise _Skip(f"Nothing to keep on {row} {qid}: Claude has not disagreed with an answer of yours there.")
        owner["kept"] = True
        project.write_answers(row, answers)
        return {**entry, "status": "kept"}

    notes, why, item = [], None, None
    ev = c.get("evidence") or {}
    if ev.get("quote"):
        doc, why = _source(project, c, row, notes)
        if doc:
            meta = project.documents()[doc]
            page = find_quote(project.pages(doc), ev["quote"], ev.get("page"))
            if page is None:
                where = f"page {ev['page']} of " if ev.get("page") else ""
                why = f"The quote is not word for word on {where}{_title(meta, doc)}, nor anywhere else in it."
            else:
                entry["found"] = {"doc": doc, "page": page}
                item = evidence_item(meta, doc, page, ev["quote"])
                cutoff = project.read("project").get("cutoff")
                why = not_admissible(meta, cutoff if is_date(cutoff) else None)
    if why:
        entry["status"] = "checked"
        entry["verdict"] = {"agree": False, "why": why, "value": None, "by": "code", "run": None, "at": stamp}
    else:
        entry["status"] = "to_check"
        found = entry["found"] or {}
        entry["reread"] = {"row": row, "question": qid, "doc": found.get("doc"), "page": found.get("page")}
    if notes:
        entry["notes"] = notes
    for r in rounds:                     # an older round on this cell is never re-read now
        if r.get("row") == row and r.get("qid") == qid and r.get("status") == "to_check":
            r["status"] = "superseded"
    if value is not None:
        owner = {"value": value, "correction": c["id"], "agreed": False if why else None,
                 "why": why, "claude": None, "kept": False}
        if c.get("by"):
            owner.update({"by": c.get("by"), "at": c.get("at")})
        evidence = [item] if item else (list((current or {}).get("evidence") or [])
                                        if current and _same(current.get("value"), value) else [])
        cell = owner_cell(question, current, value, evidence, owner)
        cell.pop("checked", None)        # a changed answer has not been checked yet
        cell.pop("rescored", None)
        problems = check_answers({"row": row, "cells": {qid: cell}}, project.read("questions"))
        if problems:
            raise _Skip("The answer could not be stored: " + "; ".join(problems))
        cells[qid] = cell
        answers["row"] = row
        project.write_answers(row, answers)
    return entry


def set_owner_verdict(project: Project, entry: dict) -> None:
    """Put a round's verdict on the owner's answer in its cell (`owner.agreed`, `why`, `claude`)."""
    answers = project.answers(entry["row"])
    cell = (answers.get("cells") or {}).get(entry["qid"])
    owner = cell.get("owner") if isinstance(cell, dict) else None
    if not isinstance(owner, dict) or owner.get("correction") != entry.get("id"):
        return
    v = entry["verdict"]
    owner.update({"agreed": v["agree"], "why": v.get("why"), "claude": v.get("value")})
    project.write_answers(entry["row"], answers)


def _take_page_check(project: Project, entry: dict, page: dict | None, stamp: str) -> bool:
    """Where the page's own check agreed or disagreed with an answer the owner gave, it stands as
    Claude's verdict on a round waiting for its re-read. True when it was taken."""
    v = page.get("verdict") if page else None
    if entry.get("status") == "to_check" and v and entry.get("value") is not None and isinstance(v.get("agree"), bool):
        entry.update({"status": "checked", "reread": None,
                      "verdict": {"agree": v["agree"], "why": v.get("why"), "value": v.get("value"), "by": "page",
                                  "run": None, "at": page.get("at") or stamp}})
        set_owner_verdict(project, entry)
        return True
    return False


def apply(project: Project, payload, now: datetime) -> dict:
    """Apply the owner's corrections (`{"corrections": [...]}`), in order of `at` (none: `now`).
    Ids already in corrections.json are passed over. Returns {"applied": [rounds], "skipped":
    [{"id", "why"}], "already": n}; each applied round has `status` "to_check" (with `reread`),
    "checked" (the code's verdict) or "kept"."""
    if not isinstance(payload, dict) or not isinstance(payload.get("corrections"), list):
        raise HandbookError('The corrections file must hold {"corrections": [...]}, the documents of '
                            "the viewer's corrections collection.")
    stamp = _stamp(now)
    # A doc with kind "check" is the page's own check of a round (views/cell.js): not a round itself.
    # Where it agreed with an answer the owner gave, it stands as Claude's verdict, so that round
    # needs no re-read here (an evidence-only round is still re-read, so its answer is recorded).
    page_checks = {}
    for c in payload["corrections"]:
        if isinstance(c, dict) and c.get("kind") == "check" and isinstance(c.get("verdict"), dict):
            had = page_checks.get(c.get("of"))
            if not had or str(c.get("at")) > str(had.get("at")):
                page_checks[c.get("of")] = c
    items = [copy.deepcopy(c) for c in payload["corrections"] if not (isinstance(c, dict) and c.get("kind") == "check")]
    for c in items:
        if isinstance(c, dict) and c.get("at") is None:
            c["at"] = stamp
    items.sort(key=lambda c: str(c.get("at")) if isinstance(c, dict) else "")
    record = project.read("corrections")
    rounds = record.setdefault("corrections", [])
    done = {r.get("id") for r in rounds}
    applied, skipped, already = [], [], 0
    for c in items:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid in done:
            # The page's check can arrive after the round was handled (hb serve applies each save at
            # once): it still stands as Claude's verdict on a round waiting for its re-read.
            entry = next((r for r in rounds if r.get("id") == cid), None)
            if entry is not None and _take_page_check(project, entry, page_checks.get(cid), stamp):
                applied.append(entry)
                project.write("corrections", record)
            else:
                already += 1
            continue
        problems = check_corrections({"corrections": [c]})
        if problems:
            skipped.append({"id": cid, "why": problems[0]})
            continue
        try:
            entry = _handle(project, c, stamp, rounds)
        except _Skip as e:
            skipped.append({"id": cid, "why": str(e)})
            continue
        _take_page_check(project, entry, page_checks.get(cid), stamp)
        rounds.append(entry)
        done.add(cid)
        applied.append(entry)
        project.write("corrections", record)       # each round as it is handled: its cell is written
    return {"applied": applied, "skipped": skipped, "already": already}


# --- Claude's verdict from the re-read ---------------------------------------------------------

def waiting(project: Project) -> list[dict]:
    """The rounds waiting for Claude's re-read, oldest first."""
    return [r for r in project.read("corrections").get("corrections", []) if r.get("status") == "to_check"]


def record(project: Project, cid: str, run: str, data, captures, now: datetime,
           why: str | None = None) -> dict:
    """Store the blind re-read of correction `cid` (the scorer's answers file `data`, from a kit for
    that row and question, with the kit's `captures`) as Claude's verdict, and return the round.
    With the owner's answer the cell keeps it and `owner.agreed` says whether Claude agrees (`why`,
    else the scorer's note, says why not); with evidence only the re-read is the answer, with the
    owner's quote added. Raises HandbookError, storing nothing, when the round is not waiting or the
    answer has a problem."""
    rec = project.read("corrections")
    entry = next((r for r in rec.get("corrections", []) if r.get("id") == cid), None)
    if entry is None:
        raise HandbookError(f"There is no correction {cid!r} in corrections.json.")
    if entry.get("status") != "to_check":
        raise HandbookError(f"Correction {cid} is not waiting for a re-read (it is {entry.get('status')}).")
    if not project.run(run):
        raise HandbookError(f"There is no run {run!r} in runs/. Start one with: run start --kind rerun ...")
    row, qid = entry["row"], entry["qid"]
    cell = (data.get("cells") or {}).get(qid) if isinstance(data, dict) else None
    if cell is None:
        raise HandbookError(f"The answers file has no answer for question {qid}.")
    problems = validate_answers(project, row, {"row": data.get("row"), "cells": {qid: cell}}, captures)
    if problems:
        raise _problem_list(f"Nothing was stored for {row} {qid}.", problems)
    question = {q["id"]: q for q in project.questions_for(unit(project, row))}[qid]
    rules = project.read("rules").get("rules", [])
    claude = _stored_cell(cell, question, rules, row, run, kit_captures(captures))
    answers = project.answers(row)
    cells = answers.setdefault("cells", {})
    current = cells.get(qid) if isinstance(cells.get(qid), dict) else None
    found = entry.get("found")
    if entry.get("value") is None:
        new = claude
        if found and claude["state"] == "answered" and not any(
                e.get("doc") == found["doc"] and e.get("page") == found["page"] for e in claude["evidence"]):
            meta = project.documents().get(found["doc"]) or {}
            new["evidence"] = [evidence_item(meta, found["doc"], found["page"], entry["evidence"]["quote"])] + new["evidence"]
        verdict = {"agree": None, "why": claude.get("note") or None}
    else:
        owner = (current or {}).get("owner")
        if not (isinstance(owner, dict) and owner.get("correction") == cid):
            raise HandbookError(f"The answer for {row} {qid} is no longer the one correction {cid} set, "
                                "so there is nothing to give a verdict on.")
        new = merge_owner(question, current, claude, why)
        verdict = {"agree": new["owner"]["agreed"], "why": new["owner"]["why"]}
    problems = check_answers({"row": row, "cells": {qid: new}}, project.read("questions"))
    if problems:
        raise _problem_list(f"Nothing was stored for {row} {qid}: the stored cell would have", problems)
    cells[qid] = new
    answers["row"] = row
    project.write_answers(row, answers)
    entry["status"] = "checked"
    entry["verdict"] = {**verdict, "value": claude.get("value"), "by": "claude", "run": run, "at": _stamp(now)}
    project.write("corrections", rec)
    return entry


# --- what the viewer sees ----------------------------------------------------------------------

def public(project: Project) -> list[dict]:
    """Every round, as the viewer shows it: no local paths."""
    out = []
    for r in project.read("corrections").get("corrections", []):
        ev = r.get("evidence") or {}
        verdict = r.get("verdict") or None
        out.append({"id": r.get("id"), "row": r.get("row"), "qid": r.get("qid"), "value": r.get("value"),
                    "reason": r.get("reason"), "keep": bool(r.get("keep")),
                    "evidence": {k: ev.get(k) for k in PUBLIC_EVIDENCE}, "by": r.get("by"), "at": r.get("at"),
                    "status": r.get("status"), "found": r.get("found"),
                    "verdict": None if verdict is None else {k: verdict.get(k) for k in ("agree", "why", "value", "by")}})
    return out

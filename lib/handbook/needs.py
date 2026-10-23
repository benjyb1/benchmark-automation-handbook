"""Data needs the owner supplies, and the answers that wait for them.

A need in needs.json with `only_source: true` names, in `why`, questions whose evidence only the owner
holds: their lab results, complaints figures or survey answers, what the organisations return to them.
No document anywhere else can answer those questions, so:

- no finder searches the web for them (`owner_only`), whatever the need's status;
- while the need is still `needed`, their answers wait for the owner's data (`waiting_for`): no
  scorer is started for them, the page shows them as waiting for that need, and the totals that
  include them are incomplete rather than counting them as no evidence. A row stops waiting once one
  of the need's documents is filed for it; the whole need, once it is `supplied` or `waived`.

An answer a scorer found in a document never waits. Built 21 Oct 2026 for Consumer Reports (its lab
results), Citizens Advice (its complaints figures and the suppliers' returns) and RSF (its tally of
abuses): the drafts left those questions out, or the trial spent most of its tokens searching for them.
"""
from __future__ import annotations

from .folder import Project, applies

_READ = object()
ALMOST_NOTHING = 0.25      # a trial or run that found fewer answers than this share says so plainly (serve.py)


def only_source_needs(project: Project) -> list[dict]:
    """The needs marked only_source, in needs.json order."""
    return [n for n in project.read("needs").get("needs") or []
            if isinstance(n, dict) and n.get("only_source") is True]


def _covers(need: dict, row: str, qid: str) -> bool:
    rows = need.get("rows")
    return qid in (need.get("why") or []) and (rows is None or row in (rows or []))


def owner_only(project: Project, row: str, listed: list[dict] | None = None) -> set[str]:
    """The ids of the questions only the owner can answer for `row` (any need status): never searched for."""
    listed = only_source_needs(project) if listed is None else listed
    return {q for n in listed for q in (n.get("why") or []) if _covers(n, row, q)}


def waiting_for(project: Project, row: str, qid: str, cell=_READ, listed: list[dict] | None = None,
                row_docs: set | None = None) -> str | None:
    """The id of the need `row`'s answer to `qid` waits for, or None. `cell` is the stored cell (read from
    answers/ when not given); one answered from a document never waits."""
    listed = only_source_needs(project) if listed is None else listed
    if not listed:
        return None
    if cell is _READ:
        cell = (project.answers(row).get("cells") or {}).get(qid)
    if isinstance(cell, dict) and cell.get("state") in ("answered", "not_applicable"):
        return None
    for n in listed:
        if n.get("status") != "needed" or not _covers(n, row, qid):
            continue
        docs = set(project.found(row).get("documents") or []) if row_docs is None else row_docs
        if docs & set(n.get("documents") or []):
            continue                                  # the owner's data for it is filed for this row
        return n.get("id")
    return None


def waiting_in_row(project: Project, row: str, cells: dict | None, listed: list[dict] | None = None) -> dict[str, str]:
    """{question id: need id} for `row`'s answers that wait for the owner's data, given its cells
    ({question id: cell}). Whether a question applies to the row is the caller's to check."""
    listed = only_source_needs(project) if listed is None else listed
    if not any(n.get("status") == "needed" for n in listed):
        return {}
    docs = set(project.found(row).get("documents") or [])
    out = {}
    for qid in dict.fromkeys(q for n in listed for q in (n.get("why") or []) if _covers(n, row, q)):
        need = waiting_for(project, row, qid, (cells or {}).get(qid), listed, docs)
        if need:
            out[qid] = need
    return out


def waiting(project: Project) -> dict[str, dict[str, str]]:
    """{row: {question id: need id}} for every answer that waits for the owner's data (rows and
    questions in units.json and questions.json order; rows with nothing waiting left out)."""
    listed = only_source_needs(project)
    if not any(n.get("status") == "needed" for n in listed):
        return {}
    questions = project.read("questions").get("questions") or []
    out = {}
    for unit in project.read("units").get("units") or []:
        row = unit.get("id")
        held = waiting_in_row(project, row, project.answers(row).get("cells") or {}, listed)
        held = {q.get("id"): held[q.get("id")] for q in questions if q.get("id") in held and applies(q, unit)}
        if held:
            out[row] = held
    return out


def outcome(project: Project, rows: list[str], qids: list[str] | None = None) -> dict:
    """What the answers on `rows` came to, over the questions `qids` (or every question that applies):
    {"answered", "no_evidence", "not_run": counts, "waiting": {need id: answers}, "open_needs": [ids],
    "no_evidence_for_needs": count}, `open_needs` being the needs still needed (only_source or not) whose
    questions came back with no evidence on those rows, in needs.json order, and `no_evidence_for_needs`
    how many of the answers with no evidence those needs are for, the others resting on no need (gh5050,
    21 Oct 2026). For the note after a trial or a run (serve.py)."""
    units = {u.get("id"): u for u in project.read("units").get("units") or [] if isinstance(u, dict)}
    questions = [q for q in project.read("questions").get("questions") or [] if isinstance(q, dict)]
    every = [n for n in project.read("needs").get("needs") or [] if isinstance(n, dict)]
    listed = [n for n in every if n.get("only_source") is True]
    out = {"answered": 0, "no_evidence": 0, "not_run": 0, "waiting": {}, "open_needs": [], "no_evidence_for_needs": 0}
    open_ids = set()
    for row in dict.fromkeys(rows):
        unit = units.get(row)
        if unit is None:
            continue
        cells = project.answers(row).get("cells") or {}
        held = waiting_in_row(project, row, cells, listed) if listed else {}
        for q in questions:
            qid = q.get("id")
            if not applies(q, unit) or (qids and qid not in qids):
                continue
            if qid in held:
                out["waiting"][held[qid]] = out["waiting"].get(held[qid], 0) + 1
                continue
            state = (cells.get(qid) or {}).get("state", "not_run")
            if state in ("answered", "no_evidence"):
                out[state] += 1
                if state == "no_evidence":
                    for_needs = {n.get("id") for n in every if n.get("status") == "needed" and _covers(n, row, qid)}
                    open_ids |= for_needs
                    out["no_evidence_for_needs"] += 1 if for_needs else 0
            elif state != "not_applicable":
                out["not_run"] += 1
    out["open_needs"] = [n.get("id") for n in every if n.get("id") in open_ids]
    return out

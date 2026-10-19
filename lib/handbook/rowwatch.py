"""Rows that need the owner's eye because of how their scoring went: stopped at the token budget, or
much slower than similar rows. Neither holds up other rows; each waits on the page for the owner.

    row_source(project, row) -> "owner" | "public"
    watch(project) -> {row: {"source", "stopped"?, "slow"?}}    # the bundle's `watch`
    similar_budget(project, row, n_questions) -> int | None       # hb kit's budget once rows are measured

Where a row's documents come from decides how a missing document is handled:
- "owner": the owner supplied them (every document is `provided_by_owner`). A document a scorer needs
  and does not have can only come from the owner, so the page asks for it at once (ingest.record_missing).
- "public": Claude found them on the web. A document a scorer names goes to the row's one second
  search first, and the owner is asked only when that fails. The timer is the general check: a row
  whose scoring took more than SLOW_FACTOR times the tokens per question of similar rows (its group,
  else the whole list) is flagged, since that usually means Claude kept looking for something that
  is not there. The owner uploads a document, or says carry on.

A row's own figure is its largest scored kit with a receipt (most questions): a one-question re-run
pays the whole preamble for one answer and would look slow. Rows stopped at their budget are left out
of the comparison: they have their own card.
"""
from __future__ import annotations
from statistics import median

from .folder import Project

SLOW_WHAT = "A document that may be missing"   # what a slow row's Upload sends (views/runbar.js), so serve knows
SLOW_FACTOR = 1.5          # Benjy, 19 Oct 2026: flag a row taking more than 50% longer than similar rows
MIN_SIMILAR = 3            # rows needed to compare with
BUDGET_FACTOR = 2.5        # a kit's budget against similar rows' tokens per question


def _units(project: Project) -> dict:
    return {u["id"]: u for u in project.read("units").get("units", [])
            if isinstance(u, dict) and isinstance(u.get("id"), str)}


def row_source(project: Project, row: str, documents: dict | None = None) -> str:
    """"owner" when every document found for the row was supplied by the owner, else "public". A row
    with no documents yet follows the project: "owner" when the project has documents and all are
    the owner's."""
    documents = project.documents() if documents is None else documents
    docs = [d for d in project.found(row).get("documents") or [] if d in documents]
    pool = docs or list(documents)
    if pool and all((documents.get(d) or {}).get("provided_by_owner") is True for d in pool):
        return "owner"
    return "public"


def _representative(project: Project, units: dict) -> dict[str, dict]:
    """Row -> {"tokens", "questions", "per_question"} from its largest receipt-backed scoring kit."""
    out: dict[str, dict] = {}
    for run in project.runs():
        if not isinstance(run, dict):
            continue
        covered = run.get("questions")
        for row, steps in (run.get("receipts") or {}).items():
            r = (steps or {}).get("score") if isinstance(steps, dict) else None
            if not isinstance(r, dict) or row not in units:
                continue
            tokens = (r.get("fresh") or 0) + (r.get("cache_read") or 0) + (r.get("output") or 0)
            n = sum(1 for q in project.questions_for(units[row]) if covered is None or q.get("id") in covered)
            if not tokens or not n:
                continue
            if row not in out or n > out[row]["questions"]:
                out[row] = {"tokens": tokens, "questions": n, "per_question": tokens / n}
    return out


def _stopped_rows(project: Project) -> set[str]:
    return {row for run in project.runs() if isinstance(run, dict) for row in (run.get("stops") or {})}


def _similar(units: dict, row: str, pool: dict, group_by: str | None) -> tuple[list[float], str | None]:
    """Tokens per question of the rows like `row` in `pool` (itself left out): its group when the group
    has MIN_SIMILAR measured, else every other measured row. Returns (figures, the group's value or None)."""
    others = {r: v for r, v in pool.items() if r != row}
    if group_by:
        value = ((units.get(row) or {}).get("labels") or {}).get(group_by)
        same = [v["per_question"] for r, v in others.items()
                if value is not None and ((units.get(r) or {}).get("labels") or {}).get(group_by) == value]
        if len(same) >= MIN_SIMILAR:
            return same, str(value)
    return [v["per_question"] for v in others.values()], None


def _group_by(project: Project) -> str | None:
    g = project.read("project").get("group_by")
    return g if isinstance(g, str) and g else None


def slow_rows(project: Project) -> dict[str, dict]:
    """Public rows whose scoring took more than SLOW_FACTOR times similar rows' tokens per question:
    {row: {"times", "similar": the group's value or None, "compared": how many rows}}."""
    units = _units(project)
    documents = project.documents()
    stopped = _stopped_rows(project)
    pool = {r: v for r, v in _representative(project, units).items() if r not in stopped}
    group_by = _group_by(project)
    out = {}
    for row, mine in pool.items():
        if row_source(project, row, documents) != "public":
            continue
        figures, value = _similar(units, row, pool, group_by)
        if len(figures) < MIN_SIMILAR:
            continue
        times = mine["per_question"] / median(figures)
        if times > SLOW_FACTOR:
            out[row] = {"times": round(times, 1), "similar": value, "compared": len(figures)}
    return out


def waiting_stops(project: Project, control: dict | None = None) -> dict[str, dict]:
    """Rows whose scorer was stopped (runs.record_stop) with questions still not scored, that the owner
    has not set aside: {row: {"why", "tokens", "budget", "left": [still unscored], "at", "carrying_on"}}.
    `carrying_on` is true while a Carry on (a re-run of those questions) is queued."""
    from .control import read as read_control
    control = read_control(project) if control is None else control
    latest: dict[str, dict] = {}
    for run in project.runs():
        for row, entries in ((run or {}).get("stops") or {}).items():
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict) and str(e.get("at") or "") >= str((latest.get(row) or {}).get("at") or ""):
                    latest[row] = e
    out = {}
    for row, e in latest.items():
        cells = project.answers(row).get("cells") or {}
        left = [q for q in e.get("left") or []
                if (cells.get(q) or {}).get("state") not in ("answered", "no_evidence", "not_applicable")]
        if not left:
            continue
        aside = (control.get("rows_ok") or {}).get(row) or {}
        if str(aside.get("stopped") or "") >= str(e.get("at") or "￿"):
            continue
        queued = any(row in (r.get("rows") or []) and set(left) & set(r.get("questions") or [])
                     for r in control.get("reruns") or [])
        out[row] = {"why": e.get("why"), "tokens": e.get("tokens"), "budget": e.get("budget"), "left": left,
                    "at": e.get("at"), "carrying_on": queued}
    return out


def watch(project: Project) -> dict:
    """The bundle's `watch`: for every row, where its documents come from; for a row waiting on the
    owner, `stopped` (waiting_stops) or `slow` (slow_rows, unless the owner said carry on)."""
    from .control import read as read_control
    control = read_control(project)
    documents = project.documents()
    out = {row: {"source": row_source(project, row, documents)} for row in _units(project)}
    for row, s in waiting_stops(project, control).items():
        out.setdefault(row, {})["stopped"] = s
    fine = control.get("rows_ok") or {}
    for row, s in slow_rows(project).items():
        if not (fine.get(row) or {}).get("slow"):
            out.setdefault(row, {})["slow"] = s
    return out


def similar_budget(project: Project, row: str, n_questions: int) -> int | None:
    """BUDGET_FACTOR times the median tokens per question of rows like `row`, times `n_questions`;
    None until MIN_SIMILAR rows are measured (hb kit then uses the cost model)."""
    units = _units(project)
    pool = {r: v for r, v in _representative(project, units).items() if r not in _stopped_rows(project)}
    figures, _ = _similar(units, row, pool, _group_by(project))
    if len(figures) < MIN_SIMILAR:
        return None
    return round(BUDGET_FACTOR * median(figures) * max(n_questions, 1))


def slow_questions(project: Project, row: str) -> list[str]:
    """What to score again after the owner sends a document for a slow or stopped row: the questions not
    scored yet, and the answers unsure about a document or with no evidence; else every question."""
    unit = _units(project).get(row)
    if unit is None:
        return []
    ids = [q["id"] for q in project.questions_for(unit)]
    cells = project.answers(row).get("cells") or {}
    picked = [q for q in ids if (cells.get(q) or {}).get("state") in (None, "no_evidence", "not_run")
              or ((cells.get(q) or {}).get("unsure") or {}).get("why") == "document"]
    return picked or ids

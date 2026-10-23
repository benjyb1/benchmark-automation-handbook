"""What is still to score: per row, the applicable questions whose cell is missing, `not_run` or out
of date (`hb todo`). A cell counts as done exactly as `runs.cell_progress` counts it: `answered` or
`no_evidence` and not out of date, so `todo` is empty when `run finish` would say every cell is done.
Rows and questions outside the run control's scope (control.json) are not listed. `to_find` gives
the rows in scope whose documents have not been looked for yet (`hb todo --find`)."""
from __future__ import annotations

from . import control, needs
from .folder import Project, applies

DONE = ("answered", "no_evidence")


def todo(project: Project) -> dict:
    """{"rows": [{"row", "questions": [ids]}], "cells": n}: rows in units.json order, questions in
    questions.json order, rows with nothing left omitted. An answer that waits for the owner's data
    (needs.py) is not listed: it is counted under its need in `waiting` ({need id: cells}, only when
    something waits) instead."""
    questions = project.read("questions").get("questions", [])
    scope = control.read(project)["scope"]
    listed = needs.only_source_needs(project)
    rows, total, held = [], 0, {}
    for u in project.read("units").get("units", []):
        if scope["rows"] is not None and u["id"] not in scope["rows"]:
            continue
        cells = project.answers(u["id"]).get("cells") or {}
        docs = set(project.found(u["id"]).get("documents") or []) if listed else set()
        left = []
        for q in questions:
            if not applies(q, u) or (scope["questions"] is not None and q.get("id") not in scope["questions"]):
                continue
            cell = cells.get(q.get("id"))
            need = needs.waiting_for(project, u["id"], q.get("id"), cell, listed, docs) if listed else None
            if need:
                held[need] = held.get(need, 0) + 1
            elif not isinstance(cell, dict) or cell.get("state") not in DONE or cell.get("out_of_date") is True:
                left.append(q["id"])
        if left:
            rows.append({"row": u["id"], "questions": left})
            total += len(left)
    return {"rows": rows, "cells": total, **({"waiting": held} if held else {})}


def to_find(project: Project, wanted: list[str] | None = None) -> dict:
    """{"rows": [ids], "count": n}: the rows in scope, in units.json order, whose documents have not
    been looked for yet: no `found/<row>.json`, or one with no documents, nothing expected but not
    found, no second search and no find usage recorded for the row in any run. A row whose every
    question rests on data only the owner holds (needs.py) has nothing to search for.
    A row whose search named its questions (`searched_for` in found/, a trial or re-run on some questions)
    is listed again for the questions in scope (and in `wanted`, when given) it did not cover, under
    "questions" ({row: [ids]}, only when there are such rows)."""
    scope = control.read(project)["scope"]
    looked = {row for run in project.runs() if isinstance(run, dict) and isinstance(run.get("usage"), dict)
              for row, steps in run["usage"].items()
              if isinstance(steps, dict) and steps.get("find") is not None}
    listed = needs.only_source_needs(project)
    questions = project.read("questions").get("questions", [])
    rows, partial = [], {}
    for u in project.read("units").get("units", []):
        row = u["id"]
        if scope["rows"] is not None and row not in scope["rows"]:
            continue
        mine = [q.get("id") for q in questions if applies(q, u)
                and (scope["questions"] is None or q.get("id") in scope["questions"])
                and (wanted is None or q.get("id") in wanted)]
        if listed:
            owners = needs.owner_only(project, row, listed)       # worked out once per row
            mine = [q for q in mine if q not in owners]
        if not mine:
            continue                      # nothing in scope to search for (or it all rests on the owner's data)
        if (project.path / "found" / f"{row}.json").is_file():
            found = project.found(row)
            # a document a search for another row of its group filed here (hb fetch --group) is not this row's own
            own = [d for d in found.get("documents") or [] if d not in (found.get("shared") or [])]
            if own or found.get("expected_not_found") or found.get("second_search") or row in looked:
                covered = found.get("searched_for")
                left = [q for q in mine if q not in covered] if isinstance(covered, list) else []
                if not left:
                    continue
                partial[row] = left
        rows.append(row)
    return {"rows": rows, "count": len(rows), **({"questions": partial} if partial else {})}

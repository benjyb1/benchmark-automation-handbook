"""What is still to score: per row, the applicable questions whose cell is missing, `not_run` or out
of date (`hb todo`). A cell counts as done exactly as `runs.cell_progress` counts it: `answered` or
`no_evidence` and not out of date, so `todo` is empty when `run finish` would say every cell is done.
Rows and questions outside the run control's scope (control.json) are not listed. `to_find` gives
the rows in scope whose documents have not been looked for yet (`hb todo --find`)."""
from __future__ import annotations

from . import control
from .folder import Project, applies

DONE = ("answered", "no_evidence")


def todo(project: Project) -> dict:
    """{"rows": [{"row", "questions": [ids]}], "cells": n}: rows in units.json order, questions in
    questions.json order, rows with nothing left omitted."""
    questions = project.read("questions").get("questions", [])
    scope = control.read(project)["scope"]
    rows, total = [], 0
    for u in project.read("units").get("units", []):
        if scope["rows"] is not None and u["id"] not in scope["rows"]:
            continue
        cells = project.answers(u["id"]).get("cells") or {}
        left = []
        for q in questions:
            if not applies(q, u) or (scope["questions"] is not None and q.get("id") not in scope["questions"]):
                continue
            cell = cells.get(q.get("id"))
            if not isinstance(cell, dict) or cell.get("state") not in DONE or cell.get("out_of_date") is True:
                left.append(q["id"])
        if left:
            rows.append({"row": u["id"], "questions": left})
            total += len(left)
    return {"rows": rows, "cells": total}


def to_find(project: Project) -> dict:
    """{"rows": [ids], "count": n}: the rows in scope, in units.json order, whose documents have not
    been looked for yet: no `found/<row>.json`, or one with no documents, nothing expected but not
    found, no second search and no find usage recorded for the row in any run."""
    scope = control.read(project)["scope"]
    looked = {row for run in project.runs() if isinstance(run, dict) and isinstance(run.get("usage"), dict)
              for row, steps in run["usage"].items()
              if isinstance(steps, dict) and steps.get("find") is not None}
    rows = []
    for u in project.read("units").get("units", []):
        row = u["id"]
        if scope["rows"] is not None and row not in scope["rows"]:
            continue
        if (project.path / "found" / f"{row}.json").is_file():
            found = project.found(row)
            if found.get("documents") or found.get("expected_not_found") or found.get("second_search"):
                continue
            if row in looked:
                continue
        rows.append(row)
    return {"rows": rows, "count": len(rows)}

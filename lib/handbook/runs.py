"""Run records in `runs/<id>.json`: starting a run, recording token usage, finishing it.

Run ids are `YYYY-MM-DD-HHMM-<kind>` (UTC), with `-2`, `-3` and so on when that id is taken.
"""
from __future__ import annotations
from datetime import datetime, timezone

from .folder import HandbookError, Project, applies, now_stamp
from .kit import unit
from .shapes import RECEIPT_KEYS, RUN_KINDS, USAGE_STEPS

STAGE_FOR = {"trial": "trial", "full": "running", "rerun": "running"}


def new_run_id(project: Project, kind: str, when: datetime | None = None) -> str:
    """The next free run id for `kind` at `when` (default now, UTC)."""
    when = when or datetime.now(timezone.utc)
    base = f"{when.astimezone(timezone.utc):%Y-%m-%d-%H%M}-{kind}"
    run_id, n = base, 1
    while project.run(run_id) or (project.path / "runs" / f"{run_id}.json").exists():
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def _get_run(project: Project, run_id: str) -> dict:
    run = project.run(run_id)
    if not run:
        raise HandbookError(f"There is no run {run_id!r} in runs/.")
    return run


def start_run(project: Project, kind: str, questions=None, rows=None,
              when: datetime | None = None) -> dict:
    """Create `runs/<id>.json` and set the project stage (trial, or running for full and rerun).
    `questions` and `rows` are id lists or None (all). Returns the run record."""
    if kind not in RUN_KINDS:
        raise HandbookError(f"The run kind must be trial, full or rerun, not {kind!r}.")
    known = {q.get("id") for q in project.read("questions").get("questions", [])}
    unknown = [q for q in questions or [] if q not in known]
    if unknown:
        raise HandbookError(f"Question {', '.join(unknown)} is not in questions.json.")
    for row in rows or []:
        unit(project, row)
    run = {"id": new_run_id(project, kind, when), "kind": kind,
           "questions": list(dict.fromkeys(questions)) if questions else None,
           "rows": list(dict.fromkeys(rows)) if rows else None,
           "started": now_stamp(), "finished": None, "usage": {}, "group_usage": {}, "models": {}}
    project.write_run(run)
    proj = project.read("project")
    proj["stage"] = STAGE_FOR[kind]
    project.write("project", proj)
    return run





def receipt_from_result(result: dict) -> dict:
    """The split of a sub-agent's tokens from the JSON `claude -p --output-format json` prints:
    `{"fresh": input + cache creation, "cache_read", "output", "turns"}`. Raises HandbookError
    when the file has no usage in it."""
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        raise HandbookError("The receipt has no \"usage\" in it. It should be the JSON that "
                            "claude -p --output-format json prints.")
    def n(key):
        v = usage.get(key) or 0
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
    turns = result.get("num_turns")
    return {"fresh": n("input_tokens") + n("cache_creation_input_tokens"),
            "cache_read": n("cache_read_input_tokens"), "output": n("output_tokens"),
            "turns": turns if isinstance(turns, int) and not isinstance(turns, bool) else 0}


def record_usage(project: Project, run_id: str, step: str, tokens: int | None, row: str | None = None,
                 group: str | None = None, receipt: dict | None = None) -> dict:
    """Add `tokens` for `step` to a row's usage, or to a group's total (`group` is "label=value";
    group usage is one figure per group, whatever the step). With `receipt` (see
    `receipt_from_result`) the tokens are its parts added up, the tokens the API processed with
    the context re-read on every turn, and the split is kept under `receipts` (rows) or
    `group_receipts`. Returns the run record."""
    run = _get_run(project, run_id)
    if step not in USAGE_STEPS:
        raise HandbookError(f"The step must be one of {', '.join(USAGE_STEPS)}, not {step!r}.")
    if receipt is not None:
        bad = [k for k in RECEIPT_KEYS if not isinstance(receipt.get(k), int) or receipt[k] < 0]
        if bad:
            raise HandbookError(f"The receipt needs whole numbers for {', '.join(RECEIPT_KEYS)}.")
        tokens = receipt["fresh"] + receipt["cache_read"] + receipt["output"]
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        raise HandbookError(f"Tokens must be a whole number of 0 or more, not {tokens!r}.")
    if (row is None) == (group is None):
        raise HandbookError("Give either a row or a group, not both.")
    if row is not None:
        unit(project, row)
        steps = run.setdefault("usage", {}).setdefault(row, {s: None for s in USAGE_STEPS})
        steps[step] = (steps.get(step) or 0) + tokens
        if receipt is not None:
            kept = run.setdefault("receipts", {}).setdefault(row, {}).setdefault(step, dict.fromkeys(RECEIPT_KEYS, 0))
            for k in RECEIPT_KEYS:
                kept[k] += receipt[k]
    else:
        label, sep, value = group.partition("=")
        labels = project.read("project").get("labels") or []
        if not sep or label not in labels:
            raise HandbookError(f"The group {group!r} must be label=value with a label from "
                                f"project.json ({', '.join(labels) or 'none yet'}).")
        units = project.read("units").get("units", [])
        if not any((u.get("labels") or {}).get(label) == value for u in units):
            raise HandbookError(f"No unit has {label} {value!r}.")
        usage = run.setdefault("group_usage", {})
        usage[group] = (usage.get(group) or 0) + tokens
        if receipt is not None:
            kept = run.setdefault("group_receipts", {}).setdefault(group, dict.fromkeys(RECEIPT_KEYS, 0))
            for k in RECEIPT_KEYS:
                kept[k] += receipt[k]
    project.write_run(run)
    return run


STOP_WHY = ("budget", "turns", "time")


def record_stop(project: Project, run_id: str, row: str, result: dict) -> dict:
    """Note in the run that the scorer for `row` was stopped before it finished (`result` is the receipt
    hb score saved: `stopped`, `budget`, `left`, and its usage), under `stops[row]`, one entry per stop:
    {"why": budget | turns | time, "tokens", "budget", "left": [qids], "at"}. The page says so plainly;
    the questions left are scored once more in a fresh kit. Returns the entry."""
    run = _get_run(project, run_id)
    why = result.get("stopped")
    if why not in STOP_WHY:
        raise HandbookError(f"The receipt says the scorer stopped for {why!r}, not one of {', '.join(STOP_WHY)}.")
    r = receipt_from_result(result)
    entry = {"why": why, "tokens": r["fresh"] + r["cache_read"] + r["output"],
             "budget": result.get("budget") if isinstance(result.get("budget"), int) else None,
             "left": [str(q) for q in result.get("left") or []], "at": now_stamp()}
    run.setdefault("stops", {}).setdefault(row, []).append(entry)
    project.write_run(run)
    return entry


def cache_read_share(run: dict) -> float | None:
    """The share of a run's recorded tokens that were cache reads, over every receipt it holds;
    None when no receipt was recorded."""
    parts = [r for steps in (run.get("receipts") or {}).values() if isinstance(steps, dict)
             for r in steps.values() if isinstance(r, dict)]
    parts += [r for r in (run.get("group_receipts") or {}).values() if isinstance(r, dict)]
    total = sum((r.get("fresh") or 0) + (r.get("cache_read") or 0) + (r.get("output") or 0) for r in parts)
    return (sum(r.get("cache_read") or 0 for r in parts) / total) if total else None


def cell_progress(project: Project) -> dict:
    """{"cells_expected": applicable cells, "cells_done": those answered or no_evidence and not
    out of date}."""
    questions = project.read("questions").get("questions", [])
    expected = done = 0
    for u in project.read("units").get("units", []):
        cells = project.answers(u["id"]).get("cells") or {}
        for q in questions:
            if applies(q, u):
                expected += 1
                c = cells.get(q.get("id")) or {}
                if c.get("state") in ("answered", "no_evidence") and c.get("out_of_date") is not True:
                    done += 1
    return {"cells_expected": expected, "cells_done": done}


def finish_run(project: Project, run_id: str) -> dict:
    """Set the run's `finished`, and the project stage to done when every applicable cell is
    answered or no_evidence. Returns {"run", "cells_expected", "cells_done", "stage"}."""
    run = _get_run(project, run_id)
    if run.get("finished"):
        raise HandbookError(f"Run {run_id} already finished at {run['finished']}.")
    run["finished"] = now_stamp()
    project.write_run(run)
    progress = cell_progress(project)
    proj = project.read("project")
    if progress["cells_expected"] and progress["cells_done"] == progress["cells_expected"]:
        proj["stage"] = "done"
        project.write("project", proj)
    return {"run": run, **progress, "stage": proj.get("stage")}

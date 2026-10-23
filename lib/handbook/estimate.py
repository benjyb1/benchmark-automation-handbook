"""The cost of a full run, worked out from the usage recorded in the latest finished trial, and a
pacing check for a full run in progress. Everything is in tokens.

`estimate(project) -> dict` and `pace(project) -> dict` are pure reads; `cmd_estimate` writes
`estimate.json`. Per-step figures (find, second search, score, second reads) are added together,
so the range is the 25th to the 75th percentile of each step's per-row figures, summed, then
multiplied by the rows left."""
from __future__ import annotations

from . import needs
from .folder import Project
from .runs import cache_read_share

QUESTION_FACTOR = 0.15      # each extra question per row adds this share of the trial's score cost
MIN_ROWS_DURING = 5         # no pacing verdict before this many rows have usage
OVER_LIMIT = 0.25           # more than this far over the estimate stops the run
DONE_STATES = ("answered", "no_evidence", "not_applicable")
STEPS = ("find", "second_find", "score", "second")


def percentile(values, q: float) -> float:
    """Linear-interpolated percentile (q from 0 to 1) of a non-empty list."""
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _mean(values) -> float:
    return sum(values) / len(values)


def _units(project: Project) -> dict:
    return {u["id"]: u for u in project.read("units").get("units", [])
            if isinstance(u, dict) and isinstance(u.get("id"), str)}


def _n_questions(project: Project, unit: dict, held: dict | None = None) -> int:
    """The questions a run would score on the row: those that apply, less any that wait for the owner's
    data (`held`, needs.waiting)."""
    mine = (held or {}).get(unit.get("id")) or {}
    return len([q for q in project.questions_for(unit) if q.get("id") not in mine])


def remaining_rows(project: Project, units: dict | None = None, held: dict | None = None) -> list[str]:
    """Rows without a complete answers file: some question that applies has no answered,
    no_evidence or not_applicable cell yet. An answer that waits for the owner's data (needs.py)
    is not scored until it arrives, so it does not count."""
    units = _units(project) if units is None else units
    held = needs.waiting(project) if held is None else held
    left = []
    for uid, unit in units.items():
        cells = project.answers(uid).get("cells", {}) or {}
        if any((cells.get(q.get("id")) or {}).get("state") not in DONE_STATES
               and q.get("id") not in (held.get(uid) or {}) for q in project.questions_for(unit)):
            left.append(uid)
    return left


def latest_finished_trial(project: Project) -> dict | None:
    trials = [r for r in project.runs() if isinstance(r, dict) and r.get("kind") == "trial"
              and r.get("finished")]
    return max(trials, key=lambda r: (str(r["finished"]), str(r.get("started")), str(r.get("id")))) \
        if trials else None


def _group_members(units: dict, key: str, fallback: list[str]) -> list[str]:
    label, _, value = key.partition("=")
    members = [uid for uid, u in units.items()
               if label in (u.get("labels") or {}) and str(u["labels"][label]) == value]
    return members or list(fallback)


def row_usage(run: dict, units: dict) -> dict:
    """Row -> {find, second_find, score, second} for the rows a run covers. A group's usage is
    treated as finding tokens and shared out equally over every row in that group."""
    usage = run.get("usage") or {}
    rows = list(run.get("rows") or [])
    rows += [r for r in usage if r not in rows]
    out = {r: {s: (usage.get(r) or {}).get(s) for s in STEPS} for r in rows}
    for key, tokens in (run.get("group_usage") or {}).items():
        if tokens is None:
            continue
        members = _group_members(units, key, rows)
        for r in members:
            if r in out:
                out[r]["find"] = (out[r]["find"] or 0) + tokens / len(members)
    return out


def _second_read_cells(project: Project, row: str, run_id: str) -> int:
    try:
        cells = project.checks(row).get("cells", {}) or {}
    except ValueError:
        return 0
    return sum(1 for c in cells.values()
               if isinstance(c, dict) and isinstance(c.get("second_read"), dict)
               and c["second_read"].get("run") == run_id)


def _step(vals: list, factor: float) -> dict:
    if not vals:
        return {"point": 0.0, "low": 0.0, "high": 0.0}
    return {"point": _mean(vals) * factor, "low": percentile(vals, .25) * factor,
            "high": percentile(vals, .75) * factor}


def show_tokens(n: float) -> str:
    """Tokens for people: whole numbers, to the nearest thousand from 10,000 up (halves up)."""
    n = int(n / 1000 + 0.5) * 1000 if n >= 10000 else int(n + 0.5)
    return f"{n:,}"


def estimate(project: Project) -> dict:
    """The full-run estimate: `{"rows", "cells", "point", "low", "high", "assumptions",
    "per_row", "trial_run"}`. With no usable usage, point/low/high are None, `per_row` is empty
    and `note` says why."""
    units = _units(project)
    held = needs.waiting(project)
    left = remaining_rows(project, units, held)
    counts = {r: _n_questions(project, units[r], held) for r in left}
    cells = sum(counts.values())
    result = {"rows": len(left), "cells": cells, "point": None, "low": None, "high": None,
              "assumptions": [], "per_row": {}, "trial_run": None}
    trial = latest_finished_trial(project)
    if trial is None:
        result["note"] = ("There is no finished trial run, so there is no usage to estimate from. "
                          "Finish a trial and record its token usage first.")
        return result
    figures = row_usage(trial, units)
    result["trial_run"] = trial.get("id")
    find = [f["find"] for f in figures.values() if f["find"] is not None]
    again = [f["second_find"] for f in figures.values() if f["second_find"] is not None]
    if not (find or again or any(f["score"] is not None or f["second"] is not None
                                 for f in figures.values())):
        result["note"] = (f"The trial run {trial.get('id')} has no token usage recorded, so there is "
                          "no figure to give. Record usage for its rows and try again.")
        return result

    # second reads: tokens per second-read cell, and the share of trial cells sent to one
    covered = trial.get("questions")
    per_cell, sent, scored, fallback, score = [], 0, 0, False, []
    for r, f in figures.items():
        k = 1
        if r in units:
            k = sum(1 for q in project.questions_for(units[r])
                    if covered is None or q.get("id") in covered)
            scored += k
        if f["score"] is not None:
            # the trial row's own figure already covers k questions: take it back to one question
            score.append(f["score"] / (1 + QUESTION_FACTOR * (max(k, 1) - 1)))
        n = _second_read_cells(project, r, str(trial.get("id")))
        if f["second"] is not None and f["second"] > 0 and n == 0:
            n, fallback = 1, True
        if f["second"] is not None and n:
            per_cell.append(f["second"] / n)
            sent += n
    q_row = cells / len(left) if left else (
        _mean([_n_questions(project, u, held) for u in units.values()]) if units else 1.0)
    q_row = max(q_row, 1.0)
    scale = 1 + QUESTION_FACTOR * (q_row - 1)
    share_rows = len(again) / len(figures)
    share_cells = sent / scored if scored else 0.0
    # A row left whose group (a company's products, a state's councils) had a row searched before it finds the
    # group's own documents filed for it (cmd_find, "group"), so its finding costs less (nrdc-tissue, 21 Oct 2026).
    from .costs import GROUP_FIND_SHARE, group_keys, row_chars
    keys, looked = group_keys(project), set(row_chars(project))
    seen = {k for r in looked for k in keys.get(r, ())}
    followers = 0
    for r in left:
        mine = set(keys.get(r, ()))
        followers += r not in looked and bool(mine & seen)
        seen |= mine
    reuse = (len(left) - followers + GROUP_FIND_SHARE * followers) / len(left) if left else 1.0
    steps = {"find": _step(find, reuse), "second_find": _step(again, share_rows),
             "score": _step(score, scale), "second": _step(per_cell, share_cells * q_row)}
    point = sum(s["point"] for s in steps.values())
    low = min(sum(s["low"] for s in steps.values()), point)
    high = max(sum(s["high"] for s in steps.values()), point)
    n = len(left)
    result.update(point=round(point * n), low=round(low * n), high=round(high * n),
                  per_row={"point": round(point), "low": round(low), "high": round(high),
                           "steps": {k: {m: round(v) for m, v in s.items()} for k, s in steps.items()}})

    a = result["assumptions"]
    a.append(f"Worked out from the trial run {trial.get('id')}, which covered {len(figures)} "
             f"{'row' if len(figures) == 1 else 'rows'}. Figures are tokens, not money.")
    share = cache_read_share(trial)
    if share is None:
        a.append("The trial's figures were recorded as plain totals, with no receipt behind them. If "
                 "they came from a host's own count of a sub-agent (the size of its context at the "
                 "end), the tokens the API processed are many times more, mostly cache reads: see "
                 "host/claude.md, Usage.")
    else:
        result["cache_read_share"] = round(share, 3)
        a.append(f"The figures are the tokens the API processed, from the scorers' receipts: "
                 f"{share:.0%} of them were cache reads (the context read again on every turn).")
    a.append(f"{n} of {len(units)} rows have no complete answers file, so those are the rows left "
             f"to run ({cells} cells in all).")
    a.append("Finding a row costs the trial's average per row. Usage recorded for a group is "
             "shared out equally over every row in that group.")
    if followers:
        a.append(f"{followers} of the {n} rows left share a group with a row searched before them, so that group's "
                 f"own documents are filed for them already: finding for each is taken as {GROUP_FIND_SHARE:.0%} of "
                 "the trial's figure (an assumption).")
    a.append(f"A full run scores every question, not one. Each extra question adds "
             f"{QUESTION_FACTOR:.0%} to the cost of the first (an assumption). The trial's per-row "
             f"scoring figure is first reduced by that for the questions the row scored in the trial, "
             f"which gives the cost of one question. These rows average {q_row:.1f} questions, so "
             f"scoring is scaled by {scale:.2f}." if q_row > 1 else
             "Each row has one question, so the trial's per-row scoring figure is used as it is.")
    if not score:
        a.append("No scoring tokens were recorded in the trial, so none are included.")
    if not find:
        a.append("No finding tokens were recorded in the trial, so none are included.")
    if again:
        a.append(f"{share_rows:.0%} of the trial's rows needed a second search, averaging "
                 f"{show_tokens(_mean(again))} tokens each; that share is assumed for every row.")
    else:
        a.append("No trial row needed a second search, so none is allowed for.")
    if per_cell:
        a.append(f"{share_cells:.0%} of the trial's cells were sent to a second read, averaging "
                 f"{show_tokens(_mean(per_cell))} tokens each; that share is assumed for every cell.")
    elif sent == 0 and not any(f["second"] for f in figures.values()):
        a.append("No trial cell was sent to a second read, so none is allowed for.")
    else:
        a.append("Second reads happened in the trial but their tokens were not recorded, so none "
                 "are included.")
    if fallback:
        a.append("Some second-read tokens were recorded for a row with no second-read cell on "
                 "file, so that row counts as one cell.")
    a.append("The range runs from the 25th to the 75th percentile of the trial's per-row figures, "
             "step by step, multiplied by the rows left. Retries and extra reruns are not included.")
    return result


def full_run_actual(project: Project, run: dict) -> tuple[int, float | None]:
    """(rows with usage, mean tokens per row so far) for a full run. Each group's usage counts in
    proportion to how many of the group's rows are done, so a group part-way through is not
    charged in full against the few rows finished so far."""
    usage = run.get("usage") or {}
    rows = [r for r, steps in usage.items()
            if isinstance(steps, dict) and any(steps.get(s) is not None for s in STEPS)]
    if not rows:
        return 0, None
    total = sum(usage[r].get(s) or 0 for r in rows for s in STEPS)
    units = _units(project)
    for key, tokens in (run.get("group_usage") or {}).items():
        members = _group_members(units, key, list(run.get("rows") or rows))
        done = sum(1 for m in members if m in rows)
        total += (tokens or 0) * min(1.0, done / len(members)) if members else 0
    return len(rows), total / len(rows)


def pace(project: Project) -> dict:
    """Compare the current full run's mean tokens per row with the estimate's per-row point.
    `state` is one of no_run, no_estimate, early, on_track, over, stop (more than 25% over)."""
    runs = [r for r in project.runs() if isinstance(r, dict) and r.get("kind") == "full"]
    live = [r for r in runs if not r.get("finished")]
    if not live:
        return {"state": "no_run", "message": "No full run is in progress, so there is nothing to "
                                              "check yet."}
    run = max(live, key=lambda r: (str(r.get("started")), str(r.get("id"))))
    stored = project.read("estimate")
    per_row = (stored.get("per_row") or {}).get("point") if isinstance(stored, dict) else None
    if per_row is None:
        per_row = (estimate(project).get("per_row") or {}).get("point")
    if not per_row:
        return {"state": "no_estimate", "run": run.get("id"),
                "message": "There is no estimate to compare with, because no token usage was "
                           "recorded for a finished trial. Record trial usage first."}
    done, mean = full_run_actual(project, run)
    out = {"run": run.get("id"), "rows_done": done, "estimate_per_row": per_row,
           "actual_per_row": None if mean is None else round(mean)}
    if done < MIN_ROWS_DURING or mean is None:
        out.update(state="early", message=f"Only {done} {'row has' if done == 1 else 'rows have'} "
                   f"usage recorded so far. The pacing check starts at {MIN_ROWS_DURING} rows.")
        return out
    over = mean / per_row - 1
    pct = round(over * 100)
    out["over_percent"] = pct
    detail = (f"{show_tokens(mean)} tokens per row over {done} rows, against {show_tokens(per_row)} in the "
              "estimate")
    if over > OVER_LIMIT:
        out.update(state="stop", message=f"Full run is running {pct}% over the estimate: {detail}. "
                   "Pause and check before going on.")
    elif pct > 0:
        out.update(state="over", message=f"Full run is running {pct}% over the estimate: {detail}.")
    else:
        out.update(state="on_track", message=f"Full run is on track: {detail}.")
    return out

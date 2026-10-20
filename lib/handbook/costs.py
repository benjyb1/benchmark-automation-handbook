"""What Claude's work costs in tokens of the owner's Claude plan: what this project has used so far,
and a rough range for work before it runs (scoring rows again, a set-up draft, a chat turn).

    summary(project) -> dict          # the bundle's `cost`: the model, each row's document length, what was used
    estimate_rows(project, rows, questions=None) -> {"low", "high", "point", "rows", "cells", "basis", "measured"}
    setup_draft(project, chars) -> {"low", "high", "point", "basis"}
    used(project) -> dict             # tokens used so far, by kind and by run

Everything is tokens processed (fresh input, cache reads and output added up), the figure the
scorers' receipts give. No money, and no share of a plan: Anthropic does not publish a plan's
allowance in tokens, so the page compares with work the owner has already run instead.

Scoring a row, by default: tokens = A x questions^Q_EXP x characters^C_EXP, where characters is the
length of the text of the row's kept documents. Fitted 19 Oct 2026 on 192 rows scored in the format
tests (25 benchmarks, receipts from `hb score`): the two together explain about 70% of the spread;
each roughly doubles the cost when it is five times bigger. Half the rows came within 0.8 and 1.3
times the fitted figure, so with no history the range is wider (DEFAULT_SPREAD, about one standard
deviation either side) to allow for a benchmark that runs dearer or cheaper than most. Once this
project has scored rows with a receipt, the figure is scaled to them (`calibration`). Finding
documents, second searches and second reads are not in this figure: the trial's own usage gives
those (estimate.py), and the page says what is left out.
"""
from __future__ import annotations
import json
from statistics import median

from .folder import Project, read_json
from .runs import cache_read_share

A, Q_EXP, C_EXP = 1190, 0.43, 0.44
MIN_CHARS = 2000                     # a row with almost nothing to read still has the scorer's own reading
DEFAULT_CHARS = 130_000              # the median row in the fit, for a row with no documents yet
DEFAULT_SPREAD = (0.65, 1.55)        # no history: about one standard deviation either side
OWN_SPREAD = (0.8, 1.3)              # scaled to this project: the middle half of the fit's rows
FEW_SPREAD = (0.7, 1.45)             # scaled to fewer than four rows of this project
FIT_ROWS = 192
# The background set-up draft, measured on 25 format tests (19 Oct 2026): about 1.7 million in the
# middle, half between 1.2 and 2.3 million; material over BIG_MATERIAL characters ran 2 to 7 million.
SETUP_SMALL = (800_000, 2_300_000)
SETUP_BIG = (2_000_000, 6_000_000)
BIG_MATERIAL = 200_000
CHAT_TURN = (30_000, 100_000)        # one corner chat reply, measured on the Feedback tab at 28k to 62k
STEPS = ("find", "second_find", "score", "second")


def score_tokens(questions: int, chars: int | None, factor: float = 1.0) -> float:
    """The middle figure for scoring `questions` questions on a row whose documents hold `chars` characters."""
    c = DEFAULT_CHARS if chars is None else max(chars, MIN_CHARS)
    return A * factor * max(questions, 1) ** Q_EXP * c ** C_EXP


def doc_chars(project: Project) -> dict[str, int]:
    """Document id -> characters of text in its pages, for every kept document."""
    out = {}
    for doc, meta in project.documents().items():
        if isinstance(meta, dict) and meta.get("kept") is False:
            continue
        out[doc] = sum(len(p.get("text") or "") for p in project.pages(doc) if isinstance(p, dict))
    return out


def row_chars(project: Project, docs: dict[str, int] | None = None) -> dict[str, int]:
    """Row id -> characters of text in its kept documents, for rows whose documents were looked for
    (a row with no found file is left out: its documents are not known yet)."""
    docs = doc_chars(project) if docs is None else docs
    out = {}
    for u in project.read("units").get("units", []):
        uid = u.get("id") if isinstance(u, dict) else None
        if not isinstance(uid, str):
            continue
        if not (project.path / "found" / f"{uid}.json").is_file():
            continue
        found = project.found(uid)
        out[uid] = sum(docs.get(d, 0) for d in found.get("documents") or [] if isinstance(d, str))
    return out


def _units(project: Project) -> dict:
    return {u["id"]: u for u in project.read("units").get("units", [])
            if isinstance(u, dict) and isinstance(u.get("id"), str)}


def _run_questions(project: Project, run: dict, unit: dict) -> int:
    covered = run.get("questions")
    return sum(1 for q in project.questions_for(unit) if covered is None or q.get("id") in covered)


def measured_rows(project: Project, chars: dict[str, int] | None = None) -> list[dict]:
    """Every row a scorer's receipt was recorded for: [{run, kind, row, questions, chars, tokens, ratio}],
    ratio being the tokens against the default formula's figure."""
    chars = row_chars(project) if chars is None else chars
    units = _units(project)
    out = []
    for run in project.runs():
        if not isinstance(run, dict):
            continue
        for row, steps in (run.get("receipts") or {}).items():
            r = (steps or {}).get("score") if isinstance(steps, dict) else None
            if not isinstance(r, dict) or row not in units:
                continue
            tokens = (r.get("fresh") or 0) + (r.get("cache_read") or 0) + (r.get("output") or 0)
            if not tokens:
                continue
            q = _run_questions(project, run, units[row])
            out.append({"run": run.get("id"), "kind": run.get("kind"), "row": row, "questions": q,
                        "chars": chars.get(row), "tokens": tokens,
                        "ratio": tokens / score_tokens(q, chars.get(row))})
    return out


def _quartile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def calibration(project: Project, chars: dict[str, int] | None = None) -> dict:
    """{"basis": "project" | "default", "factor", "low", "high", "rows"}: the default formula scaled to
    this project's own scored rows when there are any, with the range around it."""
    rows = measured_rows(project, chars)
    if not rows:
        return {"basis": "default", "factor": 1.0, "low": DEFAULT_SPREAD[0], "high": DEFAULT_SPREAD[1], "rows": 0}
    ratios = [r["ratio"] for r in rows]
    factor = median(ratios)
    if len(rows) < 4:       # too few for quartiles: at least as wide as every row measured
        low = min(FEW_SPREAD[0], min(ratios) / factor)
        high = max(FEW_SPREAD[1], max(ratios) / factor)
    else:
        low = min(_quartile(ratios, .25) / factor, OWN_SPREAD[0])
        high = max(_quartile(ratios, .75) / factor, OWN_SPREAD[1])
    return {"basis": "project", "factor": round(factor, 4), "low": round(low, 3), "high": round(high, 3),
            "rows": len(rows)}


def estimate_rows(project: Project, rows: list[str], questions: list[str] | None = None,
                  model: dict | None = None, chars: dict[str, int] | None = None) -> dict:
    """A rough range for scoring `rows` again on `questions` (None: every question that applies to
    each row). A row whose documents are not known yet counts as this project's middle row, or the fit's."""
    chars = row_chars(project) if chars is None else chars
    model = calibration(project, chars) if model is None else model
    units = _units(project)
    known = [c for c in chars.values() if c]
    middle = int(median(known)) if known else DEFAULT_CHARS
    point, cells, n = 0.0, 0, 0
    for row in dict.fromkeys(rows):
        unit = units.get(row)
        if unit is None:
            continue
        qs = [q for q in project.questions_for(unit) if questions is None or q.get("id") in questions]
        if not qs:
            continue
        n += 1
        cells += len(qs)
        point += score_tokens(len(qs), chars[row] if row in chars else middle, model["factor"])
    return {"rows": n, "cells": cells, "point": round(point), "low": round(point * model["low"]),
            "high": round(point * model["high"]), "basis": model["basis"], "measured": model["rows"]}


def past_drafts(project: Project) -> list[int]:
    """Tokens each set-up draft used, oldest first: from the page's usage log, else the last receipt."""
    drafts = [e["processed"] for e in _log(project) if e.get("kind") == "setup-draft"
              and isinstance(e.get("processed"), int) and e["processed"] > 0]
    if drafts:
        return drafts
    receipt = read_json(project.path / "runs" / "receipts" / "setup-draft.json", {}) or {}
    usage = receipt.get("usage") if isinstance(receipt, dict) else None
    if isinstance(usage, dict):
        total = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                      "cache_read_input_tokens", "output_tokens"))
        if total:
            return [total]
    return []


def setup_draft(project: Project | None, chars: int | None) -> dict:
    """A rough range for the background set-up draft over the owner's material (`chars` characters
    in all): this project's last draft when there was one, else the measured default by size."""
    past = past_drafts(project) if project is not None else []
    if past:
        last = past[-1]
        return {"low": round(last * 0.7), "high": round(last * 1.4), "point": last, "basis": "project"}
    low, high = SETUP_BIG if (chars or 0) > BIG_MATERIAL else SETUP_SMALL
    return {"low": low, "high": high, "point": round((low * high) ** 0.5), "basis": "default"}


def _log(project: Project) -> list[dict]:
    """The page's usage log (`page-data/_log/usage.jsonl`, serve.py's log_usage), one entry per line."""
    path = project.path / "page-data" / "_log" / "usage.jsonl"
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
    except OSError:
        pass
    return out


def _run_tokens(run: dict) -> int:
    usage = run.get("usage") or {}
    total = sum(v for steps in usage.values() if isinstance(steps, dict)
                for s, v in steps.items() if s in STEPS and isinstance(v, int))
    return total + sum(v for v in (run.get("group_usage") or {}).values() if isinstance(v, int))


def used(project: Project) -> dict:
    """What this project has used so far: {"total", "by_kind": {kind: tokens}, "runs": [{id, kind,
    started, finished, rows, questions, tokens, per_row, receipts}], "setup_drafts": [tokens],
    "chat": {"replies", "tokens", "median"}}. Run kinds are trial, full and rerun (a correction's
    re-read is a one-row rerun). The Feedback round's own tokens are in the bundle's feedback part."""
    runs = []
    for run in project.runs():
        if not isinstance(run, dict):
            continue
        tokens = _run_tokens(run)
        if not tokens:
            continue
        rows = [r for r, s in (run.get("usage") or {}).items()
                if isinstance(s, dict) and any(isinstance(s.get(k), int) for k in STEPS)]
        runs.append({"id": run.get("id"), "kind": run.get("kind"), "started": run.get("started"),
                     "finished": run.get("finished"), "rows": len(rows),
                     "questions": len(run["questions"]) if run.get("questions") else None,
                     "tokens": tokens, "per_row": round(tokens / len(rows)) if rows else None,
                     "receipts": cache_read_share(run) is not None})
    drafts = past_drafts(project)
    chat = [e["processed"] for e in _log(project) if e.get("kind") == "sample" and isinstance(e.get("processed"), int)]
    by_kind: dict[str, int] = {}
    for r in runs:
        by_kind[r["kind"] or "run"] = by_kind.get(r["kind"] or "run", 0) + r["tokens"]
    if drafts:
        by_kind["setup"] = sum(drafts)
    if chat:
        by_kind["chat"] = sum(chat)
    bulk = [e["processed"] for e in _log(project) if e.get("kind") == "bulk" and isinstance(e.get("processed"), int)]
    if bulk:
        by_kind["bulk"] = sum(bulk)        # reading what an owner added in bulk (bulk.py)
    return {"total": sum(by_kind.values()), "by_kind": by_kind, "runs": runs, "setup_drafts": drafts,
            "chat": {"replies": len(chat), "tokens": sum(chat), "median": round(median(chat)) if chat else None}}


def summary(project: Project) -> dict:
    """The bundle's `cost`: what the page and its chat need to give a range before work runs and to say
    what was used. `rows` is each row's document length (rows whose documents were looked for)."""
    chars = row_chars(project)
    model = calibration(project, chars)
    measured = measured_rows(project, chars)
    known = [c for c in chars.values() if c]
    return {
        "model": {"a": A, "q_exp": Q_EXP, "c_exp": C_EXP, "min_chars": MIN_CHARS, "default_chars": DEFAULT_CHARS,
                  "middle_chars": int(median(known)) if known else DEFAULT_CHARS, "fit_rows": FIT_ROWS, **model},
        "rows": chars,
        "measured": {r["row"]: r["tokens"] for r in measured},   # the latest receipt's tokens per row
        "setup": {"small": list(SETUP_SMALL), "big": list(SETUP_BIG), "big_material": BIG_MATERIAL,
                  "last": (past_drafts(project) or [None])[-1]},
        "chat_turn": list(CHAT_TURN),
        "used": used(project),
    }


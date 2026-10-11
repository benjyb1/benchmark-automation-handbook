"""The facts a report may state, and the check that a report states nothing else.

`compute(project)` gathers every figure from the project folder (answers, score sheet, checks,
rules, needs and, when it exists, `compare/compare.json`), rounded the way the viewer shows it:
counts as whole numbers, percentages with the score sheet's rounding, means to one decimal place.
It never reads the held-back folder; the comparison figures come from `compare.json`.

`unbacked(text, numbers, question_ids)` and `check_report(project)` find the numbers in
`report/report.md` that are not among the facts.
"""
from __future__ import annotations
import re

from .folder import HandbookError, Project, read_json
from .scoresheet import ROUNDING_STEPS, round_half_up, row_totals
from .shapes import BIG_MOVE_WHY, FLAGS, REASONS, check_compare

SCORED = ("answered", "no_evidence")
STATES = ("answered", "no_evidence", "not_applicable", "not_run")
NO_BAND = "No band"


# --- rounding ----------------------------------------------------------------------------------

def mean_1dp(values) -> float | None:
    """The mean of `values` to one decimal place, halves up; None for an empty list."""
    values = [v for v in values if v is not None]
    return round_half_up(sum(values) / len(values), "half_up_1dp") if values else None


def _percent(part, whole, mode: str):
    return round_half_up(part / whole * 100, mode) if whole else None


def _percent_mode(sheet: dict) -> str:
    """Shares are rounded the way the score sheet rounds its overall score (whole numbers when
    that is not a rounding the score sheet names)."""
    mode = ((sheet or {}).get("overall") or {}).get("rounding")
    return mode if mode in ROUNDING_STEPS else "half_up_int"


# --- the facts ---------------------------------------------------------------------------------

def _is_complete(project: Project, unit: dict, cells: dict) -> bool:
    """Every question that applies to the row has a scored or not-applicable cell."""
    return all((cells.get(q["id"]) or {}).get("state") in SCORED + ("not_applicable",)
               for q in project.questions_for(unit))


def _level_counts(question: dict, counts: dict, scored: int, mode: str) -> dict:
    values = [str(lv.get("value")) for lv in (question.get("answer") or {}).get("levels") or []
              if isinstance(lv, dict)]
    values += [v for v in counts if v not in values]
    return {v: {"rows": counts.get(v, 0), "percent": _percent(counts.get(v, 0), scored, mode)}
            for v in values}


def _comparison(data: dict, mode: str) -> dict:
    agree = {}
    for key in dict.fromkeys(k for r in data.get("rows") or [] for k in (r.get("agree") or {})):
        flags = [r["agree"][key] for r in data["rows"] if key in (r.get("agree") or {})]
        agree[key] = {"agree": sum(flags), "of": len(flags), "percent": _percent(sum(flags), len(flags), mode)}
    found = data.get("disagreements") or []
    by_reason = {r: sum(1 for d in found if d.get("reason") == r) for r in REASONS}
    big = data.get("big_moves") or []
    return {"level": data.get("level"), "rows": len(data.get("rows") or []), "agree": agree,
            "disagreements": {"total": len(found), "by_reason": by_reason,
                              "explained_by_claude": sum(1 for d in found if d.get("by") == "claude")},
            "past_editions": len((data.get("history") or {}).get("editions") or []),
            "big_moves": {"total": len(big),
                          "by_why": {w: sum(1 for m in big if m.get("why") == w) for w in BIG_MOVE_WHY},
                          "breaks_trend": sum(1 for m in big if m.get("trend") == "breaks")}}


def compute(project: Project) -> dict:
    """The facts object (not written); see the module docstring. Totals-based figures (bands and
    means) use the rows whose every applicable question is scored; `rows_partial` says how many
    scored rows were left out for that reason."""
    proj = project.read("project")
    sheet = project.read("scoresheet")
    computable = bool(sheet) and sheet.get("computable") is not False
    mode = _percent_mode(sheet)
    units = [u for u in project.read("units").get("units", []) if isinstance(u, dict)]
    questions = project.read("questions").get("questions", [])
    section_ids = [s["id"] for s in sheet.get("sections") or []]

    states = dict.fromkeys(STATES, 0)
    per_q = {q["id"]: {"expected": 0, "scored": 0, "counts": {}} for q in questions}
    expected = rows_scored = 0
    complete: list[tuple[dict, dict]] = []       # (unit, totals) for complete rows with totals
    complete_count = 0
    for unit in units:
        cells = {k: c for k, c in (project.answers(unit["id"]).get("cells") or {}).items()
                 if isinstance(c, dict)}
        for q in project.questions_for(unit):
            cell = cells.get(q["id"]) or {}
            state = cell.get("state") if cell.get("state") in STATES else "not_run"
            expected += 1
            states[state] += 1
            stats = per_q[q["id"]]
            stats["expected"] += 1
            if state in SCORED:
                stats["scored"] += 1
                value = cell.get("value") if cell.get("value") is not None else q.get("no_evidence_value")
                stats["counts"][str(value)] = stats["counts"].get(str(value), 0) + 1
        if not any(c.get("state") in SCORED for c in cells.values()):
            continue
        rows_scored += 1
        if _is_complete(project, unit, cells):
            complete_count += 1
            totals = row_totals(project, unit, cells) if computable else None
            if totals:
                complete.append((unit, totals))

    bands = {}
    for group in (sheet.get("bands") or []) if computable else []:
        of = group.get("of")
        names = [b.get("name") for b in sorted(group.get("bands") or [], key=lambda b: -b.get("min", 0))]
        counts = {name: 0 for name in names}
        for _, totals in complete:
            name = totals["bands"].get(of)
            counts[name or NO_BAND] = counts.get(name or NO_BAND, 0) + 1
        bands[of] = {name: {"rows": n, "percent": _percent(n, len(complete), mode)}
                     for name, n in counts.items() if n or name != NO_BAND}

    def means(pairs):
        return {"mean_overall": mean_1dp(t["overall"] for _, t in pairs),
                "sections": {sid: mean_1dp(t["sections"][sid]["percent"] for _, t in pairs)
                             for sid in section_ids}}

    overall = means(complete)
    labels = {}
    for label in proj.get("labels") or []:
        values = list(dict.fromkeys((u.get("labels") or {}).get(label) for u in units
                                    if (u.get("labels") or {}).get(label) not in (None, "")))
        labels[label] = {}
        for value in values:
            group_units = [u for u in units if (u.get("labels") or {}).get(label) == value]
            pairs = [(u, t) for u, t in complete if (u.get("labels") or {}).get(label) == value]
            m = means(pairs)
            labels[label][str(value)] = {"rows": len(group_units), "rows_complete": len(pairs),
                                         "mean_overall": m["mean_overall"], "sections": m["sections"]}

    flags, rows_checked = dict.fromkeys(FLAGS, 0), 0
    for unit in units:
        if (project.path / "checks" / f"{unit['id']}.json").is_file():
            rows_checked += 1
            for c in (project.checks(unit["id"]).get("cells") or {}).values():
                for f in (c.get("flags") or []) if isinstance(c, dict) else []:
                    if f in flags:
                        flags[f] += 1

    rules = [r for r in project.read("rules").get("rules", []) if isinstance(r, dict)]
    needs = [n for n in project.read("needs").get("needs", []) if isinstance(n, dict)]
    facts = {
        "name": proj.get("name"), "edition": proj.get("edition"), "cutoff": proj.get("cutoff"),
        "rows": len(units), "rows_scored": rows_scored, "rows_complete": complete_count,
        "rows_partial": rows_scored - complete_count,
        "cells_expected": expected, "cells": states,
        "bands": bands, "mean_overall": overall["mean_overall"], "section_means": overall["sections"],
        "questions": {q["id"]: {"expected": per_q[q["id"]]["expected"], "scored": per_q[q["id"]]["scored"],
                                "levels": _level_counts(q, per_q[q["id"]]["counts"],
                                                        per_q[q["id"]]["scored"], mode)}
                      for q in questions},
        "labels": labels,
        "flags": flags, "rows_checked": rows_checked,
        "rules": {status: sum(1 for r in rules if r.get("status") == status)
                  for status in ("open", "skipped", "decided")},
        "needs_open": sum(1 for n in needs if n.get("status") == "needed"),
    }
    compare = read_json(project.file("compare/compare"))
    if compare:
        problems = check_compare(compare)
        if problems:
            raise HandbookError("compare/compare.json has problems, so the comparison facts cannot "
                                "be worked out:\n- " + "\n- ".join(problems))
        facts["comparison"] = _comparison(compare, mode)
    return facts


# --- the report check --------------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_LINK_URL = re.compile(r"\]\([^)]*\)")
_BARE_URL = re.compile(r"(?:https?://|www\.)\S+")
_LIST_MARK = re.compile(r"^\s*\d+[.)]\s")


def _value(token: str) -> float:
    return round(float(token.replace(",", "")), 6)


def numbers_in(text: str) -> list[tuple[str, int]]:
    """Every number in `text` as `(token as written, line number)`: integers, decimals,
    percentages (the `%` is not part of the token) and both parts of "N of M". Not counted: digits
    inside a markdown link URL or a bare URL, a list item's own number, and digits that are part
    of a name such as P1 or R12."""
    found = []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = _LIST_MARK.sub(lambda m: " " * len(m.group()), line)
        line = _BARE_URL.sub(" ", _LINK_URL.sub("]", line))
        found.extend((m.group(), n) for m in _NUMBER.finditer(line))
    return found


def fact_numbers(facts) -> set[float]:
    """Every number among `facts`, nested groups flattened: numbers, numbers inside text values
    (a date such as 2025-07-14 gives 2025, 7 and 14) and numbers inside keys (a level value such
    as "25")."""
    out: set[float] = set()

    def walk(x):
        if x is None or isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.add(round(float(x), 6))
        elif isinstance(x, str):
            out.update(_value(t) for t, _ in numbers_in(x))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(facts)
    return out


def _question_id_numbers(question_ids) -> set[float]:
    out = set()
    for qid in question_ids:
        out.update(_value(m.group()) for m in re.finditer(r"\d+(?:\.\d+)?", str(qid)))
        out.update(_value(m.group()) for m in re.finditer(r"\d+", str(qid)))
    return out


def unbacked(text: str, numbers: set, question_ids) -> list[tuple[str, int]]:
    """The numbers in `text` that need a fact and have none, as `(token, line)`. Allowed without
    one: whole numbers from 0 to 10, years from 1900 to 2100, numbers that are question ids or
    appear in them, and numbers inside a markdown link URL."""
    ids = _question_id_numbers(question_ids)
    out = []
    for token, line in numbers_in(text):
        value = _value(token)
        plain = "," not in token and "." not in token
        if value in numbers or value in ids or (plain and (0 <= value <= 10 or 1900 <= value <= 2100)):
            continue
        out.append((token, line))
    return out


def check_report(project: Project) -> dict:
    """Check `report/report.md` against `report/facts.json`. Returns `{"numbers": how many numbers
    it has, "unbacked": [{"number", "line", "text"}]}`. Raises HandbookError when either file is
    missing."""
    path = project.path / "report" / "report.md"
    if not path.is_file():
        raise HandbookError(f"There is no report at {path} to check.")
    facts = read_json(project.file("report/facts"))
    if not facts:
        raise HandbookError("There is no report/facts.json to check the report against. "
                            "Run hb facts first.")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    questions = [q.get("id") for q in project.read("questions").get("questions", [])
                 if isinstance(q, dict)]
    bad = unbacked(text, fact_numbers(facts), questions)
    return {"numbers": len(numbers_in(text)),
            "unbacked": [{"number": t, "line": n, "text": lines[n - 1].strip()} for t, n in bad]}

"""Which rows to try first: a small, varied set for the trial.

`cover(project, question_id, size)` looks only at rows the question applies to. It picks, one at a
time, the row that brings the most label values (country Denmark, brand Acme, ...) not yet in the
set, lowest row id first on a tie, until every value is in. Covering every value wins over the
size asked for. If the set is still smaller than `size`, it adds rows round-robin over the
group-by label's values (the value with the fewest rows so far goes next, lowest row id within
it) until `size` is reached or there are no rows left. The same project and arguments always give
the same rows, whatever order units.json is in.
"""
from __future__ import annotations
from collections import Counter

from .folder import HandbookError, applies


def label_values(unit: dict) -> set[tuple[str, str]]:
    """The (label, value) pairs a row carries; empty values are ignored."""
    return {(label, str(value)) for label, value in (unit.get("labels") or {}).items()
            if value not in (None, "")}


def candidate_rows(project, question_id: str) -> list[dict]:
    """The units the question applies to, sorted by id. Raises HandbookError for an unknown question."""
    question = next((q for q in project.read("questions").get("questions", [])
                     if q.get("id") == question_id), None)
    if question is None:
        raise HandbookError(f"There is no question {question_id!r} in questions.json.")
    units = project.read("units").get("units", [])
    return sorted((u for u in units if applies(question, u)), key=lambda u: u["id"])


def cover(project, question_id: str, size: int = 10) -> list[str]:
    """Row ids for a trial on `question_id`: every label value covered, filled up to `size`
    (more rows when covering needs them, never more than the question's candidates), sorted by
    row id."""
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise HandbookError("The trial size must be a whole number of 1 or more.")
    candidates = candidate_rows(project, question_id)
    by_id = {u["id"]: u for u in candidates}
    values = {rid: label_values(u) for rid, u in by_id.items()}
    remaining = sorted(by_id)
    chosen: list[str] = []
    covered: set = set()

    while True:                                   # greedy cover; strict > keeps the lowest id on a tie
        best, best_gain = None, 0
        for rid in remaining:
            gain = len(values[rid] - covered)
            if gain > best_gain:
                best, best_gain = rid, gain
        if best is None:
            break
        chosen.append(best)
        covered |= values[best]
        remaining.remove(best)

    group_by = project.read("project").get("group_by")

    def group_of(rid: str) -> str:
        value = (by_id[rid].get("labels") or {}).get(group_by) if group_by else None
        return "" if value is None else str(value)

    picked = Counter(group_of(rid) for rid in chosen)
    while len(chosen) < size and remaining:       # round-robin over the group-by values
        group = min({group_of(rid) for rid in remaining}, key=lambda g: (picked[g], g))
        rid = next(r for r in remaining if group_of(r) == group)
        chosen.append(rid)
        remaining.remove(rid)
        picked[group] += 1
    return sorted(chosen)

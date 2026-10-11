"""What a rule puts at stake: how many cells it touches and how many points could move.

A rule touches every (row, question) cell where the row is one of the rule's `rows` (null means
every row) and the question is one of its `question_ids` and applies to that row. The points at
stake are the sum, over those cells, of the question's top points minus its lowest points.
"""
from __future__ import annotations

from .folder import HandbookError, applies


def _number(x):
    x = round(x, 6)
    return int(x) if float(x).is_integer() else x


def _spread(question: dict) -> float:
    points = [lv.get("points") for lv in (question.get("answer") or {}).get("levels") or []
              if isinstance(lv.get("points"), (int, float)) and not isinstance(lv.get("points"), bool)]
    return max(points) - min(points) if points else 0


def rule_stakes(project, rule: dict, questions=None, units=None) -> dict:
    """`{"cells": n, "points": p}` for `rule`. `questions` and `units` are the file contents
    (lists of dicts) when the caller has them already; otherwise they are read. Raises
    HandbookError when the rule names a question or a row that does not exist."""
    if questions is None:
        questions = project.read("questions").get("questions", [])
    if units is None:
        units = project.read("units").get("units", [])
    rid = rule.get("id", "?")
    by_question = {q.get("id"): q for q in questions}
    by_row = {u.get("id"): u for u in units}
    missing_q = [q for q in rule.get("question_ids") or [] if q not in by_question]
    if missing_q:
        raise HandbookError(f"Rule {rid} names question {', '.join(map(str, missing_q))}, which "
                            "is not in questions.json.")
    wanted = rule.get("rows")
    if wanted is None:
        rows = list(units)
    else:
        missing_r = [r for r in wanted if r not in by_row]
        if missing_r:
            raise HandbookError(f"Rule {rid} names row {', '.join(map(str, missing_r))}, which "
                                "is not in units.json.")
        rows = [by_row[r] for r in dict.fromkeys(wanted)]
    cells, points = 0, 0
    for qid in dict.fromkeys(rule.get("question_ids") or []):
        question = by_question[qid]
        spread = _spread(question)
        for unit in rows:
            if applies(question, unit):
                cells += 1
                points += spread
    return {"cells": cells, "points": _number(points)}

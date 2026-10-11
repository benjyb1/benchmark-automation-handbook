"""Link an existing rule to cells that already carry a doubt it covers (`hb link-rule`).

`ingest.raise_rule` does this for a new rule; this is the same linking for a rule that is already in
rules.json, so a repeated doubt points at the rule instead of raising a second one.
"""
from __future__ import annotations

from .folder import HandbookError, Project, applies
from .kit import unit

SCORED = ("answered", "no_evidence")


def link_rule(project: Project, rule_id: str, rows: list, questions: list) -> dict:
    """Add `rule_id` to `rules` on each scored cell of `rows` x `questions` that the question applies
    to and the rule covers. On those cells `judgement` becomes true when the rule is open or skipped,
    and a rule doubt (`unsure.why == "rule"`) is marked handled. Cells that cannot be linked are
    skipped with a reason. Returns {"rule", "linked": [[row, qid]], "skipped": [[row, qid, why]]}.
    Raises HandbookError, writing nothing, for an unknown rule, row or question, or when no cell can
    be linked."""
    rule = next((r for r in project.read("rules").get("rules", []) if r.get("id") == rule_id), None)
    if rule is None:
        raise HandbookError(f"There is no rule {rule_id!r} in rules.json.")
    rows, questions = list(dict.fromkeys(rows)), list(dict.fromkeys(questions))
    if not rows:
        raise HandbookError("Give at least one row to link the rule to (--rows).")
    if not questions:
        raise HandbookError("Give at least one question to link the rule to (--questions).")
    units = {r: unit(project, r) for r in rows}
    known = {q.get("id"): q for q in project.read("questions").get("questions", [])}
    unknown = [q for q in questions if q not in known]
    if unknown:
        raise HandbookError(f"Question {', '.join(unknown)} is not in questions.json.")

    covered_questions = rule.get("question_ids") or []
    covered_rows = rule.get("rows")
    judgement = rule.get("status") in ("open", "skipped")
    linked, skipped, updated = [], [], []
    for row in rows:
        answers = project.answers(row)
        cells = answers.get("cells") or {}
        changed = False
        for qid in questions:
            if not applies(known[qid], units[row]):
                skipped.append([row, qid, f"question {qid} does not apply to {row}"])
            elif qid not in covered_questions or (covered_rows is not None and row not in covered_rows):
                skipped.append([row, qid, f"{rule_id} does not cover {qid} for {row}"])
            elif not isinstance(cells.get(qid), dict) or cells[qid].get("state") not in SCORED:
                skipped.append([row, qid, f"{row} {qid} is not scored yet"])
            else:
                cell = cells[qid]
                before = repr(cell)
                cell["rules"] = list(dict.fromkeys(list(cell.get("rules") or []) + [rule_id]))
                if judgement:
                    cell["judgement"] = True
                if isinstance(cell.get("unsure"), dict) and cell["unsure"].get("why") == "rule":
                    cell["unsure"]["handled"] = True
                changed = changed or repr(cell) != before
                linked.append([row, qid])
        if changed:
            updated.append((row, answers))
    if not linked:
        raise HandbookError(f"Rule {rule_id} was not linked to anything: "
                            + "; ".join(why for _, _, why in skipped) + ".")
    for row, answers in updated:
        project.write_answers(row, answers)
    return {"rule": rule_id, "linked": linked, "skipped": skipped}

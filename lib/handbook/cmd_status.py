"""hb status [--json]: the checklist with statuses, counts and the next step."""
from __future__ import annotations
import copy
import json

from . import control
from .cli import command_parser, open_project
from .folder import CHECKLIST_KEYS, DEFAULTS, ID_RE, MUST_HAVES, HandbookError, Project, applies, read_json

DONE = ("confirmed", "not_needed", "skipped")


def _object(project: Project, name: str, problems: list) -> dict:
    """A top-level file as a dict. One that is not a JSON object reads as empty, with a problem."""
    data = project.read(name)
    if isinstance(data, dict):
        return data
    problems.append(f"{name}.json must be a JSON object, found {json.dumps(data)[:40]}; "
                    "status ignores it.")
    return copy.deepcopy(DEFAULTS.get(name, {}))


def _entries(data: dict, key: str, name: str, problems: list) -> list:
    """The JSON-object entries of the list under `key`; anything else is skipped, with a problem."""
    items = data.get(key, [])
    if not isinstance(items, list):
        problems.append(f"{name}.json: {key} must be a list; status ignores it.")
        return []
    good = [x for x in items if isinstance(x, dict)]
    if len(good) != len(items):
        problems.append(f"{name}.json: {len(items) - len(good)} of the {key} entries are not JSON "
                        "objects; status skips them.")
    return good


def _units(file: dict, problems: list) -> list:
    """The units with a usable id (lowercase letters, digits and dashes). The others are skipped,
    with a problem naming each."""
    items = file.get("units", [])
    if not isinstance(items, list):
        problems.append("units.json: units must be a list; status ignores it.")
        return []
    units = []
    for i, unit in enumerate(items):
        uid = unit.get("id") if isinstance(unit, dict) else None
        if isinstance(uid, str) and ID_RE.match(uid):
            units.append(unit)
        elif uid is None:
            problems.append(f"units.json: unit {i + 1} has no id, so status skips it.")
        else:
            problems.append(f"units.json: unit {i + 1} has the id {json.dumps(uid)[:40]}, which must "
                            "use only lowercase letters, digits and dashes, so status skips it.")
    return units


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def status(project: Project) -> dict:
    """Everything `hb status` reports, as one dict (the `--json` output). Files with the wrong
    shape do not stop it: they are skipped and named in `problems` (a list of plain sentences,
    empty when all is well)."""
    problems: list[str] = []
    proj = _object(project, "project", problems)
    questions = _entries(_object(project, "questions", problems), "questions", "questions", problems)
    units = _units(_object(project, "units", problems), problems)
    rules = _entries(_object(project, "rules", problems), "rules", "rules", problems)
    needs = _entries(_object(project, "needs", problems), "needs", "needs", problems)
    plan = _entries(_object(project, "where_to_look", problems), "plan", "where_to_look", problems)
    scoresheet = _object(project, "scoresheet", problems)
    runs = project.runs()
    held = read_json(project.held_back / "results.json", {})
    if not isinstance(held, dict):
        problems.append("The held-back results.json must be a JSON object; status ignores it.")
        held = {}

    states = {"answered": 0, "no_evidence": 0, "not_applicable": 0, "not_run": 0}
    expected, remaining = 0, 0
    for unit in units:
        answers = project.answers(unit["id"])
        cells = answers.get("cells", {}) if isinstance(answers, dict) else None
        if not isinstance(cells, dict):
            problems.append(f"answers/{unit['id']}.json: cells must be a JSON object; "
                            "status counts every cell as not run.")
            cells = {}
        unit_left = False
        for q in questions:
            if not applies(q, unit):
                continue
            expected += 1
            qid = q.get("id")
            cell = cells.get(qid) if isinstance(qid, str) else None
            state = cell.get("state", "not_run") if isinstance(cell, dict) else "not_run"
            if not (isinstance(state, str) and state in states):
                problems.append(f"answers/{unit['id']}.json: cell {json.dumps(qid)[:40]} has no "
                                "valid state; status counts it as not run.")
                state = "not_run"
            states[state] += 1
            unit_left = unit_left or state == "not_run"
        remaining += unit_left
    open_rules = sum(r.get("status") == "open" for r in rules)
    needs_open = sum(n.get("status") == "needed" for n in needs)
    counts = {
        "rows": len(units), "questions": len(questions),
        "cells_expected": expected,
        "cells_scored": states["answered"] + states["no_evidence"],
        "cells_answered": states["answered"], "cells_no_evidence": states["no_evidence"],
        "cells_not_applicable": states["not_applicable"], "cells_not_run": states["not_run"],
        "rows_remaining": remaining,
        "rules": len(rules), "open_rules": open_rules,
        "needs": len(needs), "needs_open": needs_open,
        "runs": len(runs),
    }

    group_labels = [x for x in proj.get("group_labels") or [] if isinstance(x, str)] \
        if isinstance(proj.get("group_labels"), list) else []
    sections = scoresheet.get("sections")
    details = {
        "questions": str(len(questions)),
        "units": str(len(units)),
        "scoresheet": _plural(len(sections) if isinstance(sections, list) else 0, "section", "sections")
        if scoresheet else None,
        "group_labels": ", ".join(group_labels) or None,
        "cutoff": proj.get("cutoff"),
        "where_to_look": _plural(len(plan), "entry", "entries"),
        "needs": f"{len(needs)}, {needs_open} still needed",
        "rules": f"{len(rules)}, {open_rules} open",
        "last_results": f"{len(held['rows']) if isinstance(held.get('rows'), dict) else 0} rows held back"
        if held else None,
    }
    checklist_in = proj.get("checklist") if isinstance(proj.get("checklist"), dict) else {}
    checklist = [{"key": key, "status": checklist_in.get(key, "missing"),
                  "must_have": key in MUST_HAVES, "detail": details.get(key)}
                 for key in CHECKLIST_KEYS]

    plural = proj.get("unit_word_plural") or "units"
    first_open = next((c for c in checklist if c["must_have"] and c["status"] not in DONE), None)
    if first_open:
        nxt = first_open["key"]
        text = (f"Next step: {nxt}, which is {first_open['status']}. Set-up needs it confirmed, "
                "skipped or marked not needed before the trial.")
    elif not runs:
        nxt, text = "trial", "Next step: trial. Set-up is complete and nothing has been run yet."
    elif remaining:
        nxt = "run"
        verb = "has" if remaining == 1 else "have"
        text = (f"Next step: run. {remaining} of {len(units)} {plural} still {verb} "
                "cells to score.")
    else:
        nxt, text = "results", "Next step: results. Every cell has been scored."

    # Run control: paused, stops, scope (control.json); one sentence when any is set.
    try:
        ctl = control.read(project)
        control_text = control.stop_text(project, ctl)
    except HandbookError as e:
        problems.append(f"{e} Status ignores control.json.")
        ctl, control_text = control.empty(), None
    return {"name": proj.get("name"), "slug": proj.get("slug") or project.slug,
            "stage": proj.get("stage"), "checklist": checklist, "counts": counts,
            "next": nxt, "next_text": text, "problems": problems,
            "control": control.public(ctl), "control_text": control_text}


def main(argv) -> int:
    parser = command_parser("status", "Show the checklist, counts and the next step.")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    s = status(open_project(args.project))
    if args.json:
        print(json.dumps(s, ensure_ascii=False))
        return 1 if s["problems"] else 0
    c = s["counts"]
    musts = sum(item["must_have"] for item in s["checklist"])
    print(f"{s['name']} ({s['slug']}), stage {s['stage']}")
    print(f"Checklist, {len(s['checklist'])} items ({musts} must-haves first):")
    for item in s["checklist"]:
        detail = f" ({item['detail']})" if item["detail"] else ""
        print(f"  {item['key']}: {item['status']}{detail}")
    print(f"Rows: {c['rows']}. Questions: {c['questions']}. "
          f"Cells scored: {c['cells_scored']} of {c['cells_expected']} "
          f"({c['cells_answered']} answered, {c['cells_no_evidence']} no evidence, "
          f"{c['cells_not_applicable']} not applicable, {c['cells_not_run']} not run). "
          f"Rules: {c['rules']}, {c['open_rules']} open. "
          f"Needs: {c['needs']}, {c['needs_open']} still needed. Runs: {c['runs']}.")
    print(s["next_text"])
    if s["control_text"]:
        print(s["control_text"])
    if s["problems"]:
        print(f"{_plural(len(s['problems']), 'problem', 'problems')} with the project files:")
        for problem in s["problems"]:
            print(f"- {problem}")
        print("Run hb validate to check every file.")
        return 1
    return 0

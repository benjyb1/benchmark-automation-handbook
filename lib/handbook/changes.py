"""Changing the set-up at any stage: rows and questions added, removed, renamed or merged, a
question's levels or wording changed, rules removed, or a whole part (score sheet, where to look,
data needed, rules to add, the basics) replaced. What the set-up Claude could draft, the corner chat
can change after scoring and mid-round too: nothing is locked because answers exist, and the only
cost is scoring again (19 Oct 2026, Benjy: "no requests the user makes that are structurally out of
reach for the corner Claude").

    change(project, op, dry=True) -> {"ok", "problems", "said", "impact"}

`op` is {"op": NAME, ...}, or a list of them applied together, in order, all or nothing (a question in a
new section with the score sheet that weighs it and the data need it rests on: Citizens Advice, 21 Oct
2026, where one op at a time could never add a category). The names and their fields are in OPS. An
add_question whose section the score sheet lacks brings it with `new_section` {name, weight}. With
`dry`, nothing is written and `impact` says what the change does to the answers already there:
    kept            answers left as they are
    out_of_date     [[row, question], ...] that keep their value but should be scored again
    removed         [[row, question], ...] answers taken away (a copy is kept for Undo by the page)
    unscored        [[row, question], ...] new cells with no answer yet
    rescore         {"rows", "questions"}: what scoring again would cover
Otherwise the change is written, and kept only if the whole-folder check finds nothing new (it is
put back as it was, with the problems, if it does). The page's server takes a copy of the project
first, so one Undo puts everything back, removed answers included (serve.py, chatwork.py).
"""
from __future__ import annotations
import copy
import re

from .folder import HandbookError, Project, applies
from .shapes import check_folder, where_without_stale

LIVE = ("answered", "no_evidence")
OPS = {
    "add_rows": "rows: [{id (optional), name, labels: {label: value}}]",
    "remove_rows": "ids: [row ids]",
    "rename_row": "id, name",
    "merge_rows": "into: a row id, rows: [row ids merged into it (their documents join it; they are removed)]",
    "add_question": "question: {id, short, wording, section, answer: {kind: levels, levels: [{value, points, meaning}]}, rule, no_evidence_value, applies_to}; new_section: {name, weight} when its section is not in the score sheet yet",
    "remove_questions": "ids: [question ids]",
    "change_question": "id, fields: {wording, short, rule, section, levels, no_evidence_value, applies_to, evidence, reading; "
                       "for a number question also unit, min, max, decimals, points, relative, lower_is_better, best, "
                       "worst (null takes one away)}",
    "remove_rules": "ids: [rule ids]",
    "set_part": "part: scoresheet | where_to_look | needs | rules | basics, content (as the set-up formats say)",
}
PARTS = ("scoresheet", "where_to_look", "needs", "rules", "basics")
QUESTION_FIELDS = ("wording", "short", "rule", "section", "levels", "no_evidence_value", "applies_to", "evidence",
                   "reading")
STALE_FIELDS = ("wording", "rule", "levels", "applies_to")
# A number question's answer: how its figure reads, and how it counts in the totals: between min and max, or with
# `relative` between the rows' lowest and highest figures, `best` or `worst` fixing an end (answers.py). The chat
# changes these too (21 Oct 2026: it could not make a figure relative). null takes one away.
FIGURE_FIELDS = ("unit", "min", "max", "decimals", "points", "relative", "lower_is_better", "best", "worst")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or "row"


class _State:
    """Every file the change may touch, read once; written together at the end."""

    def __init__(self, project: Project):
        self.p = project
        self.project = project.read("project")
        self.questions = project.read("questions")
        self.units = project.read("units")
        self.rules = project.read("rules")
        self.needs = project.read("needs") if project.file("needs").is_file() else None
        self.where_to_look = project.read("where_to_look") if project.file("where_to_look").is_file() else None
        self.scoresheet = project.read("scoresheet") if project.file("scoresheet").is_file() else None
        self.new_rules: list[str] = []          # rules a set_part added, whose stakes are worked out once written
        self.gaps_before = set(_section_problems(self))   # a gap already there is not this change's to refuse
        self.answers = {u["id"]: project.answers(u["id"]) for u in self.units.get("units", [])
                        if isinstance(u, dict) and project.path.joinpath("answers", f"{u['id']}.json").is_file()}
        self.checks: dict[str, dict] = {}
        self.found: dict[str, dict] = {}
        self.drop_files: set[str] = set()       # rows whose answers, checks and found go
        self.drop_earlier: set[str] = set()     # questions whose earlier answers go (answers/earlier/)
        self.dirty: set[str] = set()
        self.out_of_date: list = []
        self.removed: list = []
        self.unscored: list = []

    def q_by_id(self):
        return {q.get("id"): q for q in self.questions.get("questions", [])}

    def u_by_id(self):
        return {u.get("id"): u for u in self.units.get("units", [])}

    def live(self, row, qid):
        c = ((self.answers.get(row) or {}).get("cells") or {}).get(qid)
        return c if isinstance(c, dict) and c.get("state") in LIVE else None

    def stale(self, row, qid):
        c = self.live(row, qid)
        if c is not None and c.get("out_of_date") is not True:
            c["out_of_date"] = True
            self.out_of_date.append([row, qid])
            self.dirty.add("answers:" + row)


def _rows_missing(s: _State, ids) -> list:
    known = s.u_by_id()
    return [r for r in ids if r not in known]


def _remove_rows(s: _State, ids: list) -> None:
    ids = list(dict.fromkeys(ids))
    s.units["units"] = [u for u in s.units.get("units", []) if u.get("id") not in ids]
    for r in ids:
        for qid, c in ((s.answers.get(r) or {}).get("cells") or {}).items():
            if isinstance(c, dict) and c.get("state") in LIVE:
                s.removed.append([r, qid])
        s.answers.pop(r, None)
        s.drop_files.add(r)
    for rule in s.rules.get("rules", []):
        if isinstance(rule.get("rows"), list):
            rule["rows"] = [r for r in rule["rows"] if r not in ids]
    s.rules["rules"] = [r for r in s.rules.get("rules", []) if not (isinstance(r.get("rows"), list) and not r["rows"])]
    s.dirty.update({"units", "rules"})
    if s.needs is not None:
        for n in s.needs.get("needs", []):
            if isinstance(n.get("rows"), list):
                n["rows"] = [r for r in n["rows"] if r not in ids]
        s.needs["needs"] = [n for n in s.needs.get("needs", []) if not (isinstance(n.get("rows"), list) and not n["rows"])]
        s.dirty.add("needs")
    if s.where_to_look is not None:
        for e in s.where_to_look.get("plan", []):
            f = e.get("for")
            if isinstance(f, dict) and isinstance(f.get("rows"), list):
                f["rows"] = [r for r in f["rows"] if r not in ids]
        s.where_to_look["plan"] = [e for e in s.where_to_look.get("plan", [])
                                   if not (isinstance(e.get("for"), dict) and e["for"].get("rows") == [])]
        s.dirty.add("where_to_look")


def _remove_questions(s: _State, ids: list) -> None:
    ids = list(dict.fromkeys(ids))
    s.drop_earlier.update(ids)
    s.questions["questions"] = [q for q in s.questions.get("questions", []) if q.get("id") not in ids]
    for row, a in s.answers.items():
        cells = a.get("cells") or {}
        for qid in ids:
            c = cells.pop(qid, None)
            if isinstance(c, dict):
                if c.get("state") in LIVE:
                    s.removed.append([row, qid])
                s.dirty.add("answers:" + row)
        chk = s.checks.setdefault(row, s.p.checks(row))
        if any((chk.get("cells") or {}).pop(qid, None) is not None for qid in ids):
            s.dirty.add("checks:" + row)
    for rule in s.rules.get("rules", []):
        rule["question_ids"] = [q for q in rule.get("question_ids") or [] if q not in ids]
    s.rules["rules"] = [r for r in s.rules.get("rules", []) if r.get("question_ids")]
    s.dirty.update({"questions", "rules"})
    if s.needs is not None:
        for n in s.needs.get("needs", []):
            if isinstance(n.get("why"), list):
                n["why"] = [q for q in n["why"] if q not in ids]
        s.needs["needs"] = [n for n in s.needs.get("needs", []) if not (isinstance(n.get("why"), list) and not n["why"])]
        s.dirty.add("needs")
    if s.where_to_look is not None:                  # where to look stops naming them (22 Oct 2026)
        left = {q.get("id") for q in s.questions.get("questions", []) if isinstance(q, dict)}
        kept = where_without_stale(s.where_to_look, left)
        if kept is not s.where_to_look:
            s.where_to_look = kept
            s.dirty.add("where_to_look")


def _new_cells(s: _State, rows=None, qids=None) -> None:
    """Cells that apply but have no answer: listed as unscored."""
    for u in s.units.get("units", []):
        if rows is not None and u.get("id") not in rows:
            continue
        for q in s.questions.get("questions", []):
            if qids is not None and q.get("id") not in qids:
                continue
            if applies(q, u) and not ((s.answers.get(u["id"]) or {}).get("cells") or {}).get(q["id"]):
                s.unscored.append([u["id"], q["id"]])


def _apply(s: _State, op: dict) -> str:
    name = op.get("op")
    if name == "add_rows":
        added = []
        known = s.u_by_id()
        labels = list(s.project.get("labels") or [])
        for r in op.get("rows") or []:
            if not isinstance(r, dict) or not str(r.get("name") or "").strip():
                raise HandbookError("Each new row needs a name.")
            rid = str(r.get("id") or _slug(r["name"]))
            if rid in known:
                raise HandbookError(f"There is already a row {rid}.")
            unit = {"id": rid, "name": str(r["name"]).strip(),
                    "labels": {k: str(v) for k, v in (r.get("labels") or {}).items() if k in labels},
                    "status": "confirmed", "from": "added in the corner chat"}
            s.units.setdefault("units", []).append(unit)
            known[rid] = unit
            added.append(rid)
        if not added:
            raise HandbookError("Give the rows to add.")
        s.dirty.add("units")
        _new_cells(s, rows=added)
        return f"Added {', '.join(added)}."
    if name == "remove_rows":
        ids = [str(x) for x in op.get("ids") or []]
        if not ids or _rows_missing(s, ids):
            raise HandbookError(f"There is no row {', '.join(_rows_missing(s, ids)) or '(none given)'}.")
        _remove_rows(s, ids)
        return f"Removed {', '.join(ids)}."
    if name == "rename_row":
        unit = s.u_by_id().get(str(op.get("id")))
        if unit is None or not str(op.get("name") or "").strip():
            raise HandbookError("Give an existing row id and its new name.")
        unit["name"] = str(op["name"]).strip()
        s.dirty.add("units")
        return f"Renamed {unit['id']} to {unit['name']}."
    if name == "merge_rows":
        into, rows = str(op.get("into") or ""), [str(x) for x in op.get("rows") or [] if str(x) != str(op.get("into"))]
        if not rows or _rows_missing(s, [into] + rows):
            raise HandbookError("Give the row to keep (into) and the rows to merge into it.")
        found = s.found.setdefault(into, s.p.found(into))
        docs = list(found.get("documents") or [])
        for r in rows:
            docs += [d for d in s.p.found(r).get("documents") or [] if d not in docs]
        found["documents"] = docs
        s.dirty.add("found:" + into)
        _remove_rows(s, rows)
        for qid in list(((s.answers.get(into) or {}).get("cells") or {})):
            s.stale(into, qid)
        return f"Merged {', '.join(rows)} into {into}."
    if name == "add_question":
        q = dict(op.get("question") or {})
        if not q.get("id") or q["id"] in s.q_by_id():
            raise HandbookError("The new question needs an id that is not taken.")
        if op.get("new_section") is not None:
            _new_section(s, q.get("section"), op["new_section"])
        q.setdefault("short", "")
        q.setdefault("no_evidence_value", 0)
        q.setdefault("applies_to", None)
        q.setdefault("variants", [])
        q.setdefault("rule", "")
        q.setdefault("evidence", "")
        q.update({"status": "confirmed", "from": "added in the corner chat"})
        s.questions.setdefault("questions", []).append(q)
        s.dirty.add("questions")
        _new_cells(s, qids=[q["id"]])
        return f"Added question {q['id']}."
    if name == "remove_questions":
        ids = [str(x) for x in op.get("ids") or []]
        missing = [x for x in ids if x not in s.q_by_id()]
        if not ids or missing:
            raise HandbookError(f"There is no question {', '.join(missing) or '(none given)'}.")
        _remove_questions(s, ids)
        return f"Removed question{'s' if len(ids) > 1 else ''} {', '.join(ids)}."
    if name == "change_question":
        q = s.q_by_id().get(str(op.get("id")))
        fields = op.get("fields") or {}
        bad = [k for k in fields if k not in QUESTION_FIELDS + FIGURE_FIELDS]
        if q is None or not fields or bad:
            raise HandbookError(f"Give an existing question id and fields from: {', '.join(QUESTION_FIELDS)}; for a "
                                f"number question also {', '.join(FIGURE_FIELDS)}.")
        figure = [k for k in fields if k in FIGURE_FIELDS]
        kind = (q.get("answer") or {}).get("kind") if isinstance(q.get("answer"), dict) else None
        if figure and kind != "number":
            raise HandbookError(f"{', '.join(figure)} {'is' if len(figure) == 1 else 'are'} for a number question; the "
                                f"answer to question {q['id']} is {kind or 'not set'}, so change its levels instead.")
        before = copy.deepcopy(q)
        for k, v in fields.items():
            if k == "levels":
                q.setdefault("answer", {})["levels"] = v
            elif k in FIGURE_FIELDS:
                if v is None:
                    q["answer"].pop(k, None)
                else:
                    q["answer"][k] = v
            else:
                q[k] = v
        s.dirty.add("questions")
        stale = [k for k in fields if k in STALE_FIELDS and (before.get(k) if k != "levels" else (before.get("answer") or {}).get("levels")) != v_of(q, k)]
        values = {lv.get("value"): lv.get("points") for lv in (q.get("answer") or {}).get("levels") or []}
        for row in list(s.answers):
            c = s.live(row, q["id"])
            if c is None:
                continue
            if "levels" in fields and c.get("value") in values and c.get("points") != values[c.get("value")]:
                c["points"] = values[c.get("value")]
                s.dirty.add("answers:" + row)
            if stale and not ("levels" in stale and len(stale) == 1 and c.get("value") in values):
                s.stale(row, q["id"])
        if "applies_to" in fields:
            _new_cells(s, qids=[q["id"]])
        return f"Changed question {q['id']}."
    if name == "remove_rules":
        ids = [str(x) for x in op.get("ids") or []]
        known = {r.get("id") for r in s.rules.get("rules", [])}
        if not ids or any(x not in known for x in ids):
            raise HandbookError("Give existing rule ids.")
        s.rules["rules"] = [r for r in s.rules.get("rules", []) if r.get("id") not in ids]
        for row, a in s.answers.items():
            for qid, c in (a.get("cells") or {}).items():
                if isinstance(c, dict) and any(x in (c.get("rules") or []) for x in ids):
                    c["rules"] = [x for x in c["rules"] if x not in ids]
                    s.dirty.add("answers:" + row)
                    s.stale(row, qid)
        s.dirty.add("rules")
        return f"Removed rule{'s' if len(ids) > 1 else ''} {', '.join(ids)}."
    raise HandbookError(f"Unknown change {name!r}. The changes are: {', '.join(OPS)}.")


def _sheet_sections(s: _State) -> list | None:
    """The score sheet's sections, or None when it has no list of them to check against."""
    sheet = s.scoresheet
    if not isinstance(sheet, dict) or sheet.get("computable") is False or not isinstance(sheet.get("sections"), list):
        return None
    return sheet["sections"]


def _new_section(s: _State, sid, spec) -> None:
    """Add section `sid` to the score sheet for a new question: {name, weight, percent?, rounding?}, its
    percent and rounding like the sheet's first section unless given. A weighted mean needs its weight."""
    sections = _sheet_sections(s)
    if not isinstance(spec, dict) or not str(spec.get("name") or "").strip() or not isinstance(sid, str) or not sid:
        raise HandbookError("new_section is {name, weight}, for the question's section.")
    if sections is None:
        raise HandbookError("The score sheet does not list sections, so a section cannot be added to it.")
    if any(isinstance(x, dict) and x.get("id") == sid for x in sections):
        return                                    # already there (an earlier change in the same list)
    first = next((x for x in sections if isinstance(x, dict)), {})
    sections.append({"id": sid, "name": str(spec["name"]).strip(),
                     "percent": spec.get("percent", first.get("percent", True)),
                     "rounding": spec.get("rounding", first.get("rounding", "none"))})
    overall = s.scoresheet.get("overall") or {}
    if overall.get("method") == "weighted_mean_of_section_percents":
        weight = spec.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
            raise HandbookError(f"The overall is a weighted mean of the sections, so new_section needs the weight of "
                                f"{sid} (a number, such as 0.35).")
        overall.setdefault("weights", {})[sid] = weight
    s.dirty.add("scoresheet")


def _set_part(s: _State, op: dict) -> str:
    """set_part inside a list of changes: the part replaced in memory, written with the rest (as
    setup_parts.write_part does on its own: a draft, and the checklist item back to draft)."""
    from .setup_parts import BASICS, SETUP_RULE_FIELDS, _set_checklist
    part, content = str(op.get("part") or ""), op.get("content")
    if part not in PARTS:
        raise HandbookError("set_part takes scoresheet, where_to_look, needs, rules or basics; rows and questions change one by one.")
    if not isinstance(content, dict):
        raise HandbookError(f"The {part.replace('_', ' ')} must be an object.")
    s.dirty.add("project")
    if part == "basics":
        unknown = [k for k in content if k not in BASICS]
        if unknown:
            raise HandbookError(f"Not part of the basics: {', '.join(unknown)}. The basics are {', '.join(BASICS)}.")
        s.project.update(copy.deepcopy(content))
        for key in ("cutoff", "group_labels"):
            if key in content:
                _set_checklist(s.project, key, "draft")
        return "Changed the basics."
    if part == "rules":
        new = content.get("rules")
        if not isinstance(new, list) or not new or not all(isinstance(r, dict) for r in new):
            raise HandbookError("rules needs rules: a list of open rules to add.")
        have = s.rules.setdefault("rules", [])
        n = max([int(r["id"][1:]) for r in have if str(r.get("id", ""))[1:].isdigit()] or [0])
        for r in new:
            n += 1
            rule = {k: r.get(k) for k in SETUP_RULE_FIELDS}
            rule.update({"id": f"R{n}", "rows": r.get("rows"), "stakes": None, "status": "open",
                         "decision": None, "raised_by": "setup"})
            have.append(rule)
            s.new_rules.append(rule["id"])
        _set_checklist(s.project, "rules", "draft")
        s.dirty.add("rules")
        return f"Added rule{'s' if len(new) != 1 else ''} {', '.join(s.new_rules[-len(new):])}."
    data = copy.deepcopy(content)
    if part == "scoresheet":
        data["status"] = "draft"
    setattr(s, part, data)
    _set_checklist(s.project, part, "draft")
    s.dirty.add(part)
    return {"scoresheet": "Replaced the score sheet.", "where_to_look": "Replaced where to look.",
            "needs": "Replaced the data needed."}[part]


def _section_problems(s: _State) -> list[str]:
    """Questions whose section the score sheet lacks, said with the way to add it."""
    sections = _sheet_sections(s)
    if sections is None:
        return []
    ids = {x.get("id") for x in sections if isinstance(x, dict)}
    return [f"Question {q.get('id')} is in section {q.get('section')}, which is not in the score sheet. Add it with "
            "new_section {name, weight} on add_question, or set the score sheet in the same change."
            for q in s.questions.get("questions", []) if isinstance(q, dict) and q.get("section") not in ids]


def v_of(q: dict, k: str):
    return (q.get("answer") or {}).get("levels") if k == "levels" else q.get(k)


def _impact(s: _State) -> dict:
    total = sum(1 for a in s.answers.values() for c in (a.get("cells") or {}).values()
                if isinstance(c, dict) and c.get("state") in LIVE)
    redo = s.out_of_date + s.unscored
    return {"kept": total - len(s.out_of_date), "out_of_date": s.out_of_date, "removed": s.removed,
            "unscored": s.unscored,
            "rescore": {"rows": sorted({r for r, _ in redo}), "questions": sorted({q for _, q in redo}, key=str)}}


def change(project: Project, op, dry: bool = True) -> dict:
    """One change, or a list of them applied together (see the module docstring)."""
    if isinstance(op, list):
        if not op or not all(isinstance(o, dict) for o in op):
            raise HandbookError("A list of changes holds objects with op, applied together in order.")
        return _change_all(project, op, dry)
    if not isinstance(op, dict):
        raise HandbookError("A change is an object with op, or a list of them applied together.")
    if op.get("op") == "set_part":
        from . import setup_parts
        part = str(op.get("part") or "")
        if part not in ("scoresheet", "where_to_look", "needs", "rules", "basics"):
            raise HandbookError("set_part takes scoresheet, where_to_look, needs, rules or basics; rows and questions change one by one.")
        if dry:
            return {"ok": True, "problems": [], "said": f"Replace the {part.replace('_', ' ')}.",
                    "impact": {"kept": None, "out_of_date": [], "removed": [], "unscored": [], "rescore": {"rows": [], "questions": []}}}
        got = setup_parts.write_part(project, part, op.get("content"))
        return {"ok": bool(got.get("ok")), "problems": got.get("problems") or [], "said": got.get("said"), "impact": None}
    return _change_all(project, [op], dry)


def _change_all(project: Project, ops: list[dict], dry: bool) -> dict:
    """Every op applied in memory, in order; then, unless `dry`, all written, and all put back if the
    whole-folder check finds anything new."""
    s = _State(project)
    saids = []
    for o in ops:
        try:
            saids.append(_set_part(s, o) if o.get("op") == "set_part" else _apply(s, o))
        except HandbookError as e:
            if len(ops) == 1:
                raise
            return {"ok": False, "problems": [f"Change {len(saids) + 1} of {len(ops)} ({o.get('op')}): {e}"],
                    "said": " ".join(saids), "impact": None}
    said = " ".join(saids)
    impact = _impact(s)
    problems = [x for x in _section_problems(s) if x not in s.gaps_before]
    if problems:
        return {"ok": False, "problems": problems, "said": said, "impact": impact}
    if dry:
        return {"ok": True, "problems": [], "said": said, "impact": impact}
    before = set(check_folder(project))
    saved = {}

    def keep(path):
        saved.setdefault(path, path.read_bytes() if path.is_file() else None)

    for name in ("project", "questions", "units", "rules", "needs", "where_to_look", "scoresheet"):
        if name in s.dirty:
            keep(project.file(name))
            project.write(name, getattr(s, name))
    if s.new_rules:
        from .stakes import rule_stakes
        for rule in s.rules.get("rules", []):
            if rule.get("id") in s.new_rules:
                try:
                    rule["stakes"] = rule_stakes(project, rule)
                except (HandbookError, KeyError, TypeError, ValueError):
                    rule["stakes"] = None
        project.write("rules", s.rules)
    for key in s.dirty:
        kind, _, row = key.partition(":")
        if kind == "answers" and row in s.answers:
            keep(project.path / "answers" / f"{row}.json")
            project.write_answers(row, s.answers[row])
        elif kind == "checks":
            keep(project.path / "checks" / f"{row}.json")
            project.write_checks(row, s.checks[row])
        elif kind == "found":
            keep(project.path / "found" / f"{row}.json")
            project.write_found(row, s.found[row])
    earlier_dir = project.path / "answers" / "earlier"
    for path in sorted(earlier_dir.glob("*.json")) if s.drop_earlier and earlier_dir.is_dir() else []:
        data = project.earlier_answers(path.stem)
        cells = data.get("cells") or {}
        if set(cells) & s.drop_earlier:
            keep(path)
            data["cells"] = {q: v for q, v in cells.items() if q not in s.drop_earlier}
            project.write_earlier_answers(path.stem, data)
    for row in s.drop_files:
        for folder in ("answers", "answers/earlier", "checks", "found"):
            path = project.path.joinpath(*folder.split("/"), f"{row}.json")
            if path.is_file():
                keep(path)
                path.unlink()
    problems = [x for x in check_folder(project) if x not in before]
    if problems:
        for path, raw in saved.items():
            if raw is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(raw)
        empty = sorted({m.group(1) for x in problems for m in [re.search(r"section (.+?) has no questions", x)] if m})
        if empty and any(o.get("op") == "remove_questions" for o in ops):
            # ChemScore, sweep round 3: the refusal named the section but not what to do about it.
            problems.append(f"Nothing was removed. Taking these questions off leaves section{'s' if len(empty) > 1 else ''} "
                            f"{', '.join(empty)} with none: first save the score sheet without "
                            f"{'them' if len(empty) > 1 else 'it'} (set_part scoresheet), then remove the questions, "
                            "or send both in one list of changes.")
        return {"ok": False, "problems": problems, "said": said, "impact": impact}
    return {"ok": True, "problems": [], "said": said, "impact": impact}

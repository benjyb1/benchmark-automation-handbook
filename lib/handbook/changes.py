"""Changing the set-up at any stage: rows and questions added, removed, renamed or merged, a
question's levels or wording changed, rules removed, or a whole part (score sheet, where to look,
data needed, rules to add, the basics) replaced. What the set-up Claude could draft, the corner chat
can change after scoring and mid-round too: nothing is locked because answers exist, and the only
cost is scoring again (19 Oct 2026, Benjy: "no requests the user makes that are structurally out of
reach for the corner Claude").

    change(project, op, dry=True) -> {"ok", "problems", "said", "impact"}

`op` is {"op": NAME, ...}; the names and their fields are in OPS. With `dry`, nothing is written and
`impact` says what the change does to the answers already there:
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
from .shapes import check_folder

LIVE = ("answered", "no_evidence")
OPS = {
    "add_rows": "rows: [{id (optional), name, labels: {label: value}}]",
    "remove_rows": "ids: [row ids]",
    "rename_row": "id, name",
    "merge_rows": "into: a row id, rows: [row ids merged into it (their documents join it; they are removed)]",
    "add_question": "question: {id, short, wording, section, answer: {kind: levels, levels: [{value, points, meaning}]}, rule, no_evidence_value, applies_to}",
    "remove_questions": "ids: [question ids]",
    "change_question": "id, fields: {wording, short, rule, section, levels, no_evidence_value, applies_to, evidence}",
    "remove_rules": "ids: [rule ids]",
    "set_part": "part: scoresheet | where_to_look | needs | rules | basics, content (as the set-up formats say)",
}
QUESTION_FIELDS = ("wording", "short", "rule", "section", "levels", "no_evidence_value", "applies_to", "evidence")
STALE_FIELDS = ("wording", "rule", "levels", "applies_to")


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
        self.answers = {u["id"]: project.answers(u["id"]) for u in self.units.get("units", [])
                        if isinstance(u, dict) and project.path.joinpath("answers", f"{u['id']}.json").is_file()}
        self.checks: dict[str, dict] = {}
        self.found: dict[str, dict] = {}
        self.drop_files: set[str] = set()       # rows whose answers, checks and found go
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
        bad = [k for k in fields if k not in QUESTION_FIELDS]
        if q is None or not fields or bad:
            raise HandbookError(f"Give an existing question id and fields from: {', '.join(QUESTION_FIELDS)}.")
        before = copy.deepcopy(q)
        for k, v in fields.items():
            if k == "levels":
                q.setdefault("answer", {})["levels"] = v
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


def v_of(q: dict, k: str):
    return (q.get("answer") or {}).get("levels") if k == "levels" else q.get(k)


def _impact(s: _State) -> dict:
    total = sum(1 for a in s.answers.values() for c in (a.get("cells") or {}).values()
                if isinstance(c, dict) and c.get("state") in LIVE)
    redo = s.out_of_date + s.unscored
    return {"kept": total - len(s.out_of_date), "out_of_date": s.out_of_date, "removed": s.removed,
            "unscored": s.unscored,
            "rescore": {"rows": sorted({r for r, _ in redo}), "questions": sorted({q for _, q in redo}, key=str)}}


def change(project: Project, op: dict, dry: bool = True) -> dict:
    if not isinstance(op, dict):
        raise HandbookError("A change is an object with op.")
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
    s = _State(project)
    said = _apply(s, op)
    impact = _impact(s)
    if dry:
        return {"ok": True, "problems": [], "said": said, "impact": impact}
    before = set(check_folder(project))
    saved = {}

    def keep(path):
        saved.setdefault(path, path.read_bytes() if path.is_file() else None)

    for name in ("project", "questions", "units", "rules", "needs", "where_to_look"):
        if name in s.dirty:
            keep(project.file(name))
            project.write(name, getattr(s, name))
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
    for row in s.drop_files:
        for folder in ("answers", "checks", "found"):
            path = project.path / folder / f"{row}.json"
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
        return {"ok": False, "problems": problems, "said": said, "impact": impact}
    return {"ok": True, "problems": [], "said": said, "impact": impact}

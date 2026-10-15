"""Applying edits and rule decisions to a project folder.

The viewer collects the owner's edits and decisions and the host exports them as one payload
(see "Viewer edits" in the plan); the skills write the same shape for changes made in chat
(`via="chat"`, ids starting `c-`). `apply` gives both the same effects:

- question `levels_points`: new points on the question, stored `points` recomputed in every answer
- question `wording` or `rule`: that question's answered and no_evidence cells become out of date
- question `applies_to`, unit `labels`: a cell that stops applying becomes `not_applicable` (the old
  cell is kept under `previous`); a cell that starts applying gets no answer, so it is not_run
- confirm `questions` or `units`: the checklist item and every item's status become confirmed
- decision: the rule is decided, and every cell resting on it becomes out of date
- decision with `skip: true`: an open rule becomes skipped (Claude's reading stays in use and no
  cell changes); a rule already decided or skipped is left as it is

Nothing is written unless the whole payload has been worked through, and ids already listed in
`edits-applied.json` are skipped, so applying a payload twice changes nothing.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone

from .folder import HandbookError, applies, effective_question
from .shapes import check_edits

VIA = ("viewer", "chat")
LIVE = ("answered", "no_evidence")      # cells that carry a scored value
TEXT_FIELDS = ("wording", "short", "rule")
OUT_OF_DATE_FIELDS = ("wording", "rule")
SET_ASIDE_NOTE = "No longer applies after an edit to the questions or rows."


def _stamp(now: datetime) -> str:
    """`now` as a UTC timestamp ending in Z (a naive datetime is taken to be UTC)."""
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _points_for(question: dict, value):
    """The points of the question's level with this value, or None when there is no such level."""
    for level in (question.get("answer") or {}).get("levels") or []:
        if level.get("value") == value and type(level.get("value")) is type(value):
            return level.get("points")
    return None


def _blank_cell(state: str, note: str = "", previous: dict | None = None) -> dict:
    cell = {"state": state, "value": None, "points": None, "evidence": [], "rules": [],
            "judgement": False, "unsure": None, "note": note, "run": None, "out_of_date": False}
    if previous:
        cell["previous"] = previous
    return cell


class _Folder:
    """The files an edit can touch, held in memory until the whole payload has been applied."""

    def __init__(self, project):
        self.project = project
        self.docs = {name: project.read(name) for name in ("project", "questions", "units", "rules")}
        self.questions = {q.get("id"): q for q in self.docs["questions"].get("questions", [])}
        self.units = {u.get("id"): u for u in self.docs["units"].get("units", [])}
        self.rules = {r.get("id"): r for r in self.docs["rules"].get("rules", [])}
        self.dirty: set = set()             # "project", "questions", ... or ("answers", row)
        self._answers: dict = {}
        self.flagged: set = set()           # (row, question id) made out of date by this payload
        self.recomputed = self.set_aside = self.now_not_run = 0

    def cells(self, row: str) -> dict:
        if row not in self._answers:
            self._answers[row] = self.project.answers(row)
        data = self._answers[row]
        if not isinstance(data.get("cells"), dict):
            data["cells"] = {}
        return data["cells"]

    def live_cells(self, qid=None):
        """(row, question id, cell) for every answered or no_evidence cell, optionally of one question."""
        for row in self.units:
            for q, cell in self.cells(row).items():
                if isinstance(cell, dict) and cell.get("state") in LIVE and qid in (None, q):
                    yield row, q, cell

    def touch(self, row: str) -> None:
        self.dirty.add(("answers", row))

    def flag(self, row: str, qid: str, cell: dict) -> None:
        self.flagged.add((row, qid))
        if cell.get("out_of_date") is not True:
            cell["out_of_date"] = True
            self.touch(row)

    def save(self) -> None:
        if "rules" in self.dirty and self.rules and all(r.get("status") in ("decided", "skipped")
                                                        for r in self.rules.values()):
            # Every rule settled: the checklist item is done (ALI, 14 Oct 2026: it stayed "draft").
            checklist = self.docs["project"].setdefault("checklist", {})
            if checklist.get("rules") not in ("confirmed", "skipped", "not_needed"):
                checklist["rules"] = "confirmed"
                self.dirty.add("project")
        for name in ("project", "questions", "units", "rules"):
            if name in self.dirty:
                self.project.write(name, self.docs[name])
        for row, data in self._answers.items():
            if ("answers", row) in self.dirty:
                self.project.write_answers(row, data)


def _settle(f: _Folder, unit_before: dict, unit_after: dict, q_before: dict, q_after: dict) -> None:
    """Bring one row's cell for one question into line after the question or the row changed."""
    row, qid = unit_after["id"], q_after["id"]
    was, now = applies(q_before, unit_before), applies(q_after, unit_after)
    cells = f.cells(row)
    cell = cells.get(qid)
    if was and not now:
        if isinstance(cell, dict) and cell.get("state") != "not_applicable":
            live = cell.get("state") in LIVE
            cells[qid] = _blank_cell("not_applicable", SET_ASIDE_NOTE, copy.deepcopy(cell) if live else cell.get("previous"))
            f.set_aside += live
            f.touch(row)
    elif now and not was:
        f.now_not_run += 1
        if isinstance(cell, dict) and cell.get("state") == "not_applicable":
            cells[qid] = _blank_cell("not_run", previous=cell.get("previous"))
            f.touch(row)
    elif was and now and isinstance(cell, dict) and cell.get("state") in LIVE:
        before, after = effective_question(q_before, unit_before), effective_question(q_after, unit_after)
        if any(before.get(k) != after.get(k) for k in OUT_OF_DATE_FIELDS):
            f.flag(row, qid, cell)


def _edit_question(f: _Folder, e: dict) -> str | None:
    qid, field, value = e["target_id"], e["field"], e["value"]
    q = f.questions.get(qid)
    if q is None:
        return f"There is no question {qid} in questions.json."
    if field in TEXT_FIELDS:
        if q.get(field) != value:
            q[field] = value
            f.dirty.add("questions")
            if field in OUT_OF_DATE_FIELDS:
                for row, _, cell in f.live_cells(qid):
                    f.flag(row, qid, cell)
    elif field == "levels_points":
        levels = {str(lv.get("value")): lv for lv in (q.get("answer") or {}).get("levels") or []}
        missing = [k for k in value if k not in levels]
        if missing:
            return (f"Question {qid} has no level {', '.join(repr(k) for k in missing)} "
                    f"(its levels are {', '.join(repr(k) for k in levels)}).")
        for key, points in value.items():
            if levels[key].get("points") != points:
                levels[key]["points"] = points
                f.dirty.add("questions")
        for row, _, cell in f.live_cells(qid):
            points = _points_for(q, cell.get("value"))
            if points is not None and cell.get("points") != points:
                cell["points"] = points
                f.recomputed += 1
                f.touch(row)
    elif field == "applies_to":
        before = copy.deepcopy(q)
        if before.get("applies_to") != value:
            q["applies_to"] = value
            f.dirty.add("questions")
            for unit in f.units.values():
                _settle(f, unit, unit, before, q)
    return None


def _edit_unit(f: _Folder, e: dict) -> str | None:
    uid, field, value = e["target_id"], e["field"], e["value"]
    unit = f.units.get(uid)
    if unit is None:
        return f"There is no row {uid} in units.json."
    if field == "name":
        if unit.get("name") != value:
            unit["name"] = value
            f.dirty.add("units")
    elif field == "labels":
        known = f.docs["project"].get("labels") or []
        unknown = [k for k in value if k not in known]
        if unknown:
            return (f"{', '.join(repr(k) for k in unknown)} is not one of the project's labels "
                    f"({', '.join(known) or 'none set'}).")
        before = copy.deepcopy(unit)
        merged = {**(unit.get("labels") or {}), **value}
        if merged != unit.get("labels"):
            unit["labels"] = merged
            f.dirty.add("units")
            for q in f.questions.values():
                _settle(f, before, unit, q, q)
    return None


def _confirm(f: _Folder, e: dict) -> str | None:
    field = e["field"]
    f.docs["project"].setdefault("checklist", {})[field] = "confirmed"
    f.dirty.add("project")
    for item in f.docs[field].get(field, []):
        if isinstance(item, dict):
            item["status"] = "confirmed"
    f.dirty.add(field)
    return None


def _decide(f: _Folder, d: dict, via: str) -> str | None:
    rid, option = d["rule"], d.get("option")
    rule = f.rules.get(rid)
    if rule is None:
        return f"There is no rule {rid} in rules.json."
    if d.get("skip") is True:
        if rule.get("status") == "open":             # decided or already skipped: nothing changes
            rule["status"] = "skipped"
            f.dirty.add("rules")
        return None
    options = {o.get("id"): o.get("text") for o in rule.get("options") or [] if isinstance(o, dict)}
    if option is not None and option not in options:
        return f"Rule {rid} has no option {option!r} (its options are {', '.join(map(repr, options))})."
    decision = {"option": option, "text": d.get("text") or options.get(option), "at": d["at"], "via": via}
    old = rule.get("decision")
    if rule.get("status") == "decided" and isinstance(old, dict) \
            and (old.get("option"), old.get("text")) == (option, decision["text"]):
        return None                                  # the same decision again: nothing changes
    rule["status"], rule["decision"] = "decided", decision
    f.dirty.add("rules")
    for row, qid, cell in f.live_cells():
        if rid in (cell.get("rules") or []):
            f.flag(row, qid, cell)
            # The cell no longer rests on Claude's own reading once its rules are settled (ChickenTrack,
            # 15 Oct 2026: 'judgement 32' after every rule was decided).
            still_open = any((f.rules.get(r) or {}).get("status") in ("open", "skipped") for r in cell.get("rules") or [])
            if cell.get("judgement") is True and not still_open:
                cell["judgement"] = False
                f.touch(row)
    return None


def apply(project, payload: dict, now: datetime, via: str = "viewer") -> dict:
    """Apply a payload of viewer (or chat) edits and decisions to the project folder.

    `via` is "viewer" or "chat"; it is recorded on decisions. Entries whose id is already in
    `edits-applied.json` are skipped, as are entries that are malformed or aim at something that is
    not there; each skip comes with a reason and nothing about it is recorded, so it can be
    retried. Edits go in order of their `at` time (the later one wins); an entry with no `at` gets
    `now`. Raises HandbookError for a bad `via` or a payload that is not `{"edits": [...],
    "decisions": [...]}`.

    Returns `{"applied": [ids], "skipped": [{"id", "why"}], "out_of_date": n, "recomputed": n,
    "now_not_run": n, "now_not_applicable": n}`:
    - out_of_date: answers marked out of date by this payload
    - recomputed: answers whose stored points changed after a levels_points edit
    - now_not_run: rows that now have a question they did not have, so still need scoring
    - now_not_applicable: scored answers set aside because they no longer apply
    """
    if via not in VIA:
        raise HandbookError(f"via must be one of {', '.join(VIA)}, not {via!r}.")
    if not isinstance(payload, dict):
        raise HandbookError("The edits file must hold a JSON object with 'edits' and 'decisions' lists.")
    entries = {}
    for key in ("edits", "decisions"):
        entries[key] = payload.get(key, [])
        if not isinstance(entries[key], list):
            raise HandbookError(f"'{key}' in the edits file must be a list.")
    stamp = _stamp(now)

    def prepared(items):
        out = [copy.deepcopy(i) for i in items]
        for item in out:
            if isinstance(item, dict) and item.get("at") is None:
                item["at"] = stamp
        return sorted(out, key=lambda i: str(i.get("at", "")) if isinstance(i, dict) else "")

    applied_doc = project.read("edits-applied")
    done = list(applied_doc.get("applied", []))
    folder = _Folder(project)
    applied, skipped = [], []

    def run(entry, key, do):
        eid = entry.get("id") if isinstance(entry, dict) else None
        if eid in done:
            skipped.append({"id": eid, "why": "Already applied earlier."})
            return
        problems = check_edits({key: [entry]}) if isinstance(entry, dict) else ["not an object"]
        why = problems[0] if problems else do(entry)
        if why:
            skipped.append({"id": eid, "why": why})
        else:
            applied.append(eid)
            done.append(eid)

    handlers = {"question": _edit_question, "unit": _edit_unit, "confirm": _confirm}
    for e in prepared(entries["edits"]):
        run(e, "edits", lambda e: handlers[e["target"]](folder, e))
    for d in prepared(entries["decisions"]):
        run(d, "decisions", lambda d: _decide(folder, d, via))

    if applied:
        folder.save()
        applied_doc["applied"] = done
        project.write("edits-applied", applied_doc)
    return {"applied": applied, "skipped": skipped, "out_of_date": len(folder.flagged),
            "recomputed": folder.recomputed, "now_not_run": folder.now_not_run,
            "now_not_applicable": folder.set_aside}

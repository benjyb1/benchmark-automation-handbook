"""The set-up chat's writes: one part of the set-up at a time, checked before it is kept.

The Set-up tab's chat (viewer/js/views/setupchat.js) runs on the owner's Claude plan in the page and
calls `POST /api/setup` (serve.py) for each change, so the right-hand side fills in as Claude works,
as it did in the demo. Every write here:
- touches only its own file (or, for `basics` and `provided`, only those fields of project.json);
- marks what it wrote as a draft: the owner confirms each part in the chat, or on the page;
- runs the whole-folder check before and after, and puts the old file back if the write brought
  in a problem, returning the problems so Claude can fix its draft and try again.

Questions and the list of rows cannot be replaced once answers are scored against them: single
questions and rows are then changed with the Set-up tab's Edit buttons (apply-edits), which mark
the answers out of date.

`describe(project)` is what the chat reads back: the set-up as it stands, kept short.
"""
from __future__ import annotations
import json

from pathlib import Path

from .folder import CHECKLIST_KEYS, CHECKLIST_STATUSES, HandbookError, Project
from .shapes import ROLES, check_folder
from .stakes import rule_stakes

BASICS = ("name", "edition", "unit_word", "unit_word_plural", "cutoff", "labels", "group_labels", "group_by")
WHOLE = {"questions": "questions", "units": "units", "scoresheet": "scoresheet",
         "where_to_look": "where_to_look", "needs": "needs"}
PARTS = ("basics", "provided", "rules", "feedback") + tuple(WHOLE)
FEEDBACK_FIELDS = ("private_evidence", "sender", "language")    # the business round's three set-up answers
LOCKED_ONCE_SCORED = ("questions", "units")
SETUP_RULE_FIELDS = ("kind", "question_ids", "rows", "ask", "options", "recommended", "why", "last_year_note")


def _scored(project: Project) -> bool:
    return any(project.path.joinpath("answers").glob("*.json"))


def _set_checklist(p: dict, key: str, status: str) -> None:
    checklist = p.setdefault("checklist", {k: "missing" for k in CHECKLIST_KEYS})
    if checklist.get(key) != "confirmed" or status == "confirmed":
        checklist[key] = status


def _checked_write(project: Project, files: dict[str, dict]) -> list[str]:
    """Write `files` ({name: data}); keep them only if the folder check finds nothing new."""
    before = set(check_folder(project))
    old = {name: (project.file(name).read_bytes() if project.file(name).is_file() else None) for name in files}
    for name, data in files.items():
        project.write(name, data)
    new = [p for p in check_folder(project) if p not in before]
    if new:
        for name, raw in old.items():
            if raw is None:
                project.file(name).unlink(missing_ok=True)
            else:
                project.file(name).write_bytes(raw)
    return new


def _write_feedback(project: Project, data: dict) -> dict:
    """The business round's set-up answers (can businesses back up a reply with their own documents, who the packs say
    replies go to, the packs' language). They live in the round's settings, which the Feedback tab's chat
    changes later; saving them marks the questions as asked, so the set-up asks only once."""
    from . import feedback
    unknown = [k for k in data if k not in FEEDBACK_FIELDS]
    if unknown:
        return {"ok": False, "problems": [f"Not part of the business round's set-up: {', '.join(unknown)}. "
                                          f"It is {', '.join(FEEDBACK_FIELDS)}."], "said": None}
    if not data:
        return {"ok": False, "problems": ["feedback needs at least one of: " + ", ".join(FEEDBACK_FIELDS) + "."], "said": None}
    try:
        feedback.set_settings(project, **data, asked_in_setup=True)
    except HandbookError as e:
        return {"ok": False, "problems": [str(e)], "said": None}
    return {"ok": True, "problems": [], "said": "Saved the business round's answers."}


def write_part(project: Project, part: str, data) -> dict:
    """Save one part of the set-up as a draft. Returns {ok, problems, said}."""
    if part not in PARTS:
        raise HandbookError(f"There is no set-up part called {part!r}. Parts: {', '.join(PARTS)}.")
    if part in LOCKED_ONCE_SCORED and _scored(project):
        return {"ok": False, "problems": [f"Answers are already scored against the {part}, so the whole list "
                                          "cannot be replaced. Change single items with the Edit buttons on the "
                                          "Set-up tab instead."], "said": None}
    if not isinstance(data, dict):
        return {"ok": False, "problems": [f"The {part} must be an object."], "said": None}
    if part == "feedback":
        return _write_feedback(project, data)
    p = project.read("project")
    files: dict[str, dict] = {}

    if part == "basics":
        unknown = [k for k in data if k not in BASICS]
        if unknown:
            return {"ok": False, "problems": [f"Not part of the basics: {', '.join(unknown)}. "
                                              f"The basics are {', '.join(BASICS)}."], "said": None}
        p.update(data)
        if "cutoff" in data:
            _set_checklist(p, "cutoff", "draft")
        if "group_labels" in data:
            _set_checklist(p, "group_labels", "draft")
        said = "the basics"
    elif part == "provided":
        items = data.get("files")
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            return {"ok": False, "problems": ["provided needs files: a list of {file, role, note}."], "said": None}
        bad = [x.get("role") for x in items if x.get("role") not in ROLES]
        if bad:
            return {"ok": False, "problems": [f"Unknown role {bad[0]!r}; roles are {', '.join(ROLES)}."], "said": None}
        kept = {x.get("file"): x for x in p.get("provided") or []}
        for x in items:
            kept[x.get("file")] = {"file": x.get("file"), "role": x.get("role"), "note": x.get("note")}
        p["provided"] = list(kept.values())
        said = "the list of files"
    elif part == "rules":
        new = data.get("rules")
        if not isinstance(new, list) or not new:
            return {"ok": False, "problems": ["rules needs rules: a list of open rules to add."], "said": None}
        rules = project.read("rules")
        have = rules.setdefault("rules", [])
        n = max([int(r["id"][1:]) for r in have if str(r.get("id", ""))[1:].isdigit()] or [0])
        added = []
        for r in new:
            n += 1
            rule = {k: r.get(k) for k in SETUP_RULE_FIELDS}
            rule.update({"id": f"R{n}", "rows": r.get("rows"), "stakes": None, "status": "open",
                         "decision": None, "raised_by": "setup"})
            have.append(rule)
            added.append(rule)
        for rule in added:
            try:
                rule["stakes"] = rule_stakes(project, rule)
            except (HandbookError, KeyError, TypeError, ValueError):
                rule["stakes"] = None
        files["rules"] = rules
        _set_checklist(p, "rules", "draft")
        said = f"{len(added)} rule{'s' if len(added) != 1 else ''} ({', '.join(r['id'] for r in added)})"
    else:
        key, name = part, WHOLE[part]
        listed = {"questions": "questions", "units": "units"}.get(part)
        for item in (data.get(listed) or []) if listed else []:
            if isinstance(item, dict):
                item["status"] = "draft"
        if part == "scoresheet":
            data["status"] = "draft"
        files[name] = data
        _set_checklist(p, key, "draft")
        said = {"questions": "the questions", "units": "the list", "scoresheet": "the score sheet",
                "where_to_look": "where to look", "needs": "the data you need to supply"}[part]
    files["project"] = p
    problems = _checked_write(project, files)
    return {"ok": not problems, "problems": problems[:20], "said": None if problems else f"Saved {said} as a draft."}


def set_status(project: Project, key: str, status: str) -> dict:
    """The owner confirmed (or skipped) one checklist item in the chat or on the page."""
    if key not in CHECKLIST_KEYS:
        raise HandbookError(f"There is no checklist item called {key!r}.")
    if status not in CHECKLIST_STATUSES or status == "missing":
        raise HandbookError(f"A checklist item can be set to draft, confirmed, skipped or not_needed, not {status!r}.")
    p = project.read("project")
    files: dict[str, dict] = {}
    if status == "confirmed" and key in ("questions", "units"):
        d = project.read(key)
        for item in d.get(key) or []:
            item["status"] = "confirmed"
        files[key] = d
    if status == "confirmed" and key == "scoresheet":
        d = project.read("scoresheet")
        if d:
            d["status"] = "confirmed"
            files["scoresheet"] = d
    _set_checklist(p, key, status)
    files["project"] = p
    problems = _checked_write(project, files)
    return {"ok": not problems, "problems": problems[:20],
            "said": None if problems else f"{key.replace('_', ' ').capitalize()}: {status.replace('_', ' ')}."}


def _feedback_answers(project: Project) -> dict:
    """The business round's set-up answers as they stand, and whether the owner has been asked yet."""
    from . import feedback
    s = feedback.read_settings(project)
    return {"asked": bool(s["asked_in_setup"]), "private_evidence": s["private_evidence"], "sender": s["sender"],
            "language": s["language"] or project.read("project").get("language") or "the benchmark's own language (English if none is set)"}


def describe(project: Project, limit: int = 24000) -> dict:
    """The set-up as it stands, for the chat: whole small parts, the long lists cut short."""
    p = project.read("project")
    qs = project.read("questions").get("questions") or []
    units = project.read("units").get("units") or []
    rules = project.read("rules").get("rules") or []
    out = {
        "basics": {k: p.get(k) for k in BASICS},
        "stage": p.get("stage"),
        "checklist": p.get("checklist") or {},
        "provided": p.get("provided") or [],
        "answers_scored": _scored(project),
        "feedback": _feedback_answers(project),
        "questions": qs,
        "units": {"count": len(units), "first": units[:8]},
        "scoresheet": project.read("scoresheet"),
        "where_to_look": project.read("where_to_look"),
        "needs": project.read("needs"),
        "rules": [{k: r.get(k) for k in ("id", "kind", "question_ids", "ask", "options", "recommended", "status",
                                          "decision", "stakes")} for r in rules],
    }
    text = json.dumps(out, ensure_ascii=False)
    if len(text) > limit:   # long question wording: keep the ids and short names, drop the levels
        out["questions"] = [{k: q.get(k) for k in ("id", "short", "section", "status")} for q in qs]
        out["questions_cut"] = "Levels and wording left out for length: the Set-up tab shows them."
    return out


SKILL = Path(__file__).resolve().parents[2] / "skills" / "setup"


def guide() -> str:
    """The file formats (drafting.md, from project.json to where_to_look.json and needs.json) and how to
    write a rule (grill.md), for the chat's instructions."""
    text = (SKILL / "drafting.md").read_text(encoding="utf-8")
    start, end = text.find("## project.json"), text.find("## Last edition's results")
    formats = text[start:end if end > start else None].strip() if start >= 0 else text
    grill = (SKILL / "grill.md").read_text(encoding="utf-8") if (SKILL / "grill.md").is_file() else ""
    return formats + ("\n\n" + grill.strip() if grill else "")

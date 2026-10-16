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

from . import edition
from .folder import CHECKLIST_KEYS, CHECKLIST_STATUSES, HandbookError, Project
from .shapes import ROLES, check_folder
from .stakes import rule_stakes

BASICS = ("name", "edition", "unit_word", "unit_word_plural", "cutoff", "labels", "group_labels", "group_by",
          "results_sites")
WHOLE = {"questions": "questions", "units": "units", "scoresheet": "scoresheet",
         "where_to_look": "where_to_look", "needs": "needs"}
PARTS = ("basics", "provided", "rules", "last_results", "feedback") + tuple(WHOLE)    # "feedback": the Full edition only
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
    feedback = edition.optional("feedback")
    if feedback is None:
        return {"ok": False, "problems": [edition.in_full("The round with the organisations you score (packs out, "
                                                           "replies in)")], "said": None}
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
    if part == "feedback" and not edition.has_feedback():
        return _write_feedback(project, {})
    parts = PARTS if edition.has_feedback() else tuple(p for p in PARTS if p != "feedback")
    if part not in parts:
        raise HandbookError(f"There is no set-up part called {part!r}. Parts: {', '.join(parts)}.")
    if part in LOCKED_ONCE_SCORED and _scored(project):
        return {"ok": False, "problems": [f"Answers are already scored against the {part}, so the whole list "
                                          "cannot be replaced. Change single items with the Edit buttons on the "
                                          "Set-up tab instead."], "said": None}
    if not isinstance(data, dict):
        return {"ok": False, "problems": [f"The {part} must be an object."], "said": None}
    if part == "last_results":
        return _last_results(project, data)
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


def _last_results(project: Project, data: dict) -> dict:
    """Last edition's results, normalised by the chat ({file, results}): held back as `hb hold-back` does,
    with the owner's file (named as Your material lists it) kept as the original and taken out of Your
    material, then rebuilt with the score sheet when it can compute totals. Returns {ok, problems, said,
    rebuild}, rebuild being {checked, matched, mismatches} or None."""
    from . import material
    from .cmd_hold_back import store
    from .folder import write_json
    from .scoresheet import rebuild_check
    name = str(data.get("file") or "").strip()
    path = next((f for f in material.files(project)
                 if name and name in (f.name, material.name_of(f), f.name.split("--")[0])), None)
    if path is None:
        return {"ok": False, "problems": [f"There is no file {name!r} in Your material: give the file's name as "
                                          "read_material lists it."], "said": None, "rebuild": None}
    results = data.get("results")
    if not isinstance(results, dict):
        return {"ok": False, "problems": ["results must be the normalised results, in the held-back shape."],
                "said": None, "rebuild": None}
    from .cmd_hold_back import check_against
    try:
        text = material.read_text(path)
    except Exception:  # noqa: BLE001 (a file the reader cannot read is simply not checked)
        text = ""
    try:
        store(project, results, path)
    except HandbookError as e:
        lines = str(e).split("\n- ")
        return {"ok": False, "problems": lines[1:] or lines, "said": None, "rebuild": None}
    rows = results.get("rows") or {}
    units = {u.get("id") for u in project.read("units").get("units", [])}
    unknown = [r for r in rows if r not in units]
    said = (f"Kept last edition's results aside: {len(rows)} rows at {results.get('level')} level"
            + (f" ({len(unknown)} not in the list: {', '.join(unknown[:5])})" if unknown else "") + ".")
    check = check_against(project, results, text) if text else []
    if check:
        said += (f" Check {len(check)} row{'s' if len(check) != 1 else ''} against the file: "
                 + "; ".join(f"{c['row']} {c['key']} kept as {c['held']}"
                             + (f", the file has {c['near']} beside it" if c["near"] is not None else "")
                             for c in check[:8]) + ".")
    rebuild = None
    sheet = project.read("scoresheet")
    if sheet and sheet.get("computable") is not False and project.read("questions").get("questions"):
        try:
            rebuild = rebuild_check(project)
            write_json(project.held_back / "rebuild.json", rebuild)
            rebuild = {k: rebuild[k] for k in ("checked", "matched", "mismatches")}
            rebuild["mismatches"] = rebuild["mismatches"][:30]
            if rebuild["checked"]:
                said += f" The score sheet gives the same figures for {rebuild['matched']} of {rebuild['checked']} rows."
            else:
                said += (f" They are {results.get('level')}s only, with nothing finer to rebuild them from, so the score "
                         "sheet cannot be checked against them yet; scored rows will be compared with them.")
        except HandbookError as e:
            said += f" The score sheet could not be checked against them: {e}"
    return {"ok": True, "problems": [], "said": said, "rebuild": rebuild, "check": check}


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


def _feedback_answers(project: Project) -> dict | None:
    """The business round's set-up answers as they stand, and whether the owner has been asked yet (None in
    the free edition, which has no such round)."""
    feedback = edition.optional("feedback")
    if feedback is None:
        return None
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
        **({"feedback": _feedback_answers(project)} if edition.has_feedback() else {}),
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
    """The file formats (drafting.md, from project.json to last edition's results) and how to write a
    rule (grill.md), for the chat's instructions."""
    text = (SKILL / "drafting.md").read_text(encoding="utf-8")
    start, end = text.find("## project.json"), text.find("## Chat edits")
    formats = text[start:end if end > start else None].strip() if start >= 0 else text
    grill = (SKILL / "grill.md").read_text(encoding="utf-8") if (SKILL / "grill.md").is_file() else ""
    return formats + ("\n\n" + grill.strip() if grill else "")

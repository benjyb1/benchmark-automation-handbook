"""Run control: `control.json` in the project folder. The owner changes it from chat (`hb control
set`) or from the viewer's run bar (requests the host hands to `hb control apply`); the run skill
reads it before every row and between phases, and `todo` lists nothing outside its scope.

    {"paused": false, "stop_now": false, "stop_after_row": null,
     "scope": {"rows": null, "questions": null}, "stop_after": null,
     "applied": ["v-1", "c-2"], "updated": "2026-10-08T12:00:00Z"}

- `paused`, `stop_now`: stop before the next row. `resume` clears both, and `stop_after_row`.
- `stop_after_row`: stop once this row is finished.
- `scope.rows`, `scope.questions`: what to run; null means all. Kept in file order.
- `stop_after`: null, "find" or "score": stop once documents are found for every row in scope, or
  once those rows are scored. The results always wait for the owner.
- `applied`: ids of requests already handled (viewer ids, and `c-` ids from chat), so each
  request is handled once. A skipped request is recorded too: the reason is printed for the owner,
  and the viewer stops waiting for it.

A request: {"id", "action": "pause" | "resume" | "stop_now" | "stop_after_row" | "scope" |
"stop_after" | "rerun" | "rerun_done", "row", "rows", "questions", "stage", "rule", "rerun", "by", "at"}.
Only the keys its action needs are read. A scope request changes only the halves it names (`rows`,
`questions`; null = all). A rerun request (the owner asks for answers to be scored again: those
resting on a rule after deciding it, or any rows x questions from the corner chat, with `rule` null
and `why` saying what for) is kept in `reruns` as {id, rule, rows, questions, why} until the run
skill or the page's server has scored them and sends rerun_done with that id.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone

from .folder import DEFAULTS, HandbookError, Project
from .shapes import CONTROL_STOP_AFTER as STOP_AFTER

ACTIONS = ("pause", "resume", "stop_now", "stop_after_row", "scope", "stop_after", "rerun", "rerun_done")
LIST_AT_MOST = 10        # rows or questions named in a sentence before "and N more"


def _stamp(now: datetime | None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def empty() -> dict:
    """The control a project starts with: nothing paused, no stops, everything in scope."""
    return copy.deepcopy(DEFAULTS["control"])


def _ids(values) -> list | None:
    if values is None or not isinstance(values, list):
        return None
    return [v for v in values if isinstance(v, str)] or None


def read(project: Project) -> dict:
    """control.json with every key present (a missing file reads as `empty()`). Values of the
    wrong kind read as their default; `hb validate` names them. Raises HandbookError when the
    file is not a JSON object."""
    data = project.read("control")
    if not isinstance(data, dict):
        raise HandbookError("control.json must hold a JSON object. Fix or delete it; deleting it "
                            "means nothing is paused, no stop is set and everything is in scope.")
    c = empty()
    c["paused"] = data.get("paused") is True
    c["stop_now"] = data.get("stop_now") is True
    row = data.get("stop_after_row")
    c["stop_after_row"] = row if isinstance(row, str) and row else None
    scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    c["scope"] = {"rows": _ids(scope.get("rows")), "questions": _ids(scope.get("questions"))}
    c["stop_after"] = data.get("stop_after") if data.get("stop_after") in STOP_AFTER else None
    c["reruns"] = [x for x in data.get("reruns") or [] if isinstance(x, dict)] \
        if isinstance(data.get("reruns"), list) else []
    c["applied"] = [x for x in data.get("applied") or [] if isinstance(x, str)] \
        if isinstance(data.get("applied"), list) else []
    c["updated"] = data.get("updated") if isinstance(data.get("updated"), str) else None
    return c


def public(c: dict) -> dict:
    """The control without `applied` (what `status` reports)."""
    return {k: copy.deepcopy(v) for k, v in c.items() if k != "applied"}


def _entries(project: Project, name: str) -> list:
    """The objects listed in units.json or questions.json; a file of the wrong shape gives none
    (`hb validate` names it)."""
    data = project.read(name)
    items = data.get(name) if isinstance(data, dict) else None
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def _project(project: Project) -> dict:
    data = project.read("project")
    return data if isinstance(data, dict) else {}


def _known(project: Project) -> tuple[list, list]:
    return ([u["id"] for u in _entries(project, "units") if isinstance(u.get("id"), str)],
            [q["id"] for q in _entries(project, "questions") if isinstance(q.get("id"), str)])


def _names(ids: list) -> str:
    shown = ", ".join(ids[:LIST_AT_MOST])
    return shown if len(ids) <= LIST_AT_MOST else f"{shown} and {len(ids) - LIST_AT_MOST} more"


def _change(c: dict, request: dict, rows: list, questions: list) -> tuple[str | None, list]:
    """Apply one request to `c` in memory. Returns (why, left_out): `why` is the reason the whole
    request was skipped (None when it applied); `left_out` gives a reason for each part dropped
    (rows or questions a scope named that the project does not have)."""
    action = request.get("action")
    if action == "pause":
        c["paused"] = True
    elif action == "resume":
        c["paused"] = c["stop_now"] = False
        c["stop_after_row"] = None
    elif action == "stop_now":
        c["stop_now"] = True
    elif action == "stop_after_row":
        row = request.get("row")
        if not isinstance(row, str) or row not in rows:
            return f"Row {row!r} is not in units.json, so there is no row to stop after.", []
        c["stop_after_row"] = row
    elif action == "stop_after":
        stage = request.get("stage")
        stage = None if stage == "none" else stage
        if stage is not None and stage not in STOP_AFTER:
            return (f"The stop point must be find, score or none (carry on to the end), "
                    f"not {stage!r}."), []
        c["stop_after"] = stage
    elif action == "scope":
        new, left = {}, []
        for key, known, word, where in (("rows", rows, "row", "units.json"),
                                        ("questions", questions, "question", "questions.json")):
            if key not in request:
                continue
            value = request[key]
            if value is None:
                new[key] = None
                continue
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                return f"The {key} must be a list of ids, or null for all.", []
            if not value:
                return f"Choose at least one {word}, or all of them.", []
            unknown = [x for x in dict.fromkeys(value) if x not in known]
            keep = [x for x in known if x in value]
            if not keep:
                return f"None of the {key} named ({_names(unknown)}) is in {where}.", []
            if unknown:
                left.append(f"{word.capitalize()} {_names(unknown)} "
                            f"{'is' if len(unknown) == 1 else 'are'} not in {where}, so left out.")
            new[key] = keep
        if not new:
            return "A scope request names rows, questions or both (null for all).", []
        c["scope"].update(new)
        return None, left
    elif action == "rerun":
        rule = request.get("rule")
        if rule is not None and not (isinstance(rule, str) and rule):
            return "A re-run's rule is a rule id, or null for answers the owner picked.", []
        why = request.get("why") if isinstance(request.get("why"), str) and request.get("why").strip() else None
        picked = {}
        for key, known, word in (("rows", rows, "row"), ("questions", questions, "question")):
            value = request.get(key)
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value) or not value:
                return f"A re-run lists the {key} to score again.", []
            picked[key] = [x for x in known if x in value]
            if not picked[key]:
                return f"None of the {key} named ({_names(value)}) is in this project.", []
        if rule is not None:
            c["reruns"] = [r for r in c["reruns"] if r.get("rule") != rule]   # the latest asks for that rule
        c["reruns"].append({"id": request.get("id"), "rule": rule, **picked, **({"why": why.strip()} if why else {})})
    elif action == "rerun_done":
        before = len(c["reruns"])
        c["reruns"] = [r for r in c["reruns"] if r.get("id") != request.get("rerun")]
        if len(c["reruns"]) == before:
            return f"There is no waiting re-run {request.get('rerun')!r}.", []
    else:
        return f"Unknown action {action!r}; expected one of {', '.join(ACTIONS)}.", []
    return None, []


def set_control(project: Project, requests: list[dict], now: datetime | None = None) -> dict:
    """Apply requests made in chat, in order, and write control.json. All or nothing: an unknown
    row or question, or a bad value, raises HandbookError and nothing is written. Returns the
    new control."""
    c = read(project)
    rows, questions = _known(project)
    for request in requests:
        why, left = _change(c, request, rows, questions)
        if why or left:
            raise HandbookError(" ".join([why] if why else left) + " Nothing was changed.")
    c["updated"] = _stamp(now)
    project.write("control", c)
    return c


def apply(project: Project, payload, now: datetime | None = None) -> dict:
    """Apply the viewer's control requests (`{"requests": [...]}`, or the bare list) in order of
    `at` (a request with no `at` gets `now`). Ids already in `applied` are passed over and
    counted. Returns {"applied": [ids], "skipped": [{"id", "why"}], "left_out": [{"id", "why"}],
    "already": n, "control": the new control}. Skipped requests are recorded as handled too."""
    items = payload.get("requests") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise HandbookError('The control file must hold {"requests": [...]}, the requests from the '
                            "viewer's control collection.")
    stamp = _stamp(now)
    ordered = sorted((copy.deepcopy(r) for r in items),
                     key=lambda r: str(r.get("at") or stamp) if isinstance(r, dict) else stamp)
    c = read(project)
    rows, questions = _known(project)
    done = set(c["applied"])
    applied, skipped, left_out, already = [], [], [], 0
    for r in ordered:
        rid = r.get("id") if isinstance(r, dict) else None
        if not isinstance(rid, str) or not rid.strip():
            skipped.append({"id": rid if isinstance(rid, str) else None,
                            "why": "The request has no id, so it cannot be recorded."})
            continue
        if rid in done:
            already += 1
            continue
        why, left = _change(c, r, rows, questions)
        if why:
            skipped.append({"id": rid, "why": why})
        else:
            applied.append(rid)
            left_out.extend({"id": rid, "why": x} for x in left)
        c["applied"].append(rid)
        done.add(rid)
    if applied or any(s["id"] for s in skipped):
        c["updated"] = stamp
        project.write("control", c)
    return {"applied": applied, "skipped": skipped, "left_out": left_out, "already": already,
            "control": c}


# --- plain sentences ----------------------------------------------------------------------------

def _row_label(project: Project, row: str) -> str:
    for u in _entries(project, "units"):
        if u.get("id") == row and u.get("name"):
            return f"{u['name']} ({row})"
    return row


def describe(project: Project, c: dict | None = None) -> list[str]:
    """What the control says, as plain sentences (what `hb control` prints)."""
    c = c if c is not None else read(project)
    proj = _project(project)
    plural = proj.get("unit_word_plural") or "rows"
    rows, questions = _known(project)
    out = []
    if c["paused"]:
        out.append("The run is paused: it stops before the next row and waits until you say resume.")
    if c["stop_now"]:
        out.append("Stop now is set: the run stops before the next row starts.")
    if not (c["paused"] or c["stop_now"]):
        out.append("The run is not paused and stop now is not set.")
    if c["stop_after_row"]:
        out.append(f"The run stops after {_row_label(project, c['stop_after_row'])}.")
    scope_rows, scope_qs = c["scope"]["rows"], c["scope"]["questions"]
    which_rows = (f"every one of the {len(rows)} {plural}" if scope_rows is None
                  else f"{len(scope_rows)} of the {len(rows)} {plural} ({_names(scope_rows)})")
    which_qs = ("every question" if scope_qs is None
                else f"{len(scope_qs)} of the {len(questions)} questions ({_names(scope_qs)})")
    out.append(f"It runs {which_rows}, on {which_qs}.")
    if c["stop_after"] == "find":
        out.append("It stops once documents are found for every row in scope, so you can review "
                   "and download the sources before anything is scored.")
    elif c["stop_after"] == "score":
        out.append("It stops once every row in scope is scored.")
    else:
        out.append("It carries on to the end of scoring. The results always wait for you.")
    return out


def stop_text(project: Project, c: dict | None = None) -> str | None:
    """One sentence for `hb status` when the run is paused, a stop is set or the scope is
    narrowed; None otherwise."""
    c = c if c is not None else read(project)
    rows, questions = _known(project)
    plural = _project(project).get("unit_word_plural") or "rows"
    parts = []
    if c["paused"]:
        parts.append("paused")
    if c["stop_now"]:
        parts.append("set to stop now")
    if c["stop_after_row"]:
        parts.append(f"set to stop after {_row_label(project, c['stop_after_row'])}")
    if c["stop_after"] == "find":
        parts.append("set to stop once documents are found")
    elif c["stop_after"] == "score":
        parts.append("set to stop once the rows in scope are scored")
    limits = []
    if c["scope"]["rows"] is not None:
        limits.append(f"{len(c['scope']['rows'])} of {len(rows)} {plural}")
    if c["scope"]["questions"] is not None:
        limits.append(f"{len(c['scope']['questions'])} of {len(questions)} questions")
    if limits:
        parts.append("limited to " + " and ".join(limits))
    if not parts:
        return None
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Run control: the run is {joined}."

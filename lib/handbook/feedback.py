"""The feedback round: what businesses (or countries, or schemes) say about their results, and what
the owner decides about each point.

A charity sends each party its results as an Excel pack (packs.py), the parties reply in the same
file, and the owner drops the returned files into the page (replies.py). This module keeps the
round's state in `<project>/feedback/`:

    state.json      per unit: its stage, its reply files (with the rows read from them), the points
                    raised, the owner's decisions, Claude's reviews and the tokens they cost; plus
                    the files waiting for the owner (unmatched, or covering a whole group)
    settings.json   how the round runs (grouping, how much Claude does, auto-accept, headline ...)
    files/          the reply files and attachments, copied here

A unit has only three timestamps: `pack_made`, `sent` ("marked sent") and `reply_arrived`. Its stage
is one of STAGES. It is reached by what happened (a pack was made, marked sent, a reply was read,
every point decided), except that the owner can set it by hand: nothing is deleted and the unit is
then marked "stage set by hand" until the next real event moves it. Starting a unit over clears its
files and decisions (and puts back the answers it changed).

An accepted change goes into `answers/<row>.json` as the cell's `feedback` block (where it came
from, the owner's reason, the cell as it was) and a `checked` mark; results recalculate on the
next build. Keeping our answer writes the same blocks without changing the value. Every function
takes the project first and an optional `now` (a datetime) so tests can fix the clock. State is
written atomically, as everything in the project folder is.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import answers as answer_kinds
from .folder import HandbookError, Project, read_json, write_json
from .scoresheet import row_totals
from .shapes import FEEDBACK_STAGES, check_answers, is_date

STAGES = FEEDBACK_STAGES
STAGE_WORDS = {"answers_to_check": "Answers to check", "ready_to_send": "Ready to send", "sent": "Sent",
               "returned": "Returned", "needs_review": "Needs review", "reviewed": "Reviewed"}
BY_HAND_WORDS = "stage set by hand"
REPLY_WORDS = ("Agree", "Disagree", "Agree, with new information")

DEFAULT_SETTINGS = {
    "grouping": "none",              # "none" (one pack per unit) or the name of a label ("brand")
    "review": "only_when_asked",     # only_when_asked | check | decide: how much Claude reads replies
    "auto_accept": "off",            # off | no_tier_change | all: accepts Claude's proposals itself
    "reasons": "analyst_note",       # analyst_note | plain: the "why we gave this score" column
    "headline": "overall",           # overall | none | a section id: what tops the pack's first tab
    "sender": "us",                  # who replies go to, as the pack says it
    "language": None,                # the pack's language (None: the project's, else English)
    "private_evidence": None,        # may they attach their own documents (internal reports)? None: not decided
    "reply_by": None,                # a default reply-by date (YYYY-MM-DD)
    "asked_in_setup": False,         # the set-up chat has asked the owner about evidence, sender and language
}
CHOICES = {"review": ("only_when_asked", "check", "decide"),
           "auto_accept": ("off", "no_tier_change", "all"),
           "reasons": ("analyst_note", "plain")}
# What a cell's check flags must not include for the answer to count as checked (the grid's red
# corner: something a person has to look at). "judgement" and "owner_changed" are not in it.
TO_CHECK_FLAGS = ("quote_not_found", "document_changed", "group_differs", "after_cutoff",
                  "reads_disagree", "weak_source")


# --- small helpers ---------------------------------------------------------------------------

def _now(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)


def stamp(now: datetime | None = None) -> str:
    """`now` as an ISO timestamp ending in Z, to the second."""
    return _now(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(now: datetime | None = None) -> str:
    return _now(now).strftime("%Y-%m-%d")


def slug_of(text) -> str:
    """"Ready to send" -> "ready_to_send" (so words and ids are both accepted)."""
    return re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")


def units_by_id(project: Project) -> dict[str, dict]:
    return {u.get("id"): u for u in project.read("units").get("units", []) if isinstance(u, dict)}


def unit_id(project: Project, unit) -> str:
    """The id of a unit given as its id or its name (any capitals); HandbookError when unknown."""
    units = units_by_id(project)
    if unit in units:
        return unit
    named = [i for i, u in units.items() if str(u.get("name") or "").strip().lower() == str(unit).strip().lower()]
    if len(named) == 1:
        return named[0]
    word = project.read("project").get("unit_word") or "unit"
    raise HandbookError(f"There is no {word} {unit!r}" + (f" ({len(named)} share that name)." if named else "."))


def unit_word(project: Project) -> str:
    return project.read("project").get("unit_word") or "unit"


def answer_fingerprint(cell) -> str:
    """A short fingerprint of an answer as a party sees it (state, value, points, evidence), so a
    reply to an older pack can be told from one to the current answers."""
    cell = cell if isinstance(cell, dict) else {}
    body = {"state": cell.get("state"), "value": cell.get("value"), "points": cell.get("points"),
            "evidence": [[e.get("doc"), e.get("page"), e.get("quote")] for e in cell.get("evidence") or []
                         if isinstance(e, dict)]}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


# --- settings ----------------------------------------------------------------------------------

def settings_file(project: Project) -> Path:
    return project.file("feedback/settings")


def read_settings(project: Project) -> dict:
    """The round's settings: the defaults, with whatever settings.json changes. A round marked
    `sample` (invented replies, made by handbook/dev/fixture_feedback.py for a demo) also carries
    `sample: True`; the key is absent otherwise. It is not one of DEFAULT_SETTINGS, so no command
    or page route can set it."""
    saved = read_json(settings_file(project), {}) or {}
    out = {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}
    if saved.get("sample") is True:
        out["sample"] = True
    return out


def set_sample(project: Project, on: bool = True) -> dict:
    """Mark the round as a sample (or take the mark off). For the dev script that builds a sample
    round only: owners cannot reach this, and `set_settings` refuses a `sample` change."""
    out = read_settings(project)
    out.pop("sample", None)
    if on:
        out["sample"] = True
    write_json(settings_file(project), out)
    return out


def _bool(value, key):
    if isinstance(value, bool) or value is None:
        return value
    word = str(value).strip().lower()
    if word in ("yes", "true", "on", "1"):
        return True
    if word in ("no", "false", "off", "0"):
        return False
    if word in ("none", "undecided", "not decided"):
        return None
    raise HandbookError(f"{key} must be yes or no, not {value!r}.")


def set_settings(project: Project, **changes) -> dict:
    """Change settings (any of DEFAULT_SETTINGS) and return all of them. A bad value raises
    HandbookError naming the choices; nothing is written then."""
    unknown = sorted(set(changes) - set(DEFAULT_SETTINGS))
    if unknown:
        raise HandbookError(f"There is no setting {unknown[0]!r}. Settings: {', '.join(DEFAULT_SETTINGS)}.")
    out = read_settings(project)
    proj = project.read("project")
    for key, value in changes.items():
        if key in CHOICES:
            value = slug_of(value)
            if value not in CHOICES[key]:
                raise HandbookError(f"{key} can be {', '.join(CHOICES[key])}, not {value!r}.")
        elif key == "grouping":
            labels = list(proj.get("labels") or [])
            if value is None or slug_of(value) in ("", "none", "unit", "units", "off"):
                value = "none"
            elif value not in labels:
                raise HandbookError(f"Packs can be grouped by none or by one of the labels: "
                                    f"{', '.join(labels) or '(this project has no labels)'}. Not {value!r}.")
        elif key == "headline":
            sections = [s.get("id") for s in project.read("scoresheet").get("sections") or []]
            word = slug_of(value)
            if word in ("overall", "none"):
                value = word
            elif value not in sections:
                raise HandbookError(f"The headline can be overall, none or a section ({', '.join(map(str, sections))}), "
                                    f"not {value!r}.")
        elif key == "sender":
            if not isinstance(value, str) or not value.strip():
                raise HandbookError("The sender needs a name, for example 'the review team'.")
            value = value.strip()
        elif key in ("private_evidence", "asked_in_setup"):
            value = _bool(value, key)
        elif key == "reply_by":
            if value in (None, "", "none"):
                value = None
            elif not is_date(value):
                raise HandbookError(f"reply_by must be a date like 2026-11-13, not {value!r}.")
        elif key == "language":
            value = str(value).strip().lower() if value else None
            if value is not None and not re.fullmatch(r"[A-Za-z][A-Za-z -]{1,39}", value):
                raise HandbookError(f"The language needs a name or a code such as Danish or da, not {value!r}.")
        out[key] = value
    write_json(settings_file(project), out)
    return out


# --- state ---------------------------------------------------------------------------------------

def state_file(project: Project) -> Path:
    return project.file("feedback/state")


def empty_state() -> dict:
    return {"version": 1, "units": {}, "unmatched": [], "groups": [], "next_file": 1}


def new_unit() -> dict:
    return {"stage": None, "by_hand": False, "pack_made": None, "sent": None, "reply_arrived": None,
            "pack": None, "files": [], "points": [], "decisions": {}, "reviews": {}, "docs": [],
            "tokens": {"calls": 0, "processed": 0, "fresh": 0, "cache_read": 0, "output": 0}}


def read_state(project: Project) -> dict:
    saved = read_json(state_file(project), None)
    state = empty_state()
    if isinstance(saved, dict):
        state.update(saved)
    return state


def write_state(project: Project, state: dict) -> None:
    write_json(state_file(project), state)


def has_state(project: Project) -> bool:
    return state_file(project).is_file()


def unit_rec(state: dict, uid: str) -> dict:
    """The unit's record in `state`, created (with every key) when it has none yet."""
    rec = state["units"].setdefault(uid, new_unit())
    for key, value in new_unit().items():
        rec.setdefault(key, copy.deepcopy(value))
    return rec


def next_file_id(state: dict) -> str:
    n = int(state.get("next_file") or 1)
    state["next_file"] = n + 1
    return f"f{n}"


# --- files on disk -------------------------------------------------------------------------------

def safe_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", str(name)).strip(" .") or "file"
    return name[:120]


def files_dir(project: Project) -> Path:
    return project.path / "feedback" / "files"


def store_copy(project: Project, src: Path, folder: str, fid: str, name: str) -> str:
    """Copy `src` to feedback/files/<folder>/<fid>-<name>; the path relative to the project."""
    dest = files_dir(project) / safe_name(folder) / f"{fid}-{safe_name(name)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest.relative_to(project.path).as_posix()


def delete_stored(project: Project, rel: str | None) -> None:
    if not rel:
        return
    path = project.path / rel
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()               # only when empty
    except OSError:
        pass


def file_label(rec: dict) -> str:
    return rec.get("name") or rec.get("id") or "a file"


# --- which answers still need a person ----------------------------------------------------------

def unchecked_questions(answer_cells: dict, check_cells: dict, qids) -> list[str]:
    """The questions whose answer still needs checking before it goes out: no answer yet, or a
    flag the grid shows as a red corner, or a doubt Claude raised that nobody has handled, unless
    the owner (or a feedback decision) has marked it checked."""
    out = []
    for q in qids:
        cell = (answer_cells or {}).get(q)
        if not isinstance(cell, dict) or cell.get("state", "not_run") == "not_run":
            out.append(q)
            continue
        if cell.get("state") == "not_applicable" or cell.get("checked"):
            continue
        flags = ((check_cells or {}).get(q) or {}).get("flags") or []
        unsure = cell.get("unsure")
        if any(f in TO_CHECK_FLAGS for f in flags) or (isinstance(unsure, dict) and not unsure.get("handled")):
            out.append(q)
    return out


def unit_unchecked(project: Project, uid: str, unit: dict | None = None) -> list[str]:
    unit = unit or units_by_id(project)[uid]
    qids = [q.get("id") for q in project.questions_for(unit)]
    return unchecked_questions(project.answers(uid).get("cells") or {}, project.checks(uid).get("cells") or {}, qids)


def stage_of(rec: dict, unchecked: int) -> str:
    """The unit's stage: the one reached by what happened, or set by hand; before anything
    happened, answers to check while some answer needs checking, else ready to send."""
    return rec.get("stage") or ("answers_to_check" if unchecked else "ready_to_send")


def mark_checked(project: Project, unit, questions=None, now: datetime | None = None) -> list[str]:
    """Mark answers as checked by the owner (all of the unit's that need it, or the given
    questions). Returns the question ids marked."""
    uid = unit_id(project, unit)
    answers = project.answers(uid)
    cells = answers.setdefault("cells", {})
    todo = unit_unchecked(project, uid) if questions is None else list(questions)
    done = []
    for q in todo:
        if isinstance(cells.get(q), dict) and cells[q].get("state") not in (None, "not_run"):
            cells[q]["checked"] = {"at": stamp(now), "how": "owner"}
            done.append(q)
    if done:
        answers["row"] = uid
        _write_answers(project, uid, answers)
    return done


def _write_answers(project: Project, uid: str, answers: dict) -> None:
    problems = check_answers(answers, project.read("questions"))
    if problems:
        raise HandbookError(f"Nothing was changed for {uid}: the answers file would have "
                            f"{len(problems)} problem(s): " + "; ".join(problems[:3]))
    project.write_answers(uid, answers)


# --- stages ------------------------------------------------------------------------------------

def set_stage(project: Project, unit, stage, now: datetime | None = None) -> dict:
    """Set a unit's stage by hand. Nothing is deleted; the unit is marked "stage set by hand"
    until the next real event (a pack, marked sent, a reply, a decision) moves it."""
    uid = unit_id(project, unit)
    wanted = slug_of(stage)
    if wanted not in STAGES:
        raise HandbookError(f"A stage can be {', '.join(STAGE_WORDS[s] for s in STAGES)}, not {stage!r}.")
    state = read_state(project)
    rec = unit_rec(state, uid)
    rec["stage"], rec["by_hand"] = wanted, True
    write_state(project, state)
    return {"unit": uid, "stage": wanted, "by_hand": True}


def record_packs(project: Project, made: list[dict], now: datetime | None = None) -> None:
    """Record packs just written: `made` is [{"units": [ids], "file": relative path, "group": None
    or {"label", "value"}}]. Each unit gets "pack made" now and moves to ready to send, unless its
    reply is already back (then only the pack is recorded)."""
    state = read_state(project)
    for item in made:
        for uid in item["units"]:
            rec = unit_rec(state, uid)
            rec["pack_made"] = stamp(now)
            rec["pack"] = {"file": item["file"], "group": item.get("group")}
            if rec.get("stage") in (None, "answers_to_check", "ready_to_send", "sent"):
                rec["stage"], rec["by_hand"] = "ready_to_send", False
    write_state(project, state)


def mark_sent(project: Project, units, now: datetime | None = None) -> list[str]:
    """Mark units as sent: records the time and moves each to Sent (a unit whose reply is already
    back keeps its stage). Returns the unit ids."""
    ids = [unit_id(project, u) for u in ([units] if isinstance(units, str) else units)]
    if not ids:
        raise HandbookError("Say which units to mark as sent.")
    state = read_state(project)
    for uid in ids:
        rec = unit_rec(state, uid)
        rec["sent"] = stamp(now)
        if rec.get("stage") in (None, "answers_to_check", "ready_to_send", "sent"):
            rec["stage"], rec["by_hand"] = "sent", False
    write_state(project, state)
    return ids


# --- points ---------------------------------------------------------------------------------------

def _same_say(a: dict, b: dict) -> bool:
    return all((a.get(k) or "").strip() == (b.get(k) or "").strip() for k in ("reply", "why", "link", "where"))


def build_points(rec: dict) -> list[dict]:
    """The points raised for a unit, from its reply files' rows (in arrival order), with its
    decisions and reviews. Two files that say different things about one question keep both
    (`versions`, `conflict`); the newest is the point's own words."""
    points: dict[str, dict] = {}
    for f in rec["files"]:
        if f.get("kind") != "reply":
            continue
        for row in f.get("rows") or []:
            q = row["q"]
            say = {k: row.get(k) or "" for k in ("reply", "why", "link", "where")}
            version = {**say, "file": f["id"]}
            point = points.get(q)
            if point is None:
                points[q] = {"q": q, **say, "file": f["id"], "files": [f["id"]],
                             "our_value": row.get("our_value"), "changed_since": bool(row.get("changed_since")),
                             "versions": [version], "conflict": False}
                continue
            point["changed_since"] = point["changed_since"] or bool(row.get("changed_since"))
            if f["id"] not in point["files"]:
                point["files"].append(f["id"])
            if _same_say(point["versions"][-1], version):
                continue
            point["versions"].append(version)
            point["conflict"] = True
            point.update(say)
            point["file"] = f["id"]
    attachments = any(f.get("kind") == "attachment" for f in rec["files"])
    for q, point in points.items():
        decision = rec["decisions"].get(q)
        point["decision"] = decision
        point["review"] = rec["reviews"].get(q)
        point["newer_reply"] = bool(decision and len(point["versions"]) > int(decision.get("versions") or 1))
        point["attachments"] = attachments
    return list(points.values())


def refresh_points(rec: dict) -> None:
    rec["points"] = build_points(rec)


def settle(rec: dict) -> None:
    """Move a unit's stage after a reply was read or a decision made: needs review while a point
    has no decision, reviewed once all have (or the reply raised none); returned when only a file
    without a reply sheet came back."""
    refresh_points(rec)
    reply_files = [f for f in rec["files"] if f.get("kind") == "reply"]
    if reply_files:
        open_points = [p for p in rec["points"] if not p["decision"] or p["newer_reply"]]
        rec["stage"] = "needs_review" if open_points else "reviewed"
    elif rec["files"]:
        rec["stage"] = "returned"
    else:
        rec["stage"] = "sent" if rec.get("sent") else ("ready_to_send" if rec.get("pack_made") else None)
    rec["by_hand"] = False


def annotate_rows(project: Project, uid: str, rows: list[dict], pack: dict | None) -> None:
    """Put on each reply row what the pack told the party (`our_value`) and whether the answer has
    changed since (`changed_since`), when the pack was this unit's own. Otherwise our_value is the
    answer now and nothing counts as changed."""
    cells = project.answers(uid).get("cells") or {}
    mine = bool(pack) and pack.get("unit") == uid
    for row in rows:
        cell = cells.get(row["q"])
        if mine:
            row["our_value"] = (pack.get("values") or {}).get(row["q"])
            was = (pack.get("answers") or {}).get(row["q"])
            row["changed_since"] = bool(was) and was != answer_fingerprint(cell)
        else:
            row["our_value"] = (cell or {}).get("value")
            row["changed_since"] = False


# --- decisions -----------------------------------------------------------------------------------

def _level(question: dict, value):
    levels = (question.get("answer") or {}).get("levels") or []
    for lv in levels:
        if lv.get("value") == value and type(lv.get("value")) is type(value):
            return lv
    for lv in levels:
        if str(lv.get("value")) == str(value):
            return lv
    if answer_kinds.is_number(question):
        # A figure, from the page as typed ("73") or as a number: it has no level, and adds no points.
        figure = value
        if isinstance(value, str):
            try:
                figure = float(value.strip().rstrip("%").replace(",", ""))
            except ValueError:
                return None
            figure = int(figure) if figure.is_integer() else figure
        if answer_kinds.figure_ok(question, figure):
            return {"value": figure, "points": 0, "meaning": None}
    return None


def _totals(project: Project, uid: str) -> dict:
    t = row_totals(project, uid, project.answers(uid).get("cells") or {})
    return {"overall": t["overall"], "band": (t.get("bands") or {}).get("overall")} if t else {}


def _revert_cell(cells: dict, qid: str, decision: dict | None) -> bool:
    """Undo what a decision wrote into the cell: put the old cell back (a change) or take off the
    feedback and checked marks (a keep). Left alone when the cell has moved on since."""
    cell = cells.get(qid)
    fb = (cell or {}).get("feedback") if isinstance(cell, dict) else None
    if not isinstance(fb, dict):
        return False
    if fb.get("kept"):
        cell.pop("feedback", None)
        if fb.get("was_checked"):
            cell["checked"] = fb["was_checked"]
        else:
            cell.pop("checked", None)
        return True
    if decision and cell.get("value") != decision.get("value"):
        return False
    previous = fb.get("previous")
    if previous is None:
        del cells[qid]
    else:
        cells[qid] = previous
    return True


def _revert_in(cells: dict, rec: dict, qids) -> list[str]:
    """Take back the decisions on `qids` in memory: cells put back where they were, decisions
    dropped. Returns the questions whose answer was put back."""
    put_back = []
    for q in list(qids):
        decision = rec["decisions"].pop(q, None)
        if decision and _revert_cell(cells, q, decision):
            put_back.append(q)
    return put_back


def _revert_decisions(project: Project, uid: str, rec: dict, qids) -> list[str]:
    """Take back the decisions on `qids` and write the answers that were put back."""
    answers = project.answers(uid)
    cells = answers.setdefault("cells", {})
    put_back = _revert_in(cells, rec, qids)
    if put_back:
        answers["row"] = uid
        _write_answers(project, uid, answers)
    return put_back


def _file_attachments(project: Project, uid: str, rec: dict) -> list[str]:
    """File the unit's attachments as project documents (once each); returns the document ids."""
    from .cmd_add_document import add_file
    from .fetch import title_from_name
    unit = units_by_id(project)[uid]
    docs = []
    for f in rec["files"]:
        if f.get("kind") != "attachment" or f.get("doc") or f.get("doc_error"):
            continue
        notes = []
        try:
            f["doc"] = add_file(project, project.path / f["path"], [uid],
                                f"{unit.get('name')}: {title_from_name(f['name']) or f['name']}", say=notes.append)
            docs.append(f["doc"])
            rec["docs"].append(f["doc"])
        except HandbookError as e:
            f["doc_error"] = str(e)
    return docs


def decide(project: Project, unit, question: str, action: str, value=None, reason: str = "",
           now: datetime | None = None, auto: bool = False) -> dict:
    """Decide one point: `action` is "keep" (our answer stands) or "change" (to `value`, one of
    the question's levels). The decision, the owner's `reason` (kept to go back to the party), and
    for a change the new answer with where it came from, are recorded; the answer counts as
    checked. Deciding a point again takes the earlier decision back first. Attachments from the
    unit's reply become project documents when a change is accepted. Results recalculate on the
    next build. Returns what happened, with the unit's overall score and tier before and after."""
    uid = unit_id(project, unit)
    if action not in ("keep", "change"):
        raise HandbookError("A decision is either keep or change.")
    state = read_state(project)
    rec = unit_rec(state, uid)
    refresh_points(rec)
    point = next((p for p in rec["points"] if p["q"] == question), None)
    if point is None:
        raise HandbookError(f"No point was raised on question {question} for {units_by_id(project)[uid].get('name')}.")
    q = next((x for x in project.questions_for(uid) if x.get("id") == question), None)
    if q is None:
        raise HandbookError(f"Question {question} no longer applies to {uid}, so it cannot be decided.")
    reason = (reason or "").strip()
    before = _totals(project, uid)
    answers = project.answers(uid)
    answers["row"] = uid
    cells = answers.setdefault("cells", {})
    _revert_in(cells, rec, [question])            # an earlier decision on this point is taken back first
    cell = cells.get(question) if isinstance(cells.get(question), dict) else None
    new_level = None
    if action == "change":
        new_level = _level(q, value)
        if new_level is None:
            raise HandbookError(f"Question {question} has no level {value!r}. Its levels are "
                                f"{answer_kinds.describe(q)}.")
        if cell and cell.get("state") in ("answered", "no_evidence") and cell.get("value") == new_level["value"]:
            action, new_level = "keep", None            # the same answer: nothing to change
    if action == "keep" and cell is None:
        raise HandbookError(f"There is no answer on question {question} to keep.")
    reply_day = (rec.get("reply_arrived") or stamp(now))[:10]
    name = units_by_id(project)[uid].get("name")
    verb = "changed" if action == "change" else "kept"
    text = f"{verb} after {name}'s reply, {reply_day}"
    block = {"unit": name, "text": text, "date": reply_day, "kept": action == "keep", "reason": reason}
    if action == "keep":
        block["was_checked"] = cell.get("checked")
        cell["feedback"] = block
        cell["checked"] = {"at": stamp(now), "how": "feedback"}
    else:
        previous = copy.deepcopy({k: v for k, v in (cell or {}).items() if k not in ("feedback",)}) if cell else None
        block.update({"from": (cell or {}).get("value"), "previous": previous})
        evidence = list((cell or {}).get("evidence") or [])
        nev = q.get("no_evidence_value")
        no_evidence = not evidence and new_level["value"] == nev and type(new_level["value"]) is type(nev)
        new = {"state": "answered", "value": None, "points": None, "evidence": [], "rules": [],
               "judgement": False, "unsure": None, "note": "", "run": None, "out_of_date": False}
        new.update(cell or {})
        new.update({"state": "no_evidence" if no_evidence else "answered", "value": new_level["value"],
                    "points": new_level.get("points"), "evidence": evidence, "judgement": False,
                    "unsure": None, "out_of_date": False, "feedback": block,
                    "checked": {"at": stamp(now), "how": "feedback"}})
        cells[question] = new
    _write_answers(project, uid, answers)
    rec["decisions"][question] = {
        "action": action, "value": new_level["value"] if new_level else None, "reason": reason,
        "date": day(now), "from": (point.get("our_value") if action == "keep" else block.get("from")),
        "versions": len(point["versions"]), "auto": bool(auto)}
    docs = _file_attachments(project, uid, rec) if action == "change" else []
    settle(rec)
    write_state(project, state)
    return {"unit": uid, "question": question, "action": action, "value": rec["decisions"][question]["value"],
            "stage": rec["stage"], "documents": docs, "before": before, "after": _totals(project, uid),
            "text": text}


def undo_decision(project: Project, unit, question: str, now: datetime | None = None) -> dict:
    """Take back a decision: the answer goes back as it was and the point is open again."""
    uid = unit_id(project, unit)
    state = read_state(project)
    rec = unit_rec(state, uid)
    if question not in rec["decisions"]:
        raise HandbookError(f"Question {question} has no decision to undo.")
    put_back = _revert_decisions(project, uid, rec, [question])
    settle(rec)
    write_state(project, state)
    return {"unit": uid, "question": question, "answer_put_back": bool(put_back), "stage": rec["stage"]}


def auto_accept(project: Project, unit=None, now: datetime | None = None) -> list[dict]:
    """Apply the auto-accept setting to points that Claude has reviewed and nobody has decided:
    "all" takes Claude's proposal (a change when the claim, if confirmed, would change the score,
    else keep); "no_tier_change" only when the unit's tier would stay the same. Off does nothing."""
    mode = read_settings(project)["auto_accept"]
    if mode == "off":
        return []
    state = read_state(project)
    ids = [unit_id(project, unit)] if unit else [u for u in state["units"]]
    done = []
    for uid in ids:
        rec = unit_rec(state, uid)
        refresh_points(rec)
        for p in rec["points"]:
            review = p.get("review") or {}
            if p.get("decision") or not review or review.get("error"):
                continue
            action = review.get("recommend") or ("change" if review.get("changes_score") else "keep")
            value = review.get("if_confirmed") if action == "change" else None
            if action == "change" and value is None:
                continue
            if mode == "no_tier_change" and action == "change" and _tier_after(project, uid, p["q"], value) != _totals(project, uid).get("band"):
                continue
            reason = review.get("reason") or ("Accepted automatically on Claude's reading of the reply." if action == "change"
                                              else "Kept automatically: Claude found nothing in the reply that changes it.")
            done.append(decide(project, uid, p["q"], action, value, reason, now, auto=True))
    return done


def _totals_if(project: Project, uid: str, qid: str, value) -> dict | None:
    """The unit's overall score and tier if `qid` were answered `value`, as {"overall", "band",
    "points"}; None when `value` is not one of the question's levels. Nothing is written."""
    cells = copy.deepcopy(project.answers(uid).get("cells") or {})
    q = next((x for x in project.questions_for(uid) if x.get("id") == qid), None)
    lv = _level(q or {}, value)
    if lv is None:
        return None
    cell = cells.get(qid) or {"state": "answered"}
    cell.update({"state": "answered", "value": lv["value"], "points": lv.get("points")})
    cells[qid] = cell
    t = row_totals(project, uid, cells)
    if not t:
        return {"overall": None, "band": None, "points": lv.get("points")}
    return {"overall": t["overall"], "band": (t.get("bands") or {}).get("overall"), "points": lv.get("points")}


def _tier_after(project: Project, uid: str, qid: str, value):
    """The unit's overall tier if `qid` were answered `value` (nothing is written)."""
    got = _totals_if(project, uid, qid, value)
    return got["band"] if got else None


def preview(project: Project, unit, question: str, value) -> dict:
    """What changing a point to `value` would do to the unit's overall score and tier, without
    writing anything: {"unit", "question", "value", "points", "before", "after"} where before and
    after are {"overall", "band"} (the same figures `decide` returns once it is done)."""
    uid = unit_id(project, unit)
    q = next((x for x in project.questions_for(uid) if x.get("id") == question), None)
    if q is None:
        raise HandbookError(f"Question {question} does not apply to {units_by_id(project)[uid].get('name')}.")
    after = _totals_if(project, uid, question, value)
    if after is None:
        raise HandbookError(f"Question {question} has no level {value!r}. Its levels are "
                            f"{answer_kinds.describe(q)}.")
    points = after.pop("points")
    return {"unit": uid, "question": question, "value": value, "points": points,
            "before": _totals(project, uid), "after": after}


# --- files: remove, move, group files, start over -----------------------------------------------

def _find_file(state: dict, fid: str):
    """(where, owner, rec): ("unit", uid, rec) | ("unmatched", None, rec) | ("group", None, rec)."""
    for uid, rec in state["units"].items():
        for f in rec.get("files") or []:
            if f.get("id") == fid:
                return "unit", uid, f
    for where, key in (("unmatched", "unmatched"), ("group", "groups")):
        for f in state[key]:
            if f.get("id") == fid:
                return where, None, f
    raise HandbookError(f"There is no file {fid!r} in the feedback round.")


def _drop_points_without_source(project: Project, uid: str, rec: dict) -> list[str]:
    """After a file went: points no file raises any more lose their review and decision (an
    answer a decision changed is put back). Returns the questions put back."""
    refresh_points(rec)
    live = {p["q"] for p in rec["points"]}
    for q in [q for q in rec["reviews"] if q not in live]:
        rec["reviews"].pop(q)
    return _revert_decisions(project, uid, rec, [q for q in rec["decisions"] if q not in live])


def _remove_from_unit(project: Project, state: dict, uid: str, fid: str) -> dict:
    rec = unit_rec(state, uid)
    f = next(x for x in rec["files"] if x["id"] == fid)
    rec["files"].remove(f)
    delete_stored(project, f.get("path"))
    put_back = _drop_points_without_source(project, uid, rec)
    if not rec["files"]:
        rec["reply_arrived"] = None
    settle(rec)
    return {"put_back": put_back, "file": f}


def remove_file(project: Project, file_id: str, now: datetime | None = None) -> dict:
    """Remove a reply file (or attachment, or a file waiting for the owner). The points that came
    only from it go, with their decisions (answers they changed are put back)."""
    state = read_state(project)
    where, uid, f = _find_file(state, file_id)
    out = {"file": file_id, "name": file_label(f), "from": uid or where, "answers_put_back": []}
    if where == "unit":
        res = _remove_from_unit(project, state, uid, file_id)
        out["answers_put_back"], out["stage"] = res["put_back"], state["units"][uid]["stage"]
    else:
        delete_stored(project, f.get("path"))
        state["unmatched" if where == "unmatched" else "groups"].remove(f)
    write_state(project, state)
    return out


def _add_to_unit(project: Project, state: dict, uid: str, source: dict, via: str, now=None, covers=None,
                 fid: str | None = None) -> dict:
    """File a copy of `source` (a file record whose stored file is at its `path`) under unit `uid`."""
    rec = unit_rec(state, uid)
    fid = fid or next_file_id(state)
    new = {k: copy.deepcopy(v) for k, v in source.items() if k not in ("reason", "guess", "candidates", "group", "claude_read")}
    new.update({"id": fid, "via": via,
                "path": store_copy(project, project.path / source["path"], uid, fid, source["name"])})
    if covers:
        new["covers"] = covers
    if new.get("kind") == "reply":
        annotate_rows(project, uid, new.get("rows") or [], new.get("pack"))
    rec["files"].append(new)
    rec["reply_arrived"] = rec.get("reply_arrived") or stamp(now)
    return new


def move_file(project: Project, file_id: str, units, now: datetime | None = None) -> dict:
    """Send a file to other units ("that file was for X", "use it for all of Y"): from the unit it
    is under, from the unmatched files, or from a group file waiting to be confirmed. Where it was
    before, it goes (its decisions with it, answers put back). The new units read it afresh."""
    ids = [unit_id(project, u) for u in ([units] if isinstance(units, str) else units)]
    if not ids:
        raise HandbookError("Say which unit the file is for.")
    state = read_state(project)
    where, uid, f = _find_file(state, file_id)
    staged = copy.deepcopy(f)
    put_back = []
    holding = files_dir(project) / "_moving"
    holding.mkdir(parents=True, exist_ok=True)
    held = holding / safe_name(f["name"])
    shutil.copyfile(project.path / f["path"], held)
    staged["path"] = held.relative_to(project.path).as_posix()
    if where == "unit":
        put_back = _remove_from_unit(project, state, uid, file_id)["put_back"]
    else:
        delete_stored(project, f.get("path"))
        state["unmatched" if where == "unmatched" else "groups"].remove(f)
    for n, target in enumerate(ids):
        _add_to_unit(project, state, target, staged, "hand", now, fid=file_id if n == 0 else None)
        settle(unit_rec(state, target))
    shutil.rmtree(holding, ignore_errors=True)
    write_state(project, state)
    return {"file": file_id, "units": ids, "answers_put_back": put_back,
            "stages": {t: state["units"][t]["stage"] for t in ids}}


def confirm_group_file(project: Project, file_id: str, units=None, now: datetime | None = None) -> dict:
    """The owner confirms which units a group file covers (default: every unit in the group)."""
    state = read_state(project)
    where, _, f = _find_file(state, file_id)
    if where != "group":
        raise HandbookError(f"{file_id} is not a group file waiting to be confirmed.")
    ids = list(f.get("candidates") or []) if units is None else \
        [unit_id(project, u) for u in ([units] if isinstance(units, str) else units)]
    if not ids:
        raise HandbookError("Say which units the file covers.")
    for n, target in enumerate(ids):
        _add_to_unit(project, state, target, f, "group", now, covers=f.get("group"), fid=file_id if n == 0 else None)
        settle(unit_rec(state, target))
    delete_stored(project, f.get("path"))
    state["groups"].remove(f)
    write_state(project, state)
    return {"file": file_id, "units": ids, "stages": {t: state["units"][t]["stage"] for t in ids}}


def restart(project: Project, unit, now: datetime | None = None) -> dict:
    """Start a unit over: its reply files and decisions are cleared, and answers it changed are put
    back. What was sent stays (the pack and its dates); documents already filed from attachments
    stay in the project. The unit goes back to Sent, else Ready to send."""
    uid = unit_id(project, unit)
    state = read_state(project)
    rec = unit_rec(state, uid)
    put_back = _revert_decisions(project, uid, rec, list(rec["decisions"]))
    removed = len(rec["files"])
    for f in rec["files"]:
        delete_stored(project, f.get("path"))
    rec.update({"files": [], "points": [], "decisions": {}, "reviews": {}, "reply_arrived": None})
    rec["stage"] = "sent" if rec.get("sent") else ("ready_to_send" if rec.get("pack_made") else None)
    rec["by_hand"] = False
    write_state(project, state)
    return {"unit": uid, "files_cleared": removed, "answers_put_back": put_back, "stage": rec["stage"]}


# --- tokens --------------------------------------------------------------------------------------

def add_tokens(rec: dict, usage: dict) -> dict:
    """Add one Claude call's usage to the unit's tokens (processed = fresh input + cache reads + output)."""
    fresh = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
    read, out = int(usage.get("cache_read_input_tokens") or 0), int(usage.get("output_tokens") or 0)
    t = rec["tokens"]
    t["calls"] += 1
    t["fresh"] += fresh
    t["cache_read"] += read
    t["output"] += out
    t["processed"] += fresh + read + out
    return {"fresh": fresh, "cache_read": read, "output": out, "processed": fresh + read + out}


# --- the page's part of the bundle ---------------------------------------------------------------

def _point_view(p: dict, topics: dict, now_cells: dict) -> dict:
    cell = now_cells.get(p["q"]) or {}
    out = {"q": p["q"], "topic": topics.get(p["q"]), "reply": p["reply"], "why": p["why"], "link": p["link"],
           "where": p["where"], "file": p["file"], "our_value": p["our_value"], "now_value": cell.get("value"),
           "changed_since": p["changed_since"], "conflict": p["conflict"],
           "decision": p["decision"], "review": p["review"]}
    if p["conflict"]:
        out["versions"] = p["versions"]
    if p["newer_reply"]:
        out["newer_reply"] = True
    flags = []
    if p["changed_since"]:
        flags.append("we_changed_since")
    if p["attachments"]:
        flags.append("attachment")
    if p["q"] not in topics:
        flags.append("question_gone")
    out["flags"] = flags
    return out


def bundle_part(project: Project, unchecked: dict | None = None, always: bool = False) -> dict | None:
    """The `feedback` object of the page bundle, or None while the round has not started (no
    state.json; `always` builds it anyway). `unchecked` is {unit id: how many of its answers
    still need checking}, or {unit id: [those questions]} (the bundle passes the lists, so the page can
    show them); units missing from it are worked out here."""
    if not has_state(project) and not always:
        return None
    state = read_state(project)
    settings = read_settings(project)
    units = project.read("units").get("units") or []
    topics = {q.get("id"): q.get("short") or q.get("id") for q in project.read("questions").get("questions") or []}
    out_units, counts, tokens = {}, {s: 0 for s in STAGES}, 0
    for u in units:
        uid = u.get("id")
        rec = unit_rec(state, uid)
        todo = unchecked[uid] if unchecked is not None and uid in unchecked else unit_unchecked(project, uid, u)
        n = todo if isinstance(todo, int) else len(todo)
        stage = stage_of(rec, n)
        counts[stage] += 1
        tokens += rec["tokens"]["processed"]
        points = rec["points"] if rec["points"] or not rec["files"] else build_points(rec)
        entry = {"stage": stage, "by_hand": bool(rec["by_hand"]), "unchecked": n,
                 "pack_made": rec["pack_made"], "sent": rec["sent"], "reply_arrived": rec["reply_arrived"],
                 "pack": (rec.get("pack") or {}).get("file")}
        if isinstance(todo, list) and todo:
            entry["to_check"] = todo
        if rec.get("plain_reasons"):
            entry["plain_reasons"] = len(rec["plain_reasons"])
        if rec["files"]:
            entry["files"] = [{"id": f["id"], "name": f["name"], "kind": f["kind"], "via": f.get("via"),
                               "at": f.get("at"),
                               "old_pack": any(r.get("changed_since") for r in f.get("rows") or []),
                               "covers": f.get("covers"), "doc": f.get("doc"), "doc_error": f.get("doc_error"),
                               "note": f.get("note")}
                              for f in rec["files"]]
        if points:
            now_cells = project.answers(uid).get("cells") or {}
            entry["points"] = [_point_view(p, topics, now_cells) for p in points]
            decided = [p for p in points if p.get("decision")]
            entry["counts"] = {"points": len(points), "decided": len(decided),
                               "changed": sum(1 for p in decided if p["decision"]["action"] == "change"),
                               "kept": sum(1 for p in decided if p["decision"]["action"] == "keep")}
        if rec["tokens"]["calls"]:
            entry["tokens"] = rec["tokens"]["processed"]
        out_units[uid] = entry
    return {"settings": settings, "stages": [{"id": s, "label": STAGE_WORDS[s]} for s in STAGES],
            "counts": counts, "units": out_units, "tokens": tokens + int(state.get("tokens_files") or 0),
            "unmatched": [{"id": f["id"], "name": f["name"], "kind": f["kind"], "reason": f.get("reason"),
                           "guess": f.get("guess") or [], "read": f.get("claude_read"), "at": f.get("at")}
                          for f in state["unmatched"]],
            "groups": [{"id": f["id"], "name": f["name"], "group": f.get("group"),
                        "units": f.get("candidates") or [], "points": len(f.get("rows") or []), "at": f.get("at")}
                       for f in state["groups"]]}


def check_state(d) -> list[str]:
    """Problems with feedback/state.json, as readable lines ([] when fine)."""
    if not isinstance(d, dict):
        return ["state must be a JSON object"]
    out = []
    if not isinstance(d.get("units"), dict):
        out.append("units must be an object")
        return out
    for uid, rec in d["units"].items():
        if not isinstance(rec, dict):
            out.append(f"unit {uid}: must be an object")
            continue
        if rec.get("stage") is not None and rec["stage"] not in STAGES:
            out.append(f"unit {uid}: stage {rec['stage']!r} is not one of {', '.join(STAGES)}")
        for key in ("pack_made", "sent", "reply_arrived"):
            if rec.get(key) is not None and not isinstance(rec[key], str):
                out.append(f"unit {uid}: {key} must be a timestamp or null")
        for f in rec.get("files") or []:
            if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not isinstance(f.get("path"), str):
                out.append(f"unit {uid}: a file needs an id and a path")
    for key in ("unmatched", "groups"):
        if not isinstance(d.get(key, []), list):
            out.append(f"{key} must be a list")
    return out

"""Storing a scorer's answers, and raising a rule question from the cells it touches.

The scorer writes `{"row": ROW, "cells": {QID: {"state", "value", "evidence", "note", "unsure"}}}`
(the shape in the kit's brief). `ingest_answers` checks every cell first and stores nothing if any
cell has a problem; otherwise it computes points, fills `rules` and `judgement`, sets `run` and
merges the listed cells into `answers/<row>.json`, leaving every other cell as it was.

With the kit's `capture.json` (`captures`), each evidence item also gets `capture`: the id of the
document's pages as the scorer saw them, so the quote check can read that text even after the saved
copy changes. A kit whose copy of a document is not one the project has on file is refused.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone

from . import answers as answer_kinds
from .folder import HandbookError, Project, applies
from .kit import rules_for, unit
from .shapes import UNSURE_WHY, check_answers, check_rules

SCORER_STATES = ("answered", "no_evidence")
RAISED_BY = ("trial", "run")


def _same(a, b) -> bool:
    """Equal and of the same type, so True is not 1 and "1" is not 1."""
    return a == b and type(a) is type(b)


def _is_english(language) -> bool:
    """Whether a language code is English in any form: en, EN, en-GB, en_US."""
    return isinstance(language, str) and re.split(r"[-_]", language.strip().lower())[0] == "en"


def _level(question: dict, value):
    """The level dict for `value`, or None."""
    for lv in (question.get("answer") or {}).get("levels") or []:
        if _same(lv.get("value"), value):
            return lv
    return None


def _accepts(question: dict, value) -> bool:
    return _level(question, value) is not None or answer_kinds.figure_ok(question, value)


def _points(question: dict, value):
    """What `value` is worth: its level's points, 0 for a figure a number question accepts, else None."""
    level = _level(question, value)
    if level is not None:
        return level.get("points")
    return 0 if answer_kinds.figure_ok(question, value) else None


def _values(question: dict) -> str:
    return answer_kinds.describe(question)


def _check_evidence(problems, w, evidence, docs, row, stale):
    for i, ev in enumerate(evidence, start=1):
        we = f"{w}: evidence {i}"
        if not isinstance(ev, dict):
            problems.append(f"{we} must be an object with doc, page and quote")
            continue
        doc = ev.get("doc")
        if doc not in docs:
            problems.append(f"{we}: document {doc!r} is not in the kit for {row}")
        elif not docs[doc]:
            problems.append(f"{we}: document {doc} has no saved text, so it cannot be cited")
        elif doc in stale:
            problems.append(f"{we}: the kit's copy of {doc} is not one this project has saved "
                            f"(capture {stale[doc][:10]}); build the kit again and score again")
        else:
            page = ev.get("page")
            if not (isinstance(page, int) and not isinstance(page, bool) and page in docs[doc]):
                problems.append(f"{we}: page {page!r} is not a page of {doc} "
                                f"(it has pages {min(docs[doc])} to {max(docs[doc])})")
        if not (isinstance(ev.get("quote"), str) and ev["quote"].strip()):
            problems.append(f"{we}: quote must be the exact words from the page")
        for key in ("quote_en", "language", "section"):
            if ev.get(key) is not None and not isinstance(ev.get(key), str):
                problems.append(f"{we}: {key} must be text or null")


def kit_captures(captures) -> dict[str, str]:
    """{doc: capture id} from a kit's capture.json (`{"docs": {doc: {"capture", "pages"}}}`);
    {} for None or anything else."""
    docs = captures.get("docs") if isinstance(captures, dict) else None
    return {doc: info["capture"] for doc, info in (docs or {}).items()
            if isinstance(info, dict) and isinstance(info.get("capture"), str)}


def _check_cell(problems, qid, c, question, docs, row, stale):
    w = f"cell {qid}"
    if not isinstance(c, dict):
        problems.append(f"{w} must be an object")
        return
    state, value = c.get("state"), c.get("value")
    evidence = c.get("evidence") if c.get("evidence") is not None else []
    if not isinstance(evidence, list):
        problems.append(f"{w}: evidence must be a list")
        evidence = []
    if state == "answered":
        if not _accepts(question, value):
            problems.append(f"{w}: value {value!r} is not one of the question's levels ({_values(question)})")
        if not evidence:
            problems.append(f"{w}: an answered cell needs at least one piece of evidence")
    elif state == "no_evidence":
        nev = question.get("no_evidence_value")
        if value is not None and not _same(value, nev):
            problems.append(f"{w}: a no_evidence cell takes value null (or {nev!r}), found {value!r}")
        if evidence:
            problems.append(f"{w}: a no_evidence cell takes no evidence")
    elif state == "not_applicable" and question.get("may_not_apply") is True:
        # The documents show the question does not apply to this row (ACES: a camp with no riding).
        if value is not None:
            problems.append(f"{w}: a not_applicable cell takes value null, found {value!r}")
        if not evidence and not (isinstance(c.get("note"), str) and c["note"].strip()):
            # Evidence where a document shows it; else the note says why (the Bankwijzer, 15 Oct 2026: an element
            # the owner rules 'not relevant' for every bank has no page to quote, and one such cell threw
            # away a whole bank's answers).
            problems.append(f"{w}: a not_applicable cell needs the evidence that shows it does not apply, "
                            "or a note saying why")
    elif state == "not_applicable":
        problems.append(f"{w}: question {qid} is not marked may_not_apply, so its state cannot be not_applicable; "
                        "answer it or say no_evidence")
    else:
        problems.append(f"{w}: state must be \"answered\" or \"no_evidence\", found {state!r}")
    _check_evidence(problems, w, evidence, docs, row, stale)
    unsure = c.get("unsure")
    if unsure is not None:
        if not isinstance(unsure, dict) or unsure.get("why") not in UNSURE_WHY:
            why = unsure.get("why") if isinstance(unsure, dict) else unsure
            problems.append(f"{w}: unsure must be null or {{\"why\": \"rule\" or \"document\", "
                            f"\"note\": \"...\"}}, found why {why!r}")
        elif not isinstance(unsure.get("note"), str):
            problems.append(f"{w}: unsure note must be text")
    if c.get("note") is not None and not isinstance(c.get("note"), str):
        problems.append(f"{w}: note must be text")


def validate_answers(project: Project, row: str, data, captures=None) -> list[str]:
    """Every problem with a scorer's answers file for `row`, as plain sentences ([] when fine).
    `captures` is the kit's capture.json: a cited document whose kit copy is not a capture the
    project has on file is a problem."""
    if not isinstance(data, dict):
        return ["The answers file must be a JSON object with row and cells."]
    problems = []
    if data.get("row") != row:
        problems.append(f"The file is for row {data.get('row')!r}, not {row}.")
    cells = data.get("cells")
    if not isinstance(cells, dict) or not cells:
        return problems + ["The file has no cells: it needs \"cells\": {question id: answer}."]
    the_unit = unit(project, row)
    mine = {q["id"]: q for q in project.questions_for(the_unit)}
    known = {q.get("id") for q in project.read("questions").get("questions", [])}
    docs = {}
    for doc in project.found(row).get("documents") or []:
        pages = project.pages(doc) if (project.doc_dir(doc) / "meta.json").is_file() else []
        docs[doc] = {p.get("page") if isinstance(p.get("page"), int) else i
                     for i, p in enumerate(pages, start=1)}
    stale = {doc: cid for doc, cid in kit_captures(captures).items()
             if doc in docs and not project.capture(doc, cid)}
    for qid, c in cells.items():
        if qid not in mine:
            problems.append(f"cell {qid}: " + (f"question {qid} does not apply to {row}" if qid in known
                                                else f"question {qid} is not in questions.json"))
            continue
        _check_cell(problems, qid, c, mine[qid], docs, row, stale)
    return problems


def _stored_cell(c: dict, question: dict, rules: list, row: str, run: str, caps: dict) -> dict:
    state = c["state"]
    value = (c.get("value") if state == "answered" else None if state == "not_applicable"
             else question.get("no_evidence_value"))
    evidence = []
    for ev in c.get("evidence") or []:
        language = ev.get("language")
        evidence.append({"doc": ev["doc"], "page": ev["page"], "quote": ev["quote"],
                         "quote_en": ev["quote"] if _is_english(language) else ev.get("quote_en"),
                         "language": language, "capture": caps.get(ev["doc"]),
                         **({"section": ev["section"].strip(), "section_by": "scorer"}
                            if isinstance(ev.get("section"), str) and ev["section"].strip() else {})})
    applying = rules_for(rules, question["id"], row)
    unsure = c.get("unsure")
    stored_unsure = None
    if unsure is not None:
        stored_unsure = {"why": unsure["why"], "note": unsure["note"], "handled": False}
        if unsure["why"] == "document" and missing_name(unsure):
            stored_unsure["missing"] = missing_name(unsure)
    return {"state": state, "value": value, "points": None if value is None else _points(question, value),
            "evidence": evidence, "rules": [r["id"] for r in applying],
            "judgement": any(r.get("status") in ("open", "skipped") for r in applying),
            "unsure": stored_unsure,
            "note": c.get("note") or "", "run": run, "out_of_date": False}


MISSING_CHARS = 200


def missing_name(unsure) -> str | None:
    """The document a scorer said it needed and could not find in its kit (`unsure.missing`), as plain
    text cut to MISSING_CHARS; None when there is none or it is not text (a malformed name is dropped,
    never a reason to refuse the row's answers)."""
    name = unsure.get("missing") if isinstance(unsure, dict) else None
    if not isinstance(name, str) or not " ".join(name.split()):
        return None
    return " ".join(name.split())[:MISSING_CHARS]


def record_missing(project: Project, row: str, cells: dict) -> list[dict]:
    """Add each document the scorer named as missing (`unsure.missing` in `cells`, stored cells) to
    found/<row>.json `expected_not_found`, once per name (case aside), with the questions that need it,
    so the Grid shows it among the row's missing documents, the second search looks for it and the
    owner can send it. Returns the entries added or widened."""
    wanted: dict[str, dict] = {}
    for qid, c in cells.items():
        name = missing_name((c or {}).get("unsure"))
        if name:
            wanted.setdefault(name.casefold(), {"what": name, "questions": []})["questions"].append(qid)
    if not wanted:
        return []
    found = project.found(row)
    entries = found.setdefault("expected_not_found", [])
    changed = []
    for key, need in wanted.items():
        have = next((e for e in entries if isinstance(e, dict) and str(e.get("what", "")).casefold() == key), None)
        if have is None:
            have = {"what": need["what"], "where_looked": "", "by": "scorer", "questions": []}
            entries.append(have)
        qs = list(dict.fromkeys(list(have.get("questions") or []) + need["questions"]))
        if qs != have.get("questions"):
            have["questions"] = qs
            if have.get("by") == "scorer":
                have["where_looked"] = (f"Not among the documents found for this row; Claude needed it to answer "
                                        f"question{'s' if len(qs) != 1 else ''} {', '.join(qs)}.")
            changed.append(have)
    found["row"] = row
    project.write_found(row, found)
    return changed


def supplied_missing(project: Project, row: str, what: str) -> list[str]:
    """The owner sent `what` for `row`: if it answers a document a scorer named as missing
    (record_missing), take that note off the row and return the questions that needed it, to be
    scored again; [] when `what` is no such note."""
    found = project.found(row)
    entries = found.get("expected_not_found") or []
    hit = next((e for e in entries if isinstance(e, dict) and e.get("by") == "scorer"
                and str(e.get("what", "")).casefold() == " ".join(str(what).split()).casefold()), None)
    if hit is None:
        return []
    found["expected_not_found"] = [e for e in entries if e is not hit]
    project.write_found(row, found)
    return [str(q) for q in hit.get("questions") or []]


def _problem_list(head: str, problems: list) -> HandbookError:
    return HandbookError(f"{head} {len(problems)} problem(s):\n" + "\n".join(f"  - {p}" for p in problems))


# --- the owner's answer ------------------------------------------------------------------------
# A cell the owner corrected holds the owner's value under `owner` (corrections.py). The owner's
# word is final: a later score of that cell, a correction re-read or a rerun after a rule
# decision, changes only Claude's verdict on it, never the value.

def owner_cell(question: dict, base: dict | None, value, evidence: list, owner: dict) -> dict:
    """`base` (a stored cell, or None) holding the owner's `value` and `evidence`, with `owner` as
    its owner block. It rests on no reading of Claude's, so `judgement` is false; with no evidence
    and the question's no-evidence value it is a no_evidence cell."""
    nev = question.get("no_evidence_value")
    cell = {"state": "answered", "value": None, "points": None, "evidence": [], "rules": [],
            "judgement": False, "unsure": None, "note": "", "run": None, "out_of_date": False}
    cell.update({k: v for k, v in (base or {}).items() if k not in ("previous",)})
    cell.update({"state": "no_evidence" if not evidence and _same(value, nev) else "answered",
                 "value": value, "points": _points(question, value),
                 "evidence": evidence, "judgement": False, "unsure": None, "out_of_date": False,
                 "owner": owner})
    return cell


def merge_owner(question: dict, current: dict, claude: dict, why: str | None = None) -> dict:
    """The owner's cell `current` after Claude scored the same question again (`claude`, a stored
    cell): the owner's value stays; `agreed` says whether Claude's value is the same, and when it
    is not, `why` (Claude's note unless given) says why. The cell keeps its evidence, or takes
    Claude's when it has none and Claude agreed."""
    owner = dict(current["owner"])
    agreed = answer_kinds.same_answer(question, claude.get("value"), owner.get("value"))
    owner.update({"agreed": agreed, "claude": claude.get("value"),
                  "why": None if agreed else ((why or "").strip() or claude.get("note")
                                              or "Claude's re-read gave a different answer."),
                  "kept": bool(owner.get("kept")) and not agreed})
    evidence = current.get("evidence") or (claude.get("evidence") if agreed else []) or []
    return owner_cell(question, claude, owner["value"], evidence, owner)


def ingest_answers(project: Project, row: str, run: str, data, captures=None) -> list[str]:
    """Check and store a scorer's answers for `row` from `run`, binding each evidence item to the
    capture the kit's `captures` (its capture.json) names for its document. A cell holding the
    owner's answer keeps it: the scorer's answer becomes Claude's verdict on it (`merge_owner`).
    Returns the question ids stored. Raises HandbookError, storing nothing, when the run is unknown
    or any cell has a problem."""
    if not project.run(run):
        raise HandbookError(f"There is no run {run!r} in runs/. Start one with: run start --kind ...")
    problems = validate_answers(project, row, data, captures)
    if problems:
        raise _problem_list(f"Nothing was stored for {row}.", problems)
    questions = {q["id"]: q for q in project.questions_for(unit(project, row))}
    rules = project.read("rules").get("rules", [])
    caps = kit_captures(captures)
    new = {qid: _stored_cell(c, questions[qid], rules, row, run, caps) for qid, c in data["cells"].items()}
    answers = project.answers(row)
    for qid, cell in new.items():
        old = (answers.get("cells") or {}).get(qid)
        if isinstance(old, dict) and isinstance(old.get("owner"), dict):
            new[qid] = merge_owner(questions[qid], old, cell)
        elif isinstance(old, dict) and isinstance(old.get("feedback"), dict) and old["feedback"].get("kept") is False:
            # An answer changed after a party's reply stays; what the scorer read is kept beside it.
            new[qid] = {**old, "feedback": {**old["feedback"], "rescored": {"value": cell.get("value"), "run": run}}}
    problems = check_answers({"row": row, "cells": new}, project.read("questions"))
    if problems:
        raise _problem_list(f"Nothing was stored for {row}: the stored cells would have", problems)
    answers["row"] = row
    answers.setdefault("cells", {}).update(new)
    project.write_answers(row, answers)
    record_missing(project, row, new)
    return list(data["cells"])


def _top_minus_lowest(question: dict) -> float:
    points = [lv.get("points") for lv in (question.get("answer") or {}).get("levels") or []
              if isinstance(lv.get("points"), (int, float))]
    return max(points) - min(points) if points else 0


def add_instruction(project: Project, text: str, questions=None, rows=None, via: str = "chat",
                    now: str | None = None) -> dict:
    """Append the owner's standing instruction (such as "cite the section, not only the page") as a
    decided rule of kind "instruction" on `questions` (ids; None for every question) for `rows`
    (ids; None for every row, now and later). Every scorer kit built from now on carries it among
    its rulings (kit.py), so it reaches each brief; answers already scored change only when they
    are scored again. No cell is linked to it and none becomes a judgement call. Returns the rule."""
    if not (isinstance(text, str) and text.strip()):
        raise HandbookError("The instruction needs some words.")
    all_q = [q.get("id") for q in project.read("questions").get("questions", []) if q.get("id")]
    qids = all_q if not questions else list(dict.fromkeys(questions))
    unknown = [q for q in qids if q not in all_q]
    if unknown:
        raise HandbookError(f"Question {', '.join(unknown)} is not in questions.json.")
    if rows is not None:
        rows = list(dict.fromkeys(rows))
        for r in rows:
            unit(project, r)                     # HandbookError for an unknown row
    rules_file = project.read("rules")
    rules = rules_file.setdefault("rules", [])
    numbers = [int(m.group(1)) for r in rules if (m := re.fullmatch(r"R(\d+)", str(r.get("id"))))]
    at = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rule = {"id": f"R{max(numbers, default=0) + 1}", "kind": "instruction", "question_ids": qids, "rows": rows,
            "ask": "A standing instruction from the owner", "options": [], "recommended": None,
            "why": None, "stakes": {"cells": 0, "points": 0}, "last_year_note": None, "status": "decided",
            "decision": {"option": None, "text": text.strip(), "at": at, "via": via}, "raised_by": "owner"}
    problems = check_rules({"rules": [rule]}, project.read("questions"), project.read("units"))
    if problems:
        raise _problem_list("The instruction was not added:", problems)
    rules.append(rule)
    project.write("rules", rules_file)
    return rule


def raise_rule(project: Project, questions: list, rows: list, ask: str, options: list,
               recommended: str, by: str, why: str | None = None) -> dict:
    """Append an open scoring rule on `questions` × `rows` (options as [(id, text)]), with its
    stakes, and link it to those cells: their `rules` gain its id, `judgement` becomes true, and a
    rule doubt (`unsure.why == "rule"`) is marked handled. Returns the new rule."""
    if by not in RAISED_BY:
        raise HandbookError(f"--by must be trial or run, not {by!r}.")
    if not (isinstance(ask, str) and ask.strip()):
        raise HandbookError("The rule needs a question to ask the owner (--ask).")
    ids = [o[0] for o in options]
    if len(options) < 2 or len(set(ids)) != len(ids) or not all(i and t and t.strip() for i, t in options):
        raise HandbookError("Give at least two options, each with its own id and some text, "
                            "for example --option a=Yes --option b=No.")
    if recommended not in ids:
        raise HandbookError(f"The recommended option {recommended!r} is not one of the options "
                            f"({', '.join(ids)}).")
    all_q = {q.get("id"): q for q in project.read("questions").get("questions", [])}
    unknown = [q for q in questions if q not in all_q]
    if unknown:
        raise HandbookError(f"Question {', '.join(unknown)} is not in questions.json.")
    units = {r: unit(project, r) for r in rows}
    touched = [(r, q) for r in rows for q in questions if applies(all_q[q], units[r])]
    if not touched:
        raise HandbookError("None of these questions applies to these rows, so the rule would touch no cells.")
    rules_file = project.read("rules")
    rules = rules_file.setdefault("rules", [])
    numbers = [int(m.group(1)) for r in rules if (m := re.fullmatch(r"R(\d+)", str(r.get("id"))))]
    rule = {"id": f"R{max(numbers, default=0) + 1}", "kind": "scoring",
            "question_ids": list(dict.fromkeys(questions)), "rows": list(dict.fromkeys(rows)),
            "ask": ask.strip(), "options": [{"id": i, "text": t.strip()} for i, t in options],
            "recommended": recommended, "why": why,
            "stakes": {"cells": len(touched), "points": sum(_top_minus_lowest(all_q[q]) for _, q in touched)},
            "last_year_note": None, "status": "open", "decision": None, "raised_by": by}
    problems = check_rules({"rules": [rule]}, project.read("questions"), project.read("units"))
    if problems:
        raise _problem_list("The rule was not added:", problems)
    rules.append(rule)
    # Work out every row's new answers first, then write the answers, then rules.json: a failure
    # part-way cannot leave a rule in rules.json that no cell is linked to.
    updated = []
    for r in dict.fromkeys(rows):
        answers = project.answers(r)
        cells = answers.get("cells") or {}
        changed = False
        for row_id, qid in touched:
            c = cells.get(qid) if row_id == r else None
            if not isinstance(c, dict):
                continue
            c["rules"] = list(dict.fromkeys(list(c.get("rules") or []) + [rule["id"]]))
            c["judgement"] = True
            if isinstance(c.get("unsure"), dict) and c["unsure"].get("why") == "rule":
                c["unsure"]["handled"] = True
            changed = True
        if changed:
            updated.append((r, answers))
    for r, answers in updated:
        project.write_answers(r, answers)
    project.write("rules", rules_file)
    return rule

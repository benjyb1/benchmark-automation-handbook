"""Shape checks for every file in a project folder.

Each `check_<file>(d, ...)` returns a list of readable problems; an empty list means valid.
Unknown keys are ignored. Functions that take `questions` or `units` accept either the whole file
({"questions": [...]}) or the list itself. `check_folder` runs them all across a folder, adds the
checks that need more than one file, and prefixes each problem with the file it is in.
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime
from pathlib import Path

from . import answers
from . import edition as editions
from .folder import CHECKLIST_KEYS, CHECKLIST_STATUSES, ID_RE, Project

STAGES = ("setup", "trial", "running", "done")
ROLES = ("questions", "units", "methodology", "last_results", "source_list", "held_document",
         "last_report", "other")
ITEM_STATUSES = ("draft", "confirmed")
ANSWER_KINDS = ("yes_no", "levels", "number")
ROUNDINGS = ("none", "half_up_int", "half_up_1dp", "half_up_2dp")
OVERALL_METHODS = ("sum_points", "weighted_mean_of_section_percents")
RULE_KINDS = ("scoring", "scope", "evidence", "instruction")
RULE_STATUSES = ("open", "decided", "skipped")
RAISED_BY = ("setup", "trial", "run", "owner")
VIA = ("chat", "viewer")
NEED_STATUSES = ("needed", "supplied", "waived")
HOW = ("plan", "search", "last_year", "provided")
DATE_BASES = ("pdf metadata", "page text", "http header", "owner", "finder", "archive capture")
HOST_TYPES = ("issuer", "repository", "third_party", "test_server", "none")
TEXT_SOURCES = ("rendered", "archive", "archive_rendered", "ocr")
CELL_STATES = ("answered", "no_evidence", "not_applicable", "not_run")
UNSURE_WHY = ("rule", "document")
FLAGS = ("quote_not_found", "after_cutoff", "reads_disagree", "group_differs", "judgement",
         "weak_source", "document_changed", "owner_changed", "todays_version")
RUN_KINDS = ("trial", "full", "rerun")
USAGE_STEPS = ("find", "second_find", "score", "second")
RECEIPT_KEYS = ("fresh", "cache_read", "output", "turns")
LEVELS = ("question", "section", "total", "band")
REASONS = ("missing_document", "different_document", "document_date", "rule_reading", "open_rule",
           "unexplained")
REASON_BY = ("files", "claude")
EDIT_TARGETS = ("question", "unit", "confirm")
CORRECTION_STATUSES = ("to_check", "checked", "superseded", "kept")
VERDICT_BY = ("code", "claude", "page")   # page: checked in the viewer by the Claude there
EVIDENCE_PLACES = ("doc", "link", "asset")      # where the owner's quote is: exactly one of these
CHECKED_HOW = ("owner", "feedback")     # how an answer was marked checked (feedback round, or by hand)
FEEDBACK_STAGES = ("answers_to_check", "ready_to_send", "sent", "returned", "needs_review", "reviewed")
EDIT_FIELDS = {"question": ("wording", "short", "rule", "applies_to", "levels_points"),
               "unit": ("name", "labels"),
               "confirm": ("questions", "units")}

_DOC_RE = re.compile(r"^d-[0-9a-f]{10}\Z")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z\Z")
_MISSING = object()


# --- small helpers ----------------------------------------------------------------------------

def _show(v) -> str:
    text = json.dumps(v, ensure_ascii=False)
    return text if len(text) <= 60 else text[:57] + "..."


def _one_of(allowed) -> str:
    return ", ".join(map(str, allowed))


def _member(x, container) -> bool:
    """`x in container` for ids: False for anything that is not text, so a list or a dict found
    where an id belongs is a problem to report, not an unhashable-type crash."""
    return isinstance(x, str) and x in container


def _list(v) -> list:
    """`v` when it is a list, else []. Used where a file's shape was already reported elsewhere."""
    return v if isinstance(v, list) else []


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def is_date(v) -> bool:
    """An ISO date, YYYY-MM-DD."""
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def is_stamp(v) -> bool:
    """An ISO timestamp ending in Z, such as 2026-10-07T09:30:00Z."""
    if not isinstance(v, str) or not _STAMP_RE.match(v):
        return False
    try:
        datetime.fromisoformat(v[:-1])
    except ValueError:
        return False
    return True


def _items(d, key: str):
    """The list under `key` in a file dict, or the value itself when it is already a list."""
    if isinstance(d, list):
        return d
    if isinstance(d, dict) and isinstance(d.get(key), list):
        return d[key]
    return []


def _ids(d, key: str) -> set:
    """The text ids of the dicts in the list under `key`; ids of any other kind are left out."""
    return {x["id"] for x in _items(d, key) if isinstance(x, dict) and isinstance(x.get("id"), str)}


def _where(kind: str, item, i: int) -> str:
    if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
        return f"{kind} {_show(item['id'])}"
    return f"{kind} {i + 1}"


class _Out(list):
    """A problem list with the checks every file needs."""

    def obj(self, d, where) -> bool:
        if isinstance(d, dict):
            return True
        self.append(f"{where} must be a JSON object, found {_show(d)}")
        return False

    def text(self, d, key, where, required=True, nullable=False, empty_ok=False):
        v = d.get(key, _MISSING)
        if v is _MISSING or v is None:
            if v is _MISSING and required and not nullable:
                self.append(f"{where}: {key} is missing")
            elif v is None and not nullable:
                self.append(f"{where}: {key} must not be null")
            return
        if not isinstance(v, str):
            self.append(f"{where}: {key} must be text, found {_show(v)}")
        elif not empty_ok and not v.strip():
            self.append(f"{where}: {key} must not be empty")

    def enum(self, d, key, allowed, where, required=True, nullable=False):
        v = d.get(key, _MISSING)
        if v is _MISSING:
            if required:
                self.append(f"{where}: {key} is missing")
            return
        if v is None and nullable:
            return
        if v not in allowed:
            self.append(f"{where}: {key} is {_show(v)}, expected one of: {_one_of(allowed)}")

    def boolean(self, d, key, where, required=True):
        v = d.get(key, _MISSING)
        if v is _MISSING:
            if required:
                self.append(f"{where}: {key} is missing")
        elif not isinstance(v, bool):
            self.append(f"{where}: {key} must be true or false, found {_show(v)}")

    def date(self, d, key, where, nullable=True):
        v = d.get(key)
        if v is None:
            if not nullable:
                self.append(f"{where}: {key} is missing")
        elif not is_date(v):
            self.append(f"{where}: {key} must be a date like 2025-07-05, found {_show(v)}")

    def stamp(self, d, key, where, nullable=True):
        v = d.get(key)
        if v is None:
            if not nullable:
                self.append(f"{where}: {key} is missing")
        elif not is_stamp(v):
            self.append(f"{where}: {key} must be a timestamp like 2026-10-07T09:30:00Z, found {_show(v)}")

    def str_list(self, d, key, where, required=True, nullable=False) -> list:
        v = d.get(key, _MISSING)
        if v is _MISSING or v is None:
            if (v is _MISSING and required and not nullable) or (v is None and not nullable):
                self.append(f"{where}: {key} must be a list")
            return []
        if not isinstance(v, list) or not all(_text(x) for x in v):
            self.append(f"{where}: {key} must be a list of non-empty text, found {_show(v)}")
            return []
        return v

    def label_filter(self, v, where, key):
        """null, or {label: [values]} with non-empty lists of text."""
        if v is None:
            return
        if not isinstance(v, dict) or not all(
                _text(k) and isinstance(vals, list) and vals and all(isinstance(x, str) for x in vals)
                for k, vals in v.items()):
            self.append(f"{where}: {key} must be null or {{label: [values]}}, found {_show(v)}")

    def unique_ids(self, items, kind):
        seen = set()
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            x = item.get("id")
            if not _text(x):
                self.append(f"{kind} {i + 1}: id is missing or empty")
            elif x in seen:
                self.append(f"{kind} {i + 1} has a duplicate id {_show(x)}")
            else:
                seen.add(x)

    def list_of(self, d, key, where, required=True) -> list:
        v = d.get(key, _MISSING)
        if v is _MISSING:
            if required:
                self.append(f"{where}: {key} is missing")
            return []
        if not isinstance(v, list):
            self.append(f"{where}: {key} must be a list, found {_show(v)}")
            return []
        return v


# --- project.json -----------------------------------------------------------------------------

def check_project(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "project.json"):
        return out
    w = "project"
    out.text(d, "name", w)
    slug = d.get("slug")
    if not (isinstance(slug, str) and ID_RE.match(slug)):
        out.append(f"project: slug {_show(slug)} must use only lowercase letters, digits and dashes")
    out.text(d, "edition", w, required=False, nullable=True)
    out.text(d, "unit_word", w)
    out.text(d, "unit_word_plural", w)
    out.date(d, "cutoff", w)
    labels = out.str_list(d, "labels", w)
    if len(set(labels)) != len(labels):
        out.append(f"project: labels has a label twice: {_show(labels)}")
    for label in out.str_list(d, "group_labels", w):
        if label not in labels:
            out.append(f"project: group_labels names {_show(label)}, which is not in labels")
    group_by = d.get("group_by")
    if group_by is not None and group_by not in labels:
        out.append(f"project: group_by {_show(group_by)} is not in labels")
    accent = d.get("accent")
    if accent is not None and not (isinstance(accent, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", accent)):
        out.append(f"project: accent {_show(accent)} must be a colour like #1d5c4f, or null")
    for site in out.str_list(d, "results_sites", w, required=False, nullable=True):
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(/\S*)?", site.strip().lower()):
            out.append(f"project: results_sites {_show(site)} must be a web host, such as elephantstandards.com, "
                       "or a host and path, such as aldf.org/us-state-rankings")
    out.enum(d, "stage", STAGES, w)
    checklist = d.get("checklist")
    if out.obj(checklist, "project: checklist"):
        for key in checklist:
            if key not in CHECKLIST_KEYS:
                out.append(f"project: unknown checklist key {_show(key)}")
        for key in CHECKLIST_KEYS:
            if key not in checklist:
                out.append(f"project: checklist {_show(key)} is missing")
            elif checklist[key] not in CHECKLIST_STATUSES:
                out.append(f"project: checklist {_show(key)} has status {_show(checklist[key])}, "
                           f"expected one of: {_one_of(CHECKLIST_STATUSES)}")
    for i, item in enumerate(out.list_of(d, "provided", w, required=False)):
        wi = f"project: provided {i + 1}"
        if out.obj(item, wi):
            out.text(item, "file", wi)
            out.enum(item, "role", ROLES, wi)
            out.text(item, "note", wi, required=False, nullable=True, empty_ok=True)
    out.stamp(d, "created", w, nullable=False)
    return out


# --- questions.json ---------------------------------------------------------------------------

def _level_values(q) -> list | None:
    answer = q.get("answer") if isinstance(q, dict) else None
    levels = answer.get("levels") if isinstance(answer, dict) else None
    if not isinstance(levels, list):
        return None
    return [lv.get("value") for lv in levels if isinstance(lv, dict)]


def _level_points(q, value):
    points = answers.points_for(q, value)
    return _MISSING if points is answers.MISSING else points


def check_questions(d, scoresheet=None) -> list[str]:
    out = _Out()
    if not out.obj(d, "questions.json"):
        return out
    qs = out.list_of(d, "questions", "questions.json")
    out.unique_ids(qs, "question")
    sections = None
    if (isinstance(scoresheet, dict) and scoresheet.get("computable") is not False
            and isinstance(scoresheet.get("sections"), list)):
        sections = [s["id"] for s in scoresheet["sections"]
                    if isinstance(s, dict) and isinstance(s.get("id"), str)]
    for i, q in enumerate(qs):
        w = _where("question", q, i)
        if not out.obj(q, w):
            continue
        out.text(q, "short", w, empty_ok=True)
        out.text(q, "wording", w)
        out.text(q, "section", w)
        if sections is not None and _text(q.get("section")) and q["section"] not in sections:
            out.append(f"{w}: section {_show(q['section'])} is not in the score sheet "
                       f"({_one_of(sections) or 'no sections'})")
        answer = q.get("answer")
        values = []
        if out.obj(answer, f"{w}: answer"):
            out.enum(answer, "kind", ANSWER_KINDS, f"{w}: answer")
            kind = answer.get("kind")
            levels = out.list_of(answer, "levels", f"{w}: answer", required=kind != "number")
            if not levels and kind != "number":
                out.append(f"{w}: answer has no levels")
            if kind == "number":
                for key in ("min", "max"):
                    if answer.get(key) is not None and not _is_num(answer.get(key)):
                        out.append(f"{w}: answer {key} must be a number or null, found {_show(answer.get(key))}")
                if _is_num(answer.get("min")) and _is_num(answer.get("max")) and answer["min"] > answer["max"]:
                    out.append(f"{w}: answer min is above its max")
                d = answer.get("decimals")
                if d is not None and not (_is_int(d) and d >= 0):
                    out.append(f"{w}: answer decimals must be a whole number from 0, or null")
                out.text(answer, "unit", f"{w}: answer", required=False, nullable=True, empty_ok=True)
            for j, lv in enumerate(levels):
                wl = f"{w}: level {j + 1}"
                if not out.obj(lv, wl):
                    continue
                if "value" not in lv:
                    out.append(f"{wl}: value is missing")
                else:
                    values.append(lv["value"])
                if not _is_num(lv.get("points")):
                    out.append(f"{wl}: points must be a number, found {_show(lv.get('points'))}")
                out.text(lv, "meaning", wl, empty_ok=True)
            if kind == "number":
                for v in values:
                    if not isinstance(v, str):
                        out.append(f"{w}: a number answer's levels are the answers that are not figures, "
                                   f"such as \"NR\"; {_show(v)} must be text")
                if len(set(map(_show, values))) != len(values):
                    out.append(f"{w}: two levels share a value")
            if kind == "yes_no" and (sorted(map(str, values)) != ["no", "yes"]
                                     or not all(isinstance(v, str) for v in values)):
                out.append(f"{w}: a yes_no answer needs exactly the levels \"yes\" and \"no\", "
                           f"found {', '.join(_show(v) for v in values) or 'none'}")
            if kind == "levels":
                for v in values:
                    if not _is_int(v):
                        out.append(f"{w}: level value {_show(v)} must be a whole number")
                if len(set(map(_show, values))) != len(values):
                    out.append(f"{w}: two levels share a value")
        nev = q.get("no_evidence_value", _MISSING)
        if nev is _MISSING:
            out.append(f"{w}: no_evidence_value is missing")
        elif isinstance(answer, dict) and answer.get("kind") == "number":
            if not answers.accepts(q, nev):
                out.append(f"{w}: no_evidence_value {_show(nev)} is not an answer it accepts ({answers.describe(q)})")
        elif values and not any(nev == v and type(nev) is type(v) for v in values):
            out.append(f"{w}: no_evidence_value {_show(nev)} is not one of its levels "
                       f"({', '.join(_show(v) for v in values)})")
        out.text(q, "rule", w, required=False, nullable=True, empty_ok=True)
        if "may_not_apply" in q and not isinstance(q.get("may_not_apply"), bool):
            out.append(f"{w}: may_not_apply must be true or false")
        out.label_filter(q.get("applies_to"), w, "applies_to")
        for j, variant in enumerate(out.list_of(q, "variants", w, required=False)):
            wv = f"{w}: variant {j + 1}"
            if not out.obj(variant, wv):
                continue
            if variant.get("when") is None:
                out.append(f"{wv}: when is missing")
            else:
                out.label_filter(variant.get("when"), wv, "when")
            out.text(variant, "wording", wv, required=False, nullable=True)
            out.text(variant, "rule", wv, required=False, nullable=True)
        out.text(q, "evidence", w, required=False, nullable=True, empty_ok=True)
        out.enum(q, "status", ITEM_STATUSES, w)
        out.text(q, "from", w, required=False, nullable=True, empty_ok=True)
    return out


# --- units.json -------------------------------------------------------------------------------

def check_units(d, project) -> list[str]:
    out = _Out()
    if not out.obj(d, "units.json"):
        return out
    labels = [x for x in _list(project.get("labels")) if isinstance(x, str)] \
        if isinstance(project, dict) else []
    units = out.list_of(d, "units", "units.json")
    out.unique_ids(units, "unit")
    for i, u in enumerate(units):
        w = _where("unit", u, i)
        if not out.obj(u, w):
            continue
        uid = u.get("id")
        if _text(uid) and not ID_RE.match(uid):
            out.append(f"{w}: id must use only lowercase letters, digits and dashes")
        out.text(u, "name", w)
        if u.get("cutoff") is not None and not is_date(u.get("cutoff")):
            out.append(f"{w}: cutoff must be a date like 2025-09-01, or left out")
        ul = u.get("labels", _MISSING)
        if ul is _MISSING:
            out.append(f"{w}: labels is missing")
        elif out.obj(ul, f"{w}: labels"):
            for key, value in ul.items():
                if key not in labels:
                    out.append(f"{w}: label {_show(key)} is not in the project's labels "
                               f"({_one_of(labels) or 'none yet'})")
                if not isinstance(value, str):
                    out.append(f"{w}: label {_show(key)} must be text, found {_show(value)}")
        out.enum(u, "status", ITEM_STATUSES, w)
        out.text(u, "from", w, required=False, nullable=True, empty_ok=True)
    return out


# --- scoresheet.json --------------------------------------------------------------------------

def check_scoresheet(d, questions=None) -> list[str]:
    out = _Out()
    if not out.obj(d, "scoresheet.json"):
        return out
    w = "score sheet"
    out.boolean(d, "computable", w)
    out.enum(d, "status", ITEM_STATUSES, w)
    computable = d.get("computable") is not False
    out.text(d, "plain", w, required=not computable, empty_ok=computable)
    if not computable and not any(k in d for k in ("sections", "overall", "bands")):
        return out
    sections = out.list_of(d, "sections", w, required=computable)
    if computable and not sections:
        out.append(f"{w}: there are no sections")
    out.unique_ids(sections, "section")
    ids = []
    for i, s in enumerate(sections):
        ws = _where("section", s, i)
        if not out.obj(s, ws):
            continue
        if isinstance(s.get("id"), str):
            ids.append(s["id"])
        out.text(s, "name", ws)
        out.boolean(s, "percent", ws)
        out.enum(s, "rounding", ROUNDINGS, ws)
    overall = d.get("overall", _MISSING)
    if overall is _MISSING:
        if computable:
            out.append(f"{w}: overall is missing")
    elif out.obj(overall, f"{w}: overall"):
        wo = f"{w}: overall"
        out.enum(overall, "method", OVERALL_METHODS, wo)
        out.enum(overall, "rounding", ROUNDINGS, wo)
        out.boolean(overall, "use_rounded_sections", wo, required=False)
        if overall.get("method") == "weighted_mean_of_section_percents":
            weights = overall.get("weights")
            if out.obj(weights, f"{wo}: weights"):
                for sid, weight in weights.items():
                    if sid not in ids:
                        out.append(f"{wo}: weights name {_show(sid)}, which is not a section")
                    if not _is_num(weight) or weight < 0:
                        out.append(f"{wo}: weight for {_show(sid)} must be a number of 0 or more")
                for sid in ids:
                    if not _member(sid, weights):
                        out.append(f"{wo}: section {_show(sid)} has no weight")
    for i, group in enumerate(out.list_of(d, "bands", w, required=False)):
        wb = f"{w}: bands {i + 1}"
        if not out.obj(group, wb):
            continue
        of = group.get("of")
        if of != "overall" and of not in ids:
            out.append(f"{wb}: \"of\" is {_show(of)}, expected \"overall\" or a section id "
                       f"({_one_of(ids) or 'none'})")
        names = set()
        for j, band in enumerate(out.list_of(group, "bands", wb)):
            wbb = f"{wb}, band {j + 1}"
            if not out.obj(band, wbb):
                continue
            out.text(band, "name", wbb)
            if _member(band.get("name"), names):
                out.append(f"{wbb}: name {_show(band.get('name'))} is used twice")
            if isinstance(band.get("name"), str):
                names.add(band["name"])
            if not _is_num(band.get("min")):
                out.append(f"{wbb}: min must be a number, found {_show(band.get('min'))}")
            for sid in out.str_list(band, "requires_full", wbb, required=False, nullable=True):
                if sid not in ids:
                    out.append(f"{wbb}: requires_full names {_show(sid)}, which is not a section")
    qs = _items(questions, "questions")
    if qs:
        used = {q["section"] for q in qs if isinstance(q, dict) and isinstance(q.get("section"), str)}
        for sid in ids:
            if sid not in used:
                out.append(f"{w}: section {_show(sid)} has no questions")
    return out


# --- rules.json -------------------------------------------------------------------------------

def check_rules(d, questions, units) -> list[str]:
    out = _Out()
    if not out.obj(d, "rules.json"):
        return out
    qids, uids = _ids(questions, "questions"), _ids(units, "units")
    rules = out.list_of(d, "rules", "rules.json")
    out.unique_ids(rules, "rule")
    for i, r in enumerate(rules):
        w = _where("rule", r, i)
        if not out.obj(r, w):
            continue
        out.enum(r, "kind", RULE_KINDS, w)
        qlist = out.str_list(r, "question_ids", w)
        if not qlist and isinstance(r.get("question_ids"), list):
            out.append(f"{w}: question_ids is empty")
        for qid in qlist:
            if not _member(qid, qids):
                out.append(f"{w}: question {_show(qid)} is not in questions.json")
        for row in out.str_list(r, "rows", w, nullable=True):
            if not _member(row, uids):
                out.append(f"{w}: row {_show(row)} is not in units.json")
        out.text(r, "ask", w)
        options = out.list_of(r, "options", w)
        option_ids = []
        for j, opt in enumerate(options):
            wo = f"{w}: option {j + 1}"
            if out.obj(opt, wo):
                out.text(opt, "id", wo)
                out.text(opt, "text", wo)
                if opt.get("id") in option_ids:
                    out.append(f"{wo}: id {_show(opt.get('id'))} is used twice")
                option_ids.append(opt.get("id"))
        rec = r.get("recommended")
        if rec is not None and rec not in option_ids:
            out.append(f"{w}: recommended option {_show(rec)} is not one of its options "
                       f"({_one_of(map(str, option_ids)) or 'none'})")
        out.text(r, "why", w, required=False, nullable=True, empty_ok=True)
        stakes = r.get("stakes")
        if stakes is not None and out.obj(stakes, f"{w}: stakes"):
            if not (_is_int(stakes.get("cells")) and stakes["cells"] >= 0):
                out.append(f"{w}: stakes cells must be a whole number of 0 or more")
            if not (_is_num(stakes.get("points")) and stakes["points"] >= 0):
                out.append(f"{w}: stakes points must be a number of 0 or more")
        out.text(r, "last_year_note", w, required=False, nullable=True, empty_ok=True)
        out.enum(r, "status", RULE_STATUSES, w)
        decision = r.get("decision")
        if r.get("status") == "decided":
            if decision is None:
                out.append(f"{w} is decided but has no decision")
            elif out.obj(decision, f"{w}: decision"):
                wd = f"{w}: decision"
                opt = decision.get("option")
                if opt is not None and opt not in option_ids:
                    out.append(f"{wd}: option {_show(opt)} is not one of its options")
                out.text(decision, "text", wd)
                out.stamp(decision, "at", wd, nullable=False)
                out.enum(decision, "via", VIA, wd)
        elif decision is not None:
            out.append(f"{w} has a decision but its status is {_show(r.get('status'))}; "
                       "decision must be null unless the rule is decided")
        out.enum(r, "raised_by", RAISED_BY, w)
    return out


# --- where_to_look.json and needs.json --------------------------------------------------------

def check_where_to_look(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "where_to_look.json"):
        return out
    for i, entry in enumerate(out.list_of(d, "plan", "where_to_look.json", required=False)):
        w = f"plan {i + 1}"
        if not out.obj(entry, w):
            continue
        f = entry.get("for", _MISSING)
        if f is _MISSING:
            out.append(f"{w}: for is missing")
        elif f == "all":
            pass
        elif isinstance(f, dict) and set(f) == {"rows"}:
            out.str_list(f, "rows", f"{w}: for")
        elif isinstance(f, dict) and f:
            out.label_filter(f, w, "for")
        else:
            out.append(f"{w}: for is {_show(f)}, expected \"all\", {{label: [values]}} or {{\"rows\": [ids]}}")
        places = out.list_of(entry, "places", w)
        if not places and isinstance(entry.get("places"), list):
            out.append(f"{w}: places is empty")
        for j, place in enumerate(places):
            wp = f"{w}, place {j + 1}"
            if out.obj(place, wp):
                out.text(place, "what", wp)
                out.text(place, "url", wp, required=False, nullable=True)
                out.text(place, "note", wp, required=False, nullable=True, empty_ok=True)
    for i, item in enumerate(out.list_of(d, "last_year", "where_to_look.json", required=False)):
        w = f"last_year {i + 1}"
        if out.obj(item, w):
            out.text(item, "row", w)
            out.text(item, "url", w)
            out.text(item, "title", w, required=False, nullable=True, empty_ok=True)
    return out


def check_needs(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "needs.json"):
        return out
    needs = out.list_of(d, "needs", "needs.json")
    out.unique_ids(needs, "need")
    for i, n in enumerate(needs):
        w = _where("need", n, i)
        if not out.obj(n, w):
            continue
        out.text(n, "what", w)
        out.str_list(n, "why", w)
        out.str_list(n, "rows", w, nullable=True)
        out.enum(n, "status", NEED_STATUSES, w)
        out.str_list(n, "documents", w)
    return out


# --- documents/<doc>/meta.json and pages.json -------------------------------------------------

def check_meta(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "meta.json"):
        return out
    doc = d.get("id")
    w = f"document {_show(doc)}"
    if not (isinstance(doc, str) and _DOC_RE.match(doc)):
        out.append(f"{w}: id must be \"d-\" and 10 lowercase hex characters")
    out.text(d, "title", w, empty_ok=True)
    out.text(d, "publisher", w, required=False, nullable=True, empty_ok=True)
    out.text(d, "url", w, required=False, nullable=True)
    out.text(d, "archive_url", w, required=False, nullable=True)
    out.boolean(d, "kept", w)
    out.date(d, "date", w)
    out.enum(d, "date_basis", DATE_BASES, w, required=False, nullable=True)
    out.stamp(d, "fetched", w, nullable=False)
    out.enum(d, "how", HOW, w)
    out.boolean(d, "provided_by_owner", w)
    out.text(d, "language", w, required=False, nullable=True)
    out.enum(d, "host_type", HOST_TYPES, w)
    out.text(d, "file", w, required=False, nullable=True)
    out.text(d, "sha1", w, required=False, nullable=True)
    out.text(d, "capture", w, required=False, nullable=True)    # the id of the current pages
    out.enum(d, "text_source", TEXT_SOURCES, w, required=False, nullable=True)   # where a shell's words came from
    out.text(d, "not_kept_why", w, required=False, nullable=True)                 # why there is no copy
    ocr_pages = d.get("ocr_pages")      # pages whose words were read from a picture (text_source "ocr")
    if ocr_pages is not None and not (isinstance(ocr_pages, list) and all(_is_int(n) and n >= 1 for n in ocr_pages)):
        out.append(f"{w}: ocr_pages must be a list of page numbers, found {_show(ocr_pages)}")
    pages = d.get("pages")
    if pages is not None and not (_is_int(pages) and pages >= 0):
        out.append(f"{w}: pages must be a whole number or null, found {_show(pages)}")
    if d.get("kept") is False and d.get("file") is not None:
        out.append(f"{w} is not kept, so file must be null")
    if (d.get("how") == "provided") != (d.get("provided_by_owner") is True) and \
            isinstance(d.get("provided_by_owner"), bool) and d.get("how") in HOW:
        out.append(f"{w}: provided_by_owner must be true exactly when how is \"provided\"")
    if d.get("how") in ("plan", "search", "last_year") and not _text(d.get("url")):
        out.append(f"{w}: a document found by {d['how']} needs its url")
    today = d.get("todays_version")   # absent or null; {"fetched", "why"}: the live page, kept after a past cut-off
    if today is not None and not (isinstance(today, dict) and _text(today.get("why"))
                                  and isinstance(today.get("fetched"), str) and is_date(today["fetched"])):
        out.append(f"{w}: todays_version must be null or {{\"fetched\": date, \"why\": text}}, found {_show(today)}")
    weak = d.get("weak")              # absent or null: no mark; false: cleared; {"why"}: weak
    if weak is not None and weak is not False and not (isinstance(weak, dict) and _text(weak.get("why"))):
        out.append(f"{w}: weak must be null, false or {{\"why\": text}}, found {_show(weak)}")
    return out


def check_pages(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "pages.json"):
        return out
    for i, page in enumerate(out.list_of(d, "pages", "pages.json")):
        w = f"page entry {i + 1}"
        if out.obj(page, w):
            if not (_is_int(page.get("page")) and page["page"] >= 1):
                out.append(f"{w}: page must be a whole number of 1 or more")
            if not isinstance(page.get("text"), str):
                out.append(f"{w}: text must be text")
            if "ocr" in page and not isinstance(page["ocr"], bool):
                out.append(f"{w}: ocr must be true or false, found {_show(page['ocr'])}")
            if "images" in page and not (isinstance(page["images"], list) and all(_text(x) for x in page["images"])):
                out.append(f"{w}: images must be a list of file names, found {_show(page['images'])}")
    return out


# --- found/<row>.json -------------------------------------------------------------------------

def check_found(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "found file"):
        return out
    w = f"found {_show(d.get('row'))}"
    out.text(d, "row", w)
    out.str_list(d, "documents", w)
    for i, item in enumerate(out.list_of(d, "expected_not_found", w)):
        wi = f"{w}: expected_not_found {i + 1}"
        if out.obj(item, wi):
            out.text(item, "what", wi)
            out.text(item, "where_looked", wi, empty_ok=True)
            out.str_list(item, "questions", wi, required=False)     # what a scorer needed it for (ingest.record_missing)
            out.text(item, "by", wi, required=False)
    out.stamp(d, "second_search", w)
    return out


# --- answers/<row>.json -----------------------------------------------------------------------

def _check_cell(out, qid, c, q):
    w = f"cell {_show(qid)}"
    if not out.obj(c, w):
        return
    if q is None:
        out.append(f"{w} is not a question in questions.json")
    state = c.get("state")
    out.enum(c, "state", CELL_STATES, w)
    value, points = c.get("value"), c.get("points")
    if state in ("not_applicable", "not_run"):
        for key in ("value", "points"):
            if c.get(key) is not None:
                out.append(f"{w}: {key} must be null when state is {_show(state)}")
    elif state in ("answered", "no_evidence"):
        if "value" not in c or value is None:
            out.append(f"{w}: value is missing")
        if not _is_num(points):
            out.append(f"{w}: points must be a number, found {_show(points)}")
        if q is not None and value is not None:
            level_points = _level_points(q, value)
            if level_points is _MISSING:
                out.append(f"{w}: value {_show(value)} is not one of the question's levels "
                           f"({answers.describe(q)})")
            elif _is_num(points) and points != level_points:
                out.append(f"{w}: points {_show(points)} do not match level {_show(value)}, "
                           f"which is worth {_show(level_points)}")
            nev = q.get("no_evidence_value")
            if state == "no_evidence" and not (value == nev and type(value) is type(nev)):
                out.append(f"{w}: a no_evidence cell takes the question's no_evidence_value "
                           f"{_show(nev)}, found {_show(value)}")
    evidence = out.list_of(c, "evidence", w, required=False)
    owner = c.get("owner")
    if owner is not None and out.obj(owner, f"{w}: owner"):
        _check_owner(out, owner, f"{w}: owner", value)
    feedback = c.get("feedback")
    if feedback is not None and out.obj(feedback, f"{w}: feedback"):
        _check_feedback(out, feedback, f"{w}: feedback", value)
    checked = c.get("checked")
    if checked is not None and out.obj(checked, f"{w}: checked"):
        out.stamp(checked, "at", f"{w}: checked", nullable=False)
        out.enum(checked, "how", CHECKED_HOW, f"{w}: checked")
    if state == "answered" and not evidence and c.get("evidence") in (None, []) and owner is None \
            and feedback is None:
        out.append(f"{w}: an answered cell needs at least one evidence item")
    for i, ev in enumerate(evidence):
        we = f"{w}: evidence {i + 1}"
        if not out.obj(ev, we):
            continue
        out.text(ev, "doc", we)
        page = ev.get("page")
        if page is not None and not (_is_int(page) and page >= 1):
            out.append(f"{we}: page must be a whole number of 1 or more, or null, found {_show(page)}")
        out.text(ev, "quote", we)
        out.text(ev, "quote_en", we, required=False, nullable=True)
        out.text(ev, "language", we, required=False, nullable=True)
        out.text(ev, "capture", we, required=False, nullable=True)   # the capture it was scored from
        if ev.get("language") == "en" and ev.get("quote_en") != ev.get("quote"):
            out.append(f"{we}: quote_en must equal quote when the language is en")
    out.str_list(c, "rules", w, required=False)
    out.boolean(c, "judgement", w, required=False)
    unsure = c.get("unsure")
    if unsure is not None and out.obj(unsure, f"{w}: unsure"):
        out.enum(unsure, "why", UNSURE_WHY, f"{w}: unsure")
        out.text(unsure, "note", f"{w}: unsure", empty_ok=True)
        out.boolean(unsure, "handled", f"{w}: unsure", required=False)
        out.text(unsure, "missing", f"{w}: unsure", required=False)   # the document the scorer needed (ingest.py)
    out.text(c, "note", w, required=False, nullable=True, empty_ok=True)
    out.text(c, "run", w, required=False, nullable=True)
    out.boolean(c, "out_of_date", w, required=False)


def _check_owner(out, o, w, value):
    """A cell's `owner`: the owner's answer, which stands in the cell. `agreed` is Claude's verdict
    on it (null until Claude has checked it), `why` Claude's reason when it disagrees, `claude` the
    value Claude's re-read gave, `kept` the owner keeping their answer after Claude disagreed."""
    if o.get("value") is None:
        out.append(f"{w}: value is missing")
    elif not (o.get("value") == value and type(o.get("value")) is type(value)):
        out.append(f"{w}: the cell's value must be the owner's value {_show(o.get('value'))}")
    out.text(o, "correction", w)
    agreed = o.get("agreed", _MISSING)
    if agreed is _MISSING or not (agreed is None or isinstance(agreed, bool)):
        out.append(f"{w}: agreed must be true, false or null")
    out.text(o, "why", w, required=False, nullable=True)
    out.boolean(o, "kept", w)
    if o.get("kept") is True and agreed is not False:
        out.append(f"{w}: kept is only for an answer Claude disagreed with")


def _check_feedback(out, f, w, value):
    """A cell's `feedback`: what the feedback round decided about it. `kept` is true when the owner
    kept our answer after a reply, false when the answer was changed after it (the value is then the
    one the owner chose); `text` is the sentence the page shows ("changed after Acme's reply, 2026-11-02"),
    `reason` the owner's reason to go back to the business, `from` the answer before the change and
    `previous` the whole cell as it was (so the change can be undone); `rescored` is what a later
    scorer run read, kept beside the owner's decision instead of replacing it."""
    out.text(f, "unit", w)
    out.text(f, "text", w)
    out.date(f, "date", w, nullable=False)
    out.boolean(f, "kept", w)
    out.text(f, "reason", w, required=False, nullable=True, empty_ok=True)
    for key in ("previous", "rescored"):
        if f.get(key) is not None:
            out.obj(f.get(key), f"{w}: {key}")
    if f.get("kept") is False and "from" not in f:
        out.append(f"{w}: from is missing")


def check_answers(d, questions) -> list[str]:
    out = _Out()
    if not out.obj(d, "answers file"):
        return out
    by_id = {q["id"]: q for q in _items(questions, "questions")
             if isinstance(q, dict) and isinstance(q.get("id"), str)}
    out.text(d, "row", f"answers {_show(d.get('row'))}")
    cells = d.get("cells")
    if out.obj(cells, "answers: cells"):
        for qid, c in cells.items():
            _check_cell(out, qid, c, by_id.get(qid))
    return out


# --- checks/<row>.json ------------------------------------------------------------------------

def check_checks(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "checks file"):
        return out
    w = f"checks {_show(d.get('row'))}"
    out.text(d, "row", w)
    out.list_of(d, "row_flags", w, required=False)
    cells = d.get("cells")
    if out.obj(cells, f"{w}: cells"):
        for qid, c in cells.items():
            wc = f"cell {_show(qid)}"
            if not out.obj(c, wc):
                continue
            for flag in out.list_of(c, "flags", wc):
                if flag not in FLAGS:
                    out.append(f"{wc}: unknown flag {_show(flag)}, expected one of: {_one_of(FLAGS)}")
            sr = c.get("second_read")
            if sr is not None and out.obj(sr, f"{wc}: second_read"):
                if "value" not in sr:
                    out.append(f"{wc}: second_read value is missing")
                out.boolean(sr, "agrees", f"{wc}: second_read")
                out.text(sr, "run", f"{wc}: second_read", required=False, nullable=True)
    return out


# --- runs/<run>.json --------------------------------------------------------------------------

def check_run(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "run file"):
        return out
    w = f"run {_show(d.get('id'))}"
    out.text(d, "id", w)
    out.enum(d, "kind", RUN_KINDS, w)
    out.str_list(d, "questions", w, nullable=True)
    out.str_list(d, "rows", w, nullable=True)
    out.stamp(d, "started", w, nullable=False)
    out.stamp(d, "finished", w)
    usage = d.get("usage", {})
    if out.obj(usage, f"{w}: usage"):
        for row, steps in usage.items():
            if not out.obj(steps, f"{w}: usage for {_show(row)}"):
                continue
            for step in USAGE_STEPS:
                v = steps.get(step)
                if v is not None and not (_is_int(v) and v >= 0):
                    out.append(f"{w}: usage for {_show(row)} {step} must be a whole number of "
                               f"tokens or null, found {_show(v)}")
    group_usage = d.get("group_usage", {})
    if out.obj(group_usage, f"{w}: group_usage"):
        for group, v in group_usage.items():
            if v is not None and not (_is_int(v) and v >= 0):
                out.append(f"{w}: group_usage for {_show(group)} must be a whole number of tokens or null")
    models = d.get("models", {})
    if out.obj(models, f"{w}: models"):
        for role, model in models.items():
            if not isinstance(model, str):
                out.append(f"{w}: model for {_show(role)} must be text")

    def receipt(r, where):
        if out.obj(r, where):
            for key in RECEIPT_KEYS:
                v = r.get(key)
                if not (_is_int(v) and v >= 0):
                    out.append(f"{where}: {key} must be a whole number of 0 or more")
    for row, steps in (d.get("receipts") or {}).items():       # the split behind usage, when known
        if out.obj(steps, f"{w}: receipts for {_show(row)}"):
            for step, r in steps.items():
                if step not in USAGE_STEPS:
                    out.append(f"{w}: receipts for {_show(row)} name step {_show(step)}, expected one of: "
                               f"{_one_of(USAGE_STEPS)}")
                receipt(r, f"{w}: receipt for {_show(row)} {step}")
    for group, r in (d.get("group_receipts") or {}).items():
        receipt(r, f"{w}: group receipt for {_show(group)}")
    stops = d.get("stops", {})        # rows whose scorer was stopped before it finished (runs.record_stop)
    if out.obj(stops, f"{w}: stops"):
        for row, entries in stops.items():
            for k, e in enumerate(entries if isinstance(entries, list) else [None]):
                we = f"{w}: stop {k + 1} for {_show(row)}"
                if out.obj(e, we):
                    out.enum(e, "why", ("budget", "turns", "time"), we)
                    out.str_list(e, "left", we)
    return out


# --- held-back results.json and rebuild.json --------------------------------------------------

def check_held_back(d, history: bool = False) -> list[str]:
    """`history`: an older edition in `history/`, which must name its edition."""
    out = _Out()
    if not out.obj(d, "results.json"):
        return out
    w = "held-back results"
    out.enum(d, "level", LEVELS, w)
    out.text(d, "edition", w, required=history, nullable=not history)
    rows = d.get("rows")
    if out.obj(rows, f"{w}: rows"):
        for row, values in rows.items():
            wr = f"{w}: row {_show(row)}"
            if not out.obj(values, wr):
                continue
            for key, v in values.items():
                if not _text(key):
                    out.append(f"{wr}: empty key")
                elif key.startswith("band:"):
                    if v is not None and not isinstance(v, str):
                        out.append(f"{wr}: {key} must be a band name, found {_show(v)}")
                elif v is not None and not _is_num(v) and not (d.get("level") == "question" and _text(v)):
                    # At question level an answer may be text: a level that is not a figure (NR, NC).
                    out.append(f"{wr}: {key} must be a number, found {_show(v)}")
    tol = d.get("tolerance")
    if tol is not None and not (_is_num(tol) and tol >= 0):
        out.append(f"{w}: tolerance must be a number of 0 or more, or null, found {_show(tol)}")
    out.text(d, "original", w, required=False, nullable=True)
    out.text(d, "notes", w, required=False, nullable=True, empty_ok=True)
    return out


def check_rebuild(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "rebuild.json"):
        return out
    w = "rebuild check"
    for key in ("checked", "matched"):
        if not (_is_int(d.get(key)) and d[key] >= 0):
            out.append(f"{w}: {key} must be a whole number of 0 or more")
    for i, m in enumerate(out.list_of(d, "mismatches", w)):
        if out.obj(m, f"{w}: mismatch {i + 1}"):
            for key in ("row", "key"):
                out.text(m, key, f"{w}: mismatch {i + 1}")
    out.stamp(d, "at", w)
    return out


# --- compare/compare.json ---------------------------------------------------------------------

BIG_MOVE_WHY = ("big_change", "nothing_found", "band_jump")
TRENDS = ("breaks", "within", "too_short")


def _check_history(out, h, w):
    """compare.json's `history`: past editions' figures per row and key, and this run's."""
    if not out.obj(h, w):
        return
    editions = out.str_list(h, "editions", w)
    if len(set(editions)) != len(editions):
        out.append(f"{w}: editions lists an edition twice")
    levels = h.get("levels")
    if levels is not None and out.obj(levels, f"{w}: levels"):
        for ed, level in levels.items():
            if ed not in editions or level not in LEVELS:
                out.append(f"{w}: levels {_show(ed)}: {_show(level)} is not a level of a listed edition")

    def figure(x, where):
        if not (_is_num(x) or isinstance(x, str)):
            out.append(f"{where} must be a number or a band name, found {_show(x)}")

    rows = h.get("rows")
    if out.obj(rows, f"{w}: rows"):
        for row, keys in rows.items():
            if not out.obj(keys, f"{w}: rows {_show(row)}"):
                continue
            for key, values in keys.items():
                wk = f"{w}: rows {_show(row)} {_show(key)}"
                if not out.obj(values, wk):
                    continue
                for ed, x in values.items():
                    if ed not in editions:
                        out.append(f"{wk} names {_show(ed)}, which is not in editions")
                    figure(x, wk)
    this_run = h.get("this_run", {})
    if out.obj(this_run, f"{w}: this_run"):
        for row, keys in this_run.items():
            if out.obj(keys, f"{w}: this_run {_show(row)}"):
                for key, x in keys.items():
                    figure(x, f"{w}: this_run {_show(row)} {_show(key)}")


def _check_big_moves(out, moves, w):
    for i, m in enumerate(moves):
        wm = f"{w}: big move {i + 1}"
        if not out.obj(m, wm):
            continue
        out.text(m, "row", wm)
        out.text(m, "key", wm)
        out.enum(m, "why", BIG_MOVE_WHY, wm)
        out.enum(m, "reason", REASONS, wm)
        out.enum(m, "trend", TRENDS, wm)
        out.text(m, "text", wm)


def check_compare(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "compare.json"):
        return out
    w = "comparison"
    out.enum(d, "level", LEVELS, w)
    out.text(d, "edition", w, required=False, nullable=True)
    for i, r in enumerate(out.list_of(d, "rows", w)):
        wr = f"{w}: row {i + 1}"
        if not out.obj(r, wr):
            continue
        out.text(r, "row", wr)
        for key in ("ours", "theirs", "agree"):
            if key in r:
                out.obj(r[key], f"{wr}: {key}")
        agree = r.get("agree")
        if isinstance(agree, dict) and not all(isinstance(v, bool) for v in agree.values()):
            out.append(f"{wr}: agree values must be true or false")
    for i, dis in enumerate(out.list_of(d, "disagreements", w)):
        wd = f"{w}: disagreement {i + 1}"
        if not out.obj(dis, wd):
            continue
        out.text(dis, "row", wd)
        out.text(dis, "key", wd)
        out.enum(dis, "reason", REASONS, wd)
        out.enum(dis, "by", REASON_BY, wd)
        out.text(dis, "note", wd, required=False, nullable=True, empty_ok=True)
        out.str_list(dis, "cells", wd, required=False)
    summary = d.get("summary")
    if summary is not None and out.obj(summary, f"{w}: summary"):
        if "rows" in summary and not _is_int(summary["rows"]):
            out.append(f"{w}: summary rows must be a whole number")
    if d.get("history") is not None:
        _check_history(out, d["history"], f"{w}: history")
    _check_big_moves(out, out.list_of(d, "big_moves", w, required=False), w)
    return out


# --- viewer edits and edits-applied.json ------------------------------------------------------

def check_edits(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "edits file"):
        return out
    edits = out.list_of(d, "edits", "edits", required=False)
    decisions = out.list_of(d, "decisions", "decisions", required=False)
    out.unique_ids(list(edits) + list(decisions), "edit or decision")
    for i, e in enumerate(edits):
        w = _where("edit", e, i)
        if not out.obj(e, w):
            continue
        target = e.get("target")
        out.enum(e, "target", EDIT_TARGETS, w)
        out.text(e, "target_id", w)
        field = e.get("field")
        if _member(target, EDIT_FIELDS) and field not in EDIT_FIELDS[target]:
            out.append(f"{w}: field {_show(field)} cannot be edited on a {target}; "
                       f"editable: {_one_of(EDIT_FIELDS[target])}")
        value = e.get("value", _MISSING)
        if value is _MISSING:
            out.append(f"{w}: value is missing")
        elif target == "confirm" and value is not True:
            out.append(f"{w}: a confirm edit's value must be true")
        elif field == "levels_points":
            if not (isinstance(value, dict) and value and all(_is_num(v) for v in value.values())):
                out.append(f"{w}: levels_points must be {{level value: points}}, found {_show(value)}")
        elif field == "applies_to":
            out.label_filter(value, w, "value")
        elif target == "unit" and field == "labels":
            if not (isinstance(value, dict) and all(isinstance(v, str) for v in value.values())):
                out.append(f"{w}: labels must be {{label: value}}, found {_show(value)}")
        elif field in ("wording", "short", "rule", "name") and not isinstance(value, str):
            out.append(f"{w}: value must be text, found {_show(value)}")
        out.text(e, "by", w, required=False, nullable=True)
        out.stamp(e, "at", w, nullable=False)
    for i, dec in enumerate(decisions):
        w = _where("decision", dec, i)
        if not out.obj(dec, w):
            continue
        out.text(dec, "rule", w)
        out.text(dec, "option", w, required=False, nullable=True)
        out.text(dec, "text", w, required=False, nullable=True)
        skip = dec.get("skip", False)
        if not isinstance(skip, bool):
            out.append(f"{w}: skip must be true or false, found {_show(skip)}")
        elif skip:
            if dec.get("option") is not None or _text(dec.get("text")):
                out.append(f"{w}: a skip carries no option and no text")
        elif dec.get("option") is None and not _text(dec.get("text")):
            out.append(f"{w}: give an option or a text (or skip: true)")
        out.text(dec, "by", w, required=False, nullable=True)
        out.stamp(dec, "at", w, nullable=False)
    return out


def check_edits_applied(d) -> list[str]:
    out = _Out()
    if out.obj(d, "edits-applied.json"):
        out.str_list(d, "applied", "edits-applied")
    return out


# --- the owner's corrections (the viewer's `corrections` collection) and corrections.json ------

def _check_correction(out, c, w, recorded: bool = False):
    """One correction: the owner's answer (`value`, with `reason`), evidence (a quote and where it
    is: a document saved for the row with its page, a link, or an uploaded file), or both; or
    `keep: true`, keeping an answer Claude disagreed with. The host adds `evidence.local`, where
    it saved an upload."""
    out.text(c, "row", w)
    out.text(c, "qid", w)
    out.text(c, "reason", w, required=False, nullable=True)
    out.text(c, "by", w, required=False, nullable=True)
    out.stamp(c, "at", w, nullable=not recorded)
    keep = c.get("keep", False)
    if not isinstance(keep, bool):
        out.append(f"{w}: keep must be true or false, found {_show(keep)}")
        keep = False
    ev = c.get("evidence")
    if ev is None:
        ev = {}
    elif not out.obj(ev, f"{w}: evidence"):
        return
    we = f"{w}: evidence"
    for key in ("quote", "link", "asset", "file", "local"):
        out.text(ev, key, we, required=False, nullable=True)
    doc, page, link = ev.get("doc"), ev.get("page"), ev.get("link")
    if doc is not None and not (isinstance(doc, str) and _DOC_RE.match(doc)):
        out.append(f"{we}: doc must be a document id like d-0123456789, found {_show(doc)}")
    if page is not None and not (_is_int(page) and page >= 1):
        out.append(f"{we}: page must be a whole number of 1 or more, or null, found {_show(page)}")
    if _text(link) and not re.match(r"https?://", link.strip(), re.I):
        out.append(f"{we}: link must start with http:// or https://")
    places = [k for k in EVIDENCE_PLACES if _text(ev.get(k))]
    quote = _text(ev.get("quote"))
    if keep:
        if c.get("value") is not None or quote or places:
            out.append(f"{w}: keep carries no answer and no evidence")
        return
    if len(places) > 1:
        out.append(f"{we}: give one place for the quote (a saved document, a link or a file), "
                   f"found {', '.join(places)}")
    if quote and not places:
        out.append(f"{we}: say where the quote is: a saved document and page, a link or a file")
    if places and not quote:
        out.append(f"{we}: give the words to look for, copied exactly")
    if _text(doc) and page is None:
        out.append(f"{we}: a saved document needs the page the quote is on")
    if c.get("value") is None and not quote:
        out.append(f"{w}: give an answer, some evidence, or both")


def check_corrections(d) -> list[str]:
    """The file `apply-corrections --file` reads: {"corrections": [...]}, each the document's own
    fields with id set to the document's id."""
    out = _Out()
    if not out.obj(d, "corrections file"):
        return out
    items = out.list_of(d, "corrections", "corrections")
    out.unique_ids(items, "correction")
    for i, c in enumerate(items):
        w = _where("correction", c, i)
        if out.obj(c, w):
            _check_correction(out, c, w)
    return out


def check_corrections_record(d) -> list[str]:
    """corrections.json: every correction handled, each round kept, with where the code found the
    quote, what Claude must re-read and Claude's verdict."""
    out = _Out()
    if not out.obj(d, "corrections.json"):
        return out
    items = out.list_of(d, "corrections", "corrections")
    out.unique_ids(items, "correction")
    for i, c in enumerate(items):
        w = _where("correction", c, i)
        if not out.obj(c, w):
            continue
        _check_correction(out, c, w, recorded=True)
        out.enum(c, "status", CORRECTION_STATUSES, w)
        out.stamp(c, "handled", w, nullable=False)
        found, reread = c.get("found"), c.get("reread")
        if found is not None and not (isinstance(found, dict) and _text(found.get("doc"))):
            out.append(f"{w}: found must be null or {{doc, page}}")
        if reread is not None and not (isinstance(reread, dict) and _text(reread.get("question"))):
            out.append(f"{w}: reread must be null or {{row, question, doc, page}}")
        if c.get("status") == "to_check" and reread is None:
            out.append(f"{w}: a correction waiting for Claude says what to re-read (reread)")
        verdict = c.get("verdict")
        if verdict is None:
            if c.get("status") == "checked":
                out.append(f"{w}: a checked correction needs a verdict")
            continue
        wv = f"{w}: verdict"
        if not out.obj(verdict, wv):
            continue
        agree = verdict.get("agree", _MISSING)
        if agree is _MISSING or not (agree is None or isinstance(agree, bool)):
            out.append(f"{wv}: agree must be true, false or null")
        out.text(verdict, "why", wv, required=False, nullable=True)
        out.enum(verdict, "by", VERDICT_BY, wv)
        out.text(verdict, "run", wv, required=False, nullable=True)
        out.stamp(verdict, "at", wv, nullable=False)
        if agree is False and not _text(verdict.get("why")):
            out.append(f"{wv}: a verdict that disagrees says why")
    return out


# --- control.json (run control) ----------------------------------------------------------------

CONTROL_STOP_AFTER = ("find", "score")


def check_control(d) -> list[str]:
    out = _Out()
    if not out.obj(d, "control.json"):
        return out
    w = "control"
    out.boolean(d, "paused", w)
    out.boolean(d, "stop_now", w)
    out.text(d, "stop_after_row", w, nullable=True)
    scope = d.get("scope", _MISSING)
    if scope is _MISSING:
        out.append(f"{w}: scope is missing")
    elif out.obj(scope, f"{w}: scope"):
        for key in ("rows", "questions"):
            if scope.get(key, _MISSING) is _MISSING:
                out.append(f"{w}: scope.{key} is missing (null means all)")
            elif scope[key] == []:
                out.append(f"{w}: scope.{key} must name at least one, or be null for all")
            elif scope[key] is not None:
                out.str_list(scope, key, f"{w}: scope")
    out.enum(d, "stop_after", CONTROL_STOP_AFTER, w, nullable=True)
    if "reruns" in d and not (isinstance(d["reruns"], list) and all(isinstance(x, dict) for x in d["reruns"])):
        out.append(f"{w}: reruns must be a list of objects")
    out.str_list(d, "applied", w)
    out.stamp(d, "updated", w)
    ok = d.get("rows_ok", {})            # row cards the owner set aside (control.py row_ok)
    if out.obj(ok, f"{w}: rows_ok"):
        for row, marks in ok.items():
            if out.obj(marks, f"{w}: rows_ok for {_show(row)}"):
                for what, when in marks.items():
                    if what not in ("stopped", "slow") or not isinstance(when, str):
                        out.append(f"{w}: rows_ok for {_show(row)} holds {_show(what)}; expected stopped or slow with a time")
    return out


# --- the whole folder -------------------------------------------------------------------------

def _load(path: Path, rel: str, probs: list, required: bool = False):
    if not path.is_file():
        if required:
            probs.append(f"{rel}: missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        probs.append(f"{rel}: not valid JSON ({e.msg} at line {e.lineno})")
    except UnicodeDecodeError:
        probs.append(f"{rel}: not valid UTF-8 text")
    return None


def _add(probs: list, rel: str, problems) -> None:
    probs.extend(f"{rel}: {p}" for p in problems)


def check_folder(project: Project) -> list[str]:
    """Every shape check across the folder, plus the checks that span files. Each problem starts
    with the file it is in, relative to the project folder (held-back files start with the
    held-back folder's name)."""
    root = project.path
    probs: list[str] = []
    if not root.is_dir():
        return [f"{root}: the project folder does not exist"]

    proj = _load(root / "project.json", "project.json", probs, required=True)
    if proj is not None:
        _add(probs, "project.json", check_project(proj))
    proj = proj if isinstance(proj, dict) else {}
    labels = {x for x in _list(proj.get("labels")) if isinstance(x, str)}

    files = {}
    for name in ("questions", "units", "rules", "where_to_look", "needs"):
        files[name] = _load(root / f"{name}.json", f"{name}.json", probs, required=True)
    scoresheet = _load(root / "scoresheet.json", "scoresheet.json", probs)
    questions = files["questions"] if isinstance(files["questions"], dict) else {}
    units = files["units"] if isinstance(files["units"], dict) else {}
    qlist = [q for q in _items(questions, "questions") if isinstance(q, dict)]
    qids = _ids(questions, "questions")
    uids = _ids(units, "units")

    if files["questions"] is not None:
        _add(probs, "questions.json", check_questions(files["questions"], scoresheet))
        for q in qlist:
            filters = [q.get("applies_to")] + [v.get("when") for v in _list(q.get("variants"))
                                               if isinstance(v, dict)]
            for f in filters:
                for label in (f or {}) if isinstance(f, dict) else {}:
                    if label not in labels:
                        probs.append(f"questions.json: question {_show(q.get('id'))} filters on "
                                     f"label {_show(label)}, which is not in the project's labels")
    if files["units"] is not None:
        _add(probs, "units.json", check_units(files["units"], proj))
    if scoresheet is not None:
        _add(probs, "scoresheet.json", check_scoresheet(scoresheet, questions))
        # A section sharing a question's id makes every figure ambiguous (the fish scorecard, 14 Oct
        # 2026: tiers shown as "1%" because question 1 was read as section 1).
        for s in _list(scoresheet.get("sections") if isinstance(scoresheet, dict) else None):
            if isinstance(s, dict) and isinstance(s.get("id"), str) and s["id"] in qids:
                probs.append(f"scoresheet.json: section {_show(s.get('id'))} has the same id as a question; "
                             "give the section its own id, such as S1")
    rules = files["rules"] if isinstance(files["rules"], dict) else {}
    rule_ids = _ids(rules, "rules")
    if files["rules"] is not None:
        _add(probs, "rules.json", check_rules(files["rules"], questions, units))

    sites = [x for x in _list(proj.get("results_sites")) if isinstance(x, str)]
    wtl = files["where_to_look"] if isinstance(files["where_to_look"], dict) else {}
    if sites:
        from .hosts import on_results_site
        places = [(f"plan entry {i + 1}", pl.get("url")) for i, e in enumerate(_list(wtl.get("plan")))
                  if isinstance(e, dict) for pl in _list(e.get("places")) if isinstance(pl, dict)]
        places += [(f"last_year entry {i + 1}", e.get("url")) for i, e in enumerate(_list(wtl.get("last_year")))
                   if isinstance(e, dict)]
        for where, url in places:
            site = on_results_site(url, sites) if isinstance(url, str) else None
            if site:
                probs.append(f"where_to_look.json: {where} points at {site}, where the benchmark publishes its "
                             "results: a finder would save the answers for the scorer. Point it at the "
                             f"{proj.get('unit_word') or 'row'}'s own documents instead")

    docs = {}
    doc_root = root / "documents"
    if doc_root.is_dir():
        for d in sorted(doc_root.iterdir()):
            if not d.is_dir():
                continue
            rel = f"documents/{d.name}"
            meta = _load(d / "meta.json", f"{rel}/meta.json", probs, required=True)
            if not isinstance(meta, dict):
                continue
            docs[d.name] = meta
            _add(probs, f"{rel}/meta.json", check_meta(meta))
            if meta.get("id") != d.name:
                probs.append(f"{rel}/meta.json: id {_show(meta.get('id'))} does not match its folder {d.name}")
            has_pages = (d / "pages.json").is_file()
            if meta.get("kept") is True and not has_pages:
                probs.append(f"{rel}: kept is true but there is no pages.json")
            if meta.get("kept") is False and has_pages:
                probs.append(f"{rel}: the document is not kept but has a pages.json")
            if isinstance(meta.get("file"), str) and meta["file"] and not (d / meta["file"]).is_file():
                probs.append(f"{rel}/meta.json: file {_show(meta['file'])} is not in the folder")
            if has_pages:
                pages = _load(d / "pages.json", f"{rel}/pages.json", probs)
                if pages is not None:
                    _add(probs, f"{rel}/pages.json", check_pages(pages))
                    n = len(_list(pages.get("pages"))) if isinstance(pages, dict) else None
                    if _is_int(meta.get("pages")) and n is not None and n != meta["pages"]:
                        probs.append(f"{rel}/pages.json: {n} pages, but meta.json says {meta['pages']}")

    if files["where_to_look"] is not None:
        wtl = files["where_to_look"]
        _add(probs, "where_to_look.json", check_where_to_look(wtl))
        if isinstance(wtl, dict):
            for i, entry in enumerate(_list(wtl.get("plan"))):
                f = entry.get("for") if isinstance(entry, dict) else None
                if isinstance(f, dict) and set(f) == {"rows"}:
                    for row in _list(f.get("rows")):
                        if not _member(row, uids):
                            probs.append(f"where_to_look.json: plan {i + 1} names row {_show(row)}, "
                                         "which is not in units.json")
                elif isinstance(f, dict):
                    for label in f:
                        if label not in labels:
                            probs.append(f"where_to_look.json: plan {i + 1} filters on label "
                                         f"{_show(label)}, which is not in the project's labels")
            for i, item in enumerate(_list(wtl.get("last_year"))):
                if isinstance(item, dict) and not _member(item.get("row"), uids):
                    probs.append(f"where_to_look.json: last_year {i + 1} names row "
                                 f"{_show(item.get('row'))}, which is not in units.json")

    if files["needs"] is not None:
        _add(probs, "needs.json", check_needs(files["needs"]))
        for n in _items(files["needs"], "needs"):
            if not isinstance(n, dict):
                continue
            w = f"needs.json: need {_show(n.get('id'))}"
            for qid in _list(n.get("why")):
                if not _member(qid, qids):
                    probs.append(f"{w} names question {_show(qid)}, which is not in questions.json")
            for row in _list(n.get("rows")):
                if not _member(row, uids):
                    probs.append(f"{w} names row {_show(row)}, which is not in units.json")
            for doc in _list(n.get("documents")):
                if not _member(doc, docs):
                    probs.append(f"{w} names document {_show(doc)}, which has no meta.json")

    def per_row(folder: str, check):
        for f in sorted((root / folder).glob("*.json")) if (root / folder).is_dir() else []:
            rel = f"{folder}/{f.name}"
            data = _load(f, rel, probs)
            if data is None:
                continue
            _add(probs, rel, check(data))
            if not isinstance(data, dict):
                continue
            if data.get("row") != f.stem:
                probs.append(f"{rel}: row {_show(data.get('row'))} does not match the file name")
            if f.stem not in uids:
                probs.append(f"{rel}: row {_show(f.stem)} is not in units.json")
            yield rel, data

    for rel, data in per_row("found", check_found):
        for doc in _list(data.get("documents")):
            if not _member(doc, docs):
                probs.append(f"{rel}: document {_show(doc)} has no meta.json")
    for rel, data in per_row("answers", lambda d: check_answers(d, questions)):
        cells = data.get("cells") if isinstance(data.get("cells"), dict) else {}
        for qid, c in cells.items():
            if not isinstance(c, dict):
                continue
            for ev in _list(c.get("evidence")):
                doc = ev.get("doc") if isinstance(ev, dict) else None
                if _text(doc) and doc not in docs:
                    probs.append(f"{rel}: cell {_show(qid)} cites document {_show(doc)}, "
                                 "which has no meta.json")
            for rid in _list(c.get("rules")):
                if not _member(rid, rule_ids):
                    probs.append(f"{rel}: cell {_show(qid)} rests on rule {_show(rid)}, "
                                 "which is not in rules.json")
    for rel, data in per_row("checks", check_checks):
        cells = data.get("cells") if isinstance(data.get("cells"), dict) else {}
        for qid in cells:
            if qid not in qids:
                probs.append(f"{rel}: cell {_show(qid)} is not a question in questions.json")

    run_dir = root / "runs"
    for f in sorted(run_dir.glob("*.json")) if run_dir.is_dir() else []:
        rel = f"runs/{f.name}"
        data = _load(f, rel, probs)
        if data is None:
            continue
        _add(probs, rel, check_run(data))
        if isinstance(data, dict) and data.get("id") != f.stem:
            probs.append(f"{rel}: id {_show(data.get('id'))} does not match the file name")

    for rel, check in (("compare/compare.json", check_compare),
                       ("edits-applied.json", check_edits_applied),
                       ("corrections.json", check_corrections_record)):
        data = _load(root / rel, rel, probs)
        if data is not None:
            _add(probs, rel, check(data))
    fb_state = _load(root / "feedback" / "state.json", "feedback/state.json", probs)
    feedback = editions.optional("feedback")
    if fb_state is not None and feedback is not None:      # the free edition has no feedback round to check
        _add(probs, "feedback/state.json", feedback.check_state(fb_state))
    for rel in ("estimate.json", "published.json", "report/facts.json"):
        data = _load(root / rel, rel, probs)
        if data is not None and not isinstance(data, dict):
            probs.append(f"{rel}: must be a JSON object")

    ctl = _load(root / "control.json", "control.json", probs)
    if ctl is not None:
        _add(probs, "control.json", check_control(ctl))
        ctl = ctl if isinstance(ctl, dict) else {}
        scope = ctl.get("scope") if isinstance(ctl.get("scope"), dict) else {}
        stop_row = ctl.get("stop_after_row")
        for row in _list(scope.get("rows")) + ([stop_row] if stop_row is not None else []):
            if not _member(row, uids):
                probs.append(f"control.json: names row {_show(row)}, which is not in units.json")
        for qid in _list(scope.get("questions")):
            if not _member(qid, qids):
                probs.append(f"control.json: names question {_show(qid)}, which is not in questions.json")

    held = project.held_back
    if held.is_dir():
        for name, check in (("results.json", check_held_back), ("rebuild.json", check_rebuild)):
            rel = f"{held.name}/{name}"
            data = _load(held / name, rel, probs)
            if data is not None:
                _add(probs, rel, check(data))
        for f in sorted((held / "history").glob("*.json")):      # older editions
            rel = f"{held.name}/history/{f.name}"
            data = _load(f, rel, probs)
            if data is not None:
                _add(probs, rel, check_held_back(data, history=True))
    return probs

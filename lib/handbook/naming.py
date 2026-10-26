"""The names an owner reads on every tab, checked before set-up is called done: question short
names, section names, label names and values, row names and the benchmark's own name.

Nobody from us looks over a charity's set-up, so set-up Claude reads its names once more before
handing over (BFCI, 23 Oct 2026: section names that broke in the grid were only seen by a person).
`review` finds the plain faults a rule can find; set-up Claude also reads every name itself for
plain English and the owner's own wording, which no rule can judge. Nothing here blocks a save.

Each finding: {"where": {"kind", "id", "field"}, "text", "problem"} with kind one of "question",
"section", "label", "unit", "project", "rule" (a rule's ask, last-year note or reason).
"""
from __future__ import annotations

import re
from collections import defaultdict

SHORT_CHARS = 50           # a question's short name: "a short name of 2 to 4 words" (set-up chat step 2)
SECTION_CHARS = 40         # a section name sits over its columns in the grid
PROJECT_CHARS = 80

# Names set-up writes (question shorts, section names, label names, the benchmark's name) are judged in
# full. Row names and label values are the owner's data (a company as it registered itself, a trial's
# sponsor), so only what is plainly a slip is raised there: a run's code, one value spelled two ways,
# two rows with one name. Checked on the 27 Admin projects, 23 Oct 2026.

CODE = re.compile(r"(?<![\w-])(?:[DdVvQq]\d{1,3}|S\d{1,2})(?![\w.-])")     # D22, v12, Q3, S5: a run's own codes (not v5.0)
PATHISH = re.compile(r"(?:\w+/\w+\.\w{2,4}\b|\.(?:json|csv|xlsx?|pdf|md|txt)\b)", re.I)
SNAKE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b")
CAMEL = re.compile(r"\b[a-z]+[A-Z][a-zA-Z]*\b")
SPACES = re.compile(r"^\s|\s$|\s{2,}")
TOOL_STEPS = re.compile(r"\b(?:rebuild[- ]check|hold[- ]back|held[- ]back|hb [a-z-]+|validate|\w+\.json|bundle)\b", re.I)
GENERIC_ROW = re.compile(r"\brows?\b", re.I)


def _faults(text: str, what: str, limit: int | None, owners: bool = False) -> list[str]:
    """The faults in one name; `owners` for the owner's own data (only the plain slips)."""
    t = str(text or "")
    out = []
    if not t.strip():
        return [] if owners else [f"The {what} is empty."]
    if SPACES.search(t):
        out.append(f"The {what} has extra spaces.")
    if CODE.search(t):
        out.append(f"The {what} holds a code (\"{CODE.search(t).group(0)}\") an owner won't know.")
    if PATHISH.search(t):
        out.append(f"The {what} looks like a file name.")
    if owners:
        return out
    if SNAKE.search(t) or CAMEL.search(t):
        bad = (SNAKE.search(t) or CAMEL.search(t)).group(0)
        out.append(f"The {what} has a programmer's word (\"{bad}\"): use ordinary words.")
    if len(t) > 12 and t.upper() == t and re.search(r"[A-Z]{3}", t):
        out.append(f"The {what} is all in capitals.")
    if limit and len(t.strip()) > limit:
        out.append(f"The {what} is long ({len(t.strip())} characters; at most {limit} fits well).")
    return out


def _case(text: str) -> str | None:
    """"title" for Title Case Of Three Or More Words, "sentence" for Sentence case, else None."""
    words = [w for w in re.findall(r"[A-Za-z][\w'-]*", text) if len(w) > 3]
    if len(words) < 3:
        return None
    caps = sum(1 for w in words[1:] if w[0].isupper() and not w.isupper())
    if caps == len(words) - 1:
        return "title"
    if caps == 0 and words[0][0].isupper():
        return "sentence"
    return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower().replace("&", "and"))


def review(project) -> list[dict]:
    """The plain faults in the project's names (see the module docstring)."""
    found: list[dict] = []

    def add(kind, id_, field, text, problems):
        for problem in problems:
            found.append({"where": {"kind": kind, "id": id_, "field": field}, "text": str(text or ""),
                          "problem": problem})

    p = project.read("project") or {}
    add("project", None, "name", p.get("name"), _faults(p.get("name"), "benchmark's name", PROJECT_CHARS))

    qs = project.read("questions").get("questions") or []
    shorts = defaultdict(list)
    cases = defaultdict(list)
    for q in qs:
        qid = str(q.get("id"))
        short = q.get("short") or ""
        add("question", qid, "short", short, _faults(short, f"short name of question {qid}", SHORT_CHARS))
        if short.strip():
            if re.search(r"[.?!:;]$", short.strip()):
                add("question", qid, "short", short, [f"The short name of question {qid} ends in a stop or a "
                                                      "question mark; a name needs none."])
            shorts[_norm(short)].append(qid)
            c = _case(short)
            if c:
                cases[c].append(qid)
    for ids in shorts.values():
        if len(ids) > 1:
            add("question", ids[0], "short", next(q.get("short") for q in qs if str(q.get("id")) == ids[0]),
                [f"Questions {', '.join(ids)} have the same short name; owners tell questions apart by it."])
    if len(cases["title"]) >= 2 and len(cases["sentence"]) >= 2:
        fewer = "title" if len(cases["title"]) < len(cases["sentence"]) else "sentence"
        add("question", cases[fewer][0], "short", "", [
            f"The short names mix Title Case (questions {', '.join(cases['title'][:4])}) and Sentence case "
            f"(questions {', '.join(cases['sentence'][:4])}); pick one."])

    sheet = project.read("scoresheet") or {}
    for s in sheet.get("sections") or []:
        sid = str(s.get("id"))
        add("section", sid, "name", s.get("name"), _faults(s.get("name"), f"name of section {sid}", SECTION_CHARS))

    labels = p.get("labels") or []
    for label in labels:
        # a label's name is also its key; the page shows it with spaces for underscores (labelName in grid.js)
        add("label", label, "name", label, _faults(str(label).replace("_", " "), f"label \"{label}\"", 30))
    values = defaultdict(lambda: defaultdict(set))
    units = project.read("units").get("units") or []
    names = defaultdict(list)
    for u in units:
        uid = str(u.get("id"))
        add("unit", uid, "name", u.get("name"), _faults(u.get("name"), f"name of row {uid}", None, owners=True))
        names[_norm(u.get("name"))].append(uid)
        for label, value in (u.get("labels") or {}).items():
            if isinstance(value, str) and value.strip():
                values[label][_norm(value)].add(value)
    for label, by_norm in values.items():
        for spellings in by_norm.values():
            if len(spellings) > 1:
                add("label", label, "value", sorted(spellings)[0], [
                    f"The label \"{label}\" spells one value {len(spellings)} ways ("
                    + ", ".join(f'"{v}"' for v in sorted(spellings)) + "); rows split into groups that should be one."])
            for v in spellings:
                add("label", label, "value", v, _faults(v, f"value \"{v}\" of the label \"{label}\"", None, owners=True))
    for ids in names.values():
        if len(ids) > 1 and ids[0]:
            add("unit", ids[0], "name", next(u.get("name") for u in units if str(u.get("id")) == ids[0]),
                [f"Rows {', '.join(ids[:5])} have the same name; the grid can't tell them apart."])
    if len(units) >= 3:          # a one- or two-row project may share every label honestly
        for label in labels:
            held = {str((u.get("labels") or {}).get(label) or "") for u in units}
            if len(held) == 1 and "" not in held:
                add("label", label, "name", label, [f"The label \"{label}\" is \"{held.pop()}\" for every row, so it groups "
                                                    "nothing; drop it or give it real values."])

    # What the owner reads on the Rules tab: in their words, not the tool's ("Which suppliers are rows?",
    # "The rebuild check could not run": design read, 23 Oct 2026).
    word = str(p.get("unit_word") or "row")
    for r in project.read("rules").get("rules") or []:
        rid = str(r.get("id"))
        for field in ("ask", "last_year_note", "why"):
            text = str(r.get(field) or "")
            if not text:
                continue
            step = TOOL_STEPS.search(text)
            if step:
                add("rule", rid, field, text, [f"Rule {rid}'s {field.replace('_', ' ')} names a step of the tool (\"{step.group(0)}\"); "
                                               "say what it means for the owner instead."])
            if word != "row" and GENERIC_ROW.search(text):
                add("rule", rid, field, text, [f"Rule {rid}'s {field.replace('_', ' ')} says \"rows\"; the owner calls them {word}s."])
    return found

"""The step brief: the short note Claude leaves at the top of each step in the viewer (set-up,
documents, scores, results), so the owner can see how it worked and what it chose for them. Three
parts, the same whatever the size of the benchmark:

  how      How I did it: three or four short lines on the step's process, with real figures.
  decided  What I decided for you: the choices the owner can change, six at most. First the
           step's own choices (set-up: the held-back results, the cut-off, where the row list
           came from; documents: documents marked weak, third-party copies used, one document
           used for several rows; scores: answers resting on Claude's own judgement and no rule),
           then the open rules, whose recommended reading is in use until the owner decides. Rules
           rank by points at stake; in the scores step by the answers resting on them, in the
           results step by what is at stake on the rows that moved most. Decided rules fill any
           room left ("You decided"). The rest wait in the Rules tab (`more`).
  eye      Needs your eye: up to three patterns, by points at stake, each opening the viewer on
           exactly its cases and asking how the owner could unblock it (`HELP`): send a file or a
           link for each case, which the page uploads to the `supplied` collection, or check the
           cases themselves.

Code chooses every item and figure from the files; Claude writes only the sentences. `make`
returns the brief with every sentence slot empty (None), `slots` lists the slots, `fill` writes
sentences from a {where: text} map, and `check` finds the names and numbers in the sentences that
the files do not back.

    deck/<step>.json
    {"contract": "handbook-deck/2", "step", "made", "name", "edition", "title",
     "checked": null | {"at", "sha1"},
     "slides": [
       {"id": "how", "kind": "how", "lines": [SLOT, SLOT, SLOT, SLOT], "numbers": {...}},
       {"id": "decided", "kind": "decided", "items": [ITEM], "more": {"open": n, "decided": n}},
       {"id": "eye", "kind": "eye", "items": [PATTERN]}]}
    ITEM, a rule: {"id": RULE, "kind": "rule", "rule", "status": "open" | "skipped" | "decided",
       "using": option id | null, "using_text", "recommended", "cells", "points",
       "resting"?: answers resting on it (scores), "moved_rows"?, "moved_points"? (results),
       "question_ids", "only_rows": [ids] | null (the rule's rows; null: every row), "line": SLOT}
    ITEM, a choice: {"id", "kind": "choice", "choice", "label", "open": {"tab", "rows"? |
       "cells"?}, its figures, "line": SLOT}
    PATTERN: {"id", "kind": "pattern", "pattern", "label", "points", "rows", "cells"?,
       "cases": [up to six], "more", "open": {"tab", "rows" | "cells"}, "help": {"kind", "ask"},
       "line": SLOT}

The fourth line of `how` is optional: write "" to leave it out.

A case's points at stake: in the documents step, the spread (top points less lowest) of every
question that applies to the row, since nothing is scored yet; in the scores step, the spread of
each flagged cell; in the results step, how far the figure moved from last edition.
"""
from __future__ import annotations
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from .facts import compute as compute_facts, fact_numbers, numbers_in, unbacked
from .fetch import weak_why
from .folder import HandbookError, Project, now_stamp, read_json, write_json
from .stakes import _spread, rule_stakes

CONTRACT = "handbook-deck/2"
STEPS = ("setup", "documents", "scores", "results")
TITLES = {"setup": "What I understood", "documents": "Finding the documents",
          "scores": "Scoring the answers", "results": "The results"}
HOW_LINES = 4           # the last may be left out ("")
DECIDED_SHOWN = 4       # choices and rules in "What I decided for you"
PATTERNS_SHOWN = 3      # patterns in "Needs your eye"
CASES_SHOWN = 6         # cases named on a pattern

# The patterns each step looks for, in the order used when points are level, with the label
# shown and the viewer tab that shows every case.
PATTERNS = {
    "setup": {
        "need": ("Data I still need from you", "rules"),
        "draft": ("Parts of the set-up still in draft", "setup"),
    },
    "documents": {
        "not_kept": ("A document I couldn't keep a copy of", "documents"),
        "not_found": ("Something expected that I couldn't find", "documents"),
        "none": ("No documents at all", "documents"),
    },
    "scores": {
        "weak_source": ("Answers from a copy that isn't their own", "grid"),
        "quote_not_found": ("Quotes that don't match the saved page word for word", "grid"),
        "group_differs": ("Same chain, same document, different answers by country", "grid"),
        "after_cutoff": ("Evidence dated after the cut-off", "grid"),
        "todays_version": ("Web pages saved as they are today, not as they stood at the cut-off", "grid"),
        "needs_document": ("Answers scored as if there were nothing, for want of a document I couldn't get", "grid"),
        "reads_disagree": ("A second read came out differently", "grid"),
        "document_changed": ("Saved documents that changed after the answer was scored", "grid"),
        "page_gone": ("Web pages that lost most of their text since they were saved", "grid"),
    },
    "results": {
        "big_change": ("Big changes since last edition", "results"),
        "band_jump": ("Jumps of more than one band", "results"),
        "nothing_found": ("Scored before, nothing found this time", "results"),
    },
}
# What the owner could do to unblock each pattern: "send" (a file, or a link to it, for each case),
# or "check" (look at the cases themselves). Fixed words, so they need no checking.
HELP = {
    "need": ("send", "Can you send these? Upload the file or paste a link, for each one."),
    "draft": ("check", "Can you look these over on the Set-up tab and confirm them, or tell me what to change?"),
    "not_kept": ("send", "Can you download these and send them to me? Open each link, save the file "
                         "(for a web page, print it to PDF) and upload it here."),
    "not_found": ("send", "Do you know where this is? Upload the file or paste a link."),
    "none": ("send", "Do you know where they publish this? Upload a file or paste a link."),
    "weak_source": ("send", "Do you have their own copy? Upload it, or paste a link to it, "
                            "and I'll score from that instead."),
    "quote_not_found": ("check", "Can you open a few and see whether the words are on the page? If "
                                 "you have the version that was live, send it and I'll score again."),
    "group_differs": ("check", "Can you open a few and say whether the difference is real?"),
    "after_cutoff": ("send", "Do you have a version from before the cut-off? Upload it or paste a link."),
    "todays_version": ("send", "Do you have these pages as they stood at the cut-off? Upload a saved copy or "
                               "paste a link to one, and I'll score from that."),
    "needs_document": ("send", "Do you have the document each of these needed? Upload it or paste a link, and "
                               "I'll score those answers again."),
    "reads_disagree": ("check", "Can you open these and say which reading is right?"),
    "document_changed": ("check", "Nothing to send: I'll score these again from the new copy. Say if "
                                  "you would rather I kept the answers from the old one."),
    "page_gone": ("check", "Should I look for where the text went, or use the new page as it is? Open one of "
                           "these answers and choose under its source."),
    "big_change": ("check", "Can you check a few against what you know of these {plural}?"),
    "band_jump": ("check", "Can you check a few against what you know of these {plural}?"),
    "nothing_found": ("send", "Did they publish something I missed? Upload it or paste a link."),
}
# The step's own choices: the label shown (with {plural}, the owner's word for rows) and the tab
# where the owner changes it.
CHOICES = {
    "held_back": ("Last edition's results, kept away from the scorers", "setup"),
    "cutoff": ("The cut-off date for evidence", "setup"),
    "row_list": ("Where the list of {plural} came from", "setup"),
    "weak": ("Documents I marked as weak sources", "documents"),
    "third_party": ("Copies from someone else's site", "documents"),
    "shared": ("One document used for several {plural}", "documents"),
    "judgement": ("Answers resting on my own reading, not a rule", "grid"),
}

SLOT = "line"           # the one sentence slot on every item; `how` has `lines`
OPEN = ("open", "skipped")


def _number(x):
    x = round(x, 6)
    return int(x) if float(x).is_integer() else x


def path(project: Project, step: str):
    return project.path / "deck" / f"{step}.json"


def read(project: Project, step: str) -> dict | None:
    return read_json(path(project, step)) or None


# --- what the files say ------------------------------------------------------------------------

class _Files:
    """The project files a brief reads, read once."""

    def __init__(self, project: Project):
        self.project = project
        self.proj = project.read("project")
        self.units = [u for u in project.read("units").get("units") or [] if isinstance(u, dict)]
        self.questions = [q for q in project.read("questions").get("questions") or [] if isinstance(q, dict)]
        self.by_q = {q.get("id"): q for q in self.questions}
        self.rules = [r for r in project.read("rules").get("rules") or [] if isinstance(r, dict)]
        self.docs = project.documents()
        self.qs_for = {u["id"]: project.questions_for(u) for u in self.units}
        self.names = {u["id"]: u.get("name") or u["id"] for u in self.units}
        self.plural = self.proj.get("unit_word_plural") or "rows"
        self._answers: dict[str, dict] = {}

    def row_points(self, row: str) -> float:
        return sum(_spread(q) for q in self.qs_for.get(row, []))

    def case(self, row: str, **extra) -> dict:
        return {"row": row, "name": self.names.get(row, row), **extra}

    def answers(self, row: str) -> dict:
        if row not in self._answers:
            self._answers[row] = self.project.answers(row).get("cells") or {}
        return self._answers[row]

    def found(self) -> dict[str, dict]:
        return {u["id"]: self.project.found(u["id"]) for u in self.units}


def _doc_bit(meta: dict | None, doc: str) -> dict:
    meta = meta or {}
    return {"doc": doc, "title": meta.get("title") or doc, "publisher": meta.get("publisher"),
            "url": meta.get("url"), "archive_url": meta.get("archive_url")}


def _rank(cases: list[dict]) -> list[dict]:
    return sorted(cases, key=lambda c: (-c["points"], str(c["name"]).casefold()))


def _pattern(step: str, key: str, cases: list[dict], cells: list | None = None, plural: str = "rows") -> dict:
    label, tab = PATTERNS[step][key]
    cases = _rank(cases)
    rows = list(dict.fromkeys(c["row"] for c in cases if c.get("row")))
    out = {"id": key, "kind": "pattern", "pattern": key, "label": label,
           "points": _number(sum(c["points"] for c in cases)), "rows": len(rows),
           "cases": cases[:CASES_SHOWN], "more": max(0, len(cases) - CASES_SHOWN),
           "open": {"tab": tab, "rows": rows},
           "help": {"kind": HELP[key][0], "ask": HELP[key][1].replace("{plural}", plural)}, SLOT: None}
    if cells is not None:
        out["cells"] = len(cells)
        out["open"] = {"tab": tab, "cells": cells}
    return out


def _choice(f: _Files, key: str, rows: list | None = None, cells: list | None = None, **figures) -> dict:
    label, tab = CHOICES[key]
    out = {"id": key, "kind": "choice", "choice": key, "label": label.format(plural=f.plural),
           "open": {"tab": tab}, **figures, SLOT: None}
    if rows is not None:
        out["open"]["rows"] = list(dict.fromkeys(rows))
        out["rows"] = len(out["open"]["rows"])
    if cells is not None:
        out["open"]["cells"] = cells
        out["cells"] = len(cells)
    return out


# --- each step: its figures, its own choices, its patterns -------------------------------------

def _setup(f: _Files) -> tuple[dict, list[dict], list[dict]]:
    proj = f.proj
    choices = []
    held = read_json(f.project.held_back / "results.json")
    if isinstance(held, dict) and held.get("rows"):
        choices.append(_choice(f, "held_back", edition=held.get("edition"), level=held.get("level"),
                               file=PurePosixPath(str(held.get("original") or "")).name or None,
                               held_rows=len(held["rows"])))
    if proj.get("cutoff"):
        choices.append(_choice(f, "cutoff", date=proj["cutoff"]))
    listed = [PurePosixPath(str(p.get("file") or "")).name for p in proj.get("provided") or []
              if isinstance(p, dict) and p.get("role") == "units" and p.get("file")]
    sources = Counter(str(u.get("from")) for u in f.units if u.get("from"))
    if listed or sources:
        source, n = (listed[0], len(f.units)) if listed else sources.most_common(1)[0]
        choices.append(_choice(f, "row_list", source=source, from_source=n, total=len(f.units)))

    needs = [n for n in f.project.read("needs").get("needs") or []
             if isinstance(n, dict) and n.get("status") == "needed"]
    need_cases = []
    for n in needs:
        rows = n.get("rows")
        rows = [r for r in rows if r in f.names] if isinstance(rows, list) else [u["id"] for u in f.units]
        need_cases.append({"row": None, "name": n.get("what") or n.get("id"), "need": n.get("id"),
                           "rows_affected": len(rows),
                           "points": _number(sum(f.row_points(r) for r in rows))})
    drafts = [{"row": None, "name": k.replace("_", " ").capitalize(), "item": k, "points": 0}
              for k, v in (proj.get("checklist") or {}).items() if v in ("draft", "missing")]
    patterns = [_pattern("setup", k, v, plural=f.plural) for k, v in (("need", need_cases), ("draft", drafts)) if v]
    for p in patterns:
        only_rules = p["pattern"] == "draft" and [c["item"] for c in p["cases"]] == ["rules"]
        p["open"] = {"tab": "rules" if only_rules else PATTERNS["setup"][p["pattern"]][1]}
    status = Counter(r.get("status") for r in f.rules)
    numbers = {"rows": len(f.units), "questions": len(f.questions), "labels": len(proj.get("labels") or []),
               "files_provided": len(proj.get("provided") or []), "rules": len(f.rules),
               "rules_open": status["open"], "rules_decided": status["decided"],
               "rules_skipped": status["skipped"], "needs_open": len(needs)}
    return numbers, choices, patterns


def _documents(f: _Files) -> tuple[dict, list[dict], list[dict]]:
    found = f.found()
    used: dict[str, list] = defaultdict(list)
    for row, fd in found.items():
        for d in fd.get("documents") or []:
            if isinstance(d, str):
                used[d].append(row)
    not_kept, not_found, none = [], [], []
    for u in f.units:
        row = u["id"]
        ids = [d for d in found[row].get("documents") or [] if isinstance(d, str)]
        pts = f.row_points(row)
        lost = [d for d in ids if d not in f.docs or f.docs[d].get("kept") is False]
        missing = [x for x in found[row].get("expected_not_found") or [] if x]
        if lost:
            not_kept.append(f.case(row, points=pts, documents=[_doc_bit(f.docs.get(d), d) for d in lost]))
        if missing:
            not_found.append(f.case(row, points=pts, expected=[
                str(x.get("what") or x) if isinstance(x, dict) else str(x) for x in missing]))
        if not ids:
            none.append(f.case(row, points=pts))
    patterns = [_pattern("documents", k, v, plural=f.plural) for k, v in
                (("not_kept", not_kept), ("not_found", not_found), ("none", none)) if v]

    def biggest(docs):
        d = max(docs, key=lambda d: (len(used[d]), str(f.docs.get(d, {}).get("title") or d)))
        return {**_doc_bit(f.docs.get(d), d), "used_by": len(used[d])}

    choices = []
    meta = {d: f.docs[d] for d in used if d in f.docs}
    marked = [d for d, m in meta.items() if isinstance(m.get("weak"), dict) and weak_why(m)]
    third = [d for d, m in meta.items() if d not in marked and weak_why(m) and m.get("host_type") == "third_party"]
    shared = [d for d in used if len(used[d]) > 1]
    for key, docs in (("weak", marked), ("third_party", third), ("shared", shared)):
        if docs:
            extra = {"why": [weak_why(meta[d]) for d in docs][:CASES_SHOWN]} if key == "weak" else {}
            choices.append(_choice(f, key, rows=[r for d in docs for r in used[d]], documents=len(docs),
                                   example=biggest(docs), **extra))
    how = Counter(m.get("how") for m in meta.values())
    numbers = {"rows": len(f.units),
               "rows_with_documents": sum(1 for r in found.values() if r.get("documents")),
               "rows_without_documents": len(none),
               "rows_missing_something": len(not_found),
               "documents": len(used),
               "documents_kept": sum(1 for m in meta.values() if m.get("kept") is not False),
               "documents_not_kept": len(used) - sum(1 for m in meta.values() if m.get("kept") is not False),
               "documents_weak": sum(1 for m in meta.values() if weak_why(m)),
               "documents_third_party": sum(1 for m in meta.values() if m.get("host_type") == "third_party"),
               "documents_with_archive_copy": sum(1 for m in meta.values() if m.get("archive_url")),
               "documents_shared": len(shared),
               "found_by_search": how["search"], "found_from_plan": how["plan"],
               "found_from_last_year": how["last_year"], "provided_by_owner": how["provided"]}
    return numbers, choices, patterns


def _scores(f: _Files) -> tuple[dict, list[dict], list[dict]]:
    by_flag: dict[str, dict[str, list]] = {k: defaultdict(list) for k in PATTERNS["scores"]}
    flagged_rows, own_reading, seconds = set(), [], 0
    open_ids = {r.get("id") for r in f.rules if r.get("status") in OPEN}
    for u in f.units:
        row = u["id"]
        checks = f.project.checks(row).get("cells") or {}
        for qid, c in checks.items():
            if not isinstance(c, dict):
                continue
            seconds += bool(c.get("second_read"))
            for flag in c.get("flags") or []:
                if flag in by_flag:
                    by_flag[flag][row].append(qid)
                    flagged_rows.add(row)
        for qid, a in f.answers(row).items():
            if isinstance(a, dict) and a.get("judgement") and not (set(a.get("rules") or []) & open_ids):
                own_reading.append([row, qid])
    patterns = []
    for flag, rows in by_flag.items():
        cases, cells = [], []
        for row, qids in rows.items():
            pts = sum(_spread(f.by_q[q]) for q in qids if q in f.by_q)
            cases.append(f.case(row, points=_number(pts), questions=qids))
            cells += [[row, q] for q in qids]
        if cases:
            patterns.append(_pattern("scores", flag, cases, cells, plural=f.plural))
    choices = []
    if own_reading:
        choices.append(_choice(f, "judgement", cells=own_reading,
                               rows_affected=len({r for r, _ in own_reading})))
    facts = compute_facts(f.project)
    numbers = {"cells_expected": facts["cells_expected"], "answered": facts["cells"]["answered"],
               "no_evidence": facts["cells"]["no_evidence"], "rows_scored": facts["rows_scored"],
               "rows_flagged": len(flagged_rows), "second_reads": seconds,
               **{f"flag_{k}": sum(len(v) for v in rows.values()) for k, rows in by_flag.items()}}
    return numbers, choices, patterns


def _moved(f: _Files) -> tuple[dict, list[str]]:
    compare = read_json(f.project.file("compare/compare")) or {}
    rows = list(dict.fromkeys(m.get("row") for m in compare.get("big_moves") or [] if m.get("row") in f.names))
    return compare, rows


def _results(f: _Files) -> tuple[dict, list[dict], list[dict]]:
    compare, moved = _moved(f)
    figures = {r.get("row"): r for r in compare.get("rows") or []}
    by_why: dict[str, list] = defaultdict(list)
    for m in compare.get("big_moves") or []:
        if m.get("why") not in PATTERNS["results"] or m.get("row") not in f.names:
            continue
        ours, theirs = m.get("ours"), m.get("theirs")
        if not all(isinstance(x, (int, float)) for x in (ours, theirs)):
            # A band jump names bands; its size is the move in the figure the band is for.
            key = str(m.get("key") or "").removeprefix("band:")
            r = figures.get(m["row"]) or {}
            ours, theirs = (r.get("ours") or {}).get(key), (r.get("theirs") or {}).get(key)
        size = abs(ours - theirs) if all(isinstance(x, (int, float)) for x in (ours, theirs)) else 0
        by_why[m["why"]].append(f.case(m["row"], points=_number(size), key=m.get("key"),
                                       ours=ours, theirs=theirs, said=m.get("text")))
    patterns = []
    for why, moves in by_why.items():
        # One case per row: its biggest move.
        per_row: dict[str, dict] = {}
        for m in _rank(moves):
            per_row.setdefault(m["row"], m)
        patterns.append(_pattern("results", why, list(per_row.values()), plural=f.plural))
    agree = sum(1 for r in compare.get("rows") or [] if (r.get("agree") or {}) and all(r["agree"].values()))
    facts = compute_facts(f.project)
    numbers = {"rows_scored": facts["rows_scored"], "rows_complete": facts["rows_complete"],
               "mean_overall": facts["mean_overall"], "rows_compared": len(compare.get("rows") or []),
               "rows_agreeing": agree, "big_moves": len(compare.get("big_moves") or []),
               "rows_moved": len(moved)}
    return numbers, [], patterns


GATHER = {"setup": _setup, "documents": _documents, "scores": _scores, "results": _results}


# --- the rules ------------------------------------------------------------------------------------

def _stakes(f: _Files, rule: dict, rows: list | None = None) -> dict:
    if rows is not None:
        wanted = rule.get("rows")
        rule = {**rule, "rows": [r for r in rows if wanted is None or r in wanted]}
    try:
        return rule_stakes(f.project, rule, f.questions, f.units)
    except HandbookError:
        return rule.get("stakes") or {"cells": 0, "points": 0}


def _rules(f: _Files, step: str) -> list[dict]:
    """Every open, skipped or decided rule as an item, open and skipped first, each group in the
    step's order."""
    resting: Counter = Counter()
    moved_resting: Counter = Counter()
    moved = _moved(f)[1] if step == "results" else []
    if step in ("scores", "results"):
        for u in f.units:
            for a in f.answers(u["id"]).values():
                for rid in (a.get("rules") or []) if isinstance(a, dict) else []:
                    resting[rid] += 1
                    if u["id"] in moved:
                        moved_resting[rid] += 1
    items = []
    for r in f.rules:
        status = r.get("status")
        if status not in ("open", "skipped", "decided"):
            continue
        s = _stakes(f, r)
        options = {o.get("id"): o.get("text") for o in r.get("options") or [] if isinstance(o, dict)}
        decision = r.get("decision") if status == "decided" and isinstance(r.get("decision"), dict) else None
        using = decision.get("option") if decision else r.get("recommended")
        item = {"id": r.get("id"), "kind": "rule", "rule": r.get("id"), "status": status,
                "using": using, "using_text": (decision or {}).get("text") or options.get(using),
                "recommended": r.get("recommended"), "cells": s.get("cells"), "points": s.get("points"),
                "question_ids": list(r.get("question_ids") or []), "only_rows": r.get("rows")}
        if step == "scores":
            item["resting"] = resting[r.get("id")]
            key = (-item["resting"], -(item["points"] or 0))
        elif step == "results" and moved:
            m = _stakes(f, r, moved)
            item.update(moved_rows=len(moved), moved_points=m.get("points"), resting=moved_resting[r.get("id")])
            key = (-item["resting"], -(item["moved_points"] or 0), -(item["points"] or 0))
        else:
            key = (-(item["points"] or 0), -(item["cells"] or 0))
        item[SLOT] = None
        items.append(((status == "decided",) + key + (str(r.get("id")),), item))
    return [item for _, item in sorted(items, key=lambda x: x[0])]


# --- the brief ------------------------------------------------------------------------------------

def make(project: Project, step: str) -> dict:
    """The brief for `step`, every sentence slot empty."""
    if step not in STEPS:
        raise HandbookError(f"There is no step {step!r}. Choose one of: {', '.join(STEPS)}.")
    f = _Files(project)
    numbers, choices, patterns = GATHER[step](f)
    patterns.sort(key=lambda p: (-p["points"], list(PATTERNS[step]).index(p["pattern"])))
    rules = _rules(f, step)
    shown = (choices + rules)[:DECIDED_SHOWN]
    left = rules[max(0, DECIDED_SHOWN - len(choices)):]
    slides = [{"id": "how", "kind": "how", "lines": [None] * HOW_LINES, "numbers": numbers},
              {"id": "decided", "kind": "decided", "items": shown,
               "more": {"open": sum(1 for r in left if r["status"] in OPEN),
                        "decided": sum(1 for r in left if r["status"] == "decided")}},
              {"id": "eye", "kind": "eye", "items": patterns[:PATTERNS_SHOWN]}]
    return {"contract": CONTRACT, "step": step, "made": now_stamp(),
            "name": f.proj.get("name"), "edition": f.proj.get("edition"),
            "title": TITLES[step], "checked": None, "slides": slides}


def slots(deck: dict) -> list[tuple[str, str]]:
    """Every sentence slot as `(where, text or None)`; `where` reads like "how.lines[2]",
    "decided.R17.line" or "eye.not_kept.line"."""
    out = []
    for s in deck.get("slides") or []:
        if s.get("kind") == "how":
            out += [(f"how.lines[{i}]", t) for i, t in enumerate(s.get("lines") or [])]
        for item in s.get("items") or []:
            out.append((f"{s.get('id')}.{item.get('id')}.{SLOT}", item.get(SLOT)))
    return out


def _optional(where: str, deck: dict) -> bool:
    """The last `how` line may be left out with ""."""
    return where == f"how.lines[{HOW_LINES - 1}]"


def fill(deck: dict, sentences: dict) -> tuple[int, list[str]]:
    """Write `sentences` ({where: text}) into the deck's slots. Returns how many were written and
    the keys that name no slot."""
    targets = {}
    for s in deck.get("slides") or []:
        if s.get("kind") == "how":
            for i in range(len(s.get("lines") or [])):
                targets[f"how.lines[{i}]"] = (s["lines"], i)
        for item in s.get("items") or []:
            targets[f"{s.get('id')}.{item.get('id')}.{SLOT}"] = (item, SLOT)
    written, unknown = 0, []
    for where, text in sentences.items():
        if where not in targets or not (text is None or isinstance(text, str)):
            unknown.append(where)
            continue
        box, key = targets[where]
        box[key] = text.strip() if isinstance(text, str) else None
        written += 1
    return written, unknown


def _strip_slots(x):
    """`x` with every sentence slot removed, so the numbers walked are the files' only."""
    if isinstance(x, dict):
        return {k: _strip_slots(v) for k, v in x.items()
                if k != SLOT and not (k == "lines" and x.get("kind") == "how")}
    if isinstance(x, list):
        return [_strip_slots(v) for v in x]
    return x


def _names(f: _Files, project: Project) -> dict[str, set]:
    """Each name a sentence might use (a row's name, or a value of one of its labels) -> the rows
    it stands for."""
    labels = project.read("project").get("labels") or []
    out: dict[str, set] = defaultdict(set)
    for u in f.units:
        out[f.names[u["id"]]].add(u["id"])
        for label in labels:
            v = (u.get("labels") or {}).get(label)
            if isinstance(v, str) and len(v) > 1:
                out[v].add(u["id"])
    return out


def _item_rows(item: dict) -> set | None:
    """The rows an item is about; None for one about the whole project (any name may appear)."""
    if item.get("kind") == "rule":
        return None if item.get("only_rows") is None else set(item["only_rows"])
    opened = item.get("open") or {}
    if item.get("kind") == "choice" and "rows" not in opened and "cells" not in opened:
        return None
    return set(opened.get("rows") or []) | {c[0] for c in opened.get("cells") or []}


def _texts(deck: dict):
    """Each written sentence as `(where, text, rows it may name or None)`."""
    for s in deck.get("slides") or []:
        if s.get("kind") == "how":
            for i, t in enumerate(s.get("lines") or []):
                if isinstance(t, str) and t.strip():
                    yield f"how.lines[{i}]", t, None
        for item in s.get("items") or []:
            t = item.get(SLOT)
            if isinstance(t, str) and t.strip():
                yield f"{s.get('id')}.{item.get('id')}.{SLOT}", t, _item_rows(item)


def _sha1(deck: dict) -> str:
    return hashlib.sha1(json.dumps(slots(deck), ensure_ascii=False).encode("utf-8")).hexdigest()


def check(project: Project, deck: dict) -> dict:
    """Problems with a written brief: `{"empty": [where], "numbers": [{where, number, text}],
    "names": [{where, name, text}]}`. A number must be among the brief's own figures or the
    project's facts (numbers from 0 to 10, years and question ids need none). A row's name, or a
    label value such as a brand or country, may appear only in a sentence about that row."""
    f = _Files(project)
    try:
        facts = compute_facts(project)
    except Exception:  # noqa: BLE001  set-up folders may not compute yet
        facts = {}
    numbers = fact_numbers(facts) | fact_numbers(_strip_slots(deck))
    qids = [q.get("id") for q in f.questions]
    names = _names(f, project)
    patterns = {n: re.compile(r"(?<![\w])" + re.escape(n) + r"(?![\w])") for n in names}
    out = {"empty": [w for w, t in slots(deck)
                     if t is None or (isinstance(t, str) and not t.strip() and not _optional(w, deck))],
           "numbers": [], "names": []}
    for where, text, allowed in _texts(deck):
        for token, _ in numbers_in(text):
            if unbacked(token, numbers, qids):
                out["numbers"].append({"where": where, "number": token, "text": text})
        if allowed is None:
            continue
        for name, rows in names.items():
            if not (rows & allowed) and patterns[name].search(text):
                out["names"].append({"where": where, "name": name, "text": text})
    return out


def mark_checked(project: Project, step: str, deck: dict) -> None:
    deck["checked"] = {"at": now_stamp(), "sha1": _sha1(deck)}
    write_json(path(project, step), deck)


def is_checked(deck: dict) -> bool:
    c = deck.get("checked")
    return deck.get("contract") == CONTRACT and isinstance(c, dict) and c.get("sha1") == _sha1(deck)


def checked_decks(project: Project) -> list[dict]:
    """The briefs in deck/ whose sentences passed `hb check-deck` and have not changed since, in
    step order (for the bundle)."""
    return [d for d in (read(project, s) for s in STEPS) if d and is_checked(d)]


def writer_brief(deck: dict) -> list[str]:
    """One line per empty slot with the figures it may use, so the sentences can be written
    without reading the whole file."""
    out = []
    for s in deck.get("slides") or []:
        if s.get("kind") == "how":
            nums = ", ".join(f"{k} {v}" for k, v in (s.get("numbers") or {}).items())
            todo = [i for i, t in enumerate(s.get("lines") or []) if t is None]
            if todo:
                out.append(f"how.lines[{todo[0]}..{todo[-1]}]: {nums}")
        for item in s.get("items") or []:
            if item.get(SLOT) is not None:
                continue
            where = f"{s.get('id')}.{item.get('id')}.{SLOT}"
            bits = {k: v for k, v in item.items()
                    if k not in (SLOT, "id", "kind", "open", "help", "cases", "question_ids", "only_rows", "rows",
                                 "rule", "choice", "pattern", "recommended")
                    and v not in (None, [], {})}
            if isinstance(bits.get("example"), dict):
                ex = bits["example"]
                bits["example"] = f"{ex.get('title')} (used for {ex.get('used_by')})"
            if item.get("kind") == "pattern":
                bits["cases"] = ", ".join(c["name"] for c in item.get("cases") or [])
                bits["rows"] = item.get("rows")
            elif item.get("kind") == "choice" and isinstance(item.get("rows"), int):
                bits["rows"] = item["rows"]
            out.append(f"{where}: " + "; ".join(f"{k} {v}" for k, v in bits.items()))
    return out

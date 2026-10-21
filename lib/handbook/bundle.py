"""What the viewer reads: the bundle (`viewer/bundle.json`, also inlined in the page), the
per-row evidence files (`viewer/evidence/<row>.json`) and the download sheets
(`viewer/downloads.json`).

Two things never reach the bundle: the scorer's note on a cell (it goes into the Evidence
download sheet, under "Scorer's note", and into the row's evidence file, which the cell panel
shows as Claude's reasoning) and anything from the held-back folder (last edition's results
reach the viewer only through `compare/compare.json`, as `results.compare`).
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import PurePosixPath

from .checks import _Documents
from .fetch import weak_why
from . import control, corrections, costs, edition, rowwatch
from .folder import Project, applies, effective_question, read_json
from .team import cell_rev, rule_rev
from .custom_charts import read_charts
from .deck import checked_decks
from .report_draft import drafting
from .scoresheet import row_totals

CONTRACT = "handbook-bundle/1"
EVIDENCE_KEYS = ("doc", "page", "section", "quote", "quote_en", "language")
SECOND_READ_KEYS = ("value", "agrees", "run")
OWNER_KEYS = ("value", "agreed", "why", "claude", "kept", "correction", "by", "at")

STATE_WORDS = {"answered": "Answered", "no_evidence": "No evidence",
               "not_applicable": "Not applicable", "not_run": "Not run"}
FLAG_WORDS = {"quote_not_found": "Quote not on saved page", "after_cutoff": "Dated after the cut-off",
              "reads_disagree": "Second read differs", "group_differs": "Differs across countries",
              "judgement": "Rests on an undecided rule", "weak_source": "Not from the company",
              "document_changed": "Document changed since scored", "owner_changed": "Changed by the owner",
              "todays_version": "Only today's copy of the page"}
REASON_WORDS = {"missing_document": "Missing document", "different_document": "Different document",
                "document_date": "Document date", "rule_reading": "Rule reading",
                "open_rule": "Undecided rule", "unexplained": "Not yet explained"}
BY_WORDS = {"files": "From the files", "claude": "Claude's reading"}


def stamp(now: datetime) -> str:
    """`now` as an ISO timestamp in UTC ending in Z (a naive datetime is taken as UTC)."""
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(project: Project) -> list[tuple[dict, list[str], dict, dict]]:
    """(unit, ids of the questions that apply to it, its answer cells, its check cells) for
    every unit, in units.json order."""
    questions = project.read("questions").get("questions") or []
    out = []
    for unit in project.read("units").get("units") or []:
        row = unit.get("id")
        qids = [q.get("id") for q in questions if applies(q, unit)]
        out.append((unit, qids, project.answers(row).get("cells") or {},
                    project.checks(row).get("cells") or {}))
    return out


def _evidence(item: dict, documents: dict) -> dict:
    out = {k: item.get(k) for k in EVIDENCE_KEYS if k != "section" or item.get(k)}   # a section only when known
    if out["doc"] not in documents:
        out["missing"] = True
    return out


def _cell(answer: dict | None, check: dict | None, documents: dict, sources=None) -> dict:
    """A bundle cell: the answer without the scorer's note, `unsure` or `run`, plus its flags and
    second read from checks/. A missing answer is {"state": "not_run"}. When the cell is flagged
    quote_not_found, each quote that fails the check against its kept document (`sources`, a
    checks._Documents) is marked `"not_found": true`, so the panel can say which one it was."""
    if answer is None:
        cell = {"state": "not_run"}
    else:
        cell = {"state": answer.get("state", "not_run"), "value": answer.get("value"),
                "points": answer.get("points"),
                "evidence": [_evidence(e, documents) for e in answer.get("evidence") or []],
                "rules": list(answer.get("rules") or []), "judgement": bool(answer.get("judgement")),
                "out_of_date": bool(answer.get("out_of_date"))}
        if isinstance(answer.get("owner"), dict):      # the owner's answer, and Claude's verdict on it
            cell["owner"] = {k: answer["owner"].get(k) for k in OWNER_KEYS}
        if isinstance(answer.get("feedback"), dict):   # what the feedback round decided about it
            cell["feedback"] = {k: answer["feedback"].get(k) for k in ("text", "kept", "reason")}
        if answer.get("checked"):
            # Who checked it is a hidden person id, never a name: the local page puts the name to it, and
            # a published page shows only that a person checked it (team.py).
            chk = answer["checked"] if isinstance(answer["checked"], dict) else {}
            cell["checked"] = {k: chk.get(k) for k in ("by", "at", "how")} if chk.get("by") else True
        if isinstance(answer.get("rescored"), dict):   # a run read something else for an answer a person checked
            cell["rescored"] = {k: answer["rescored"].get(k) for k in ("value", "run")}
        cell["rev"] = cell_rev(answer)                 # what the page saw, sent back with a save
        if sources is not None and "quote_not_found" in ((check or {}).get("flags") or []):
            for item in cell["evidence"]:
                doc = item.get("doc")
                if (isinstance(doc, str) and sources.kept(doc)
                        and not sources.found(doc, item.get("quote") or "")):
                    item["not_found"] = True
                    if sources.has_ocr(doc):
                        item["ocr"] = True      # some of its pages were read from a picture
    if answer is not None or check:
        check = check or {}
        second = check.get("second_read")
        cell["flags"] = list(check.get("flags") or [])
        cell["second_read"] = ({k: second.get(k) for k in SECOND_READ_KEYS}
                               if isinstance(second, dict) else None)
    return cell


def _weak(meta: dict) -> dict | None:
    """`{"why", "auto"}` when the document counts as weak (`auto`: only because it is on a
    third-party site), else None. Replaces meta's own `weak` mark in the bundle."""
    why = weak_why(meta)
    return None if why is None else {"why": why, "auto": not isinstance(meta.get("weak"), dict)}


def _text_or_none(path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _results(project: Project) -> dict:
    """The report, the facts, compare.json, the owner's charts and the report-drafting state;
    with a comparison that has past editions, also `history` (compare.json's own, for the charts
    that draw trends)."""
    compare = read_json(project.file("compare/compare"))
    if isinstance(compare, dict) and edition.is_free():      # the free edition compares with one edition and shows no trends
        compare = {k: v for k, v in compare.items() if k != "history"}
    out = {"report": _text_or_none(project.path / "report" / "report.md"),
           "facts": read_json(project.file("report/facts")), "compare": compare,
           "charts": read_charts(project), "drafting": drafting(project)}
    if isinstance(compare, dict) and compare.get("history") and edition.is_full():
        out["history"] = compare["history"]
    return out


def make_bundle(project: Project, now: datetime) -> dict:
    """The bundle (see the plan's "The bundle" shape). Cells cover every row × applicable
    question; totals come from `scoresheet.row_totals` (None when the sheet can't compute)."""
    proj = project.read("project")
    documents = project.documents()
    sources = _Documents(project)
    cells, totals, found, unchecked = {}, {}, {}, {}
    feedback = edition.optional("feedback")        # None in the free edition: it has no feedback round
    for unit, qids, answers, checks in _rows(project):
        row = unit.get("id")
        if feedback:
            unchecked[row] = feedback.unchecked_questions(answers, checks, qids)
        cells[row] = {q: _cell(answers.get(q), checks.get(q), documents, sources) for q in qids}
        totals[row] = row_totals(project, unit, answers)
        found[row] = project.found(row)
    out = {
        "contract": CONTRACT,
        "edition": edition.edition(),        # "free" or "full": the page words what it offers by it
        "generated": stamp(now),
        "project": {k: v for k, v in proj.items() if k != "provided"},
        "provided": [{"name": PurePosixPath(str(p.get("file") or "")).name, "role": p.get("role"),
                      "note": p.get("note") or ""} for p in proj.get("provided") or []],
        "needs": project.read("needs").get("needs") or [],
        "questions": project.read("questions").get("questions") or [],
        "scoresheet": project.read("scoresheet") or None,
        "units": project.read("units").get("units") or [],
        "rules": project.read("rules").get("rules") or [],
        # What the page saw of each rule, sent back with a decision (a colleague may have decided it since).
        "rule_revs": {r.get("id"): rule_rev(r) for r in project.read("rules").get("rules") or []
                      if isinstance(r, dict) and r.get("id")},
        "where_to_look": project.read("where_to_look"),
        "cells": cells,
        "totals": totals,
        "documents": {d: {**{k: v for k, v in meta.items() if k not in ("file", "sha1")},
                          "weak": _weak(meta)} for d, meta in documents.items()},
        "found": found,
        "runs": [{k: v for k, v in r.items() if k != "by_name"} if isinstance(r, dict) else r
                 for r in project.runs()],                    # who started it: a hidden id only (team.py)
        "estimate": project.read("estimate") or None,
        # Tokens: what was used and the model for a rough range before work runs (costs.py, views' HB.cost).
        "cost": costs.summary(project),
        # Rows waiting on the owner: stopped at their budget, or much slower than similar rows (rowwatch.py).
        "watch": rowwatch.watch(project),
        "results": _results(project),
        "applied_edits": list(project.read("edits-applied").get("applied") or []),
        # Every round of the owner's corrections, with what the code found and Claude's verdict.
        "corrections": corrections.public(project),
        # Run control; `applied` stays in so the run bar knows which requests Claude has handled.
        "control": control.read(project),
        # The step briefs whose sentences passed `hb check-deck` (views/deck.js).
        "decks": checked_decks(project),
    }
    # The feedback round (packs sent, replies, the owner's decisions), once it has started.
    part = feedback.bundle_part(project, unchecked) if feedback else None
    if part is not None:
        out["feedback"] = part
    return out


def evidence_files(project: Project) -> dict[str, dict]:
    """Row id -> {"row", "pages": {"<doc>#<page>": page text}, "notes": {qid: scorer's note}} for
    every row with an answer, holding only the pages its cells cite (kept documents, known page
    numbers) and the notes that are not empty. A row with no answer yet has no file: a long list
    (EggTrack's 601 rows) would otherwise pass the page's 500 data files before anything is scored."""
    documents = project.documents()
    texts: dict[str, dict] = {}
    out = {}
    for unit, qids, answers, _ in _rows(project):
        if not any(isinstance(answers.get(q), dict) and (answers[q].get("state") in ("answered", "no_evidence")
                   or answers[q].get("evidence")) for q in qids):     # not_applicable with evidence counts
            continue
        pages = {}
        for q in qids:
            for e in (answers.get(q) or {}).get("evidence") or []:
                doc, page = e.get("doc"), e.get("page")
                if page is None or doc not in documents:
                    continue
                if doc not in texts:
                    texts[doc] = {p.get("page"): p.get("text") for p in project.pages(doc)}
                if texts[doc].get(page) is not None:
                    pages[f"{doc}#{page}"] = texts[doc][page]
        notes = {q: (answers.get(q) or {}).get("note") for q in qids}
        out[unit.get("id")] = {"row": unit.get("id"), "pages": dict(sorted(pages.items())),
                               "notes": {q: n for q, n in notes.items() if isinstance(n, str) and n.strip()}}
    return out


# Past this many rows with answers, rows share evidence files: a published page may hold at most 500
# data files, and EggTrack's full run would have 601 rows (14 Oct 2026).
EVIDENCE_ROW_FILES = 400
EVIDENCE_PARTS = 100


def evidence_part(row: str) -> str:
    """The shared evidence file a row's evidence goes in: the sum of its id's character codes, mod 100
    (the page works out the same: viewer/js/data.js)."""
    return f"part-{sum(map(ord, row)) % EVIDENCE_PARTS:02d}"


def evidence_data_files(project: Project) -> dict[str, dict]:
    """{"evidence/<name>.json": data}: one file per row with answers, or, past EVIDENCE_ROW_FILES rows,
    shared files {"rows": {row: evidence}} named by evidence_part."""
    files = evidence_files(project)
    if len(files) <= EVIDENCE_ROW_FILES:
        return {f"evidence/{row}.json": ev for row, ev in files.items()}
    parts: dict[str, dict] = {}
    for row, ev in files.items():
        parts.setdefault(f"evidence/{evidence_part(row)}.json", {"rows": {}})["rows"][row] = ev
    return parts


# --- download sheets ------------------------------------------------------------------------

def _yes_no(value) -> str | None:
    return None if value is None else ("Yes" if value else "No")


def _join(values) -> str:
    return ", ".join(str(v) for v in values or [])


def _checked_by(checked) -> list:
    """The "Checked by" and "Checked on" cells. Who is a hidden person id ("person:p-..."), which the
    local page's download turns into the person's name; anywhere else it reads "A person"."""
    if not checked:
        return [None, None]
    if isinstance(checked, dict) and checked.get("by"):
        return [f"person:{checked['by']}", (checked.get("at") or "")[:10] or None]
    return ["A person", (checked.get("at") or "")[:10] or None] if isinstance(checked, dict) else ["A person", None]


def _sheet(sheet_id: str, name: str, columns: list[str], rows: list[list]) -> dict:
    return {"id": sheet_id, "name": name, "columns": columns, "rows": rows}


def downloads_rows(project: Project, bundle: dict | None = None) -> dict:
    """{"sheets": [{"id", "name", "columns", "rows"}]}: Answers, Evidence (with the scorer's note,
    labelled "Scorer's note"), Documents, Rules, and Comparison when compare.json exists. Each
    row is a list in column order. `bundle` saves building it again."""
    b = bundle or make_bundle(project, datetime.now(timezone.utc))
    proj = b["project"]
    word = str(proj.get("unit_word") or "row").capitalize()
    words = str(proj.get("unit_word_plural") or "rows").capitalize()
    labels = list(proj.get("labels") or [])
    units = {u.get("id"): u for u in b["units"]}
    questions = {q.get("id"): q for q in b["questions"]}
    docs = b["documents"]

    answers, evidence = [], []
    for row, cells in b["cells"].items():
        unit = units.get(row, {})
        notes = project.answers(row).get("cells") or {}
        for qid, c in cells.items():
            q = effective_question(questions.get(qid, {}), unit)
            answers.append([row, unit.get("name"), *[(unit.get("labels") or {}).get(lb) for lb in labels],
                            qid, q.get("short"), q.get("section"), STATE_WORDS.get(c["state"], c["state"]),
                            c.get("value"), c.get("points"), _join(c.get("rules")),
                            _yes_no(c.get("judgement")),
                            _join(FLAG_WORDS.get(f, f) for f in c.get("flags") or []),
                            (c.get("second_read") or {}).get("value"), _yes_no(c.get("out_of_date")),
                            *_checked_by(c.get("checked"))])
            note = (notes.get(qid) or {}).get("note") or None
            if c["state"] == "not_run" and not note:
                continue
            for e in c.get("evidence") or [{}]:
                d = docs.get(e.get("doc"), {})
                evidence.append([row, unit.get("name"), qid, STATE_WORDS.get(c["state"], c["state"]),
                                 c.get("value"), c.get("points"), e.get("doc"), d.get("title"),
                                 e.get("page"), e.get("section"), e.get("quote"), e.get("quote_en"), e.get("language"),
                                 d.get("url"), note])

    cited_by: dict[str, list[str]] = {}
    for row, f in b["found"].items():
        for d in f.get("documents") or []:
            cited_by.setdefault(d, []).append(row)
    documents = [[d, m.get("title"), m.get("publisher"), m.get("date"), m.get("date_basis"),
                  m.get("url"), m.get("archive_url"), m.get("how"), _yes_no(m.get("provided_by_owner")),
                  m.get("language"), m.get("host_type"),
                  "Weak: " + m["weak"]["why"] if m.get("weak") else "Own words",
                  _yes_no(m.get("kept")), m.get("pages"),
                  m.get("fetched"), _join(cited_by.get(d)),
                  m["todays_version"]["why"] if isinstance(m.get("todays_version"), dict) else None]
                 for d, m in docs.items()]

    rules = []
    for r in b["rules"]:
        dec, stakes = r.get("decision") or {}, r.get("stakes") or {}
        rules.append([r.get("id"), r.get("kind"), _join(r.get("question_ids")),
                      _join(r.get("rows")) if r.get("rows") is not None else "All",
                      r.get("ask"), "; ".join(f"{o.get('id')}: {o.get('text')}" for o in r.get("options") or []),
                      r.get("recommended"), r.get("why"), stakes.get("cells"), stakes.get("points"),
                      r.get("status"), dec.get("text"), dec.get("at"), dec.get("via"), r.get("raised_by"),
                      r.get("last_year_note")])

    sheets = [
        _sheet("answers", "Answers", [f"{word} id", word, *labels, "Question", "Short name", "Section",
                                      "State", "Value", "Points", "Rules", "Judgement", "Flags",
                                      "Second read", "Out of date", "Checked by", "Checked on"], answers),
        _sheet("evidence", "Evidence", [f"{word} id", word, "Question", "State", "Value", "Points",
                                        "Document", "Title", "Page", "Section or article", "Quote", "Quote in English",
                                        "Language", "URL", "Scorer's note"], evidence),
        _sheet("documents", "Documents", ["Document", "Title", "Publisher", "Date", "Date from", "URL",
                                          "Archive URL", "How found", "Provided by owner", "Language",
                                          "Host type", "Source strength", "Saved copy kept", "Pages",
                                          "Fetched", words, "Kept as today's page because"],
               documents),
        _sheet("rules", "Rules", ["Rule", "Kind", "Questions", words, "Question asked", "Options",
                                  "Recommended", "Why", "Cells affected", "Points at stake", "Status",
                                  "Decision", "Decided at", "Decided via", "Raised by", "Last edition"],
               rules),
    ]
    compare = b["results"].get("compare")
    if compare:
        explained = {(d.get("row"), d.get("key")): d for d in compare.get("disagreements") or []}
        lines = []
        for r in compare.get("rows") or []:
            ours, theirs, agree = r.get("ours") or {}, r.get("theirs") or {}, r.get("agree") or {}
            for key in dict.fromkeys([*theirs, *ours]):
                d = explained.get((r.get("row"), key), {})
                lines.append([r.get("row"), units.get(r.get("row"), {}).get("name"), key, ours.get(key),
                              theirs.get(key), _yes_no(agree.get(key)),
                              REASON_WORDS.get(d.get("reason"), d.get("reason")),
                              BY_WORDS.get(d.get("by"), d.get("by")), d.get("note")])
        sheets.append(_sheet("comparison", "Comparison",
                             [f"{word} id", word, "What", "Ours", "Last edition", "Agree", "Reason",
                              "Explained by", "Note"], lines))
    return {"sheets": sheets}

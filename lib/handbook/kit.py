"""The scorer's kit: the only folder a scorer (or a second reader) may read.

`write_kit(project, row, run)` clears `Project.kit_dir(row)` (outside the project folder), keeping
only a second reader's `second/` folder, and writes:

    brief.md                what to do
    questions.json          the row's questions as the row sees them, with levels, coding rule and
                            the ids of the rulings that apply; the rulings themselves (decided, or
                            the recommended reading) once each, with the questions they cover
    docs/<doc>/meta.json    title, publisher, date, language only (never the address)
    docs/<doc>/p001.txt     one file per page, so a search hit names its page
    docs/<doc>/p001.jpg     the picture of a page, next to its text, for a page that is a photograph, a
                            scan or mostly picture; at most MAX_PICTURES in a kit and MAX_PICTURE_BYTES
                            between them
    capture.json            the content hash of every page file and of each document's capture,
                            which `ingest` copies into the answers so each one is bound to the
                            text it was scored from
    check_quotes.py         a copy of quotecheck.py: the scorer runs it on its answers.json before
                            it finishes, so a quote that is not word for word on a page is fixed
                            while the page is still to hand

With `second=True` it writes `second/brief.md` and `second/questions.json` instead of the top-level
pair, for a second, independent reader of the listed questions. It clears and rewrites only
`second/`, `docs/` and `capture.json`, so the first reader's top-level files are left alone.

The kit is what keeps the scorer blind, so it is built only from questions, units, rules (without
their last-year notes), the row's found documents and their pages. It never reads the held-back
folder, answers/, checks/, compare/ or report/, and it leaves out document addresses altogether, so
a last-year address cannot get through in any form.
"""
from __future__ import annotations
import json
import shutil
from pathlib import Path

from . import answers
from .folder import HandbookError, Project, write_json
from .quotecheck import capture_id, page_hash

META_KEYS = ("title", "publisher", "date", "language")
MAX_PICTURES = 12                       # pictures in one kit: each costs a scorer up to about 1,600 tokens to look at
MAX_PICTURE_BYTES = 6_000_000
IMAGE_FILES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tiff", ".tif")
SELF_CHECK = "check_quotes.py"          # the kit's copy of quotecheck.py
QUOTECHECK = Path(__file__).with_name("quotecheck.py")
UNDECIDED = "not decided: use this reading"


def unit(project: Project, row: str) -> dict:
    """The unit `row` from units.json; HandbookError when there is none."""
    for u in project.read("units").get("units", []):
        if u.get("id") == row:
            return u
    raise HandbookError(f"There is no unit {row!r} in units.json.")


def rules_for(rules: list, question_id: str, row_id: str) -> list[dict]:
    """The rules (from rules.json "rules") on `question_id` that apply to `row_id`, in file order."""
    return [r for r in rules if question_id in (r.get("question_ids") or [])
            and (r.get("rows") is None or row_id in r.get("rows"))]


def _ruling(rule: dict) -> dict:
    """A rule as the scorer sees it: the ask and the reading to use, nothing else."""
    if rule.get("status") == "decided":
        decision = rule.get("decision") or {}
        return {"id": rule["id"], "ask": rule.get("ask"), "status": "decided",
                "reading": decision.get("text")}
    options = {o.get("id"): o.get("text") for o in rule.get("options") or []}
    reading = options.get(rule.get("recommended"))
    return {"id": rule["id"], "ask": rule.get("ask"),
            "status": UNDECIDED if reading else "not decided: no reading recommended",
            "reading": reading}


def kit_questions(project: Project, row: str, questions=None) -> tuple[list[dict], list[dict]]:
    """(questions, rulings) for the row's kit, the questions limited to `questions` (ids) when
    given. Each question's `rules` names the rulings that apply to it; `rulings` holds each one
    once, with the questions it covers, since a ruling on every question repeated under every
    question made questions.json several times its size. Unknown ids, and ids that do not apply
    to the row, raise HandbookError."""
    mine = project.questions_for(unit(project, row))
    if questions:
        known = {q.get("id") for q in project.read("questions").get("questions", [])}
        unknown = [q for q in questions if q not in known]
        if unknown:
            raise HandbookError(f"Question {', '.join(unknown)} is not in questions.json.")
        by_id = {q["id"]: q for q in mine}
        missing = [q for q in questions if q not in by_id]
        if missing and len(set(missing)) == len(set(questions)):
            raise HandbookError(f"Question {', '.join(missing)} does not apply to {row}.")
        # A run's question list is for all its rows; one that does not apply to this row is left out
        # of its kit (FAIRR, 14 Oct 2026: two salmon questions stopped the kit of a shrimp producer).
        mine = [by_id[q] for q in dict.fromkeys(questions) if q in by_id]
    rules = project.read("rules").get("rules", [])
    out, rulings = [], {}
    for q in mine:
        applying = rules_for(rules, q["id"], row)
        for r in applying:
            rulings.setdefault(r["id"], {**_ruling(r), "questions": []})["questions"].append(q["id"])
        out.append({"id": q["id"], "short": q.get("short"), "wording": q.get("wording"),
                    "levels": [{"value": lv.get("value"), "meaning": lv.get("meaning")}
                               for lv in (q.get("answer") or {}).get("levels") or []],
                    **({"figure": {k: (q.get("answer") or {}).get(k) for k in ("unit", "min", "max", "decimals")}}
                       if answers.is_number(q) else {}),
                    **({"may_not_apply": True} if q.get("may_not_apply") is True else {}),
                    "no_evidence_value": q.get("no_evidence_value"),
                    "rule": q.get("rule"), "evidence": q.get("evidence"),
                    "rules": [r["id"] for r in applying]})
    order = {r.get("id"): i for i, r in enumerate(rules)}
    return out, sorted(rulings.values(), key=lambda r: order.get(r["id"], len(order)))


def _page_name(number: int, width: int) -> str:
    return f"p{number:0{width}d}.txt"


def _picture_plan(project: Project, docs: list[str]) -> tuple[dict, int]:
    """Which page pictures go in the kit: {(doc, page number): [file names in the document's folder]} and
    how many were left out. A picture that is a whole document (an image file) comes before a page picture
    of a longer document; within each, the row's document order and page order. Stops at MAX_PICTURES
    files or MAX_PICTURE_BYTES."""
    wanted = []
    for order, doc in enumerate(docs):
        folder = project.doc_dir(doc)
        whole = str((project.documents().get(doc) or {}).get("file") or "").lower().endswith(IMAGE_FILES)
        for i, page in enumerate(project.pages(doc), start=1):
            files = [f for f in page.get("images") or [] if isinstance(f, str) and (folder / f).is_file()]
            if files:
                number = page.get("page") if isinstance(page.get("page"), int) else i
                wanted.append((0 if whole else 1, order, number, doc, files))
    wanted.sort(key=lambda w: w[:3])
    plan, count, size, left_out = {}, 0, 0, 0
    for _, _, number, doc, files in wanted:
        total = sum((project.doc_dir(doc) / f).stat().st_size for f in files)
        if count + len(files) > MAX_PICTURES or size + total > MAX_PICTURE_BYTES:
            left_out += len(files)
            continue
        plan[(doc, number)] = files
        count, size = count + len(files), size + total
    return plan, left_out


def _write_docs(project: Project, row: str, docs_dir: Path) -> tuple[dict, dict]:
    """Copy the row's found documents into `docs_dir`; returns ({"kept": n, "not_kept": n, "pictures": n,
    "pictures_left_out": n}, captures), where captures is `{doc: {"capture": id, "pages": {"p001.txt": sha1,
    ...}}}` for every document with page files: the hash of each page file as written, and the capture id
    those hashes give (the same id `prepare` recorded for the same text)."""
    metas = project.documents()
    counts = {"kept": 0, "not_kept": 0, "pictures": 0, "pictures_left_out": 0, "empty_pages": 0}
    captures, index = {}, []
    found = list(dict.fromkeys(project.found(row).get("documents") or []))
    plan, counts["pictures_left_out"] = _picture_plan(project, [d for d in found if d in metas])
    for doc in found:
        meta = metas.get(doc)
        if meta is None:
            raise HandbookError(f"Document {doc} is listed for {row} in found/ but has no meta.json.")
        out = {k: meta.get(k) for k in META_KEYS}
        if not out.get("date") and meta.get("fetched"):
            # An undated page shows when it was saved: it may have changed since the cut-off (ACES re-test,
            # 15 Oct 2026: pages saved in 2026 judged a camp as at its 2025 audit with no warning).
            out["saved_on"] = str(meta["fetched"])[:10]
        folder = docs_dir / doc
        pages = project.pages(doc)
        if pages and isinstance(meta.get("ocr_pages"), list) and meta["ocr_pages"]:
            out["ocr_pages"] = [n for n in meta["ocr_pages"] if isinstance(n, int)]   # the quote check allows for slips there
        write_json(folder / "meta.json", out)
        if not pages:
            counts["not_kept"] += 1
            continue
        counts["kept"] += 1
        numbers = [p.get("page") if isinstance(p.get("page"), int) else i
                   for i, p in enumerate(pages, start=1)]
        width = max(3, len(str(max(numbers))))
        texts, hashes, shown = [], {}, []
        for number, page in zip(numbers, pages):
            text = page.get("text") or ""
            (folder / _page_name(number, width)).write_text(text, encoding="utf-8")
            texts.append(text)
            hashes[_page_name(number, width)] = page_hash(text)
            files = plan.get((doc, number)) or []
            names = []
            for n, f in enumerate(files, 1):
                name = _page_name(number, width)[:-4] + ("" if len(files) == 1 else f"-{n}") + ".jpg"
                shutil.copyfile(project.doc_dir(doc) / f, folder / name)
                names.append(name)
            counts["pictures"] += len(names)
            counts["empty_pages"] += 0 if text.strip() else 1
            shown.append((_page_name(number, width), text, names, bool(page.get("ocr"))))
        captures[doc] = {"capture": capture_id(texts), "pages": hashes}
        index.append((doc, out, shown))
    (docs_dir / INDEX).write_text(index_text(index, counts["pictures_left_out"]), encoding="utf-8")
    return counts, captures


INDEX = "index.md"
GIST = 110             # characters of each page shown in the index


def _gist(text: str) -> str:
    words = " ".join((text or "").split())
    return words if len(words) <= GIST else words[:GIST - 3].rstrip() + "..."


def index_text(index, left_out: int = 0) -> str:
    """docs/index.md: every document with page files, its title, language and page count, and one
    line per page (its first words), so a scorer can see where to look before it searches. A page's
    entry is (page file, text) or (page file, text, picture files, read from a picture): a page with a
    picture says so, and says when its words were read from a picture by a text reader."""
    lines = ["# What is in docs/", "",
             "One line per page: the page file and its first words. Search the page files for the "
             "words you need (grep -il), then read the pages that match.", ""]
    for doc, meta, pages in index:
        lines.append(f"## {doc}: {meta.get('title') or 'untitled'} ({meta.get('language') or 'language unknown'}, "
                     f"{len(pages)} page{'s' if len(pages) != 1 else ''})")
        for entry in pages:
            name, text = entry[0], entry[1]
            pictures, ocr_page = (entry[2], entry[3]) if len(entry) > 2 else ([], False)
            tags = (["words read from a picture by a text reader"] if ocr_page else []) + \
                   ([f"picture: {', '.join(pictures)}"] if pictures else [])
            shown = " [" + "; ".join(tags) + "]" if tags else ""
            if text.strip():
                gist = _gist(text)
            elif pictures:
                gist = "(no readable text: only the picture can be looked at, and it cannot be quoted)"
            else:
                gist = "(no text: this page could not be read, so it cannot be quoted)"
            lines.append(f"- {name}{shown}: {gist}")
        lines.append("")
    if left_out:
        lines += [f"{left_out} more picture file{'s' if left_out != 1 else ''} left out to keep this folder small; "
                  "their page files hold the words.", ""]
    return "\n".join(lines)


# From this many questions the brief asks for answers.json to be written as the scorer goes, so a
# session stopped at its budget (cmd_score.py) leaves the answers it had (Zambia, 61 questions, 53 turns).
SAVE_AS_YOU_GO = 10
# A row's token budget is this many times the cost model's figure for its kit: wide enough that a row
# within the usual spread never meets it (half the format-test rows came within 1.3 times), narrow
# enough to stop a row going round in circles (Canada: 5.4M; Zambia: 53 turns).
BUDGET_FACTOR = 2.5
MIN_BUDGET = 150_000

# The example answers file in every brief. Ids, documents and words are made up on purpose, so the
# example cannot nudge an answer.
EXAMPLE = {"row": "ROW", "cells": {
    "4.2": {"state": "answered", "value": 3,
            "evidence": [{"doc": "d-0123456789", "page": 4, "section": "s. 12(3)",
                          "quote": "Exact words copied from p004.txt.",
                          "quote_en": "Your English translation, or the same words if the page is English.",
                          "language": "da"}],
            "note": "One sentence on why this level fits.", "unsure": None},
    "4.3": {"state": "no_evidence", "value": None, "evidence": [],
            "note": "One sentence on what you searched for.",
            "unsure": {"why": "document", "note": "The definition this turns on is in another act, which is not here.",
                       "missing": "The Example Act 2019"}},
}}


def _example(row_id: str) -> str:
    """EXAMPLE as compact JSON, a few lines per cell, so the brief stays short."""
    def d(v):
        return json.dumps(v, ensure_ascii=False)
    lines = [f'{{"row": {d(row_id)}, "cells": {{']
    items = list(EXAMPLE["cells"].items())
    for i, (qid, c) in enumerate(items):
        lines.append(f' {d(qid)}: {{"state": {d(c["state"])}, "value": {d(c["value"])},')
        lines.append(f'  "evidence": {d(c["evidence"])},')
        lines.append(f'  "note": {d(c["note"])}, "unsure": {d(c["unsure"])}}}'
                     + ("," if i < len(items) - 1 else ""))
    lines.append("}}")
    return "\n".join(lines)


def brief_text(project: Project, row: dict, kit: Path, n_questions: int, counts: dict,
               second: bool = False, look=None, figures: bool = False, may_not_apply: bool = False) -> str:
    """The plain-English brief for a scorer (or, with `second`, a second reader). `look`: pages
    to read first, as (doc, page). `figures`: some question takes a figure (answers.py)."""
    unit_word = project.read("project").get("unit_word") or "unit"
    plural = project.read("project").get("unit_word_plural") or "units"
    bench = project.read("project").get("name") or "the benchmark"
    cutoff = row.get("cutoff") or project.read("project").get("cutoff")      # a row's own cut-off first
    labels = ", ".join(f"{k} {v}" for k, v in (row.get("labels") or {}).items())
    qfile = "second/questions.json" if second else "questions.json"
    afile = "second/answers.json" if second else "answers.json"
    example = _example(row["id"])
    if second:
        intro = [f"# Second reading: {row.get('name') or row['id']}", "",
                 f"You are giving a second, independent reading of {n_questions} question(s) for one "
                 f"{unit_word}: {row.get('name') or row['id']} ({labels}). These cells are being re-read "
                 f"because other {plural} in the same group answered differently. Read the "
                 "documents afresh and answer on the evidence alone."]
    else:
        intro = [f"# Scoring brief: {row.get('name') or row['id']}", "",
                 f"You are scoring one {unit_word}: {row.get('name') or row['id']} ({labels})."]
    not_kept = (f" {counts['not_kept']} document(s) were found but their text was not saved: they "
                "have only meta.json, so you cannot quote or cite them." if counts["not_kept"] else "")
    lines = intro + [
        "",
        f"Everything you need is in this folder: {kit}",
        f"- {qfile}: the {n_questions} question(s) to answer, with their levels and coding rule, and "
        "under rulings the readings that apply, each given once with the questions it covers.",
        "- docs/index.md: every document with its pages, one line per page with its first words.",
        "- docs/<doc>/meta.json: each document's title, publisher, date and language (an undated one shows "
        "saved_on, the day it was saved, which may be after the cut-off).",
        "- docs/<doc>/p001.txt, p002.txt and so on: the text of each page, one file per page.",
        *(["- docs/<doc>/p001.jpg and so on: the picture of a page, next to its text file, for a page that is a "
           "photograph, a scan or mostly picture (a menu poster, a grid of logos, a chart). docs/index.md marks "
           "each one. The page file holds the words a text reader took from the picture, which can have a "
           "wrong letter or figure."] if counts.get("pictures") else []),
        f"{counts['kept']} document(s) have page files.{not_kept}",
        *([f"{counts['empty_pages']} page file(s) are empty because the page could not be read (a scan or a picture "
           "the tool had no way to read): docs/index.md marks each one. Do not quote an empty page. If the answer "
           "may rest on one, answer from what you can read and mark the cell unsure (why \"document\")."]
          if counts.get("empty_pages") else []),
        *(["", "Read these pages first: " + "; ".join(f"{doc} page {page}" for doc, page in look) +
            ". They were pointed out as relevant to the question, not as proof of any answer: "
            "answer on all the evidence, as below."] if look else []),
        "",
        "How to work",
        "1. Read only inside this folder. Never use the web, other files on this computer, or outside "
        "knowledge. Answer from these documents alone.",
        *([f"   Answer as things stood on the cut-off date, {cutoff}: a law, policy or figure that came in after "
           "it does not count, even when a document dated later shows it. Each document's date is in its "
           "meta.json; a page that says it was updated after the cut-off may describe later changes, so "
           "mark it unsure (why \"document\") when that matters."] if cutoff else []),
        f"2. Read {qfile} first and follow each question's coding rule and the rulings its rules list "
        f"names. A ruling marked \"decided\" is settled. A ruling marked \"{UNDECIDED}\" is still open "
        "with the owner: apply that reading.",
        "3. Read docs/index.md, then search the page files before reading them, from this folder, for "
        "example grep -il \"policy\" docs/*/p*.txt, then read the pages the search finds. Do not read every "
        "page of every document. Work in few steps: one search over all the pages for the words of "
        "several questions at once, then read whole pages.",
        *(["   Look at a picture (open the .jpg with the Read tool) only when where something sits or how it "
           "looks is what matters: which column a figure is in, which logos are in a row, what a chart shows, "
           "or when the page file reads as garbled. Otherwise read the page file: it costs far less. On a page "
           "read from a picture, quote the words as the picture shows them or as the page file has them; the "
           "quote check allows for a small slip there. A picture whose page file is empty has no readable words, "
           "so it cannot be quoted: if it matters, answer from the rest and mark the cell unsure (why "
           "\"document\")."] if counts.get("pictures") else []),
        "4. Answer every question listed. Each answer has a state:",
        "   - \"answered\": value is one of the question's level values, with at least one piece of evidence."
        + (" A question with \"figure\" also takes a figure: write it as a plain number in the figure's unit, "
           "as the document gives it (73 for 73%, never \"73%\" or 0.73), or one of its level values when "
           "those fit instead." if figures else ""),
        "   - \"no_evidence\": nothing in the documents supports an answer. Value null, evidence empty.",
        *(["   - \"not_applicable\": only for a question with \"may_not_apply\", when the documents show it does "
           "not apply to this row (a venue that offers no riding, for a question about riding). Value null, "
           "with the evidence that shows it."] if may_not_apply else []),
        "5. Evidence: doc is the document folder name; page is the number in the page file name "
        "(p004.txt is page 4); quote is copied exactly from the page file, character for character, "
        "and runs to at least five words (a number or a heading alone cannot be found on the page); "
        "language is the page's language code, for example \"en\" or \"da\". When the page is not in "
        "English, add quote_en, your English translation. When it is English, quote_en is the same as quote. "
        "For a table or a page in columns, quote the words in the order the page file has them, even "
        "where the columns run together; never describe a table or add words of your own in brackets. "
        "A quote is never a web address, a page title or an error message.",
        "   section: where the quote sits in the document's own numbering, when it has one: the nearest "
        "section, article, regulation or numbered paragraph above the quote, with its subsection and "
        "paragraph, written short, for example \"s. 4(2)(a)\", \"art. 35(1)\", \"reg. 7\" or \"para. 12\". "
        "Take it from the headings on the page or the pages before it, never from memory. When the "
        "document has no numbering (a web page, a report without numbered parts), leave section out.",
        "   A document's title in meta.json can be wrong: some sites label a working page \"Not Found\". "
        "Judge a document by its page text, never by its title.",
        f"   Never answer from what a document says about this benchmark's own ratings of it (\"{bench}\": a "
        "certificate, grade, tier or rank it was given): answer from what the documents show it does.",
        "6. note: one plain sentence on why you chose this value.",
        "7. If your answer is a best guess because the rule is unclear, or because a document you would "
        "expect seems to be missing, still give your best answer and add "
        "unsure: {\"why\": \"rule\", \"note\": \"...\"} or {\"why\": \"document\", \"note\": \"...\"}. "
        "Otherwise unsure is null. Never give a confidence score or a percentage.",
        "   A question can turn on a document that is not in this folder: another law, regulations, a definition "
        "set out elsewhere, a form. Search this folder for it once. If it is not here, stop looking: answer from "
        "what you have, add \"missing\": the document's name (with its year and country when you know them) to "
        "unsure {\"why\": \"document\"}, and move on. Do not search for the same document again for later "
        "questions: give the same missing name. It is looked for or asked for, and those questions are "
        "scored again once it is here.",
        f"8. Write {afile} in exactly this shape. The question ids, documents and words below are only "
        "an example; use the ids from " + qfile + "."
        + (f" With this many questions, write {afile} early and write it again, whole, after every ten or so "
           "answers, so the answers you have are kept if the session is stopped." if n_questions > SAVE_AS_YOU_GO else ""),
        "",
        "```json",
        example,
        "```",
        "",
        f"Before you finish, run python3 {SELF_CHECK} {afile} from this folder. It lists every quote "
        "that is not word for word on a page of its document. Fix each one by copying the words from "
        "the page file, or by citing the page that holds them, and run it again until it lists none. "
        f"Then check that every question in {qfile} has an entry and {afile} is valid JSON.",
    ]
    return "\n".join(lines) + "\n"


def write_kit(project: Project, row: str, run: str, questions=None, second: bool = False,
              look=None) -> Path:
    """Clear and write the kit for `row` (see the module docstring) and return its folder.
    `questions` limits the questions (ids); `second` needs `questions`. `look` is a list of
    (doc, page) the brief asks the scorer to read first: the pages a correction points to, named
    without the owner's answer or quote, so the re-read stays blind."""
    if second and not questions:
        raise HandbookError("A second-reading kit needs the questions to re-read: pass --questions.")
    found = project.found(row).get("documents") or []
    for doc, _ in look or []:
        if doc not in found:
            raise HandbookError(f"Document {doc} is not saved for {row}, so the kit cannot point to it.")
    if not project.run(run):
        raise HandbookError(f"There is no run {run!r} in runs/. Start one with: run start --kind ...")
    the_unit = unit(project, row)
    qs, rulings = kit_questions(project, row, questions)
    if not qs:
        raise HandbookError(f"No question applies to {row}, so there is nothing to score.")
    kit = project.kit_dir(row)
    if kit.exists():
        # A normal kit clears everything but the second reader's folder; a second kit clears only
        # its own folder and the documents, so the first reader's files stay where they are.
        stale = ([kit / "second", kit / "docs", kit / "capture.json"] if second
                 else [c for c in kit.iterdir() if c.name != "second"])
        for path in stale:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
    (kit / "docs").mkdir(parents=True, exist_ok=True)
    counts, captures = _write_docs(project, row, kit / "docs")
    write_json(kit / "capture.json", {"row": row, "run": run, "docs": captures})
    shutil.copyfile(QUOTECHECK, kit / SELF_CHECK)
    where = kit / "second" if second else kit
    write_json(where / "questions.json", {
        "row": {"id": row, "name": the_unit.get("name"), "labels": the_unit.get("labels") or {}},
        "run": run, "rulings": rulings, "questions": qs})
    where.mkdir(parents=True, exist_ok=True)
    (where / "brief.md").write_text(brief_text(project, the_unit, kit, len(qs), counts, second, look,
                                                     figures=any("figure" in q for q in qs),
                                                     may_not_apply=any(q.get("may_not_apply") for q in qs)),
                                    encoding="utf-8")
    if not second:
        write_json(kit / "limits.json", kit_limits(project, row, [q["id"] for q in qs]))
    return kit


def kit_limits(project: Project, row: str, qids: list[str]) -> dict:
    """{"budget", "max_turns", "estimate", "from"} for scoring `qids` on `row`. Once MIN_SIMILAR rows like
    it are measured, 2.5 times their tokens per question (rowwatch.similar_budget, "from": "similar");
    before that, BUDGET_FACTOR times the cost model's figure (costs.py, "from": "estimate"). At least
    MIN_BUDGET, with the turn cap as a backstop. hb score stops the session at the budget."""
    from .cmd_score import DEFAULT_MAX_TURNS
    from .costs import estimate_rows
    from .rowwatch import similar_budget
    point = estimate_rows(project, [row], qids)["point"]
    similar = similar_budget(project, row, len(qids))
    budget = similar if similar is not None else round(point * BUDGET_FACTOR)
    return {"budget": max(MIN_BUDGET, budget), "max_turns": DEFAULT_MAX_TURNS, "estimate": point,
            "from": "similar" if similar is not None else "estimate"}

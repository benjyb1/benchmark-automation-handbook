"""Our results beside last edition's held-back results, row by row and key by key.

`compare(project)` scores every row from its answers with the score sheet (`row_totals`), lines
the figures up against the held-back ones and works out, from the files alone, why a pair differs
where it can. It reads the held-back folder and nothing else outside the project's own files, and
it never changes an answer or a score.

It is the only reader of the past editions in the held-back folder: `results.json` is the latest
(what the comparison is against), `history/<edition>.json` the older ones, in the same shape. They
reach the viewer only through compare.json's `history` (each past edition's figures per row and
key, with this run's) and `big_moves` (large differences from the latest edition that the files do
not explain, each with the row's trend). Nothing from them goes into a kit, a prompt or a rule.

A key is a question id (only when the held-back results go down to question level), a section id,
`overall` or `band:<of>`. At question level the held-back figure is the question's points, the
same reading `rebuild_check` uses.
"""
from __future__ import annotations
import re
from datetime import date

from . import answers, edition, needs
from .folder import HandbookError, Project, read_json, write_json
from .scoresheet import round_half_up, row_totals, scales
from .shapes import REASONS, check_compare, check_held_back, is_date

# A big move is a disagreement with the latest edition whose reason is in BIG_MOVE_REASONS and that
# is large: a gap of at least BIG_GAP_SHARE of the key's range (100 for a percentage, else its most
# points); bands BAND_JUMP or more apart; or ours zero or without evidence where theirs was at least
# NOTHING_FOUND_SHARE of the most it could be. Its trend says whether ours falls outside the row's
# range over every past edition, once there are TREND_MIN_EDITIONS past figures or more.
BIG_GAP_SHARE = 0.25
BAND_JUMP = 2
NOTHING_FOUND_SHARE = 0.5
TREND_MIN_EDITIONS = 2
BIG_MOVE_REASONS = ("unexplained", "missing_document")
LATEST = "last edition"                  # the latest edition's name when results.json gives none
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
                9: "nine", 10: "ten"}

SCORED = ("answered", "no_evidence")
UNDECIDED = ("open", "skipped")
NOT_APPLICABLE = "n/a"           # our answer when the question does not apply to the row

NOT_COMPARED_TEXT = {
    "not_scored": "None of its questions have been scored yet.",
    "partial": "Some of its questions have not been scored, so its totals were not compared (a section scored in full still is).",
    "waiting": "Some of its answers wait for data you need to supply, so they and its totals were not compared (a section scored in full still is).",
    "total_only": "Last edition's results give only a total for it, so it is compared once all its questions are scored.",
    "not_in_last_edition": "It is not in last edition's results.",
    "not_in_units": "It is in last edition's results but not among this edition's rows.",
    "rank_only": "Last edition gives it only a rank, so it is checked in the rank order, not figure by figure.",
}


def _rank_order(held: dict, totals_by_row: dict, units: dict | None = None) -> dict | None:
    """Whether the rows fully scored keep last edition's order (ALDF and VACI publish ranks only):
    {"key", "rows": [{"row", "ours", "theirs"}], "pairs", "same_order", "swapped": [[better, worse]]},
    `ours` being our overall score and `theirs` their rank (1 is best). Pairs with the same rank are
    left out; a pair is in the same order when our score for their better row is not lower. None when
    no rank is held back."""
    keys = sorted({k for v in held.values() for k in v if k.startswith("rank:")})
    if not keys:
        return None
    key = "rank:overall" if "rank:overall" in keys else keys[0]
    of, _, within = key[len("rank:"):].partition("@")
    # rank:overall@jurisdiction_type ranks rows only against rows with the same value of that label
    # (ALDF: 50 states ranked 1-50, territories 1-6 on their own).
    group = (lambda row: ((units or {}).get(row) or {}).get("labels", {}).get(within)) if within else (lambda row: None)
    rows = []
    for row, t in totals_by_row.items():
        theirs = (held.get(row) or {}).get(key)
        ours = t.get("overall") if of == "overall" else (t.get("sections", {}).get(of) or {}).get("percent")
        if _number(theirs) and _number(ours):
            rows.append({"row": row, "ours": ours, "theirs": theirs, **({"group": group(row)} if within else {})})
    rows.sort(key=lambda r: r["theirs"])
    pairs, same, swapped = 0, 0, []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a["theirs"] == b["theirs"] or a.get("group") != b.get("group"):
                continue
            pairs += 1
            if a["ours"] >= b["ours"]:
                same += 1
            else:
                swapped.append([a["row"], b["row"]])
    return {"key": key, "rows": rows, "pairs": pairs, "same_order": same, "swapped": swapped}


def read_held_back(project: Project) -> dict:
    """The held-back results.json, checked. Raises HandbookError when it is missing or invalid."""
    path = project.held_back / "results.json"
    results = read_json(path)
    if not results:
        raise HandbookError(f"There are no held-back results at {path}, so there is nothing to "
                            "compare with. Run hold-back with last edition's results first.")
    problems = check_held_back(results)
    if problems:
        raise HandbookError("The held-back results.json has problems:\n- " + "\n- ".join(problems))
    return results


def edition_key(edition) -> tuple:
    """A sort key for edition names: the numbers in the name, in order ("2022/23" after "2022" and
    before "2023"), then the name itself."""
    return tuple(int(x) for x in re.findall(r"\d+", str(edition))), str(edition)


def read_history(project: Project) -> list[dict]:
    """The older editions in the held-back `history/` folder, checked, oldest first ([] when there
    are none). Raises HandbookError naming the file when one is invalid. The free edition keeps no history:
    it compares with the latest past edition only, and shows no trends."""
    out = []
    if edition.is_free():
        return out
    for path in sorted((project.held_back / "history").glob("*.json")):
        data = read_json(path)
        problems = check_held_back(data, history=True)
        if problems:
            raise HandbookError(f"The held-back history/{path.name} has problems:\n- "
                                + "\n- ".join(problems))
        out.append(data)
    return sorted(out, key=lambda d: edition_key(d["edition"]))


def _agree(ours, theirs) -> bool:
    if isinstance(ours, (int, float)) and isinstance(theirs, (int, float)) \
            and not isinstance(ours, bool) and not isinstance(theirs, bool):
        return abs(ours - theirs) < 1e-9
    return ours == theirs


def _points(question: dict, cell: dict):
    """The points a scored cell adds, as `row_totals` reads them."""
    if cell.get("points") is not None:
        return cell["points"]
    value = cell.get("value") if cell.get("state") == "answered" else question.get("no_evidence_value")
    for level in (question.get("answer") or {}).get("levels") or []:
        if level.get("value") == value:
            return level.get("points") or 0
    return 0


def _section_figure(section: dict, entry: dict):
    """The figure a section is published as: its percent, or its points when `percent` is false."""
    if section.get("percent", True):
        return entry["percent"]
    return round_half_up(entry["points"], section.get("rounding"))


def _ours(project: Project, unit: dict, cells: dict, level: str, sheet: dict, ends: dict | None = None) -> tuple[dict, bool]:
    """Every key we can give for the row, in a fixed order, and whether the row is partial. `ends` holds
    the relative figures' ends (scoresheet.scales). A section the sheet does not work out gives no key,
    nor does the overall that needs it."""
    totals = row_totals(project, unit, cells, ends) if sheet.get("computable") is not False else None
    out = {}
    if level == "question":
        for q in project.questions_for(unit):
            cell = cells.get(q["id"])
            if isinstance(cell, dict) and cell.get("state") in SCORED:
                # A number question is published as its figure (or NR and the like), not as points.
                if answers.is_number(q):
                    out[q["id"]] = cell.get("value") if cell.get("state") == "answered" else q.get("no_evidence_value")
                else:
                    out[q["id"]] = _points(q, cell)
            elif isinstance(cell, dict) and cell.get("state") == "not_applicable":
                # Our 'does not apply' against a published answer is a difference to see (HSUS, 15 Oct 2026:
                # Mississippi's bear-hunting item, n/a here and 0 in the key, was silently left out).
                out[q["id"]] = NOT_APPLICABLE
    if totals is None:                               # no totals (a tracker): the answers alone
        return out, False
    if totals["partial"]:
        # A trial scores a few questions: compare each section whose questions are all scored
        # (FAIRR and ALDF, 14 Oct 2026: 'Compared 0 of 60' after every slice).
        open_sections = {q.get("section") for q in project.questions_for(unit)
                         if not (isinstance(cells.get(q["id"]), dict) and cells[q["id"]].get("state") in SCORED
                                 + ("not_applicable",))}
        for section in sheet.get("sections") or []:
            entry = totals["sections"][section["id"]]
            if section["id"] not in open_sections and entry.get("max"):
                out[section["id"]] = _section_figure(section, entry)
        return out, True
    skipped = set(totals.get("not_computed") or [])
    for section in sheet.get("sections") or []:
        if section["id"] not in skipped:
            out[section["id"]] = _section_figure(section, totals["sections"][section["id"]])
    if totals["overall"] is not None or not skipped:
        out["overall"] = totals["overall"]
    for of, name in totals["bands"].items():
        if of not in skipped and (of != "overall" or "overall" in out):
            out[f"band:{of}"] = name
    return out, False


def _qids_under(key: str, scored: list[dict], sections: set) -> list[str]:
    """The scored questions (`{"id", "section"}`, in question order) a key covers."""
    if key in sections:
        return [q["id"] for q in scored if q.get("section") == key]
    if key == "overall":
        return [q["id"] for q in scored]
    if key.startswith("band:"):
        of = key[len("band:"):]
        return _qids_under(of, scored, sections) if of in sections else [q["id"] for q in scored]
    return [q["id"] for q in scored if q["id"] == key]


def _reason(project: Project, row: str, qids: list[str], cells: dict, rules: dict, docs: dict,
            cutoff: str | None, as_at: str | None = None) -> tuple[str, str | None]:
    """The first reason the files prove for the cells under a key, and a plain note (or None). `as_at` is
    the date last edition's answers reflect: a document we cite dated after it may have changed since
    (de-transparenzranking, 21 Oct 2026: a law amended after the owner scored it). With no such date,
    an unexplained difference says that a change since cannot be checked."""
    stuck = {rid: [] for q in qids for rid in cells[q].get("rules") or []
             if (rules.get(rid) or {}).get("status") in UNDECIDED}
    for q in qids:
        for rid in cells[q].get("rules") or []:
            if rid in stuck:
                stuck[rid].append(q)
    if stuck:
        parts = [f"{rid} ({', '.join(qs)})" for rid, qs in stuck.items()]
        return "open_rule", "Rests on a rule that is not decided: " + ", ".join(parts) + "."
    if cutoff:
        for q in qids:
            for e in cells[q].get("evidence") or []:
                meta = docs.get(e.get("doc")) if isinstance(e, dict) else None
                if meta and is_date(meta.get("date")) and \
                        date.fromisoformat(meta["date"]) > date.fromisoformat(cutoff):
                    return "document_date", (f"Cites {e['doc']}, dated {meta['date']}, after the "
                                             f"cut-off of {cutoff}.")
    if as_at:
        for q in qids:
            for e in cells[q].get("evidence") or []:
                meta = docs.get(e.get("doc")) if isinstance(e, dict) else None
                if meta and is_date(meta.get("date")) and meta["date"] > as_at:
                    return "document_date", (f"Cites {e['doc']}, dated {meta['date']}, after the date last edition's "
                                             f"answers reflect ({as_at}), so the source may have changed since.")
    if not project.found(row).get("documents"):
        return "missing_document", "No documents were found for this row."
    return "unexplained", (None if as_at else "Last edition's answers carry no date, so a change in the source "
                                              "since then cannot be checked.")


CLAUDE_REASONS = ("missing_document", "different_document", "document_date", "rule_reading", "unexplained")


def fill_reason(project: Project, fill: dict) -> str:
    """Record the reason the owner accepted for one difference (`Use this as the reason` in the
    page): `fill` is {row, key, ours, theirs, reason, note, cells}. As skills/results/reasons.md
    writes them: a reason other than `unexplained` becomes Claude's reading (`by: "claude"`, kept
    by later comparisons while the figures stand); `unexplained` keeps its reason and only gains
    the note. Raises HandbookError when the difference is not there or its figures have moved."""
    reason, note = fill.get("reason"), str(fill.get("note") or "").strip()
    if reason not in CLAUDE_REASONS:
        raise HandbookError(f"A reason must be one of {', '.join(CLAUDE_REASONS)}, not {reason!r}.")
    if not note:
        raise HandbookError("A reason needs its note: one sentence on what the answers show.")
    path = project.file("compare/compare")
    data = read_json(path) or {}
    for d in data.get("disagreements") or []:
        if d.get("row") != fill.get("row") or d.get("key") != fill.get("key"):
            continue
        if not (_agree(d.get("ours"), fill.get("ours")) and _agree(d.get("theirs"), fill.get("theirs"))):
            raise HandbookError("The figures have changed since Claude suggested this reason. Ask Claude again.")
        if d.get("by") == "files" and d.get("reason") != "unexplained":
            raise HandbookError("The files already explain this difference, so its reason stays.")
        cells = fill.get("cells")
        d["note"] = note
        if isinstance(cells, list) and cells and all(isinstance(c, str) for c in cells):
            d["cells"] = cells
        if reason != "unexplained":
            d["reason"], d["by"] = reason, "claude"
        write_json(path, data)
        return reason
    raise HandbookError(f"There is no difference for {fill.get('row')} at {fill.get('key')} in the comparison.")


def _kept(previous, ours: dict, theirs: dict) -> dict:
    """`{(row, key): disagreement}` for the reasons Claude filled, whose figures have not moved."""
    kept = {}
    for d in (previous or {}).get("disagreements") or []:
        if not (isinstance(d, dict) and d.get("by") == "claude" and d.get("reason") in REASONS):
            continue
        row, key = d.get("row"), d.get("key")
        if key in ours.get(row, {}) and _agree(d.get("ours"), ours[row][key]) \
                and _agree(d.get("theirs"), theirs[row][key]):
            kept[(row, key)] = d
    return kept


# --- past editions and big moves --------------------------------------------------------------

def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _history(project: Project, latest: dict, units: list[dict], sheet: dict, this_run: dict) -> dict:
    """compare.json's `history`: for this edition's rows, every past edition's figure at each key
    our score sheet gives (`{row: {key: {edition: figure}}}`), the editions oldest first with the
    latest last, each edition's level, and this run's figure for those keys (`this_run`)."""
    name = latest.get("edition") or LATEST
    editions = [e for e in read_history(project) if e["edition"] != name] + [dict(latest, edition=name)]
    sections = [s.get("id") for s in sheet.get("sections") or []]
    bands = [f"band:{b.get('of')}" for b in sheet.get("bands") or []]
    rows = {}
    for unit in units:
        row = unit.get("id")
        keys = {}
        for key in [q["id"] for q in project.questions_for(unit)] + sections + ["overall"] + bands:
            values = {e["edition"]: e["rows"][row][key] for e in editions
                      if ((e.get("rows") or {}).get(row) or {}).get(key) is not None}
            if values:
                keys[key] = values
        if keys:
            rows[row] = keys
    ours = {row: {k: v for k, v in this_run.get(row, {}).items() if k in keys and v is not None}
            for row, keys in rows.items()}
    return {"editions": [e["edition"] for e in editions],
            "levels": {e["edition"]: e["level"] for e in editions},
            "rows": rows, "this_run": {row: keys for row, keys in ours.items() if keys}}


def _band_names(sheet: dict, of: str) -> list[str]:
    """The names of the bands of `of`, best (highest `min`) first."""
    for group in sheet.get("bands") or []:
        if group.get("of") == of:
            ordered = sorted(group.get("bands") or [], key=lambda b: b.get("min") or 0, reverse=True)
            return [b.get("name") for b in ordered]
    return []


def _band_word(names: list[str]) -> str:
    """The word the band names share ("Tier 1", "Tier 2" give "tier"), else "band"."""
    firsts = {str(n).split()[0] if str(n).split() else "" for n in names}
    word = firsts.pop() if len(firsts) == 1 else ""
    return word.lower() if word.isalpha() and len(word) >= 3 else "band"


def _is_percent(key: str, sheet: dict) -> bool:
    for section in sheet.get("sections") or []:
        if section.get("id") == key:
            return section.get("percent", True)
    overall = sheet.get("overall") or {}
    return key == "overall" and (overall.get("method") != "sum_points" or bool(overall.get("percent")))


def _most(key: str, question: dict | None, totals: dict | None, sheet: dict):
    """The most a key can be for a row: 100 for a percentage, else its most points (None when the
    row's totals are not known)."""
    if question is not None:
        a = question.get("answer") or {}
        if answers.is_number(question):              # a figure: the width of its range
            lo, hi = a.get("min"), a.get("max")
            return hi - lo if _number(lo) and _number(hi) and hi > lo else None
        return max((lvl.get("points") or 0 for lvl in a.get("levels") or []), default=0)
    if _is_percent(key, sheet):
        return 100
    sections = (totals or {}).get("sections") or {}
    if key in sections:
        return sections[key]["max"]
    return sum(e["max"] for e in sections.values()) if key == "overall" and sections else None


def _what(key: str, sheet: dict) -> str:
    """The key in words, to start a sentence: "The overall score", "Commitments (P1)"."""
    if key == "overall":
        return "The overall score"
    if key.startswith("band:"):
        of = key[len("band:"):]
        word = _band_word(_band_names(sheet, of))
        return f"The overall {word}" if of == "overall" else f"The {word} for {_what(of, sheet)}"
    for section in sheet.get("sections") or []:
        if section.get("id") == key:
            return f"{section['name']} ({key})" if section.get("name") else str(key)
    return f"Question {key}"


def _shown(v, key: str, sheet: dict, question: dict | None = None) -> str:
    """A figure as the sentence says it: "71%", "5 points", "Tier 1"; a number question's figure in
    its own unit ("73%")."""
    if not _number(v):
        return str(v)
    if answers.is_number(question):
        return answers.show_figure(question, round(v, 4))
    n = str(int(v)) if float(v).is_integer() else f"{round(v, 2):.2f}".rstrip("0").rstrip(".")   # 0.25, not 0.2 or 1e+06
    return f"{n}%" if _is_percent(key, sheet) else f"{n} point{'' if n == '1' else 's'}"


def _trend(past: dict, ours, rank) -> tuple:
    """("breaks" | "within" | "too_short", lowest, highest): whether ours falls outside the range of
    the past figures, as ranked by `rank` (None for a figure it cannot place)."""
    placed = [(rank(v), v) for v in past.values() if rank(v) is not None]
    if len(placed) < TREND_MIN_EDITIONS or rank(ours) is None:
        return "too_short", None, None
    lo, hi = min(placed, key=lambda x: x[0]), max(placed, key=lambda x: x[0])
    return ("breaks" if not lo[0] <= rank(ours) <= hi[0] else "within"), lo[1], hi[1]


def _move_text(m: dict, sheet: dict, edition: str, apart: int, no_evidence: bool, span: tuple,
               question: dict | None = None) -> str:
    """The big move in one plain sentence."""
    key, ours, theirs = m["key"], m["ours"], m["theirs"]
    what, show = _what(key, sheet), (lambda v: _shown(v, key, sheet, question))
    if m["why"] == "band_jump":
        word = _band_word(_band_names(sheet, key[len("band:"):]))
        text = (f"{what} moved from {theirs} in {edition} to {ours} in this run, "
                f"{NUMBER_WORDS.get(apart, apart)} {word}s apart")
    elif m["why"] == "nothing_found":
        text = (f"{what} was {show(theirs)} in {edition}, but this run found no evidence for it "
                f"and gives {show(ours)}" if no_evidence
                else f"{what} was {show(theirs)} in {edition} and scores nothing in this run")
    else:
        gap = abs(ours - theirs)
        gap = str(int(gap)) if float(gap).is_integer() else str(round(gap, 1))
        points = "points" if _is_percent(key, sheet) or gap != "1" else "point"
        if answers.is_number(question):
            unit = str((question.get("answer") or {}).get("unit") or "")
            points = "percentage points" if unit == "%" else unit
        text = (f"{what} went from {show(theirs)} in {edition} to {show(ours)} in this run, "
                f"{gap}{' ' + points if points else ''} {'lower' if ours < theirs else 'higher'}")
    if m["trend"] != "too_short":
        first, last, lo, hi = span
        where = "outside" if m["trend"] == "breaks" else "within"
        what_range = (f"the {_band_word(_band_names(sheet, key[len('band:'):]))}s it had"
                      if key.startswith("band:") else "its range")
        both = show(lo) if lo == hi else f"{show(lo)} to {show(hi)}"
        text += f", {where} {what_range} over {first} to {last} ({both})"
    return text + "."


def _big_moves(project: Project, disagreements: list[dict], history: dict, cells_by_row: dict,
               unit_by_id: dict, sheet: dict, questions: dict) -> list[dict]:
    """compare.json's `big_moves`, in the order of the disagreements they come from."""
    edition = history["editions"][-1]
    out = []
    for d in disagreements:
        if d.get("reason") not in BIG_MOVE_REASONS:
            continue
        row, key, ours, theirs = d["row"], d["key"], d["ours"], d["theirs"]
        cells = cells_by_row[row]
        qids = [q for q in d.get("cells") or [] if q in cells]
        no_evidence = bool(qids) and all(cells[q].get("state") == "no_evidence" for q in qids)
        apart = 0
        if key.startswith("band:"):
            names = _band_names(sheet, key[len("band:"):])
            rank = (lambda v, names=names: names.index(v) if v in names else None)
            if rank(ours) is None or rank(theirs) is None:
                continue
            apart = abs(rank(ours) - rank(theirs))
            if apart < BAND_JUMP:
                continue
            why = "band_jump"
        else:
            question = None
            if not (_number(ours) and _number(theirs)):
                continue
            unit = unit_by_id[row]
            question = next((q for q in project.questions_for(unit) if q["id"] == key), None) \
                if key in questions else None
            most = _most(key, question, None if question else row_totals(project, unit, cells), sheet)
            if not most:
                continue
            if theirs >= NOTHING_FOUND_SHARE * most and ours < theirs and (ours == 0 or no_evidence):
                why = "nothing_found"
            elif abs(ours - theirs) >= BIG_GAP_SHARE * most - 1e-9:
                why = "big_change"
            else:
                continue
            rank = (lambda v: v if _number(v) else None)
        past = (history["rows"].get(row) or {}).get(key) or {}
        trend, lo, hi = _trend(past, ours, rank)
        span = [ed for ed in history["editions"] if ed in past]
        m = {"row": row, "key": key, "ours": ours, "theirs": theirs, "why": why,
             "reason": d["reason"], "trend": trend}
        m["text"] = _move_text(m, sheet, edition, apart, no_evidence,
                               (span[0], span[-1], lo, hi) if span else (None, None, lo, hi),
                               None if key.startswith("band:") else question)
        out.append(m)
    return out


def compare(project: Project) -> dict:
    """The compare.json object for the project (not written). See Shapes in the plan; `summary`
    also has `of` (rows compared per key) and `not_compared` (`[{"row", "why", "text"}]`).
    Raises HandbookError when there are no valid held-back results or no usable score sheet."""
    results = read_held_back(project)
    sheet = project.read("scoresheet")
    level = results["level"]
    if not sheet:
        raise HandbookError("There is no score sheet yet, so there is nothing to compare.")
    if sheet.get("computable") is False and level != "question":
        raise HandbookError("The score sheet does not compute totals and last edition's results are "
                            f"given at {level} level, so there is nothing to compare: hold back last "
                            "edition's answers question by question to compare them.")
    held = results.get("rows") or {}
    units = [u for u in project.read("units").get("units", []) if isinstance(u, dict)]
    unit_by_id = {u.get("id"): u for u in units}
    questions = project.read("questions").get("questions", [])
    sections = {s.get("id") for s in sheet.get("sections") or []}
    rules = {r.get("id"): r for r in project.read("rules").get("rules", []) if isinstance(r, dict)}
    docs = project.documents()
    cutoff = project.read("project").get("cutoff")
    cutoff = cutoff if is_date(cutoff) else None
    as_at = results.get("as_at") if is_date(results.get("as_at")) else None    # never invented: unknown is None
    try:
        previous = read_json(project.file("compare/compare"))
    except HandbookError:
        previous = None                              # an unreadable old file is simply replaced

    rows, not_compared = [], []
    ours_by_row, theirs_by_row, cells_by_row, full_totals = {}, {}, {}, {}
    this_run = {}                                    # every key we give, per scored row (history)
    question_ids = {q.get("id") for q in questions}
    waits = needs.waiting(project)                   # answers that wait for the owner's data: not compared
    ends = scales(project)                           # relative figures' ends among all the rows, once
    for unit in units:
        row = unit.get("id")
        cells = {q: c for q, c in (project.answers(row).get("cells") or {}).items()
                 if isinstance(c, dict) and q not in (waits.get(row) or {})}
        if not any(c.get("state") in SCORED for c in cells.values()):
            if row in held:
                not_compared.append({"row": row, "why": "not_scored"})
            continue
        this_run[row], partial = _ours(project, unit, cells, "question", sheet, ends)
        if row not in held:
            not_compared.append({"row": row, "why": "not_in_last_edition"})
            continue
        ours_all = {k: v for k, v in this_run[row].items()
                    if level == "question" or k not in question_ids}
        theirs_row = held[row]
        qmap_row = {q["id"]: q for q in project.questions_for(unit)}
        for k, v in theirs_row.items():
            # A level held as text ("yes") is compared with our level, not our points: two levels can be
            # worth the same points (EggTrack's yes/no flags are all 0), so points would hide a difference.
            if isinstance(v, str) and k in ours_all and k in qmap_row and not answers.is_number(qmap_row[k]):
                c = cells.get(k) or {}
                ours_all[k] = c.get("value") if c.get("state") == "answered" else qmap_row[k].get("no_evidence_value")
        keys = [k for k in ours_all if theirs_row.get(k) is not None]
        # A question last edition left blank (null in its results) is compared too: an answer found where
        # they recorded none is a difference to see (hpc-pay, 21 Oct 2026: two answered blanks read as 21 of 21).
        keys += [k for k in ours_all if level == "question" and k in question_ids and k in theirs_row
                 and theirs_row[k] is None]
        if partial:
            # Last edition gave this row only a total: nothing to compare until the row is complete (KnowTheChain).
            total_only = not any(k in question_ids or k in sections for k, v in theirs_row.items() if v is not None)
            not_compared.append({"row": row, "why": "waiting" if row in waits else "total_only" if total_only else "partial"})
        if not partial and sheet.get("computable") is not False:
            full_totals[row] = row_totals(project, unit, cells, ends)
        if not keys:
            if any(k.startswith("rank:") for k in theirs_row) and not partial:
                not_compared.append({"row": row, "why": "rank_only"})
            continue
        ours_by_row[row] = {k: ours_all[k] for k in keys}
        theirs_by_row[row] = {k: theirs_row[k] for k in keys}
        cells_by_row[row] = cells
    for row in held:
        if row not in unit_by_id:
            not_compared.append({"row": row, "why": "not_in_units"})
    for n in not_compared:
        n["text"] = NOT_COMPARED_TEXT[n["why"]]

    keep = _kept(previous, ours_by_row, theirs_by_row)
    disagreements, agree_counts, of_counts = [], {}, {}
    for unit in units:
        row = unit.get("id")
        if row not in ours_by_row:
            continue
        cells = cells_by_row[row]
        scored = [q for q in project.questions_for(unit)
                  if isinstance(cells.get(q["id"]), dict) and cells[q["id"]].get("state") in SCORED]
        ours, theirs = ours_by_row[row], theirs_by_row[row]
        qmap = {q["id"]: q for q in project.questions_for(unit)}
        tol = results.get("tolerance")

        def agrees(k):
            if theirs[k] is None:                  # left blank last edition: agrees only with no answer found
                return (cells.get(k) or {}).get("state") != "answered"
            # A tolerance (last results that are a panel's mean, as Food-EPI's are) applies to answers.
            if k in qmap and _number(tol) and _number(ours[k]) and _number(theirs[k]):
                return abs(ours[k] - theirs[k]) <= tol + 1e-9
            if answers.is_number(qmap.get(k)):
                return answers.same_answer(qmap[k], ours[k], theirs[k])
            return _agree(ours[k], theirs[k])
        agree = {k: agrees(k) for k in ours}
        rows.append({"row": row, "ours": ours, "theirs": theirs, "agree": agree})
        for key, ok in agree.items():
            of_counts[key] = of_counts.get(key, 0) + 1
            agree_counts[key] = agree_counts.get(key, 0) + ok
            if ok:
                continue
            qids = _qids_under(key, scored, sections)
            if theirs[key] is None:
                d = {"row": row, "key": key, "ours": ours[key], "theirs": None, "reason": "published_blank",
                     "by": "files", "note": "Last edition left this blank; this edition found an answer.", "cells": qids}
            elif (row, key) in keep:
                d = {**keep[(row, key)], "row": row, "key": key, "ours": ours[key],
                     "theirs": theirs[key]}
                d.setdefault("cells", qids)
            else:
                reason, note = _reason(project, row, qids, cells, rules, docs, cutoff, as_at)
                d = {"row": row, "key": key, "ours": ours[key], "theirs": theirs[key],
                     "reason": reason, "by": "files", "note": note, "cells": qids}
            disagreements.append(d)

    order = [q["id"] for q in questions] + [s["id"] for s in sheet.get("sections") or []] + ["overall"]
    key_order = sorted(of_counts, key=lambda k: order.index(k) if k in order else len(order))
    history = _history(project, results, units, sheet, this_run)
    out = {"level": level, "edition": results.get("edition"), "rows": rows, "disagreements": disagreements,
           "summary": {"rows": len(rows),
                       "agree": {k: agree_counts[k] for k in key_order},
                       "of": {k: of_counts[k] for k in key_order},
                       "not_compared": not_compared,
                       "as_at": as_at},
           "history": history,
           "rank_order": _rank_order(held, full_totals, unit_by_id),
           "big_moves": _big_moves(project, disagreements, history, cells_by_row, unit_by_id, sheet,
                                   {q.get("id"): q for q in questions})}
    problems = check_compare(out)
    if problems:
        raise HandbookError("The comparison would not be valid:\n- " + "\n- ".join(problems))
    return out

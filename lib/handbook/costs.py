"""What Claude's work costs in tokens of the owner's Claude plan: what this project has used so far,
and a rough range for work before it runs (scoring rows again, a set-up draft, a chat turn).

    summary(project) -> dict          # the bundle's `cost`: the model, each row's document length, what was used
    estimate_rows(project, rows, questions=None) -> {"low", "high", "point", "rows", "cells", "basis", "measured"}
    setup_draft(project, chars) -> {"low", "high", "point", "basis"}
    used(project) -> dict             # tokens used so far, by kind and by run

Everything is tokens processed (fresh input, cache reads and output added up), the figure the
scorers' receipts give. No money, and no share of a plan: Anthropic does not publish a plan's
allowance in tokens, so the page compares with work the owner has already run instead.

Scoring a row in one read (oneread.py, the default from 21 Oct 2026 for a row small enough): tokens =
ONE_BASE + characters / ONE_CHARS_PER_TOKEN + ONE_PER_QUESTION x questions, since the message is the pages
and the questions and the reply runs to a few hundred tokens an answer. Measured 21 Oct 2026: RTI
Liberia 99,000 (formula 104,000), Kenya 196,000 (180,000), Sierra Leone 176,000 (168,000); Pecking Order
dk-kfc 76,000 (74,000); a CTIS trial 14,000 (12,000). Rows that share long documents (LCV vote records)
come in at a third of it, which the project's own receipts then correct. A one-read receipt is told
apart by having no cache reads (one_read_receipt).

A row too long for one message is read in parts (oneread.parts, from 21 Oct 2026): tokens = parts x
(ONE_BASE + ONE_PER_QUESTION x questions) + characters / ONE_CHARS_PER_TOKEN.

Scoring a row with the full scorer (careful scoring, and a kit's budget): tokens = A x questions^Q_EXP x characters^C_EXP, where characters is the
length of the text of the row's kept documents. Fitted 19 Oct 2026 on 192 rows scored in the format
tests (25 benchmarks, receipts from `hb score`): the two together explain about 70% of the spread;
each roughly doubles the cost when it is five times bigger. A was scaled down on 20 Oct 2026: the 36
rows scored since the 19 Oct scorer safeguards (13 benchmarks) came in at 0.39 to 1.02 times the old
figure, 0.57 in the middle (BankTrack's five banks, 4 questions each: 0.22M to 0.69M against 0.36M
to 1.42M), and a kit's budget of 2.5 times the old figure gave 3.5M for one bank. Half the rows came within 0.8 and 1.3
times the fitted figure, so with no history the range is wider (DEFAULT_SPREAD, about one standard
deviation either side) to allow for a benchmark that runs dearer or cheaper than most. Once this
project has scored rows with a receipt, the figure is scaled to them (`calibration`). Second searches
and second reads are not in this figure: the trial's own usage gives those (estimate.py).

Finding a row's documents (`estimate_rows(..., finding=True)`, for the page's trial and runs and `hb cost`:
wherever the owner is shown a figure before work runs) is added for each row not looked for yet: FIND_ROW, or
this project's own finds once a run has recorded some (`find_cost`). Such a row's documents are not known, so
its scoring is priced at the middle row but its high end allows for big documents (BIG_CHARS, read in parts),
and for the wider second search most such rows got on 21 Oct 2026 (SECOND_FIND, SECOND_SHARE), with its rescore
of what it added (SECOND_CHARS). Measured 21 Oct 2026: page trials came in at 3 to 14 times the top of an
estimate that left both out (hpc-pay 1.06M against 91k to 217k; tpi-mq 2.05M against 117k to 279k), finding
taking 60 to 80% on most benchmarks; sweep round 3 found it 35% to 55% of a trial (21 rows, 234,000 in the
middle, inside FIND_ROW's middle half).
"""
from __future__ import annotations
import json
from statistics import median

from . import needs
from .folder import Project, read_json
from .runs import cache_read_share

A, Q_EXP, C_EXP = 680, 0.43, 0.44           # A was 1190 until 20 Oct 2026 (see above)
MIN_CHARS = 2000                     # a row with almost nothing to read still has the scorer's own reading
DEFAULT_CHARS = 130_000              # the median row in the fit, for a row with no documents yet
DEFAULT_SPREAD = (0.65, 1.55)        # no history: about one standard deviation either side
OWN_SPREAD = (0.8, 1.3)              # scaled to this project: the middle half of the fit's rows
FEW_SPREAD = (0.7, 1.45)             # scaled to fewer than four rows of this project
FIT_ROWS = 192
# The background set-up draft, measured on 25 format tests (19 Oct 2026): about 1.7 million in the
# middle, half between 1.2 and 2.3 million; material over BIG_MATERIAL characters ran 2 to 7 million.
# Since the draft works in few, large steps, the eight drafts of sweep round 3 (21 Oct 2026) ran 263,000 to
# 1,120,000, 530,000 in the middle (Global Health 50/50: shown 0.8 to 2.3 million, used 534,000).
SETUP_SMALL = (300_000, 1_200_000)
SETUP_BIG = (2_000_000, 6_000_000)
BIG_MATERIAL = 200_000
CHAT_TURN = (30_000, 100_000)        # one corner chat reply, measured on the Feedback tab at 28k to 62k
STEPS = ("find", "second_find", "score", "second")
ONE_BASE, ONE_CHARS_PER_TOKEN, ONE_PER_QUESTION = 1500, 3.6, 1000
ONE_QUESTION_TOKENS = 350            # a question as the message holds it, for whether a row fits
# Finding one row's documents on the page (cmd_find: a finder session, and a second when the first list did not
# all save or saved a thin copy): 48 rows of 19 benchmarks, 21 Oct 2026 (runs/receipts/find-*.json), 120,000 to
# 330,000 tokens for the middle half, 190,000 in the middle; 30,000 for a chocolate bar, 690,000 for a state's laws.
# Sweep round 3 (21 Oct 2026) measured 21 rows of eight benchmarks at 98,000 to 747,000, 234,000 in the middle and
# half between 167,000 and 320,000, inside this range; cmd_find.BUDGET now stops a row's finding at 350,000.
FIND_ROW = (120_000, 190_000, 330_000)          # low, point, high
# The size a row not looked for yet may turn out to be: listed companies' annual reports and a state's laws ran
# 660,000 to 1.5 million characters a row (hpc-pay, tpi-mq, de-transparenzranking, pt-qdsd, 21 Oct 2026).
BIG_CHARS = 700_000
# A wider second search after scoring (serve.score_run, cmd_find.wider_search): one finder session (49 first
# sessions, 21 Oct 2026: 100,000 to 210,000, 140,000 in the middle), then about half the row's answers scored
# again. SECOND_SHARE of rows are assumed to need one: 27 of 37 page-trial rows would have, that day.
SECOND_FIND = (100_000, 140_000, 210_000)
SECOND_SHARE = 0.7
# The rescore after it reads only what it added and what those answers cited (kit --docs, 22 Oct 2026; it read the
# whole kit again before, and tpi-mq's General Mills row cost 2.41M against 874k): about half the row's questions,
# over this many characters (a report or two in the middle, a law's worth at the top).
SECOND_CHARS = (60_000, 250_000)
# A row whose group (a company's products, a state's councils) had another row searched already finds the group's
# own documents filed for it (cmd_find, "group"): assumed to cost this share of a first row's finding (on
# nrdc-tissue, 21 Oct 2026, three or four of a product's six to eight documents were its company's).
GROUP_FIND_SHARE = 0.65


def _question_tokens(questions: int, question_chars: int | None) -> float:
    """Tokens the questions take in the message: their own text when known (the UK university league's 67
    questions carried long rules, sweep round 3), else ONE_QUESTION_TOKENS each."""
    from .oneread import CHARS_PER_TOKEN
    if question_chars:
        return question_chars / CHARS_PER_TOKEN
    return ONE_QUESTION_TOKENS * max(questions, 1)


def fits_one_read(questions: int, chars: int, question_chars: int | None = None) -> bool:
    """Whether a row this size is read in one message (oneread.LIMIT) rather than in parts."""
    from .oneread import CHARS_PER_TOKEN, LIMIT
    return chars / CHARS_PER_TOKEN + _question_tokens(questions, question_chars) <= LIMIT


def one_read_receipt(receipt: dict) -> bool:
    """A receipt from one read: no cache reads (it turns caching off), in one call or a few (a kit read in
    parts takes one a part, and one more where the parts disagree)."""
    return (receipt.get("cache_read") or 0) == 0 and 0 < (receipt.get("turns") or 0) <= 12


def read_parts(questions: int, chars: int, question_chars: int | None = None) -> int:
    """How many parts a row this size is read in (oneread.parts): 1 when it fits one message."""
    from .oneread import CHARS_PER_TOKEN, LIMIT, NOTE_ROOM
    if fits_one_read(questions, chars, question_chars):
        return 1
    room = max(LIMIT - _question_tokens(questions, question_chars) - NOTE_ROOM, LIMIT // 4)
    return max(2, -(-int(chars / CHARS_PER_TOKEN) // int(room)))


def score_tokens(questions: int, chars: int | None, factor: float = 1.0, one_read: bool | None = None,
                 question_chars: int | None = None) -> float:
    """The middle figure for scoring `questions` questions on a row whose documents hold `chars` characters:
    read without tools (in one message, or in parts when it is too long: the pages once, the brief and
    questions once a part), or with the full scorer when `one_read` is False (careful scoring, a kit's budget).
    `question_chars`, the questions' own text, adds what runs over the usual length to every part."""
    c = DEFAULT_CHARS if chars is None else max(chars, MIN_CHARS)
    if one_read is False:
        return A * factor * max(questions, 1) ** Q_EXP * c ** C_EXP
    n = 1 if one_read else read_parts(questions, c, question_chars)
    extra = max(0.0, _question_tokens(questions, question_chars) - ONE_QUESTION_TOKENS * max(questions, 1))
    return factor * (n * (ONE_BASE + ONE_PER_QUESTION * max(questions, 1) + extra) + c / ONE_CHARS_PER_TOKEN)


def doc_chars(project: Project) -> dict[str, int]:
    """Document id -> characters of text in its pages, for every kept document."""
    out = {}
    for doc, meta in project.documents().items():
        if isinstance(meta, dict) and meta.get("kept") is False:
            continue
        out[doc] = sum(len(p.get("text") or "") for p in project.pages(doc) if isinstance(p, dict))
    return out


def _searched(project: Project) -> set[str]:
    """Rows a run recorded a find for."""
    return {row for run in project.runs() if isinstance(run, dict) and isinstance(run.get("usage"), dict)
            for row, steps in run["usage"].items() if isinstance(steps, dict) and steps.get("find") is not None}


def row_chars(project: Project, docs: dict[str, int] | None = None) -> dict[str, int]:
    """Row id -> characters of text in its kept documents, for rows whose documents were looked for
    (a row with no found file is left out: its documents are not known yet; so is one that holds only
    documents a search for another row of its group filed for it, `shared`)."""
    docs = doc_chars(project) if docs is None else docs
    searched = None
    out = {}
    for u in project.read("units").get("units", []):
        uid = u.get("id") if isinstance(u, dict) else None
        if not isinstance(uid, str):
            continue
        if not (project.path / "found" / f"{uid}.json").is_file():
            continue
        found = project.found(uid)
        shared = set(found.get("shared") or [])
        if shared and not (set(found.get("documents") or []) - shared) and not found.get("expected_not_found") \
                and not found.get("second_search"):
            searched = _searched(project) if searched is None else searched
            if uid not in searched:
                continue
        out[uid] = sum(docs.get(d, 0) for d in found.get("documents") or [] if isinstance(d, str))
    return out


def _units(project: Project) -> dict:
    return {u["id"]: u for u in project.read("units").get("units", [])
            if isinstance(u, dict) and isinstance(u.get("id"), str)}


def _run_questions(project: Project, run: dict, unit: dict) -> int:
    covered = run.get("questions")
    return sum(1 for q in project.questions_for(unit) if covered is None or q.get("id") in covered)


def measured_rows(project: Project, chars: dict[str, int] | None = None) -> list[dict]:
    """Every row a scorer's receipt was recorded for: [{run, kind, row, questions, chars, tokens, ratio}],
    ratio being the tokens against the default formula's figure."""
    chars = row_chars(project) if chars is None else chars
    units = _units(project)
    out = []
    for run in project.runs():
        if not isinstance(run, dict):
            continue
        for row, steps in (run.get("receipts") or {}).items():
            r = (steps or {}).get("score") if isinstance(steps, dict) else None
            if not isinstance(r, dict) or row not in units:
                continue
            tokens = (r.get("fresh") or 0) + (r.get("cache_read") or 0) + (r.get("output") or 0)
            if not tokens:
                continue
            q = _run_questions(project, run, units[row])
            out.append({"run": run.get("id"), "kind": run.get("kind"), "row": row, "questions": q,
                        "chars": chars.get(row), "tokens": tokens,
                        "ratio": tokens / score_tokens(q, chars.get(row), one_read=one_read_receipt(r))})
    return out


def _quartile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def calibration(project: Project, chars: dict[str, int] | None = None) -> dict:
    """{"basis": "project" | "default", "factor", "low", "high", "rows"}: the default formula scaled to
    this project's own scored rows when there are any, with the range around it."""
    rows = measured_rows(project, chars)
    if not rows:
        return {"basis": "default", "factor": 1.0, "low": DEFAULT_SPREAD[0], "high": DEFAULT_SPREAD[1], "rows": 0}
    ratios = [r["ratio"] for r in rows]
    factor = median(ratios)
    if len(rows) < 4:       # too few for quartiles: at least as wide as every row measured
        low = min(FEW_SPREAD[0], min(ratios) / factor)
        high = max(FEW_SPREAD[1], max(ratios) / factor)
    else:
        low = min(_quartile(ratios, .25) / factor, OWN_SPREAD[0])
        high = max(_quartile(ratios, .75) / factor, OWN_SPREAD[1])
    return {"basis": "project", "factor": round(factor, 4), "low": round(low, 3), "high": round(high, 3),
            "rows": len(rows)}


def measured_finds(project: Project) -> list[float]:
    """Tokens finding each row's documents took in this project's runs (step find, with an equal share of
    any group search), one figure per row and run."""
    units = _units(project)
    out = []
    for run in project.runs():
        if not isinstance(run, dict):
            continue
        usage = run.get("usage") or {}
        found = {r: float(s.get("find")) for r, s in usage.items()
                 if isinstance(s, dict) and isinstance(s.get("find"), (int, float))}
        for key, tokens in (run.get("group_usage") or {}).items():
            label, _, value = str(key).partition("=")
            members = [u for u, unit in units.items() if str((unit.get("labels") or {}).get(label)) == value]
            for m in members:
                if m in found and isinstance(tokens, (int, float)):
                    found[m] += tokens / len(members)
        out += [t for t in found.values() if t > 0]
    return out


def find_cost(project: Project) -> dict:
    """{"low", "point", "high", "basis": "project" | "default", "rows"}: tokens to find one row's documents,
    from this project's own finds when a run recorded some (at least as wide as the default's spread)."""
    low, point, high = FIND_ROW
    own = measured_finds(project)
    if not own:
        return {"low": low, "point": point, "high": high, "basis": "default", "rows": 0}
    mid = median(own)
    lo, hi = mid * low / point, mid * high / point
    if len(own) < 4:
        lo, hi = min(lo, min(own)), max(hi, max(own))
    else:
        lo, hi = min(lo, _quartile(own, .25)), max(hi, _quartile(own, .75))
    return {"low": round(lo), "point": round(mid), "high": round(hi), "basis": "project", "rows": len(own)}


def estimate_rows(project: Project, rows: list[str], questions: list[str] | None = None,
                  model: dict | None = None, chars: dict[str, int] | None = None, one_read: bool | None = None,
                  finding: bool = False) -> dict:
    """A rough range for scoring `rows` again on `questions` (None: every question that applies to
    each row). A row whose documents are not known yet counts as this project's middle row, or the fit's.
    `one_read` False prices the full scorer whatever the row's size (hb kit's budget is for it alone).
    The questions' own text is counted (a row with long rules may not fit one message).
    Answers that wait for the owner's data (needs.py) are not scored, so they are left out.
    `finding` (the page's trial and runs, hb cost): each row not looked for yet also costs finding its documents
    (`find`, per row), unless all it asks rests on the owner's data, and its high end allows for big
    documents (BIG_CHARS). `finding` in the result is what finding adds to low, point and high (the first
    search and the wider second search's own session; scoring again after it counts as scoring)."""
    chars = row_chars(project) if chars is None else chars
    model = calibration(project, chars) if model is None else model
    if one_read is None and project.read("project").get("scoring") == "careful":
        one_read = False                     # careful scoring: always the full scorer
    units = _units(project)
    known = [c for c in chars.values() if c]
    middle = int(median(known)) if known else DEFAULT_CHARS
    held = needs.waiting(project)
    owners = needs.only_source_needs(project) if finding else []
    find = find_cost(project) if finding else None
    keys = group_keys(project) if finding else {}
    seen = {k for r in chars for k in keys.get(r, ())}      # groups with a row looked for already
    point = low = high = 0.0
    cells, n, find_rows, find_shared = 0, 0, 0, 0
    f_point = f_low = f_high = 0.0                          # what finding adds, for the words that say so
    for row in dict.fromkeys(rows):
        unit = units.get(row)
        if unit is None:
            continue
        qs = [q for q in project.questions_for(unit) if (questions is None or q.get("id") in questions)
              and q.get("id") not in (held.get(row) or {})]
        if not qs:
            continue
        n += 1
        cells += len(qs)
        qchars = sum(len(json.dumps(q, ensure_ascii=False)) for q in qs)
        s = score_tokens(len(qs), chars[row] if row in chars else middle, model["factor"], one_read, qchars)
        point, low, high = point + s, low + s * model["low"], high + s * model["high"]
        if finding and row not in chars:
            top = max(s * model["high"], score_tokens(len(qs), max(middle, BIG_CHARS), model["factor"], one_read, qchars))
            high += top - s * model["high"]
            if {q.get("id") for q in qs} - needs.owner_only(project, row, owners):
                find_rows += 1
                share = GROUP_FIND_SHARE if seen & set(keys.get(row, ())) else 1.0
                find_shared += share < 1
                seen |= set(keys.get(row, ()))
                point, low, high = point + share * find["point"], low + share * find["low"], high + share * find["high"]
                # the wider second search some rows get, and those answers scored again (none at the low end)
                half = max(1, len(qs) // 2)
                hchars = qchars * half // len(qs)
                point += SECOND_SHARE * (SECOND_FIND[1] + score_tokens(half, SECOND_CHARS[0], model["factor"], one_read, hchars))
                high += SECOND_FIND[2] + score_tokens(half, SECOND_CHARS[1], model["factor"], one_read, hchars) * model["high"]
                f_point += share * find["point"] + SECOND_SHARE * SECOND_FIND[1]
                f_low += share * find["low"]
                f_high += share * find["high"] + SECOND_FIND[2]
    return {"rows": n, "cells": cells, "point": round(point), "low": round(low), "high": round(high),
            "basis": model["basis"], "measured": model["rows"], "find_rows": find_rows, "find_shared": find_shared,
            "find": find, "finding": {"rows": find_rows, "point": round(f_point), "low": round(f_low),
                                      "high": round(f_high)} if find_rows else None}


def group_keys(project: Project) -> dict[str, list[str]]:
    """Row -> its "label=value" groups that two or more rows share, for the project's group labels."""
    labels = project.read("project").get("group_labels") or []
    units = [u for u in project.read("units").get("units", []) if isinstance(u, dict) and isinstance(u.get("id"), str)]
    counts: dict[str, int] = {}
    mine: dict[str, list[str]] = {}
    for u in units:
        for label in labels:
            value = (u.get("labels") or {}).get(label)
            if value is not None and str(value).strip():
                key = f"{label}={value}"
                counts[key] = counts.get(key, 0) + 1
                mine.setdefault(u["id"], []).append(key)
    return {r: [k for k in ks if counts[k] > 1] for r, ks in mine.items() if any(counts[k] > 1 for k in ks)}


def past_drafts(project: Project) -> list[int]:
    """Tokens each set-up draft used, oldest first: from the page's usage log, else the last receipt."""
    drafts = [e["processed"] for e in _log(project) if e.get("kind") == "setup-draft"
              and isinstance(e.get("processed"), int) and e["processed"] > 0]
    if drafts:
        return drafts
    receipt = read_json(project.path / "runs" / "receipts" / "setup-draft.json", {}) or {}
    usage = receipt.get("usage") if isinstance(receipt, dict) else None
    if isinstance(usage, dict):
        total = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                      "cache_read_input_tokens", "output_tokens"))
        if total:
            return [total]
    return []


def setup_draft(project: Project | None, chars: int | None) -> dict:
    """A rough range for the background set-up draft over the owner's material (`chars` characters
    in all): this project's last draft when there was one, else the measured default by size."""
    past = past_drafts(project) if project is not None else []
    if past:
        last = past[-1]
        return {"low": round(last * 0.7), "high": round(last * 1.4), "point": last, "basis": "project"}
    low, high = SETUP_BIG if (chars or 0) > BIG_MATERIAL else SETUP_SMALL
    return {"low": low, "high": high, "point": round((low * high) ** 0.5), "basis": "default"}


def _log(project: Project) -> list[dict]:
    """The page's usage log (`page-data/_log/usage.jsonl`, serve.py's log_usage), one entry per line."""
    path = project.path / "page-data" / "_log" / "usage.jsonl"
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
    except OSError:
        pass
    return out


def _run_tokens(run: dict) -> int:
    usage = run.get("usage") or {}
    total = sum(v for steps in usage.values() if isinstance(steps, dict)
                for s, v in steps.items() if s in STEPS and isinstance(v, int))
    return total + sum(v for v in (run.get("group_usage") or {}).values() if isinstance(v, int))


def used(project: Project) -> dict:
    """What this project has used so far: {"total", "by_kind": {kind: tokens}, "runs": [{id, kind,
    started, finished, rows, questions, tokens, per_row, receipts}], "setup_drafts": [tokens],
    "chat": {"replies", "tokens", "median"}}. Run kinds are trial, full and rerun (a correction's
    re-read is a one-row rerun). The Feedback round's own tokens are in the bundle's feedback part."""
    runs = []
    for run in project.runs():
        if not isinstance(run, dict):
            continue
        tokens = _run_tokens(run)
        if not tokens:
            continue
        rows = [r for r, s in (run.get("usage") or {}).items()
                if isinstance(s, dict) and any(isinstance(s.get(k), int) for k in STEPS)]
        runs.append({"id": run.get("id"), "kind": run.get("kind"), "started": run.get("started"),
                     "finished": run.get("finished"), "rows": len(rows),
                     "questions": len(run["questions"]) if run.get("questions") else None,
                     "tokens": tokens, "per_row": round(tokens / len(rows)) if rows else None,
                     "receipts": cache_read_share(run) is not None})
    drafts = past_drafts(project)
    chat = [e["processed"] for e in _log(project) if e.get("kind") == "sample" and isinstance(e.get("processed"), int)]
    by_kind: dict[str, int] = {}
    for r in runs:
        by_kind[r["kind"] or "run"] = by_kind.get(r["kind"] or "run", 0) + r["tokens"]
    if drafts:
        by_kind["setup"] = sum(drafts)
    if chat:
        by_kind["chat"] = sum(chat)
    bulk = [e["processed"] for e in _log(project) if e.get("kind") == "bulk" and isinstance(e.get("processed"), int)]
    if bulk:
        by_kind["bulk"] = sum(bulk)        # reading what an owner added in bulk (bulk.py)
    return {"total": sum(by_kind.values()), "by_kind": by_kind, "runs": runs, "setup_drafts": drafts,
            "chat": {"replies": len(chat), "tokens": sum(chat), "median": round(median(chat)) if chat else None}}


def summary(project: Project) -> dict:
    """The bundle's `cost`: what the page and its chat need to give a range before work runs and to say
    what was used. `rows` is each row's document length (rows whose documents were looked for)."""
    from .oneread import CHARS_PER_TOKEN, LIMIT, NOTE_ROOM
    chars = row_chars(project)
    model = calibration(project, chars)
    measured = measured_rows(project, chars)
    known = [c for c in chars.values() if c]
    listed = needs.only_source_needs(project)
    units = _units(project)
    return {
        "model": {"a": A, "q_exp": Q_EXP, "c_exp": C_EXP, "min_chars": MIN_CHARS, "default_chars": DEFAULT_CHARS,
                  "middle_chars": int(median(known)) if known else DEFAULT_CHARS, "fit_rows": FIT_ROWS, **model,
                  # one read, as score_tokens has it: a row is read in one message, or in parts when too long
                  "one_read": {"base": ONE_BASE, "chars_per_token": ONE_CHARS_PER_TOKEN, "per_question": ONE_PER_QUESTION,
                               "fit_chars_per_token": CHARS_PER_TOKEN, "question_tokens": ONE_QUESTION_TOKENS,
                               "limit": LIMIT, "note_room": NOTE_ROOM},
                  "careful": project.read("project").get("scoring") == "careful", "big_chars": BIG_CHARS},
        # finding a row's documents, for rows not looked for yet (those not in `rows`); questions only the owner's
        # data answers are never searched for, and answers that wait for that data are not scored (needs.py)
        "find": find_cost(project),
        "second": {"low": SECOND_FIND[0], "point": SECOND_FIND[1], "high": SECOND_FIND[2], "share": SECOND_SHARE,
                   "chars": list(SECOND_CHARS)},
        "groups": group_keys(project), "group_find_share": GROUP_FIND_SHARE,
        "owner_only": {r: sorted(q) for r in units if (q := needs.owner_only(project, r, listed))} if listed else {},
        "held": {r: sorted(q) for r, q in needs.waiting(project).items()},
        "rows": chars,
        "measured": {r["row"]: r["tokens"] for r in measured},   # the latest receipt's tokens per row
        "setup": {"small": list(SETUP_SMALL), "big": list(SETUP_BIG), "big_material": BIG_MATERIAL,
                  "last": (past_drafts(project) or [None])[-1]},
        "chat_turn": list(CHAT_TURN),
        "used": used(project),
    }


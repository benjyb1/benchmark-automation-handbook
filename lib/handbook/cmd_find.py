"""hb find --project DIR (--row ROW | --group LABEL=VALUE) [--missing TEXT] [--questions Q1,Q2] [--budget N] [--json]: find and save the
documents for one row (or one group) in a clean headless Claude session, for the page's Score button.

What the run skill's finder does from a Claude Code session (skills/run/finder.md), without one: the
page starts scoring on its own (serve.py), so the finder has to run as `hb score` runs a scorer, in a
fresh `claude -p` in safe mode on the cheaper model, with nothing of the owner's session riding along.

The finder searches and reads the web but runs no commands. It reads a small folder made for it here
(the row, the questions, the places to look, what is already saved) and writes finds.json there: the
addresses to save and what it could not find. This command then saves each address with `hb fetch` and
records each gap with `hb found`. A session that reads web pages and could also run shell commands on
the owner's Mac is what that avoids: a page can carry words meant to steer it. It has no Bash, and the
file tools are refused under the owner's folders, as for a scorer (cmd_score.OFF_LIMITS).

The trade-off: the finder cannot see when a fetch fails and try again, as one with a shell can. Fetch
itself tries the archive and a browser view; what still fails is recorded as not found, so the second
search, the Grid's "missing" list and the owner can pick it up.

The finder's own web tool is not the tool's fetch: it sends its own agent from this Mac, which some robot
filters refuse (abnamro.com answered it 503 on every page, 20 Oct 2026, and a browser 200), it is not
allowed on some sites (web.archive.org) and it stops at a smaller file size. So an address the finder could
not open for one of those reasons is listed with `refused`, and `hb fetch` tries it as a browser would.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .claude_cli import FINDER_MODEL
from .shapes import where_without_stale
from .cli import command_parser, open_project
from . import fetch, needs
from .cmd_score import OFF_LIMITS
from .folder import HandbookError, _matches, write_json

HB = Path(__file__).resolve().parents[1] / "hb.py"
CAP = 120_000                # what the brief tells a finder session it may use; BUDGET is what stops it
# Tokens one row's finding may use over both rounds before the session is stopped (what it wrote to finds.json
# so far is still saved). Sweep round 3 (21 Oct 2026) measured 98,000 to 747,000 a row, 234,000 in the middle,
# with nothing to stop a long one: the UK university league spent 747,000 on one university.
BUDGET = 350_000
MIN_ROUND = 60_000           # a second round only when at least this much of the budget is left
MAX_SAVE = 25                # addresses saved from one round's list (Global Health 50/50: 224 for four rows)
TIMEOUT_S = 1500
TOOLS = "Read,Write,WebSearch,WebFetch"
RECEIPTS = ("runs", "receipts")

BRIEF = """# Finding documents for a benchmark

You are a document finder. Someone else scores, from the saved text alone. You never answer the
questions and never say what a document shows about them.

Read `context.json` in this folder. It gives the benchmark's name and cut-off date, the {who}, the
questions (what kind of document matters), the places to look, last year's sources (places to try,
never proof of anything), and the documents already saved, which you do not need again.

## How to look

1. Work through the places to look in order, then the organisation's own website, then web search.
   Use your web search and fetch tools. Try every place to look: each is there because the owner named
   it. When one gives nothing, record it in `missing` with what you looked for there.
2. Prefer the organisation's own document, on its own site, over anything about it elsewhere. A
   landing page, summary or press release is not the document: follow it to the full policy, law or
   report. Use something not in the organisation's own words only when you cannot find its own, and
   mark it weak.
3. Find the version that was current on the cut-off date. A document dated after the cut-off is no
   use. If the live page has changed since, give an archived copy from before the cut-off
   (web.archive.org).
4. A site in several parts is several documents: list each part that matters.
5. Do not stop at the first hit. Stop when every question has a place it would be answered from,
   listed or recorded as not found.
6. Never list the benchmark's own published results, scorecards, rankings or reports about these
   {plural}, or news and write-ups of them. They would reach the scorer.
7. Prefer an address that is the document itself (a PDF, or the page with the full text) to one
   that needs a script or a login to show it. When a page you open links to a file a question needs
   (a spreadsheet, a list, a PDF), list the file's own address too, from an archived copy as well.
8. Your fetch tool is sometimes refused where a browser is not: an HTTP error such as 403, 429 or
   503 from the organisation's site, a site your tool is not allowed to open (web.archive.org), or
   a file too large for it. Do not give up on that site, and do not swap in a copy elsewhere for
   it: keep searching for its documents' addresses (search results, links on pages you could open,
   other language versions) and list each one with `refused` saying what your tool said. This tool
   then fetches it itself, as a browser, and tries the web archive for a copy from before the
   cut-off. Never list an address you have not seen in a search result or on a page.
9. List only what the questions need, about one or two documents a question, and never more than
   {max_save} addresses: never a whole website's pages, and nothing for a question not listed in
   context.json.

## What to write

Write `finds.json` in this folder, and nothing else:

```
{{"save": [{{"url": "https://...", "how": "plan" | "search" | "last_year",
             "date": "YYYY-MM-DD or null (only when the document says the date it speaks as at)",
             "weak": "one line saying why it is not in the organisation's own words, or null",
             "what": "a few words: what the document is",
             "refused": "only when your fetch tool could not open it: what it said, e.g. HTTP 503"}}],
  "missing": [{{"what": "the document you expected and could not find", "looked": "where you looked"}}]}}
```

`how` is "plan" when the places to look led you there, "last_year" when last year's list did, and
"search" otherwise. {group_note}{missing_note}
{round_note}

Write finds.json early, as soon as you have a few addresses, and write it again, whole, as you go:
the session can be stopped at its token cap, and only what finds.json holds is then saved.
Token cap: {cap:,}. If you get near it, write what you have to finds.json and stop.
Reply in one line: how many documents you listed and how many you could not find.
"""


def context_for(project, row: str | None, group: tuple[str, str] | None, missing: str | None,
                questions: list[str] | None = None) -> dict:
    """What the finder may see: never answers, scores or last edition's results. The questions are those
    that apply to the row (or to some row of the group) and, when `questions` is given (a trial or re-run that
    names some, or the run's scope), only those: a finder told about every question searched for all of them
    (es-imco-bipe, 21 Oct 2026: a trial of 15 of 116 questions found documents for all 116; RDR's Search row
    took Android pages for questions that do not apply to it; Freedom on the Net searched for 21 questions in
    a 7-question run)."""
    meta = project.read("project")
    units = project.read("units").get("units") or []
    where = project.read("where_to_look") if project.file("where_to_look").is_file() else {}
    where = where_without_stale(where, [q.get("id") for q in project.read("questions").get("questions") or []
                                        if isinstance(q, dict) and q.get("id")])     # never a question that is gone
    questions_scope = set(questions) if questions else None
    if row:
        unit = next((u for u in units if u.get("id") == row), None)
        if unit is None:
            raise HandbookError(f"There is no row {row} in units.json.")
        rows, values = [unit], {v for v in (unit.get("labels") or {}).values() if isinstance(v, str)} | {row}
    else:
        label, value = group
        rows = [u for u in units if (u.get("labels") or {}).get(label) == value]
        if len(rows) < 2:
            raise HandbookError(f"Fewer than two rows have {label} = {value}, so there is no group to search for.")
        values = {value}
    def wanted(entry) -> bool:
        # The plan's `for` is "all", {"rows": [ids]} or {label: [values]} (drafting.md); a plain string or
        # list is read as row ids or label values. The two object forms were dropped here until sweep round 3
        # (Freedom on the Net: Kenya Law and mevzuat.gov.tr never reached the finder).
        aim = entry.get("for")
        if isinstance(aim, dict):
            if set(aim) == {"rows"}:
                return any(u.get("id") in (aim.get("rows") or []) for u in rows)
            return bool(aim) and any(_matches(aim, u) for u in rows)
        if isinstance(aim, list):
            return bool(values & {a for a in aim if isinstance(a, str)})
        return aim == "all" or (isinstance(aim, str) and aim in values)
    plan = [p for p in where.get("plan") or [] if isinstance(p, dict) and wanted(p)]
    docs = project.documents()
    have = set()
    for u in rows:
        have |= set(project.found(u["id"]).get("documents") or [])
    # Questions only the owner's data can answer are never searched for (needs.py): in group mode, those
    # the owner holds for every row of the group.
    listed = needs.only_source_needs(project)
    owners = set.intersection(*[needs.owner_only(project, u["id"], listed) for u in rows]) if listed else set()
    seen, questions = set(), []
    for u in rows:
        for q in project.questions_for(u):
            qid = q.get("id")
            if qid in seen or qid in owners or (questions_scope is not None and qid not in questions_scope):
                continue
            seen.add(qid)
            questions.append({k: q.get(k) for k in ("id", "short", "wording", "evidence")})
    # The row's groups (a group label's value two or more rows share): a document that speaks for all of them
    # is filed for the whole group, so their own searches do not look for it again (nrdc-tissue, 21 Oct 2026).
    groups = []
    if row:
        for label in meta.get("group_labels") or []:
            value = (rows[0].get("labels") or {}).get(label)
            n = sum(1 for u in units if value is not None and (u.get("labels") or {}).get(label) == value)
            if n >= 2:
                groups.append({"label": label, "name": value, "rows": n})
    return {
        "benchmark": meta.get("name"), "cutoff": project.cutoff_for([u["id"] for u in rows]),
        "mode": "row" if row else "group",
        "groups": groups,
        "row": {"id": rows[0]["id"], "name": rows[0].get("name"), "labels": rows[0].get("labels")} if row else None,
        "group": {"label": group[0], "value": group[1], "rows": [u.get("name") for u in rows]} if group else None,
        "questions": questions,
        "places_to_look": plan,
        "last_year_sources": [s for s in where.get("last_year") or [] if not row or s.get("row") in (None, row)],
        # a page filed with no words (a script that would not render, a robot check) is not saved: still to find
        "already_saved": [{"title": docs.get(d, {}).get("title"), "url": docs.get(d, {}).get("url")} for d in sorted(have)
                          if not (docs.get(d) or {}).get("not_kept_why")],
        "what_was_missing": missing or None,
    }


def brief_for(context: dict, unit_word: str, plural: str) -> str:
    who = (f"{unit_word} you are finding for" if context["mode"] == "row"
           else f"group of {plural} you are finding for (documents that speak for every one of them, such as a "
                "group-wide policy or report)")
    note = ("This is the second search, a wider one: `what_was_missing` says what the first search and the scorer could "
            "not find. Look where the first search did not: other language versions, a parent organisation's site, "
            "document libraries, PDFs linked from reports, archived copies from before the cut-off. If a saved page is a "
            "press release, news item or landing page about a report or policy, find the document it announces. If the "
            "places to look name a register or list (a signatory list, a participant database), look this "
            f"{unit_word} up in it and list the page that shows its entry. A document named by its title, number or "
            "citation whose official home would not give it: look for the same document by that title or citation on "
            "another site that publishes such texts, and mark that copy weak." if context.get("what_was_missing") else "")
    if context["mode"] == "group":
        note += " In group mode leave `missing` empty: each row's own finder looks again."
    again = context.get("first_round")
    round_note = ("This is your second go. `first_round` in context.json says what happened to your first list: the "
                  "addresses that would not save, and why, and how many words of text each saved document holds. A "
                  "law, policy or report of a few hundred words is usually a summary, a viewer page or a landing page, "
                  "not the full text. List only better copies of what is missing or too thin (another official site, a "
                  "PDF, an archived copy from before the cut-off), and record in `missing` what you still cannot find. "
                  "For an address that would not save even for this tool, look for the same document elsewhere: first its "
                  "other addresses on the same official site (a print view, a PDF), then the same document by its title, "
                  "number or citation on another site that publishes such texts, marked weak. "
                  "Do not list again what saved well. Never give an address you have not opened with your fetch tool "
                  "in this session, unless your tool was refused there (rule 8): then say so in `refused`." if again else
                  "Never give an address you have not opened with your fetch tool in this session, unless your tool was "
                  "refused there (rule 8): then say so in `refused`.")
    group_note = ""
    if context.get("groups"):
        group_note = ("Add \"group\": the label, from `groups` in context.json, to a document that speaks for every "
                      f"{unit_word} sharing this one's value of that label (for example a company's own policy that "
                      f"covers all its products), not for this {unit_word} alone: it is then saved for each of them, "
                      "and their searches do not look for it again. ")
    return BRIEF.format(who=who, plural=plural, missing_note=note.strip(), round_note=round_note,
                        cap=min(CAP, context.get("cap") or CAP), max_save=MAX_SAVE, group_note=group_note)


def command(folder: Path, model: str = FINDER_MODEL) -> list[str]:
    settings = {"disableAllHooks": True,
                "permissions": {"deny": [f"{tool}(/{root}/**)" for root in OFF_LIMITS
                                         for tool in ("Read", "Grep", "Glob", "Edit", "Write")]}}
    prompt = (f"Find documents for one benchmark row. Your folder is {folder}. Read {folder}/brief.md and follow it "
              f"exactly: read {folder}/context.json, search and read the web, and write {folder}/finds.json. Work only "
              "inside that folder.")
    return ["claude", "-p", prompt, "--safe-mode", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--settings", json.dumps(settings), "--disable-slash-commands", "--no-session-persistence",
            "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk", "--tools", TOOLS,
            "--allowed-tools", TOOLS, "--disallowed-tools", "Bash,Edit", "--model", model]


def hb(project, *args, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(HB), *args, "--project", str(project.path)], capture_output=True,
                       text=True, timeout=timeout)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def save_finds(project, finds: dict, row: str | None, group: tuple[str, str] | None) -> dict:
    """Save each listed address with hb fetch, and record each gap with hb found (row mode only)."""
    target = ["--row", row] if row else ["--group", f"{group[0]}={group[1]}"]
    labels = {}
    if row:
        unit = next((u for u in project.read("units").get("units") or [] if u.get("id") == row), {}) or {}
        labels = {g: (unit.get("labels") or {}).get(g) for g in project.read("project").get("group_labels") or []}
    saved, failed, recorded = [], [], 0
    listed = [i for i in finds.get("save") or [] if isinstance(i, dict)]
    for item in listed[:MAX_SAVE]:
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        how = item.get("how") if item.get("how") in ("plan", "search", "last_year") else "search"
        widen = []
        if row and isinstance(item.get("group"), str) and labels.get(item["group"]) is not None:
            widen = ["--group", f"{item['group']}={labels[item['group']]}"]      # for every row of the group
        args = ["fetch", url, *target, *widen, "--how", how]
        if item.get("date"):
            args += ["--date", str(item["date"]), "--dated-by", "finder"]
        if item.get("weak"):
            args += ["--weak", str(item["weak"])]
        code, out = hb(project, *args)
        entry = {"url": url, "what": item.get("what"), "said": (out.splitlines() or [""])[-1][:300]}
        if item.get("refused"):
            entry["refused"] = str(item["refused"])[:200]   # the finder's tool could not open it; ours tried
        why = ((project.documents().get(fetch.doc_id_for_url(url)) or {}).get("not_kept_why") if code == 0 else None)
        if why:
            # Filed with no words (de-transparenzranking, 21 Oct 2026: a law portal drawn by a script): a failed
            # fetch, so the second go looks for another copy of it and the gap is recorded if it finds none.
            entry["said"] = f"Saved without the document's text. {why}"[:300]
        (saved if code == 0 and not why else failed).append(entry)
    return {"saved": saved, "failed": failed, "missing": list(finds.get("missing") or []),
            "over_limit": max(0, len(listed) - MAX_SAVE)}


def record_gaps(project, row: str | None, rounds: list[dict]) -> int:
    """What is still missing after the last round, on the row's found list (row mode only)."""
    if not row or not rounds:
        return 0
    last = rounds[-1]
    gaps = list(last["missing"]) + [{"what": f.get("what") or f["url"], "looked": f"{f['url']} ({f['said'] or 'would not save'})"}
                                    for f in last["failed"]]
    recorded = 0
    for gap in gaps:
        if gap.get("what"):
            code, _ = hb(project, "found", "--row", row, "--expect", str(gap["what"]), "--looked", str(gap.get("looked") or "the web"))
            recorded += code == 0
    return recorded


THIN_WORDS = 600


def no_words_failed(report: dict) -> dict:
    """A document the first round saved with no words at all (a scan with no text, a page that drew nothing)
    goes under would_not_save: a failed fetch, as one that would not open."""
    empty = [d for d in report["saved"] if not d.get("words")]
    return {**report, "saved": [d for d in report["saved"] if d.get("words")],
            "would_not_save": report["would_not_save"] + [{"url": d["url"], "what": d.get("title"),
                                                            "why": "It saved with no words, so it cannot be read."}
                                                           for d in empty]}


def words_in(project, doc: str) -> int:
    from .folder import read_json
    pages = (read_json(project.doc_dir(doc) / "pages.json", {}) or {}).get("pages") or []
    return sum(len(str(pg.get("text") or "").split()) for pg in pages if isinstance(pg, dict))


def first_round_report(project, row: str | None, group, before: set[str], got: dict) -> dict:
    """What the finder's first list came to, for its second go: failures, and the words each new document holds."""
    hb(project, "prepare", timeout=1800)
    docs = project.documents()
    rows = [row] if row else [u["id"] for u in project.read("units").get("units") or []
                              if (u.get("labels") or {}).get(group[0]) == group[1]]
    now = set()
    for r in rows:
        now |= set(project.found(r).get("documents") or [])
    fresh = sorted(now - before)
    from .kit import late_documents
    late = {}
    for r in rows:
        late.update({d: when for d, when in late_documents(project, r).items() if d in fresh})
    cutoff = project.read("project").get("cutoff")
    return no_words_failed({
        "would_not_save": [{"url": f["url"], "what": f.get("what"), "why": f["said"]} for f in got["failed"]]
                          # dated after the cut-off: no use, so the second go looks for the version current then
                          + [{"url": docs.get(d, {}).get("url"), "what": docs.get(d, {}).get("title"),
                              "why": f"It is dated {when}, after the cut-off ({cutoff}), so it cannot be used. Find "
                                     "the version current at the cut-off, or an archived copy from before it."}
                             for d, when in late.items()],
        "saved": [{"title": docs.get(d, {}).get("title"), "url": docs.get(d, {}).get("url"), "words": words_in(project, d)}
                  for d in fresh if not (docs.get(d) or {}).get("not_kept_why") and d not in late]})


# A row gets one wider second search after scoring (serve.score_run, as the run skill's) when this many of its
# answers say a document is missing, or when more than half of them found no evidence.
WIDER_DOUBTS = 3


def wider_search(project, row: str) -> dict | None:
    """Whether `row` gets its one wider second search after scoring: None, or {"questions": the answers to score
    again if it saves anything, "missing": what to tell the finder}. Never for a row that had its second search,
    whose documents the owner supplied, or for questions only the owner's data answers (needs.py)."""
    found = project.found(row)
    if found.get("second_search") is not None:
        return None
    docs = project.documents()
    mine = [d for d in found.get("documents") or [] if isinstance(d, str)]
    if mine and all((docs.get(d) or {}).get("provided_by_owner") for d in mine):
        return None
    owners = needs.owner_only(project, row)
    shorts = {q.get("id"): q.get("short") or q.get("id") for q in project.read("questions").get("questions") or []}
    cells = {q: c for q, c in (project.answers(row).get("cells") or {}).items()
             if isinstance(c, dict) and c.get("state") in ("answered", "no_evidence") and q not in owners}
    doubts = {q: c["unsure"] for q, c in cells.items() if isinstance(c.get("unsure"), dict)
              and c["unsure"].get("why") == "document" and not c["unsure"].get("handled")}
    empty = [q for q, c in cells.items() if c["state"] == "no_evidence"]
    if not cells or (len(doubts) < WIDER_DOUBTS and len(empty) * 2 <= len(cells)):
        return None
    lines = []
    if len(empty) * 2 > len(cells):
        lines.append(f"{len(empty)} of {len(cells)} answers found no evidence in the saved documents, on "
                     + "; ".join(str(shorts.get(q, q)) for q in empty[:30]) + ".")
    for q, u in doubts.items():
        lines.append(f"Question {q} ({shorts.get(q, q)}): the scorer needed "
                     + (f"{u['missing']}. " if u.get("missing") else "a document that is not saved. ")
                     + str(u.get("note") or "")[:300])
    for e in found.get("expected_not_found") or []:
        if isinstance(e, dict) and e.get("what") and e.get("by") != "scorer":
            lines.append(f"Expected but not found: {e['what']} (looked: {str(e.get('where_looked') or '')[:200]}).")
    return {"questions": [q for q in cells if q in doubts or q in empty], "missing": "\n".join(lines)[:6000]}


def run_finder(project, row: str | None = None, group: tuple[str, str] | None = None, missing: str | None = None,
               timeout_s: int = TIMEOUT_S, rounds: int = 2, questions: list[str] | None = None,
               budget: int | None = None) -> dict:
    """One finder session, and a second when the first list did not all save or saved a thin copy:
    the second is told what happened, as a finder with a shell would see for itself (Antigua and
    Barbuda, 19 Oct 2026: a dead address and a 417-word viewer page of the Act left 59 of 61 answers
    with no evidence). `questions` narrows what the finder looks for (a run's scope). Both rounds share
    `budget` tokens (BUDGET): a session over it is stopped and what its finds.json holds is saved, and a
    second round runs only with at least MIN_ROUND left."""
    budget = BUDGET if budget is None else budget
    context = dict(context_for(project, row, group, missing, questions), cap=budget)
    if not context["questions"]:
        # Every question here rests on data only the owner holds (needs.py): nothing to search the web for.
        return {"saved": 0, "failed": 0, "not_found": 0, "rounds": 0, "tokens": 0, "ok": True, "broke": False,
                "reply": "Nothing to look for: every question rests on data the owner supplies.", "skipped": True}
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so no finder can be started.")
    meta = project.read("project")
    rows = [row] if row else None
    before = set()
    for r in rows or [u["id"] for u in project.read("units").get("units") or [] if (u.get("labels") or {}).get(group[0]) == group[1]]:
        before |= set(project.found(r).get("documents") or [])
    done, tokens, replies, results = [], 0, [], []
    rounds = max(1, min(rounds, 2))
    stopped = None
    for n in range(1, rounds + 1):
        result, finds = _session(context, meta, timeout_s, budget=budget - tokens)
        usage = result.get("usage") or {}
        tokens += sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                        "cache_read_input_tokens", "output_tokens"))
        stopped = result.get("stopped") or stopped
        replies.append(str(result.get("result") or "").strip())
        results.append(dict(result, finds=finds))
        got = save_finds(project, finds, row, group)
        done.append(got)
        if n >= rounds or budget - tokens < MIN_ROUND:
            break
        report = first_round_report(project, row, group, before, got)
        if not report["would_not_save"] and all(d["words"] >= THIN_WORDS for d in report["saved"]):
            break
        context = dict(context_for(project, row, group, missing, questions), first_round=report, cap=budget - tokens)
    not_found = record_gaps(project, row, done)
    if row and not missing:
        # What this search covered: a later run looks again for the questions it did not (todo.to_find).
        found = project.found(row)
        if questions:
            order = [q.get("id") for q in project.read("questions").get("questions") or []]
            covered = set(found.get("searched_for") or []) | set(questions)
            found["searched_for"] = [q for q in order if q in covered]
        else:
            found.pop("searched_for", None)
        found["row"] = row
        project.write_found(row, found)
    name = (f"find-{row}" if row else f"find-{group[0]}-{group[1]}") + ("-second.json" if missing else ".json")
    receipt = project.path.joinpath(*RECEIPTS, name)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    write_json(receipt, {"rounds": results, "saved": done})
    saved = sum(len(r["saved"]) for r in done)
    return {"saved": saved, "failed": len(done[-1]["failed"]), "not_found": not_found, "rounds": len(done),
            "tokens": tokens, "ok": any(r.get("finds") for r in results), "reply": replies[-1], "budget": budget,
            "stopped": stopped, "over_limit": sum(r.get("over_limit") or 0 for r in done),
            # every session ended in an error (stopped, timed out, signed out) with nothing listed: no search happened
            "broke": all(r.get("is_error") for r in results) and not any(r.get("finds") for r in results)}


def _session(context: dict, meta: dict, timeout_s: int, budget: int | None = None) -> tuple[dict, dict]:
    """One finder session, watched as it runs (as cmd_score watches a scorer) and stopped once it has
    processed more than `budget` tokens; finds.json as it stands is read either way."""
    from .cmd_score import watch
    folder = Path(tempfile.mkdtemp(prefix="hb-find-"))
    write_json(folder / "context.json", {k: v for k, v in context.items() if k != "cap"})
    (folder / "brief.md").write_text(brief_for(context, meta.get("unit_word") or "row", meta.get("unit_word_plural") or "rows"),
                                     encoding="utf-8")
    proc = subprocess.Popen(command(folder), cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    fired: list[bool] = []

    def time_up():
        fired.append(True)
        proc.terminate()
    timer = threading.Timer(timeout_s, time_up)
    timer.start()
    err: list[str] = []
    drain = threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)
    drain.start()
    try:
        seen = watch(proc.stdout, budget, proc.terminate)
        proc.wait()
    finally:
        timer.cancel()
    result = seen["result"]
    if result is None:
        if fired:
            why = f"The finder ran for more than {timeout_s // 60} minutes and was stopped."
        elif seen["stopped"]:
            why = f"The finder was stopped at its token budget ({budget:,})."
        else:
            tail = "".join(err).strip().splitlines()
            why = "The finder ended without a result" + (f": {tail[-1][:200]}" if tail else ".")
        result = {"is_error": not seen["stopped"], "result": why, "usage": seen["usage"]}
    if seen["stopped"]:
        result["stopped"] = "budget"
    try:
        finds = json.loads((folder / "finds.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        finds = {}
    shutil.rmtree(folder, ignore_errors=True)
    return result, finds


def _group(text: str) -> tuple[str, str]:
    label, sep, value = text.partition("=")
    if not sep or not label or not value:
        raise ValueError("give the group as LABEL=VALUE")
    return label, value


def main(argv) -> int:
    parser = command_parser("find", "Find and save one row's (or group's) documents in a clean headless session.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--row", default=None)
    who.add_argument("--group", default=None, metavar="LABEL=VALUE", type=_group)
    parser.add_argument("--missing", default=None, help="second search: what the first search could not find")
    parser.add_argument("--questions", default=None, help="look only for what these questions need (ids, comma separated)")
    parser.add_argument("--budget", type=int, default=None,
                        help=f"stop finding once this many tokens are processed, over both rounds (default {BUDGET:,})")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    qs = [q.strip() for q in (args.questions or "").split(",") if q.strip()] or None
    got = run_finder(project, args.row, args.group, args.missing, questions=qs, budget=args.budget)
    if args.json:
        print(json.dumps(got))
    else:
        print(f"Saved {got['saved']}, could not save {got['failed']}, not found {got['not_found']}, in {got['rounds']} "
              f"round{'s' if got['rounds'] != 1 else ''}. {got['tokens']:,} tokens processed"
              + (f", stopped at its budget of {got['budget']:,}" if got.get("stopped") else "") + "."
              + (f" {got['over_limit']} more addresses were listed than the {MAX_SAVE} a round saves." if got.get("over_limit") else ""))
    return 0 if got["ok"] else 1

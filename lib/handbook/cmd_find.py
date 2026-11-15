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
itself tries the archive and a browser view of a script-built page; what still fails is recorded as not
found, so the second search, the Grid's "missing" list and the owner can pick it up.

The finder's own web tool is not the tool's fetch: it sends its own agent from this Mac, which some robot
filters refuse (abnamro.com answered it 503 on every page, 20 Oct 2026, and a browser 200), it is not
allowed on some sites (web.archive.org) and it stops at a smaller file size. So an address the finder could
not open for one of those reasons is listed with `refused`, and `hb fetch` tries it as the tool (which asks as
itself) and then the web archive's copy.
"""
from __future__ import annotations
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from collections import Counter
from pathlib import Path

from .claude_cli import FINDER_MODEL
from .shapes import where_without_stale
from .cli import command_parser, open_project
from . import fetch, needs, system
from .cmd_score import OFF_LIMITS, TOOL_DIR, search_usage, search_words
from .folder import HandbookError, _matches, write_json
from .hosts import on_results_site, site_parts
from .oneread import words as _words

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
   mark it weak. When you find a document only on another site (a legal database abroad, a regional
   body, a news site), take its exact title, number and date from that copy and search the
   organisation's own sites for the same document (its domain, its gazette, its legal database)
   before you settle for the copy. List the official copy when you find it, and the other only if
   the official one would not open.
3. Find the version that was current on the cut-off date. A document dated after the cut-off is no
   use. If the live page has changed since, give an archived copy from before the cut-off
   (web.archive.org).
4. A site in several parts is several documents: list each part that matters.
5. Do not stop at the first hit. Stop when every question has a place it would be answered from,
   listed or recorded as not found.
6. Never list the benchmark's own published results, scorecards, rankings or reports about these
   {plural}, or news and write-ups of them. They would reach the scorer.{sites_rule}
7. Prefer an address that is the document itself (a PDF, or the page with the full text) to one
   that needs a script or a login to show it. When a page you open links to a file a question needs
   (a spreadsheet, a list, a PDF), list the file's own address too, from an archived copy as well.
8. Your fetch tool is sometimes refused where a browser is not: an HTTP error such as 403, 429 or
   503 from the organisation's site, a site your tool is not allowed to open (web.archive.org), or
   a file too large for it. Do not give up on that site, and do not swap in a copy elsewhere for
   it: keep searching for its documents' addresses (search results, links on pages you could open,
   other language versions) and list each one with `refused` saying what your tool said. This tool
   then fetches it itself and, if the site refuses it too, takes the web archive's copy from before
   the cut-off. Never list an address you have not seen in a search result or on a page.
9. List only what the questions need, about one or two documents a question, and never more than
   {max_save} addresses: never a whole website's pages, and nothing for a question not listed in
   context.json.{compare_rule}

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
        "never_open": [site for site, _, _ in site_parts(meta.get("results_sites"))],
        "compare": bool(meta.get("finder_compare")),
    }


# The finder's rule when the project sets "finder_compare": gather candidates for each main document and keep the
# best, rather than the first good-enough one. An open-data benchmark, 30 Oct 2026: the same settings found different documents on
# different days (Kenya land use 4/19 then 9/19, as a different atlas turned up first), so a test of whether
# comparing before choosing makes finding steadier and better.
COMPARE_RULE = """
   Do not keep the first document that will do. For each main document the questions rest on (the
   law, the policy, the portal or dataset, the programme's own pages), gather up to five candidate
   sources first: the official site, the gazette or legal database, the data portal, an archived
   copy, other publishers. Open each one, then list the best three at most: the organisation's own
   copy first, then the fullest text, then the version current at the cut-off. In `what`, say in a
   few words why each one beat the others."""


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
                  "Do not list again what saved well. "
                  + ("A saved document with `weak` under it is a copy from another site, not the organisation's own: "
                     "take its title, number and date from it and look for the same document on the organisation's own "
                     "sites (its domain, gazette or legal database, archived copies of those from before the cut-off). "
                     "List the official copy when you find it; if you cannot, record in `missing` where you looked. "
                     if any(d.get("weak") for d in (again.get("saved") or [])) else "")
                  + ("A saved page with `links` under it reads as a list or a portal (a catalogue of courses, a "
                     "register's front page, a publications list): those are the pages and files it links to, files "
                     "first. The questions are usually answered by the items, not by the list: list the ones a question "
                     "needs (a course's own page, a record or notice, a document), as many as the questions need within "
                     "the limit, and nothing from the list for its own sake. This tool fetches them itself, so list them "
                     "even where your fetch tool is refused on that site. "
                     + ("Nothing on your first list failed or saved thin, so this go is only to list from `links`: "
                        "search nothing, open one or two of them at most to see what they are, and write finds.json. "
                        if context.get("links_only") else "")
                     if any(d.get("links") for d in (again.get("saved") or [])) else "")
                  + "Never give an address you have not opened with your fetch tool "
                  "in this session, unless your tool was refused there (rule 8) or it is one of those `links`: then say "
                  "so in `refused`." if again else
                  "Never give an address you have not opened with your fetch tool in this session, unless your tool was "
                  "refused there (rule 8): then say so in `refused`.")
    group_note = ""
    if context.get("groups"):
        group_note = ("Add \"group\": the label, from `groups` in context.json, to a document that speaks for every "
                      f"{unit_word} sharing this one's value of that label (for example a company's own policy that "
                      f"covers all its products), not for this {unit_word} alone: it is then saved for each of them, "
                      "and their searches do not look for it again. ")
    sites = context.get("never_open") or []
    sites_rule = ("\n   Never open, search inside or list anything on these sites, or archived copies of their pages: "
                  + ", ".join(sites) + ". They hold the benchmark's results, or the owner has ruled them out. Your "
                  "fetch tool refuses them; pass them as blocked_domains on every web search." if sites else "")
    return BRIEF.format(who=who, plural=plural, missing_note=note.strip(), round_note=round_note,
                        cap=min(CAP, context.get("cap") or CAP), max_save=MAX_SAVE, group_note=group_note,
                        sites_rule=sites_rule, compare_rule=COMPARE_RULE if context.get("compare") else "")


def deny_rules(sites) -> list[str]:
    """The fetch rules that stop the finder opening a results site at all, not only saving from it: a
    whole host and every subdomain (a rule for the host alone let a results site's subdomain
    through, 20 Oct 2026). A site that names a path leaves the rest of its host open, so it is left to
    the brief and to the check on what the finder opened. An archived copy cannot be refused this way
    without refusing the whole archive; that check catches it."""
    rules = []
    for _, host, path in site_parts(sites):
        if not path:
            rules += [f"WebFetch(domain:{host})", f"WebFetch(domain:*.{host})"]
    return rules


# Where a finder may never write on a Mac or Linux, beside the owner's folders (OFF_LIMITS): the places a stray
# file could run or change the computer. Claude Code's file-write rules are Edit rules (they cover Write too; a
# Write(...) rule is accepted and matches nothing). An allow rule for the finder's folder alone did not stop a
# Write elsewhere on Claude Code 2.1.301 (15 Nov 2026), so these are refused by name; on 2.1.314 and later
# blockWritesOutsideWorkingDirectories also keeps every Write inside the finder's own folder.
WRITE_OFF_LIMITS = ("/Applications", "/Library", "/System", "/opt", "/usr", "/bin", "/sbin", "/etc", "/private/etc",
                    "/private/var/root", "/cores")


def write_rules(folder: Path) -> list[str]:
    """Writing allowed in the finder's own folder, as absolute Edit rules: the folder as given and as it resolves
    (/tmp is /private/tmp on a Mac). A finds.json written to the wrong path still reaches the run from the stream
    (read_finds)."""
    paths = {system.rule_path(folder), system.rule_path(Path(folder).resolve())}
    return [f"Edit(/{p}/**)" for p in sorted(paths)]


def command(folder: Path, model: str = FINDER_MODEL, sites=()) -> list[str]:
    """The finder's session: web tools, Read, and Write for its own folder. The file tools are refused under the
    owner's folders (OFF_LIMITS) and writing under the computer's own (WRITE_OFF_LIMITS); on Windows, which has
    neither, everything beside the finder's own path is refused (system.off_limits_windows), as for a scorer."""
    if system.sandboxed():
        deny = ([f"{tool}(/{root}/**)" for root in OFF_LIMITS for tool in ("Read", "Grep", "Glob", "Edit", "Write")]
                + [f"Edit(/{root}/**)" for root in WRITE_OFF_LIMITS])
    else:
        deny = [f"{tool}(/{root}/**)" for root in system.off_limits_windows(folder, also=[TOOL_DIR])
                for tool in ("Read", "Grep", "Glob", "Edit", "Write")]
    settings = {"disableAllHooks": True,
                "permissions": {"deny": deny + deny_rules(sites), "blockWritesOutsideWorkingDirectories": True}}
    prompt = (f"Find documents for one benchmark row. Your folder is {folder}. Read {folder}/brief.md and follow it "
              f"exactly: read {folder}/context.json, search and read the web, and write {folder}/finds.json. Work only "
              "inside that folder.")
    allowed = ",".join(["Read", "Write", "WebSearch", "WebFetch", *write_rules(folder)])
    return [system.claude(), "-p", prompt, "--safe-mode", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--settings", json.dumps(settings), "--disable-slash-commands", "--no-session-persistence",
            "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk", "--tools", TOOLS,
            "--allowed-tools", allowed, "--disallowed-tools", "Bash,Edit", "--model", model]


def read_stream(text: str) -> tuple[dict | None, dict]:
    """The run's result event, and its trail: each address the finder opened, each it was refused, and
    each search, read from the stream of events (the plain json output keeps only the result)."""
    result, calls = None, []
    for line in (text or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            result = event
        elif event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in ("WebFetch", "WebSearch"):
                    calls.append(block)
    refused_ids = {d.get("tool_use_id") for d in (result or {}).get("permission_denials") or [] if isinstance(d, dict)}
    trail = {"opened": [], "refused": [], "searched": []}
    for call in calls:
        given = call.get("input") or {}
        if call.get("name") == "WebSearch":
            trail["searched"].append(str(given.get("query") or ""))
        else:
            trail["refused" if call.get("id") in refused_ids else "opened"].append(str(given.get("url") or ""))
    return result, trail


def hb(project, *args, timeout: int = 300) -> tuple[int, str]:
    """Run one hb command for the project: (exit code, what it printed). A command that runs past `timeout` or cannot
    start is a failure like any other, never an exception (unseen-es, 5 Nov 2026: one fetch that timed out ended
    Peru's whole find with no receipt)."""
    try:
        p = subprocess.run([sys.executable, str(HB), *args, "--project", str(project.path)], capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"hb {args[0] if args else ''} did not finish within {timeout // 60} minutes and was stopped."
    except OSError as e:
        return 126, f"hb {args[0] if args else ''} could not run: {e}"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def save_finds(project, finds: dict, row: str | None, group: tuple[str, str] | None, offered=()) -> dict:
    """Save each listed address with hb fetch, and record each gap with hb found (row mode only). An address among
    `offered` (the links a saved list page offered the second go) is fetched as a list item: a page of short
    lines is kept, not refused as menus."""
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
        args = ["fetch", url, *target, *widen, "--how", how] + (["--from-list"] if url in offered else [])
        if item.get("date"):
            args += ["--date", str(item["date"]), "--dated-by", "finder"]
        if item.get("weak"):
            args += ["--weak", str(item["weak"])]
        try:
            code, out = hb(project, *args)
        except Exception as e:       # noqa: BLE001  one address that breaks never loses the rest of the list
            code, out = 1, f"The fetch failed: {type(e).__name__}: {e}"
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


# A saved page with no more words than LIST_WORDS and at least LIST_LINKS links reads as a list or a portal (a
# catalogue of courses, a register's front page, a publications list): the questions are usually answered by
# the items it links to, not by the list. An open-data benchmark, 29 Oct 2026: Uruguay's finder saved AGESIC's course catalogue
# (241 words, 38 links) and never a course's own page, so four training answers found nothing; Senegal's saved
# the procurement portal's front page (900 words, 179 links to tenders, awards and notices) and nine answers on
# record fields found nothing. The finder's own fetch tool is refused on some of these sites (gub.uy), so the
# second go is shown the links from the saved copy and this tool fetches what it lists.
LIST_WORDS = 1500
LIST_LINKS = 10
MAX_LINKS = 40
SHARED_ON = 3                # an address linked from this many saved pages is a site's menu or footer, not an item
LINKS_ROUND = 30_000         # budget a second go needs when it is only to list from `links` (no searching)
FILE_ENDS = (".pdf", ".xlsx", ".xls", ".csv", ".docx", ".doc", ".ods", ".json", ".zip")
_LINK = re.compile(r"""(?is)<a\b[^>]*?href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""")


# An item's own page ranks before other links (an open-data benchmark, 5 Nov 2026): a course, notice or record page carries the date
# that dates it, which a catalogue or list rarely does. On AGESIC's catalogue the four course pages came after the
# terms of use, the search page and the category menus; on Ghana's newspaper page 52 links of site furniture came
# before anything else. An item is known by its address (a course or record id, a news or course path with a slug
# after it) or by a date in the link's words. Share buttons, terms, privacy, sign-in and the like go last.
_ITEM_URL = re.compile(r"(?i)(course/view\.php\?id=\d|[?&](idavis|course|curso|cours|nid)=\d|"
                       r"/(cursos?|courses?|cours|formations?|formacion|capacitacion(es)?|trainings?|programm?es?|"
                       r"noticias?|news|actualites?|articles?|eventos?|events?|agenda|calendario-actividades|avis|"
                       r"node)/(?:[^?#]*/)?[^/?#]*(-[^/?#]*-|\d)[^/?#]*$)")
_DATED = re.compile(r"(?i)\b(\d{1,2}[/.-]\d{1,2}[/.-](19|20)\d{2}|(19|20)\d{2}[/.-]\d{1,2}[/.-]\d{1,2}|"
                    r"\d{1,2}(st|nd|rd|th)? (de )?[a-zé]{3,10}\.? (de |del )?(19|20)\d{2}|"
                    r"[a-zé]{3,10}\.? \d{1,2},? (19|20)\d{2})\b")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_NEXT_PAGE = re.compile(r"(?i)^(p[aá]g(ina|e)?\.? ?\d+|siguiente|next|suivant|›|»|>)$")
_BOILERPLATE = re.compile(r"(?i)^(facebook|twitter|x|linkedin|whatsapp|instagram|youtube|telegram|email|"
                          r"imprimir|print|share|compartir|partager)$|t[ée]rminos|terms (of|and)|conditions|"
                          r"privacidad|privacy|confidentialit|cookies?|iniciar sesi|sign in|log ?in|acceder$|"
                          r"connexion|accesibilidad|accessibility|accessibilit|reportar|enviar reporte|report an? |"
                          r"contact|mapa del sitio|site ?map|plan du site")
_BOILERPLATE_URL = re.compile(r"(?i)(sharer|share-offsite|intent/(tweet|user)|/(auth|login|signin)\b|"
                              r"/(tag|author|category)/|/user/(view|profile)|ayudanos-a-mejorar)")


def page_links(project, doc: str) -> list[dict]:
    """[{"text", "url"}] of every link on a saved page, from its original HTML, or from meta.json's `links` when
    the HTML was not kept (the page as a browser drew it, or its print view), resolved against the page's
    address and deduplicated, in page order. [] for a document with neither."""
    folder = project.doc_dir(doc)
    meta = project.documents().get(doc) or {}
    raw = next(iter(sorted(folder.glob("original.htm*"))), None)
    if raw is not None:
        pairs = [(html.unescape(h).strip(), re.sub(r"<[^>]+>", " ", html.unescape(t)))
                 for h, t in _LINK.findall(fetch.decode_html(raw.read_bytes()))]
    else:
        pairs = [(str(u), str(t)) for u, t in meta.get("links") or [] if isinstance(u, str)]
    out, seen = [], set()
    for href, text in pairs:
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        url = urllib.parse.urljoin(meta.get("url") or "", href)
        text = " ".join(text.split())
        if not url.startswith(("http://", "https://")) or len(text) < 3 or url in seen:
            continue
        seen.add(url)
        out.append({"text": text[:120], "url": url})
    return out


def shared_links(project) -> set[str]:
    """Addresses linked from SHARED_ON or more of the project's saved pages: a site's menus and footer (gub.uy,
    29 Oct 2026: 60 of a news page's 76 links), which no list of items is made of."""
    count: Counter = Counter()
    for doc in project.documents():
        count.update({l["url"] for l in page_links(project, doc)})
    return {u for u, n in count.items() if n >= SHARED_ON}


def listing_links(project, doc: str, questions: list[dict], never=(), shared: set | None = None) -> dict | None:
    """The links of a saved page that reads as a list or a portal (see LIST_WORDS), leaving out the site's own
    menus (`shared_links`), the page itself, a site's front page, results sites and pages already saved: files
    first, then an item's own page (`_ITEM_URL`, or a date in the link's words), then links whose text shares a
    word with the questions or the page's title, names a year or is the list's next page, then links deeper in the page's own section, then the rest in
    page order, with share buttons, terms and sign-in last (`_BOILERPLATE`), at most MAX_LINKS. None for a page of prose, or one with
    too few links left to be a list."""
    if words_in(project, doc) > LIST_WORDS:
        return None
    docs = project.documents()
    own = str((docs.get(doc) or {}).get("url") or "")
    saved = {d.get("url") for d in docs.values() if isinstance(d, dict)}
    shared = shared_links(project) if shared is None else shared
    links = []
    for l in page_links(project, doc):
        path = urllib.parse.urlsplit(l["url"]).path
        if (l["url"] in saved or l["url"] in shared or on_results_site(l["url"], never)
                or l["url"].rstrip("/") == own.rstrip("/") or path in ("", "/")):
            continue
        links.append(l)
    files = [l for l in links if urllib.parse.urlsplit(l["url"]).path.lower().endswith(FILE_ENDS)]
    if len(links) < LIST_LINKS:
        # no list, but a short page linking to a file (a course sheet, a notice's PDF) offers the file
        return {"links": files[:MAX_LINKS], "more": 0} if files else None
    asked = set(_words(str((docs.get(doc) or {}).get("title") or "")))
    for q in questions or []:
        asked.update(_words(" ".join(str(q.get(k) or "") for k in ("short", "wording"))))
    section = urllib.parse.urlsplit(own).path.rsplit("/", 1)[0] + "/"

    def rank(link):
        parts = urllib.parse.urlsplit(link["url"])
        path = parts.path
        if _BOILERPLATE.search(link["text"]) or _BOILERPLATE_URL.search(link["url"]):
            return 5                                   # share buttons, terms, sign-in: never an item
        if path.lower().endswith(FILE_ENDS):
            return 0
        if _ITEM_URL.search(path + ("?" + parts.query if parts.query else "")) or _DATED.search(link["text"]):
            return 1                                   # one item's own page: a course, a notice, a dated report
        if (asked & set(_words(link["text"])) or _YEAR.search(link["text"])
                or _NEXT_PAGE.search(link["text"].strip())):
            return 2                                   # words of the questions, a year, the list's next page
        return 3 if len(section) > 1 and path.startswith(section) and len(path) > len(section) else 4
    ranked = sorted(links, key=rank)       # a stable sort: page order within each rank
    return {"links": ranked[:MAX_LINKS], "more": max(0, len(ranked) - MAX_LINKS)}


def first_round_report(project, row: str | None, group, before: set[str], got: dict, questions: list | None = None,
                       never=()) -> dict:
    """What the finder's first list came to, for its second go: failures, the words each new document holds, and
    for a page that reads as a list or a portal, the links it offers (`listing_links`)."""
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
    shared = shared_links(project)
    return no_words_failed({
        "would_not_save": [{"url": f["url"], "what": f.get("what"), "why": f["said"]} for f in got["failed"]]
                          # dated after the cut-off: no use, so the second go looks for the version current then
                          + [{"url": docs.get(d, {}).get("url"), "what": docs.get(d, {}).get("title"),
                              "why": f"It is dated {when}, after the cut-off ({cutoff}), so it cannot be used. Find "
                                     "the version current at the cut-off, or an archived copy from before it."}
                             for d, when in late.items()],
        "saved": [{"title": docs.get(d, {}).get("title"), "url": docs.get(d, {}).get("url"), "words": words_in(project, d),
                   **({"weak": docs[d]["weak"].get("why")} if isinstance((docs.get(d) or {}).get("weak"), dict) else {}),
                   **(listing_links(project, d, questions or [], never, shared) or {})}
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
    # Evidence set aside under a rule (undated, outside the period, not official) in two or more answers: one
    # document a whole branch rests on, so the wider search looks for a dated or official copy of it.
    aside: dict = {}
    for q, c in cells.items():
        u = c.get("unsure")
        if isinstance(u, dict) and u.get("why") == "set_aside" and not u.get("handled") and isinstance(u.get("doc"), str):
            aside.setdefault(u["doc"], []).append(q)
    aside = {d: qs for d, qs in aside.items() if len(qs) >= 2}
    # Text that went from a saved page at a re-check, which the owner asked to have looked for (recheck.look).
    moved = [str(q) for e in found.get("expected_not_found") or [] if isinstance(e, dict) and e.get("by") == "recheck"
             for q in e.get("questions") or [] if q in cells]
    if not cells or (len(doubts) < WIDER_DOUBTS and len(empty) * 2 <= len(cells) and not moved and not aside):
        return None
    lines = []
    if len(empty) * 2 > len(cells):
        lines.append(f"{len(empty)} of {len(cells)} answers found no evidence in the saved documents, on "
                     + "; ".join(str(shorts.get(q, q)) for q in empty[:30]) + ".")
    for q, u in doubts.items():
        lines.append(f"Question {q} ({shorts.get(q, q)}): the scorer needed "
                     + (f"{u['missing']}. " if u.get("missing") else "a document that is not saved. ")
                     + str(u.get("note") or "")[:300])
    for d, qs in aside.items():
        meta = docs.get(d) or {}
        note = next((str(cells[q]["unsure"].get("note") or "") for q in qs), "")
        lines.append(f"{len(qs)} answers set aside {meta.get('title') or d} ({meta.get('url') or 'saved copy'}): {note[:300]} "
                     "Find a copy of the same evidence that meets the rule: a dated version from within the period, an "
                     "archived copy from then, or the official site's own copy.")
    for e in found.get("expected_not_found") or []:
        if isinstance(e, dict) and e.get("what") and e.get("by") != "scorer":
            lines.append(f"Expected but not found: {e['what']} (looked: {str(e.get('where_looked') or '')[:200]}).")
    asided = {q for qs in aside.values() for q in qs}
    return {"questions": [q for q in cells if q in doubts or q in empty or q in moved or q in asided],
            "missing": "\n".join(lines)[:6000]}


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
    search_tokens = searches = 0        # the search tool's own model, outside `tokens` and the budget (search_usage)
    rounds = max(1, min(rounds, 2))
    stopped = None
    offered: set = set()
    retried, lost = False, 0       # lost: tokens of a session that ended with no list, run again (below)
    n = 0
    while n < rounds:
        n += 1
        result, finds, trail = _session(context, meta, timeout_s, budget=budget + lost - tokens)
        usage = result.get("usage") or {}
        spent = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                     "cache_read_input_tokens", "output_tokens"))
        tokens += spent
        more, k = search_usage(result)
        search_tokens, searches = search_tokens + more, searches + k
        stopped = result.get("stopped") or stopped
        if result.get("kept"):
            result = dict(result, kept=file_kept(project, result["kept"], row, group, n))
        if not finds and not retried and not result.get("stopped") and not result.get("timed_out"):
            # A session that ended with no list at all (no finds.json found anywhere): its folder and stream are kept,
            # and it runs once more, unchanged, with the budget it had (the same command worked minutes later every
            # time on 4-5 Nov 2026). Its tokens still count in the totals.
            retried = True
            results.append(dict(result, finds=finds, trail=trail, retried=True))
            replies.append(str(result.get("result") or "").strip())
            lost += spent
            n -= 1
            continue
        replies.append(str(result.get("result") or "").strip())
        results.append(dict(result, finds=finds, trail=trail))
        got = save_finds(project, finds, row, group, offered)
        done.append(got)
        if n >= rounds:
            break
        report = first_round_report(project, row, group, before, got, context["questions"], context.get("never_open") or ())
        # a page with few words that offers links is a list, not a thin copy of a document
        short = bool(report["would_not_save"]) or any(d["words"] < THIN_WORDS and not d.get("links") for d in report["saved"])
        lists = any(d.get("links") for d in report["saved"])
        # a copy from another site: the second go looks for the official one (Senegal's law, 22 Oct 2026, came only
        # from SenLII, AFAPDP and WIPO while the benchmark's researcher used cdp.sn)
        weak = any(d.get("weak") for d in report["saved"])
        offered = {l["url"] for d in report["saved"] for l in d.get("links") or []}
        # A second go for lists alone searches nothing, so it needs less of the budget than a wider one.
        if not (short or lists or weak) or budget + lost - tokens < (MIN_ROUND if short or weak else LINKS_ROUND):
            break
        context = dict(context_for(project, row, group, missing, questions), first_round=report, cap=budget + lost - tokens,
                       links_only=lists and not short and not weak)
    not_found = record_gaps(project, row, done)
    probed = None
    if row and meta.get("probe"):
        # try the row's data sources, not only read about them (cmd_probe.py); never stops the find
        from .cmd_probe import probe_row
        try:
            probed = probe_row(project, row, access=bool(meta.get("probe_access")))
        except (OSError, HandbookError) as exc:
            probed = f"failed: {exc}"[:200]
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
    # What the finder read on a results site despite the rules (an archived copy, or a page on a site
    # that names a path): its finds may echo the benchmark's own evidence, so the run says so.
    sites = meta.get("results_sites")
    read_blocked = sorted({u for r in results for u in (r.get("trail") or {}).get("opened") or []
                           if on_results_site(u, sites)})
    refused = sum(len((r.get("trail") or {}).get("refused") or []) for r in results)
    write_json(receipt, {"rounds": results, "saved": done, "read_blocked": read_blocked})
    saved = sum(len(r["saved"]) for r in done)
    return {"saved": saved, "failed": len(done[-1]["failed"]), "not_found": not_found, "rounds": len(done),
            "tokens": tokens, "search_tokens": search_tokens, "searches": searches,
            "ok": any(r.get("finds") for r in results), "reply": replies[-1], "budget": budget,
            "retried": [r.get("kept") or r.get("result") for r in results if r.get("retried")],
            "kept": [r["kept"] for r in results if r.get("kept")],
            "stopped": stopped, "over_limit": sum(r.get("over_limit") or 0 for r in done),
            "read_blocked": read_blocked, "refused": refused, "probe": probed,
            # every session ended in an error (stopped, timed out, signed out) with nothing listed: no search happened
            "broke": all(r.get("is_error") for r in results) and not any(r.get("finds") for r in results)}


def _session(context: dict, meta: dict, timeout_s: int, budget: int | None = None) -> tuple[dict, dict, dict]:
    """One finder session, watched as it runs (as cmd_score watches a scorer) and stopped once it has
    processed more than `budget` tokens; finds.json as it stands is read either way."""
    from .cmd_score import watch
    folder = Path(tempfile.mkdtemp(prefix="hb-find-", dir=finder_root()))
    write_json(folder / "context.json", {k: v for k, v in context.items() if k != "cap"})
    (folder / "brief.md").write_text(brief_for(context, meta.get("unit_word") or "row", meta.get("unit_word_plural") or "rows"),
                                     encoding="utf-8")
    proc = subprocess.Popen(command(folder, sites=meta.get("results_sites")), cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
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
        lines: list[str] = []   # kept for the trail of what the finder opened (read_stream)
        seen = watch((lines.append(line) or line for line in proc.stdout), budget, proc.terminate)
        proc.wait()
    finally:
        timer.cancel()
    result = seen["result"]
    trail = read_stream("".join(lines))[1]
    timed_out = False
    if result is None:
        if fired:
            why = f"The finder ran for more than {timeout_s // 60} minutes and was stopped."
            timed_out = True
        elif seen["stopped"]:
            why = f"The finder was stopped at its token budget ({budget:,})."
        else:
            tail = "".join(err).strip().splitlines()
            why = "The finder ended without a result" + (f": {tail[-1][:200]}" if tail else ".")
        result = {"is_error": not seen["stopped"], "result": why, "usage": seen["usage"]}
    if seen["stopped"]:
        result["stopped"] = "budget"
    if timed_out:
        result["timed_out"] = True
    finds, how = read_finds(folder, lines)
    if how != "file":
        # Nothing usable in the folder, or the list had to be taken from elsewhere: the folder, the stream and
        # stderr are kept so the cause can be seen (run_finder files them under the project's runs).
        result["finds_from"] = how
        result["kept"] = str(keep_session(folder, lines, err, how))
    shutil.rmtree(folder, ignore_errors=True)
    return result, finds, trail


def file_kept(project, kept: str, row, group, n: int) -> str:
    """Move a kept finder session (keep_session) into the project's runs/find-kept, named by row, time and round."""
    import time
    name = (row or f"{group[0]}-{group[1]}" if (row or group) else "find") + time.strftime("-%Y%m%d-%H%M%S") + f"-r{n}"
    dest = project.path.joinpath("runs", "find-kept", re.sub(r"[^\w.-]+", "-", name))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(kept, dest)
    except OSError:
        return kept
    return str(dest)


def finder_root() -> str | None:
    """Where the finder's folder is made: the temp folder, unless it lies under a root the file tools are refused
    (OFF_LIMITS), where the finder could not write its finds.json at all (a TMPDIR under /Users, 4 Nov 2026);
    then /tmp. None means tempfile's own choice."""
    root = Path(tempfile.gettempdir()).resolve()
    if any(str(root) == r or str(root).startswith(r + "/") for r in OFF_LIMITS):
        for other in ("/tmp", "/private/tmp", "/var/tmp"):
            if Path(other).is_dir():
                return other
    return None


def _parse_finds(text: str) -> dict:
    """The finds object in a file's or a reply's text: plain JSON, else fenced or wrapped in words."""
    try:
        got = json.loads(text)
    except (TypeError, ValueError):
        got = finds_from(text or "")
    return got if isinstance(got, dict) and ("save" in got or "missing" in got) else {}


def finds_from(text: str) -> dict:
    """The finds.json object in a text (bare or fenced; the last fenced one when there are several). {} if none."""
    text = text or ""
    for body in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or []):
        try:
            got = json.loads(body)
            if isinstance(got, dict) and ("save" in got or "missing" in got):
                return got
        except ValueError:
            pass
    start, end = text.find("{"), text.rfind("}")
    try:
        got = json.loads(text[start:end + 1]) if start >= 0 else {}
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def written_finds(lines) -> list[tuple[str, str]]:
    """(path, content) of every Write the finder made to a file named finds.json, in order, from the stream."""
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Write":
                given = block.get("input") or {}
                path = str(given.get("file_path") or "")
                if Path(path).name == "finds.json" and isinstance(given.get("content"), str):
                    out.append((path, given["content"]))
    return out


def read_finds(folder: Path, lines) -> tuple[dict, str]:
    """The finder's list and where it came from: "file" (its folder's finds.json); "file, loosely read" (that file
    with fences or words around the JSON); "written elsewhere: PATH" (the finder wrote finds.json to another path:
    on 4-5 Nov 2026 three finds wrote it under a copy of their folder's path with one "/" turned into "-", and the
    Write tool made that folder, so the run saw no list and lost 0.8M to 1.7M tokens each time; the content is
    taken from the Write call in the stream, never read from that path); "nothing" when there is no list."""
    path = folder / "finds.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = None
    if text is not None:
        try:
            got = json.loads(text)
            if isinstance(got, dict):
                return got, "file"
        except ValueError:
            pass
        got = _parse_finds(text)
        if got:
            return got, "file, loosely read"
    for where, content in reversed(written_finds(lines)):
        got = _parse_finds(content)
        if got:
            return got, ("file, loosely read" if Path(where) == path else f"written elsewhere: {where}")
    return {}, "nothing"


def keep_session(folder: Path, lines, err, how: str) -> Path:
    """Copy a finder's folder, its stream and its stderr out of the temp folder that is deleted next."""
    kept = Path(tempfile.mkdtemp(prefix="hb-find-kept-", dir=finder_root()))
    try:
        shutil.copytree(folder, kept / "folder", dirs_exist_ok=True)
    except OSError:
        pass
    (kept / "stream.jsonl").write_text("".join(lines), encoding="utf-8")
    (kept / "stderr.txt").write_text("".join(err), encoding="utf-8")
    (kept / "why.txt").write_text(f"finds.json: {how}\nfolder: {folder}\n", encoding="utf-8")
    return kept


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
              + search_words(got.get("search_tokens") or 0, got.get("searches") or 0)
              + (f" {got['over_limit']} more addresses were listed than the {MAX_SAVE} a round saves." if got.get("over_limit") else ""))
        if got.get("retried"):
            print(f"A finder session ended with no list of finds, so it was run once more. What it left is kept in "
                  f"{', '.join(got['retried'])}.")
        elif got.get("kept"):
            print(f"The finder's list was not where it was asked to write it, and was taken from its session instead. "
                  f"The session is kept in {', '.join(got['kept'])}.")
        if got["read_blocked"]:
            print(f"Warning: the finder read {len(got['read_blocked'])} page(s) on a results site, so its finds may "
                  "echo the benchmark's own evidence: " + ", ".join(got["read_blocked"]))
    return 0 if got["ok"] else 1

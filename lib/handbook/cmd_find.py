"""hb find --project DIR (--row ROW | --group LABEL=VALUE) [--missing TEXT] [--json]: find and save the
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
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .claude_cli import FINDER_MODEL
from .cli import command_parser, open_project
from .cmd_score import OFF_LIMITS
from .folder import HandbookError, write_json

HB = Path(__file__).resolve().parents[1] / "hb.py"
CAP = 120_000
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
   Use your web search and fetch tools.
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
   that needs a script or a login to show it.

## What to write

Write `finds.json` in this folder, and nothing else:

```
{{"save": [{{"url": "https://...", "how": "plan" | "search" | "last_year",
             "date": "YYYY-MM-DD or null (only when the document says the date it speaks as at)",
             "weak": "one line saying why it is not in the organisation's own words, or null",
             "what": "a few words: what the document is"}}],
  "missing": [{{"what": "the document you expected and could not find", "looked": "where you looked"}}]}}
```

`how` is "plan" when the places to look led you there, "last_year" when last year's list did, and
"search" otherwise. {missing_note}
{round_note}

Token cap: {cap:,}. If you get near it, write what you have to finds.json and stop.
Reply in one line: how many documents you listed and how many you could not find.
"""


def context_for(project, row: str | None, group: tuple[str, str] | None, missing: str | None) -> dict:
    """What the finder may see: never answers, scores or last edition's results."""
    meta = project.read("project")
    units = project.read("units").get("units") or []
    where = project.read("where_to_look") if project.file("where_to_look").is_file() else {}
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
        aim = entry.get("for")
        if isinstance(aim, list):
            return bool(values & {a for a in aim if isinstance(a, str)})
        return aim == "all" or (isinstance(aim, str) and aim in values)
    plan = [p for p in where.get("plan") or [] if isinstance(p, dict) and wanted(p)]
    docs = project.documents()
    have = set()
    for u in rows:
        have |= set(project.found(u["id"]).get("documents") or [])
    questions = [{k: q.get(k) for k in ("id", "short", "wording", "evidence")}
                 for q in project.read("questions").get("questions") or []]
    return {
        "benchmark": meta.get("name"), "cutoff": meta.get("cutoff"),
        "mode": "row" if row else "group",
        "row": {"id": rows[0]["id"], "name": rows[0].get("name"), "labels": rows[0].get("labels")} if row else None,
        "group": {"label": group[0], "value": group[1], "rows": [u.get("name") for u in rows]} if group else None,
        "questions": questions,
        "places_to_look": plan,
        "last_year_sources": [s for s in where.get("last_year") or [] if not row or s.get("row") in (None, row)],
        "already_saved": [{"title": docs.get(d, {}).get("title"), "url": docs.get(d, {}).get("url")} for d in sorted(have)],
        "what_was_missing": missing or None,
    }


def brief_for(context: dict, unit_word: str, plural: str) -> str:
    who = (f"{unit_word} you are finding for" if context["mode"] == "row"
           else f"group of {plural} you are finding for (documents that speak for every one of them, such as a "
                "group-wide policy or report)")
    note = ("This is the second search: `what_was_missing` says what the first search could not find. Look where "
            "it did not: other language versions, a parent organisation's site, document libraries, PDFs linked "
            "from reports, archived copies from before the cut-off." if context.get("what_was_missing") else "")
    if context["mode"] == "group":
        note += " In group mode leave `missing` empty: each row's own finder looks again."
    again = context.get("first_round")
    round_note = ("This is your second go. `first_round` in context.json says what happened to your first list: the "
                  "addresses that would not save, and why, and how many words of text each saved document holds. A "
                  "law, policy or report of a few hundred words is usually a summary, a viewer page or a landing page, "
                  "not the full text. List only better copies of what is missing or too thin (another official site, a "
                  "PDF, an archived copy from before the cut-off), and record in `missing` what you still cannot find. "
                  "Do not list again what saved well. Never give an address you have not opened with your fetch tool "
                  "in this session." if again else
                  "Never give an address you have not opened with your fetch tool in this session.")
    return BRIEF.format(who=who, plural=plural, missing_note=note.strip(), round_note=round_note, cap=CAP)


def command(folder: Path, model: str = FINDER_MODEL) -> list[str]:
    settings = {"disableAllHooks": True,
                "permissions": {"deny": [f"{tool}(/{root}/**)" for root in OFF_LIMITS
                                         for tool in ("Read", "Grep", "Glob", "Edit", "Write")]}}
    prompt = (f"Find documents for one benchmark row. Your folder is {folder}. Read {folder}/brief.md and follow it "
              f"exactly: read {folder}/context.json, search and read the web, and write {folder}/finds.json. Work only "
              "inside that folder.")
    return ["claude", "-p", prompt, "--safe-mode", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--settings", json.dumps(settings), "--disable-slash-commands", "--no-session-persistence",
            "--output-format", "json", "--permission-mode", "dontAsk", "--tools", TOOLS, "--allowed-tools", TOOLS,
            "--disallowed-tools", "Bash,Edit", "--model", model]


def hb(project, *args, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(HB), *args, "--project", str(project.path)], capture_output=True,
                       text=True, timeout=timeout)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def save_finds(project, finds: dict, row: str | None, group: tuple[str, str] | None) -> dict:
    """Save each listed address with hb fetch, and record each gap with hb found (row mode only)."""
    target = ["--row", row] if row else ["--group", f"{group[0]}={group[1]}"]
    saved, failed, recorded = [], [], 0
    for item in finds.get("save") or []:
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        how = item.get("how") if item.get("how") in ("plan", "search", "last_year") else "search"
        args = ["fetch", url, *target, "--how", how]
        if item.get("date"):
            args += ["--date", str(item["date"])]
        if item.get("weak"):
            args += ["--weak", str(item["weak"])]
        code, out = hb(project, *args)
        (saved if code == 0 else failed).append({"url": url, "what": item.get("what"), "said": (out.splitlines() or [""])[-1][:300]})
    return {"saved": saved, "failed": failed, "missing": list(finds.get("missing") or [])}


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
    return {"would_not_save": [{"url": f["url"], "what": f.get("what"), "why": f["said"]} for f in got["failed"]],
            "saved": [{"title": docs.get(d, {}).get("title"), "url": docs.get(d, {}).get("url"), "words": words_in(project, d)}
                      for d in fresh]}


def run_finder(project, row: str | None = None, group: tuple[str, str] | None = None, missing: str | None = None,
               timeout_s: int = TIMEOUT_S) -> dict:
    """One finder session, and a second when the first list did not all save or saved a thin copy:
    the second is told what happened, as a finder with a shell would see for itself (Antigua and
    Barbuda, 19 Oct 2026: a dead address and a 417-word viewer page of the Act left 59 of 61 answers
    with no evidence)."""
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so no finder can be started.")
    meta = project.read("project")
    rows = [row] if row else None
    before = set()
    for r in rows or [u["id"] for u in project.read("units").get("units") or [] if (u.get("labels") or {}).get(group[0]) == group[1]]:
        before |= set(project.found(r).get("documents") or [])
    rounds, tokens, replies, results = [], 0, [], []
    context = context_for(project, row, group, missing)
    for n in (1, 2):
        result, finds = _session(context, meta, timeout_s)
        usage = result.get("usage") or {}
        tokens += sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                        "cache_read_input_tokens", "output_tokens"))
        replies.append(str(result.get("result") or "").strip())
        results.append(dict(result, finds=finds))
        got = save_finds(project, finds, row, group)
        rounds.append(got)
        if n == 2:
            break
        report = first_round_report(project, row, group, before, got)
        if not report["would_not_save"] and all(d["words"] >= THIN_WORDS for d in report["saved"]):
            break
        context = dict(context_for(project, row, group, missing), first_round=report)
    not_found = record_gaps(project, row, rounds)
    name = f"find-{row}.json" if row else f"find-{group[0]}-{group[1]}.json"
    receipt = project.path.joinpath(*RECEIPTS, name)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    write_json(receipt, {"rounds": results, "saved": rounds})
    saved = sum(len(r["saved"]) for r in rounds)
    return {"saved": saved, "failed": len(rounds[-1]["failed"]), "not_found": not_found, "rounds": len(rounds),
            "tokens": tokens, "ok": any(r.get("finds") for r in results), "reply": replies[-1]}


def _session(context: dict, meta: dict, timeout_s: int) -> tuple[dict, dict]:
    folder = Path(tempfile.mkdtemp(prefix="hb-find-"))
    write_json(folder / "context.json", context)
    (folder / "brief.md").write_text(brief_for(context, meta.get("unit_word") or "row", meta.get("unit_word_plural") or "rows"),
                                     encoding="utf-8")
    try:
        proc = subprocess.run(command(folder), cwd=folder, capture_output=True, text=True, timeout=timeout_s)
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            result = {"is_error": True, "result": "The finder ended without a result" + (f": {tail[-1][:200]}" if tail else ".")}
    except subprocess.TimeoutExpired:
        result = {"is_error": True, "result": f"The finder ran for more than {timeout_s // 60} minutes and was stopped."}
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
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    got = run_finder(project, args.row, args.group, args.missing)
    if args.json:
        print(json.dumps(got))
    else:
        print(f"Saved {got['saved']}, could not save {got['failed']}, not found {got['not_found']}, in {got['rounds']} "
              f"round{'s' if got['rounds'] != 1 else ''}. {got['tokens']:,} tokens processed.")
    return 0 if got["ok"] else 1

"""hb draft-setup --project DIR: draft the set-up from the owner's uploads, in one background Claude session.

What "Draft my set-up" on the Set-up tab runs (through `hb serve`). One `claude -p` session, run as
scorers are (the unmodified command, the owner's plan, only the tools it needs, the Bash sandbox
on with no network), follows the set-up skill's steps 1 and 2 without an owner to ask: it reads
every file in `inbox/uploads/`, records them in `provided`, drafts the questions, rows, score sheet,
group labels and where-to-look plan as `draft`, holds back last edition's results when they are
there, and turns what it could not settle into open rules and data needs (the few that matter show on the
chat button's badge).
The owner then checks, corrects and confirms in the page.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .claude_cli import MODEL
from .cli import command_parser, open_project
from .folder import HandbookError, write_json

LIB = Path(__file__).resolve().parents[1]
SKILL = LIB.parent / "skills" / "setup"
TOOLS = "Read,Write,Edit,Glob,Grep,Bash"
CAP = 1_200_000
TIMEOUT_S = 2700
# Not runs/*.json itself: every file there is read as a run (validate listed 12 problems and the page a
# phantom run after each draft, 14 Oct 2026).
RECEIPT = ("runs", "receipts", "setup-draft.json")
# What Claude is doing right now, in the owner's words, for the page's run bar and the corner chat
# (serve.py reads it while the draft runs). Rewritten after every step; never read back by the draft.
PROGRESS = ("runs", "receipts", "setup-draft.progress.json")
# The set-up files whose arrival changes the checklist: the server rebuilds the page when one is written.
LANDS = {"questions.json": "the questions", "units.json": "the list", "scoresheet.json": "the score sheet",
         "project.json": "the checklist", "rules.json": "the rules", "needs.json": "what is still needed",
         "where_to_look.json": "where to look"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prompt_for(project: Path, python: str) -> str:
    hb = f'"{python}" "{LIB / "hb.py"}"'
    return "\n".join([
        f"Draft a benchmark's set-up from the owner's files. The project folder is {project}.",
        f"Read {SKILL / 'SKILL.md'} (steps 1 and 2 only, and its rules) and {SKILL / 'drafting.md'}, and follow them, "
        "with these differences, because the owner is not here to answer:",
        f"- The owner's files are in {project}/inbox/uploads/ (each name starts with an upload id and two dashes). "
        f"Read them with: {hb} material-text --project \"{project}\" FILE (it prints PDF, Word and Excel files as text).",
        f"- For a script (for example turning a long sheet into the row list), run it with \"{python}\", never python3: "
        "on a Mac without Apple's developer tools, python3 opens an installer instead of running.",
        "- The project folder already exists (hb init has run); do not run init.",
        "- Do not ask questions. Where the skill says to ask or wait, draft what the material supports, mark it draft, "
        "and add the question to your open questions instead.",
        "- If last edition's results are among the files, normalise and hold them back and run rebuild-check as step 4 "
        "says, and record any mismatch as an open question.",
        f"- Run every hb command as {hb} COMMAND --project \"{project}\" ... . Validate and fix until validate is clean, "
        "then build. Do not publish anything and do not use the web.",
        "- Put each open question where the owner will see it: a point a scorer would have to guess becomes an open "
        "rule (raise-rule, as grill.md says, with your recommendation), and something only the owner can supply becomes "
        "a data need (step 5). Keep them few: only what changes many points or blocks the work.",
        "- Work in few, large steps: every step re-reads everything before it, so the number of steps is "
        "the cost (drafts took 23 to 53 steps and 1.3 to 5.5 million tokens, 14 Oct 2026). Read each upload "
        "once; write each set-up file whole in one go; write a long list of rows or results with one script, "
        "not by hand; do not read back a file you have just written; run validate once everything is "
        "written, then fix what it lists. Of grill.md you need only \"Writing a rule\".",
        "- Before you finish, check four things (format tests, 15 Oct 2026): (1) in last edition's results, a "
        "question that is always empty or 'not applicable' for one kind of row gets applies_to on that label; "
        "(2) may_not_apply only on questions about something a row may simply not have (an activity, a "
        "product, an animal), never on every question, and reading \"lookup\" only on questions answered straight "
        "off a record, list or table (a vote, a register entry), never on one that weighs a policy or a law; (3) a list of practices that rule a row out becomes "
        "questions or a rule, not left out; (4) each rule you raise fits the levels and no_evidence_value you "
        "wrote (a rule saying 'not applicable' needs may_not_apply; a rule saying 'NC when nothing is found' "
        "needs that as the no_evidence_value); and results_sites lists only hosts the material names as "
        "where the results are published, and the hosts their tables load from (a Google Sheet behind a "
        "tracker): never guess one (ChickenTrack's draft invented two and missed the real one). Also: a "
        "published rank is held back as rank:overall (a number); a cut-off the material states (a date, or a "
        "window such as 'June to mid-August 2025') goes into the basics as the draft cutoff, but when the material "
        "states none, leave cutoff null and its checklist item missing (never today's date, never a date you work "
        "out) and raise one open rule asking the owner for the date, each option with \"sets\": {\"cutoff\": "
        "\"YYYY-MM-DD\"} holding a date from the material (when last edition's data was collected, when it was "
        "published), with no date recommended (RDR and Global Health 50/50, 21 Oct 2026, got an invented date and "
        "today's); where you invent a boundary between levels the material does not give, "
        "raise a rule naming it; and walk two real rows through each ladder you write. A question or category the "
        "material says is scored from the owner's own records, survey or figures the organisations send only to the "
        "owner is kept with its published scale, points and weight, and linked to one data need with only_source "
        "true, as drafting.md says: never drop a weighted part of the score sheet (Citizens Advice's complaints, "
        "35% of its rating, and the abuses third of RSF's safety score were dropped, 21 Oct 2026). Raise an open "
        "question too when the methodology "
        "names another edition or year than the one the owner is scoring (say which questions it may change), when "
        "the published results rest on a questionnaire the organisations answered or on evidence behind a login, or "
        "when a published value averages many dated items by weights the material does not give: those cells may "
        "not be reproducible from public documents (sweep round 3, 21 Oct 2026: UK university league, the fashion "
        "index, InfluenceMap).",
        f"- Last, run {hb} names --project \"{project}\" and fix what it lists in the names you drafted (a code such as "
        "D22, a file name, mixed capitals, a short name that is long or the same as another's); a fault in the owner's "
        "own list (a value spelled two ways) becomes an open question. Nobody looks over the names after you. "
        "Every rule's question and note is in the owner's words: their word for a row, and never a step of the tool "
        "(a rebuild check, holding back, validate); say what it means for them.",
        f"Token cap: {CAP:,}. If you get near it, save the drafts you have, validate, build and stop.",
        "Reply in two lines: what you drafted, and how many open questions you left for the owner.",
    ])


def _about_cutoff(rule: dict) -> bool:
    return any(isinstance(o, dict) and "cutoff" in (o.get("sets") or {}) for o in rule.get("options") or []) \
        or any(w in str(rule.get("ask") or "").lower() for w in ("cut-off", "cutoff", "cut off"))


def ensure_cutoff_rule(project, today: str) -> dict | None:
    """After a draft: a cut-off the material did not give (none, or the day of drafting) goes to the owner as an
    open rule, unless one already asks about it (de-transparenzranking and gh5050, 21 Oct 2026: today's date stood
    in silently, while the published results were collected months earlier). Returns the rule added, or None.
    The day of drafting is never kept as the cut-off, and the rule recommends no date: nothing is set until the owner
    picks (sweep round 3, 21 Oct 2026: RDR got an invented date and Global Health 50/50 today's)."""
    from .stakes import rule_stakes
    meta = project.read("project")
    if meta.get("cutoff") not in (None, "", today):
        return None
    if meta.get("cutoff") == today:
        meta["cutoff"] = None
        if isinstance(meta.get("checklist"), dict):
            meta["checklist"]["cutoff"] = "missing"
        project.write("project", meta)
    rules = project.read("rules")
    if any(isinstance(r, dict) and _about_cutoff(r) for r in rules.get("rules") or []):
        return None
    qids = [q.get("id") for q in project.read("questions").get("questions") or [] if isinstance(q, dict) and q.get("id")]
    if not qids:
        return None
    have = rules.setdefault("rules", [])
    n = max([int(r["id"][1:]) for r in have if isinstance(r, dict) and str(r.get("id", ""))[1:].isdigit()] or [0])
    rule = {"id": f"R{n + 1}", "kind": "evidence", "question_ids": qids, "rows": None,
            "ask": ("Your material does not say what date the evidence must date from (the cut-off), or what date last "
                    "edition's results reflect. Which date should Claude use?"),
            "options": [{"id": "a", "text": f"Today ({today}), the evidence as it stands now.", "sets": {"cutoff": today}},
                        {"id": "b", "text": "Another date, such as when last edition's results were collected. Give it in your own words."}],
            "recommended": None, "why": "Your material does not say, so the date is yours to give.",
            "stakes": None, "last_year_note": None, "status": "open", "decision": None, "raised_by": "setup"}
    have.append(rule)
    try:
        rule["stakes"] = rule_stakes(project, rule)
    except (HandbookError, KeyError, TypeError, ValueError):
        rule["stakes"] = None
    project.write("rules", rules)
    return rule


def drop_stale_where_refs(project) -> list[str]:
    """After a draft: where to look may still name questions the draft merged or renumbered away (TranspariMED
    CTIS, 22 Oct 2026). Those mentions are taken out, so a stale id never reaches the finder or the page.
    Returns the ids taken out."""
    from .shapes import stale_where_refs, where_without_stale
    if not project.file("where_to_look").is_file():
        return []
    where = project.read("where_to_look")
    qids = {q.get("id") for q in project.read("questions").get("questions") or [] if isinstance(q, dict) and q.get("id")}
    if not qids:
        return []
    gone = list(dict.fromkeys(q for _, _, ids in stale_where_refs(where, qids) for q in ids))
    if gone:
        project.write("where_to_look", where_without_stale(where, qids))
    return gone


def command(project: Path, python: str, held_back: Path | None = None) -> list[str]:
    """The draft session. It writes in the project and, to hold back last edition's results, in the
    project's held-back folder beside it (EggTrack, 14 Oct 2026: without it, hold-back was refused)."""
    lib = str(LIB.parent)
    writable = [str(project)] + ([str(held_back)] if held_back else [])
    settings = {"disableAllHooks": True,
                "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                            "autoAllowBashIfSandboxed": True, "filesystem": {"allowWrite": writable},
                            "network": {"allowedDomains": []}}}
    allowed = ",".join(["Read", "Glob", "Grep", "Bash"] + [f"{tool}({w}/**)" for w in writable for tool in ("Edit", "Write")])
    extra = ["--add-dir", str(held_back)] if held_back else []
    return ["claude", "-p", prompt_for(project, python), "--safe-mode", "--strict-mcp-config", "--mcp-config",
            '{"mcpServers":{}}', "--settings", json.dumps(settings), "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
            "--tools", TOOLS, "--allowed-tools", allowed, "--disallowed-tools", "WebFetch,WebSearch",
            "--add-dir", lib, "--model", MODEL, *extra]


def upload_names(project: Path) -> dict[str, str]:
    """Stored upload file name -> the name the owner gave it (uploads are saved as `<id>--<name>`)."""
    return {f.name: f.name.split("--", 1)[1] for f in project.joinpath("inbox", "uploads").glob("*--*")}


ALL = "*"                     # describe(): a step that read every upload at once


def describe(tool: dict, uploads: dict[str, str]) -> tuple[str, str | None, str | None]:
    """One step of the draft session in the owner's words: (line, upload read, set-up file written)."""
    name, inp = tool.get("name"), tool.get("input") or {}
    if name == "Bash":
        cmd = str(inp.get("command") or "")
        named = [stored for stored, shown in uploads.items() if stored in cmd or (shown and shown in cmd)]
        if "material-text" in cmd or (named and any(w in cmd for w in ("cat ", "head ", "python", "open("))):
            # Sweep round 3 (four benchmarks): the draft read its files in one loop or script, so no command
            # named a file and the page said "0 of 4 files read" until Done.
            if len(named) > 1 or (not named and "inbox/uploads" in cmd and any(c in cmd for c in ("*", "for ", "glob"))):
                return "Reading your files", ALL, None
            if named:
                return f"Reading {uploads[named[0]]}", named[0], None
            return "Reading one of your files", None, None
        for word, line in ((" validate", "Checking the draft for gaps"), (" build", "Building your page"),
                           ("hold-back", "Setting last edition's results aside"), ("rebuild-check", "Checking last edition's results add up"),
                           ("raise-rule", "Noting a question for you"), (" need", "Noting something you may need to add")):
            if word in cmd:
                return line, None, None
        return "Working through your files", None, None
    if name in ("Write", "Edit"):
        base = Path(str(inp.get("file_path") or "")).name
        if base in LANDS:
            return f"Writing {LANDS[base]}", None, base
        return "Writing part of the set-up", None, None
    if name in ("Read", "Glob", "Grep"):
        path = str(inp.get("file_path") or inp.get("path") or "")
        if "/skills/" in path:
            return "Reading how to draft a set-up", None, None
        stored = Path(path).name
        if stored in uploads:
            return f"Reading {uploads[stored]}", stored, None
        return "Looking through the project", None, None
    return "Thinking about your set-up", None, None


def run_streaming(cmd: list[str], project: Path, timeout: int = TIMEOUT_S) -> dict:
    """Run the draft session, writing PROGRESS after every step, and return its final result event."""
    uploads = upload_names(project)
    path = project.joinpath(*PROGRESS)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"line": "Starting Claude", "files_total": len(uploads), "files_read": 0, "steps": 0,
             "wrote": [], "started": now_iso(), "at": now_iso()}
    read: set[str] = set()
    write_json(path, state)
    # What the draft wrote, from the files themselves: it often writes them with a script, not the Write tool.
    def stamp(name):
        f = project / name
        return f.stat().st_mtime_ns if f.is_file() else None
    before = {name: stamp(name) for name in LANDS}
    proc = subprocess.Popen(cmd, cwd=project, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")
    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    result, last = {}, ""
    try:
        for raw in proc.stdout:
            last = raw.strip() or last
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result = event
                continue
            if event.get("type") != "assistant":
                continue
            for part in (event.get("message") or {}).get("content") or []:
                if part.get("type") != "tool_use":
                    continue
                line, upload, wrote = describe(part, uploads)
                if upload == ALL:
                    read.update(uploads)
                elif upload:
                    read.add(upload)
                for name in [wrote] + [n for n in LANDS if stamp(n) != before[n]]:
                    if name and name not in state["wrote"]:
                        state["wrote"].append(name)
                state.update({"line": line, "files_read": len(read), "steps": state["steps"] + 1, "at": now_iso()})
                if line.startswith("Reading ") and uploads and upload:
                    state["line"] = f"{line} ({len(read)} of {len(uploads)})"
                write_json(path, state)
        proc.wait()
    finally:
        killer.cancel()
    state.update({"line": "Done" if result and not result.get("is_error") else "Stopped", "at": now_iso(), "finished": True})
    write_json(path, state)
    if not result:
        tail = (proc.stderr.read() if proc.stderr else "").strip().splitlines() or [last]
        raise HandbookError("The set-up draft ended without a result" + (f": {tail[-1][:200]}" if tail and tail[-1] else "."))
    return result


def main(argv) -> int:
    parser = command_parser("draft-setup", "Draft the set-up from the files in inbox/uploads/ (one background Claude session).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so the set-up cannot be drafted.")
    from .material import files
    if not files(project):         # names plain files put in inbox/uploads/ by hand, as uploads are
        raise HandbookError("There are no files to draft from. Add them on the Set-up tab first.")
    project.held_back.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    result = run_streaming(command(project.path, sys.executable, project.held_back), project.path)
    try:
        ensure_cutoff_rule(project, today)          # never a silent default for the cut-off
    except (HandbookError, OSError, ValueError):
        pass
    try:
        drop_stale_where_refs(project)              # where to look never names a question that is gone
    except (HandbookError, OSError, ValueError):
        pass
    path = project.path.joinpath(*RECEIPT)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, result)
    usage = result.get("usage") or {}
    tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                   "cache_read_input_tokens", "output_tokens"))
    if args.json:
        print(json.dumps({"tokens": tokens, "result": result.get("result"), "ok": not result.get("is_error")}))
    else:
        print(str(result.get("result") or "").strip())
        print(f"{tokens:,} tokens processed over {result.get('num_turns') or 0} turns.")
    return 1 if result.get("is_error") else 0

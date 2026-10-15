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
from pathlib import Path

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
        "product, an animal), never on every question; (3) a list of practices that rule a row out becomes "
        "questions or a rule, not left out; (4) each rule you raise fits the levels and no_evidence_value you "
        "wrote (a rule saying 'not applicable' needs may_not_apply; a rule saying 'NC when nothing is found' "
        "needs that as the no_evidence_value); and results_sites lists only hosts the material names as "
        "where the results are published, and the hosts their tables load from (a Google Sheet behind a "
        "tracker): never guess one (ChickenTrack's draft invented two and missed the real one). Also: a "
        "published rank is held back as rank:overall (a number); the cut-off you recommend goes into the "
        "basics as the draft cutoff; where you invent a boundary between levels the material does not give, "
        "raise a rule naming it; and walk two real rows through each ladder you write.",
        f"Token cap: {CAP:,}. If you get near it, save the drafts you have, validate, build and stop.",
        "Reply in two lines: what you drafted, and how many open questions you left for the owner.",
    ])


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
            "--no-session-persistence", "--output-format", "json", "--permission-mode", "dontAsk",
            "--tools", TOOLS, "--allowed-tools", allowed, "--disallowed-tools", "WebFetch,WebSearch",
            "--add-dir", lib, *extra]


def main(argv) -> int:
    parser = command_parser("draft-setup", "Draft the set-up from the files in inbox/uploads/ (one background Claude session).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so the set-up cannot be drafted.")
    if not any(project.path.joinpath("inbox", "uploads").glob("*--*")):
        raise HandbookError("There are no files to draft from. Add them on the Set-up tab first.")
    project.held_back.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(command(project.path, sys.executable, project.held_back), cwd=project.path, capture_output=True, text=True,
                          timeout=TIMEOUT_S)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise HandbookError("The set-up draft ended without a result" + (f": {tail[-1][:200]}" if tail else ".")) from None
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

"""hb draft-setup --project DIR: draft the set-up from the owner's uploads, in one background Claude session.

What "Draft my set-up" on the Set-up tab runs (through `hb serve`). One `claude -p` session, run as
scorers are (the unmodified command, the owner's plan, only the tools it needs, the Bash sandbox
on with no network), follows the set-up skill's steps 1 and 2 without an owner to ask: it reads
every file in `inbox/uploads/`, records them in `provided`, drafts the questions, rows, score sheet,
group labels and where-to-look plan as `draft`, holds back last edition's results when they are
there, and writes what it could not settle as open questions in Claude's note on the Set-up step.
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
RECEIPT = ("runs", "setup-draft-receipt.json")


def prompt_for(project: Path, python: str) -> str:
    hb = f'"{python}" "{LIB / "hb.py"}"'
    return "\n".join([
        f"Draft a benchmark's set-up from the owner's files. The project folder is {project}.",
        f"Read {SKILL / 'SKILL.md'} (steps 1 and 2 only, and its rules) and {SKILL / 'drafting.md'}, and follow them, "
        "with these differences, because the owner is not here to answer:",
        f"- The owner's files are in {project}/inbox/uploads/ (each name starts with an upload id and two dashes). "
        f"Read them with: {hb} material-text --project \"{project}\" FILE (it prints PDF, Word and Excel files as text).",
        "- The project folder already exists (hb init has run); do not run init.",
        "- Do not ask questions. Where the skill says to ask or wait, draft what the material supports, mark it draft, "
        "and add the question to your open questions instead.",
        "- If last edition's results are among the files, normalise and hold them back and run rebuild-check as step 4 "
        "says, and record any mismatch as an open question.",
        f"- Run every hb command as {hb} COMMAND --project \"{project}\" ... . Validate and fix until validate is clean, "
        "then build. Do not publish anything and do not use the web.",
        f"- Finish by writing Claude's note on the Set-up step as {LIB.parent / 'skills' / 'run' / 'deck.md'} says "
        "(--step setup): what you drafted, what you proposed rather than took from the files, and the open questions, "
        "most important first, each with your recommendation, in plain UK English for a non-technical owner.",
        f"Token cap: {CAP:,}. If you get near it, save the drafts you have, validate, build and stop.",
        "Reply in two lines: what you drafted, and how many open questions you left for the owner.",
    ])


def command(project: Path, python: str) -> list[str]:
    lib = str(LIB.parent)
    settings = {"disableAllHooks": True,
                "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                            "autoAllowBashIfSandboxed": True, "network": {"allowedDomains": []}}}
    allowed = ",".join(["Read", "Glob", "Grep", "Bash", f"Edit({project}/**)", f"Write({project}/**)"])
    return ["claude", "-p", prompt_for(project, python), "--safe-mode", "--strict-mcp-config", "--mcp-config",
            '{"mcpServers":{}}', "--settings", json.dumps(settings), "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "json", "--permission-mode", "dontAsk",
            "--tools", TOOLS, "--allowed-tools", allowed, "--disallowed-tools", "WebFetch,WebSearch",
            "--add-dir", lib]


def main(argv) -> int:
    parser = command_parser("draft-setup", "Draft the set-up from the files in inbox/uploads/ (one background Claude session).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so the set-up cannot be drafted.")
    if not any(project.path.joinpath("inbox", "uploads").glob("*--*")):
        raise HandbookError("There are no files to draft from. Add them on the Set-up tab first.")
    proc = subprocess.run(command(project.path, sys.executable), cwd=project.path, capture_output=True, text=True,
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

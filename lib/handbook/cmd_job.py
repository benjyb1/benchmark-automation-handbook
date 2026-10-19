"""hb job --brief FILE [--json]: one background Claude session that does what the owner asked for in
the corner chat and no ready-made tool covers (a new column, a change across many answers, a file
in a shape no export gives, sections Claude must read the pages to find).

What a job the owner confirmed in the chat runs (through `hb serve`, which takes a copy of the
project first so one Undo puts everything back, and checks and builds after). The brief is the
chat's own words for the task. The session runs as the set-up draft does (`cmd_draft_setup`): the
unmodified `claude` command on the owner's plan, Read, Glob, Grep, Bash and writes only inside the
project folder, the Bash sandbox on with no network, and no web. It may change any of the project's
files; the evidence rule still holds: a quote must be word for word on a saved page, and the
checks that run afterwards flag one that is not.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .claude_cli import MODEL
from .cli import command_parser, open_project
from .folder import HandbookError

LIB = Path(__file__).resolve().parents[1]
TOOLS = "Read,Write,Edit,Glob,Grep,Bash"
CAP = 1_500_000
TIMEOUT_S = 2700
MAX_TURNS = 80


def prompt_for(project: Path, python: str, brief: str) -> str:
    hb = f'"{python}" "{LIB / "hb.py"}"'
    return "\n".join([
        f"You are doing one task on a benchmark project for its owner, in the background. The project folder is {project}.",
        "The owner asked for it in the page's chat and confirmed it; nobody is here to answer questions, so do the task "
        "as well as the files allow and say in your reply what you could not do.",
        "",
        "THE TASK (the chat's words for what the owner wants):",
        brief.strip(),
        "",
        "The project folder:",
        "- project.json (name, edition, cut-off, the row word), questions.json (questions, levels, points), units.json "
        "(the rows), scoresheet.json, rules.json (rules, and the owner's standing instructions as rules of kind "
        "\"instruction\").",
        "- answers/<row>.json: {row, cells: {question id: {state, value, points, evidence: [{doc, page, section, quote, "
        "quote_en, language}], rules, judgement, unsure, note, run}}}. section is optional: where the quote sits in the "
        "document's own numbering, such as \"s. 4(2)(a)\" or \"art. 35(1)\".",
        "- documents/<doc>/meta.json (title, publisher, date, url) and documents/<doc>/pages.json ({pages: [{page, text}]}): "
        "the saved text of every document. found/<row>.json lists each row's documents.",
        "- viewer/ and page-data/ are built from the rest: never edit them; build rebuilds them.",
        f"- Run hb commands as {hb} COMMAND --project \"{project}\" ... ; {hb} help lists them and COMMAND --help explains one. "
        "Prefer a command to editing a file by hand where one exists (add-instruction, raise-rule, locate, check, validate).",
        f"- For a script, run it with \"{python}\", never python3.",
        "",
        "Rules:",
        "- Evidence is only ever words copied exactly from a saved page (documents/<doc>/pages.json). Never invent or "
        "paraphrase a quote, a page or a section. If something cannot be found in the saved pages, say so.",
        "- Do not use the web, do not publish anything, and work only inside the project folder.",
        "- Work in few, large steps: every step re-reads everything before it, so the number of steps is the cost. "
        "Use one script for a change across many files rather than many edits.",
        f"- When you have finished, run {hb} validate --project \"{project}\" and fix what it lists. The page then checks "
        "and rebuilds by itself.",
        f"Token cap: {CAP:,}. If you get near it, save what you have, validate and stop.",
        "Reply in at most three short sentences for the owner, in plain UK English with no file names or commands: "
        "what you changed, and anything you could not do.",
    ])


def command(project: Path, python: str, brief: str) -> list[str]:
    lib = str(LIB.parent)
    settings = {"disableAllHooks": True,
                "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                            "autoAllowBashIfSandboxed": True, "filesystem": {"allowWrite": [str(project)]},
                            "network": {"allowedDomains": []}}}
    allowed = ",".join(["Read", "Glob", "Grep", "Bash", f"Edit({project}/**)", f"Write({project}/**)"])
    return ["claude", "-p", prompt_for(project, python, brief), "--safe-mode", "--strict-mcp-config", "--mcp-config",
            '{"mcpServers":{}}', "--settings", json.dumps(settings), "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "json", "--permission-mode", "dontAsk",
            "--tools", TOOLS, "--allowed-tools", allowed, "--disallowed-tools", "WebFetch,WebSearch",
            "--max-turns", str(MAX_TURNS), "--add-dir", lib, "--model", MODEL]


def main(argv) -> int:
    parser = command_parser("job", "Do one task the owner asked for in the chat (one background Claude session).")
    parser.add_argument("--brief", required=True, help="a text file with the task")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so the job cannot run.")
    brief = Path(args.brief).read_text(encoding="utf-8")
    if not brief.strip():
        raise HandbookError("The job has no task.")
    proc = subprocess.run(command(project.path, sys.executable, brief), cwd=project.path, capture_output=True,
                          text=True, timeout=TIMEOUT_S)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise HandbookError("The job ended without a result" + (f": {tail[-1][:200]}" if tail else ".")) from None
    usage = result.get("usage") or {}
    tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                   "cache_read_input_tokens", "output_tokens"))
    if args.json:
        print(json.dumps({"tokens": tokens, "result": result.get("result"), "ok": not result.get("is_error"),
                          "turns": result.get("num_turns")}))
    else:
        print(str(result.get("result") or "").strip())
        print(f"{tokens:,} tokens processed over {result.get('num_turns') or 0} turns.")
    return 1 if result.get("is_error") else 0

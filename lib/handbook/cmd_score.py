"""hb score --kit KIT [--model M] [--effort E] [--max-turns N]: run one scorer on a kit in a clean,
headless Claude session and save its receipt.

The scorer is a fresh `claude -p` in safe mode: no MCP servers, plugins, skills, hooks, CLAUDE.md or
custom agents of the owner's session ride along, and it has only the tools a scorer needs (read,
search, write; shell commands for grep and the kit's check_quotes.py). The prompt is the one in
skills/run/scorer.md and names the kit path and the cap only. Blindness is enforced, not asked
for: Claude Code's sandbox refuses shell reads under the owner's folders (`OFF_LIMITS`), the
permission rules refuse the file tools there, and the session has no network. The session's answer, with the tokens it processed (cache reads, fresh input, output)
and the turns, is saved as KIT/receipt.json for `hb run usage --receipt`.

Measured 10 and 11 Oct 2026 on three Pecking Order rows: a scorer started from the owner's Code tab
session carried 73,000 tokens of that session's own preamble into every turn; the same kit in a
safe-mode session starts far lighter, so the same answers cost a fraction of the tokens.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .cli import command_parser
from .folder import HandbookError, write_json

CAP = 200_000
TIMEOUT_S = 1800
RECEIPT = "receipt.json"
# What a scorer may do: read and search the kit, write its answers, and run shell commands (grep
# over the page files, the kit's check_quotes.py). The web tools are taken away. Bash is allowed
# whole: with only prefix rules (Bash(grep *) and the like) a compound command is refused, the
# scorer then falls back to one file read per turn, and the turns doubled when this was measured.
TOOLS = "Read,Write,Edit,Glob,Grep,Bash"
NO_TOOLS = "WebFetch,WebSearch"
# Where a scorer must not look: the owner's files (the project folder, last edition's results, the
# answer key) live under these roots; the kit sits in the system temp folder. Claude Code's sandbox
# stops shell commands reading there (an OS sandbox, not a prompt), and the permission rules stop
# the Read, Grep and Glob tools, which the sandbox does not cover. Checked 11 Oct 2026: cat, Read
# and Grep outside the kit were all refused, cat inside it worked, and the turn count held.
OFF_LIMITS = ("/Users", "/Volumes", "/home")


def settings(kit: Path) -> dict:
    """The one-session settings: hooks off, the Bash sandbox on and failing closed, no network,
    reads allowed in the kit and refused under OFF_LIMITS, and the same roots denied to the file
    tools."""
    return {"disableAllHooks": True,
            "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                        "autoAllowBashIfSandboxed": True,
                        "filesystem": {"allowRead": [str(kit)], "denyRead": list(OFF_LIMITS)},
                        "network": {"allowedDomains": []}},
            "permissions": {"deny": [f"{tool}(/{root}/**)" for root in OFF_LIMITS
                                     for tool in ("Read", "Grep", "Glob", "Edit", "Write")]}}


def prompt_for(kit: Path, cap: int = CAP) -> str:
    """The scorer prompt from skills/run/scorer.md, filled in: the kit path and the cap, nothing else."""
    return (f"Score one row of a benchmark. Your folder is {kit}. Read {kit}/brief.md and follow it exactly: "
            f"work only inside {kit}, answer every question it lists, and write {kit}/answers.json. Do not use "
            f"the web.\nToken cap: {cap:,}. If you get near it, write the answers you have to {kit}/answers.json "
            "and stop.\nReply in one line: how many questions you answered, how many have no evidence, and how "
            "many you marked unsure.")


def command(kit: Path, model: str | None, effort: str | None, max_turns: int | None) -> list[str]:
    cmd = ["claude", "-p", prompt_for(kit), "--safe-mode", "--strict-mcp-config", "--mcp-config",
           '{"mcpServers":{}}', "--settings", json.dumps(settings(kit)), "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "json", "--permission-mode", "dontAsk",
           "--tools", TOOLS, "--allowed-tools", TOOLS, "--disallowed-tools", NO_TOOLS]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    return cmd


def run_scorer(kit: Path, model=None, effort=None, max_turns=None, timeout_s: int = TIMEOUT_S) -> dict:
    """Run the scorer and return the session's result JSON (also saved as KIT/receipt.json, with
    `kit`, `model` and `effort` added). Raises HandbookError when claude is missing, the session
    fails before it answers, or it prints no JSON."""
    if not (kit / "brief.md").is_file():
        raise HandbookError(f"{kit} is not a kit: there is no brief.md in it. Build it with hb kit first.")
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so no scorer can be started this way.")
    try:
        proc = subprocess.run(command(kit, model, effort, max_turns), cwd=kit, capture_output=True,
                              text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise HandbookError(f"The scorer for {kit.name} ran for more than {timeout_s // 60} minutes and was "
                            "stopped. Whatever it wrote to answers.json can still be ingested.") from None
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise HandbookError(f"The scorer session for {kit.name} ended without a result"
                            + (f": {tail[-1][:200]}" if tail else ".")) from None
    result.update({"kit": str(kit), "model": model, "effort": effort})
    write_json(kit / RECEIPT, result)
    return result


def describe(result: dict) -> str:
    usage = result.get("usage") or {}
    fresh = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
    read, out = usage.get("cache_read_input_tokens") or 0, usage.get("output_tokens") or 0
    return (f"{fresh + read + out:,} tokens processed ({read:,} cache reads, {fresh:,} fresh input, {out:,} output) "
            f"over {result.get('num_turns') or 0} turns.")


def main(argv) -> int:
    parser = command_parser("score", "Run one blind scorer on a kit in a clean headless session.", project=False)
    parser.add_argument("--kit", required=True, help="the kit folder hb kit printed")
    parser.add_argument("--model", default=None, help="a model alias, for example sonnet (default: the account's)")
    parser.add_argument("--effort", default=None, help="an effort level, for example low")
    parser.add_argument("--max-turns", type=int, default=None, help="stop the scorer after this many turns")
    args = parser.parse_args(argv)
    kit = Path(args.kit).expanduser()
    result = run_scorer(kit, args.model, args.effort, args.max_turns)
    reply = " ".join(str(result.get("result") or "").split())
    print(f"Scorer for {kit.name}: {reply or '(no reply)'}")
    print(describe(result))
    print(f"Receipt: {kit / RECEIPT}")
    if not (kit / "answers.json").is_file():
        print("The scorer wrote no answers.json.", file=sys.stderr)
        return 1
    return 1 if result.get("is_error") else 0

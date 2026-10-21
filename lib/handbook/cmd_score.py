"""hb score --kit KIT [--mode auto|one-read|full] [--model M] [--effort E] [--max-turns N] [--budget TOKENS]:
score a kit and save its receipt.

By default (`auto`) a kit small enough is read in one go (oneread.py): one Claude call with no tools and
the row's pages in the message, about a fifth to a tenth of the tokens (21 Oct 2026). A bigger kit, a reply
that is not an answers file, and any question the reading left out go to the full scorer below, and the
receipt adds both up. `--mode full` always runs the full scorer; `--mode one-read` never falls back.

The full scorer:

The session is watched as it runs (stream-json) and stopped once it has processed more than its token
budget, by default the kit's limits.json (hb kit: about 2.5 times the cost model's figure for the row),
with a turn cap as a backstop. A stopped scorer keeps what it wrote (the brief has a long row write
answers.json as it goes); the receipt says `stopped` and lists the questions `left`, for one fresh kit.
Measured 15 Oct 2026 on the RTI Rating: turns, not document length, drove the cost (Zambia 53 turns and
2.0M tokens, Canada 84 turns and 5.4M), mostly a scorer re-reading for documents the kit did not have.

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
import threading
from pathlib import Path

from . import oneread
from .claude_cli import MODEL
from .cli import command_parser
from .folder import HandbookError, read_json, write_json

CAP = 200_000
TIMEOUT_S = 1800
RECEIPT = "receipt.json"
LIMITS = "limits.json"          # written by hb kit: {"budget": tokens, "max_turns": n}
# A backstop under the budget: Canada's 117-page Act took 84 turns and 5.4M tokens (15 Oct 2026); most
# rows take 10 to 25.
DEFAULT_MAX_TURNS = 60
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


def command(kit: Path, model: str | None, effort: str | None, max_turns: int | None, cap: int = CAP) -> list[str]:
    cmd = ["claude", "-p", prompt_for(kit, cap), "--safe-mode", "--strict-mcp-config", "--mcp-config",
           '{"mcpServers":{}}', "--settings", json.dumps(settings(kit)), "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
           "--tools", TOOLS, "--allowed-tools", TOOLS, "--disallowed-tools", NO_TOOLS]
    cmd += ["--model", model or MODEL]
    if effort:
        cmd += ["--effort", effort]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    return cmd


def limits(kit: Path, budget: int | None = None, max_turns: int | None = None) -> dict:
    """{"budget", "max_turns"} for a kit: what was passed, else the kit's limits.json (written by hb kit
    from the cost model), else no budget and DEFAULT_MAX_TURNS."""
    got = read_json(kit / LIMITS, {}) or {}
    b = budget if budget is not None else got.get("budget")
    t = max_turns if max_turns is not None else got.get("max_turns")
    ok = lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0      # noqa: E731
    return {"budget": b if ok(b) else None, "max_turns": t if ok(t) else DEFAULT_MAX_TURNS}


USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def watch(lines, budget: int | None, stop) -> dict:
    """Read the session's stream-json lines as they come. Each API call is counted once (by message id):
    its input and cache tokens are exact there, its output only partly, which is a small share. When the
    count passes `budget`, `stop()` is called once. Returns {"result": the final result event or None,
    "tokens", "usage": the split by kind, "calls", "stopped": "budget" or None}."""
    seen, tokens, result, stopped = set(), 0, None, None
    usage = dict.fromkeys(USAGE_KEYS, 0)
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            result = event
        elif event.get("type") == "assistant":
            msg = event.get("message") or {}
            mid = msg.get("id") or f"call-{len(seen)}"
            if mid not in seen:
                seen.add(mid)
                for k in USAGE_KEYS:
                    n = (msg.get("usage") or {}).get(k)
                    n = n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0
                    usage[k] += n
                    tokens += n
                if budget and tokens > budget and stopped is None:
                    stopped = "budget"
                    stop()
    return {"result": result, "tokens": tokens, "usage": usage, "calls": len(seen), "stopped": stopped}


def left_questions(kit: Path) -> list[str]:
    """The kit's questions that its answers.json has no answer for (all of them when it has none)."""
    qs = [str(q.get("id")) for q in (read_json(kit / "questions.json", {}) or {}).get("questions") or []
          if isinstance(q, dict)]
    try:
        cells = (json.loads((kit / "answers.json").read_text(encoding="utf-8")) or {}).get("cells") or {}
    except (OSError, ValueError, AttributeError):
        cells = {}
    return [q for q in qs if q not in cells]


def run_scorer(kit: Path, model=None, effort=None, max_turns=None, timeout_s: int = TIMEOUT_S,
               budget: int | None = None) -> dict:
    """Run the scorer and return the session's result (also saved as KIT/receipt.json, with `kit`,
    `model`, `effort`, `budget`, `max_turns`, `stopped` and `left` added). The session is watched as it
    runs and stopped once it has processed more than its budget (`limits`); a session that reaches its
    turn cap stops itself. Either way `stopped` says so ("budget" or "turns", else null) and `left` lists
    the questions its answers.json does not answer. Raises HandbookError when claude is missing, or the
    session fails before it answers and printed nothing usable."""
    if not (kit / "brief.md").is_file():
        raise HandbookError(f"{kit} is not a kit: there is no brief.md in it. Build it with hb kit first.")
    if shutil.which("claude") is None:
        raise HandbookError("The claude command line is not installed, so no scorer can be started this way.")
    lim = limits(kit, budget, max_turns)
    cap = max(CAP, lim["budget"] or 0)
    proc = subprocess.Popen(command(kit, model, effort, lim["max_turns"], cap), cwd=kit, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
        seen = watch(proc.stdout, lim["budget"], proc.terminate)
        proc.wait()
    finally:
        timer.cancel()
    result = seen["result"]
    if result is None:
        if fired and not seen["calls"]:
            raise HandbookError(f"The scorer for {kit.name} ran for more than {int(timeout_s // 60)} minutes and was "
                                "stopped. Whatever it wrote to answers.json can still be ingested.")
        if not seen["calls"]:
            tail = "".join(err).strip().splitlines()
            raise HandbookError(f"The scorer session for {kit.name} ended without a result"
                                + (f": {tail[-1][:200]}" if tail else "."))
        # Stopped from here (its budget, or the time limit): the usage is what the stream showed.
        result = {"type": "result", "subtype": "stopped", "is_error": False, "result": "",
                  "num_turns": seen["calls"], "usage": seen["usage"], "usage_from_stream": True}
    stopped = seen["stopped"] or ("time" if fired else "turns" if result.get("subtype") == "error_max_turns" else None)
    result.update({"kit": str(kit), "model": model, "effort": effort, "budget": lim["budget"],
                   "max_turns": lim["max_turns"], "stopped": stopped, "left": left_questions(kit)})
    write_json(kit / RECEIPT, result)
    return result


MODES = ("auto", "one-read", "full")


def _add(a: dict, b: dict) -> dict:
    return {k: int(a.get(k) or 0) + int(b.get(k) or 0) for k in USAGE_KEYS}


def _full_for(kit: Path, qids: list[str], model, effort, max_turns, budget) -> dict:
    """The full scorer on only `qids` of the kit, its answers merged into the ones already in answers.json.
    questions.json is cut to those questions for the run and put back after."""
    qfile, afile = kit / "questions.json", kit / "answers.json"
    whole = qfile.read_text(encoding="utf-8")
    kept = read_json(afile, {}) or {}
    data = json.loads(whole)
    data["questions"] = [q for q in data.get("questions") or [] if str(q.get("id")) in qids]
    data["rulings"] = [r for r in data.get("rulings") or [] if set(qids) & set(r.get("questions") or [])]
    qfile.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    afile.unlink(missing_ok=True)
    try:
        result = run_scorer(kit, model, effort, max_turns, budget=budget)
    finally:
        qfile.write_text(whole, encoding="utf-8")
        new = read_json(afile, {}) or {}
        cells = {**(kept.get("cells") or {}),
                 **{q: c for q, c in (new.get("cells") or {}).items() if str(q) in qids}}
        write_json(afile, {"row": kept.get("row") or new.get("row"), "cells": cells})
    result["left"] = left_questions(kit)
    return result


def score(kit: Path, mode: str = "auto", model=None, effort=None, max_turns=None, budget=None) -> dict:
    """Score the kit (see the module docstring) and save KIT/receipt.json; returns the receipt."""
    if mode not in MODES:
        raise HandbookError(f"--mode is one of {', '.join(MODES)}, not {mode!r}.")
    if not (kit / "brief.md").is_file():
        raise HandbookError(f"{kit} is not a kit: there is no brief.md in it. Build it with hb kit first.")
    if mode == "full" or (mode == "auto" and not oneread.fits(kit)):
        return run_scorer(kit, model, effort, max_turns, budget=budget)
    try:
        result = oneread.run(kit, model, effort)
    except HandbookError as e:
        if mode == "one-read":
            raise
        result = run_scorer(kit, model, effort, max_turns, budget=budget)
        result["one_read"] = f"fell back to the full scorer: {e}"
        return result
    left = left_questions(kit)
    if left and mode == "auto":
        full = _full_for(kit, left, model, effort, max_turns, budget)
        result.update({"usage": _add(result["usage"], full.get("usage") or {}),
                       "num_turns": result["num_turns"] + int(full.get("num_turns") or 0),
                       "mode": "one read, then the full scorer", "stopped": full.get("stopped"),
                       "budget": full.get("budget"), "max_turns": full.get("max_turns"),
                       "result": result["result"] + f" The full scorer took the {len(left)} left: "
                                 + " ".join(str(full.get("result") or "").split())})
        left = full.get("left") or []
    result.update({"kit": str(kit), "left": left})
    result.setdefault("stopped", None)
    result.setdefault("budget", None)
    result.setdefault("max_turns", None)
    write_json(kit / RECEIPT, result)
    return result


STOP_WORDS = {"budget": "its token budget", "turns": "its turn limit", "time": "the time limit"}


def stop_words(result: dict) -> str | None:
    """"Stopped at its token budget (2,500,000) with 12 questions left: 31, 32, ..." or None."""
    why = result.get("stopped")
    if not why:
        return None
    left = result.get("left") or []
    limit = f" ({result['budget']:,})" if why == "budget" and result.get("budget") else ""
    return (f"Stopped at {STOP_WORDS.get(why, why)}{limit} with {len(left)} question{'s' if len(left) != 1 else ''} left"
            + (": " + ", ".join(left) if left else "") + ".")


def describe(result: dict) -> str:
    usage = result.get("usage") or {}
    fresh = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
    read, out = usage.get("cache_read_input_tokens") or 0, usage.get("output_tokens") or 0
    calls = "call" if result.get("mode") == "one read" else "turn"
    n = result.get("num_turns") or 0
    return (f"{fresh + read + out:,} tokens processed ({read:,} cache reads, {fresh:,} fresh input, {out:,} output) "
            f"over {n} {calls}{'s' if n != 1 else ''}.")


def main(argv) -> int:
    parser = command_parser("score", "Score a kit blind: in one reading when it is small enough, else the full scorer.",
                            project=False)
    parser.add_argument("--kit", required=True, help="the kit folder hb kit printed")
    parser.add_argument("--mode", default="auto", choices=MODES,
                        help="auto (default): one reading when the kit fits, else the full scorer; one-read; full")
    parser.add_argument("--model", default=None, help=f"a model id, for example claude-sonnet-5-5 (default: {MODEL}, at its default effort)")
    parser.add_argument("--effort", default=None, help="an effort level, for example low")
    parser.add_argument("--max-turns", type=int, default=None,
                        help=f"stop the scorer after this many turns (default: the kit's limits.json, else {DEFAULT_MAX_TURNS})")
    parser.add_argument("--budget", type=int, default=None,
                        help="stop the scorer once it has processed this many tokens (default: the kit's limits.json)")
    args = parser.parse_args(argv)
    kit = Path(args.kit).expanduser()
    result = score(kit, args.mode, args.model, args.effort, args.max_turns, budget=args.budget)
    reply = " ".join(str(result.get("result") or "").split())
    print(f"Scorer for {kit.name}: {reply or '(no reply)'}")
    print(describe(result))
    stop = stop_words(result)
    if stop:
        print(stop + (" Ingest what it wrote and go on to the next row: the row waits on the page for the owner"
                      " (carry on, upload a document, or leave it)." if result.get("left") else ""))
    print(f"Receipt: {kit / RECEIPT}")
    if not (kit / "answers.json").is_file():
        print("The scorer wrote no answers.json.", file=sys.stderr)
        return 1
    return 1 if result.get("is_error") and not result.get("stopped") else 0

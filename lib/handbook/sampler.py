"""Asking Claude from the local page: the `sample` capability of `hb serve`, on the owner's own plan.

    Sampler(cwd, bridge_url, system_extra).start(input, tools, tier, json_mode) -> SampleCall

Every call runs the official `claude` command as installed (found on PATH, unmodified) in print
mode. Nothing here reads, copies or passes on the owner's sign-in: `claude` signs itself in. If it
is not signed in, the call fails with `session_expired` and the page tells the owner to sign in to
Claude themselves.

The launch is kept clean and small, like `hb score`'s: no settings files, hooks, plugins, skills,
slash commands or the owner's own MCP servers, none of Claude Code's built-in tools, and a short
system prompt of our own in place of the coding one (measured 11 Oct 2026: about 1,300 tokens of
preamble against 31,000 for `hb score`'s safe-mode session). A call without page tools adds
`--safe-mode`. A call with page tools (the corner chat) cannot use safe mode, because safe mode
switches off every MCP server; it gets exactly one, `page_tools_bridge.py`, which hands each tool
call to the page and returns the page's answer, so Claude's own tool loop runs to the end in one
`claude -p` session.

Token-lean chats: the page sends the whole conversation each time (the sample contract), but a chat
keeps one Claude session. After each answer the conversation so far is remembered by a hash of its
turns; when the next call starts with those same turns, the session is resumed (`--resume`) and
only what is new is sent: the owner's new message, and the lines of the page's instructions turn
that changed (its "now" section moves with the page). Each call's processed tokens (cache reads,
fresh input, output) come from the session's own result and are reported with the answer.
"""
from __future__ import annotations
from .claude_cli import MODEL
import difflib
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent / "page_tools_bridge.py"
# Full model ids, never aliases (claude_cli.py): an older Claude Code maps an alias to an older model
# without saying so. The page's ordinary tier runs on the tool's model, like the scorers.
MODEL_FOR_TIER = {"quick": "claude-haiku-5-5", "default": MODEL, "complex": MODEL}
TIERS = tuple(MODEL_FOR_TIER)
MAX_INPUT_BYTES = 262_144
MAX_TOOLS = 48                  # our own cap (the published page's sample allows 24); the chat offers up to about 42
MAX_TURNS = 25                  # Claude's rounds in one call: enough to search the documents, read, then propose
CALL_TIMEOUT_S = 600
TOOL_WAIT_S = 160
KEEP_SESSIONS = 200

LOCAL_SYSTEM = (
    "You are Claude, answering inside the page of a benchmark tool that runs on the owner's own computer. "
    "Everything you know about the benchmark comes from the messages and from the page's tools; you have no other "
    "tools, files or web access. Follow the page's instructions in the first message. "
    "In this local page, a change the owner confirms is applied on their computer within a minute, and a re-run "
    "they confirm is scored on their computer straight away, with progress in the page's run bar. So you may say "
    "a confirmed change is being applied; never say it is done before the page shows it."
)
JSON_NOTE = "Your final message is read by a program: reply with only the JSON asked for, no other text."


class SampleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def processed(usage: dict | None) -> int:
    u = usage or {}
    return sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                             "cache_read_input_tokens", "output_tokens"))


def _norm(text: str) -> str:
    return " ".join(str(text).split())


def turns_of(value) -> list[dict]:
    """The input as [{role, content}], consecutive turns of one role joined (after the first), starting and ending
    on a user turn. Raises SampleError invalid_request / prompt_too_large."""
    if isinstance(value, str):
        value = [{"role": "user", "content": value}]
    if not isinstance(value, list) or not value:
        raise SampleError("invalid_request", "The input must be a prompt or a list of turns.")
    out: list[dict] = []
    size = 0
    for t in value:
        if not isinstance(t, dict) or t.get("role") not in ("user", "assistant") \
                or not isinstance(t.get("content"), str) or not t["content"].strip():
            raise SampleError("invalid_request", "Each turn needs a role (user or assistant) and some text.")
        size += len(t["content"].encode("utf-8"))
        # The first turn (the page's standing instructions) stays on its own, so a chat's later
        # calls can compare it and send only what changed.
        if len(out) > 1 and out[-1]["role"] == t["role"]:
            out[-1] = {"role": t["role"], "content": out[-1]["content"] + "\n\n" + t["content"]}
        else:
            out.append({"role": t["role"], "content": t["content"]})
    if size > MAX_INPUT_BYTES:
        raise SampleError("prompt_too_large", "The input is over 256 KiB.")
    if out[0]["role"] != "user" or out[-1]["role"] != "user":
        raise SampleError("invalid_request", "The turns must start and end with a user turn.")
    return out


def tools_of(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_TOOLS:
        raise SampleError("invalid_request", f"tools must be a list of at most {MAX_TOOLS}.")
    out, seen = [], set()
    for t in value:
        name = t.get("name") if isinstance(t, dict) else None
        if not isinstance(name, str) or not name or len(name) > 64 or name in seen \
                or not all(c.isalnum() or c in "_-" for c in name):
            raise SampleError("invalid_request", f"A tool's name is missing, repeated or not allowed: {name!r}.")
        seen.add(name)
        schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object", "properties": {}}
        out.append({"name": name, "description": str(t.get("description") or "")[:1024], "inputSchema": schema})
    return out


def key_of(turns: list[dict]) -> str:
    return hashlib.sha1(json.dumps([[t["role"], _norm(t["content"])] for t in turns]).encode()).hexdigest()


def head_changes(old: str, new: str) -> str | None:
    """The lines of the instructions turn that are new since `old`, or None when nothing changed.
    When most of it changed, the whole of `new`."""
    if _norm(old) == _norm(new):
        return None
    a, b = old.splitlines(), new.splitlines()
    added = [line for line in difflib.ndiff(a, b) if line.startswith("+ ")]
    if len(added) > max(8, len(b) // 3):
        return new
    return "\n".join(line[2:] for line in added)


def transcript(turns: list[dict]) -> str:
    who = {"user": "Owner", "assistant": "You (earlier)"}
    return "\n\n".join(f"{who[t['role']]}: {t['content']}" for t in turns)


class SampleCall:
    """One call: its events for the page to poll, and the page tools it is waiting on."""

    def __init__(self, tools: list[dict], tier: str):
        self.id = secrets.token_hex(8)
        self.secret = secrets.token_urlsafe(24)
        self.tools = tools
        self.tier = tier
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.waiting: dict[str, dict] = {}
        self.proc: subprocess.Popen | None = None
        self.finished = False
        self.cancelled = False
        self.created = time.time()

    def emit(self, event: dict) -> None:
        with self.cond:
            if self.finished:
                return
            event["n"] = len(self.events)
            self.events.append(event)
            if event["type"] in ("done", "error"):
                self.finished = True
                for w in self.waiting.values():
                    w["result"] = {"ok": False, "error": "The call ended."}
                    w["ready"].set()
            self.cond.notify_all()

    def events_after(self, after: int, wait_s: float) -> list[dict]:
        deadline = time.time() + wait_s
        with self.cond:
            while len(self.events) <= after and not self.finished and time.time() < deadline:
                self.cond.wait(timeout=max(0.05, deadline - time.time()))
            return self.events[after:] if after >= 0 else list(self.events)

    def tool_request(self, name: str, tool_input: dict) -> dict:
        """Called from the bridge's HTTP request: hand the call to the page and wait for its answer."""
        if not any(t["name"] == name for t in self.tools):
            return {"ok": False, "error": f"There is no page tool called {name}."}
        tid = secrets.token_hex(6)
        waiter = {"ready": threading.Event(), "result": None}
        with self.cond:
            if self.finished:
                return {"ok": False, "error": "The call ended."}
            self.waiting[tid] = waiter
        self.emit({"type": "tool", "id": tid, "name": name, "input": tool_input if isinstance(tool_input, dict) else {}})
        if not waiter["ready"].wait(TOOL_WAIT_S):
            result = {"ok": False, "error": "The page did not answer within 150 seconds."}
        else:
            result = waiter["result"] or {"ok": False, "error": "No result."}
        with self.cond:
            self.waiting.pop(tid, None)
        return result

    def tool_result(self, tid: str, result: dict) -> bool:
        with self.cond:
            waiter = self.waiting.get(tid)
        if not waiter:
            return False
        waiter["result"] = result
        waiter["ready"].set()
        return True

    def cancel(self) -> None:
        self.cancelled = True
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        self.emit({"type": "error", "code": "cancelled", "message": "Stopped.", "text": None})


class Sampler:
    """Starts calls and keeps each chat's Claude session for the next turn."""

    def __init__(self, cwd: Path, bridge_url, system_extra="", log=None, claude: str = "claude"):
        self.cwd = Path(cwd)
        self.bridge_url = bridge_url          # callable: call id -> URL of that call's bridge
        self.system_extra = system_extra      # text, or a callable read at each call
        self.log = log or (lambda entry: None)
        self.claude = claude
        self.sessions: dict[str, dict] = {}
        self.calls: dict[str, SampleCall] = {}
        self._lock = threading.Lock()

    # -- planning ------------------------------------------------------------------------------

    def plan(self, turns: list[dict], with_tools: bool) -> dict:
        """What to send: {prompt, resume (session id or None), head, convo}."""
        head, convo = turns[0]["content"], turns[1:]
        if not convo:
            return {"prompt": head, "resume": None, "head": head, "convo": convo}
        with self._lock:
            for k in range(len(convo) - 1, 0, -1):
                if convo[k - 1]["role"] != "assistant":
                    continue
                known = self.sessions.get(key_of(convo[:k]))
                if not known:
                    continue
                new = convo[k:]
                parts = []
                changed = head_changes(known["head"], head)
                if changed:
                    parts.append("(The page's instructions have changed since your last reply. The changed lines now read:\n"
                                 + changed + ")")
                if len(new) == 1:
                    parts.append(new[0]["content"])
                else:
                    parts.append("The conversation since your last reply:\n\n" + transcript(new))
                return {"prompt": "\n\n".join(parts), "resume": known["sid"], "head": head, "convo": convo}
        if len(convo) == 1:
            prompt = head + "\n\n" + convo[0]["content"]
        else:
            prompt = (head + "\n\nThe conversation so far:\n\n" + transcript(convo[:-1])
                      + "\n\nThe owner's new message:\n" + convo[-1]["content"])
        return {"prompt": prompt, "resume": None, "head": head, "convo": convo}

    def remember(self, plan: dict, answer: str, session_id: str | None) -> None:
        if not session_id or not plan["convo"]:
            return
        with self._lock:
            self.sessions[key_of(plan["convo"] + [{"role": "assistant", "content": answer}])] = \
                {"sid": session_id, "head": plan["head"]}
            while len(self.sessions) > KEEP_SESSIONS:
                self.sessions.pop(next(iter(self.sessions)))

    # -- running -------------------------------------------------------------------------------

    def command(self, call: SampleCall, plan: dict, model: str | None, json_mode: bool, mcp_file: Path | None) -> list[str]:
        extra = self.system_extra() if callable(self.system_extra) else self.system_extra
        extra = (extra or "").strip() if call.tools else ""      # the page's chat instructions: the chat only
        system = LOCAL_SYSTEM + ("\n\n" + extra if extra else "")
        if json_mode:
            system += "\n\n" + JSON_NOTE
        cmd = [self.claude, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--setting-sources", "", "--settings", json.dumps({"disableAllHooks": True}),
               "--strict-mcp-config", "--mcp-config", str(mcp_file) if mcp_file else '{"mcpServers":{}}',
               "--tools", "", "--permission-mode", "dontAsk", "--disable-slash-commands",
               "--max-turns", str(MAX_TURNS), "--system-prompt", system]
        if call.tools:
            cmd += ["--allowed-tools", ",".join("mcp__page__" + t["name"] for t in call.tools)]
        else:
            cmd += ["--safe-mode", "--no-session-persistence"]
        cmd += ["--model", model or MODEL]
        if plan["resume"]:
            cmd += ["--resume", plan["resume"]]
        return cmd

    def start(self, value, tools=None, tier: str | None = None, json_mode: bool = False) -> SampleCall:
        turns = turns_of(value)
        tool_list = tools_of(tools)
        tier = tier if tier in TIERS else "default"
        if shutil.which(self.claude) is None:
            raise SampleError("unavailable", "The claude command line is not installed on this computer.")
        call = SampleCall(tool_list, tier)
        with self._lock:
            self.calls[call.id] = call
            for old in [c for c in self.calls.values() if c.finished and time.time() - c.created > 900]:
                self.calls.pop(old.id, None)
        threading.Thread(target=self._run, args=(call, turns, json_mode), daemon=True).start()
        return call

    def _run(self, call: SampleCall, turns: list[dict], json_mode: bool) -> None:
        plan = self.plan(turns, bool(call.tools))
        model = MODEL_FOR_TIER.get(call.tier)
        tmp = Path(tempfile.mkdtemp(prefix="hb-sample-"))
        try:
            mcp_file = None
            if call.tools:
                (tmp / "tools.json").write_text(json.dumps(call.tools), encoding="utf-8")
                mcp_file = tmp / "mcp.json"
                mcp_file.write_text(json.dumps({"mcpServers": {"page": {
                    "command": sys.executable, "args": ["-I", str(BRIDGE)],
                    "env": {"HB_BRIDGE_URL": self.bridge_url(call.id), "HB_BRIDGE_SECRET": call.secret,
                            "HB_BRIDGE_TOOLS": str(tmp / "tools.json")}}}}), encoding="utf-8")
                os.chmod(mcp_file, 0o600)
            result = self._once(call, plan, model, json_mode, mcp_file)
            if result is None and model and not call.cancelled:     # the model was refused: never fall back
                call.emit({"type": "error", "code": "unavailable", "text": None,       # to an older one quietly
                           "message": "Claude on this computer is too old for the model the tool uses. "
                                      "Run `claude update` (or ask Claude to do it), then try again."})
            if result is None:
                return
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _once(self, call: SampleCall, plan: dict, model, json_mode, mcp_file):
        """One `claude -p`. Emits events; returns the result dict, or None when the model was refused
        (and nothing was emitted yet, so the caller may try again without --model)."""
        self.cwd.mkdir(parents=True, exist_ok=True)
        cmd = self.command(call, plan, model, json_mode, mcp_file)
        started = time.time()
        try:
            proc = subprocess.Popen(cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8")
        except OSError as e:
            call.emit({"type": "error", "code": "unavailable", "message": f"claude could not start: {e}", "text": None})
            return {}
        call.proc = proc
        timer = threading.Timer(CALL_TIMEOUT_S, lambda: proc.poll() is None and proc.terminate())
        timer.start()
        stderr_lines: list[str] = []
        threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True).start()
        try:
            proc.stdin.write(plan["prompt"])
            proc.stdin.close()
        except OSError:
            pass
        messages: dict[str, list[str]] = {}       # assistant message id -> its text blocks
        order: list[str] = []
        live: list[str] = []                      # text streamed so far, one item per round
        init: dict = {}
        result: dict | None = None
        shown = ""
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "system" and ev.get("subtype") == "init":
                init = ev
            elif kind == "stream_event":
                e = ev.get("event") or {}
                if e.get("type") == "message_start":
                    live.append("")
                elif e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                    if not live:
                        live.append("")
                    live[-1] += e["delta"].get("text") or ""
                    text = "\n\n".join(t.strip("\n") for t in live if t.strip())
                    if text.strip() and text != shown:
                        call.emit({"type": "text", "text": text})
                        shown = text
            elif kind == "assistant":
                msg = ev.get("message") or {}
                mid = msg.get("id") or f"m{len(order)}"
                if mid not in messages:
                    messages[mid] = []
                    order.append(mid)
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        messages[mid].append(block["text"])
            elif kind == "result":
                result = ev
        proc.wait()
        timer.cancel()
        if call.cancelled:
            return {}
        text = "\n\n".join("".join(messages[m]).strip() for m in order if "".join(messages[m]).strip()) \
            or (result or {}).get("result") or shown
        usage = (result or {}).get("usage") or {}
        record = {"call": call.id, "tier": call.tier, "model": model, "resumed": bool(plan["resume"]),
                  "tools": len(call.tools), "turns": (result or {}).get("num_turns"),
                  "processed": processed(usage), "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                  "fresh_input": int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0),
                  "output": int(usage.get("output_tokens") or 0), "seconds": round(time.time() - started, 1),
                  "api_key_source": init.get("apiKeySource")}
        problem = None
        if result is None or result.get("is_error"):
            detail = " ".join(((result or {}).get("result") or "".join(stderr_lines[-5:]) or "").split())[:300]
            low = detail.lower()
            if model and "model" in low and any(w in low for w in ("not found", "not available", "invalid", "access", "not allowed")):
                return None
            if "log in" in low or "login" in low or "not logged" in low or "authenticat" in low or "sign in" in low:
                problem = ("session_expired", "Claude is not signed in on this computer. Open Claude and sign in, then try again.")
            elif "limit" in low and ("usage" in low or "rate" in low):
                problem = ("rate_limited", "Your Claude plan's limit was reached for now. Try again later.")
            elif result is not None and result.get("subtype") == "error_max_turns" and text.strip():
                problem = None
            else:
                problem = ("upstream_error", "Claude stopped before answering" + (f": {detail}" if detail else "."))
        record["ok"] = problem is None
        self.log({"kind": "sample", **record})
        if problem:
            call.emit({"type": "error", "code": problem[0], "message": problem[1], "text": text or None,
                       "usage": record})
            return {}
        if not text.strip():
            call.emit({"type": "error", "code": "empty_completion", "message": "Claude sent back nothing.", "text": None,
                       "usage": record})
            return {}
        if call.tools:
            self.remember(plan, text, (result or {}).get("session_id") or init.get("session_id"))
        truncated = (result or {}).get("subtype") == "error_max_turns" or (result or {}).get("stop_reason") == "max_tokens"
        call.emit({"type": "done", "text": text, "truncated": bool(truncated), "modelTierApplied": call.tier,
                   "usage": record})
        return result or {}

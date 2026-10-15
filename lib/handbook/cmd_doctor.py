"""hb doctor [--folder DIR] [--probe] [--json]: is this computer ready to run the benchmark tool?

Each check is one line an owner can act on: what was found, and what to do if it is not ready.
The local checks cost nothing. `--probe` also starts one tiny Claude Code session in the
background, as scorers are started, and reports what it was given: whether it signed in with the
owner's Claude plan or an API key (billed separately), the model, and whether the owner's
organisation lets it fetch web pages. That costs a few thousand tokens of the plan.

The tool only ever calls the `claude` command as Anthropic installed it, and never reads or
copies the sign-in.
"""
from __future__ import annotations
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .cli import command_parser
from .folder import default_root, protected_folder, synced_folder_warning

OK, WARN, BLOCKED = "ok", "attention", "blocked"
INSTALL_CLAUDE = "curl -fsSL https://claude.ai/install.sh | bash"
SIGN_IN = "claude auth login --claudeai"
PROBE_URL = "https://example.com"
PROBE_WORDS = "Example Domain"
PROBE_PROMPT = (f"Fetch {PROBE_URL} with WebFetch and reply with its main heading only. "
                "If you cannot fetch it, reply with the single word BLOCKED and the reason.")
PROBE_TIMEOUT_S = 180


def check(name: str, state: str, says: str, fix: str | None = None) -> dict:
    return {"check": name, "state": state, "says": says, "fix": fix}


def python_checks() -> list[dict]:
    found = [check("Python", OK, f"Python {sys.version.split()[0]}.")]
    for module, job in (("pypdfium2", "Reading PDFs"), ("openpyxl", "Excel downloads"),
                        ("PIL", "Pictures and scanned pages"), ("xlrd", "Old Excel (.xls) files"),
                        ("olefile", "Outlook (.msg) messages")):
        try:
            importlib.import_module(module)
            found.append(check(job, OK, "Ready."))
        except ImportError:
            found.append(check(job, BLOCKED, f"{module} is not installed.",
                               "Run the tool through its launcher, handbook/lib/hb, which installs it."))
    from . import ocr
    reader = ocr.engine()
    found.append(check("Words in pictures", OK, "Ready (the Mac's own text reader)." if reader == "vision"
                       else "Ready (tesseract)." if reader else "", None) if reader else
                 check("Words in pictures", WARN, "No program on this computer can read the words in a picture, so "
                       "pictures and scanned pages are kept and shown to the scorer but their text is empty.",
                       "On a Mac this is built in. Elsewhere, install tesseract."))
    return found


def tool_checks() -> list[dict]:
    """The tool itself must not sit in Desktop, Documents or Downloads (see `protected_folder`)."""
    here = Path(__file__).resolve().parents[2]
    guarded = protected_folder(here)
    if guarded:
        return [check("Tool folder", WARN, f"The tool is in your {guarded} folder ({here}), which macOS "
                      "guards, so the double-click app cannot start it.",
                      "Install it again with the set-up prompt; it goes in ~/.benchmark-tool.")]
    return []


CHROME_APPS = (Path("/Applications/Google Chrome.app"), Path.home() / "Applications" / "Google Chrome.app")


def render_checks() -> list[dict]:
    """Pages that draw their words with a script are read by opening them in the owner's own Chrome
    (render.py, through Playwright, which the launcher installs). Without Chrome they come from the
    web archive when it has them, and are otherwise left out: every quote from such a page would
    then be a quote not found."""
    from . import render
    has_chrome = any(p.is_dir() for p in CHROME_APPS)
    if not has_chrome:
        return [check("Reading script-built pages", WARN, "Google Chrome is not installed, so pages that build "
                      "their text with a script can only come from the web archive, and some will be left out.",
                      "Install Google Chrome (google.com/chrome); the tool opens such pages in it, out of sight.")]
    if render.renderer() is None:
        return [check("Reading script-built pages", WARN, "Chrome is here, but the part that drives it is missing, "
                      "so pages that build their text with a script can only come from the web archive.",
                      "Run the tool through its launcher (lib/hb), which installs it.")]
    return [check("Reading script-built pages", OK, "Pages that build their text with a script are read in Chrome.")]


def claude_path() -> str | None:
    return shutil.which("claude") or next(
        (str(p) for p in [Path.home() / ".local" / "bin" / "claude"] if p.is_file()), None)


def auth_status(path: str) -> dict | None:
    """`claude auth status --json` (free: no session): loggedIn, authMethod, subscriptionType, ...
    None when it cannot be read. Only these three fields are used; the email is never shown."""
    try:
        got = subprocess.run([path, "auth", "status", "--json"], capture_output=True, text=True, timeout=30)
        return json.loads(got.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def sign_in_checks(status: dict | None) -> list[dict]:
    if status is None:
        return []
    if not status.get("loggedIn"):
        return [check("Sign-in", BLOCKED, "The Claude Code command line is not signed in.",
                      f"Sign in with your Claude account: {SIGN_IN} (it opens Claude's own sign-in page).")]
    if status.get("authMethod") != "claude.ai":
        return [check("Sign-in", WARN, f"Signed in through {status.get('authMethod') or 'an API account'}, billed "
                      "at API rates rather than your Claude plan.", f"Sign in with your Claude account: {SIGN_IN}")]
    plan = str(status.get("subscriptionType") or "").strip()
    return [check("Sign-in", OK, "Signed in with your Claude account" + (f" ({plan.title()} plan)." if plan else "."))]


def claude_checks(env=os.environ) -> list[dict]:
    found = []
    path = claude_path()
    if path is None:
        found.append(check("Claude Code command line", BLOCKED, "Not installed. The tool runs Claude through it, "
                           "on your own Claude plan.", f"Install it: {INSTALL_CLAUDE}"))
    else:
        try:
            version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            version = ""
        found.append(check("Claude Code command line", OK, version or f"Installed at {path}."))
        found += sign_in_checks(auth_status(path))
    if env.get("ANTHROPIC_API_KEY"):
        found.append(check("Billing", WARN, "An Anthropic API key is set on this computer (ANTHROPIC_API_KEY), so "
                           "Claude would bill that key at API rates instead of using your Claude plan.",
                           "Remove ANTHROPIC_API_KEY from your shell settings, unless you mean to pay by API key."))
    return found


def folder_checks(folder: str | None) -> list[dict]:
    target = Path(folder).expanduser() if folder else default_root()
    found = []
    warning = synced_folder_warning(target)
    guarded = protected_folder(target)
    if warning:
        found.append(check("Project folder", WARN, warning, f"Choose a folder such as {default_root()}."))
    elif guarded:
        warning = (f"{target} is in your {guarded} folder, which macOS guards: the double-click "
                   "app that reopens the benchmark would be refused there.")
        found.append(check("Project folder", WARN, warning, f"Choose a folder such as {default_root()}."))
    existing = next((p for p in [target, *target.parents] if p.exists()), None)
    if existing is None or not os.access(existing, os.W_OK):
        found.append(check("Project folder", BLOCKED, f"The tool cannot write to {target}.",
                           "Choose a folder in your home folder."))
    elif not warning:
        found.append(check("Project folder", OK, f"{target} is fine."))
    return found


def probe_command(path: str, prompt: str = PROBE_PROMPT) -> list[str]:
    return [path, "-p", prompt, "--safe-mode", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands", "--no-session-persistence", "--output-format", "stream-json",
            "--verbose", "--permission-mode", "dontAsk", "--tools", "WebFetch", "--allowed-tools", "WebFetch",
            "--max-turns", "3"]


def read_probe(lines) -> tuple[dict, dict]:
    """(the session's init message, its result message) from stream-json output."""
    init, result = {}, {}
    for line in lines:
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if message.get("type") == "system" and message.get("subtype") == "init":
            init = message
        elif message.get("type") == "result":
            result = message
    return init, result


def judge_probe(init: dict, result: dict, stderr: str = "") -> list[dict]:
    """Plain-words checks from what the probe session reported."""
    if not init:
        tail = (stderr or "").strip().splitlines()
        return [check("Claude session", BLOCKED, "Claude Code could not start a session"
                      + (f": {tail[-1][:200]}" if tail else "."),
                      "Open the Claude desktop app and check you are signed in, then try again.")]
    found = []
    source = init.get("apiKeySource") or "none"
    if source == "none":
        found.append(check("Billing", OK, "Signed in with your Claude plan, so runs use your plan's allowance."))
    else:
        found.append(check("Billing", WARN, f"Claude Code is using an API key ({source}), billed at API rates "
                           "rather than your Claude plan.", "Sign in with your Claude account instead (run "
                           "`claude`, then /login), unless you mean to pay by API key."))
    if init.get("model"):
        found.append(check("Model", OK, f"Claude runs as {init['model']}."))
    usage = result.get("usage") or {}
    spent = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                            "cache_read_input_tokens", "output_tokens"))
    text = str(result.get("result") or "")
    denied = [d.get("tool_name") for d in result.get("permission_denials") or [] if isinstance(d, dict)]
    if result.get("is_error") and not text:
        found.append(check("Claude session", BLOCKED, f"The session stopped with an error "
                           f"({result.get('api_error_status') or result.get('subtype') or 'unknown'}).",
                           "Check you are signed in and have allowance left (Settings > Usage in the Claude app)."))
    elif "WebFetch" not in (init.get("tools") or []) or "WebFetch" in denied:
        found.append(check("Web access", WARN, "Your organisation's settings stop Claude from fetching web pages, "
                           "so it cannot find documents itself.", "Upload the documents yourself, or ask your "
                           "Claude administrator to allow WebFetch and WebSearch for Claude Code."))
    elif PROBE_WORDS.lower() in text.lower():
        found.append(check("Web access", OK, "Claude can fetch web pages."))
    else:
        found.append(check("Web access", WARN, f"Claude could not fetch a test page: {text[:200] or 'no reply'}.",
                           "Check the internet connection; your organisation may block Claude's web access."))
    if spent:
        found.append(check("Probe cost", OK, f"This check used {spent:,} tokens of your plan."))
    return found


def probe() -> list[dict]:
    path = claude_path()
    if path is None:
        return []
    try:
        proc = subprocess.run(probe_command(path), capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
                              cwd=Path.home())
    except subprocess.TimeoutExpired:
        return [check("Claude session", BLOCKED, f"A test session ran for over {PROBE_TIMEOUT_S // 60} minutes.",
                      "Check the internet connection and try again.")]
    init, result = read_probe(proc.stdout.splitlines())
    return judge_probe(init, result, proc.stderr)


def main(argv) -> int:
    parser = command_parser("doctor", "Check this computer is ready to run the benchmark tool.", project=False)
    parser.add_argument("--folder", help=f"where the project will be kept (default: {default_root()})")
    parser.add_argument("--probe", action="store_true",
                        help="also start one tiny Claude session to check sign-in, model and web access "
                             "(uses a few thousand tokens of your plan)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    checks = python_checks() + tool_checks() + render_checks() + claude_checks() + folder_checks(args.folder)
    if args.probe and not any(c["state"] == BLOCKED and c["check"] == "Claude Code command line" for c in checks):
        checks += probe()
    ready = not any(c["state"] == BLOCKED for c in checks)
    if args.json:
        print(json.dumps({"ready": ready, "checks": checks}, indent=1, ensure_ascii=False))
    else:
        mark = {OK: "ok  ", WARN: "note", BLOCKED: "STOP"}
        for c in checks:
            print(f"{mark[c['state']]}  {c['check']}: {c['says']}" + (f"\n      What to do: {c['fix']}" if c["fix"] and c["state"] != OK else ""))
        print("Ready." if ready else "Not ready yet: fix the lines marked STOP.")
    return 0 if ready else 1

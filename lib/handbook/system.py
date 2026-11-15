"""What differs between a Mac, Windows and Linux, in one place.

The tool was built on a Mac. Everything here keeps the Mac doing exactly what it did and gives
Windows and Linux their own way of doing the same thing:

- `alive(pid)`: whether a process is running. On Windows `os.kill(pid, 0)` does not ask, it ends
  the process, so there the question goes to the Windows API instead.
- `own_group()` and `stop_tree(p)`: start a command in a process group of its own and stop it with
  everything it started (a `claude -p` under an `hb` command). A new session and `killpg` on a Mac
  and Linux; a new process group and `taskkill /T` on Windows.
- `app_browser()`: Chrome or Edge, for the page in a window of its own, off the Mac.
- `claude()`: the claude command line. On Windows a bare "claude" is not enough when it came from
  npm (claude.cmd), so the full path is used there.
- `rule_path(path)`: a path as Claude Code's permission rules write it. On Windows they use POSIX
  form, C:\\Users\\a becoming /c/Users/a.
- `sandboxed()`: whether Claude Code's Bash sandbox exists here. It does on a Mac and Linux (with
  bubblewrap and socat) and not on native Windows, where the sessions get no shell at all instead.

Tests patch `sys.platform` (and `shutil.which`, `os.environ`) to try the Windows and Linux paths on a Mac.
"""
from __future__ import annotations
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path, PureWindowsPath


def windows() -> bool:
    return sys.platform.startswith("win")


def mac() -> bool:
    return sys.platform == "darwin"


def linux() -> bool:
    return sys.platform.startswith("linux")


def sandboxed() -> bool:
    """Whether Claude Code's Bash sandbox runs here: on a Mac and Linux, not on native Windows."""
    return not windows()


# ---- Processes ---------------------------------------------------------------------------------

_STILL_ACTIVE = 259
_QUERY_LIMITED = 0x1000          # PROCESS_QUERY_LIMITED_INFORMATION


def _alive_windows(pid: int) -> bool:
    import ctypes
    kernel = ctypes.windll.kernel32                                   # type: ignore[attr-defined]
    handle = kernel.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def alive(pid) -> bool:
    """Whether process `pid` is running. Never sends it a signal on Windows (os.kill would end it)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if windows():
        try:
            return _alive_windows(pid)
        except (OSError, AttributeError, ImportError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def own_group() -> dict:
    """Popen keywords that start a command in a process group of its own, so it can be stopped with
    whatever it starts, and Ctrl-C in the window that started the server does not reach it."""
    if windows():
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


def stop_tree(p: subprocess.Popen) -> None:
    """Stop `p` and everything it started (started with `own_group`)."""
    if windows():
        try:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=30)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            return
        except (OSError, AttributeError):
            pass
    try:
        p.terminate()
    except OSError:
        pass


# ---- Programs ----------------------------------------------------------------------------------

def _env_dirs(*names: str) -> list[Path]:
    return [Path(os.environ[n]) for n in names if os.environ.get(n)]


def app_browser() -> str | None:
    """Chrome, else Edge, as a program that takes --app=URL, off the Mac (the Mac opens Chrome's
    .app itself, see cmd_serve.show). None when neither is found."""
    if windows():
        tails = [Path("Google") / "Chrome" / "Application" / "chrome.exe",
                 Path("Microsoft") / "Edge" / "Application" / "msedge.exe"]
        roots = _env_dirs("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")
        for tail in tails:                              # Chrome anywhere first, then Edge
            for root in roots:
                if (root / tail).is_file():
                    return str(root / tail)
        return shutil.which("chrome") or shutil.which("msedge")
    if linux():
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                     "microsoft-edge", "microsoft-edge-stable"):
            found = shutil.which(name)
            if found:
                return found
    return None


def claude() -> str:
    """The claude command line to start. "claude" on a Mac and Linux, as before; on Windows the full
    path (claude.exe from Anthropic's installer, or claude.cmd from npm), which Windows cannot find
    from the bare name for a .cmd."""
    if not windows():
        return "claude"
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude.exe"
    return str(local) if local.is_file() else "claude"


def claude_installed() -> bool:
    if windows():
        c = claude()
        return c != "claude" or shutil.which("claude") is not None
    return shutil.which("claude") is not None


# ---- Paths in Claude Code's permission rules ---------------------------------------------------

def rule_path(path) -> str:
    """`path` as Claude Code's permission rules write it. Unchanged on a Mac and Linux; on Windows in
    POSIX form ("C:\\Users\\a" -> "/c/Users/a"), which is how Claude Code matches paths there."""
    if not windows():
        return str(path)
    p = PureWindowsPath(str(path))
    drive = p.drive.rstrip(":").lower()
    parts = [x for x in p.parts[1:]] if p.drive else list(p.parts)
    body = "/".join(x.strip("\\/") for x in parts if x.strip("\\/"))
    return f"/{drive}/{body}" if drive else "/" + body


def windows_drives() -> list[str]:
    """The drive letters that exist here ("C:", "D:"), for the scorer's rules on Windows."""
    if not windows():
        return []
    return [f"{c}:" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{c}:\\")]


def off_limits_windows(kit: Path, also: list[Path] | None = None) -> list[str]:
    """The folders a scorer must not read on Windows, in rule form (each "/c/..." as `rule_path`
    gives). The kit sits in the temp folder, which on Windows is inside the home folder
    (C:\\Users\\a\\AppData\\Local\\Temp), so the Mac's "all of /Users" would shut the kit too. Instead
    every folder beside the kit's own path is refused, from the drive down to the temp folder: other
    drives whole, the other folders at the top of the kit's drive, the other people's homes, the
    other folders in this home (Desktop, Documents, OneDrive, Dropbox...), and so on, plus `also`
    (the project, its held-back folder, the tool) by name. Folders that cannot be listed are skipped."""
    kit = Path(kit)
    chain = [kit, *kit.parents]                      # kit, Temp, Local, AppData, a, Users, C:\
    on_path = {str(p).lower() for p in chain}
    deny: list[str] = []
    kit_drive = PureWindowsPath(str(kit)).drive.upper()
    for drive in windows_drives():
        if drive.upper() != kit_drive:
            deny.append(rule_path(drive + "\\"))
    temp_root = None
    for p in chain:
        if p.name.lower() == "temp":
            temp_root = p
            break
    for parent in reversed(chain[1:]):                # from the drive root down
        if temp_root is not None and parent == temp_root:
            break                                    # inside the temp folder: the kits root only
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            if str(child).lower() not in on_path:
                try:
                    if child.is_dir():
                        deny.append(rule_path(child))
                except OSError:
                    continue
    for extra in also or []:
        deny.append(rule_path(extra))
    seen, out = set(), []
    for d in deny:
        key = d.rstrip("/").lower()
        if key and key not in seen:
            seen.add(key)
            out.append(d.rstrip("/"))
    return out


# ---- Background sessions that change the project (set-up draft, chat jobs) -----------------------

# Without the sandbox (Windows) these sessions may run the tool's own commands and nothing else:
# `hb ...` in Git Bash (lib/hb) or PowerShell (lib/hb.cmd), found through PATH (`session_env`).
# A compound command is refused unless every part matches, so `hb validate; curl ...` is refused.
HB_ONLY = ("Bash(hb *)", "PowerShell(hb *)")


def hb_call(python: str, hb_py: Path) -> str:
    """How a session's prompt says to run an hb command: the tool's own Python on hb.py where the
    sandbox is, plain `hb` on Windows (the one command its rules allow)."""
    return f'"{python}" "{hb_py}"' if sandboxed() else "hb"


def session_env(lib: Path) -> dict | None:
    """The environment for such a session: unchanged where the sandbox is (None), and on Windows with
    the tool's lib folder first on PATH, so `hb` is found, and Python in UTF-8."""
    if sandboxed():
        return None
    env = dict(os.environ)
    env["PATH"] = str(lib) + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    return env


def key_deny_rules() -> list[str]:
    """The file tools refused on the Full edition's download key file (edition.key_file), as absolute rules. A
    background session that reads saved documents has no reason to open it, and on Windows, where nothing fences
    the hb commands it may run, a document's injected words could otherwise have it read the key and send it out."""
    from .edition import key_file
    return [f"{tool}(/{rule_path(key_file())})" for tool in ("Read", "Grep", "Edit", "Write")]


def edit_rules(folders) -> list[str]:
    """Edit and Write allowed inside each folder, as absolute rules: "//c/Users/a/proj/**" (a rule
    starting with one "/" is read from the working folder, two make it absolute)."""
    return [f"{tool}(/{rule_path(w)}/**)" for w in folders for tool in ("Edit", "Write")]

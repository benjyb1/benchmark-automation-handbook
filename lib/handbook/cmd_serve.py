"""hb serve [--project DIR] [--port N] [--no-browser] [--app-window]: open the project's page in your own browser.

The page works on its own from then on: what you save there is applied at once, re-runs are scored
on this computer, and the corner chat asks Claude on your own plan. Close the tab to pause; open
the address again (or run `hb serve` again) to carry on. Ctrl-C, or closing the terminal, stops it.

`hb serve` alone opens the last project used (remembered in ~/.handbook/serve.json). When the
project is already being served (the launcher app may start it a second time), it opens the browser
at the running page and exits 0. It never reads from the keyboard, so it runs fine with no terminal
attached (the launcher sends its output to `<project>/.serve.log`). See serve.py.
"""
from __future__ import annotations
import hashlib
import json
import os
import signal
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

from .cli import command_parser, open_project
from .folder import HandbookError, read_json, write_json
from .localstore import Config, home
from .serve import LocalServer

DEFAULT_PORT = 8470


def project_key(path) -> str:
    return hashlib.sha1(str(Path(path).resolve()).encode("utf-8")).hexdigest()[:16]


def running_file(path) -> Path:
    """Where a running server for this project leaves its port and token: under the home folder,
    readable by this user only (never in the project folder)."""
    return home() / "running" / f"{project_key(path)}.json"


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def already_running(path) -> str | None:
    """The first address of a server already serving this project, or None."""
    info = read_json(running_file(path), None)
    if not isinstance(info, dict) or not _alive(info.get("pid")):
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(info['port'])}/hb-alive", timeout=2) as res:
            answer = json.loads(res.read().decode("utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if answer.get("project") != project_key(path):
        return None
    return f"http://127.0.0.1:{int(info['port'])}/?t={info['token']}"


CHROME = Path("/Applications/Google Chrome.app")


def show(url: str, app_window: bool = False) -> None:
    """Open the page: in a window of its own (Chrome's app mode, no tabs or address bar) when asked
    and Chrome is installed, otherwise in the default browser."""
    if app_window and sys.platform == "darwin" and CHROME.is_dir():
        if subprocess.run(["open", "-na", str(CHROME), "--args", f"--app={url}"]).returncode == 0:
            return
    webbrowser.open(url)


def main(argv) -> int:
    parser = command_parser("serve", "Open the project's page in your browser, served from this computer.")
    parser.add_argument("--port", type=int, default=None,
                        help=f"the port on 127.0.0.1 (default {DEFAULT_PORT}, or a free one if that is taken)")
    parser.add_argument("--no-browser", action="store_true", help="print the address but don't open the browser")
    parser.add_argument("--app-window", action="store_true",
                        help="open the page in its own window (Chrome's app mode) when Chrome is installed")
    args = parser.parse_args(argv)
    config = Config()
    where = args.project or config.last_project()
    if not where:
        raise HandbookError("Say which project to open: hb serve --project <folder>.")
    project = open_project(where)
    name = project.read("project").get("name") or project.path.name

    running = already_running(project.path)
    if running:
        config.remember(project.path, name)
        print(f"{name} is already open at {running}", flush=True)
        if not args.no_browser:
            show(running, args.app_window)
        return 0

    print(f"Building the page for {name}...", flush=True)
    try:
        server = LocalServer(project, args.port if args.port is not None else DEFAULT_PORT, config)
    except OSError:
        if args.port is not None:
            raise HandbookError(f"Port {args.port} is in use. Pick another with --port, or leave it out.") from None
        server = LocalServer(project, 0, config)
    server.alive_key = project_key(project.path)
    if not server.worker.build():
        print("The page could not be rebuilt; showing the last build.", flush=True)
    config.remember(project.path, name)
    marker = running_file(project.path)
    write_json(marker, {"pid": os.getpid(), "port": server.port, "token": server.token,
                        "project": str(project.path.resolve())})
    os.chmod(marker, 0o600)
    server.start_background()
    url = server.first_url()
    print(f"Your benchmark is open at {url}", flush=True)
    print("Keep this running while you work. Close the browser tab to pause; press Ctrl-C here to stop.",
          flush=True)
    if not args.no_browser:
        show(url, args.app_window)

    def stop(signum, frame):
        for sig in (signal.SIGTERM, signal.SIGINT):     # once is enough: a second signal must not
            signal.signal(sig, signal.SIG_IGN)          # cut the clean-up short
        raise KeyboardInterrupt
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        signal.signal(signal.SIGTERM, stop)
    except ValueError:                     # not the main thread
        before = {}
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopped. Run hb serve again to carry on where you left off.", flush=True)
    finally:
        if before:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        server.stop_work()
        server.server_close()
        for sig, handler in before.items():
            signal.signal(sig, handler)
        info = read_json(marker, None)
        if isinstance(info, dict) and info.get("pid") == os.getpid():
            marker.unlink(missing_ok=True)
    return 0

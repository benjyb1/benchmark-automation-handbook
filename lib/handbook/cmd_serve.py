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
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .cli import command_parser, open_project
from .folder import HandbookError, read_json, write_json
from .localstore import Config, home
from .serve import LocalServer
from . import updates, upgrade
from .upgrade import ProjectTooNew
from .updater import wait_for_exit

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
    """The first address of a server already serving this project, or None. While that server is restarting
    after an update (its running file says "restarting"), waits up to 90 seconds for the new one."""
    info = read_json(running_file(path), None)
    if isinstance(info, dict) and info.get("restarting") and time.time() - float(info["restarting"]) < 120:
        old = info.get("pid")
        for _ in range(180):
            time.sleep(0.5)
            info = read_json(running_file(path), None)
            if isinstance(info, dict) and info.get("pid") != old and not info.get("restarting"):
                break
    if not isinstance(info, dict) or not _alive(info.get("pid")) or info.get("restarting"):
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
    # Set by the page that restarts itself after an update (updater.respawn): its process id, and its pass,
    # kept so the open tab carries on. Never passed on to anything this server starts.
    restart_of = os.environ.pop("HB_RESTART_OF", None)
    token = os.environ.pop("HB_SERVE_TOKEN", None) if restart_of else None
    config = Config()
    where = args.project or config.last_project()
    if not where:
        raise HandbookError("Say which project to open: hb serve --project <folder>.")
    if restart_of:
        wait_for_exit(int(restart_of))
    else:
        running = already_running(Path(where).expanduser())
        if running:
            known = read_json(Path(where).expanduser() / "project.json", {}) or {}
            name = known.get("name") or Path(where).name
            config.remember(Path(where).expanduser().resolve(), name)
            print(f"{name} is already open at {running}", flush=True)
            if not args.no_browser:
                show(running, args.app_window)
            return 0
    try:
        project = open_project(where)        # an older project's files are upgraded here (upgrade.py)
    except ProjectTooNew as e:
        return serve_too_new(Path(where).expanduser(), e, args, token)
    name = project.read("project").get("name") or project.path.name
    up = upgrade.last_upgrade(project.path)

    print(f"Building the page for {name}...", flush=True)
    server = None
    wanted = args.port if args.port is not None else DEFAULT_PORT
    for attempt in range(40 if restart_of else 1):       # after a restart, the old server may still hold the port
        try:
            server = LocalServer(project, wanted, config, token=token)
            break
        except OSError:
            time.sleep(0.5)
    if server is None:
        if args.port is not None and not restart_of:
            raise HandbookError(f"Port {args.port} is in use. Pick another with --port, or leave it out.") from None
        server = LocalServer(project, 0, config, token=token)
    server.alive_key = project_key(project.path)
    server.team.can_relaunch, server.team.app_window = True, args.app_window
    if up and up.get("at") and time.time() - _epoch(up["at"]) < 3600:
        server.worker.note(upgrade.upgrade_words(up))
    if not server.worker.build():
        print("The page could not be rebuilt; showing the last build.", flush=True)
    config.remember(project.path, name)
    marker = running_file(project.path)
    write_json(marker, {"pid": os.getpid(), "port": server.port, "token": server.token,
                        "project": str(project.path.resolve())})
    os.chmod(marker, 0o600)
    server.marker = marker
    server.start_background()
    start_update_checks()
    watch_format(server, project.path)
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
        if isinstance(info, dict) and info.get("pid") == os.getpid() and not server.restarting:
            marker.unlink(missing_ok=True)
    return 0


def _epoch(stamp: str) -> float:
    from datetime import datetime, timezone
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


FORMAT_CHECK_S = 5


def watch_format(server, path) -> None:
    """Re-read the project's format.json every few seconds. When a newer tool (a colleague's, in a shared
    folder) has upgraded the project past what this copy understands, stop writing at once and restart into
    the page that offers the update (serve_too_new)."""
    def loop():
        while not server.restarting:
            time.sleep(FORMAT_CHECK_S)
            try:
                newer = upgrade.project_format(path) > upgrade.FORMAT
            except Exception:  # noqa: BLE001  (half-synced file: look again next time)
                newer = False
            if newer:
                print("A newer version of the tool upgraded this project's files; switching to the update page.",
                      flush=True)
                server.restart()
                return
    threading.Thread(target=loop, daemon=True).start()


def start_update_checks() -> None:
    """Ask GitHub for the latest release now and then every six hours while the page runs; updates.latest only
    really asks once a day. HB_NO_UPDATE_CHECK=1 turns it off (tests)."""
    if os.environ.get("HB_NO_UPDATE_CHECK") or updates.is_development():
        return

    def loop():
        while True:
            try:
                updates.latest()
            except Exception:  # noqa: BLE001  (never take the page down over a check)
                pass
            time.sleep(6 * 3600)
    threading.Thread(target=loop, daemon=True).start()


def serve_too_new(path: Path, err: ProjectTooNew, args, token: str | None) -> int:
    """The project was upgraded by a newer version of the tool: serve a small page that says so and offers the
    update, instead of opening (and possibly damaging) files this version does not understand."""
    from .serve_blocked import BlockedServer
    print(str(err), flush=True)
    server = None
    for port in ([args.port] if args.port is not None else []) + [DEFAULT_PORT, 0]:
        try:
            server = BlockedServer(path, err, port, token=token)
            break
        except OSError:
            continue
    marker = running_file(path)
    server.alive_key = project_key(path)
    write_json(marker, {"pid": os.getpid(), "port": server.port, "token": server.token,
                        "project": str(path.resolve())})
    os.chmod(marker, 0o600)
    server.marker = marker
    start_update_checks()
    url = server.first_url()
    print(f"Open {url} to update the tool.", flush=True)
    if not args.no_browser:
        show(url, args.app_window)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        info = read_json(marker, None)
        if isinstance(info, dict) and info.get("pid") == os.getpid() and not server.restarting:
            marker.unlink(missing_ok=True)
    return 0

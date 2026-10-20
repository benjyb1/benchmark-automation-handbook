"""The page's Update and "Go back to the previous version" buttons, the chat's update tools and `hb update`.

One update at a time, in a thread of the running page (`hb serve`):
1. install: download the new version beside the current one and check it (updates.prepare); nothing in use
   changes yet, so a failed download leaves everything as it was;
2. wait until nothing is running: no scoring job, nothing queued and no Claude reply under way, on this page and
   on any other page this computer is serving (each `hb serve` leaves its port and pass in ~/.handbook/running);
3. swap the new copy in (updates.swap_in) or the previous one back (updates.go_back), in one step;
4. restart: every other open page is asked to restart once it is idle, then this one restarts itself
   (`respawn`): it starts the tool again on the same port with the same pass, so an open tab only has to
   reload, and a page behind Benjy's admin page (which reads ~/.handbook/running on every request) carries on.
   While it restarts, the running file says "restarting", so `hb serve` started by the launcher or the admin
   page waits for it instead of starting a second page.

Projects are not touched: the new version upgrades a project's files when it opens it (upgrade.py).
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from . import updates, upgrade
from .folder import HandbookError, read_json, write_json
from .localstore import Config, home

IDLE_CHECK_S = 2.0


def running_infos() -> list[dict]:
    """Every page this computer is serving: [{"pid", "port", "token", "project", "file"}] (live ones only)."""
    out = []
    for f in sorted((home() / "running").glob("*.json")):
        info = read_json(f, None) if f.is_file() else None
        if not isinstance(info, dict):
            continue
        try:
            os.kill(int(info["pid"]), 0)
        except (OSError, KeyError, ValueError, TypeError):
            continue
        out.append({**info, "file": str(f)})
    return out


def _call(info: dict, method: str, path: str, body: dict | None = None, timeout: float = 5):
    port, token = int(info["port"]), str(info["token"])
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/{path}", data=data, method=method, headers={
        "Host": f"127.0.0.1:{port}", "Cookie": f"hb_{port}={token}", "X-HB-Token": token,
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def page_busy(info: dict) -> str | None:
    """What another page is doing (a job's title, "a Claude reply"), or None when it is idle or not answering."""
    try:
        s = _call(info, "GET", "state")
    except (OSError, ValueError):
        return None
    job = s.get("job")
    if job:
        return job.get("title") or "a job"
    if s.get("queued"):
        return "a queued job"
    if s.get("chatting"):
        return "a Claude reply"
    return None


def other_pages(own_pid: int | None = None) -> list[dict]:
    return [i for i in running_infos() if int(i.get("pid") or 0) != (own_pid or -1)]


def format_of_install(folder: Path) -> int:
    """The project format an installed copy of the tool writes (its upgrade.FORMAT; 1 before formats existed)."""
    try:
        text = (Path(folder) / "lib" / "handbook" / "upgrade.py").read_text(encoding="utf-8")
    except OSError:
        return 1
    m = re.search(r"^FORMAT = (\d+)", text, re.M)
    return int(m.group(1)) if m else 1


def back_blockers(extra: list[str] | None = None) -> list[dict]:
    """Projects the previous version could not open, because a later version upgraded their files:
    [{"name", "path", "backup", "at"}], where backup is the copy taken before the first upgrade past what the
    previous version reads (None if there is none) and at is when it was taken."""
    fmt = format_of_install(updates.previous_dir())
    seen, out = set(), []
    paths = [p.get("path") for p in Config().projects()] + list(extra or [])
    for path in paths:
        if not path or str(Path(path).resolve()) in seen:
            continue
        seen.add(str(Path(path).resolve()))
        p = Path(path)
        if (p / "project.json").is_file() and upgrade.project_format(p) > fmt:
            name = (read_json(p / "project.json", {}) or {}).get("name") or p.name
            ups = (read_json(p / upgrade.LOG, {"upgrades": []}) or {}).get("upgrades") or []
            first = next((u for u in reversed(ups) if int(u.get("from") or 1) <= fmt and u.get("backup")
                          and (Path(u["backup"]) / p.name).is_dir()), None)
            out.append({"name": name, "path": str(p), "backup": first["backup"] if first else None,
                        "at": first.get("at") if first else None})
    return out


def blocked_words(blocked: list[dict]) -> str:
    names = ", ".join(b["name"] for b in blocked)
    if all(b["backup"] for b in blocked):
        return (f"The previous version cannot read {names}: this version upgraded {'its' if len(blocked) == 1 else 'their'} "
                "files. You can still go back if you put "
                f"{'it' if len(blocked) == 1 else 'them'} back as {'it was' if len(blocked) == 1 else 'they were'} "
                "before the upgrade, from the copy taken then. Anything changed since is set aside in a copy, not lost.")
    return (f"The previous version cannot read {names}, and there is no copy from before the upgrade to put back, so "
            "going back would leave it unopenable.")


def restore_before_upgrade(blocked: list[dict]) -> list[str]:
    """Put each blocked project back as it was in its backup. The project as it is now is moved to
    ~/.handbook/backups/<name>/<date> before going back/ first (never deleted). Returns plain lines."""
    said = []
    for b in blocked:
        path = Path(b["path"])
        src = Path(b["backup"])
        if not (src / path.name).is_dir():
            raise HandbookError(f"The copy of {b['name']} from before the upgrade is missing, so it was not put back.")
        when = time.strftime("%Y-%m-%d %H%M%S")
        aside = upgrade.backups_root() / path.name / f"{when} before going back"
        n = 1
        while aside.exists():
            n += 1
            aside = aside.with_name(f"{when} before going back ({n})")
        aside.mkdir(parents=True)
        held = path.with_name(path.name + ".held-back")
        shutil.move(str(path), str(aside / path.name))
        if held.is_dir():
            shutil.move(str(held), str(aside / held.name))
        upgrade._copy_tree(src / path.name, path)
        if (src / held.name).is_dir():
            upgrade._copy_tree(src / held.name, held)
        said.append(f"{b['name']} is back as it was before the upgrade; how it was just now is kept in {aside}.")
    return said


class Updater:
    """One install or go-back at a time for a running page. `server` gives `busy()` (what this page is doing, or
    None), `restart()` and `project_path`."""

    def __init__(self, server):
        self.server = server
        self.how = None              # "install" or "back"
        self.phase = None            # downloading, waiting, swapping, restarting, failed, cancelled
        self.words = None            # one plain line for the page
        self.error = None
        self.cancel_wanted = False
        self.thread = None
        self.lock = threading.Lock()

    def public(self) -> dict:
        return {"how": self.how, "phase": self.phase, "words": self.words, "error": self.error}

    def busy_updating(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, how: str, restore: bool = False) -> dict:
        if how not in ("install", "back"):
            raise HandbookError("Say install or back.")
        with self.lock:
            if self.busy_updating():
                return self.public()
            if updates.is_development():
                raise HandbookError("This is a development copy of the tool (inside a git repository), so it is "
                                    "not updated from here.")
            if how == "install":
                st = updates.status(check=True)
                if not st["available"]:
                    raise HandbookError(f"You already have the latest version ({st['current']})."
                                        if st.get("latest") else "No newer version was found.")
                found = updates.latest()
            else:
                if not updates.previous_version():
                    raise HandbookError("There is no previous version on this computer to go back to.")
                blocked = back_blockers([str(self.server.project_path)])
                if blocked and not (restore and all(b["backup"] for b in blocked)):
                    raise HandbookError(blocked_words(blocked))
                found = blocked or None
            self.how, self.phase, self.error, self.cancel_wanted = how, "starting", None, False
            self.words = "Getting ready..."
            self.thread = threading.Thread(target=self._run, args=(how, found), daemon=True)
            self.thread.start()
            return self.public()

    def cancel(self) -> dict:
        if self.busy_updating() and self.phase in ("downloading", "waiting", "starting"):
            self.cancel_wanted = True
            self.words = "Stopping the update..."
        return self.public()

    def _say(self, text: str) -> None:
        self.words = text

    def _wait_quiet(self) -> bool:
        """Wait until this page and every other page are idle twice in a row; False when cancelled."""
        quiet_once = False
        while True:
            if self.cancel_wanted:
                return False
            mine = self.server.busy()
            theirs = None if mine else next((b for b in (page_busy(i) for i in other_pages(os.getpid())) if b), None)
            doing = mine or theirs
            if doing:
                quiet_once = False
                where = "" if mine else " on another benchmark's page"
                self.words = f"Waiting for {doing}{where} to finish before updating. Your work carries on as normal."
            elif quiet_once:
                return True
            else:
                quiet_once = True
            time.sleep(IDLE_CHECK_S)

    def _run(self, how: str, found: dict | None) -> None:
        try:
            new = None
            if how == "install":
                self.phase = "downloading"
                new = updates.prepare(found, say=self._say)
            if self.cancel_wanted:
                raise _Cancelled()
            self.phase = "waiting"
            if not self._wait_quiet():
                raise _Cancelled()
            self.phase = "swapping"
            if how == "back" and found:
                self.words = " ".join(restore_before_upgrade(found))
            done = updates.swap_in(new) if how == "install" else updates.go_back()
            self.phase = "restarting"
            self.words = (f"Version {done['to']} is in place. Restarting the page..." if how == "install" else
                          f"Back to version {done['to']}. Restarting the page...")
            for info in other_pages(os.getpid()):
                try:
                    _call(info, "POST", "update/restart", {})
                except (OSError, ValueError):
                    pass
            time.sleep(0.5)                      # let the page see "restarting" once
            self.server.restart()
        except _Cancelled:
            self.phase, self.words = "cancelled", "The update was stopped. Nothing changed."
            if how == "install":
                updates._trash(updates.new_dir())
        except HandbookError as e:
            self.phase, self.error, self.words = "failed", str(e), str(e)
        except Exception as e:  # noqa: BLE001
            self.phase, self.error = "failed", f"The update stopped: {type(e).__name__}: {e}. Nothing changed."
            self.words = self.error


class _Cancelled(Exception):
    pass


def restart_when_idle(server) -> None:
    """Another page updated the tool: restart this one as soon as it is idle (its work is never cut off)."""
    def wait():
        while server.busy():
            time.sleep(IDLE_CHECK_S)
        server.restart()
    threading.Thread(target=wait, daemon=True).start()


def respawn(server, project_path: Path, marker: Path | None) -> None:
    """Start the tool again for this project, on the same port with the same pass, then stop this server. The
    new one waits for this process to end before it takes the port (cmd_serve, HB_RESTART_OF)."""
    if marker is not None:
        info = read_json(marker, None)
        if isinstance(info, dict) and info.get("pid") == os.getpid():
            info["restarting"] = time.time()
            write_json(marker, info)
    hb = updates.tool_dir() / "lib" / "hb"
    env = dict(os.environ, HB_SERVE_TOKEN=server.token, HB_RESTART_OF=str(os.getpid()))
    log = open(Path(project_path) / ".serve.log", "ab")
    subprocess.Popen([str(hb), "serve", "--project", str(project_path), "--no-browser", "--port", str(server.port)],
                     env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    log.close()
    threading.Thread(target=server.shutdown, daemon=True).start()


def wait_for_exit(pid: int, timeout_s: float = 90) -> None:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)

"""Project formats: a newer tool upgrades an older project's files once, after taking a backup.

Every project folder records the format of its files in `format.json` ({"format": 2, "by": "1.0.0"}). A
folder without it is format 1 (every project made before formats were numbered). FORMAT is the format this
copy of the tool writes.

When the tool opens a project (cli.open_project, so `hb serve` and every command), `ensure`:
- does nothing when the project is already at FORMAT;
- for an older project: copies the whole folder (and its `.held-back` sibling) to
  ~/.handbook/backups/upgrades/<slug>/<date> format <n>/ first (a subfolder of its own: team.py's daily snapshots
  are the zips in ~/.handbook/backups/<slug>-<hash>/ and are pruned, these never are), then runs each step in STEPS from the project's format up
  to FORMAT, once, writing the new number after each step, and appends what changed to the project's
  `upgrades.json`. Only one process upgrades at a time (an `.upgrading` lock file in the project);
- for a project newer than this tool (a colleague's newer copy upgraded a shared folder, or the owner went back
  to the previous version): raises ProjectTooNew, so nothing is written with code that does not understand it.

Updating the tool never touches projects: they are upgraded when the new tool first opens them. A running page
also re-reads format.json every few seconds (cmd_serve), so when a colleague's newer tool upgrades a shared folder,
an older page stops writing at once and restarts into the "update to carry on" page.

Shared folders (team.py): the lock file cannot stop two computers upgrading at once, so every step must be
deterministic: the same files in give the same files out, with no timestamps or random ids anywhere but
upgrades.json (team.py merges clash copies: the higher format.json wins, upgrades.json entries are joined). In a
team project the upgrade is also written to the team log, with the person's name.

To change the format: add a step to STEPS (to-format, one plain sentence of what changes, the function that
does it and returns a list of what it changed), raise FORMAT, and add a fixture of the old format to
tests/test_handbook_upgrade.py.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .folder import HandbookError, read_json, write_json
from .localstore import home

FORMAT = 2
FILE = "format.json"
LOG = "upgrades.json"
LOCK = ".upgrading"


class ProjectTooNew(HandbookError):
    """The project's files are in a newer format than this copy of the tool understands."""

    def __init__(self, message: str, fmt: int, by: str | None):
        super().__init__(message)
        self.format = fmt
        self.by = by


def _step_record_format(project) -> list[str]:
    return ["The project now records the format of its files (format.json), so later versions of the tool "
            "can upgrade it safely."]


# (format it upgrades to, what changes in plain words, the function). In order.
STEPS = [
    (2, "Record the format of the project's files.", _step_record_format),
]


def tool_version() -> str:
    from . import updates
    return updates.version()


def project_format(path) -> int:
    data = read_json(Path(path) / FILE, None)
    try:
        return int(data["format"]) if isinstance(data, dict) else 1
    except (KeyError, TypeError, ValueError):
        return 1


def stamp_new(path) -> None:
    """Mark a project this tool has just made as being in its format (folder.init_project calls this)."""
    write_json(Path(path) / FILE, {"format": FORMAT, "by": tool_version()})


def too_new_words(fmt: int, by: str | None, who: str | None = None) -> str:
    if who:
        return (f"{who} upgraded this project for version {by or 'a newer version'} of the benchmark tool. This copy "
                f"is version {tool_version()}, so it cannot open the upgraded files safely. Update your copy to keep "
                "working on it.")
    return (f"This project was last opened with a newer version of the benchmark tool"
            f"{f' (version {by})' if by else ''}, which upgraded its files. This copy is version {tool_version()}, "
            "so it cannot open them safely. Update the tool to carry on.")


def _copy_tree(src: Path, dest: Path) -> None:
    """A full copy; on a Mac a clone (`cp -c`), which is instant and takes no extra space until a file changes."""
    if sys.platform == "darwin":
        done = subprocess.run(["/bin/cp", "-cR", str(src), str(dest)], capture_output=True, text=True)
        if done.returncode == 0:
            return
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest, symlinks=True)


def backup(project, fmt: int) -> Path:
    """Copy the project folder (and its held-back sibling) under ~/.handbook/backups; returns the folder."""
    path = Path(project.path)
    when = datetime.now().strftime("%Y-%m-%d %H%M%S")
    base = backups_root() / path.name / f"{when} format {fmt}"
    dest, n = base, 1
    while True:                      # two projects of the same name may be upgraded in the same second
        try:
            dest.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            n += 1
            dest = base.with_name(f"{base.name} ({n})")
    _copy_tree(path, dest / path.name)
    (dest / path.name / LOCK).unlink(missing_ok=True)       # the upgrade's own lock is not part of the project
    held = path.with_name(path.name + ".held-back")
    if held.is_dir():
        _copy_tree(held, dest / held.name)
    return dest


def backups_root() -> Path:
    return home() / "backups" / "upgrades"


def _team():
    """team.py where this copy has it (the team-working build), else None."""
    try:
        from . import team
        return team
    except ImportError:
        return None


def team_upgrader(path) -> dict | None:
    """The newest "upgraded" entry in a team project's log ({"name", "text", "at"}), or None."""
    import json
    folder = Path(path) / "team" / "log"
    best = None
    for f in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("kind") == "upgraded" and (best is None or str(e.get("at")) > str(best.get("at"))):
                best = e
    return best


def _lock(path: Path, wait_s: float = 120) -> int | None:
    """Take the project's upgrade lock; None when another process held it for `wait_s` and it is still there."""
    lock = path / LOCK
    end = time.time() + wait_s
    while True:
        try:
            return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 600:      # left behind by a crash
                    lock.unlink()
                    continue
            except OSError:
                continue
            if time.time() > end:
                return None
            time.sleep(0.5)


def ensure(project) -> dict | None:
    """Upgrade `project` to FORMAT if it is older (see the module docstring). Returns the log entry when it
    upgraded, else None. Raises ProjectTooNew for a project newer than this tool."""
    path = Path(project.path)
    fmt = project_format(path)
    if fmt == FORMAT:
        return None
    if fmt > FORMAT:
        by = (read_json(path / FILE, {}) or {}).get("by")
        who = (team_upgrader(path) or {}).get("name")
        raise ProjectTooNew(too_new_words(fmt, by, who), fmt, by)
    fd = _lock(path)
    if fd is None:
        raise HandbookError("Another copy of the tool is upgrading this project's files. Try again in a minute.")
    try:
        fmt = project_format(path)                 # another process may have done it while we waited
        if fmt >= FORMAT:
            return None
        saved = backup(project, fmt)
        changes = []
        for to, what, step in STEPS:
            if to <= fmt:
                continue
            changes.append({"to": to, "what": what, "changed": step(project) or []})
            write_json(path / FILE, {"format": to, "by": tool_version()})
        entry = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "from": fmt, "to": FORMAT,
                 "tool": tool_version(), "backup": str(saved), "steps": changes}
        log = read_json(path / LOG, {"upgrades": []})
        log.setdefault("upgrades", []).append(entry)
        write_json(path / LOG, log)
        _team_log(project)
        return entry
    finally:
        os.close(fd)
        (path / LOCK).unlink(missing_ok=True)


def _team_log(project) -> None:
    """In a team project, say in the team log who upgraded it (never raises)."""
    team = _team()
    if team is None or not getattr(project, "is_team", lambda: False)():
        return
    try:
        from .localstore import Config
        me = team.me(Config())
        name = (me or {}).get("name") or "Someone"
        team.log(project, me, "upgraded", text=f"{name} upgraded this project for version {tool_version()}")
    except Exception:  # noqa: BLE001  (the log is a courtesy; the upgrade is done)
        pass


def last_upgrade(path) -> dict | None:
    log = read_json(Path(path) / LOG, {"upgrades": []}) or {}
    ups = log.get("upgrades") or []
    return ups[-1] if ups else None


def upgrade_words(entry: dict) -> str:
    """One plain line for the page's Recent changes."""
    # No folder path here: owners read this line, and the copy's place matters only to "go back".
    return (f"This project's files were upgraded for version {entry.get('tool')} of the tool. A copy of them as "
            f"they were is kept on this computer, so going back to the earlier version puts them back.")

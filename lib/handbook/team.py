"""Team working: several people, each on their own computer with their own Claude plan, on one project
folder that a sync service keeps in step (Dropbox, Google Drive, OneDrive, a network share).

A project is a team project when `team.json` sits in its folder:

    {"since": "2026-10-19T10:00:00Z", "members": {"p-1a2b3c4d5e": {"name": "Toby Mendel",
     "computer": "Toby's MacBook", "joined": "..."}}}

What each person's tool does differently in a team project (see serve.py for where):
- It applies only what this person saved in the page, and starts only this person's re-runs and
  re-reads; everyone else's changes arrive through the sync service, already applied by their own tool.
- It builds the page into this person's own folder (`Project.viewer`), never into the shared one.
- It writes this person's actions to `team/log/<person id>.jsonl` (one file each, so two computers
  never write the same log file). Human actions only; chats are never logged.
- It looks for the copies a sync service makes when two computers saved the same file before it
  synced ("(Toby's conflicted copy ...)", "file (1).json", "file 2.json", "file-ComputerName.json"),
  merges what can be merged (lists of records, cells nobody disagrees on) and turns the rest into
  clashes, `team/clashes/<id>.json`, which anyone on the team can resolve from the page.
- A save made from a page that had not yet seen a colleague's change to the same cell or rule is held
  as a clash instead of quietly overwriting it.

Each person is known by their Claude account: the name it gives (which the person can change in the
corner chat) and a hidden id made from the account's id, so a renamed person stays the same person and
two people with the same name stay apart.

Also here, for every project: a daily snapshot of the project into this person's own home folder
(`backups`), restoring one, and the files for "Share with a team" and "Send a copy".
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .folder import HandbookError, Project, handbook_home, now_stamp, read_json, write_json

TEAM_FILE = "team.json"
TEAM_DIR = "team"
LOG_DIR = "team/log"
CLASH_DIR = "team/clashes"
JOBS_DIR = "team/jobs"
SET_ASIDE_DIR = "team/set-aside"
BACKUPS_KEPT = 14
JOB_FRESH_S = 300                    # a colleague's job state older than this is not shown as running
DATALESS = 0x40000000                # macOS: a file the sync service keeps online only (SF_DATALESS)

# Never in a backup, a copy sent out or a shared copy: rebuilt, per person, or too large to matter.
LEAVE_OUT_DIRS = ("viewer", ".git", "__pycache__")
LEAVE_OUT_PARTS = (".app",)
LEAVE_OUT_FILES = (".serve.log", ".DS_Store", ".upgrading")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(now: datetime | None = None) -> str:
    return (now or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- Who this person is ------------------------------------------------------------------------------

def claude_account() -> dict:
    """The Claude account `claude` is signed in with on this computer: {name, uuid}, or {} when there is
    none. Read from Claude's own settings file; nothing is sent anywhere."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    path = Path(folder).expanduser() / ".claude.json" if folder else Path.home() / ".claude.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    acct = data.get("oauthAccount") if isinstance(data, dict) else None
    if not isinstance(acct, dict):
        return {}
    name = next((acct.get(k).strip() for k in ("displayName", "fullName")
                 if isinstance(acct.get(k), str) and acct.get(k).strip()), None)
    uuid = acct.get("accountUuid") if isinstance(acct.get("accountUuid"), str) else None
    return {k: v for k, v in (("name", name), ("uuid", uuid)) if v}


_COMPUTER: list[str] = []


def computer_name() -> str:
    """This computer's name as people see it ("Toby's MacBook Air"); OneDrive adds it to clash copies."""
    if not _COMPUTER:
        name = None
        if sys.platform == "darwin":
            try:
                name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                                      timeout=5).stdout.strip() or None
            except (OSError, subprocess.SubprocessError):
                name = None
        _COMPUTER.append(name or os.environ.get("COMPUTERNAME") or socket.gethostname().split(".")[0])
    return _COMPUTER[0]


def person_id(config) -> str:
    """This person's hidden id: from their Claude account when there is one, else a random id kept in
    their own config. Never their name, so a rename keeps them the same person."""
    acct = claude_account()
    if acct.get("uuid"):
        return "p-" + hashlib.sha1(acct["uuid"].encode("utf-8")).hexdigest()[:10]
    data = config.read()
    key = (data.get("user") or {}).get("key")
    if isinstance(key, str) and re.fullmatch(r"p-[a-f0-9]{10}", key):
        return key
    key = "p-" + secrets.token_hex(5)
    with config._lock:
        data = config.read()
        data["user"] = {**(data.get("user") or {}), "key": key}
        config._write(data)
    return key


def machine_key(config) -> str:
    """This computer's own key, kept in this person's config: a save carries it (`on`), so a person with the
    project open on two computers never has both apply it, or both spend tokens on its re-run."""
    data = config.read()
    key = data.get("machine")
    if isinstance(key, str) and re.fullmatch(r"m-[a-f0-9]{10}", key):
        return key
    key = "m-" + secrets.token_hex(5)
    with config._lock:
        data = config.read()
        data["machine"] = key
        config._write(data)
    return key


def me(config) -> dict:
    """{id, name, computer}: the name the person chose (corner chat, or the page's name box), else the
    name on their Claude account; None when there is neither (the page then asks)."""
    return {"id": person_id(config), "name": config.user_name() or claude_account().get("name"),
            "computer": computer_name()}


# ---- The team file -------------------------------------------------------------------------------------

def read_team(project: Project) -> dict:
    try:
        data = read_json(project.path / TEAM_FILE, {}) or {}
    except HandbookError:
        data = {}
    members = data.get("members") if isinstance(data.get("members"), dict) else {}
    return {"since": data.get("since"), "members": {k: v for k, v in members.items() if isinstance(v, dict)}}


def start_team(project: Project, person: dict) -> dict:
    """Make the project a team project with this person as its first member."""
    team = {"since": now_stamp(), "members": {}}
    write_json(project.path / TEAM_FILE, team)
    join(project, person)
    return read_team(project)


def join(project: Project, person: dict) -> bool:
    """Add this person to `team.json`, or bring their name and computer up to date. True when they are new."""
    if not project.is_team() or not person.get("id"):
        return False
    team = read_team(project)
    known = team["members"].get(person["id"])
    entry = {"name": person.get("name") or (known or {}).get("name"), "computer": person.get("computer"),
             "joined": (known or {}).get("joined") or now_stamp()}
    if known == entry:
        return False
    team["members"][person["id"]] = entry
    write_json(project.path / TEAM_FILE, {"since": team["since"] or now_stamp(), "members": team["members"]})
    if not known:
        log(project, person, "joined", text=f"{entry['name'] or 'A new person'} joined the team")
    elif known.get("name") and entry["name"] and known.get("name") != entry["name"]:
        log(project, person, "renamed", before=known.get("name"), after=entry["name"],
            text=f"{known.get('name')} is now called {entry['name']}")
    return not known


def names(project: Project) -> dict:
    """Person id -> name, for everyone who has been on the team."""
    return {pid: m.get("name") or "Someone" for pid, m in read_team(project)["members"].items()}


def name_of(project: Project, pid) -> str | None:
    if not pid:
        return None
    return names(project).get(pid)


def initials(name: str | None) -> str:
    words = [w for w in re.split(r"[\s\-]+", name or "") if w and w[0].isalpha()]
    return ("".join(w[0] for w in words[:2]) or "?").upper()


# ---- The log of human actions --------------------------------------------------------------------------

LOG_KINDS = ("joined", "renamed", "shared", "corrected", "checked", "unchecked", "rule_decided",
             "rule_skipped", "edited", "document_added", "run_started", "run_stopped", "setup_changed",
             "clash_resolved", "clash_found", "restored", "run_finished", "upgraded")   # a list, never checked


def log(project: Project, person: dict, kind: str, **fields) -> dict | None:
    """Append one action to this person's own log file. Only in a team project; never raises."""
    if not project.is_team() or not person or not person.get("id"):
        return None
    entry = {"at": now_stamp(), "by": person["id"], "name": person.get("name"), "kind": kind,
             **{k: v for k, v in fields.items() if v is not None}}
    path = project.path / LOG_DIR / f"{person['id']}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return entry


def _read_lines(path: Path, start: int = 0) -> tuple[list[dict], int]:
    try:
        with path.open("rb") as f:
            f.seek(start)
            raw = f.read()
    except OSError:
        return [], start
    out = []
    keep = raw.rfind(b"\n") + 1                  # a half-synced last line waits for the next look
    for line in raw[:keep].decode("utf-8", "replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out, start + keep


def read_log(project: Project, limit: int | None = None) -> list[dict]:
    """Everyone's actions, newest first."""
    folder = project.path / LOG_DIR
    entries = []
    for path in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        entries.extend(_read_lines(path)[0])
    entries.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    return entries[:limit] if limit else entries


class LogFollower:
    """Hands back colleagues' actions as their log files sync in (never this person's own)."""

    def __init__(self, project: Project, my_id: str):
        self.project, self.my_id = project, my_id
        self.offsets: dict[str, int] | None = None

    def new(self) -> list[dict]:
        folder = self.project.path / LOG_DIR
        files = {p.stem: p for p in folder.glob("*.jsonl")} if folder.is_dir() else {}
        first = self.offsets is None
        if first:
            self.offsets = {}
        out = []
        for pid, path in files.items():
            if pid == self.my_id:
                continue
            got, end = _read_lines(path, self.offsets.get(pid, 0))
            self.offsets[pid] = end
            if not first:
                out.extend(got)
        return sorted(out, key=lambda e: str(e.get("at") or ""))


# ---- What a colleague's tool is doing --------------------------------------------------------------------

def write_job(project: Project, person: dict, job: dict | None) -> None:
    """This person's running job, for colleagues' pages ("Raphael: re-running 12 rows, 4 done")."""
    if not project.is_team() or not person.get("id"):
        return
    path = project.path / JOBS_DIR / f"{person['id']}.json"
    try:
        old = read_json(path, None)
    except HandbookError:
        old = None
    if isinstance(old, dict) and old.get("job") == job and old.get("name") == person.get("name"):
        if not job:
            return
        try:                          # unchanged and running: say "still going" once a minute, no more
            at = datetime.strptime(str(old.get("at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if _now() - at < timedelta(seconds=60):
                return
        except ValueError:
            pass
    data = {"name": person.get("name"), "at": now_stamp(), "job": job}
    try:
        write_json(path, data)
    except OSError:
        pass


def others_jobs(project: Project, my_id: str) -> list[dict]:
    folder = project.path / JOBS_DIR
    out = []
    cutoff = _now() - timedelta(seconds=JOB_FRESH_S)
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.stem == my_id:
            continue
        try:
            data = read_json(path, {}) or {}
        except HandbookError:
            continue
        job = data.get("job")
        try:
            at = datetime.strptime(str(data.get("at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if isinstance(job, dict) and job.get("status") in ("running", "queued") and at >= cutoff:
            out.append({"by": path.stem, "name": data.get("name"), "job": job})
    return out


# ---- Cells and rules: who changed them, and what a page saw ------------------------------------------------

def cell_rev(cell) -> str:
    """A short fingerprint of what a person sees in a cell: its value and whose answer stands in it.
    The page sends back the fingerprint it showed with every save, so a save made before a colleague's
    change synced in can be told apart from one made after."""
    if not isinstance(cell, dict):
        return "none"
    owner = cell.get("owner") if isinstance(cell.get("owner"), dict) else {}
    key = [cell.get("state"), cell.get("value"), owner.get("value"), owner.get("correction"), owner.get("by")]
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]


def rule_rev(rule) -> str:
    if not isinstance(rule, dict):
        return "none"
    d = rule.get("decision") if isinstance(rule.get("decision"), dict) else {}
    key = [rule.get("status"), d.get("option"), d.get("text"), d.get("by")]
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]


def value_words(question: dict | None, value) -> str:
    """A cell's value as people read it: the level's label when the question has one."""
    if value is None:
        return "no answer"
    for lv in ((question or {}).get("answer") or {}).get("levels") or []:
        if lv.get("value") == value and type(lv.get("value")) is type(value):
            label = lv.get("label") or lv.get("name")
            return f"{value} ({label})" if label and str(label) != str(value) else str(value)
    return str(value)


# ---- Clashes ------------------------------------------------------------------------------------------------
#
# {"id", "kind": "cell" | "rule" | "file" | "save" | "decision", "source": "sync" | "save",
#  "row", "qid", "rule", "file", "title", "doc" (the held save's id, for kind save/decision),
#  "sides": [{"key", "by", "name", "at", "words", "data"}], "created",
#  "open": true, "resolved": {"by", "name", "at", "chose", "words"} | null}

def clash_path(project: Project, cid: str) -> Path:
    if not re.fullmatch(r"k-[a-z0-9-]{4,80}", cid or ""):
        raise HandbookError(f"{cid!r} is not a clash id.")
    return project.path / CLASH_DIR / f"{cid}.json"


def clashes(project: Project, only_open: bool = False) -> list[dict]:
    folder = project.path / CLASH_DIR
    out = []
    for path in sorted(folder.glob("k-*.json")) if folder.is_dir() else []:
        if conflict_of(path.name) is not None:
            continue
        try:
            c = read_json(path, None)
        except HandbookError:
            continue
        if isinstance(c, dict) and c.get("id") == path.stem and (not only_open or c.get("open")):
            out.append(c)
    return sorted(out, key=lambda c: str(c.get("created") or ""))


def add_clash(project: Project, clash: dict, person: dict | None = None) -> dict:
    """Record a clash (idempotent: the same clash found twice is kept once)."""
    basis = json.dumps([clash.get("kind"), clash.get("row"), clash.get("qid"), clash.get("rule"),
                        clash.get("file"), clash.get("doc"),
                        sorted(json.dumps(s.get("data"), sort_keys=True, default=str) for s in clash["sides"])],
                       sort_keys=True, default=str)
    cid = "k-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    path = clash_path(project, cid)
    old = read_json(path, None) if path.is_file() else None
    if isinstance(old, dict):
        return old
    record = {"id": cid, "created": now_stamp(), "open": True, "resolved": None, **clash}
    write_json(path, record)
    if person:
        log(project, person, "clash_found", clash=cid, row=clash.get("row"), qid=clash.get("qid"),
            rule=clash.get("rule"), file=clash.get("file"), text=f"Clash found: {clash.get('title')}")
    return record


def resolve_clash(project: Project, cid: str, chose: str, person: dict) -> dict:
    """Keep one side of a clash. Cell, rule and file clashes are written here; a held save (kind save or
    decision) is applied or dropped by its author's own tool, which reads the resolution."""
    path = clash_path(project, cid)
    clash = read_json(path, None)
    if not isinstance(clash, dict):
        raise HandbookError("That clash is not there any more.")
    if not clash.get("open"):
        return clash
    side = next((s for s in clash.get("sides") or [] if s.get("key") == chose), None)
    if side is None:
        raise HandbookError(f"That clash has no side {chose!r}.")
    kind = clash.get("kind")
    if kind == "cell":
        current = (project.answers(clash["row"]).get("cells") or {}).get(clash["qid"])
        if cell_rev(current) not in {cell_rev(s.get("data")) for s in clash.get("sides") or []}:
            raise HandbookError(f"{clash.get('title')} has changed again since this clash. Look at the answer as it "
                                "is now and correct it there if it needs it.")
        answers = project.answers(clash["row"])
        answers.setdefault("cells", {})[clash["qid"]] = side["data"]
        answers["row"] = clash["row"]
        project.write_answers(clash["row"], answers)
    elif kind == "rule":
        rules = project.read("rules")
        listed = rules.get("rules") or []
        now_rule = next((r for r in listed if isinstance(r, dict) and r.get("id") == clash["rule"]), None)
        if rule_rev(now_rule) not in {rule_rev(s.get("data")) for s in clash.get("sides") or []}:
            raise HandbookError(f"{clash.get('title')} has changed again since this clash. Look at the rule as it is now.")
        for i, r in enumerate(listed):
            if isinstance(r, dict) and r.get("id") == clash["rule"]:
                listed[i] = side["data"]
                break
        else:
            listed.append(side["data"])
        rules["rules"] = listed
        project.write("rules", rules)
    elif kind == "file":
        target = _inside(project, clash["file"])
        if side.get("data") is None:
            pass
        elif target.suffix == ".json" and not isinstance(side["data"], str):
            write_json(target, side["data"])
        elif target.suffix == ".json":
            try:
                json.loads(side["data"])
            except ValueError:
                raise HandbookError("That version of the file is incomplete (it was caught half-synced), so it "
                                    "cannot be kept. Keep the other one.") from None
            target.write_text(str(side["data"]), encoding="utf-8")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(side["data"]), encoding="utf-8")
    clash["open"] = False
    clash["resolved"] = {"by": person.get("id"), "name": person.get("name"), "at": now_stamp(), "chose": chose,
                         "words": side.get("words"), "side_by": side.get("by"), "side_name": side.get("name")}
    write_json(path, clash)
    whose = "their own" if side.get("by") == person.get("id") else f"{side.get('name') or 'the other'}'s"
    log(project, person, "clash_resolved", clash=cid, row=clash.get("row"), qid=clash.get("qid"),
        rule=clash.get("rule"), chose=chose, kept_by=side.get("by"),
        text=f"{person.get('name') or 'Someone'} kept {whose} version of {clash.get('title')}")
    return clash


def close_clash(project: Project, cid: str, person: dict, why: str) -> None:
    """Close a clash nobody needs to choose on any more (its held save was replaced by a newer one)."""
    path = clash_path(project, cid)
    clash = read_json(path, None)
    if isinstance(clash, dict) and clash.get("open"):
        clash["open"] = False
        clash["resolved"] = {"by": person.get("id"), "name": person.get("name"), "at": now_stamp(), "chose": None,
                             "words": why}
        write_json(path, clash)


def _inside(project: Project, rel: str) -> Path:
    target = (project.path / rel).resolve()
    if project.path.resolve() not in target.parents:
        raise HandbookError(f"{rel} is not inside the project.")
    return target


# ---- The copies sync services make when two computers saved the same file ------------------------------------

_DROPBOX = re.compile(r"^(?P<base>.+?) \((?:(?P<who>[^()]*?)'s )?conflicted copy[^()]*\)(?P<ext>\.[A-Za-z0-9]+)?$",
                      re.I)
_NUMBERED = re.compile(r"^(?P<base>.+?) \((?P<n>\d{1,2})\)(?P<ext>\.[A-Za-z0-9]+)$")      # Google Drive, Box
_ICLOUD = re.compile(r"^(?P<base>.+?) (?P<n>[2-9])(?P<ext>\.[A-Za-z0-9]+)$")            # iCloud Drive


def conflict_of(name: str, computers=(), exists=None) -> tuple[str, str] | None:
    """(the name of the file this is a sync service's clash copy of, the service), or None.
    `exists(name)` says whether a file of that name is beside it: a numbered or computer-named copy
    counts only then, so a row called "kenya-2" is never taken for a copy."""
    m = _DROPBOX.match(name)
    if m:
        return m.group("base") + (m.group("ext") or ""), "Dropbox"
    for rx, service in ((_NUMBERED, "Google Drive"), (_ICLOUD, "iCloud")):
        m = rx.match(name)
        if m:
            base = m.group("base") + m.group("ext")
            if exists is None or exists(base):
                return base, service
    stem, dot, ext = name.rpartition(".")
    if dot:
        for comp in computers:
            tail = "-" + re.sub(r"\s+", "-", comp or "")
            if comp and stem.endswith(tail) and len(stem) > len(tail):
                base = stem[: -len(tail)] + "." + ext
                if exists is None or exists(base):
                    return base, "OneDrive"
    return None


# Where the tool itself writes, so where a sync service's clash copy can be the tool's to merge. Never the
# owner's own files (inbox/, documents/, feedback/files/): "Annual report (1).pdf" there is a real file.
TOOL_TOPS = ("answers", "checks", "found", "runs", "compare", "report", "team", "page-data")


def _tool_written(rel: Path) -> bool:
    return rel == Path(".") or (bool(rel.parts) and rel.parts[0] in TOOL_TOPS)


def find_conflicts(project: Project) -> list[tuple[Path, Path, str]]:
    """Every clash copy in the folders the tool writes: (copy, the file it is a copy of, the service)."""
    computers = sorted({m.get("computer") for m in read_team(project)["members"].values() if m.get("computer")}
                       | {computer_name()})
    out = []
    for folder, dirs, files in os.walk(project.path):
        rel = Path(folder).relative_to(project.path)
        dirs[:] = [d for d in dirs if not (rel == Path(".") and d in LEAVE_OUT_DIRS)
                   and not d.endswith(".app") and d != "set-aside" and _tool_written(rel / d)]
        if not _tool_written(rel):
            continue
        here = set(files)
        for name in files:
            if name.endswith(".tmp"):
                continue
            found = conflict_of(name, computers, lambda base: base in here)
            if found:
                out.append((Path(folder) / name, Path(folder) / found[0], found[1]))
    return out


def _set_aside(project: Project, copy: Path) -> None:
    """Keep the clash copy out of the way (never deleted: a person can still look at it)."""
    rel = copy.relative_to(project.path)
    dest = project.path / SET_ASIDE_DIR / f"{_now():%Y%m%d-%H%M%S}" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(copy), str(dest))


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _by_id(items) -> dict:
    return {i.get("id"): i for i in items or [] if isinstance(i, dict) and i.get("id")}


def _union(a: list, b: list) -> list:
    seen, out = set(), []
    for x in list(a or []) + list(b or []):
        k = json.dumps(x, sort_keys=True, default=str)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _merge_records(a: list, b: list, prefer=None) -> list:
    """Two lists of records with ids: every record from both, one per id (`prefer` picks between two)."""
    out = _by_id(a)
    for rid, rec in _by_id(b).items():
        if rid not in out:
            out[rid] = rec
        elif prefer and out[rid] != rec:
            out[rid] = prefer(out[rid], rec)
    order = [i.get("id") for i in list(a or []) + list(b or []) if isinstance(i, dict)]
    return [out[i] for i in dict.fromkeys(order) if i in out]


_ROUND_ORDER = {"to_check": 0, "superseded": 1, "checked": 2, "kept": 2}


def _later_round(x: dict, y: dict) -> dict:
    return max((x, y), key=lambda r: (_ROUND_ORDER.get(r.get("status"), 0),
                                      str(((r.get("verdict") or {}).get("at")) or "")))


def _newer(x: dict, y: dict) -> dict:
    return max((x, y), key=lambda r: str(r.get("at") or r.get("updated") or r.get("finished") or ""))


def _side(person_id, name, at, words, data, key) -> dict:
    return {"key": key, "by": person_id, "name": name, "at": at, "words": words, "data": data}


def merge_conflicts(project: Project, person: dict) -> dict:
    """Deal with every clash copy: merge what can be merged, record a clash for the rest, and put the copy
    aside. Returns {"merged": n, "clashes": [ids]}."""
    merged, new = 0, []
    questions = {q.get("id"): q for q in project.read("questions").get("questions") or []} \
        if (project.path / "questions.json").is_file() else {}
    who = names(project)
    for copy, base, service in find_conflicts(project):
        try:
            got = _merge_one(project, person, copy, base, service, questions, who)
        except Exception:  # noqa: BLE001 - one copy that cannot be merged must not stop the others
            continue
        merged += 1
        new.extend(got)
    return {"merged": merged, "clashes": new}


def _merge_one(project: Project, person: dict, copy: Path, base: Path, service: str, questions: dict,
               who: dict) -> list[str]:
    """Merge one clash copy into its original (see merge_conflicts); the ids of the clashes it made."""
    new: list[str] = []
    if not base.exists():                 # the original was removed on one computer: the copy is all there is
        os.replace(copy, base)
        return new
    rel = base.relative_to(project.path).as_posix()
    top = rel.split("/")[0]
    a, b = _load(base), _load(copy)
    if base.suffix == ".jsonl" or copy.suffix == ".jsonl":
        lines = _union(base.read_text(encoding="utf-8").splitlines(),
                       copy.read_text(encoding="utf-8").splitlines())
        base.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif a is None or b is None:
        # Not JSON (a report, a document's file), or a half-written file. Text a person wrote becomes a
        # clash; anything else keeps the version in the folder (the copy is set aside, not lost).
        text = base.suffix in (".md", ".txt", ".csv", ".json")
        if text and _bytes(base) != _bytes(copy):
            new.append(add_clash(project, {
                "kind": "file", "source": "sync", "file": rel, "title": f"the file {rel}",
                "sides": [_side(None, "This copy", None, "the version in the folder",
                                base.read_text(encoding="utf-8", errors="replace"), "a"),
                          _side(None, f"{service}'s copy", None, f"the copy {service} kept",
                                copy.read_text(encoding="utf-8", errors="replace"), "b")]}, person)["id"])
    elif a == b:
        pass
    elif rel.startswith("answers/earlier/") and isinstance(a, dict) and isinstance(b, dict):
        # What answers said before a run replaced them (ingest.keep_earlier): both computers' records, oldest first.
        cells_a, cells_b = a.get("cells") or {}, b.get("cells") or {}
        for qid in set(cells_a) | set(cells_b):
            both = _union(cells_a.get(qid) or [], cells_b.get(qid) or [])
            cells_a[qid] = sorted(both, key=lambda v: str((v or {}).get("replaced") or "") if isinstance(v, dict) else "")
        a["cells"] = cells_a
        write_json(base, a)
    elif top == "answers" and isinstance(a, dict) and isinstance(b, dict):
        cells_a, cells_b = a.get("cells") or {}, b.get("cells") or {}
        row = a.get("row") or base.stem
        for qid in sorted(set(cells_a) | set(cells_b)):
            ca, cb = cells_a.get(qid), cells_b.get(qid)
            if cb is None or ca == cb:
                continue
            if ca is None:
                cells_a[qid] = cb
                continue
            if cell_rev(ca) == cell_rev(cb):              # the same answer; one is just fuller
                cells_a[qid] = ca if len(json.dumps(ca)) >= len(json.dumps(cb)) else cb
                continue
            q = questions.get(qid)
            new.append(add_clash(project, {
                "kind": "cell", "source": "sync", "row": row, "qid": qid,
                "title": f"{row}, {(q or {}).get('short') or qid}",
                "sides": [_cell_side(ca, q, who, "a"), _cell_side(cb, q, who, "b")]}, person)["id"])
        a["cells"] = cells_a
        write_json(base, a)
    elif rel == "rules.json" and isinstance(a, dict) and isinstance(b, dict):
        ra, rb = _by_id(a.get("rules")), _by_id(b.get("rules"))
        for rid, rule in rb.items():
            if rid not in ra:
                a.setdefault("rules", []).append(rule)
            elif rule_rev(ra[rid]) != rule_rev(rule) or ra[rid].get("text") != rule.get("text"):
                new.append(add_clash(project, {
                    "kind": "rule", "source": "sync", "rule": rid, "title": f"rule {rid}",
                    "sides": [_rule_side(ra[rid], who, "a"), _rule_side(rule, who, "b")]}, person)["id"])
        write_json(base, a)
    elif rel == "format.json" and isinstance(a, dict) and isinstance(b, dict):
        # Two computers upgraded the project's files at once: the higher format is the one both now have.
        write_json(base, a if (a.get("format") or 1) >= (b.get("format") or 1) else b)
    elif rel == "upgrades.json":
        if isinstance(a, list) and isinstance(b, list):
            write_json(base, _union(a, b))
        elif isinstance(a, dict) and isinstance(b, dict):
            write_json(base, {**b, **a, **{k: _union(a.get(k), b.get(k)) for k in set(a) | set(b)
                                           if isinstance(a.get(k), list) or isinstance(b.get(k), list)}})
    elif rel == "edits-applied.json":
        write_json(base, {**a, "applied": _union(a.get("applied"), b.get("applied"))})
    elif rel == "corrections.json":
        write_json(base, {**a, "corrections": _merge_records(a.get("corrections"), b.get("corrections"),
                                                             _later_round)})
    elif rel == "control.json":
        newer, older = (a, b) if str(a.get("updated") or "") >= str(b.get("updated") or "") else (b, a)
        seen = set(newer.get("applied") or [])
        # A re-run only in the older copy: the newer one finished it if it had seen the request, so it
        # stays finished; one the newer copy never saw is still waiting.
        reruns = list(newer.get("reruns") or []) + [r for r in older.get("reruns") or [] if isinstance(r, dict)
                                                    and r.get("id") not in seen
                                                    and r.get("id") not in {x.get("id") for x in newer.get("reruns") or []}]
        write_json(base, {**newer, "applied": _union(older.get("applied"), newer.get("applied")), "reruns": reruns,
                          "paused": bool(newer.get("paused") or older.get("paused")),
                          "stop_now": bool(newer.get("stop_now") or older.get("stop_now"))})
    elif rel == TEAM_FILE:
        members = {**(b.get("members") or {}), **(a.get("members") or {})}
        write_json(base, {"since": min(filter(None, [a.get("since"), b.get("since")]), default=now_stamp()),
                          "members": members})
    elif top == "page-data" or top in ("runs", "checks", "found", "compare") or rel.startswith(JOBS_DIR):
        keep = _newer(a, b) if isinstance(a, dict) and isinstance(b, dict) else a
        if top == "runs" and isinstance(a, dict) and isinstance(b, dict):
            keep = b if b.get("finished") and not a.get("finished") else a
        write_json(base, keep)
    elif rel.startswith(CLASH_DIR):
        keep = a if not a.get("open") else b if not b.get("open") else a
        write_json(base, keep)
    else:
        new.append(add_clash(project, {
            "kind": "file", "source": "sync", "file": rel, "title": f"the file {rel}",
            "sides": [_side(None, "This copy", None, "the version in the folder", a, "a"),
                      _side(None, f"{service}'s copy", None, f"the copy {service} kept", b, "b")]},
            person)["id"])
    _set_aside(project, copy)
    return new


def _cell_side(cell: dict, question, who: dict, key: str) -> dict:
    owner = cell.get("owner") if isinstance(cell.get("owner"), dict) else {}
    by = owner.get("by")
    name = who.get(by) if by else ("Claude" if cell.get("run") else None)
    return _side(by, name, owner.get("at"), value_words(question, cell.get("value")), cell, key)


def _rule_side(rule: dict, who: dict, key: str) -> dict:
    d = rule.get("decision") if isinstance(rule.get("decision"), dict) else {}
    words = d.get("text") or (f"option {d.get('option')}" if d.get("option") else rule.get("status") or "open")
    return _side(d.get("by"), who.get(d.get("by")) if d.get("by") else None, d.get("at"), words, rule, key)


# ---- Changes arriving through the sync service --------------------------------------------------------------

WATCH_TOP = ("answers", "checks", "found", "runs", "documents", "compare", "report", "team", "page-data")


def fingerprint(project: Project) -> str:
    """A short fingerprint of the project's shared files (names, sizes, change times): it changes when a
    colleague's change syncs in. Only file details are read, never contents, so it is cheap."""
    h = hashlib.sha1()
    for name in sorted(os.listdir(project.path)) if project.path.is_dir() else []:
        p = project.path / name
        if p.is_file() and name.endswith(".json"):
            _hash_stat(h, name, p)
    for top in WATCH_TOP:
        root = project.path / top
        if not root.is_dir():
            continue
        for folder, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in ("set-aside", "_log", "usage")
                             and not (Path(folder) == project.path / "team" and d in ("log", "jobs")))
            if top == "documents" and Path(folder) != root:
                files = [f for f in files if f == "meta.json"]
                dirs[:] = []
            for f in sorted(files):
                if not f.endswith(".tmp"):
                    _hash_stat(h, f"{folder}/{f}", Path(folder) / f)
    return h.hexdigest()[:16]


def _hash_stat(h, label: str, path: Path) -> None:
    try:
        st = path.stat()
    except OSError:
        return
    h.update(f"{label}:{st.st_size}:{st.st_mtime_ns};".encode("utf-8", "replace"))


def online_only(project: Project, limit: int = 5000) -> int:
    """How many of the project's files the sync service keeps online only (macOS marks them "dataless");
    each one is downloaded the moment the tool reads it, which is slow and fails offline."""
    if not hasattr(os.stat_result, "st_flags"):
        return 0
    n = seen = 0
    for folder, dirs, files in os.walk(project.path):
        dirs[:] = [d for d in dirs if d not in LEAVE_OUT_DIRS and not d.endswith(".app")]
        for f in files:
            seen += 1
            try:
                if os.lstat(os.path.join(folder, f)).st_flags & DATALESS:
                    n += 1
            except OSError:
                continue
            if seen >= limit:
                return n
    return n


# ---- Where a team can keep its folder -----------------------------------------------------------------------

def sync_service(path) -> str | None:
    """The sync service (or network share) a folder is in, or None for a folder only on this computer."""
    p = Path(path).expanduser()
    low = str(p).lower()
    parts = [x.lower() for x in p.parts]
    if "cloudstorage" in parts:
        name = p.parts[parts.index("cloudstorage") + 1] if len(p.parts) > parts.index("cloudstorage") + 1 else ""
        for needle, service in (("dropbox", "Dropbox"), ("googledrive", "Google Drive"), ("onedrive", "OneDrive"),
                                ("box", "Box"), ("icloud", "iCloud Drive")):
            if name.lower().startswith(needle):
                return service
    if "mobile documents" in low or "com~apple~clouddocs" in low:
        return "iCloud Drive"
    for needle, service in (("/dropbox", "Dropbox"), ("/google drive", "Google Drive"), ("/onedrive", "OneDrive"),
                            ("/box", "Box")):
        if needle in low:
            return service
    if len(p.parts) > 2 and p.parts[1] == "Volumes":
        return "a network share"
    return None


def places(home: Path | None = None) -> list[dict]:
    """The shared folders on this computer a team project could go in: {service, path, advice}."""
    home = home or Path.home()
    found = []
    cloud = home / "Library" / "CloudStorage"
    if cloud.is_dir():
        for d in sorted(cloud.iterdir()):
            if not d.is_dir() or d.name.lower().endswith("cloudtemp"):     # OneDrive's own scratch folder
                continue
            svc = sync_service(d)
            if svc == "Google Drive":
                for sub in ("My Drive", "Shared drives"):
                    if (d / sub).is_dir():
                        found.append({"service": svc, "path": str(d / sub)})
            elif svc:
                found.append({"service": svc, "path": str(d)})
    for d, svc in ((home / "Dropbox", "Dropbox"),
                   (home / "Library" / "Mobile Documents" / "com~apple~CloudDocs", "iCloud Drive")):
        if d.is_dir() and not any(f["path"] == str(d) for f in found):
            found.append({"service": svc, "path": str(d)})
    volumes = Path("/Volumes")
    if volumes.is_dir():
        for d in sorted(volumes.iterdir()):
            if d.is_dir() and not d.is_symlink() and d.name not in ("Macintosh HD", "Recovery", "Preboot"):
                if os.path.ismount(d) and not str(d).startswith("/Volumes/com.apple"):
                    found.append({"service": "a network share", "path": str(d)})
    for f in found:
        f["advice"] = ADVICE.get(f["service"], "")
    return found


ADVICE = {
    "Dropbox": "Works well. Turn on 'Make available offline' for the project folder.",
    "Google Drive": "Works well in 'Mirror files' mode, or with the project folder set to 'Available offline'.",
    "OneDrive": "Works. Right-click the project folder and choose 'Always keep on this device'.",
    "Box": "Works. Mark the project folder 'Make available offline'.",
    "iCloud Drive": "Works, but it is slow to sync and moves files online to save space. Not advised.",
    "a network share": "Changes show at once, but it only works while you are connected to it.",
}


# ---- Backups -------------------------------------------------------------------------------------------------

def _project_key(project: Project) -> str:
    return f"{project.slug}-{hashlib.sha1(str(project.path.resolve()).encode('utf-8')).hexdigest()[:10]}"


def backup_folder(project: Project) -> Path:
    return handbook_home() / "backups" / _project_key(project)


def _kept_files(project: Project, with_documents: bool):
    for folder, dirs, files in os.walk(project.path):
        rel = Path(folder).relative_to(project.path)
        top = rel.parts[0] if rel.parts else ""
        dirs[:] = sorted(d for d in dirs if not d.endswith(".app") and d not in LEAVE_OUT_DIRS
                         and not (rel == Path(".") and d == "documents" and not with_documents)
                         and not (rel.as_posix() == "team" and d == "set-aside")
                         and not (rel.as_posix() == "page-data" and d == "_log"))
        if top in LEAVE_OUT_DIRS:
            continue
        for f in sorted(files):
            if f in LEAVE_OUT_FILES or f.endswith(".tmp") or (_tool_written(rel) and conflict_of(
                    f, exists=lambda b, fs=set(files): b in fs)):
                continue
            yield Path(folder) / f


def take_backup(project: Project, force: bool = False, label: str | None = None) -> Path | None:
    """Today's snapshot of the project (without documents, which can be fetched again), in this person's
    own home folder; at most one a day unless `force`. Older than the newest 14 are removed."""
    folder = backup_folder(project)
    day = datetime.now().strftime("%Y-%m-%d")
    if label:                        # one of several a day (before a restore): the time keeps each name unique
        label = f"{label}-{datetime.now():%H%M%S}"
    name = f"{day}{'-' + label if label else ''}.zip"
    if not force and (folder / f"{day}.zip").exists():
        return None
    n = 2
    while label and (folder / name).exists():      # two in the same second: never overwrite one
        name = f"{day}-{label}-{n}.zip"
        n += 1
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for path in _kept_files(project, with_documents=False):
            z.write(path, path.relative_to(project.path).as_posix())
    os.replace(tmp, folder / name)
    daily = sorted(p for p in folder.glob("*.zip"))
    for old in daily[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return folder / name


def list_backups(project: Project) -> list[dict]:
    folder = backup_folder(project)
    out = []
    for p in sorted(folder.glob("*.zip"), reverse=True) if folder.is_dir() else []:
        try:
            when = datetime.strptime(p.name[:10], "%Y-%m-%d")
        except ValueError:
            continue
        out.append({"name": p.name, "day": f"{when.day} {when:%B %Y}", "label": p.stem[11:] or None,
                    "size": p.stat().st_size})
    return out


def restore(project: Project, name: str, person: dict) -> dict:
    """Put the project back as it was in a snapshot. A snapshot of how it is now is taken first, so the
    restore can itself be undone. Documents are left alone (snapshots do not hold them)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[A-Za-z0-9-]*\.zip", name or ""):
        raise HandbookError("Pick one of the snapshots listed.")
    path = backup_folder(project) / name
    if not path.is_file():
        raise HandbookError("That snapshot is not there any more.")
    import io
    chosen = io.BytesIO(path.read_bytes())    # read first: the snapshot taken next may prune this one
    take_backup(project, force=True, label="before-restore")
    with zipfile.ZipFile(chosen) as z:
        members = [m for m in z.namelist() if not m.endswith("/")]
        for m in members:
            if m.startswith("/") or ".." in Path(m).parts:
                raise HandbookError("That snapshot holds a file outside the project; nothing was restored.")
        keep = set(members)
        for current in list(_kept_files(project, with_documents=False)):
            rel = current.relative_to(project.path).as_posix()
            if rel not in keep and not rel.startswith(("team/log/", "team/clashes/", "team/jobs/")) \
                    and rel != TEAM_FILE and not rel.startswith("page-data/"):
                current.unlink(missing_ok=True)
        for m in members:
            if m.startswith(("team/log/", "team/jobs/")):
                continue                        # the record of what people did is never wound back
            target = project.path / m
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(z.read(m))
            os.replace(tmp, target)
    log(project, person, "restored", snapshot=name, text=f"{person.get('name') or 'Someone'} restored the "
        f"project to the snapshot {name[:-4]}")
    return {"restored": name, "files": len(members)}


# ---- Share with a team, and send a copy ----------------------------------------------------------------------

def share(project: Project, into, person: dict) -> Project:
    """Copy the project (and last edition's held-back results) into a shared folder and make it a team
    project there, with this person as its first member. The old folder is left as it was."""
    into = Path(into).expanduser()
    if not into.is_dir():
        raise HandbookError(f"There is no folder {into}.")
    if project.is_team():
        raise HandbookError("This project is already shared with a team.")
    dest = into / project.slug
    for d in (dest, into / project.held_back.name):
        if d.exists():
            raise HandbookError(f"There is already a folder called {d.name} in {into}. Move or rename it first.")
    if dest.resolve() == project.path.resolve() or project.path.resolve() in dest.resolve().parents:
        raise HandbookError("Pick a shared folder outside the project.")
    _copy_tree(project.path, dest)
    if project.held_back.is_dir():
        _copy_tree(project.held_back, into / project.held_back.name)
    new = Project(dest)
    start_team(new, person)
    service = sync_service(dest) or "a shared folder"
    log(new, person, "shared", where=service, text=f"{person.get('name') or 'Someone'} shared the project "
        f"with the team ({service}). Everything before this was done by {person.get('name') or 'them'} alone.")
    return new


def _copy_tree(src: Path, dest: Path) -> None:
    def ignore(folder, names):
        rel = Path(folder).relative_to(src)
        return {n for n in names if n in LEAVE_OUT_FILES or n.endswith((".app", ".tmp"))
                or (rel == Path(".") and n in LEAVE_OUT_DIRS)}
    shutil.copytree(src, dest, ignore=ignore)


def copy_zip(project: Project, dest: Path) -> Path:
    """One zip of the whole project (documents included, the page left out: it is rebuilt from the rest),
    for someone outside the team. Their changes never come back."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for path in _kept_files(project, with_documents=True):
            rel = path.relative_to(project.path).as_posix()
            if rel.startswith(("team/", "page-data/")) or rel == TEAM_FILE:
                continue                     # the team's own records stay with the team
            z.write(path, f"{project.slug}/{rel}")
        if project.held_back.is_dir():
            for path in sorted(p for p in project.held_back.rglob("*") if p.is_file()):
                z.write(path, f"{project.held_back.name}/{path.relative_to(project.held_back).as_posix()}")
    return dest


def unpack(zip_path, root) -> Project:
    """Open a copy someone sent: unzip it into `root` and return the project."""
    zip_path, root = Path(zip_path).expanduser(), Path(root).expanduser()
    with zipfile.ZipFile(zip_path) as z:
        names_ = [n for n in z.namelist() if not n.endswith("/")]
        if not names_ or any(n.startswith("/") or ".." in Path(n).parts for n in names_):
            raise HandbookError("That file is not a copy of a benchmark project.")
        top = sorted({Path(n).parts[0] for n in names_})
        slug = next((t for t in top if f"{t}/project.json" in names_), None)
        if not slug:
            raise HandbookError("That file is not a copy of a benchmark project (it has no project.json).")
        if (root / slug).exists():
            raise HandbookError(f"There is already a project called {slug} in {root}.")
        root.mkdir(parents=True, exist_ok=True)
        z.extractall(root, [n for n in names_ if Path(n).parts[0] in (slug, f"{slug}.held-back")])
    return Project(root / slug)

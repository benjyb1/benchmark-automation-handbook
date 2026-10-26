"""The project folder: paths, reading and writing, held-back and kit locations, the sync warning.

A project lives at `<root>/<slug>/`; last edition's results sit beside it in
`<root>/<slug>.held-back/`. Scorer kits are written outside both, under
`tempfile.gettempdir()/handbook-kits/<slug>/<row>/`. Every JSON file is written atomically with
`write_json`.
"""
from __future__ import annotations
import copy
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CHECKLIST_KEYS = ("questions", "units", "scoresheet", "group_labels", "cutoff", "where_to_look",
                  "needs", "rules", "last_results", "methodology", "source_list", "held_documents",
                  "last_report")
MUST_HAVES = CHECKLIST_KEYS[:8]
CHECKLIST_STATUSES = ("missing", "draft", "confirmed", "skipped", "not_needed")
SUBFOLDERS = ("inbox", "documents", "answers", "checks", "found", "runs", "report", "compare", "viewer")

# Top-level files `Project.read` and `Project.write` know, with what a missing file reads as.
DEFAULTS = {
    "project": {},
    "questions": {"questions": []},
    "units": {"units": []},
    "scoresheet": {},
    "rules": {"rules": []},
    "where_to_look": {"plan": [], "last_year": []},
    "needs": {"needs": []},
    "edits-applied": {"applied": []},
    "corrections": {"corrections": []},
    "estimate": {},
    "published": {},
    "control": {"paused": False, "stop_now": False, "stop_after_row": None,
                "scope": {"rows": None, "questions": None}, "stop_after": None,
                "reruns": [], "applied": [], "updated": None},
}

ID_RE = re.compile(r"^[a-z0-9-]+\Z")             # unit ids and slugs
_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*\Z")   # document and run ids used as file names


class HandbookError(Exception):
    """A problem a command explains to the owner. The command line prints it and exits 1."""


def now_stamp() -> str:
    """The current UTC time as an ISO timestamp ending in Z, to the second."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def handbook_home() -> Path:
    """This person's own folder for the tool: $HANDBOOK_HOME, else ~/.handbook (never shared)."""
    return Path(os.environ.get("HANDBOOK_HOME") or Path.home() / ".handbook").expanduser()


def read_json(path, default=None):
    """The JSON in `path`, or a copy of `default` when the file does not exist."""
    path = Path(path)
    if not path.is_file():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HandbookError(f"{path} is not valid JSON ({e.msg} at line {e.lineno}).") from e
    except UnicodeDecodeError as e:
        raise HandbookError(f"{path} is not valid UTF-8 text, so it cannot be read as JSON.") from e


def write_json(path, data) -> None:
    """Write `data` to `path` atomically (via `<name>.tmp`), UTF-8, indent 1, trailing newline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)      # the old file is untouched; do not leave the temp file behind
        raise


def _row_id(row: str) -> str:
    if not isinstance(row, str) or not ID_RE.match(row):
        raise ValueError(f"row id {row!r} must use only lowercase letters, digits and dashes")
    return row


def _file_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _NAME_RE.match(value):
        raise ValueError(f"{what} {value!r} must use only letters, digits, dots, dashes and underscores")
    return value


def _matches(label_filter, row) -> bool:
    """A row matches a `{label: [values]}` filter when, for every label given, its value is listed.
    None (or an empty filter) matches every row."""
    if not label_filter:
        return True
    labels = (row or {}).get("labels") or {}
    return all(labels.get(label) in (values or []) for label, values in label_filter.items())


def applies(question: dict, row: dict) -> bool:
    """Whether `question` applies to the unit `row` (its `applies_to` label filter; null = all)."""
    return _matches(question.get("applies_to"), row)


def effective_question(question: dict, row: dict) -> dict:
    """A copy of `question` as `row` sees it: the first variant whose `when` matches the row
    replaces `wording` and/or `rule` (a variant that gives no rule keeps the base rule)."""
    out = copy.deepcopy(question)
    for variant in question.get("variants") or []:
        if _matches(variant.get("when"), row):
            for key in ("wording", "rule"):
                if variant.get(key) is not None:
                    out[key] = variant[key]
            break
    return out


class Project:
    """One project folder. `path` is `<root>/<slug>`; `held_back` is its sibling."""

    def __init__(self, path):
        self.path = Path(os.path.abspath(os.path.expanduser(path)))    # abspath folds ".." away
        self.slug = self.path.name
        self.held_back = self.path.parent / f"{self.slug}.held-back"

    def __repr__(self) -> str:
        return f"Project({str(self.path)!r})"

    def exists(self) -> bool:
        """True when the folder holds a project.json."""
        return (self.path / "project.json").is_file()

    def is_team(self) -> bool:
        """True when the project is shared by a team (`team.json` in the folder; see team.py)."""
        return (self.path / "team.json").is_file()

    @property
    def viewer(self) -> Path:
        """Where `build` writes the page: `viewer/` in the project, or, for a team project, a folder of
        this person's own under the home folder, so two computers never rebuild the same shared files."""
        if self.is_team():
            import hashlib
            key = hashlib.sha1(str(self.path.resolve()).encode("utf-8")).hexdigest()[:10]
            return handbook_home() / "views" / f"{self.slug}-{key}"
        return self.path / "viewer"

    def shown_path(self, path) -> str:
        """`path` relative to the project when it is inside it, else in full (a team project's page)."""
        path = Path(path)
        try:
            return path.relative_to(self.path).as_posix()
        except ValueError:
            return str(path)

    # --- locations ----------------------------------------------------------------------------

    def file(self, name: str) -> Path:
        """The path of a named project file. Top-level names are those in DEFAULTS
        ("project", "questions", ..., "estimate", "published"); a name with a slash is a file in a
        subfolder, such as "compare/compare" or "report/facts" (".json" is added)."""
        if name in DEFAULTS:
            return self.path / f"{name}.json"
        if isinstance(name, str) and "/" in name:
            parts = name.split("/")
            if all(part and part not in (".", "..") for part in parts) and not name.startswith("/"):
                return self.path.joinpath(*parts[:-1], parts[-1] if parts[-1].endswith(".json")
                                          else parts[-1] + ".json")
        raise ValueError(f"unknown project file {name!r}; known names: {', '.join(DEFAULTS)}, "
                         "or a subfolder path such as 'compare/compare'")

    def kit_dir(self, row: str) -> Path:
        """Where the scorer kit for `row` goes: outside the project, in the system temp folder."""
        return Path(tempfile.gettempdir()) / "handbook-kits" / self.slug / _row_id(row)

    def doc_dir(self, doc: str) -> Path:
        """`documents/<doc>/` (not created)."""
        return self.path / "documents" / _file_id(doc, "document id")

    # --- named files --------------------------------------------------------------------------

    def read(self, name: str) -> dict:
        """A named file (see `file`); a missing file reads as its empty default."""
        return read_json(self.file(name), DEFAULTS.get(name, {}))

    def write(self, name: str, data: dict) -> None:
        """Write a named file atomically."""
        write_json(self.file(name), data)

    # --- per-row files ------------------------------------------------------------------------

    def answers(self, row: str) -> dict:
        return read_json(self.path / "answers" / f"{_row_id(row)}.json", {"row": row, "cells": {}})

    def write_answers(self, row: str, data: dict) -> None:
        write_json(self.path / "answers" / f"{_row_id(row)}.json", data)

    def earlier_answers(self, row: str) -> dict:
        """`answers/earlier/<row>.json`: what each answer said before a later run replaced it (ingest.py)."""
        return read_json(self.path / "answers" / "earlier" / f"{_row_id(row)}.json", {"row": row, "cells": {}})

    def write_earlier_answers(self, row: str, data: dict) -> None:
        write_json(self.path / "answers" / "earlier" / f"{_row_id(row)}.json", data)

    def checks(self, row: str) -> dict:
        return read_json(self.path / "checks" / f"{_row_id(row)}.json",
                         {"row": row, "row_flags": [], "cells": {}})

    def write_checks(self, row: str, data: dict) -> None:
        write_json(self.path / "checks" / f"{_row_id(row)}.json", data)

    def found(self, row: str) -> dict:
        return read_json(self.path / "found" / f"{_row_id(row)}.json",
                         {"row": row, "documents": [], "expected_not_found": [], "second_search": None})

    def cutoff_for(self, rows=None) -> str | None:
        """The cut-off that holds for these rows (ids): a row's own `cutoff` in units.json when it has one,
        else the project's; for several rows, the earliest, so a copy from before it serves them all. The kit
        and the checks always read a row's own date; the finder and fetch read the project's until sweep
        round 3 (TI: Chemring's 2020-04-30 lost to the project's 2020-12-31, and the finder saved its FY2020
        report and 2023 to 2025 pages)."""
        project = self.read("project").get("cutoff")
        units = {u.get("id"): u for u in self.read("units").get("units") or [] if isinstance(u, dict)}
        dates = [(units.get(r) or {}).get("cutoff") or project for r in rows or []]
        dates = [d for d in dates if isinstance(d, str) and d]
        return min(dates) if dates else project

    def write_found(self, row: str, data: dict) -> None:
        write_json(self.path / "found" / f"{_row_id(row)}.json", data)

    # --- documents and runs -------------------------------------------------------------------

    def documents(self) -> dict[str, dict]:
        """Document id (its folder name) -> meta.json, for every `documents/<doc>/meta.json`."""
        folder = self.path / "documents"
        if not folder.is_dir():
            return {}
        return {d.name: read_json(d / "meta.json") for d in sorted(folder.iterdir())
                if d.is_dir() and (d / "meta.json").is_file()}

    def pages(self, doc: str) -> list[dict]:
        """The pages of a kept document (`pages.json` "pages"); [] when there is no pages.json."""
        return (read_json(self.doc_dir(doc) / "pages.json", {}) or {}).get("pages") or []

    def capture(self, doc: str, capture_id: str) -> list[dict]:
        """The pages of one capture of a document: the current pages when `capture_id` is the one
        meta.json names (or, for a document read before captures were recorded, the one its
        current pages hash to), else `captures/<id>.json` (written by `prepare` each time the text
        it reads differs); [] when there is no such capture."""
        from .quotecheck import capture_id as hash_of          # standard library only, no cycle
        meta = read_json(self.doc_dir(doc) / "meta.json", {}) or {}
        pages = self.pages(doc)
        current = meta.get("capture")
        if current is None and pages:
            current = hash_of(p.get("text") or "" for p in pages if isinstance(p, dict))
        if current == capture_id:
            return pages
        path = self.doc_dir(doc) / "captures" / f"{_file_id(capture_id, 'capture id')}.json"
        return (read_json(path, {}) or {}).get("pages") or []

    def runs(self) -> list[dict]:
        """Every `runs/*.json` (top level only), sorted by id."""
        folder = self.path / "runs"
        if not folder.is_dir():
            return []
        runs = [(read_json(f), f.stem) for f in folder.glob("*.json") if f.is_file()]
        return [r for r, _ in sorted(runs, key=lambda x: str((x[0].get("id") if isinstance(x[0], dict)
                                                              else None) or x[1]))]

    def run(self, run_id: str) -> dict:
        """`runs/<run_id>.json`, or {} when there is no such run."""
        return read_json(self.path / "runs" / f"{_file_id(run_id, 'run id')}.json", {})

    def write_run(self, data: dict) -> None:
        """Write `runs/<data["id"]>.json` atomically."""
        write_json(self.path / "runs" / f"{_file_id(data.get('id'), 'run id')}.json", data)

    # --- questions per row --------------------------------------------------------------------

    def questions_for(self, row) -> list[dict]:
        """The questions that apply to `row` (a unit dict, or a unit id), in file order, each as
        the row sees it (the first matching variant's wording and rule applied; copies)."""
        if isinstance(row, str):
            unit = next((u for u in self.read("units").get("units", []) if u.get("id") == row), None)
            if unit is None:
                raise HandbookError(f"There is no unit {row!r} in units.json.")
            row = unit
        return [effective_question(q, row) for q in self.read("questions").get("questions", [])
                if applies(q, row)]


def init_project(root, name: str, slug: str) -> Project:
    """Create `<root>/<slug>/` with project.json (every checklist item missing, stage setup), the
    five empty set-up files and the subfolders. The held-back folder is not created."""
    if not isinstance(slug, str) or not ID_RE.match(slug):
        raise HandbookError(f"The slug {slug!r} must use only lowercase letters, digits and dashes, "
                            "for example example-benchmark-2025.")
    if not isinstance(name, str) or not name.strip():
        raise HandbookError("The benchmark needs a name.")
    root = Path(root).expanduser()
    if root.exists() and not root.is_dir():
        raise HandbookError(f"The root {root} is a file, not a folder. "
                            "Pass --root with the folder that should hold the projects.")
    project = Project(root / slug)
    if project.exists():
        raise HandbookError(f"There is already a project at {project.path}. "
                            "Choose another slug or folder, or carry on with that one.")
    for sub in SUBFOLDERS:
        (project.path / sub).mkdir(parents=True, exist_ok=True)
    project.write("project", {
        "name": name.strip(), "slug": slug, "edition": None,
        "unit_word": "unit", "unit_word_plural": "units",
        "cutoff": None, "labels": [], "group_labels": [], "group_by": None,
        "stage": "setup",
        "checklist": {key: "missing" for key in CHECKLIST_KEYS},
        "provided": [],
        "created": now_stamp(),
    })
    for key in ("questions", "units", "rules", "where_to_look", "needs"):
        project.write(key, DEFAULTS[key])
    from . import upgrade
    upgrade.stamp_new(project.path)
    return project


_SYNCED = (("mobile documents", "iCloud Drive"), ("com~apple~clouddocs", "iCloud Drive"),
           ("icloud drive", "iCloud Drive"), ("onedrive", "OneDrive"), ("dropbox", "Dropbox"),
           ("google drive", "Google Drive"), ("googledrive", "Google Drive"))


def icloud_desktop_and_documents(home: Path | None = None) -> bool:
    """Whether iCloud keeps the Mac's Desktop and Documents folders: their paths then look local
    (~/Documents) but are synced, and iCloud Drive holds a Documents folder."""
    home = home or Path.home()
    return (home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Documents").is_dir()


def default_root(home: Path | None = None) -> Path:
    """Where projects go unless the owner picks a folder: ~/Benchmark tool. Not in Documents:
    iCloud often syncs it, and macOS guards it (see `protected_folder`)."""
    return (home or Path.home()) / "Benchmark tool"


PROTECTED = ("Desktop", "Documents", "Downloads")


def protected_folder(path, home: Path | None = None) -> str | None:
    """"Desktop", "Documents" or "Downloads" when `path` is inside one of them, else None. macOS
    guards these folders: the double-click "Open ..." app is refused ("Operation not permitted")
    when the tool or the project sits in one, so neither should."""
    given, home = Path(path).expanduser(), home or Path.home()
    for name in PROTECTED:
        if given == home / name or home / name in given.parents:
            return name
    return None


def synced_folder_warning(path, home: Path | None = None) -> str | None:
    """A warning naming the sync service when `path` is inside iCloud Drive, OneDrive, Dropbox or
    Google Drive, or inside ~/Desktop or ~/Documents while iCloud syncs them; None otherwise, and None
    for a project shared by a team (its `team.json`), which belongs in a shared folder."""
    given = Path(path).expanduser()
    home = home or Path.home()
    if (given / "team.json").is_file():
        return None              # a team project is in a shared folder on purpose; team.py deals with syncing
    if icloud_desktop_and_documents(home):
        for kept in (home / "Desktop", home / "Documents"):
            if given == kept or kept in given.parents:
                return (f"{given} is in your {kept.name} folder, which iCloud syncs on this Mac. "
                        "Synced folders can upload, lock or duplicate files while the tool is "
                        f"writing them, so keep the project outside it, for example in {default_root(home)}.")
    candidates = [given]
    try:
        candidates.append(given.resolve())
    except OSError:
        pass
    for candidate in candidates:
        for part in candidate.parts:
            low = part.lower()
            for needle, service in _SYNCED:
                if low.startswith(needle):
                    return (f"{given} is inside {service}. Synced folders can upload, lock or "
                            "duplicate files while the tool is writing them, so keep the "
                            f"project outside {service}, for example in {default_root(home)}.")
    return None

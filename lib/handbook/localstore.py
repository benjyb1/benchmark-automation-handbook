"""What the page saves when it is served by `hb serve`, and the small config under the home folder.

    Store(project_path)            the page's collections, as plain files in the project folder
    Config()                       ~/.handbook/serve.json: the owner's name and the projects used

The store is the local stand-in for the Artifact database the published page uses (`claude.use("db")`).
Each document is one small JSON file, `<project>/page-data/<collection>/<id>.json`, holding the
document's own fields, so everything the owner saved can be read, copied or exported as it is.
A collection's `version` changes whenever a document in it is written or removed; the page polls it
(`onSnapshot`) and the server watches it to apply what the owner saved.

In a project shared by a team, each person's tool reads everyone's documents (the sync service brings
them in) but applies only its own person's (`by`); team.py and teamwork.py say how clashes are caught.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from .folder import HandbookError, handbook_home, read_json, write_json

STORE_DIR = "page-data"
COLLECTION_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
MAX_DOC_BYTES = 1_000_000


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


class Store:
    """The page's collections in `<project>/page-data/`. Thread-safe within one process."""

    def __init__(self, project_path):
        self.root = Path(project_path) / STORE_DIR
        self._lock = threading.Lock()

    def _folder(self, collection: str) -> Path:
        if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
            raise HandbookError(f"{collection!r} is not a collection name the page can use.")
        return self.root / collection

    def _file(self, collection: str, doc_id: str) -> Path:
        if not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id):
            raise HandbookError(f"{doc_id!r} is not a document id the page can use.")
        return self._folder(collection) / f"{doc_id}.json"

    def collections(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and COLLECTION_RE.match(p.name))

    def version(self, collection: str) -> str:
        """A short fingerprint of the collection: its file names, sizes and change times."""
        folder = self._folder(collection)
        h = hashlib.sha1()
        if folder.is_dir():
            for p in sorted(folder.glob("*.json")):
                try:
                    st = p.stat()
                except FileNotFoundError:
                    continue
                h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns};".encode())
        return h.hexdigest()[:16]

    def list(self, collection: str) -> list[dict]:
        """Every document in the collection, oldest file first, each with its `id`. A file that is
        not a JSON object is passed over (and left alone)."""
        folder = self._folder(collection)
        if not folder.is_dir():
            return []
        out = []
        files = []
        for p in folder.glob("*.json"):
            try:
                files.append((p.stat().st_mtime_ns, p.name, p))
            except FileNotFoundError:
                continue
        for _, _, p in sorted(files):
            if not DOC_ID_RE.match(p.stem):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                out.append({**data, "id": p.stem})
        return out

    def get(self, collection: str, doc_id: str) -> dict | None:
        path = self._file(collection, doc_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError:
            return None
        return {**data, "id": doc_id} if isinstance(data, dict) else None

    def set(self, collection: str, doc_id: str, data: dict) -> dict:
        if not isinstance(data, dict):
            raise HandbookError("A document must be a JSON object.")
        doc = {**data, "id": doc_id}
        if len(json.dumps(doc, ensure_ascii=False).encode("utf-8")) > MAX_DOC_BYTES:
            raise HandbookError("That document is too large to save (over 1 MB).")
        path = self._file(collection, doc_id)
        with self._lock:
            write_json(path, doc)
        return doc

    def update(self, collection: str, doc_id: str, fields: dict) -> dict:
        if not isinstance(fields, dict):
            raise HandbookError("An update must be a JSON object.")
        with self._lock:
            current = self.get(collection, doc_id)
            if current is None:
                raise HandbookError(f"There is no document {doc_id} in {collection} to update.")
            doc = {**current, **fields, "id": doc_id}
            write_json(self._file(collection, doc_id), doc)
        return doc

    def delete(self, collection: str, doc_id: str) -> bool:
        path = self._file(collection, doc_id)
        with self._lock:
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False


# ---- The config under the home folder ------------------------------------------------------------

def home() -> Path:
    """Where `hb serve` keeps its config: $HANDBOOK_HOME, else ~/.handbook."""
    return handbook_home()


class Config:
    """~/.handbook/serve.json: {"user": {"name"}, "last_project": path,
    "projects": [{"path", "name", "opened"}]}. Nothing secret is kept here."""

    MAX_PROJECTS = 20

    def __init__(self, folder: Path | None = None):
        self.path = (folder or home()) / "serve.json"
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            data = read_json(self.path, {})
        except HandbookError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        write_json(self.path, data)

    def user_name(self) -> str | None:
        name = (self.read().get("user") or {}).get("name")
        return name.strip() if isinstance(name, str) and name.strip() else None

    def set_user_name(self, name: str) -> str:
        name = " ".join(str(name or "").split())[:80]
        if not name:
            raise HandbookError("Write a name.")
        with self._lock:
            data = self.read()
            data["user"] = {**(data.get("user") or {}), "name": name}
            self._write(data)
        return name

    def last_project(self) -> str | None:
        last = self.read().get("last_project")
        return last if isinstance(last, str) and last else None

    def remember(self, project_path, name: str | None) -> None:
        path = str(Path(project_path).expanduser().resolve())
        with self._lock:
            data = self.read()
            projects = [p for p in data.get("projects") or [] if isinstance(p, dict) and p.get("path") != path]
            projects.insert(0, {"path": path, "name": name, "opened": _stamp()})
            data["projects"] = projects[: self.MAX_PROJECTS]
            data["last_project"] = path
            self._write(data)

    def projects(self) -> list[dict]:
        return [p for p in self.read().get("projects") or [] if isinstance(p, dict) and p.get("path")]


def user_id(name: str | None) -> str | None:
    """The id saved with the owner's edits (`by`): their name as lowercase words joined by dashes."""
    if not name:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or None

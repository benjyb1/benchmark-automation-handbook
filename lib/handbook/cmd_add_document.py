"""hb add-document FILE --rows ROW,... [--title T]: file a document the owner provided (a PDF, a
web page saved as HTML, or a text file) and note which rows it belongs to. The document's id comes
from the file's bytes, so adding the same file twice files it once. Run `hb prepare` afterwards.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

from . import fetch
from .cli import command_parser, open_project
from .folder import HandbookError

KINDS = {".pdf": "pdf", ".html": "html", ".htm": "html", ".txt": "text", ".text": "text",
         ".md": "text"}


def main(argv) -> int:
    parser = command_parser("add-document", "File a document the owner provided.")
    parser.add_argument("file", help="the document (PDF, HTML or text)")
    parser.add_argument("--rows", required=True, help="the rows it belongs to, separated by commas")
    parser.add_argument("--title", default=None, help="what to call it (default: from the file name)")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    wanted = [r.strip() for r in args.rows.split(",") if r.strip()]
    if not wanted:
        raise HandbookError("--rows needs at least one row id.")
    add_file(project, Path(args.file).expanduser(), wanted, args.title)
    return 0


def add_file(project, path: Path, rows: list, title: str | None = None, say=print) -> str:
    """File the document at `path` for `rows` (once, by its bytes) and return its id. What happened
    goes to `say`. Raises HandbookError for a missing file, a kind the handbook cannot read or an
    unknown row."""
    if not path.is_file():
        raise HandbookError(f"There is no file at {path}.")
    kind = KINDS.get(path.suffix.lower())
    if kind is None:
        raise HandbookError(f"The handbook can read PDF, HTML and text files, not "
                            f"{path.suffix or 'this kind'} ({path.name}). Save it as a PDF or a "
                            "text file and add that.")
    rows = fetch.resolve_rows(project, rows)

    data = path.read_bytes()
    doc = fetch.doc_id_for_bytes(data)
    if fetch.has_document(project, doc):
        added = fetch.add_to_found(project, rows, doc)
        title = project.documents()[doc].get("title") or doc
        say(f"Already have {doc} ({title}); it is filed once. "
            + (f"Added it to {', '.join(added)}." if added else "It was already filed for those rows."))
        return doc
    title = (title or "").strip() or fetch.title_from_name(path.name) or path.name
    meta = fetch.new_meta(doc, title=title, how="provided", provided=True, host_type="none",
                          sha1=hashlib.sha1(data).hexdigest())
    fetch.store_document(project, meta, data, fetch.EXTENSIONS[kind])
    fetch.add_to_found(project, rows, doc)
    say(f"Added {path.name} as {doc}, titled \"{title}\", for {len(rows)} row"
        f"{'s' if len(rows) != 1 else ''}: {', '.join(rows)}.")
    say("Run prepare to read its text.")
    return doc

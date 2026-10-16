"""hb add-document FILE --rows ROW,... [--title T]: file a document the owner provided (a PDF, a picture,
a web page saved as HTML, a Word, Excel or PowerPoint file, a text file or any other kind in readers.py)
and note which rows it belongs to. The document's id comes from the file's bytes, so adding the same
file twice files it once. Run `hb prepare` afterwards.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

from . import edition, fetch, readers
from .cli import command_parser, open_project
from .folder import HandbookError

def main(argv) -> int:
    parser = command_parser("add-document", "File a document the owner provided.")
    parser.add_argument("file", help="the document (PDF, picture, Word, Excel, HTML, text and more)")
    parser.add_argument("--rows", required=True, help="the rows it belongs to, separated by commas")
    parser.add_argument("--title", default=None, help="what to call it (default: from the file name)")
    parser.add_argument("--url", default=None,
                        help="where the file came from, when it was downloaded by hand (a site the tool could not "
                             "fetch): it is then a found document, not one the owner provided")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="the date it speaks as at (the law as at, the data as at), over the file's own date")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    wanted = [r.strip() for r in args.rows.split(",") if r.strip()]
    if not wanted:
        raise HandbookError("--rows needs at least one row id.")
    doc = add_file(project, Path(args.file).expanduser(), wanted, args.title, url=args.url)
    if args.date:
        fetch.set_as_at(project, doc, args.date)
        print(f"Dated {doc} {args.date}: the date it speaks as at.")
    return 0


def add_file(project, path: Path, rows: list, title: str | None = None, say=print, url: str | None = None) -> str:
    """File the document at `path` for `rows` (once, by its bytes) and return its id. What happened
    goes to `say`. Raises HandbookError for a missing file, a kind the handbook cannot read or an
    unknown row."""
    if not path.is_file():
        raise HandbookError(f"There is no file at {path}.")
    data = path.read_bytes()
    kind = readers.kind_for_name(path.name) or readers.kind_of(data, "", path.name)
    if kind is None or readers.cannot_read(path.name, "", data):
        raise HandbookError(readers.cannot_read(path.name, "", data) or
                            f"The tool cannot read {path.suffix or 'this kind of file'} ({path.name}). It reads "
                            f"{readers.READABLE_WORDS} files: save it as one of those and add that.")
    refusal = edition.evidence_refusal(kind)
    if refusal:                                  # the free edition takes PDFs, web pages and text as evidence
        raise HandbookError(refusal)
    rows = fetch.resolve_rows(project, rows)

    doc = fetch.doc_id_for_bytes(data)
    if fetch.has_document(project, doc):
        added = fetch.add_to_found(project, rows, doc)
        title = project.documents()[doc].get("title") or doc
        say(f"Already have {doc} ({title}); it is filed once. "
            + (f"Added it to {', '.join(added)}." if added else "It was already filed for those rows."))
        return doc
    title = (title or "").strip() or fetch.title_from_name(path.name) or path.name
    if url:
        from .hosts import on_results_site
        site = on_results_site(url, project.read("project").get("results_sites"))
        if site:
            raise HandbookError(f"{url} is on {site}, where the benchmark publishes its results, so it was not "
                                "added: the scorer must answer from the organisation's own documents.")
    if url:
        # Downloaded by hand from a site the tool could not fetch (INAO, India Code): keep where it came
        # from, judged like any fetched document (ALI and the Animal Protection Index, 14 Oct 2026).
        from .hosts import classify_host
        host_type, _, archive_url = classify_host(url)
        meta = fetch.new_meta(doc, title=title, how="search", url=url.strip(), archive_url=archive_url,
                              host_type=host_type, sha1=hashlib.sha1(data).hexdigest())
    else:
        meta = fetch.new_meta(doc, title=title, how="provided", provided=True, host_type="none",
                              sha1=hashlib.sha1(data).hexdigest())
    fetch.store_document(project, meta, data, readers.stored_extension(kind, data, path.name))
    fetch.add_to_found(project, rows, doc)
    say(f"Added {path.name} as {doc}, titled \"{title}\", for {len(rows)} row"
        f"{'s' if len(rows) != 1 else ''}: {', '.join(rows)}.")
    say("Run prepare to read its text.")
    return doc

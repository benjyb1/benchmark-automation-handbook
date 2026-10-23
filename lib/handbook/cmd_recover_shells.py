"""hb recover-shells [--doc ID] [--dry-run]: replace saved web pages that are script shells with their words.

A page built by JavaScript can be saved as its empty frame ("Please enable JavaScript", or a few words of
menu), so every quote from it fails the check against the saved page ("Quote not on saved page"), though
Claude read the words when it scored. `hb fetch` has handled this since it learnt to render pages
(render.py), but documents saved before that, or brought in from another run, can still be shells. This
finds each kept web page whose text is a shell and tries what fetch tries: for a cut-off that has
passed, the archive's capture from the year before it; then the page in a browser (the owner's Chrome);
then the archive's latest capture before the cut-off. `--doc ID --archive` replaces one saved page with
the archive's copy from before the cut-off, for a page that has changed since. When one gives words, it becomes the saved copy and is read
again (`prepare`; the old text is kept under captures/). Then `hb check` runs, so the flags follow.

It also retitles saved pages whose title reads like an error ("Not Found") though their text is a real
page: a scorer once took such a title at its word and scored the page as no evidence.

No AI is used. Pages it cannot recover are listed and left as they are.
"""
from __future__ import annotations
import hashlib
import re

from . import fetch, render
from .cli import command_parser, open_project
from .folder import HandbookError, read_json, write_json
from .prepare import find_original, is_stub, prepare_document


def shells(project) -> list[str]:
    """Kept web pages whose saved text is a shell."""
    out = []
    for doc, meta in sorted(project.documents().items()):
        path = find_original(project, doc)
        if not meta.get("kept") or path is None or path.suffix not in (".html", ".htm", ".txt"):
            continue
        pages = (read_json(project.doc_dir(doc) / "pages.json", {}) or {}).get("pages") or []
        texts = [p.get("text") or "" for p in pages]
        if is_stub(texts) or render.is_shell(" ".join(texts)):
            out.append(doc)
    return out


def archive_only(url: str, cutoff: str | None) -> dict:
    """The archive's capture from the year before the cut-off, raw, when it is not a shell."""
    for stamp in [x for x in render.archive_captures(url, cutoff) if render._near(x, cutoff)][:2]:
        raw = fetch.fetch_bytes(render.ARCHIVE_RAW.format(stamp=stamp, url=url))
        if raw.ok and not render.is_shell(fetch.extract_text_html(raw.content)):
            return {"kind": "html", "content": raw.content, "title": fetch.html_title(raw.content),
                    "text_source": "archive", "archive_url": render.ARCHIVE_PAGE.format(stamp=stamp, url=url)}
        got = render.render_text(render.ARCHIVE_PAGE.format(stamp=stamp, url=url))   # the capture, in Chrome
        if got is not None and not render.is_shell(got["text"]):
            return {"kind": "text", "content": got["text"].encode("utf-8"), "title": got["title"],
                    "text_source": "archive_rendered", "archive_url": render.ARCHIVE_PAGE.format(stamp=stamp, url=url)}
    return {"kind": None, "why": "the archive has no readable capture from the year before the cut-off"}


def fix_titles(project) -> list[tuple[str, str, str]]:
    """Saved web pages titled like an error ("Not Found") whose text is a real page get a title from
    their address, so no scorer reads the title as the page being missing. The old title is kept
    as `title_was`. Returns [(doc, old, new)]."""
    out = []
    for doc, meta in sorted(project.documents().items()):
        if not meta.get("kept") or not meta.get("url"):
            continue
        pages = (read_json(project.doc_dir(doc) / "pages.json", {}) or {}).get("pages") or []
        text = " ".join(p.get("text") or "" for p in pages)
        if fetch.misleading_title(meta.get("title") or "", text):
            new = fetch.title_from_url(meta["url"])
            meta.update({"title_was": meta.get("title"), "title": new})
            write_json(project.doc_dir(doc) / "meta.json", meta)
            out.append((doc, meta["title_was"], new))
    return out


def recover_one(project, doc: str, cutoff: str | None, archive: bool = False) -> tuple[bool, str]:
    meta = project.documents()[doc]
    url = meta.get("url")
    if not url:
        return False, "it has no web address to fetch again"
    got = archive_only(url, cutoff) if archive else render.recover(url, cutoff)
    if got["kind"] is None:
        return False, got["why"]
    folder = project.doc_dir(doc)
    for old in folder.glob("original.*"):
        old.unlink()
    new = dict(meta)
    new.update({"text_source": got["text_source"], "sha1": hashlib.sha1(got["content"]).hexdigest(),
                "file": None})
    if got["archive_url"]:
        new["archive_url"] = got["archive_url"]
        new.pop("todays_version", None)        # the copy from before the cut-off, not today's page
        stamp = re.search(r"/web/(\d{8})", got["archive_url"])
        if stamp and not new.get("date"):
            s = stamp.group(1)
            new["date"], new["date_basis"] = f"{s[:4]}-{s[4:6]}-{s[6:8]}", "archive capture"
    fetch.store_document(project, new, got["content"], fetch.EXTENSIONS[got["kind"]])
    read = prepare_document(project, doc)
    how = {"rendered": "the page as a browser shows it", "archive": "the archive's capture",
           "archive_rendered": "the archive's capture as a browser shows it"}[got["text_source"]]
    chars = sum(len(p.get("text") or "") for p in (read_json(folder / "pages.json", {}) or {}).get("pages") or [])
    return True, f"now saved from {how} ({chars:,} characters)"


def main(argv) -> int:
    parser = command_parser("recover-shells", "Replace saved web pages that are script shells with their words.")
    parser.add_argument("--doc", default=None, help="just this document")
    parser.add_argument("--dry-run", action="store_true", help="list the shells without fetching")
    parser.add_argument("--archive", action="store_true",
                        help="with --doc: take the archive's copy from the year before the cut-off, even if the "
                             "saved page is not a shell (for a page that has changed since the cut-off)")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    if not args.dry_run:
        for doc, old, new in fix_titles(project):
            print(f"{doc}: its title said \"{old}\" though the page holds text; now titled \"{new}\".")
    todo = shells(project)
    if args.archive and not args.doc:
        raise HandbookError("--archive needs --doc: say which saved page to replace with the archive's copy.")
    if args.doc:
        if args.doc not in todo and not (args.archive and args.doc in project.documents()):
            raise HandbookError(f"{args.doc} is not a saved web page whose text is a shell "
                                "(add --archive to replace it with the archive's copy anyway).")
        todo = [args.doc]
    if not todo:
        print("No saved web page is a shell. Nothing to do.")
        return 0
    docs = project.documents()
    print(f"{len(todo)} saved web page{'s are' if len(todo) != 1 else ' is a'} script shell{'s' if len(todo) != 1 else ''}:")
    if args.dry_run:
        for doc in todo:
            print(f"  {doc}  {docs[doc].get('url')}")
        return 0
    fixed = 0
    for doc in todo:
        rows = [u.get("id") for u in project.read("units").get("units") or []
                if doc in (project.found(u.get("id")).get("documents") or [])]
        ok, said = recover_one(project, doc, project.cutoff_for(rows), archive=args.archive)
        fixed += ok
        print(f"  {doc}  {docs[doc].get('url')}\n    {'Recovered: ' if ok else 'Not recovered: '}{said}")
    if fixed:
        from . import cmd_check
        cmd_check.main(["--project", str(project.path)])
    print(f"Recovered {fixed} of {len(todo)}.")
    return 0

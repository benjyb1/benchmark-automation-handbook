"""hb fetch URL --row ROW [--group LABEL=VALUE] --how plan|search|last_year|provided [--weak WHY]: fetch a
document politely into `documents/` and add it to the row's `found/` file (and to every row whose
label matches the group). A web address already in `documents/` is not fetched again; it is just
added to the row's `found/`. `--weak WHY` marks it a weak source (not in the organisation's own
words), also when it was saved before, unless someone cleared it. Run `hb prepare` afterwards.
"""
from __future__ import annotations
import argparse
import hashlib
import re
import urllib.parse

from . import fetch, readers, render
from .cli import command_parser, open_project
from .folder import HandbookError, write_json
from .hosts import classify_host, on_results_site


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, round(n / 1000))} KB"


def _rows_text(rows) -> str:
    return f"{len(rows)} row{'s' if len(rows) != 1 else ''}: {', '.join(rows)}"


def _group(text: str) -> str:
    label, sep, value = text.partition("=")
    if not (sep and label.strip() and value.strip()):
        raise argparse.ArgumentTypeError(f"should look like LABEL=VALUE, for example brand=Acme (got {text!r})")
    return text


def _why(text: str) -> str:
    try:
        return fetch.clean_why(text)
    except HandbookError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def main(argv) -> int:
    parser = command_parser("fetch", "Fetch a document into the project and note which rows it "
                                     "belongs to.")
    parser.add_argument("url", help="the web address of the document")
    parser.add_argument("--row", action="append", default=None,
                        help="the row it was found for (repeat for more)")
    parser.add_argument("--group", default=None, metavar="LABEL=VALUE", type=_group,
                        help="add it to every row whose label matches, for example brand=Acme")
    parser.add_argument("--how", required=True, choices=("plan", "search", "last_year", "provided"),
                        help="how it was found: the where-to-look plan, a search, last year's list, "
                             "or a link the owner provided")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="the date it speaks as at, when the document says so, over its file date")
    parser.add_argument("--weak", default=None, metavar="WHY", type=_why,
                        help="mark it a weak source and say why in one line, for example "
                             "\"A news report, not the organisation's own words\"")
    args = parser.parse_args(argv)
    if not args.row and not args.group:
        parser.error("give --row ROW, --group LABEL=VALUE, or both")
    project = open_project(args.project)
    rows = fetch.resolve_rows(project, args.row, args.group)       # checked before any fetching
    doc = save_url(project, args.url, rows, args.how, args.weak)
    if args.date:
        fetch.set_as_at(project, doc, args.date)
        print(f"Dated {doc} {args.date}: the date it speaks as at.")
    return 0


def save_url(project, url: str, rows: list, how: str, weak: str | None = None, say=print) -> str:
    """Fetch `url` into documents/ (or find it there already) and file it for `rows`; returns the
    document id. What happened goes to `say`, one sentence at a time. Raises HandbookError when the
    page does not load or is not something the handbook can read; nothing is saved then."""
    url = url.strip()
    site = on_results_site(url, project.read("project").get("results_sites"))
    if site:
        raise HandbookError(f"{url} is on {site}, where the benchmark publishes its results, so it was not "
                            "saved: the scorer must answer from the organisation's own documents.")
    doc = fetch.doc_id_for_url(url)

    if fetch.has_document(project, doc):
        meta = project.documents()[doc]
        title = meta.get("title") or doc
        added = fetch.add_to_found(project, rows, doc)
        refused = [r for r in rows if doc in (project.found(r).get("removed") or [])]
        if refused:
            say(f"{doc} was taken off {_rows_text(refused)} before (found --remove), so it was not filed there again.")
        if added:
            say(f"Already have {doc} ({title}), so it was not fetched again. "
                f"Added it to {_rows_text(added)}.")
        else:
            say(f"Already have {doc} ({title}) and it is already filed for {_rows_text(rows)}."
                + ("" if weak else " Nothing to do."))
        if weak and meta.get("weak") is False:
            say("It was cleared as not weak before, so it was left as it is.")
        elif weak and not isinstance(meta.get("weak"), dict):
            fetch.mark_weak(project, doc, weak)
            say(f"Marked it as a weak source: {weak}")
        return doc

    result = fetch.fetch_bytes(url)
    if result.failure == "too-large":
        raise HandbookError(f"Could not fetch {url}: {fetch.TOO_LARGE}.")
    archived_note = None
    if not result.ok:
        # A site that blocks robots (EUR-Lex's 202), refuses, or fails: the archive's capture from
        # before the cut-off, when there is one, is the same document.
        cutoff = project.read("project").get("cutoff")
        copy = render.archive_copy(url, cutoff)
        if copy is None or not copy.ok:
            raise HandbookError(f"Could not fetch {url}: {fetch.describe_failure(result)}, and "
                                f"{render.archive_why_not(url, cutoff)}. Nothing was saved.")
        archived_note = (f"The site would not give the page ({fetch.describe_failure(result)}), so the web "
                         f"archive's capture was kept: {copy.url}")
        result = fetch.FetchResult(url, 200, copy.content, copy.content_type)
        archive_from_copy = copy.url
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    kind = fetch.kind_of(result.content, result.content_type, name)
    refusal = readers.cannot_read(name, result.content_type, result.content)
    if kind is None or refusal:
        raise HandbookError(f"{url} gave {result.content_type or 'something'}, which the tool "
                            f"cannot read. Nothing was saved. "
                            + (refusal or f"It reads {readers.READABLE_WORDS} files: if you can save it as one of "
                                          "those, add that with add-document."))
    from . import edition
    if edition.evidence_refusal(kind):
        raise HandbookError(f"{url} gave a {readers.LABELS[kind]}. Nothing was saved. {edition.evidence_refusal(kind)}")
    title = (fetch.html_title(result.content) if kind == "html" else "") or fetch.title_from_url(url)
    if kind == "html" and fetch.dead_page(title, fetch.extract_text_html(result.content)):
        raise HandbookError(f"{url} is a placeholder page (\"{title.strip()[:80]}\"), not the document. Nothing was saved.")
    if kind == "html" and fetch.misleading_title(title, fetch.extract_text_html(result.content)):
        title = fetch.title_from_url(url)   # e.g. "Not Found | joejuice.com" on a page that holds the policy
    landed = on_results_site(result.final_url, project.read("project").get("results_sites")) if result.final_url else None
    if landed:
        # HSUS (15 Oct 2026): the owner's old results post now redirects to its new domain, which fetch saved.
        raise HandbookError(f"{url} leads to {result.final_url}, on {landed}, where the benchmark publishes its "
                            "results, so it was not saved.")
    host_type, _, archive_url = classify_host(url)
    content, notes, text_source, not_kept_why = result.content, [], None, None
    if archived_note:
        archive_url = archive_from_copy
        notes.append(archived_note)
    if kind == "html" and render.is_shell(fetch.extract_text_html(content)):
        # The server sent a script shell: the words the finder saw are not in it. Try the page
        # rendered, then the archive, before giving up on it.
        got = render.recover(url, project.read("project").get("cutoff"))
        if got["kind"] is None:
            not_kept_why = got["why"]
            notes.append(f"Not kept: {got['why']} Record it as not found for the row and look for "
                         "another copy (a PDF, a press release, a page the site serves as text).")
        else:
            kind, content, text_source = got["kind"], got["content"], got["text_source"]
            title = got["title"] or title
            archive_url = got["archive_url"] or archive_url
            notes.append({"rendered": "The page drew its words with a script, so its text was taken from the "
                                      "page as a browser shows it.",
                          "archive": "The live page is a script shell, so the archive's capture was kept instead.",
                          "archive_rendered": "The live page and the archive's capture are script shells, so the "
                                              "text was taken from the archived page as a browser shows it."}[text_source])
    meta = fetch.new_meta(doc, title=title, how=how, url=url, archive_url=archive_url,
                          host_type=host_type, sha1=hashlib.sha1(content).hexdigest() if not_kept_why is None else None,
                          provided=how == "provided")
    if text_source:
        meta["text_source"] = text_source
    stamp = re.search(r"/(?:web|ukgwa)/(\d{8})\d*", archive_url or "")
    if stamp:
        # An archive copy existed on its capture date, so it was published by then: a date to check the
        # cut-off against when the page gives none (four format tests, 15 Oct 2026, had null dates here).
        s = stamp.group(1)
        meta["date"], meta["date_basis"] = f"{s[:4]}-{s[4:6]}-{s[6:8]}", "archive capture"
    if weak:
        meta["weak"] = {"why": weak}
    if not_kept_why is not None:
        meta["not_kept_why"] = not_kept_why
        folder = project.doc_dir(doc)
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "meta.json", meta)          # filed by address only: no copy, no text
    else:
        fetch.store_document(project, meta, content, readers.stored_extension(kind, content, name))
    fetch.add_to_found(project, rows, doc)
    label = readers.LABELS[kind]
    say(f"Fetched {url} as {doc} ({label}, {_size(len(content))}), titled \"{title}\". "
        f"Filed for {_rows_text(rows)}.")
    for note in notes:
        say(note)
    if weak:
        say(f"Marked as a weak source: {weak}")
    elif fetch.is_weak(meta):
        say(f"It counts as a weak source ({fetch.weak_why(meta)}); prefer the organisation's own "
            "copy if there is one.")
    if not_kept_why is None:
        say("Run prepare to read its text.")
    return doc

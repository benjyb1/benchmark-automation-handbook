"""hb prepare [--doc ID]: read every saved document that has no text yet into `pages.json` (one page
per PDF page, slide or sheet; one page for a web page, a picture or a text file). The words in a picture
or on a scanned page are read with the Mac's own text reader. With --doc, read that one document, again if
it was already read. Documents known only by their web address, with no saved copy, are skipped
and counted.
"""
from __future__ import annotations

from . import readers
from .cli import command_parser, open_project
from .folder import HandbookError
from .prepare import find_original, pending_documents, prepare_document, unsaved_documents

LABELS = readers.LABELS


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def main(argv) -> int:
    parser = command_parser("prepare", "Read saved documents into text, page by page.")
    parser.add_argument("--doc", default=None, help="read just this document (again, if need be)")
    args = parser.parse_args(argv)
    project = open_project(args.project)

    if args.doc:
        if args.doc not in project.documents():
            known = sorted(project.documents())
            raise HandbookError(f"There is no document {args.doc}. "
                                f"{_plural(len(known), 'document')} in this project"
                                + (f": {', '.join(known[:10])}{' ...' if len(known) > 10 else ''}." if known else "."))
        if find_original(project, args.doc) is None:
            raise HandbookError(f"{args.doc} has no saved copy to read.")
        todo = [args.doc]
    else:
        todo = pending_documents(project)
        skipped = unsaved_documents(project)
        if not todo:
            print(f"Every saved document already has its text ({_plural(len(project.documents()) - len(skipped), 'document')}).")
            if skipped:
                print(f"{_plural(len(skipped), 'document')} with no saved copy, skipped: {', '.join(skipped)}.")
            return 0

    done, problems = [], []
    for doc in todo:
        try:
            done.append(prepare_document(project, doc))
        except HandbookError as e:
            if str(e) not in problems:               # the missing-tool message once, not once per PDF
                problems.append(str(e))
    print(f"{_plural(len(todo), 'document')} needed text; read {len(done)}.")
    for d in done:
        line = f"  {d['doc']}: {LABELS.get(d['kind'], d['kind'])}, {_plural(d['pages'], 'page')}"
        if d["language"]:
            line += f", {d['language']}"
        if d.get("ocr_pages"):
            line += (f". Words read from a picture on {d['ocr_pages']} of {d['pages']} "
                     "(OCR: a letter or figure can be wrong)")
            if d.get("ocr_quality") is not None:
                line += f", the reader's own confidence {round(d['ocr_quality'] * 100)}%"
        if d.get("pictures"):
            line += f". {_plural(d['pictures'], 'picture')} kept for the scorer to look at"
        if d.get("unread_pictures"):
            line += (". The words in the picture could not be read (no text reader on this computer), "
                     "so it can only be looked at")
        elif d["empty_pages"]:
            line += (f". No text on {d['empty_pages']} of {d['pages']} "
                     "(a scan or pictures? it will need another route)")
        if d.get("stub"):
            line += (". Almost no text: the page draws its words with a script, so this copy "
                     "cannot be quoted. Record it as not found so the second search looks for a "
                     "readable copy (an archived one, or a PDF)")
        print(line)
        for note in d.get("notes") or []:
            if "could not be read" not in note and not note.startswith("Read the words"):   # said above
                print(f"    {note}")
    if not args.doc and skipped:
        print(f"{_plural(len(skipped), 'document')} with no saved copy, skipped: {', '.join(skipped)}.")
    for problem in problems:
        print(problem)
    return 1 if problems else 0

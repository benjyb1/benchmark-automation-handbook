"""hb recheck --doc DOC | --row ROW | --all | --accept DOC | --look DOC [--json]: fetch saved web pages again
and compare their text with
the saved copy. Unchanged, the date of the check is recorded. Changed, the live page is saved as a new copy
beside the earlier one, which stays exactly as it was, with the lines added and removed; rows whose cut-off
has not passed read the new copy, and their answers citing the earlier one go out of date (never an answer a
person checked). When most of a page's text has gone, rows stay on the earlier copy until the owner chooses:
--accept DOC switches them to the new copy, --look DOC has the next search look for where the text went.
No Claude, no tokens. See recheck.py.
"""
from __future__ import annotations
import json
import time
import urllib.parse

from . import recheck
from .cli import command_parser, open_project
from .folder import HandbookError


def main(argv) -> int:
    parser = command_parser("recheck", "Check saved web pages against the live site, keeping every copy.")
    parser.add_argument("--doc", action="append", default=None, help="a document (repeat for more)")
    parser.add_argument("--row", action="append", default=None, help="every document filed for this row (repeat for more)")
    parser.add_argument("--all", action="store_true", help="every saved web page in the project")
    parser.add_argument("--accept", default=None, metavar="DOC",
                        help="use the new copy of this page, saved when most of its text had gone")
    parser.add_argument("--look", default=None, metavar="DOC",
                        help="have the next search look for where this page's text went")
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    args = parser.parse_args(argv)
    decide = [x for x in (args.accept, args.look) if x]
    if len(decide) + bool(args.doc or args.row or args.all) != 1:
        parser.error("give --doc DOC, --row ROW or --all to check pages, or one of --accept DOC and --look DOC")
    project = open_project(args.project)
    if decide:
        got = recheck.accept(project, args.accept) if args.accept else recheck.look(project, args.look)
        print(json.dumps(got, ensure_ascii=False) if args.json else recheck.say(got))
        return 0
    docs = recheck.targets(project, args.doc, args.row, args.all)
    if not docs:
        raise HandbookError("There are no saved web pages to check there.")
    results, last = [], {}
    have = project.documents()
    for doc in docs:
        # One request a second at most to any one site: a re-check of every page asks a few sites many times.
        host = urllib.parse.urlsplit(str((have.get(doc) or {}).get("url") or "")).hostname or ""
        wait = recheck.POLITE_S - (time.monotonic() - last.get(host, -1e9))
        if wait > 0:
            time.sleep(wait)
        got = recheck.recheck_document(project, doc)
        last[host] = time.monotonic()
        results.append(got)
        if not args.json:
            print(recheck.say(got))
    counts = {k: sum(1 for r in results if r["result"] == k) for k in ("same", "changed", "unreachable", "skipped")}
    if args.json:
        print(json.dumps({"results": results, "counts": counts}, ensure_ascii=False))
    else:
        print(f"Checked {len(results)} page{'s' if len(results) != 1 else ''}. {counts['changed']} changed, "
              f"{counts['same']} unchanged, {counts['unreachable']} could not be reached.")
        if any(r.get("out_of_date") for r in results):
            print("Score the out-of-date answers again to read the new copies.")
    return 0

"""hb found --row ROW --expect WHAT --looked WHERE: note a document the row should have but that was
not found. hb found --row ROW --second-search-done: note that the row's one second search is over.
hb found --row ROW --remove DOC: take a document off the row (dated after the cut-off, the wrong
organisation, a page that gives the benchmark's own result); answers that cite it go out of date.
"""
from __future__ import annotations

from .cli import command_parser, open_project
from .folder import now_stamp
from .fetch import resolve_rows


def main(argv) -> int:
    parser = command_parser("found", "Record what was not found for a row, or that its second "
                                     "search is done.")
    parser.add_argument("--row", required=True, help="the row")
    parser.add_argument("--expect", default=None, metavar="WHAT",
                        help="a document the row should have but that was not found")
    parser.add_argument("--looked", default=None, metavar="WHERE", help="where it was looked for")
    parser.add_argument("--second-search-done", action="store_true",
                        help="the row's one second search has finished")
    parser.add_argument("--remove", default=None, metavar="DOC",
                        help="take this document off the row; answers citing it are marked out of date")
    args = parser.parse_args(argv)
    if args.remove:
        project = open_project(args.project)
        [row] = resolve_rows(project, [args.row])
        return _remove(project, row, args.remove.strip())
    if args.second_search_done:
        if args.expect is not None or args.looked is not None:
            parser.error("--second-search-done goes on its own, without --expect or --looked")
    elif not (args.expect and args.looked):
        parser.error("give --expect WHAT with --looked WHERE, or --second-search-done")
    project = open_project(args.project)
    [row] = resolve_rows(project, [args.row])

    found = project.found(row)
    found["row"] = row
    if args.second_search_done:
        found["second_search"] = now_stamp()
        project.write_found(row, found)
        print(f"Marked the second search for {row} as done.")
        return 0
    item = {"what": args.expect.strip(), "where_looked": args.looked.strip()}
    notes = found.setdefault("expected_not_found", [])
    if item in notes:
        print(f"Already noted for {row}: {item['what']} (looked: {item['where_looked']}).")
        return 0
    notes.append(item)
    project.write_found(row, found)
    print(f"Noted for {row}: {item['what']} not found (looked: {item['where_looked']}). "
          f"{len(notes)} expected but not found for this row.")
    return 0


def _remove(project, row: str, doc: str) -> int:
    """Take `doc` off `row` (found/<row>.json); answers of the row that cite it are marked out of date.
    The document stays on file for any other row. ACES, the fish scorecard and the Animal Protection
    Index (14 Oct 2026) each had to edit found/<row>.json by hand to do this."""
    found = project.found(row)
    docs = found.get("documents") or []
    if doc not in docs:
        print(f"{doc} is not filed for {row}; nothing to do.")
        return 1
    found["row"] = row
    found["documents"] = [d for d in docs if d != doc]
    found["removed"] = list(dict.fromkeys((found.get("removed") or []) + [doc]))   # so a re-fetch does not file it again
    project.write_found(row, found)
    answers = project.answers(row)
    stale = [qid for qid, c in (answers.get("cells") or {}).items()
             if isinstance(c, dict) and any(isinstance(e, dict) and e.get("doc") == doc for e in c.get("evidence") or [])]
    for qid in stale:
        answers["cells"][qid]["out_of_date"] = True
    if stale:
        project.write_answers(row, answers)
    print(f"Took {doc} off {row}." + (f" {len(stale)} answer(s) cited it and are now out of date: "
                                       f"{', '.join(stale)}. Score them again." if stale else ""))
    return 0

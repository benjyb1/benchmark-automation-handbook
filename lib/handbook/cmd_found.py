"""hb found --row ROW --expect WHAT --looked WHERE: note a document the row should have but that was
not found. hb found --row ROW --second-search-done: note that the row's one second search is over.
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
    args = parser.parse_args(argv)
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

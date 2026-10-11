"""hb second-reads [--json]: the cells that need a second read, grouped by question and group.
hb second-reads --record --row ROW --run RUN --file PATH: store a second reader's answers."""
from __future__ import annotations
import json
import sys
from pathlib import Path

from .checks import record_second_read, second_reads, unchecked_rows
from .cli import command_parser, open_project
from .folder import HandbookError, read_json


def main(argv) -> int:
    parser = command_parser("second-reads", "List the cells that need a second read, or record one.")
    parser.add_argument("--json", action="store_true", help="print the list (or the recorded cells) as JSON")
    parser.add_argument("--record", action="store_true", help="store a second reader's answers file")
    parser.add_argument("--row", help="with --record: the row the file is for")
    parser.add_argument("--run", help="with --record: the run that did the second read")
    parser.add_argument("--file", help="with --record: the second reader's answers file")
    args = parser.parse_args(argv)
    if args.record and not (args.row and args.run and args.file):
        parser.error("--record needs --row, --run and --file")
    if not args.record and (args.row or args.run or args.file):
        parser.error("--row, --run and --file only go with --record")
    project = open_project(args.project)

    if args.record:
        path = Path(args.file).expanduser()
        if not path.is_file():
            raise HandbookError(f"There is no second-read file at {path}.")
        results = record_second_read(project, args.row, args.run, read_json(path))
        if args.json:
            print(json.dumps({"row": args.row, "run": args.run, "recorded": results}, ensure_ascii=False))
            return 0
        agree = sum(1 for r in results if r["agrees"])
        print(f"Recorded {len(results)} second read{'s' if len(results) != 1 else ''} for {args.row}: "
              f"{agree} agree, {len(results) - agree} disagree.")
        for r in results:
            if not r["agrees"]:
                print(f"  {r['question']}: the second read says {r['value']!r}, flagged reads_disagree")
        return 0

    missing = unchecked_rows(project)
    if missing:
        print(f"{len(missing)} scored row{'s have' if len(missing) != 1 else ' has'} no checks yet "
              f"({', '.join(missing)}). Run hb check first.", file=sys.stderr)
    listing = second_reads(project)
    if args.json:
        print(json.dumps(listing, ensure_ascii=False))
        return 0
    if not listing:
        print("No cells need a second read.")
        return 0
    cells = sum(len(g["rows"]) for g in listing)
    print(f"{cells} cell{'s' if cells != 1 else ''} in {len(listing)} group"
          f"{'s' if len(listing) != 1 else ''} need a second read (read each group's rows together):")
    for g in listing:
        print(f"  Question {g['question']}, {g['group'] or 'no group'}: {', '.join(g['rows'])}")
    return 0

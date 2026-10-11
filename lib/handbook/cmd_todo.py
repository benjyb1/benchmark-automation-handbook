"""hb todo [--json]: the questions still to score on each row (missing, not run or out of date).
hb todo --find [--json]: the rows whose documents have not been looked for yet.
Both list only the rows and questions in the run control's scope (`hb control`)."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .todo import to_find, todo


def _find(project, as_json: bool) -> int:
    result = to_find(project)
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif not result["rows"]:
        print("Every row in scope has had its documents looked for.")
    else:
        n = result["count"]
        print(", ".join(result["rows"]))
        print(f"{n} row{'s' if n != 1 else ''} still to look for documents for.")
    return 0


def main(argv) -> int:
    parser = command_parser("todo", "List the questions still to score on each row.")
    parser.add_argument("--json", action="store_true", help='print {"rows": [{"row", "questions"}], "cells": n}')
    parser.add_argument("--find", action="store_true",
                        help='list the rows whose documents have not been looked for yet ({"rows": [ids], "count": n})')
    args = parser.parse_args(argv)
    if args.find:
        return _find(open_project(args.project), args.json)
    result = todo(open_project(args.project))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if not result["rows"]:
        print("Nothing left to score.")
        return 0
    for item in result["rows"]:
        print(f"{item['row']}: {', '.join(item['questions'])}")
    n, rows = result["cells"], len(result["rows"])
    print(f"{n} cell{'s' if n != 1 else ''} still to score in {rows} row{'s' if rows != 1 else ''}.")
    return 0

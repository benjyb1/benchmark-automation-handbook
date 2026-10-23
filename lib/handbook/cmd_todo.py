"""hb todo [--json]: the questions still to score on each row (missing, not run or out of date).
hb todo --find [--questions Q,...] [--json]: the rows whose documents have not been looked for yet (for those
questions), and rows whose search named other questions only.
Both list only the rows and questions in the run control's scope (`hb control`)."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .todo import to_find, todo


def _find(project, as_json: bool, questions=None) -> int:
    result = to_find(project, questions)
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif not result["rows"]:
        print("Every row in scope has had its documents looked for.")
    else:
        n = result["count"]
        print(", ".join(result["rows"]))
        print(f"{n} row{'s' if n != 1 else ''} still to look for documents for.")
    return 0


TODO_NAMES = 5          # rows named on a line before "and N more rows"


def main(argv) -> int:
    parser = command_parser("todo", "List the questions still to score on each row.")
    parser.add_argument("--json", action="store_true", help='print {"rows": [{"row", "questions"}], "cells": n}')
    parser.add_argument("--find", action="store_true",
                        help='list the rows whose documents have not been looked for yet ({"rows": [ids], "count": n})')
    parser.add_argument("--questions", default=None, metavar="Q,...",
                        help="with --find: only the documents these questions need")
    args = parser.parse_args(argv)
    if args.find:
        return _find(open_project(args.project), args.json,
                     [q.strip() for q in (args.questions or "").split(",") if q.strip()] or None)
    result = todo(open_project(args.project))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    held = result.get("waiting") or {}
    waits = sum(held.values())
    wait_line = (f"{waits} answer{' waits' if waits == 1 else 's wait'} for data the owner supplies "
                 f"({', '.join(held)}).") if held else None
    if not result["rows"]:
        print("Nothing left to score." + (" " + wait_line if wait_line else ""))
        return 0
    # Rows with the same questions left are one line (TI Defence, sweep round 3: 134 lines naming all 18
    # questions each, about 2,500 tokens, for "every row, every question").
    groups: dict[tuple, list[str]] = {}
    for item in result["rows"]:
        groups.setdefault(tuple(item["questions"]), []).append(item["row"])
    for qs, rows_ in groups.items():
        who = ", ".join(rows_) if len(rows_) <= TODO_NAMES else f"{', '.join(rows_[:TODO_NAMES])} and {len(rows_) - TODO_NAMES} more rows"
        print(f"{who}: {', '.join(qs)}")
    n, rows = result["cells"], len(result["rows"])
    print(f"{n} cell{'s' if n != 1 else ''} still to score in {rows} row{'s' if rows != 1 else ''}.")
    if wait_line:
        print(wait_line)
    return 0

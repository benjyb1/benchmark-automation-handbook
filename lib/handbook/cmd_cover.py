"""hb cover --question QID [--size N] [--json]: pick the rows to try a question on first.

Every label value (each country, each brand, ...) among the rows the question applies to is
covered at least once, even when that takes more rows than `--size`. Nothing is written.
"""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .cover import candidate_rows, cover
from .folder import HandbookError


def main(argv) -> int:
    parser = command_parser("cover", "Pick the rows to run a question on first, covering every "
                                     "country, brand and other label value.")
    parser.add_argument("--question", required=True, help="the question id, for example 1.1")
    parser.add_argument("--size", type=int, default=10,
                        help="how many rows to pick (default 10); more are picked when covering "
                             "every label value needs them")
    parser.add_argument("--json", action="store_true",
                        help='print {"question": ..., "rows": [...]} and nothing else')
    args = parser.parse_args(argv)
    project = open_project(args.project)

    rows = cover(project, args.question, args.size)
    if not rows:
        raise HandbookError(f"Question {args.question} applies to no rows, so there is nothing to pick.")
    if args.json:
        print(json.dumps({"question": args.question, "rows": rows}, ensure_ascii=False))
        return 0

    units = {u["id"]: u for u in candidate_rows(project, args.question)}
    n = len(rows)
    print(f"Trial rows for question {args.question}: {n} row{'s' if n != 1 else ''} "
          f"of the {len(units)} it applies to.")
    for rid in rows:
        unit = units[rid]
        labels = ", ".join(str(v) for v in (unit.get("labels") or {}).values() if v not in (None, ""))
        print(f"  {rid}  {unit.get('name', '')}" + (f" ({labels})" if labels else ""))
    if n > args.size:
        print(f"Covering every label value takes {n} rows, more than the {args.size} asked for.")
    elif n < args.size:
        print(f"Only {n} row{'s' if n != 1 else ''} take{'' if n != 1 else 's'} this question, "
              "so that is all of them.")
    return 0

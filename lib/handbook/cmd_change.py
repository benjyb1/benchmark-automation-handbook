"""hb change --op JSON [--apply] [--json]: change the set-up at any stage (changes.py). Without
--apply it only says what the change would do to the answers already there; with --apply it is
written, and kept only if the project still checks. The ops: hb change --list."""
from __future__ import annotations
import json

from .changes import OPS, change
from .cli import command_parser, open_project
from .folder import HandbookError


def main(argv) -> int:
    parser = command_parser("change", "Change rows, questions, rules or a set-up part, at any stage.")
    parser.add_argument("--op", help='the change as JSON, e.g. {"op": "remove_rows", "ids": ["norway"]}')
    parser.add_argument("--apply", action="store_true", help="write it (otherwise only say what it would do)")
    parser.add_argument("--list", action="store_true", help="list the changes and their fields")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        for name, fields in OPS.items():
            print(f"{name}: {fields}")
        return 0
    if not args.op:
        parser.error("--op is needed (or --list)")
    try:
        op = json.loads(args.op)
    except json.JSONDecodeError as e:
        raise HandbookError(f"--op is not valid JSON: {e}") from None
    got = change(open_project(args.project), op, dry=not args.apply)
    if args.json:
        print(json.dumps(got, ensure_ascii=False))
    else:
        imp = got.get("impact") or {}
        print(got.get("said") or "")
        if got["problems"]:
            print("Not changed:\n" + "\n".join("  - " + p for p in got["problems"]))
        elif imp:
            print(f"{len(imp.get('removed') or [])} answers removed, {len(imp.get('out_of_date') or [])} out of date, "
                  f"{len(imp.get('unscored') or [])} new to score.")
    return 0 if got["ok"] else 1

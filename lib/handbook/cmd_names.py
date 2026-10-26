"""hb names [--json]: the plain faults in the names an owner reads (naming.py): question short names,
section names, labels, row names. Set-up reads this before it is called done; it never blocks a save."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .naming import review

SHOW = 30


def main(argv) -> int:
    parser = command_parser("names", "List the names an owner reads that look wrong (codes, file names, "
                                     "repeats, mixed capitals, too long).")
    parser.add_argument("--json", action="store_true", help='print {"names": [{"where", "text", "problem"}]}')
    args = parser.parse_args(argv)
    found = review(open_project(args.project))
    if args.json:
        print(json.dumps({"names": found}, ensure_ascii=False))
        return 0
    if not found:
        print("Every name looks fine.")
        return 0
    for f in found[:SHOW]:
        print(f"- {f['problem']}" + (f" ({f['text']})" if f["text"] and f["text"] not in f["problem"] else ""))
    if len(found) > SHOW:
        print(f"... and {len(found) - SHOW} more (--json lists them all).")
    print(f"{len(found)} name{'s' if len(found) != 1 else ''} to look at.")
    return 0

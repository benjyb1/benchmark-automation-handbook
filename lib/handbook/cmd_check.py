"""hb check [--json]: compute the flags for every scored row and write `checks/<row>.json`."""
from __future__ import annotations
import json

from .checks import run_checks
from .cli import command_parser, open_project

SHOW = 20          # flagged cells listed in the plain output; the count is always the full one


def main(argv) -> int:
    parser = command_parser("check", "Compute the flags for every scored row.")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    result = run_checks(project)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    word = project.read("project").get("unit_word_plural") or "rows"
    if not result["rows"]:
        print("No scored rows yet, so there is nothing to check.")
        return 0
    print(f"Checked {result['rows']} of {result['rows_expected']} {word} ({result['cells']} cells).")
    print("Flags: " + ", ".join(f"{flag} {n}" for flag, n in result["flags"].items()) + ".")
    flagged = result["flagged"]
    if flagged:
        print(f"Showing {min(SHOW, len(flagged))} of {len(flagged)} flagged cells:")
        for f in flagged[:SHOW]:
            print(f"  {f['row']} {f['question']}: {', '.join(f['flags'])}")
    waiting = result["second_reads_waiting"]
    print(f"{waiting} cell{'s' if waiting != 1 else ''} waiting for a second read." if waiting
          else "No cells need a second read.")
    return 0

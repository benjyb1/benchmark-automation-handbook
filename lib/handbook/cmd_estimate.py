"""hb estimate [--during] [--json]: what a full run is likely to cost, from the latest finished
trial's token usage, written to `estimate.json`. With --during, compares a full run in progress
with that estimate and exits 3 when it is more than 25% over (after at least 5 rows)."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .estimate import estimate, pace, show_tokens


def main(argv) -> int:
    parser = command_parser("estimate", "Estimate the cost of a full run from the trial's usage, "
                                        "or check a full run's pace against it.")
    parser.add_argument("--during", action="store_true",
                        help="compare the full run in progress with the estimate (exit 3 when "
                             "more than 25%% over after 5 rows)")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    project = open_project(args.project)

    if args.during:
        result = pace(project)
        print(json.dumps(result, ensure_ascii=False) if args.json else result["message"])
        return 3 if result["state"] == "stop" else 0

    result = estimate(project)
    if result["point"] is not None:
        project.write("estimate", result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if result["point"] is None:
        print(result["note"])
        return 0
    rows, cells = result["rows"], result["cells"]
    if not rows:
        print("Every row already has a complete answers file, so a full run has nothing left to do.")
    else:
        print(f"A full run of the {rows} remaining {'row' if rows == 1 else 'rows'} "
              f"({cells} {'cell' if cells == 1 else 'cells'}) is likely to use about "
              f"{show_tokens(result['point'])} tokens, most likely between {show_tokens(result['low'])} "
              f"and {show_tokens(result['high'])}.")
    print("Assumptions:")
    for line in result["assumptions"]:
        print(f"- {line}")
    print("Saved to estimate.json.")
    return 0

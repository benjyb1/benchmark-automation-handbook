"""hb rebuild-check [--json]: score last edition's held-back results with the score sheet, write
`<slug>.held-back/rebuild.json` and list every mismatch. Exits 1 when anything differs."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .folder import write_json
from .scoresheet import rebuild_check


def main(argv) -> int:
    parser = command_parser("rebuild-check", "Check that the score sheet rebuilds last edition's "
                                             "published results.")
    parser.add_argument("--json", action="store_true", help="print the rebuild.json object")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    result = rebuild_check(project)
    write_json(project.held_back / "rebuild.json", result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["mismatches"] else 0
    checked, matched, mismatches = result["checked"], result["matched"], result["mismatches"]
    print(f"Rebuilt last edition's results: {matched} of {checked} rows match.")
    if mismatches:
        print(f"Mismatches ({len(mismatches)}):")
        for m in mismatches:
            print(f"  {m['row']}, {m['key']}: ours {m['ours']}, published {m['theirs']}")
    elif not checked:
        print("No row had anything the score sheet could rebuild.")
    return 1 if mismatches else 0

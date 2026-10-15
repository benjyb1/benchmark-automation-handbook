"""hb second-finds [--json]: the rows that still need their one second search."""
from __future__ import annotations
import json

from .checks import second_finds
from .cli import command_parser, open_project


def _reason(entry: dict) -> str:
    parts = []
    if "expected_not_found" in entry["why"]:
        parts.append("a document was expected but not found")
    if "unsure_document" in entry["why"]:
        parts.append("unsure about a document on question " + ", ".join(entry["questions"]))
    if "after_cutoff" in entry["why"]:
        parts.append("an answer cites a document dated after the cut-off: look for the version current then")
    return "; ".join(parts)


def main(argv) -> int:
    parser = command_parser("second-finds", "List the rows that need their one second search.")
    parser.add_argument("--json", action="store_true", help="print the list as JSON")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    listing = second_finds(project)
    if args.json:
        print(json.dumps(listing, ensure_ascii=False))
        return 0
    if not listing:
        print("No row needs a second search.")
        return 0
    print(f"{len(listing)} row{'s need' if len(listing) != 1 else ' needs'} a second search:")
    for entry in listing:
        print(f"  {entry['row']}: {_reason(entry)}")
    return 0

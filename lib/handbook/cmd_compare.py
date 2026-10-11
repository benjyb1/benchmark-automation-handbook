"""hb compare [--json]: our results beside last edition's, with the reasons the files can prove.
Writes `compare/compare.json`."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .compare import compare
from .folder import write_json

REASON_WORDS = {"missing_document": "missing document", "different_document": "different document",
                "document_date": "document dated after the cut-off", "rule_reading": "rule reading",
                "open_rule": "undecided rule", "unexplained": "not yet explained"}
SHOW = 10          # keys and rows listed in the plain output; the counts are always the full ones


def main(argv) -> int:
    parser = command_parser("compare", "Compare our results with last edition's held-back results "
                                       "and work out the reasons the files can prove.")
    parser.add_argument("--json", action="store_true", help="print the compare.json object")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    data = compare(project)
    write_json(project.file("compare/compare"), data)
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
        return 0
    word = project.read("project").get("unit_word_plural") or "rows"
    expected = len(project.read("units").get("units", []))
    summary = data["summary"]
    print(f"Compared {summary['rows']} of {expected} {word} with last edition's results, at "
          f"{data['level']} level. Written to compare/compare.json.")
    for key, n in list(summary["agree"].items())[:SHOW]:
        print(f"  {key}: {n} of {summary['of'][key]} agree")
    if len(summary["agree"]) > SHOW:
        print(f"  Showing {SHOW} of {len(summary['agree'])} keys.")
    found = data["disagreements"]
    by_reason: dict[str, int] = {}
    for d in found:
        by_reason[d["reason"]] = by_reason.get(d["reason"], 0) + 1
    detail = ": " + ", ".join(f"{n} {REASON_WORDS[r]}" for r, n in by_reason.items()) if found else ""
    print(f"{len(found)} disagreement{'s' if len(found) != 1 else ''}{detail}.")
    unexplained = by_reason.get("unexplained", 0)
    if unexplained:
        print(f"{unexplained} not yet explained: the results step explains those from the evidence.")
    editions = data["history"]["editions"]
    if len(editions) > 1:
        print(f"Past editions for the trends: {', '.join(editions)}.")
    big = data["big_moves"]
    if big:
        print(f"{len(big)} big move{'s' if len(big) != 1 else ''} to check first:")
        for m in big[:SHOW]:
            print(f"  {m['row']}: {m['text']}")
        if len(big) > SHOW:
            print(f"  Showing {SHOW} of {len(big)}.")
    skipped = summary["not_compared"]
    if skipped:
        print(f"Not compared ({len(skipped)}):")
        for n in skipped[:SHOW]:
            print(f"  {n['row']}: {n['text']}")
        if len(skipped) > SHOW:
            print(f"  Showing {SHOW} of {len(skipped)}.")
    return 0

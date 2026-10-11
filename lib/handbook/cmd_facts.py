"""hb facts: write `report/facts.json`, every figure the report may state."""
from __future__ import annotations

from .cli import command_parser, open_project
from .facts import compute
from .folder import write_json


def main(argv) -> int:
    parser = command_parser("facts", "Write report/facts.json, the figures a report may state.")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    facts = compute(project)
    write_json(project.file("report/facts"), facts)
    word = project.read("project").get("unit_word_plural") or "rows"
    print("Wrote report/facts.json.")
    print(f"{facts['rows_scored']} of {facts['rows']} {word} scored, {facts['rows_complete']} with "
          "every question scored (the bands and means use those).")
    if facts["rows_partial"]:
        print(f"{facts['rows_partial']} scored {'row has' if facts['rows_partial'] == 1 else 'rows have'} "
              "questions still to score and are left out of the bands and means.")
    if "comparison" in facts:
        print(f"Comparison with last edition included: {facts['comparison']['rows']} {word} compared.")
    else:
        print("No comparison yet, so there are no agreement figures.")
    return 0

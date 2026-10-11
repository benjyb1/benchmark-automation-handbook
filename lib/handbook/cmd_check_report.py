"""hb check-report: fail when report/report.md states a number that is not in report/facts.json."""
from __future__ import annotations

from .cli import command_parser, open_project
from .facts import check_report

SHOW = 40           # numbers listed; the count is always the full one
CONTEXT = 100       # characters of the line shown beside each number


def main(argv) -> int:
    parser = command_parser("check-report", "Check that report/report.md states only numbers that "
                                            "are in report/facts.json.")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    result = check_report(project)
    bad = result["unbacked"]
    if not bad:
        if result["numbers"]:
            print(f"Checked {result['numbers']} numbers in report/report.md against "
                  "report/facts.json: every one is accounted for.")
        else:
            print("report/report.md has no numbers to check.")
        return 0
    print(f"report/report.md states {len(bad)} number{'s' if len(bad) != 1 else ''} that "
          f"{'is' if len(bad) == 1 else 'are'} not in report/facts.json:")
    for b in bad[:SHOW]:
        text = b["text"] if len(b["text"]) <= CONTEXT else b["text"][:CONTEXT - 3] + "..."
        print(f"  line {b['line']}: {b['number']} (in: {text})")
    if len(bad) > SHOW:
        print(f"  Showing {SHOW} of {len(bad)}.")
    print("Use the figures in report/facts.json, or run hb facts again if the figures have changed. "
          "Numbers from 0 to 10, years, question ids and link addresses need no fact.")
    return 1

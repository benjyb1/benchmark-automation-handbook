"""hb stakes: fill `stakes` ({"cells", "points"}) on every rule in rules.json.

Cells are the (row, question) pairs a rule touches; points are how far the score could move,
the top points of each such question minus its lowest. If any rule names a question or row that
does not exist, nothing is written and every such rule is listed.
"""
from __future__ import annotations

from .cli import command_parser, open_project
from .folder import HandbookError
from .stakes import rule_stakes


def main(argv) -> int:
    parser = command_parser("stakes", "Work out how many cells and points each rule puts at stake "
                                      "and save them in rules.json.")
    args = parser.parse_args(argv)
    project = open_project(args.project)

    data = project.read("rules")
    rules = data.get("rules", [])
    if not rules:
        print("There are no rules yet, so there are no stakes to fill.")
        return 0
    questions = project.read("questions").get("questions", [])
    units = project.read("units").get("units", [])

    results, problems = [], []
    for rule in rules:
        try:
            results.append(rule_stakes(project, rule, questions, units))
        except HandbookError as e:
            problems.append(str(e))
    if problems:
        raise HandbookError(f"Stakes not filled, because {len(problems)} of {len(rules)} rules "
                            "could not be worked out. Fix rules.json first:\n- " + "\n- ".join(problems))

    for rule, stakes in zip(rules, results):
        rule["stakes"] = stakes
    project.write("rules", data)
    print(f"Filled stakes on {len(rules)} rule{'s' if len(rules) != 1 else ''}:")
    for rule in rules:
        s = rule["stakes"]
        print(f"  {rule.get('id', '?')}: {s['cells']} cell{'s' if s['cells'] != 1 else ''}, "
              f"{s['points']} point{'s' if s['points'] != 1 else ''}")
    return 0

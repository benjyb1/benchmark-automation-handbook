"""hb link-rule --rule R --rows R1,R2 --questions Q,...: link an existing rule to the scored cells
that carry a doubt it already covers."""
from __future__ import annotations

from .cli import command_parser, open_project
from .cmd_kit import id_list
from .links import link_rule


def main(argv) -> int:
    parser = command_parser("link-rule", "Link an existing rule to the scored cells it already covers.")
    parser.add_argument("--rule", required=True, help="the rule id, for example R3")
    parser.add_argument("--rows", required=True, help="unit ids, comma separated")
    parser.add_argument("--questions", required=True, help="question ids, comma separated")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    result = link_rule(project, args.rule.strip(), id_list(args.rows) or [], id_list(args.questions) or [])
    linked = result["linked"]
    print(f"Linked {args.rule.strip()} to {len(linked)} cell{'s' if len(linked) != 1 else ''}: "
          + ", ".join(f"{row} {qid}" for row, qid in linked) + ".")
    for _, _, why in result["skipped"]:
        print(f"  Skipped: {why}.")
    return 0

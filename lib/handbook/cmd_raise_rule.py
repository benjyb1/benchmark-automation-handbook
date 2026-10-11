"""hb raise-rule --questions Q,... --rows R,... --ask TEXT --option a=TEXT --option b=TEXT
--recommended a --by trial|run [--why TEXT]: add an open rule question for the owner and link it to
those cells."""
from __future__ import annotations

from .cli import command_parser, open_project
from .cmd_kit import id_list
from .ingest import raise_rule


def main(argv) -> int:
    parser = command_parser("raise-rule", "Add an open rule question and link it to the cells it touches.")
    parser.add_argument("--questions", required=True, help="question ids, comma separated")
    parser.add_argument("--rows", required=True, help="unit ids, comma separated")
    parser.add_argument("--ask", required=True, help="the question for the owner")
    parser.add_argument("--option", action="append", default=[], metavar="ID=TEXT",
                        help="an answer the owner can choose; give at least two")
    parser.add_argument("--recommended", required=True, help="the option id to use until decided")
    parser.add_argument("--by", required=True, choices=("trial", "run"))
    parser.add_argument("--why", default=None, help="one short sentence on the recommendation")
    args = parser.parse_args(argv)
    options = []
    for text in args.option:
        oid, sep, words = text.partition("=")
        if not sep or not oid.strip():
            parser.error(f"--option {text!r} must look like a=The option in words")
        options.append((oid.strip(), words))
    project = open_project(args.project)
    rule = raise_rule(project, id_list(args.questions) or [], id_list(args.rows) or [], args.ask,
                      options, args.recommended.strip(), args.by, args.why)
    stakes = rule["stakes"]
    print(f"Raised rule {rule['id']} (open) on {', '.join(rule['question_ids'])} for "
          f"{len(rule['rows'])} row(s): {stakes['cells']} cell(s), {stakes['points']} point(s) at stake.")
    return 0

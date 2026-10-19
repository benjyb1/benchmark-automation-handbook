"""hb add-instruction --text TEXT [--questions Q,...] [--rows R,...] [--via chat|viewer]: add the
owner's standing instruction for the scorers (a decided rule of kind "instruction"). Without
--questions it covers every question; without --rows every row, now and later. It reaches every
scorer kit built from now on; answers already scored change only when they are scored again."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .cmd_kit import id_list
from .ingest import add_instruction


def main(argv) -> int:
    parser = command_parser("add-instruction", "Add a standing instruction that every scorer follows.")
    parser.add_argument("--text", required=True, help="the instruction, in the owner's words")
    parser.add_argument("--questions", help="question ids, comma separated (default: all)")
    parser.add_argument("--rows", help="row ids, comma separated (default: all)")
    parser.add_argument("--via", default="chat", choices=("chat", "viewer"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    rule = add_instruction(project, args.text, id_list(args.questions), id_list(args.rows), args.via)
    if args.json:
        print(json.dumps({"id": rule["id"], "questions": len(rule["question_ids"]), "rows": rule["rows"]}))
    else:
        rows = "every row" if rule["rows"] is None else f"{len(rule['rows'])} row(s)"
        print(f"Added instruction {rule['id']} for {len(rule['question_ids'])} question(s) and {rows}.")
    return 0

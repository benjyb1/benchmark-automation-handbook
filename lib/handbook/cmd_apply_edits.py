"""hb apply-edits --file EDITS.json [--via viewer|chat]: apply the owner's edits and rule decisions.

The file is the viewer's export, or, for a change the owner made in chat, the same shape written by
the skill (`{"edits": [...], "decisions": [...]}`; chat ids start with `c-`). Ids already applied are
skipped, so running it twice is safe.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

from .cli import command_parser, open_project
from .edits import VIA, apply
from .folder import HandbookError, read_json


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def main(argv) -> int:
    parser = command_parser("apply-edits", "Apply the owner's edits and rule decisions to the project.")
    parser.add_argument("--file", required=True, help="the edits (JSON): {edits: [...], decisions: [...]}")
    parser.add_argument("--via", choices=VIA, default="viewer",
                        help="where the changes were made: viewer (default) or chat")
    args = parser.parse_args(argv)
    project = open_project(args.project)

    path = Path(args.file).expanduser()
    if not path.is_file():
        raise HandbookError(f"There is no edits file at {path}.")
    payload = read_json(path)
    result = apply(project, payload, datetime.now(timezone.utc), args.via)

    applied, skipped = result["applied"], result["skipped"]
    total = len(applied) + len(skipped)
    if not total:
        print("Nothing to apply: the file holds no edits or decisions.")
        return 0
    source = "the viewer" if args.via == "viewer" else "chat"
    print(f"Applied {len(applied)} of {_count(total, 'change', 'changes')} from {source}.")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for s in skipped:
            print(f"  {s['id']}: {s['why']}")
    if result["out_of_date"]:
        print(f"{_count(result['out_of_date'], 'answer is', 'answers are')} now out of date: scored "
              "under the earlier wording, rule or decision.")
    if result["recomputed"]:
        print(f"{_count(result['recomputed'], 'answer', 'answers')} got new points.")
    if result["now_not_applicable"]:
        print(f"{_count(result['now_not_applicable'], 'answer was', 'answers were')} set aside because "
              "the question no longer applies to the row. The earlier answer is kept in the file.")
    if result["now_not_run"]:
        print(f"{_count(result['now_not_run'], 'question now applies', 'questions now apply')} "
              "to a row with no answer yet.")
    return 0

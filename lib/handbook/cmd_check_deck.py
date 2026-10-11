"""hb check-deck --step STEP: fail when a sentence in the step's brief is missing, or states a number
or names a row the files do not back; when it passes, mark the brief checked so `build` includes it."""
from __future__ import annotations

from .cli import command_parser, open_project
from .deck import CONTRACT, STEPS, check, mark_checked, read
from .folder import HandbookError

CONTEXT = 100


def _short(text: str) -> str:
    return text if len(text) <= CONTEXT else text[:CONTEXT - 3] + "..."


def main(argv) -> int:
    parser = command_parser("check-deck", "Check the sentences in deck/<step>.json against the files.")
    parser.add_argument("--step", required=True, choices=STEPS)
    args = parser.parse_args(argv)
    project = open_project(args.project)
    deck = read(project, args.step)
    if not deck:
        raise HandbookError(f"There is no deck/{args.step}.json. Run hb deck --step {args.step} first.")
    if deck.get("contract") != CONTRACT:
        raise HandbookError(f"deck/{args.step}.json is in an older shape. Run hb deck --step "
                            f"{args.step} --again and write its sentences.")
    found = check(project, deck)
    if not any(found.values()):
        mark_checked(project, args.step, deck)
        print(f"deck/{args.step}.json: every sentence is written and every name and number is "
              "backed by the files. Marked checked; build and publish to show it at the top of the step.")
        return 0
    if found["empty"]:
        print(f"Still to write ({len(found['empty'])}): {', '.join(found['empty'])}.")
    for n in found["numbers"]:
        print(f"  {n['where']}: {n['number']} is not in the brief or the facts (in: {_short(n['text'])})")
    for n in found["names"]:
        print(f"  {n['where']}: names {n['name']}, which this item is not about (in: {_short(n['text'])})")
    print("Use the figures and cases on each item (hb deck lists them). Numbers from 0 to 10, years "
          "and question ids need nothing behind them.")
    return 1

"""hb deck --step STEP [--again] [--fill FILE]: write deck/<step>.json, the brief at the top of
that step in the viewer, with every item and figure chosen from the files and the sentences left
for Claude to write. `--fill FILE` writes sentences from a JSON object {where: text} into the
slots (the deck as it stands, or a fresh one with --again). Then `hb check-deck --step STEP`."""
from __future__ import annotations
import json
from pathlib import Path

from .cli import command_parser, open_project
from .deck import CONTRACT, STEPS, fill, make, path, read, slots, writer_brief
from .folder import HandbookError, write_json


def main(argv) -> int:
    parser = command_parser("deck", "Choose what goes in the brief at the top of a step and write "
                                    "deck/<step>.json with the sentences still to write.")
    parser.add_argument("--step", required=True, choices=STEPS)
    parser.add_argument("--again", action="store_true",
                        help="choose the items afresh, replacing a brief whose sentences are written")
    parser.add_argument("--fill", default=None, metavar="FILE",
                        help="a JSON object {where: sentence} to write into the slots, for example "
                             "{\"how.lines[0]\": \"...\", \"decided.R1.line\": \"...\"}")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    old = read(project, args.step)
    old = old if old and old.get("contract") == CONTRACT else None   # an older shape starts afresh
    written = old and any(isinstance(t, str) and t.strip() for _, t in slots(old))
    if old and not args.again and (written or args.fill):
        deck = old
    else:
        deck = make(project, args.step)
    if written and deck is old and not args.fill:
        raise HandbookError(f"deck/{args.step}.json already has sentences written. Pass --again "
                            "to choose the items afresh and start the sentences again, or --fill "
                            "to write more sentences into it.")
    if args.fill:
        try:
            sentences = json.loads(Path(args.fill).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise HandbookError(f"Could not read {args.fill} as JSON: {e}")
        if not isinstance(sentences, dict):
            raise HandbookError(f"{args.fill} must hold one JSON object, {{where: sentence}}.")
        n, unknown = fill(deck, sentences)
        deck["checked"] = None
        print(f"Wrote {n} sentence{'s' if n != 1 else ''} from {args.fill}."
              + (f" No slot for: {', '.join(unknown)}." if unknown else ""))
    write_json(path(project, args.step), deck)
    by_kind = {s["id"]: s for s in deck["slides"]}
    print(f"deck/{args.step}.json: {len(by_kind['decided']['items'])} things I decided, "
          f"{len(by_kind['eye']['items'])} that need the owner's eye.")
    todo = writer_brief(deck)
    if todo:
        print("Sentences to write, with what each may use:")
        for line in todo:
            print("  " + line)
        print(f"Write them in deck/{args.step}.json (or a --fill file), then run: "
              f"hb check-deck --step {args.step}")
    else:
        print(f"Every sentence is written. Run: hb check-deck --step {args.step}")
    return 0

"""hb apply-corrections: the owner's corrections ("here is the evidence", "the answer should be X").

    hb apply-corrections --file FILE [--json]
        the viewer's `corrections` documents, as the host exported them ({"corrections": [...]});
        ids already handled are passed over, so the host can send everything each time
    hb apply-corrections --record ID --run RUN --file KIT/answers.json [--why TEXT]
        Claude's verdict on correction ID, from the blind re-read of its row and question

The first form checks each quote word for word against its source (fetching a link, or filing an
upload the host saved, first), checks the source counts, puts the owner's answer in the cell, and
lists the re-reads still to do: for each, `kit --row ROW --run RUN --questions Q --look DOC:PAGE`,
`score --kit KIT`, then the second form. See corrections.py.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from . import corrections
from .cli import command_parser, open_project
from .folder import HandbookError, read_json


def _look(r: dict) -> str:
    rr = r.get("reread") or {}
    return f" --look {rr['doc']}:{rr['page']}" if rr.get("doc") else ""


def _record(args, project) -> int:
    answers = Path(args.file).expanduser()
    if not answers.is_file():
        raise HandbookError(f"There is no answers file at {answers}.")
    captures = read_json(answers.parent / "capture.json", None)
    entry = corrections.record(project, args.record, args.run, read_json(answers),
                               captures, datetime.now(timezone.utc), args.why)
    v = entry["verdict"]
    where = f"{entry['row']} {entry['qid']}"
    if v["agree"] is None:
        print(f"Re-read {where} with the owner's evidence: Claude's answer is {v['value']!r}, now stored.")
    elif v["agree"]:
        print(f"Claude agrees with the owner's answer on {where} ({entry['value']!r}).")
    else:
        print(f"Claude disagrees with the owner's answer on {where}: it read {v['value']!r}. Why: {v['why']}")
        print("The owner's answer stays in the cell. Run check, then build and publish.")
    return 0


def main(argv) -> int:
    parser = command_parser("apply-corrections", "Check the owner's corrections and record Claude's verdicts.")
    parser.add_argument("--file", required=True,
                        help="the corrections (JSON) or, with --record, the re-read's answers.json")
    parser.add_argument("--record", metavar="ID", help="record Claude's verdict on this correction")
    parser.add_argument("--run", help="with --record: the run the re-read was scored in")
    parser.add_argument("--why", help="with --record: Claude's reason, in one sentence, when it disagrees "
                                      "(default: the scorer's note)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    if args.record and not args.run:
        parser.error("--record needs --run, the run the re-read was scored in")
    project = open_project(args.project)
    if args.record:
        return _record(args, project)

    path = Path(args.file).expanduser()
    if not path.is_file():
        raise HandbookError(f"There is no corrections file at {path}.")
    result = corrections.apply(project, read_json(path), datetime.now(timezone.utc))
    waiting = corrections.waiting(project)
    if args.json:
        print(json.dumps({**result, "waiting": waiting}, ensure_ascii=False))
        return 0
    applied = result["applied"]
    if not applied and not result["skipped"]:
        print("Nothing new: every correction was handled before." if result["already"]
              else "Nothing to apply: the file holds no corrections.")
    for r in applied:
        where = f"{r['row']} {r['qid']}"
        answer = f" The owner's answer {r['value']!r} is in the cell." if r.get("value") is not None else ""
        if r["status"] == "kept":
            print(f"{r['id']}: the owner keeps their answer on {where}, though Claude disagreed.")
        elif r["status"] == "checked":
            print(f"{r['id']}: {where} failed the check: {r['verdict']['why']}{answer}")
        else:
            found = r.get("found")
            quote = f" The quote is word for word on {found['doc']} page {found['page']}." if found else ""
            print(f"{r['id']}: {where} passed the check.{quote}{answer}")
        for note in r.get("notes") or []:
            print(f"  {note}")
    for s in result["skipped"]:
        print(f"Skipped {s['id']}: {s['why']}")
    if waiting:
        print(f"{len(waiting)} re-read(s) to do, one question each (the scorer stays blind):")
        for r in waiting:
            print(f"  {r['id']}: kit --row {r['row']} --run RUN --questions {r['qid']}{_look(r)}, "
                  f"score --kit KIT, then apply-corrections --record {r['id']} --run RUN --file KIT/answers.json")
    if applied:
        print("Run check, then build and publish.")
    return 0

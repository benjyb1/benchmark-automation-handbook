"""hb bulk read FILE... | hb bulk apply --plan ID --file CONFIRMED.json: an owner's bulk upload of documents
and extra coding instructions (bulk.py; the local page's "Add documents or instructions" runs both steps).

`read` takes a list of documents (an Excel or CSV naming each document's row, title, file or web address,
date and type), the files themselves or a zip of them, files with no list, and a list of extra coding
instructions by question number; it prints the plan (with --json, the plan itself) and files nothing.
`apply` files what the owner kept from that plan and adds the instructions; run `hb prepare` afterwards.
"""
from __future__ import annotations
import json
from pathlib import Path

from . import bulk
from .cli import command_parser, open_project
from .folder import HandbookError, read_json


def main(argv) -> int:
    parser = command_parser("bulk", "Add documents and extra coding instructions in bulk.")
    parser.add_argument("step", choices=("read", "apply"))
    parser.add_argument("files", nargs="*", help="read: the lists, documents and zips")
    parser.add_argument("--plan", default=None, help="apply: the plan id that read printed")
    parser.add_argument("--file", default=None, help="apply: the owner's confirmed table (JSON); default: as read")
    parser.add_argument("--json", action="store_true", help="print the plan or the result as JSON")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    if args.step == "read":
        if not args.files:
            raise HandbookError("Name the files to read.")
        paths = [Path(f).expanduser() for f in args.files]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise HandbookError("There is no file at " + ", ".join(missing) + ".")
        plan = bulk.read(project, [(p, p.name) for p in paths])
        if args.json:
            print(json.dumps(plan, ensure_ascii=False))
        else:
            print(f"Plan {plan['id']}: {plan['summary']}.")
            for d in plan["documents"]:
                print(f"  {d['n']}. {d['title']} -> {', '.join(d['rows']) or '?'}"
                      f"{' dated ' + d['date'] if d['date'] else ''}; questions {', '.join(d['questions']) or 'none'}"
                      + (f" [{'; '.join(d['problems'])}]" if d["problems"] else ""))
            for e in plan["instructions"]:
                print(f"  Question {e.get('qid') or '?'}: {len(e['lines'])} instruction(s), touches {e.get('touches', 0)} answers")
        return 0
    if not args.plan:
        raise HandbookError("Give the plan id with --plan.")
    plan = bulk.load_plan(project, args.plan)
    confirmed = read_json(Path(args.file), None) if args.file else {
        "documents": plan["documents"], "instructions": plan["instructions"]}
    if not isinstance(confirmed, dict):
        raise HandbookError("The confirmed table must be a JSON object with documents and instructions.")
    result = bulk.apply(project, args.plan, confirmed, say=(lambda s: None) if args.json else print)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Filed {len(result['filed'])} document(s); {len(result['failed'])} could not be filed; "
              f"{sum(1 for i in result['instructions'] if i['added'])} instruction(s) added; "
              f"{sum(len(q) for q in result['out_of_date'].values())} answer(s) now out of date.")
        for f in result["failed"]:
            print(f"  Not filed: {f['title']}: {f['why']}")
    return 0

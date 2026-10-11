"""hb run start|usage|finish: run records in runs/.

    run start --kind trial|full|rerun [--questions Q,...] [--rows R,...] [--json]
    run usage --run RUN (--row ROW | --group LABEL=VALUE) --step find|second_find|score|second
              (--tokens N | --receipt FILE)
    run finish --run RUN

`--receipt FILE` is the JSON a headless scorer prints (`claude -p --output-format json`, saved by
`hb score`): the tokens recorded are everything the API processed, cache reads included, and the
split is kept. `--tokens N` records a plain figure, such as the one a host's Agent tool reports.
"""
from __future__ import annotations
import json
from pathlib import Path

from .cli import command_parser, open_project
from .cmd_kit import id_list
from .folder import HandbookError, read_json
from .runs import finish_run, receipt_from_result, record_usage, start_run
from .shapes import RUN_KINDS, USAGE_STEPS

PROJECT_HELP = "the project folder (default: $HANDBOOK_PROJECT, then the current folder)"


def _count(ids) -> str:
    return str(len(ids)) if ids else "all"


def main(argv) -> int:
    parser = command_parser("run", "Start a run, record its token usage, finish it.")
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("start", help="start a run and print its id")
    start.add_argument("--kind", required=True, choices=RUN_KINDS)
    start.add_argument("--questions", help="question ids, comma separated (default all)")
    start.add_argument("--rows", help="unit ids, comma separated (default all)")
    start.add_argument("--json", action="store_true", help="print the run record")
    usage = sub.add_parser("usage", help="add tokens used by one step")
    usage.add_argument("--run", required=True)
    who = usage.add_mutually_exclusive_group(required=True)
    who.add_argument("--row")
    who.add_argument("--group", metavar="LABEL=VALUE")
    usage.add_argument("--step", required=True, choices=USAGE_STEPS)
    how = usage.add_mutually_exclusive_group(required=True)
    how.add_argument("--tokens", type=int, help="a plain token figure")
    how.add_argument("--receipt", help="the JSON a headless scorer printed (claude -p --output-format json)")
    finish = sub.add_parser("finish", help="mark a run finished")
    finish.add_argument("--run", required=True)
    for p in (start, usage, finish):
        p.add_argument("--project", dest="sub_project", metavar="PROJECT", default=None,
                       help=PROJECT_HELP)
    args = parser.parse_args(argv)
    project = open_project(args.sub_project or args.project)

    if args.action == "start":
        run = start_run(project, args.kind, id_list(args.questions), id_list(args.rows))
        if args.json:
            print(json.dumps(run, ensure_ascii=False))
        else:
            print(f"Started {run['kind']} run {run['id']} ({_count(run['questions'])} question(s), "
                  f"{_count(run['rows'])} row(s)). Stage is now {project.read('project').get('stage')}.")
        return 0
    if args.action == "usage":
        receipt = None
        if args.receipt:
            path = Path(args.receipt).expanduser()
            if not path.is_file():
                raise HandbookError(f"There is no receipt at {path}.")
            receipt = receipt_from_result(read_json(path))
        run = record_usage(project, args.run, args.step, args.tokens, row=args.row, group=args.group,
                           receipt=receipt)
        total = run["usage"][args.row][args.step] if args.row else run["group_usage"][args.group]
        if receipt:
            tokens = receipt["fresh"] + receipt["cache_read"] + receipt["output"]
            print(f"Recorded {tokens:,} tokens for {args.row or args.group} ({args.step}) in run {args.run}: "
                  f"{receipt['cache_read']:,} cache reads, {receipt['fresh']:,} fresh input, "
                  f"{receipt['output']:,} output, over {receipt['turns']} turns; {total:,} so far.")
        else:
            print(f"Recorded {args.tokens} tokens for {args.row or args.group} ({args.step}) in run "
                  f"{args.run}; {total} so far.")
        return 0
    result = finish_run(project, args.run)
    print(f"Finished run {args.run}. Cells answered or with no evidence: {result['cells_done']} of "
          f"{result['cells_expected']}. Stage: {result['stage']}.")
    return 0

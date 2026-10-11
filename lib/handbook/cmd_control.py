"""hb control: pause, stop and scope the run (control.json).

    hb control [--json]                       what the control says (plain sentences, or the file)
    hb control set (--pause | --resume | --stop-now | --stop-after-row ROW | --rows R,... |
                    --all-rows | --questions Q,... | --all-questions | --stop-after find|score|none)
    hb control apply --file F                 the viewer's requests, as the host exported them

`set` takes several options at once and applies them in that order (so `--resume
--stop-after-row R` clears the stops, then sets the new one). An unknown row or question stops it
with nothing changed. `apply` skips unknown rows and questions with a reason, and passes over
requests it has handled before, so the host can send everything each time.
"""
from __future__ import annotations
import json
from pathlib import Path

from . import control
from .cli import command_parser, open_project
from .folder import HandbookError, read_json


def _ids(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _show(argv) -> int:
    parser = command_parser("control", "Show the run control: paused, stops, scope and stop point. "
                                       "Change it with 'hb control set' or 'hb control apply'.")
    parser.add_argument("--json", action="store_true", help="print control.json (with defaults filled in)")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    c = control.read(project)
    if args.json:
        print(json.dumps(c, ensure_ascii=False))
        return 0
    for line in control.describe(project, c):
        print(line)
    return 0


def _set(argv) -> int:
    parser = command_parser("control set", "Change the run control from chat.")
    state = parser.add_mutually_exclusive_group()
    state.add_argument("--pause", action="store_true", help="stop before the next row")
    state.add_argument("--resume", action="store_true", help="clear pause, stop now and stop after row")
    parser.add_argument("--stop-now", action="store_true", help="stop before the next row starts")
    parser.add_argument("--stop-after-row", metavar="ROW", help="stop once this row is finished")
    rows = parser.add_mutually_exclusive_group()
    rows.add_argument("--rows", metavar="R,...", help="run only these rows")
    rows.add_argument("--all-rows", action="store_true", help="run every row")
    questions = parser.add_mutually_exclusive_group()
    questions.add_argument("--questions", metavar="Q,...", help="run only these questions")
    questions.add_argument("--all-questions", action="store_true", help="run every question")
    parser.add_argument("--stop-after", choices=("find", "score", "none"),
                        help="stop after finding documents, after scoring, or carry on to the end (none)")
    parser.add_argument("--rerun-done", metavar="ID", help="a re-run the owner asked for is scored: take it off the list")
    args = parser.parse_args(argv)
    if args.resume and args.stop_now:
        parser.error("--resume clears stop now; give one or the other")

    requests = []
    if args.resume:
        requests.append({"action": "resume"})
    if args.pause:
        requests.append({"action": "pause"})
    if args.stop_now:
        requests.append({"action": "stop_now"})
    if args.stop_after_row is not None:
        requests.append({"action": "stop_after_row", "row": args.stop_after_row})
    scope = {}
    if args.rows is not None or args.all_rows:
        scope["rows"] = None if args.all_rows else _ids(args.rows)
    if args.questions is not None or args.all_questions:
        scope["questions"] = None if args.all_questions else _ids(args.questions)
    if scope:
        requests.append({"action": "scope", **scope})
    if args.stop_after is not None:
        requests.append({"action": "stop_after", "stage": None if args.stop_after == "none" else args.stop_after})
    if args.rerun_done:
        requests.append({"action": "rerun_done", "rerun": args.rerun_done})
    if not requests:
        parser.error("say what to change, for example --pause, --rows R1,R2 or --stop-after find")

    project = open_project(args.project)
    c = control.set_control(project, requests)
    print("Run control updated.")
    for line in control.describe(project, c):
        print(line)
    return 0


def _apply(argv) -> int:
    parser = command_parser("control apply", "Apply the run control requests made in the viewer.")
    parser.add_argument("--file", required=True, help='the requests (JSON): {"requests": [...]}')
    args = parser.parse_args(argv)
    project = open_project(args.project)
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise HandbookError(f"There is no control requests file at {path}.")
    result = control.apply(project, read_json(path))
    applied, skipped, already = result["applied"], result["skipped"], result["already"]
    if not applied and not skipped:
        if already:
            print(f"Nothing new to apply: {already} request{'s were' if already != 1 else ' was'} "
                  "handled before.")
        else:
            print("Nothing to apply: the file holds no requests.")
        return 0
    total = len(applied) + len(skipped)
    print(f"Applied {len(applied)} of {total} new request{'s' if total != 1 else ''} from the viewer.")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for s in skipped:
            print(f"  {s['id']}: {s['why']}")
    for s in result["left_out"]:
        print(f"  {s['id']}: {s['why']}")
    for line in control.describe(project, result["control"]):
        print(line)
    return 0


def main(argv) -> int:
    if argv and argv[0] == "set":
        return _set(argv[1:])
    if argv and argv[0] == "apply":
        return _apply(argv[1:])
    return _show(argv)

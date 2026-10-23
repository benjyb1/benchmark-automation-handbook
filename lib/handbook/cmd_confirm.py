"""hb confirm ITEM[,ITEM...] [--status confirmed|skipped|not_needed] [--need ID=supplied|waived ...]:
record what the owner said about set-up checklist items, and mark data needs supplied or waived.

What the Set-up tab's Confirm buttons and the corner chat's set_setup_status do (setup_parts.set_status),
for a session with no page. Before sweep round 3 (21 Oct 2026) apply-edits confirmed only the questions
and the rows, so five of the eight benchmark owners edited project.json and needs.json by hand to confirm
the score sheet, group labels, cut-off, where to look, the data needs and the rules.
"""
from __future__ import annotations

from .changes import change
from .cli import command_parser, open_project
from .folder import CHECKLIST_KEYS, HandbookError
from .setup_parts import set_status

STATUSES = ("confirmed", "skipped", "not_needed")   # a confirmed item is never set back (setup_parts)
NEED_STATUSES = ("supplied", "waived", "needed")


def set_needs(project, pairs: list[str]) -> list[str]:
    """Each "N1=waived" applied to needs.json (checked as a whole before it is written); what was done."""
    if not project.file("needs").is_file():
        raise HandbookError("There is no needs.json yet, so there is no data need to mark.")
    needs = project.read("needs")
    by_id = {n.get("id"): n for n in needs.get("needs") or [] if isinstance(n, dict)}
    done = []
    for pair in pairs:
        nid, sep, status = pair.partition("=")
        if not sep or status not in NEED_STATUSES:
            raise HandbookError(f"Give a need as ID=supplied, ID=waived or ID=needed, not {pair!r}.")
        if nid not in by_id:
            raise HandbookError(f"There is no data need {nid}. The needs are: {', '.join(by_id) or 'none'}.")
        by_id[nid]["status"] = status
        done.append(f"{nid} {status}")
    got = change(project, {"op": "set_part", "part": "needs", "content": needs}, dry=False)
    if not got.get("ok"):
        raise HandbookError("The data needs were not saved: " + "; ".join(got.get("problems") or ["they did not check"]))
    return done


def main(argv) -> int:
    parser = command_parser("confirm", "Confirm set-up checklist items, and mark data needs supplied or waived.")
    parser.add_argument("items", nargs="?", default="",
                        help="checklist items, comma-separated: " + ", ".join(CHECKLIST_KEYS))
    parser.add_argument("--status", default="confirmed", choices=STATUSES, help="what to record (default confirmed)")
    parser.add_argument("--need", action="append", default=[], metavar="ID=STATUS",
                        help="a data need and its status: supplied, waived or needed (repeat for several)")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    items = [i.strip() for i in args.items.split(",") if i.strip()]
    if not items and not args.need:
        parser.error("name at least one checklist item or --need")
    if args.need:
        print("Data needs: " + ", ".join(set_needs(project, args.need)) + ".")
    failed = 0
    for item in items:
        got = set_status(project, item, args.status)
        if got.get("ok"):
            print(f"{item}: {args.status.replace('_', ' ')}.")
        else:
            failed += 1
            print(f"{item}: not changed. " + "; ".join(got.get("problems") or []))
    return 1 if failed else 0

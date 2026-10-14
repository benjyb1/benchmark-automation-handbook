"""hb packs make|mark-sent|list: the Excel packs businesses (or countries, or schemes) get with their
results, in the feedback round.

    packs make (--units a,b | --ready | --all) [--group-by LABEL|none] [--reply-by DATE] [--sender TEXT]
        write the packs into packs/<today>/ and record "pack made"
    packs mark-sent (--units a,b | --made)
        record that the packs went out
    packs list
        who has a pack, and when it was made and sent
"""
from __future__ import annotations
import json

from . import feedback, packs
from .cli import command_parser, open_project
from .folder import HandbookError

PROJECT_HELP = "the project folder (default: $HANDBOOK_PROJECT, then the current folder)"


def _ids(text):
    return [x.strip() for x in str(text).split(",") if x.strip()]


def main(argv) -> int:
    parser = command_parser("packs", "Make the packs for the feedback round, and mark them as sent.")
    sub = parser.add_subparsers(dest="action", required=True)
    make = sub.add_parser("make", help="write packs")
    pick = make.add_mutually_exclusive_group(required=True)
    pick.add_argument("--units", help="unit ids or names, separated by commas")
    pick.add_argument("--ready", action="store_true", help="every unit that is ready to send")
    pick.add_argument("--all", action="store_true", help="every unit")
    make.add_argument("--group-by", default=None, help="a label of the project: one pack per value, a tab per unit "
                                                      "(none: one per unit; default: the grouping setting)")
    make.add_argument("--reply-by", default=None, help="the reply-by date, YYYY-MM-DD (default: the setting)")
    make.add_argument("--sender", default=None, help="who replies go to (default: the sender setting)")
    sent = sub.add_parser("mark-sent", help="record that packs went out")
    which = sent.add_mutually_exclusive_group(required=True)
    which.add_argument("--units", help="unit ids or names, separated by commas")
    which.add_argument("--made", action="store_true", help="every unit with a pack that is not marked sent yet")
    listing = sub.add_parser("list", help="packs made and sent")
    for p in (make, sent, listing):
        p.add_argument("--project", dest="sub_project", metavar="PROJECT", default=None, help=PROJECT_HELP)
        p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.sub_project or args.project)
    word = feedback.unit_word(project)

    if args.action == "make":
        scope = "all" if args.all else "ready" if args.ready else "units"
        result = packs.make_packs(project, scope, _ids(args.units) if args.units else None, args.group_by,
                                  args.reply_by, args.sender)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
            return 0
        names = feedback.units_by_id(project)
        for p in result["packs"]:
            print(f"{p['file']}  ({', '.join(names[u]['name'] for u in p['units'])})")
        for uid, qs in result["warnings"].items():
            print(f"Warning: {names[uid]['name']} has {len(qs)} answer{'s' if len(qs) != 1 else ''} still to check "
                  f"({', '.join(qs[:6])}{'...' if len(qs) > 6 else ''}). The pack was made with the answers as they are.")
        for s in result["skipped"]:
            print(f"Skipped {names[s['unit']]['name']}: {s['why']}")
        print(f"Made {len(result['packs'])} pack{'s' if len(result['packs']) != 1 else ''} in {result['folder']}/. "
              "Send them from your own email, then run: hb packs mark-sent")
        return 0

    if args.action == "mark-sent":
        if args.made:
            state = feedback.read_state(project)
            ids = [u for u, r in state["units"].items() if r.get("pack_made") and not r.get("sent")]
            if not ids:
                raise HandbookError(f"No {word} has a pack that is not marked as sent.")
        else:
            ids = _ids(args.units)
        done = feedback.mark_sent(project, ids)
        if args.json:
            print(json.dumps({"units": done}))
        else:
            print(f"Marked {len(done)} as sent: {', '.join(done)}.")
        return 0

    state = feedback.read_state(project)
    rows = [{"unit": uid, "name": u.get("name"), "pack": (state["units"].get(uid) or {}).get("pack"),
             "pack_made": (state["units"].get(uid) or {}).get("pack_made"),
             "sent": (state["units"].get(uid) or {}).get("sent")}
            for uid, u in feedback.units_by_id(project).items()]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    for r in rows:
        if r["pack_made"]:
            print(f"{r['name']}: pack made {r['pack_made'][:10]}, " + (f"sent {r['sent'][:10]}" if r["sent"] else "not sent")
                  + f" ({(r['pack'] or {}).get('file')})")
    if not any(r["pack_made"] for r in rows):
        print("No packs made yet.")
    return 0

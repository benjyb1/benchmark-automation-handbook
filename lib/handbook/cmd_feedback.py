"""hb feedback: the replies to the packs, and the owner's decisions on each point.

    feedback status                                  where every unit is, and what waits for the owner
    feedback read PATH... [--unit U] [--replace] [--review | --no-review]
                                                     read returned files (a folder, or files)
    feedback decide --unit U --question Q (--keep | --value V) [--reason TEXT]
    feedback undo --unit U --question Q              take a decision back
    feedback review --unit U [--question Q ...] [--force]
                                                     ask Claude to read the free text (one small call)
    feedback stage --unit U STAGE                    set a stage by hand (nothing is deleted)
    feedback move-file --file ID --units U[,V]       a file was for someone else (or for several)
    feedback remove-file --file ID
    feedback confirm-group --file ID [--units U,V]   a group file: which units it covers
    feedback restart --unit U                        start a unit over (files and decisions cleared)
    feedback mark-checked --unit U [--questions Q,R] the owner has checked these answers
    feedback settings [--set key=value ...]          show or change the round's settings
    feedback plain-reasons [--units U,V]             rewrite the analyst notes in plain words, one small
                                                     Claude call per unit (about 5 to 8 thousand tokens each)
    feedback read-file --file ID                     one small Claude call: whose is a file nobody matched
"""
from __future__ import annotations
import json

from . import feedback, replies
from .cli import command_parser, open_project
from .folder import HandbookError

PROJECT_HELP = "the project folder (default: $HANDBOOK_PROJECT, then the current folder)"


def _ids(text):
    return [x.strip() for x in str(text).split(",") if x.strip()]


def _show(result, args, lines):
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


def _parser():
    parser = command_parser("feedback", "Read the replies to the packs and record the owner's decisions.")
    sub = parser.add_subparsers(dest="action", required=True)

    def add(name, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--project", dest="sub_project", metavar="PROJECT", default=None, help=PROJECT_HELP)
        p.add_argument("--json", action="store_true")
        return p

    add("status", "where every unit is")
    p = add("read", "read returned files")
    p.add_argument("paths", nargs="+", help="the files, or a folder of them")
    p.add_argument("--unit", default=None, help="say whose they are (id or name, commas for several)")
    p.add_argument("--replace", action="store_true", help="clear what that unit already sent (and its decisions) first")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--review", dest="review", action="store_true", default=None,
                      help="have Claude read the free text now")
    mode.add_argument("--no-review", dest="review", action="store_false", help="do not call Claude")
    p = add("decide", "keep our answer or change it")
    p.add_argument("--unit", required=True)
    p.add_argument("--question", required=True)
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--keep", action="store_true")
    what.add_argument("--value", help="the level to change the answer to")
    p.add_argument("--reason", default="", help="why; kept to go back to them")
    p = add("undo", "take a decision back")
    p.add_argument("--unit", required=True)
    p.add_argument("--question", required=True)
    p = add("review", "ask Claude to read a unit's replies")
    p.add_argument("--unit", required=True)
    p.add_argument("--question", action="append", default=None)
    p.add_argument("--force", action="store_true", help="read points Claude has read already")
    p = add("stage", "set a stage by hand")
    p.add_argument("--unit", required=True)
    p.add_argument("stage")
    p = add("move-file", "send a file to other units")
    p.add_argument("--file", required=True)
    p.add_argument("--units", required=True, help="unit ids or names, separated by commas")
    p = add("remove-file", "remove a file")
    p.add_argument("--file", required=True)
    p = add("confirm-group", "confirm which units a group file covers")
    p.add_argument("--file", required=True)
    p.add_argument("--units", default=None, help="default: every unit in the group")
    p = add("restart", "start a unit over")
    p.add_argument("--unit", required=True)
    p = add("mark-checked", "mark answers as checked")
    p.add_argument("--unit", required=True)
    p.add_argument("--questions", default=None, help="default: all that need it")
    p = add("plain-reasons", "rewrite the reasons in plain words")
    p.add_argument("--units", default=None, help="unit ids or names, separated by commas (default: every unit with notes)")
    p = add("read-file", "ask Claude whose a waiting file is")
    p.add_argument("--file", required=True)
    p = add("settings", "show or change settings")
    p.add_argument("--set", dest="set_", action="append", default=[], metavar="KEY=VALUE")
    return parser


def main(argv) -> int:
    args = _parser().parse_args(argv)
    project = open_project(args.sub_project or args.project)
    names = {uid: u.get("name") for uid, u in feedback.units_by_id(project).items()}
    word = feedback.unit_word(project)
    act = args.action

    if act == "status":
        part = feedback.bundle_part(project, always=True)
        lines = [f"{s['label']}: {part['counts'][s['id']]}" for s in part["stages"]]
        for uid, u in part["units"].items():
            if u.get("points"):
                lines.append(f"  {names[uid]}: {u['counts']['decided']} of {u['counts']['points']} points decided")
        for f in part["unmatched"]:
            lines.append(f"Unmatched {f['id']} ({f['name']}): {f['reason']}")
        for f in part["groups"]:
            lines.append(f"Group file {f['id']} ({f['name']}) for {f['group']['value']}: confirm which of "
                         f"{len(f['units'])} it covers")
        _show(part, args, lines)
        return 0

    if act == "read":
        res = replies.read_replies(project, args.paths, args.unit and _ids(args.unit), args.replace, args.review)
        lines = []
        for f in res["files"]:
            if f["status"] == "matched":
                lines.append(f"{f['file']}: {', '.join(names[u] for u in f['units'])} (by {f['via']}), "
                             f"{f['points']} point{'s' if f['points'] != 1 else ''}"
                             + (", a reply to an older pack: we have changed an answer since" if f.get("old_pack") else ""))
            elif f["status"] == "group":
                lines.append(f"{f['file']}: for {f['group']['value']}. {f['reason']} (file {f['file_id']})")
            elif f["status"] == "unmatched":
                guess = f" Maybe: {', '.join(names[g] for g in f['guess'])}." if f.get("guess") else ""
                lines.append(f"{f['file']}: not matched. {f['reason']}{guess} (file {f['file_id']})")
            else:
                lines.append(f"{f['file']}: {f['reason']}")
        for e in res["errors"]:
            lines.append(f"Claude could not read {names[e['unit']]}'s reply: {e['error']}")
        if res["tokens"]:
            lines.append(f"Claude has read replies for {res['tokens']:,} tokens in total for these {word}s.")
        _show(res, args, lines)
        return 0

    if act == "decide":
        res = feedback.decide(project, args.unit, args.question, "keep" if args.keep else "change",
                              None if args.keep else args.value, args.reason)
        b, a = res["before"], res["after"]
        _show(res, args, [f"{names[res['unit']]}, question {args.question}: {res['text']}.",
                          f"Overall {b.get('overall')} ({b.get('band')}) -> {a.get('overall')} ({a.get('band')}). "
                          "Build to see it in the results."])
        return 0

    if act == "undo":
        res = feedback.undo_decision(project, args.unit, args.question)
        _show(res, args, [f"Decision on question {args.question} taken back."
                          + (" The answer is as it was." if res["answer_put_back"] else "")])
        return 0

    if act == "review":
        res = replies.review_unit(project, args.unit, args.question, args.force)
        t = res["tokens"]
        _show(res, args, [f"Claude read {len(res['reviewed'])} point{'s' if len(res['reviewed']) != 1 else ''}"
                          + (f" for {t['processed']:,} tokens." if t else ".")])
        return 0

    if act == "stage":
        res = feedback.set_stage(project, args.unit, args.stage)
        _show(res, args, [f"{names[res['unit']]}: {feedback.STAGE_WORDS[res['stage']]} ({feedback.BY_HAND_WORDS})."])
        return 0

    if act == "move-file":
        res = feedback.move_file(project, args.file, _ids(args.units))
        _show(res, args, [f"File {args.file} is now with {', '.join(names[u] for u in res['units'])}."])
        return 0

    if act == "remove-file":
        res = feedback.remove_file(project, args.file)
        _show(res, args, [f"Removed {res['name']}." + (f" Put back the answers on {', '.join(res['answers_put_back'])}."
                                                       if res["answers_put_back"] else "")])
        return 0

    if act == "confirm-group":
        res = feedback.confirm_group_file(project, args.file, _ids(args.units) if args.units else None)
        _show(res, args, [f"File {args.file} now covers {', '.join(names[u] for u in res['units'])}."])
        return 0

    if act == "restart":
        res = feedback.restart(project, args.unit)
        _show(res, args, [f"{names[res['unit']]} started over: {res['files_cleared']} file(s) cleared"
                          + (f", answers put back on {', '.join(res['answers_put_back'])}" if res["answers_put_back"] else "")
                          + f". Now: {feedback.STAGE_WORDS.get(res['stage'], 'worked out from the answers')}."])
        return 0

    if act == "mark-checked":
        done = feedback.mark_checked(project, args.unit, _ids(args.questions) if args.questions else None)
        _show({"questions": done}, args, [f"Marked {len(done)} answer{'s' if len(done) != 1 else ''} as checked."])
        return 0

    if act == "plain-reasons":
        from . import plainreasons
        ids = plainreasons.with_notes(project, _ids(args.units) if args.units else None)
        if not ids:
            raise HandbookError("None of those has a note to rewrite yet.")
        feedback.set_settings(project, reasons="plain")
        done, tokens, problems = [], 0, []
        for uid in ids:
            try:
                got = plainreasons.rewrite_unit(project, uid)
                done.append(uid)
                tokens += got["tokens"]
            except HandbookError as e:
                problems.append(f"{names[uid]}: {e}")
        lines = [f"Plain reasons written for {len(done)} of {len(ids)} {word}s, {tokens:,} tokens."] + problems
        _show({"units": done, "tokens": tokens, "problems": problems}, args, lines)
        return 1 if problems and not done else 0

    if act == "read-file":
        res = replies.read_unmatched(project, args.file)
        who = ", ".join(names[u] for u in res["units"]) or "not clear"
        _show(res, args, [f"{who}. {res['why']} ({res['tokens']:,} tokens)"])
        return 0

    if args.set_:                                           # settings
        changes = {}
        for item in args.set_:
            if "=" not in item:
                raise HandbookError(f"Use --set key=value, for example --set review=check (not {item!r}).")
            key, value = item.split("=", 1)
            changes[key.strip()] = value.strip()
        current = feedback.set_settings(project, **changes)
    else:
        current = feedback.read_settings(project)
    _show(current, args, [f"{k}: {v}" for k, v in current.items()])
    return 0

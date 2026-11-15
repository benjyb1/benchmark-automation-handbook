"""hb review send|bring-in|status: an Excel workbook of every answer for colleagues to check, and
reading their marks back in (review.py).

    review send [--sender NAME]
        write the workbook into review/ and print where it is
    review bring-in [--file PATH]
        read the newest workbook in review/ (or the one given) and apply it: check marks under each
        reviewer's name, corrections for answers they changed, and a list to look at
    review status
        the workbooks in review/ and what the last bring-in did
"""
from __future__ import annotations
import json

from . import review
from .cli import command_parser, open_project
from .folder import HandbookError


def main(argv) -> int:
    parser = command_parser("review", "Send the answers to colleagues for review in Excel, and bring their marks back in.")
    sub = parser.add_subparsers(dest="action", required=True)
    send = sub.add_parser("send", help="write the review workbook")
    send.add_argument("--sender", default=None, help="the name reviewers tell when they're done")
    bring = sub.add_parser("bring-in", help="read a review workbook back in")
    bring.add_argument("--file", default=None, help="the workbook (default: the newest in review/)")
    status = sub.add_parser("status", help="the workbooks and the last bring-in")
    for p in (send, bring, status):
        p.add_argument("--project", dest="sub_project", metavar="PROJECT", default=None,
                       help="the project folder (default: $HANDBOOK_PROJECT, then the current folder)")
        p.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    try:
        project = open_project(args.sub_project or args.project)
        if args.action == "send":
            out = review.send(project, sender=args.sender)
            text = (f"Wrote {out['answers']} answers to review into {out['path']}. Share that file with your colleagues "
                    "(or the review folder, if the project is in OneDrive, SharePoint, Google Drive or Dropbox).")
        elif args.action == "bring-in":
            out = review.bring_in(project, args.file)
            text = review.summary_words(out)
        else:
            out = review.status(project)
            text = (f"{len(out['workbooks'])} workbook(s) in {out['folder']}."
                    + (" Last brought in " + review.summary_words(out["last"]) if out.get("last") else ""))
    except HandbookError as e:
        print(f"review: {e}")
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else text)
    return 0

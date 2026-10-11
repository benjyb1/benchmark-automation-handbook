"""hb mark-weak --doc D (--why T | --clear): mark a saved document as a weak source (not in the
organisation's own words: a news report, blog, aggregator or directory) with a one-line reason, or
clear it. Clearing records that the document is not weak, which also overrides the rule that a copy
on a third-party site is weak. Documents the owner provided are never weak. Run `hb check`
afterwards so the cells that cite it get or lose the `weak_source` flag.
"""
from __future__ import annotations
import argparse

from . import fetch
from .cli import command_parser, open_project
from .folder import HandbookError


def _why(text: str) -> str:
    try:
        return fetch.clean_why(text)
    except HandbookError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def main(argv) -> int:
    parser = command_parser("mark-weak", "Mark a document as a weak source, or clear the mark.")
    parser.add_argument("--doc", required=True, help="the document id, for example d-0123456789")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--why", type=_why, default=None,
                       help="why it is weak, in one line, for example \"A news report, not the "
                            "organisation's own words\"")
    which.add_argument("--clear", action="store_true", help="record that it is not a weak source")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    doc = args.doc.strip()

    if args.clear:
        meta = fetch.clear_weak(project, doc)
        print(f"{doc} ({meta.get('title') or 'untitled'}) no longer counts as a weak source.")
    else:
        meta = fetch.mark_weak(project, doc, args.why)
        print(f"Marked {doc} ({meta.get('title') or 'untitled'}) as a weak source: {args.why}")
    print("Run check, then build, so the cells that cite it show the change.")
    return 0

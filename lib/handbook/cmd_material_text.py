"""hb material-text --project DIR FILE: print one of the owner's uploads as text (PDF, Word, Excel, text).

For the set-up draft (`hb draft-setup`), which reads the owner's files this way.
"""
from __future__ import annotations
from pathlib import Path

from .cli import command_parser, open_project
from .folder import HandbookError
from .material import read_text


def main(argv) -> int:
    parser = command_parser("material-text", "Print one of the owner's uploaded files as text.")
    parser.add_argument("file", help="the file, in inbox/uploads/")
    args = parser.parse_args(argv)
    open_project(args.project)
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise HandbookError(f"There is no file {path}.")
    try:
        print(read_text(path))
    except ValueError as e:
        raise HandbookError(str(e)) from None
    return 0

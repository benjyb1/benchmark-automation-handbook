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
    project = open_project(args.project)
    path = Path(args.file).expanduser()
    if not path.is_file():
        # A bare name, as the help says (InfluenceMap, sweep round 3: "There is no file" for a name in
        # inbox/uploads/): the stored file, or the one uploaded under that name (`<id>--<name>`).
        uploads = project.path / "inbox" / "uploads"
        named = [f for f in sorted(uploads.glob("*")) if f.is_file()
                 and (f.name == args.file or f.name.split("--", 1)[-1] == args.file)] if uploads.is_dir() else []
        if len(named) == 1:
            path = named[0]
        elif named:
            raise HandbookError(f"More than one upload is called {args.file}: " + ", ".join(f.name for f in named) + ".")
        else:
            raise HandbookError(f"There is no file {path}, nor one called {args.file} in inbox/uploads/.")
    try:
        print(read_text(path))
    except ValueError as e:
        raise HandbookError(str(e)) from None
    return 0

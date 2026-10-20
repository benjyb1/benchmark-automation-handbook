"""hb team <action>: working as a team on one shared project folder (see team.py). Owners do all of
this from the page (the Team or Share button); the command is for the set-up Claude and for tests.

    hb team status  --project DIR             who is on the team, clashes waiting, the sync service
    hb team share   --project DIR --into DIR  copy the project into a shared folder as a team project
    hb team backup  --project DIR             take today's snapshot now (into ~/.handbook/backups)
    hb team unpack  ZIP [--root DIR]          open a copy someone sent (default root ~/Benchmark tool)
"""
from __future__ import annotations
import json
import sys

from . import team
from .cli import command_parser, open_project
from .folder import HandbookError, default_root
from .localstore import Config


def main(argv) -> int:
    parser = command_parser("team", "Work on one shared project as a team.", project=False)
    parser.add_argument("action", choices=("status", "share", "backup", "unpack"))
    parser.add_argument("zip", nargs="?", help="for unpack: the copy's .zip file")
    parser.add_argument("--project", default=None, help="the project folder")
    parser.add_argument("--into", default=None, help="for share: the shared folder (Dropbox, Google Drive...)")
    parser.add_argument("--root", default=None, help="for unpack: where to put the project")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    me = team.me(Config())

    if args.action == "unpack":
        if not args.zip:
            raise HandbookError("Say which file to open: hb team unpack <copy.zip>.")
        project = team.unpack(args.zip, args.root or default_root())
        print(f"Opened the copy at {project.path}. Open it with: hb serve --project \"{project.path}\"")
        return 0

    project = open_project(args.project)
    if args.action == "share":
        if not args.into:
            raise HandbookError("Say which shared folder to put the project in: --into <folder>.")
        new = team.share(project, args.into, me)
        out = {"path": str(new.path), "service": team.sync_service(new.path)}
        print(json.dumps(out) if args.json else f"Shared: the team project is now at {new.path}.")
        return 0
    if args.action == "backup":
        path = team.take_backup(project, force=True)
        print(f"Snapshot taken: {path}")
        return 0
    info = {"team": project.is_team(), "me": me, "members": team.names(project),
            "clashes_open": len(team.clashes(project, only_open=True)) if project.is_team() else 0,
            "service": team.sync_service(project.path), "backups": [b["name"] for b in team.list_backups(project)]}
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    elif not info["team"]:
        print(f"{project.path.name} is not shared with a team. Share it from the page, or: hb team share --into <folder>.")
    else:
        print(f"Team: {', '.join(info['members'].values())}. Clashes waiting: {info['clashes_open']}. "
              f"Folder: {info['service'] or 'on this computer only'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

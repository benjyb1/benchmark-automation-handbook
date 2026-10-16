"""`hb <command> [args]`: runs `handbook.cmd_<command>.main(argv) -> int` (dashes become underscores).

Commands are found by file name, so a new `cmd_<name>.py` needs no registration here. Exit codes:
0 success, 1 a problem the command explains (raise `HandbookError`), 2 bad arguments.

Command modules use `command_parser` for their arguments and `open_project` for the folder:

    def main(argv) -> int:
        parser = command_parser("status", "Show the checklist and the next step.")
        parser.add_argument("--json", action="store_true")
        args = parser.parse_args(argv)
        project = open_project(args.project)
"""
from __future__ import annotations
import argparse
import importlib
import os
import re
import sys
from pathlib import Path

from . import edition
from .folder import HandbookError, Project

HERE = Path(__file__).resolve().parent


def commands() -> list[str]:
    """Every command name, from the `cmd_*.py` files beside this one."""
    return sorted(p.stem[len("cmd_"):].replace("_", "-") for p in HERE.glob("cmd_*.py")
                  if edition.is_full() or p.stem not in edition.FULL_MODULES)     # the free edition has no packs or feedback


def command_parser(command: str, description: str, project: bool = True) -> argparse.ArgumentParser:
    """An argument parser for `hb <command>` that already takes `--project` (unless `project`
    is False)."""
    parser = argparse.ArgumentParser(prog=f"hb {command}", description=description)
    if project:
        parser.add_argument("--project", default=None,
                            help="the project folder (default: $HANDBOOK_PROJECT, then the current folder)")
    return parser


def open_project(path: str | None = None, must_exist: bool = True) -> Project:
    """The project at `path`, else $HANDBOOK_PROJECT, else the current folder. With `must_exist`,
    raises HandbookError when there is no project.json there."""
    project = Project(path or os.environ.get("HANDBOOK_PROJECT") or os.getcwd())
    if must_exist and not project.exists():
        raise HandbookError(f"No project found at {project.path}: there is no project.json. "
                            "Pass --project, set HANDBOOK_PROJECT, or run init first.")
    return project


def _usage() -> str:
    names = commands()
    return ("Usage: python3 handbook/lib/hb.py <command> [--project FOLDER] [args]\n"
            f"Commands ({len(names)}): {', '.join(names)}")


def _exit_code(code) -> int:
    if code is None:
        return 0
    return code if isinstance(code, int) else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_usage(), file=sys.stdout if argv else sys.stderr)
        return 0 if argv else 2
    name = argv[0]
    module = "cmd_" + name.replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name) or not (HERE / f"{module}.py").is_file():
        print(f"Unknown command {name!r}.\n{_usage()}", file=sys.stderr)
        return 2
    if module in edition.FULL_MODULES and not edition.is_full():
        print(edition.in_full(f"hb {name}, the round with the organisations you score,"), file=sys.stderr)
        return 1
    command = importlib.import_module(f"{__package__}.{module}")
    try:
        return _exit_code(command.main(argv[1:]))
    except HandbookError as e:
        print(str(e), file=sys.stderr)
        return 1
    except SystemExit as e:          # argparse: --help exits 0, bad arguments exit 2
        return _exit_code(e.code)
    except (OSError, ValueError, KeyError, AttributeError, TypeError, UnicodeDecodeError) as e:
        # Not a problem the command knew how to explain, usually a project file with the wrong
        # shape. One plain line, no traceback, and the command that finds the broken file.
        detail = " ".join(str(e).split()) or type(e).__name__
        print(f"hb {name} could not finish ({type(e).__name__}: {detail}). "
              "Run hb validate to check the project files.", file=sys.stderr)
        return 1

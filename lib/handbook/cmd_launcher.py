"""hb launcher --project DIR [--desktop]: make "Open <benchmark>.app", a double-click way back in.

The app starts `hb serve` for the project in the background (no Terminal window), which opens the
page in the browser, so an owner can stop and come back without asking Claude. It is a small
AppleScript app made with macOS's own `osacompile`; nothing is downloaded, and because it is made
on this Mac it opens without a security warning. It goes in the project folder, and with
`--desktop` a copy goes on the Desktop too. The server's output goes to `<project>/.serve.log`.
"""
from __future__ import annotations
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .cli import command_parser, open_project
from .folder import HandbookError

HB = Path(__file__).resolve().parents[1] / "hb"


def app_name(benchmark: str) -> str:
    """'Open <name>.app', with characters Finder dislikes in a name taken out."""
    clean = re.sub(r"\s+", " ", re.sub(r"[/:\\]+", " ", benchmark or "")).strip() or "my benchmark"
    return f"Open {clean}.app"


def _applescript_text(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def script(hb: Path, project: Path) -> str:
    """The AppleScript: start `hb serve` in the background and return at once."""
    return "\n".join([
        f"set hb to {_applescript_text(str(hb))}",
        f"set proj to {_applescript_text(str(project))}",
        'do shell script quoted form of hb & " serve --project " & quoted form of proj & '
        '" > " & quoted form of (proj & "/.serve.log") & " 2>&1 &"',
    ])


def make_app(target: Path, text: str) -> None:
    if shutil.which("osacompile") is None:
        raise HandbookError("Making the launcher needs a Mac (osacompile was not found).")
    if target.exists():
        shutil.rmtree(target)
    got = subprocess.run(["osacompile", "-o", str(target), "-e", text], capture_output=True, text=True)
    if got.returncode != 0:
        raise HandbookError(f"The launcher could not be made: {(got.stderr or '').strip()[:200]}")


def main(argv) -> int:
    parser = command_parser("launcher", "Make a double-click app that opens this benchmark's page.")
    parser.add_argument("--desktop", action="store_true", help="also put a copy on the Desktop")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    name = app_name((project.read("project") or {}).get("name"))
    text = script(HB, project.path)
    places = [project.path] + ([Path.home() / "Desktop"] if args.desktop else [])
    for place in places:
        make_app(place / name, text)
        print(f"Made {place / name}.")
    print("Double-click it to open the benchmark's page; it starts the tool in the background.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""hb kit --row ROW --run RUN [--questions Q,...] [--second]: write the scorer's kit (or, with
--second, a second reader's kit for the listed questions) outside the project and print its path."""
from __future__ import annotations

from .cli import command_parser, open_project
from .kit import write_kit


def id_list(text: str | None) -> list[str] | None:
    """"a, b,c" -> ["a", "b", "c"]; None or blank -> None."""
    items = [x.strip() for x in (text or "").split(",") if x.strip()]
    return items or None


def main(argv) -> int:
    parser = command_parser("kit", "Write the scorer's kit for one row and print where it is.")
    parser.add_argument("--row", required=True, help="the unit id")
    parser.add_argument("--run", required=True, help="the run id (from run start)")
    parser.add_argument("--questions", help="only these question ids, comma separated")
    parser.add_argument("--second", action="store_true",
                        help="a second reader's kit for the listed questions (needs --questions)")
    parser.add_argument("--look", action="append", default=None, metavar="DOC:PAGE",
                        help="a page the brief asks the scorer to read first (a correction's re-read); "
                             "repeat for more")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    look = []
    for item in args.look or []:
        doc, sep, page = item.partition(":")
        if not (sep and doc.strip() and page.strip().isdigit()):
            parser.error(f"--look should look like DOC:PAGE, for example d-0123456789:4 (got {item!r})")
        look.append((doc.strip(), int(page)))
    kit = write_kit(project, args.row, args.run, id_list(args.questions), args.second, look)
    docs = [d for d in (kit / "docs").iterdir() if d.is_dir()]
    kept = sum(1 for d in docs if any(d.glob("p*.txt")))
    what = "second-reading kit" if args.second else "scorer kit"
    brief = kit / "second" / "brief.md" if args.second else kit / "brief.md"
    print(f"Wrote the {what} for {args.row}: {len(docs)} document(s), {kept} with page files, "
          f"{len(docs) - kept} without. The brief is {brief}")
    print(kit)
    return 0

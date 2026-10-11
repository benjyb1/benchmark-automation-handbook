"""hb evidence (--rows R,... | --label LABEL=VALUE | --all) [--questions Q,...] [--format xlsx|html]
[--out PATH]: write the evidence behind every answer for the chosen rows, as a workbook (one sheet
per row) or one page to send to the organisation scored, and print where it is."""
from __future__ import annotations

from .cli import command_parser, open_project
from .evidence_pack import FORMATS, export


def id_list(text: str | None) -> list[str] | None:
    """"a, b,c" -> ["a", "b", "c"]; None or blank -> None. (Copied from cmd_kit.)"""
    items = [x.strip() for x in (text or "").split(",") if x.strip()]
    return items or None


def main(argv) -> int:
    parser = command_parser("evidence", "Write the evidence for chosen rows to a workbook or a page "
                                        "and print where it is.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--rows", help="these row ids, comma separated")
    which.add_argument("--label", help="every row with this label value, as LABEL=VALUE (country=Denmark)")
    which.add_argument("--all", action="store_true", help="every row")
    parser.add_argument("--questions", help="only these question ids, comma separated")
    parser.add_argument("--format", choices=FORMATS, default=None,
                        help="xlsx (one sheet per row) or html (one page); default from --out, else xlsx")
    parser.add_argument("--out", help="where to write it (default: exports/ in the project folder)")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    rows = id_list(args.rows) if args.rows is not None else None
    result = export(project, rows=[] if args.rows is not None and rows is None else rows,
                    label=args.label, all_rows=args.all, questions=id_list(args.questions),
                    fmt=args.format, out=args.out)
    proj = project.read("project")
    word = ((proj.get("unit_word") or "row") if result["rows"] == 1
            else (proj.get("unit_word_plural") or "rows"))
    n, k = result["questions"], result["with_evidence"]
    print(f"Wrote the evidence for {result['rows']} {word}: {n} answer{'s' if n != 1 else ''}, "
          f"{k} with a quote and {n - k} saying why there is none.")
    print(result["path"])
    return 0

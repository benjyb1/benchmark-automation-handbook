"""hb conflicts [--rows R1,R2] [--questions Q1,Q2] [--kit KIT --cutoff DATE] [--json]: the figures from the
contradiction log (conflicts.py), worked out by plain code with no model call: how often sources disagreed behind
an answer, per indicator and per row; how often that ended in an unsure answer; which kind of source won and
whether that follows the evidence order (conflicts.py); and, from the documents' own dates, whether the answer went
with the copy inside the study period and the later version. Answers read before the log existed are left out.
With --kit, the answers of one kit (KIT/answers.json) instead of the project's."""
from __future__ import annotations
import json
from pathlib import Path

from .cli import command_parser, open_project
from .conflicts import figures, kit_records, project_records, report
from .folder import HandbookError


def main(argv) -> int:
    parser = command_parser("conflicts", "Figures from the contradiction log: what disagreed, and which side won.")
    parser.add_argument("--rows", default=None, help="only these rows, comma-separated")
    parser.add_argument("--questions", default=None, help="only these questions, comma-separated")
    parser.add_argument("--kit", default=None, help="a kit folder: its answers.json instead of the project's answers")
    parser.add_argument("--cutoff", default=None, help="with --kit, the end of the study period (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    if args.kit:
        kit = Path(args.kit).expanduser()
        if not (kit / "answers.json").is_file():
            raise HandbookError(f"There is no answers.json in {kit}.")
        records = kit_records(kit, cutoff=args.cutoff)
    else:
        records = project_records(open_project(args.project))
    rows = {r for r in (args.rows or "").split(",") if r}
    qids = {q for q in (args.questions or "").split(",") if q}
    records = [r for r in records if (not rows or r["row"] in rows) and (not qids or r["question"] in qids)]
    figs = figures(records)
    print(json.dumps(figs, ensure_ascii=False, indent=1) if args.json else report(figs))
    return 0

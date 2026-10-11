"""hb ingest-answers --row ROW --run RUN --file PATH: check a scorer's answers.json and store it in
answers/<row>.json. Nothing is stored when any cell has a problem (exit 1, each problem listed).
The kit's capture.json beside the answers file, when there is one, binds each answer to the text
it was scored from."""
from __future__ import annotations
from pathlib import Path

from .cli import command_parser, open_project
from .folder import HandbookError, read_json
from .ingest import ingest_answers


def _listed(ids: list) -> str:
    return f"{len(ids)} ({', '.join(ids)})" if ids else "0"


def main(argv) -> int:
    parser = command_parser("ingest-answers", "Check and store a scorer's answers for one row.")
    parser.add_argument("--row", required=True, help="the unit id")
    parser.add_argument("--run", required=True, help="the run id the answers belong to")
    parser.add_argument("--file", required=True, help="the scorer's answers.json")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise HandbookError(f"There is no answers file at {path}.")
    captures = read_json(path.with_name("capture.json"), None)      # the kit's, when scored from a kit
    stored = ingest_answers(project, args.row, args.run, read_json(path), captures)
    cells = project.answers(args.row)["cells"]
    unsure = [q for q in stored if cells[q].get("unsure")]
    judgement = [q for q in stored if cells[q].get("judgement")]
    print(f"Stored {len(stored)} answer(s) for {args.row} from run {args.run}: {', '.join(stored)}.")
    if captures is None:
        print("No capture.json beside the answers, so these answers are not bound to the kit's copies.")
    print(f"Unsure: {_listed(unsure)}. Resting on an undecided rule: {_listed(judgement)}.")
    return 0

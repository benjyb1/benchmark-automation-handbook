"""hb chart add|remove|list: the owner's charts in report/charts/, shown on the Results tab.

    chart add --file F        check the chart file and add it (an existing id is replaced)
    chart remove --id ID
    chart list [--json]       the charts, and the columns each dataset has in this project
"""
from __future__ import annotations
import json
from pathlib import Path

from .cli import command_parser, open_project
from .custom_charts import add_chart, all_columns, read_charts, remove_chart
from .folder import HandbookError, read_json

PROJECT_HELP = "the project folder (default: $HANDBOOK_PROJECT, then the current folder)"


def main(argv) -> int:
    parser = command_parser("chart", "Add, remove or list the owner's charts (report/charts/).")
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="check a chart file and add it")
    add.add_argument("--file", required=True, help="a chart: {id, title, description, dataset, spec}")
    remove = sub.add_parser("remove", help="remove a chart")
    remove.add_argument("--id", required=True, help="the chart's id")
    listing = sub.add_parser("list", help="list the charts and the columns to use")
    listing.add_argument("--json", action="store_true")
    for p in (add, remove, listing):
        p.add_argument("--project", dest="sub_project", metavar="PROJECT", default=None, help=PROJECT_HELP)
    args = parser.parse_args(argv)
    project = open_project(args.sub_project or args.project)

    if args.action == "add":
        path = Path(args.file).expanduser()
        if not path.is_file():
            raise HandbookError(f"There is no chart file at {path}.")
        chart = read_json(path)
        done = add_chart(project, chart)
        print(f"{'Added' if done == 'added' else 'Replaced'} the chart {chart['id']} "
              f"({chart['title'].strip()}). Build and publish to show it under Your charts on the "
              "Results tab.")
        return 0
    if args.action == "remove":
        remove_chart(project, args.id)
        print(f"Removed the chart {args.id}. Build and publish to take it off the Results tab.")
        return 0

    charts = read_charts(project)
    datasets = all_columns(project)
    if args.json:
        print(json.dumps({"charts": [{k: c[k] for k in ("id", "title", "description", "dataset")}
                                     for c in charts], "datasets": datasets}, ensure_ascii=False))
        return 0
    if charts:
        print(f"{len(charts)} chart{'s' if len(charts) != 1 else ''}:")
        for c in charts:
            print(f"  {c['id']}: {c['title']} (dataset {c['dataset']})")
    else:
        print("No charts yet.")
    print("Columns each dataset has in this project:")
    for name, cols in datasets.items():
        print(f"  {name}: {', '.join(cols)}")
    return 0

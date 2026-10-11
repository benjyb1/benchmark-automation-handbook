"""hb validate: run every shape check across the project folder."""
from __future__ import annotations

from .cli import command_parser, open_project
from .shapes import check_folder


def main(argv) -> int:
    parser = command_parser("validate", "Check every file in the project folder.")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    problems = check_folder(project)
    if not problems:
        print(f"No problems found in {project.path}.")
        return 0
    word = "problem" if len(problems) == 1 else "problems"
    print(f"{len(problems)} {word} found in {project.path}:")
    for p in problems:
        print(f"- {p}")
    return 1

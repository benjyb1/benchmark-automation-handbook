"""hb init --root DIR --name NAME --slug SLUG: create a new project folder."""
from __future__ import annotations

from .cli import command_parser
from .folder import default_root, init_project, synced_folder_warning


def main(argv) -> int:
    parser = command_parser("init", "Create a project folder with empty set-up files.", project=False)
    parser.add_argument("--root", default=str(default_root()),
                        help="the folder that holds projects (default: ~/Benchmark tool)")
    parser.add_argument("--name", required=True, help="the benchmark's name")
    parser.add_argument("--slug", required=True,
                        help="the folder name: lowercase letters, digits and dashes")
    args = parser.parse_args(argv)
    project = init_project(args.root, args.name, args.slug)
    print(f"Created the project folder {project.path}.")
    warning = synced_folder_warning(project.path)
    if warning:
        print(f"Warning: {warning}")
    print(f"Use --project {project.path} (or set HANDBOOK_PROJECT) with the other commands.")
    return 0

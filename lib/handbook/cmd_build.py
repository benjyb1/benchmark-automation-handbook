"""hb build [--out DIR] [--json]: turn the project folder into `viewer/` (page.html with the bundle
inlined, bundle.json, evidence/<row>.json, downloads.json, publish.json) and say what to publish."""
from __future__ import annotations
import json
from datetime import datetime, timezone

from .cli import command_parser, open_project
from .page import build


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, round(n / 1000))} KB"


def main(argv) -> int:
    parser = command_parser("build", "Build the viewer page and its data files into viewer/.")
    parser.add_argument("--out", default=None,
                        help="also copy viewer/ to this folder, outside the project (for hosts "
                             "that can only publish from certain folders)")
    parser.add_argument("--json", action="store_true", help="print the publish information as JSON")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    info = build(project, datetime.now(timezone.utc), out=args.out)
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
        return 0

    bundle = json.loads((project.viewer / "bundle.json").read_text(encoding="utf-8"))
    words = bundle["project"].get("unit_word_plural") or "rows"
    states = [c["state"] for cells in bundle["cells"].values() for c in cells.values()]
    scored = sum(s in ("answered", "no_evidence") for s in states)
    page_size = (project.path / info["page"]).stat().st_size
    files, changed = info["files"], info["changed"]
    print(f"Built {info['page']} ({_size(page_size)}) for {len(bundle['cells'])} {words}: "
          f"{len(states)} cells expected, {scored} scored, {states.count('not_applicable')} not "
          f"applicable, {states.count('not_run')} not run.")
    print(f"Data files: {len(files)} ({len(files) - 1} evidence files and downloads.json).")
    if info["first_publish"]:
        print(f"First publish: publish the page with all {len(files)} data files.")
    elif changed:
        print(f"Changed since the last publish: {len(changed)} of {len(files)} data files: "
              f"{', '.join(changed)}.")
    else:
        print(f"None of the {len(files)} data files changed since the last publish; publish the page only.")
    if info["removed"]:
        print(f"No longer built ({len(info['removed'])}): {', '.join(info['removed'])}.")
    if info.get("out"):
        print(f"Copied viewer/ to {info['out']}.")
    print("After a successful publish, run: hb published")
    return 0

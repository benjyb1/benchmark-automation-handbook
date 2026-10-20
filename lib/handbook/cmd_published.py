"""hb published: run only after a publish succeeds. Records the data files' hashes from
`viewer/publish.json` in `published.json`, so the next build lists only files that change after
this. A failed publish is not recorded, so the next build offers the same files again."""
from __future__ import annotations
import hashlib

from .cli import command_parser, open_project
from .folder import HandbookError, now_stamp, read_json


def main(argv) -> int:
    parser = command_parser("published", "Record that the viewer built last was published.")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    viewer = project.viewer
    info = read_json(viewer / "publish.json")
    if not isinstance(info, dict) or not isinstance(info.get("hashes"), dict):
        raise HandbookError("There is no viewer/publish.json yet. Run hb build, publish, then hb published.")
    for name, digest in info["hashes"].items():
        path = viewer / name
        if not path.is_file() or hashlib.sha1(path.read_bytes()).hexdigest() != digest:
            raise HandbookError(f"viewer/{name} is not the file the last build wrote. Run hb build "
                                "again and publish before recording.")
    project.write("published", {"hashes": info["hashes"], "at": now_stamp()})
    n = len(info["hashes"])
    print(f"Recorded {n} published data files. The next build lists only the ones that change.")
    return 0

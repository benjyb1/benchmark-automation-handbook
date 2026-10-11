"""hb hold-back --file NORMALISED.json --original FILE: keep a past edition's results away from the
scorers.

The normalised results (already in the held-back shape) go into `<slug>.held-back/`. The latest
past edition is `results.json` (what compare and rebuild-check use); older ones are kept as
`history/<edition>.json` in the same shape, for the trends. A file whose edition is older than
results.json's goes to `history/`; a newer one replaces results.json and moves the old one to
`history/`; the same edition replaces it. The owner's original file is copied to `original/`
(with the edition in front of its name when another edition's original already has that name),
and any of the two files that sat in the project's `inbox/` is removed from it. The checklist item
`last_results` becomes confirmed.
"""
from __future__ import annotations
import re
import shutil
from pathlib import Path

from .cli import command_parser, open_project
from .compare import edition_key
from .folder import HandbookError, read_json, write_json
from .shapes import check_held_back


def _in_inbox(project, path: Path) -> bool:
    inbox = (project.path / "inbox").resolve()
    return inbox in path.resolve().parents


def _safe(edition) -> str:
    """The edition as a file name: "2023/24" gives "2023-24"."""
    return re.sub(r"[^A-Za-z0-9.-]+", "-", str(edition)).strip("-.") or "edition"


def _place(new: dict, old: dict | None) -> str:
    """Where the new results go: "first" (nothing held back yet), "newer", "same" or "older"
    than the held-back results.json. Raises HandbookError when the editions cannot be ordered."""
    if old is None:
        return "first"
    a, b = new.get("edition"), old.get("edition")
    if a is None and b is None:
        return "same"                                # neither names an edition: replace, as ever
    if a is None:
        raise HandbookError(f"These results give no edition, but the held-back folder already "
                            f"holds the {b} edition, so it is not clear where they go. Add "
                            "\"edition\" (the year they cover) to the normalised file and run "
                            "hold-back again.")
    if b is None:
        raise HandbookError(f"The held-back results.json gives no edition, so the {a} results "
                            "cannot be placed before or after it. Add its \"edition\" (the year it "
                            "covers) and run hold-back again.")
    if str(a).strip() == str(b).strip():
        return "same"
    ka, kb = edition_key(a)[0], edition_key(b)[0]
    if ka == kb:
        raise HandbookError(f"Cannot tell whether the {a} edition is older or newer than the held-"
                            f"back {b} edition. Name editions by year, like 2024 or 2023/24.")
    return "newer" if ka > kb else "older"


def main(argv) -> int:
    parser = command_parser("hold-back", "Store a past edition's results where the scorers cannot "
                                         "see them.")
    parser.add_argument("--file", required=True,
                        help="the results, already normalised to the held-back shape (JSON)")
    parser.add_argument("--original", required=True,
                        help="the owner's original results file, kept as it was")
    args = parser.parse_args(argv)
    project = open_project(args.project)

    source, original = Path(args.file).expanduser(), Path(args.original).expanduser()
    if not source.is_file():
        raise HandbookError(f"There is no normalised results file at {source}.")
    if not original.is_file():
        raise HandbookError(f"There is no original results file at {original}.")
    data = read_json(source)
    problems = check_held_back(data)
    if problems:
        raise HandbookError(f"{source.name} is not in the held-back shape, so nothing was stored. "
                            f"{len(problems)} problem{'s' if len(problems) != 1 else ''}:\n- "
                            + "\n- ".join(problems))

    held = project.held_back
    latest_path = held / "results.json"
    old = read_json(latest_path) if latest_path.is_file() else None
    place = _place(data, old)
    edition = data.get("edition")
    target = held / "history" / f"{_safe(edition)}.json" if place == "older" else latest_path
    replaced = read_json(target, {}).get("original") if target.is_file() and place != "newer" else None

    name = f"original/{original.name}"
    if (held / name).exists() and name != replaced:      # another edition's original: keep it
        name = f"original/{_safe(edition)}-{original.name}"
    (held / "original").mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, held / name)
    data["original"] = name
    if place == "newer":
        write_json(held / "history" / f"{_safe(old['edition'])}.json", old)
    write_json(target, data)
    if place != "older":
        stale = [held / "rebuild.json"] + ([held / "history" / f"{_safe(edition)}.json"]
                                          if edition is not None else [])
        for path in stale:                           # the old check; a copy of the new latest
            if path.exists():
                path.unlink()

    moved = []
    for path in (original, source):
        if _in_inbox(project, path) and path.exists():
            path.unlink()
            moved.append(path.name)

    proj = project.read("project")
    proj.setdefault("checklist", {})["last_results"] = "confirmed"
    project.write("project", proj)

    rows = data.get("rows") or {}
    units = {u.get("id") for u in project.read("units").get("units", [])}
    unknown = [r for r in rows if r not in units]
    count = f"{len(rows)} row{'s' if len(rows) != 1 else ''} at {data.get('level')} level"
    if place == "older":
        print(f"Kept the {edition} edition as an older edition: {count}, in "
              f"{target.relative_to(held.parent)}. The latest past edition is still {old['edition']}: "
              "the comparison and the rebuild check use that one, and older editions show as trends.")
    elif place == "newer":
        print(f"Held back the {edition} edition as the latest past edition: {count}, in {held}. "
              f"The {old['edition']} edition is kept as an older edition, in history/.")
    else:
        label = f" (edition {edition})" if edition else ""
        verb = "Replaced last edition's held-back results with these" if place == "same" \
            else "Held back last edition's results"
        print(f"{verb}: {count}{label}, in {held}.")
    print(f"Original kept as {held / name}.")
    if moved:
        print(f"Removed from the inbox: {', '.join(moved)}.")
    print(f"{len(rows) - len(unknown)} of {len(rows)} rows match a unit in units.json.")
    if unknown:
        print(f"Not in units.json ({len(unknown)}): {', '.join(unknown)}.")
    if place == "older":
        print("Checklist: last_results is confirmed. Run compare again to add it to the trends.")
    else:
        print("Checklist: last_results is now confirmed. Next, run rebuild-check to see whether the "
              "score sheet gives the same results.")
    return 0

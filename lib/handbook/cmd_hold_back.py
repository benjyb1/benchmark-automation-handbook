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

from . import edition as editions
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


def store(project, data: dict, original: Path, source: Path | None = None) -> dict:
    """Hold back `data` (already in the held-back shape), keeping a copy of the owner's `original`
    file; `source`, the normalised file, is removed from the inbox with the original. Used by the
    command and by the page's set-up chat. Returns {place, edition, target, original, moved,
    old_edition}. Raises HandbookError, storing nothing, when `data` is not in the held-back shape."""
    problems = check_held_back(data)
    if problems:
        what = source.name if source else "These results"
        raise HandbookError(f"{what} is not in the held-back shape, so nothing was stored. "
                            f"{len(problems)} problem{'s' if len(problems) != 1 else ''}:\n- "
                            + "\n- ".join(problems))
    held = project.held_back
    latest_path = held / "results.json"
    old = read_json(latest_path) if latest_path.is_file() else None
    place = _place(data, old)
    if place in ("older", "newer") and not editions.is_full():
        # The free edition compares with one previous edition. A second one (older, for the trends, or newer,
        # which would push the first into history) is a Full version feature; nothing is stored.
        raise HandbookError(editions.past_editions_refusal(old.get("edition") if old else None, data.get("edition")))
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

    # A file that is also the methodology or last report stays where the owner put it (ALDF Canada,
    # 15 Oct 2026: the one report PDF held both, and hold-back removed it from the owner's material).
    other_roles = {str(x.get("file") or "") for x in project.read("project").get("provided") or []
                   if isinstance(x, dict) and x.get("role") not in (None, "last_results")}
    keep = {original.name, original.name.split("--", 1)[-1]} & other_roles
    moved = []
    for path in (original, source):
        if path is original and keep:
            continue
        if path is not None and _in_inbox(project, path) and path.exists():
            path.unlink()
            moved.append(path.name)

    proj = project.read("project")
    proj.setdefault("checklist", {})["last_results"] = "confirmed"
    project.write("project", proj)
    return {"place": place, "edition": edition, "target": target, "original": name, "moved": moved,
            "old_edition": old.get("edition") if old else None}


# --- checking the held-back figures against the owner's file ---------------------------------------
# The Animal Protection Index (14 Oct 2026): the page's chat transcribed 50 grades from the rankings
# page and shifted nine by a row; nothing caught it. Each held value is looked for beside its row's
# name in the owner's file: on the row's line when the row has several figures, else as the nearest
# of that column's values.

NEAR = 200


def _tokens(text: str, value) -> list[int]:
    """Where `value` stands alone in `text` (a grade "B", "NR", or a number however written: 73, 73.0, 73%)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        out = []
        for m in re.finditer(r"(?<![\w.])(\d+(?:[.,]\d+)?)(?![\w])", text):
            try:
                if abs(float(m.group(1).replace(",", ".")) - float(value)) < 1e-6:
                    out.append(m.start())
            except ValueError:
                pass
        return out
    v = str(value).strip()
    if not v:
        return []
    return [m.start() for m in re.finditer(rf"(?<![\w]){re.escape(v)}(?![\w])", text)]


def check_against(project, data: dict, text: str) -> list[dict]:
    """[{row, key, held, near}] for held values the owner's file does not put beside the row's name;
    rows whose name is not in the file, or whose values cannot be placed, are not judged."""
    rows = data.get("rows") or {}
    names = {u.get("id"): u.get("name") for u in project.read("units").get("units", []) if isinstance(u, dict)}
    columns: dict = {}
    for values in rows.values():
        for k, v in (values or {}).items():
            if v is not None:
                columns.setdefault(k, set()).add(v)
    lines = text.splitlines()
    out = []
    for row, values in rows.items():
        held = {k: v for k, v in (values or {}).items() if v is not None}
        name = names.get(row) or row
        if not held or not name:
            continue
        pattern = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])", re.I)
        spans = [m.span() for m in pattern.finditer(text)]
        if not spans:
            continue
        if len(held) > 1:
            mine = [ln for ln in lines if pattern.search(ln)]
            best = max(mine, key=lambda ln: sum(bool(_tokens(ln, v)) for v in held.values()), default="")
            if sum(bool(_tokens(best, v)) for v in held.values()) * 2 < len(held):
                continue                                   # its line is not in the file: cannot judge
            out += [{"row": row, "key": k, "held": v, "near": None} for k, v in held.items() if not _tokens(best, v)]
            continue
        (key, value), = held.items()
        # Judge only where it can be judged (ALDF, 15 Oct 2026: 29 false warnings out of 56 on a report whose
        # state names recur in the prose; 13 on tier names set as headings over many rows).
        if len(spans) > 2:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Numbers (ranks 1 to 56) also stand for pages and statute sections: judge them only from the
            # row's own line, never from the nearest number.
            mine = [ln for ln in lines if pattern.search(ln)]
            if any(_tokens(ln, value) for ln in mine):
                continue
            near = next((c for ln in mine for c in columns[key] if c != value and _tokens(ln, c)), None)
            if near is not None and len(mine) == 1:
                out.append({"row": row, "key": key, "held": value, "near": near})
            continue
        holders = sum(1 for v in rows.values() if (v or {}).get(key) == value)
        if isinstance(value, str) and holders > 1 and len(_tokens(text, value)) * 2 < holders:
            continue                                       # a heading over its rows, not a cell beside each
        placed = [(abs(p - (a if p < a else b)), cand) for a, b in spans for cand in columns[key]
                  for p in _tokens(text, cand) if (a - NEAR) <= p <= (b + NEAR)]
        if not placed:
            continue
        nearest = min(placed, key=lambda x: x[0])[1]
        if nearest != value:
            out.append({"row": row, "key": key, "held": value, "near": nearest})
    return out


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
    try:
        from . import material
        text = material.read_text(original)
    except Exception:  # noqa: BLE001 (a file the reader cannot read is simply not checked)
        text = ""
    got = store(project, data, original, source)
    for c in check_against(project, data, text) if text else []:
        print(f"Check {c['row']} {c['key']}: held back as {c['held']}"
              + (f", but the owner's file has {c['near']} beside its name." if c["near"] is not None
                 else ", but that figure is not on its line in the owner's file."))
    place, edition, held, name, moved, old = (got["place"], got["edition"], project.held_back, got["original"],
                                              got["moved"], got["old_edition"])
    target = got["target"]
    rows = data.get("rows") or {}
    units = {u.get("id") for u in project.read("units").get("units", [])}
    unknown = [r for r in rows if r not in units]
    count = f"{len(rows)} row{'s' if len(rows) != 1 else ''} at {data.get('level')} level"
    if place == "older":
        print(f"Kept the {edition} edition as an older edition: {count}, in "
              f"{target.relative_to(held.parent)}. The latest past edition is still {old}: "
              "the comparison and the rebuild check use that one, and older editions show as trends.")
    elif place == "newer":
        print(f"Held back the {edition} edition as the latest past edition: {count}, in {held}. "
              f"The {old} edition is kept as an older edition, in history/.")
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

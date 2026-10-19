"""The page build: project folder -> `viewer/`.

    build(project, now, out=None) -> dict      # writes viewer/, returns the publish info

What `viewer/` holds after a build:
- `page.html`: `handbook/viewer/index.html` with its four slots filled ({{TITLE}}, {{STYLE}},
  {{DATA}}, {{SCRIPT}}). The template is content only: it starts with its <title> element and has
  no doctype, html, head or body (the Artifact tool adds its own and reads the title from the
  start of the file). The bundle sits in <script type="application/json" id="hb-data">.
- `index.html`: the same page in a minimal UTF-8 document, for local preview only (never published).
- `bundle.json`, `evidence/<row>.json`, `downloads.json`, `publish.json` (see `build`).

Scripts are joined from `handbook/viewer/js/` in SCRIPT_ORDER, skipping files not written yet.
"""
from __future__ import annotations
import hashlib
import html
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .bundle import downloads_rows, evidence_data_files, make_bundle
from .contract import validate
from .folder import HandbookError, Project

VIEWER_SRC = Path(__file__).resolve().parents[2] / "viewer"
SCRIPT_ORDER = ("util.js", "data.js", "components.js", "charts.js", "edits.js", "downloads.js",
                "views/", "app.js")
# Scripts in viewer/js that are never joined into the page: `local.js` is served by `hb serve` and
# loaded before the page's own scripts there only, so the published page never has it.
LOCAL_ONLY = ("local.js",)
DATA_ID = 'id="hb-data"'
_PREVIEW = ('<!doctype html>\n<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body>\n'
            '{page}</body>\n</html>\n')


def _not_a_number(value, path: str = "") -> str | None:
    """Where in `value` the first NaN or infinity is, as a path such as `cells.se-acme.2.1.page`."""
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return path or "the bundle itself"
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for key, child in items:
        found = _not_a_number(child, f"{path}.{key}" if path else str(key))
        if found:
            return found
    return None


def bundle_json(bundle: dict) -> str:
    """The bundle as compact JSON, as inlined in the page. A NaN or infinity cannot be written as
    JSON, so it stops the build with a HandbookError that says where the value is."""
    try:
        return json.dumps(bundle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except ValueError:
        where = _not_a_number(bundle)
        raise HandbookError(
            "The page was not built: the data has a number that is not a real number (NaN or "
            f"infinity) at {where or 'an unknown place'} in the page data. Find that value in the "
            "project's files (for a path starting cells.<row>, the row's answers file) and correct "
            "it.") from None


# copied from frontend/build.py (json_for_script)
def json_for_script(text: str) -> str:
    """JSON safe inside a <script> element: every "<" written as the JSON escape \\u003c, which
    JSON.parse reads back as "<", so no text in the bundle can end the element early."""
    return text.replace("<", "\\u003c")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HandbookError(f"The viewer file {path} is missing.") from None


# adapted from frontend/build.py (join_scripts)
def join_scripts(folder: Path) -> tuple[str, list[str]]:
    """The scripts in `folder` joined in SCRIPT_ORDER (only those that exist), and their names
    relative to it. A ";" goes between files. A script the order has no place for stops the build
    rather than going missing from the page."""
    folder = Path(folder)
    if not folder.is_dir():
        return "", []
    names = []
    for entry in SCRIPT_ORDER:
        if entry == "views/":
            names += sorted(p.relative_to(folder).as_posix()
                            for p in (folder / "views").glob("*.js") if p.is_file())
        elif (folder / entry).is_file():
            names.append(entry)
    unplaced = sorted({p.relative_to(folder).as_posix() for p in folder.rglob("*.js") if p.is_file()}
                      - set(names) - set(LOCAL_ONLY))
    if unplaced:
        raise HandbookError(f"{folder} holds scripts the build has no place for: {', '.join(unplaced)}. "
                            "Add them to SCRIPT_ORDER in handbook/lib/handbook/page.py.")
    return "\n;\n".join(_read(folder / n).rstrip() for n in names), names


_SLOT = re.compile(r"\{\{([A-Z]+)\}\}")


# copied from frontend/build.py (_fill)
def _fill(text: str, slots: dict[str, str], what: str) -> str:
    """`text` with each slot {{NAME}} replaced by slots[NAME], in one pass, so nothing filled in is
    read again as a slot. Each slot must be in `text` exactly once, and no other may be."""
    found = _SLOT.findall(text)
    wrong = ([f"{{{{{s}}}}} is missing" for s in slots if s not in found]
             + [f"{{{{{s}}}}} is there {found.count(s)} times" for s in slots if found.count(s) > 1]
             + [f"{{{{{s}}}}} is not a slot the build fills" for s in dict.fromkeys(found) if s not in slots])
    if wrong:
        raise HandbookError(f"In {what}, " + "; ".join(wrong) + ".")
    return _SLOT.sub(lambda m: slots[m.group(1)], text)


# copied from frontend/build.py (_inlinable)
def _inlinable(text: str, what: str, *closers: str) -> str:
    """`text`, unless it holds something that would end its element early once inlined."""
    for closer in closers:
        if closer in text.lower():
            raise HandbookError(f"{what} contains {closer!r}, which would end its element early in the page.")
    return text


# adapted from frontend/build.py (render): one content-only rendering, no head/body markers
def render(template: str, *, title: str, styles: str, data: str, scripts: str) -> str:
    """The page from the content-only template, `data` being the bundle's JSON. The template must
    start with its <title> element and hold the hb-data script."""
    template = template.strip() + "\n"
    if not template.startswith("<title>"):
        raise HandbookError("The viewer template should start with its <title> element: the Artifact "
                            "tool reads the title from the start of the file.")
    if DATA_ID not in template:
        raise HandbookError(f'The viewer template has no <script type="application/json" {DATA_ID}>.')
    slots = {"TITLE": html.escape(title),
             "STYLE": _inlinable(styles, "handbook/viewer/styles.css", "</style"),
             "DATA": json_for_script(data),
             "SCRIPT": _inlinable(scripts, "the joined scripts", "</script", "<!--")}
    return _fill(template, slots, "the viewer template")


def page_title(project: dict) -> str:
    """The benchmark's name, with its edition when the name does not already hold it."""
    name = str(project.get("name") or "Benchmark")
    edition = str(project.get("edition") or "")
    return f"{name} {edition}" if edition and edition not in name else name


def _text(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _copy_out(viewer: Path, out: Path, names: list[str]) -> None:
    for name in names:
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(viewer / name, out / name)
    keep = {n for n in names if n.startswith("evidence/")}
    for old in (out / "evidence").glob("*.json"):
        if f"evidence/{old.name}" not in keep:
            old.unlink()


def build(project: Project, now: datetime, out=None) -> dict:
    """Write `viewer/` and return the publish info (also written as `viewer/publish.json`):

        {"page": "viewer/page.html", "files": {published path: path in the project},
         "changed": [data files whose sha1 differs from published.json; all when it is absent],
         "removed": [data files published before that this build no longer has],
         "hashes": {published path: sha1}, "first_publish": bool}

    Nothing is written when the bundle breaks its contract (HandbookError lists the problems).
    `out`, when given, is a folder outside the project that also receives a copy of `viewer/`
    (the info then has "out": its absolute path)."""
    viewer = project.path / "viewer"
    if out is not None:
        out = Path(out).expanduser().resolve()
        if out == project.path.resolve() or project.path.resolve() in out.parents:
            raise HandbookError(f"--out must be a folder outside the project; {out} is inside it "
                                "(the build already writes viewer/ there).")

    bundle = make_bundle(project, now)
    data_files = {name: _text(ev) for name, ev in evidence_data_files(project).items()}
    data_files["downloads.json"] = _text(downloads_rows(project, bundle))
    styles = VIEWER_SRC / "styles.css"
    page = render(_read(VIEWER_SRC / "index.html"), title=page_title(bundle["project"]),
                  styles=_read(styles) if styles.is_file() else "", data=bundle_json(bundle),
                  scripts=join_scripts(VIEWER_SRC / "js")[0])
    problems = validate(bundle, page_size=len(page.encode("utf-8")),
                        data_files={k: len(v.encode("utf-8")) for k, v in data_files.items()})
    if problems:
        raise HandbookError("The viewer was not built; the bundle breaks its contract:\n"
                            + "\n".join(f"  {p}" for p in problems))

    names = sorted(data_files)
    hashes = {n: sha1(data_files[n]) for n in names}
    published = project.read("published").get("hashes") or {}
    info = {"page": "viewer/page.html", "files": {n: f"viewer/{n}" for n in names},
            "changed": [n for n in names if published.get(n) != hashes[n]],
            "removed": sorted(set(published) - set(hashes)),
            "hashes": hashes, "first_publish": not published}
    if out is not None:
        info["out"] = str(out)

    for old in (viewer / "evidence").glob("*.json"):
        if f"evidence/{old.name}" not in data_files:
            old.unlink()
    for name in names:
        _write(viewer / name, data_files[name])
    _write(viewer / "page.html", page)
    _write(viewer / "index.html", _PREVIEW.format(page=page))
    _write(viewer / "bundle.json", _text(bundle))
    _write(viewer / "publish.json", _text(info))
    if out is not None:
        _copy_out(viewer, out, ["page.html", "index.html", "bundle.json", "publish.json", *names])
    return info

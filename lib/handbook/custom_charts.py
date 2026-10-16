"""The owner's charts: Vega-Lite specs Claude writes when the owner asks for a chart, kept in
`report/charts/<id>.json` and drawn on the viewer's Results tab under "Your charts".

    {"id", "title", "description", "dataset": "rows" | "cells" | "compare" | "history",
     "spec": {a Vega-Lite spec with no data}}

The page builds each dataset from the bundle (`viewer/js/views/mycharts.js`, whose COLUMNS
literal must equal DATASETS). So a spec never carries data: no `data`, `url` or `datasets` key
anywhere in it, and every figure a chart shows comes from the project's own files.

    DATASETS                                  dataset -> its columns, with three placeholders
    columns(project, dataset) -> [str]        the columns for this project, placeholders expanded
    all_columns(project) -> {dataset: [str]}
    check_chart(chart, project) -> [str]      problems, [] when the chart can be added
    add_chart(project, chart) -> "added" | "replaced"
    remove_chart(project, chart_id)
    read_charts(project) -> [chart]           in id order (the bundle's `results.charts`)
    bundle_problems(charts) -> [str]          the build's safety check on `results.charts`
"""
from __future__ import annotations
import re

from . import edition
from .folder import ID_RE, HandbookError, Project, read_json, write_json

# Placeholders in DATASETS["rows"]: one column per label (named as the label, holding the row's
# value), per score-sheet section (named by the section id, holding its percent) and per band set
# (named band_<what it bands>, holding the band's name). Scores are null for a row that is not
# fully scored, as in the Results tab's own charts.
LABELS = "<label>"
SECTIONS = "<section>"
BANDS = "band_<of>"
DATASETS = {
    "rows": ("row", "name", LABELS, SECTIONS, "overall", BANDS),
    "cells": ("row", "name", "question", "state", "value", "points", "flags"),
    "compare": ("row", "name", "key", "ours", "theirs", "agree", "reason"),
    "history": ("row", "name", "key", "edition", "value", "source"),
}
KEYS = ("id", "title", "description", "dataset", "spec")
NO_DATA = ("data", "url", "datasets")
FOLDER = ("report", "charts")

# Keys whose string value names a field, keys whose list names fields, and the strings that are
# expressions over a data object (`datum.x`). Names a transform makes are collected from `as`.
_FIELD_KEYS = ("field", "density", "quantile", "regression", "loess", "impute")
_FIELD_LIST_KEYS = ("groupby", "fold", "fields", "flatten")
_EXPR_KEYS = ("filter", "calculate", "test")
_DEFAULT_AS = {"fold": ("key", "value"), "density": ("value", "density"), "quantile": ("prob", "value")}
_DATUM = re.compile(r"""\bdatum\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*(['"])(.*?)\2\s*\])""")


def _expand(template, labels, sections, bands) -> list[str]:
    out = []
    for col in template:
        out += ([str(x) for x in labels] if col == LABELS else [str(x) for x in sections] if col == SECTIONS
                else [f"band_{x}" for x in bands] if col == BANDS else [col])
    return out


def columns(project: Project, dataset: str) -> list[str]:
    """The columns of `dataset` for this project, in the order the page builds them."""
    if dataset not in DATASETS:
        raise HandbookError(f"There is no dataset called {dataset!r}. The datasets are "
                            f"{', '.join(DATASETS)}.")
    labels = project.read("project").get("labels") or []
    sheet = project.read("scoresheet") or {}
    sections = [s.get("id") for s in sheet.get("sections") or [] if isinstance(s, dict) and s.get("id")]
    bands = [b.get("of") for b in sheet.get("bands") or [] if isinstance(b, dict) and b.get("of")]
    return _expand(DATASETS[dataset], labels, sections, bands)


def all_columns(project: Project) -> dict[str, list[str]]:
    """Every dataset's columns; the free edition has no `history` (results across past editions)."""
    return {name: columns(project, name) for name in DATASETS if name != "history" or edition.is_full()}


# --- walking a spec --------------------------------------------------------------------------

def _walk(node, path: str):
    """(dict, its path) for every dict inside `node`, `node` itself first."""
    if isinstance(node, dict):
        yield node, path
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def data_keys(spec) -> list[str]:
    """Where `spec` holds a `data`, `url` or `datasets` key, as paths such as `spec.layer[0].data`."""
    return [f"{path}.{k}" for d, path in _walk(spec, "spec") for k in d if k in NO_DATA]


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _made_names(spec) -> tuple[set[str], bool]:
    """Field names the spec's transforms make (`as`, and the default names of fold, density and
    quantile), and whether it pivots (which makes names from the data, so they can't be known)."""
    made, pivots = set(), False
    for d, _ in _walk(spec, "spec"):
        made.update(_strings(d.get("as")))
        for key, names in _DEFAULT_AS.items():
            if key in d and "as" not in d:
                made.update(names)
        pivots = pivots or "pivot" in d
    return made, pivots


def _field_refs(spec) -> list[tuple[str, str]]:
    """(field name, where) for every field the spec names."""
    refs = []
    for d, path in _walk(spec, "spec"):
        for key in _FIELD_KEYS:
            if isinstance(d.get(key), str):
                refs.append((d[key], f"{path}.{key}"))
        for key in _FIELD_LIST_KEYS:
            if isinstance(d.get(key), list):
                refs += [(v, f"{path}.{key}") for v in _strings(d[key])]
        if isinstance(d.get("on"), str) and ("regression" in d or "loess" in d):
            refs.append((d["on"], f"{path}.on"))
        if isinstance(d.get("key"), str) and "impute" in d:
            refs.append((d["key"], f"{path}.key"))
        repeat = d.get("repeat")
        if isinstance(repeat, (list, dict)):
            lists = [repeat] if isinstance(repeat, list) else list(repeat.values())
            refs += [(v, f"{path}.repeat") for lst in lists for v in _strings(lst)]
        for key in _EXPR_KEYS:
            if isinstance(d.get(key), str):
                refs += [(m.group(1) or m.group(3), f"{path}.{key}") for m in _DATUM.finditer(d[key])]
    return [(name.replace("\\.", ".").strip(), where) for name, where in refs]


def _has_mark(spec) -> bool:
    return any(isinstance(d.get("mark"), (str, dict)) for d, _ in _walk(spec, "spec"))


# --- checking --------------------------------------------------------------------------------

def check_chart(chart, project: Project) -> list[str]:
    """Problems with `chart` as plain sentences; [] when it can be added."""
    if not isinstance(chart, dict):
        return ["A chart must be a JSON object with id, title, description, dataset and spec."]
    probs = [f"The chart has a key {k!r} that charts do not use; the keys are {', '.join(KEYS)}."
             for k in chart if k not in KEYS]
    cid = chart.get("id")
    if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
        probs.append("The chart's id must be lower-case letters, digits and dashes, such as "
                     f"'overall-by-country' (not {cid!r}).")
    if not isinstance(chart.get("title"), str) or not chart["title"].strip():
        probs.append("The chart needs a title: a few words saying what it shows.")
    if not isinstance(chart.get("description", ""), str):
        probs.append("The chart's description must be text (it may be empty).")
    dataset = chart.get("dataset")
    if dataset == "history" and edition.is_free():
        return probs + [edition.in_full("A chart of results across past editions")]
    if dataset not in DATASETS:
        probs.append(f"The chart's dataset must be one of {', '.join(DATASETS)}, not {dataset!r}.")
    spec = chart.get("spec")
    if not isinstance(spec, dict):
        return probs + ["The chart's spec must be a Vega-Lite spec (a JSON object)."]
    for where in data_keys(spec):
        probs.append(f"The spec holds {where.rsplit('.', 1)[1]!r} at {where}. A chart takes its data "
                     "only from the page, so leave out data, url and datasets everywhere.")
    if not _has_mark(spec):
        probs.append("The spec has no mark (such as \"mark\": \"bar\"), at the top or in a layer or "
                     "sub-chart.")
    if dataset in DATASETS:
        cols = columns(project, dataset)
        made, pivots = _made_names(spec)
        if not pivots:
            seen = set()
            for name, where in _field_refs(spec):
                if name in cols or name in made or name == "*" or (name, where) in seen:
                    continue
                seen.add((name, where))
                probs.append(f"{where}: {name!r} is not a column of the {dataset} dataset (its columns: "
                             f"{', '.join(cols)}).")
    return probs


def _path(project: Project, chart_id: str):
    return project.path.joinpath(*FOLDER, f"{chart_id}.json")


def add_chart(project: Project, chart) -> str:
    """Check `chart` and write it to `report/charts/<id>.json`. Returns "added", or "replaced" when
    a chart with that id was there. Raises HandbookError (and writes nothing) when it has problems."""
    probs = check_chart(chart, project)
    if probs:
        raise HandbookError("The chart was not added:\n- " + "\n- ".join(probs))
    path = _path(project, chart["id"])
    existed = path.is_file()
    write_json(path, {"id": chart["id"], "title": chart["title"].strip(),
                      "description": chart.get("description") or "", "dataset": chart["dataset"],
                      "spec": chart["spec"]})
    return "replaced" if existed else "added"


def remove_chart(project: Project, chart_id: str) -> None:
    path = _path(project, chart_id) if isinstance(chart_id, str) and ID_RE.fullmatch(chart_id) else None
    if path is None or not path.is_file():
        raise HandbookError(f"There is no chart {chart_id!r}. hb chart list shows the charts there are.")
    path.unlink()


def read_charts(project: Project) -> list[dict]:
    """Every chart file in `report/charts/`, in id order, with only the chart keys."""
    folder = project.path.joinpath(*FOLDER)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        chart = read_json(path)
        if isinstance(chart, dict):
            out.append({k: chart.get(k, "" if k == "description" else None) for k in KEYS})
    return out


def bundle_problems(charts) -> list[str]:
    """The build's check on the bundle's `results.charts`: a list of charts, each with an id, a
    known dataset and a spec that loads no data."""
    if not isinstance(charts, list):
        return ["results.charts must be a list"]
    probs = []
    for i, chart in enumerate(charts):
        at = f"results.charts[{i}]"
        if not isinstance(chart, dict) or not isinstance(chart.get("id"), str):
            probs.append(f"{at} must be a chart with an id")
            continue
        at = f"{at} ({chart['id']})"
        if chart.get("dataset") not in DATASETS:
            probs.append(f"{at}: dataset {chart.get('dataset')!r} is not one of {', '.join(DATASETS)}")
        if not isinstance(chart.get("spec"), dict):
            probs.append(f"{at}: the spec must be an object")
            continue
        for where in data_keys(chart["spec"]):
            probs.append(f"{at}: the spec holds {where}; a chart's data comes only from the page "
                         "(remove it with hb chart remove, or fix it and add it again)")
    return probs

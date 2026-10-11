"""The bundle contract: what the viewer relies on, plus the build's safety checks (no held-back
keys, no scorer's note or last-year material in cells, page under 16 MB, at most 500 data files).

    validate(bundle, page_size=None, data_files=None) -> list[str]     # [] when it holds
"""
from __future__ import annotations

from .bundle import CONTRACT
from .custom_charts import bundle_problems
from .folder import applies
from .shapes import CELL_STATES

MAX_PAGE_BYTES = 16 * 1000 * 1000        # the Artifact limit for the page and for each text file
MAX_DATA_FILES = 500

_DICT, _LIST, _STR = dict, list, str
TOP_KEYS = {"contract": _STR, "generated": _STR, "project": _DICT, "provided": _LIST, "needs": _LIST,
            "questions": _LIST, "scoresheet": (_DICT, type(None)), "units": _LIST, "rules": _LIST,
            "where_to_look": _DICT, "cells": _DICT, "totals": _DICT, "documents": _DICT,
            "found": _DICT, "runs": _LIST, "estimate": (_DICT, type(None)), "results": _DICT,
            "applied_edits": _LIST, "corrections": _LIST}
NEVER_ANYWHERE = ("held_back",)
NEVER_IN_CELLS = ("note", "last_year", "last_year_note", "unsure")


def _keys(obj, where: str):
    """(key, where) for every dict key inside `obj`, at any depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, where
            yield from _keys(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _keys(v, f"{where}[{i}]")


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def validate(bundle: dict, page_size: int | None = None, data_files: dict | None = None) -> list[str]:
    """Problems with `bundle`, as readable lines ([] when it holds). `page_size` is the built
    page in bytes; `data_files` maps each published data file's path to its size in bytes."""
    if not isinstance(bundle, dict):
        return ["the bundle must be a JSON object"]
    probs = []
    if bundle.get("contract") != CONTRACT:
        probs.append(f"contract must be {CONTRACT!r}, not {bundle.get('contract')!r}")
    for key, kind in TOP_KEYS.items():
        if key not in bundle:
            probs.append(f"{key} is missing")
        elif not isinstance(bundle[key], kind):
            probs.append(f"{key} has the wrong type ({type(bundle[key]).__name__})")
    if probs:
        return probs

    if "provided" in bundle["project"]:
        probs.append("project must not hold provided (file paths); the bundle lists names only")
    for key, where in _keys(bundle, "bundle"):
        if key in NEVER_ANYWHERE:
            probs.append(f"{where} holds the key {key!r}; held-back results reach the viewer only "
                         "through compare")
    for key, where in _keys(bundle["cells"], "cells"):
        if key in NEVER_IN_CELLS:
            probs.append(f"{where} holds the key {key!r}, which must not reach the viewer")

    units = {u.get("id"): u for u in bundle["units"] if isinstance(u, dict)}
    questions = {q.get("id"): q for q in bundle["questions"] if isinstance(q, dict)}
    documents = bundle["documents"]
    for row, cells in bundle["cells"].items():
        if row not in units:
            probs.append(f"cells: row {row!r} is not in units")
            continue
        if not isinstance(cells, dict):
            probs.append(f"cells.{row} must be an object")
            continue
        for qid, cell in cells.items():
            at = f"cells.{row}.{qid}"
            if qid not in questions:
                probs.append(f"{at}: question {qid!r} does not exist")
                continue
            if not applies(questions[qid], units[row]):
                probs.append(f"{at}: question {qid} does not apply to {row}, so it should be absent")
            if not isinstance(cell, dict) or cell.get("state") not in CELL_STATES:
                probs.append(f"{at}: state must be one of {', '.join(CELL_STATES)}")
                continue
            for i, ev in enumerate(cell.get("evidence") or []):
                if not isinstance(ev, dict):
                    probs.append(f"{at}.evidence[{i}] must be an object")
                elif ev.get("doc") not in documents and ev.get("missing") is not True:
                    probs.append(f"{at}.evidence[{i}]: document {ev.get('doc')!r} is not in documents "
                                 "and not marked missing")
    for row, unit in units.items():
        cells = bundle["cells"].get(row)
        if not isinstance(cells, dict):
            probs.append(f"cells: row {row!r} has no cells")
            continue
        for qid, q in questions.items():
            if applies(q, unit) and qid not in cells:
                probs.append(f"cells.{row}: question {qid} applies but has no cell")
        if row not in bundle["totals"]:
            probs.append(f"totals: row {row!r} is missing (null when not computable)")
    for row in bundle["totals"]:
        if row not in units:
            probs.append(f"totals: row {row!r} is not in units")
    for key in ("report", "facts", "compare"):
        if key not in bundle["results"]:
            probs.append(f"results.{key} is missing")
    if "charts" in bundle["results"]:
        probs += bundle_problems(bundle["results"]["charts"])

    if page_size is not None and page_size > MAX_PAGE_BYTES:
        probs.append(f"the page is {_mb(page_size)}, over the 16 MB limit")
    if data_files is not None:
        if len(data_files) > MAX_DATA_FILES:
            probs.append(f"{len(data_files)} data files, over the limit of {MAX_DATA_FILES}")
        for name, size in data_files.items():
            if size > MAX_PAGE_BYTES:
                probs.append(f"{name} is {_mb(size)}, over the 16 MB limit")
    return probs

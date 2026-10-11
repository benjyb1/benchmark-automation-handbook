"""How points become section scores, an overall score and bands, and the check that the score
sheet rebuilds last edition's published results.

`row_totals` scores one row from its answer cells. `rebuild_check` scores the held-back results
from their own finest level (question points, or section scores) and compares what it gets with
the keys the owner's results also give. It never reads `answers/`.
"""
from __future__ import annotations
from decimal import ROUND_HALF_UP, Decimal

from .folder import HandbookError, Project, applies, now_stamp, read_json
from .shapes import check_held_back

ROUNDING_STEPS = {"half_up_int": Decimal("1"), "half_up_1dp": Decimal("0.1")}
SCORED = ("answered", "no_evidence")


def round_half_up(x, mode: str):
    """`x` rounded by a score-sheet rounding mode: "none" keeps it, "half_up_int" gives a whole
    number, "half_up_1dp" one decimal place. Halves always go up (never banker's rounding), and
    float noise such as 72.49999999999999 is treated as the half it stands for."""
    if x is None:
        return None
    if mode in (None, "none"):
        return x
    if mode not in ROUNDING_STEPS:
        raise ValueError(f"unknown rounding {mode!r}; expected none, half_up_int or half_up_1dp")
    d = Decimal(repr(round(float(x), 9))).quantize(ROUNDING_STEPS[mode], rounding=ROUND_HALF_UP)
    return int(d) if mode == "half_up_int" else float(d)


def band_for(score, bands: list[dict]) -> str | None:
    """The name of the first band, in descending `min`, whose `min` is at or below `score`."""
    if score is None:
        return None
    for band in sorted(bands or [], key=lambda b: b.get("min", 0), reverse=True):
        if band.get("min") is not None and band["min"] <= score:
            return band.get("name")
    return None


# --- the calculation, shared by row_totals and rebuild_check ----------------------------------

def _top(question: dict):
    return max((lvl.get("points") or 0 for lvl in (question.get("answer") or {}).get("levels") or []),
               default=0)


def _level_points(question: dict, value):
    for lvl in (question.get("answer") or {}).get("levels") or []:
        if lvl.get("value") == value:
            return lvl.get("points")
    return None


def _section_score(section: dict, entry: dict):
    """The figure a section is published as: its percent, or its points when `percent` is false."""
    if section.get("percent", True):
        return entry["percent"]
    return round_half_up(entry["points"], section.get("rounding"))


def _sections(sheet: dict, questions: list[dict], unit: dict, points_for) -> tuple[dict, dict, bool]:
    """Section totals for `unit`. `points_for(question)` returns the points a question adds, or
    None when it adds nothing (not applicable), or "not_run" (adds nothing, marks the row partial).
    Returns (sections, unrounded percents, partial)."""
    out, raw, partial = {}, {}, False
    for section in sheet.get("sections") or []:
        out[section["id"]] = {"points": 0, "max": 0, "percent": None}
    for q in questions:
        entry = out.get(q.get("section"))
        if entry is None or not applies(q, unit):
            continue
        pts = points_for(q)
        if pts == "not_run":
            partial = True
            continue
        if pts is None:
            continue
        entry["points"] += pts
        entry["max"] += _top(q)
    for section in sheet.get("sections") or []:
        entry = out[section["id"]]
        if entry["max"]:
            raw[section["id"]] = entry["points"] / entry["max"] * 100
            entry["percent"] = round_half_up(raw[section["id"]], section.get("rounding"))
    return out, raw, partial


def _overall_from_sections(sheet: dict, sections: dict, raw: dict):
    """The overall score from section totals (`raw` holds the unrounded percents)."""
    overall = sheet.get("overall") or {}
    if overall.get("method") == "sum_points":
        points = sum(e["points"] for e in sections.values())
        top = sum(e["max"] for e in sections.values())
        if overall.get("percent"):
            if not top:
                return None
            return round_half_up(points / top * 100, overall.get("rounding"))
        return round_half_up(points, overall.get("rounding"))
    use_rounded = overall.get("use_rounded_sections") is True
    percents = {sid: (e["percent"] if use_rounded else raw.get(sid)) for sid, e in sections.items()}
    return _weighted_mean(overall, percents)


def _weighted_mean(overall: dict, percents: dict):
    """Weighted mean of the section percents that exist; a section with nothing available for the
    row drops out and the remaining weights are rescaled."""
    weights = overall.get("weights") or {}
    pairs = [(weights.get(sid, 0), p) for sid, p in percents.items() if p is not None]
    total_weight = sum(w for w, _ in pairs)
    if not pairs or not total_weight:
        return None
    return round_half_up(sum(w * p for w, p in pairs) / total_weight, overall.get("rounding"))


def _bands(sheet: dict, overall, section_scores: dict) -> dict:
    out = {}
    for group in sheet.get("bands") or []:
        of = group.get("of")
        score = overall if of == "overall" else section_scores.get(of)
        out[of] = band_for(score, group.get("bands"))
    return out


def _unit(project: Project, row):
    if isinstance(row, dict):
        return row
    unit = next((u for u in project.read("units").get("units", []) if u.get("id") == row), None)
    if unit is None:
        raise HandbookError(f"There is no unit {row!r} in units.json.")
    return unit


def row_totals(project: Project, row, cells: dict) -> dict | None:
    """Totals for one row from its answer cells (`{question id: cell}`).

    Returns `{"sections": {sid: {"points", "max", "percent"}}, "overall": number | None,
    "bands": {of: name | None}, "partial": bool}`, or None when the score sheet is not computable.
    `row` is a unit dict (or a unit id). `answered` and `no_evidence` cells add their points and the
    question's top points; `not_applicable` adds nothing; `not_run` (or a missing cell) adds
    nothing and makes the row partial. A section's `percent` is None when nothing in it applies.
    """
    sheet = project.read("scoresheet")
    if not sheet or sheet.get("computable") is False:
        return None
    unit = _unit(project, row)
    questions = project.read("questions").get("questions", [])

    def points_for(q):
        c = (cells or {}).get(q.get("id")) or {}
        state = c.get("state", "not_run")
        if state not in SCORED:
            return "not_run" if state == "not_run" else None
        if c.get("points") is not None:
            return c["points"]
        value = c.get("value") if state == "answered" else q.get("no_evidence_value")
        return _level_points(q, value) or 0

    sections, raw, partial = _sections(sheet, questions, unit, points_for)
    overall = _overall_from_sections(sheet, sections, raw)
    scores = {s["id"]: _section_score(s, sections[s["id"]]) for s in sheet.get("sections") or []}
    return {"sections": sections, "overall": overall, "bands": _bands(sheet, overall, scores),
            "partial": partial}


# --- the rebuild check ------------------------------------------------------------------------

def _decimals(n) -> int:
    if isinstance(n, int) or float(n).is_integer():
        return 0
    text = repr(float(n))
    return len(text.split(".")[1]) if "." in text and "e" not in text else 9


def _rounding_for(sheet: dict, key: str):
    """The score sheet's rounding mode for a published key: a section's own, or the overall's."""
    if key == "overall":
        return (sheet.get("overall") or {}).get("rounding")
    for section in sheet.get("sections") or []:
        if section.get("id") == key:
            return section.get("rounding")
    return None


def _same(ours, theirs, mode=None) -> bool:
    """Whether our figure matches the published one. `mode` is the score sheet's rounding for the
    key, so a figure is read at the precision the sheet publishes to, not at whatever the
    published number happens to show (87.0 is not 87.4 on a sheet that rounds to one decimal
    place). With no rounding to go by, the published number's own decimals set the precision."""
    if isinstance(theirs, str) or theirs is None or ours is None or isinstance(ours, str):
        return ours == theirs
    if mode in ROUNDING_STEPS:
        return round_half_up(ours, mode) == theirs
    places = _decimals(theirs)
    mode = "half_up_int" if places == 0 else ("half_up_1dp" if places == 1 else None)
    if mode:
        return round_half_up(ours, mode) == theirs
    return abs(ours - theirs) < 10 ** -places / 2


def _derive(sheet: dict, questions: list[dict], unit: dict, values: dict, level: str) -> dict:
    """Every key the score sheet can compute from a held-back row at its finest `level`."""
    section_list = sheet.get("sections") or []
    if level == "question":
        def points_for(q):
            return values.get(q.get("id"))      # held-back values are points; null adds nothing
        sections, raw, _ = _sections(sheet, questions, unit, points_for)
        scores = {s["id"]: _section_score(s, sections[s["id"]]) for s in section_list}
        overall = _overall_from_sections(sheet, sections, raw)
        return {**scores, "overall": overall,
                **{f"band:{of}": name for of, name in _bands(sheet, overall, scores).items()}}
    if level == "section":
        scores = {s["id"]: values.get(s["id"]) for s in section_list}
        overall_cfg = sheet.get("overall") or {}
        out = {}
        # The published sections are rounded. An overall built from the unrounded ones cannot be
        # rebuilt from them exactly, so it is not checked at this level.
        if overall_cfg.get("method") == "weighted_mean_of_section_percents" and \
                overall_cfg.get("use_rounded_sections") is True and \
                all(s.get("percent", True) for s in section_list):
            out["overall"] = _weighted_mean(overall_cfg, scores)
        elif overall_cfg.get("method") == "sum_points" and not overall_cfg.get("percent") and \
                not any(s.get("percent", True) for s in section_list) and \
                all(v is not None for v in scores.values()):
            out["overall"] = round_half_up(sum(scores.values()), overall_cfg.get("rounding"))
        overall = out.get("overall", values.get("overall"))
        for of, name in _bands(sheet, overall, scores).items():
            out[f"band:{of}"] = name
        return out
    if level == "total":
        return {f"band:{of}": name for of, name in _bands(sheet, values.get("overall"), {}).items()
                if of == "overall"}
    return {}


def rebuild_check(project: Project) -> dict:
    """Score last edition's held-back results with the score sheet and compare.

    Returns the rebuild.json shape: `{"checked", "matched", "mismatches": [{"row", "key", "ours",
    "theirs"}], "at"}`. A row is checked when it has at least one key the sheet can compute from
    its finest level; it matches when every such key agrees. Does not write rebuild.json.
    """
    results = read_json(project.held_back / "results.json")
    if not results:
        raise HandbookError(f"There are no held-back results at {project.held_back / 'results.json'}. "
                            "Run hold-back with last edition's results first.")
    problems = check_held_back(results)
    if problems:
        raise HandbookError("The held-back results.json has problems:\n- " + "\n- ".join(problems))
    sheet = project.read("scoresheet")
    if not sheet:
        raise HandbookError("There is no score sheet yet, so there is nothing to rebuild with.")
    if sheet.get("computable") is False:
        raise HandbookError("The score sheet is marked not computable, so the published results "
                            "cannot be rebuilt from it.")
    questions = project.read("questions").get("questions", [])
    units = {u.get("id"): u for u in project.read("units").get("units", [])}
    level = results.get("level")

    checked, matched, mismatches = 0, 0, []
    for row, values in (results.get("rows") or {}).items():
        # A row missing from this edition's units: a question applies when a value was published.
        unit = units.get(row) or {"id": row, "labels": {}}
        qs = questions if row in units else [dict(q, applies_to=None) for q in questions
                                             if values.get(q.get("id")) is not None]
        ours = _derive(sheet, qs, unit, values, level)
        keys = [k for k in ours if values.get(k) is not None]
        if not keys:
            continue
        checked += 1
        row_mismatches = [{"row": row, "key": k, "ours": ours[k], "theirs": values[k]}
                          for k in keys if not _same(ours[k], values[k], _rounding_for(sheet, k))]
        matched += not row_mismatches
        mismatches.extend(row_mismatches)
    return {"checked": checked, "matched": matched, "mismatches": mismatches, "at": now_stamp()}

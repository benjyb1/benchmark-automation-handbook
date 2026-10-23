"""How points become section scores, an overall score and bands, and the check that the score
sheet rebuilds last edition's published results.

`row_totals` scores one row from its answer cells; `all_totals` scores every row, ranks them when the
sheet asks, and works out once the ends of figures scored against all the rows. `rebuild_check` scores
the held-back results from their own finest level (question points, or section scores) and compares
what it gets with the keys the owner's results also give. It never reads `answers/`.

Beyond points per level (21 Oct 2026, the wider-benchmark tests):
- a figure counts when its answer has `points` (answers.py): RSF's 0 to 100 indicators, safety two
  thirds questionnaire and one third abuses. A blank figure (no evidence) never counts as 0: it adds
  nothing and the row is partial;
- a `relative` figure is scored between all the rows' lowest and highest figures (CCPI), so it counts
  only once every row it applies to is scored; `overall.rank` ranks the rows with complete totals;
- a section with `computable: false` is not worked out, and the others still are: the overall then
  needs every section that carries weight (a sheet is never all or nothing);
- a `bonus` section's points go on top of the total without raising its maximum, and `cap` on a
  section or on the overall is the most it can reach (HRC's flex points, the final score capped at 100).
"""
from __future__ import annotations
from decimal import ROUND_HALF_UP, Decimal

from . import answers, needs
from .folder import HandbookError, Project, applies, now_stamp, read_json
from .shapes import check_held_back

ROUNDING_STEPS = {"half_up_int": Decimal("1"), "half_up_1dp": Decimal("0.1"), "half_up_2dp": Decimal("0.01")}
SCORED = ("answered", "no_evidence")


def round_half_up(x, mode: str):
    """`x` rounded by a score-sheet rounding mode: "none" keeps it, "half_up_int" gives a whole
    number, "half_up_1dp" one decimal place, "half_up_2dp" two (ALI publishes 8.45). Halves always go up (never banker's rounding), and
    float noise such as 72.49999999999999 is treated as the half it stands for."""
    if x is None:
        return None
    if mode in (None, "none"):
        # No rounding, but float noise is not a figure: 7.949999999999999 is 7.95 (ALI, 14 Oct 2026).
        return round(x, 9) if isinstance(x, float) else x
    if mode not in ROUNDING_STEPS:
        raise ValueError(f"unknown rounding {mode!r}; expected none, half_up_int, half_up_1dp or half_up_2dp")
    d = Decimal(repr(round(float(x), 9))).quantize(ROUNDING_STEPS[mode], rounding=ROUND_HALF_UP)
    return int(d) if mode == "half_up_int" else float(d)


def band_for(score, bands: list[dict], full=None) -> str | None:
    """The name of the first band, in descending `min`, whose `min` is at or below `score` and whose
    `requires_full` sections are all fully met (`full`: the ids of the row's fully met sections; None
    when not known, and then a band group with conditions gives no band). ACES (14 Oct 2026): Gold
    needs 80% of the advanced points AND every mandatory criterion met."""
    if score is None:
        return None
    ordered = sorted(bands or [], key=lambda b: b.get("min", 0), reverse=True)
    if full is None and any(b.get("requires_full") for b in ordered):
        return None
    for band in ordered:
        if band.get("min") is not None and band["min"] <= score \
                and all(s in full for s in band.get("requires_full") or []):
            return band.get("name")
    return None


# --- the calculation, shared by row_totals and rebuild_check ----------------------------------

def _top(question: dict):
    return answers.top_points(question)


def _level_points(question: dict, value):
    points = answers.points_for(question, value)
    return None if points is answers.MISSING else points


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _capped(x, cap):
    return min(x, cap) if _num(cap) and _num(x) else x


def _flagged(sheet: dict, key: str) -> set:
    """The ids of the sections where `key` is true (`bonus`), or false for `computable`."""
    want = False if key == "computable" else True
    return {sec.get("id") for sec in sheet.get("sections") or [] if isinstance(sec, dict) and sec.get(key) is want}


def _figure_points(q: dict, value, scales: dict | None):
    """The points a counting figure question adds for `value`: a level's points for a text answer (NR),
    the figure's place between its ends otherwise; "not_run" when it cannot be worked out yet (a blank,
    or a relative figure before every row is scored)."""
    if value is None:
        return "not_run"
    if answers.level_of(q, value) is not None:
        return _level_points(q, value) or 0
    scale = (scales or {}).get(q.get("id")) if answers.relative(q) else None
    pts = answers.figure_points(q, value, scale)
    return "not_run" if pts is None else pts


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
    skip = _flagged(sheet, "computable")
    for section in sheet.get("sections") or []:
        out[section["id"]] = {"points": 0, "max": 0, "percent": None}
    for q in questions:
        entry = out.get(q.get("section"))
        if entry is None or not applies(q, unit) or q.get("section") in skip:
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
        if section["id"] in skip:                    # not worked out: its scheme does not fit
            entry.update(points=None, max=None)
            continue
        if _num(section.get("cap")):                 # the most the section can reach (HRC's Part I: 30)
            entry["points"], entry["max"] = min(entry["points"], section["cap"]), min(entry["max"], section["cap"])
        if entry["max"]:
            raw[section["id"]] = entry["points"] / entry["max"] * 100
            entry["percent"] = round_half_up(raw[section["id"]], section.get("rounding"))
    return out, raw, partial


def _overall_from_sections(sheet: dict, sections: dict, raw: dict):
    """The overall score from section totals (`raw` holds the unrounded percents)."""
    overall = sheet.get("overall") or {}
    skip, bonus = _flagged(sheet, "computable"), _flagged(sheet, "bonus")
    if overall.get("method") == "sum_points":
        if skip:
            return None                               # a section that is not worked out is part of the sum
        points = sum(e["points"] for e in sections.values())
        top = sum(e["max"] for sid, e in sections.items() if sid not in bonus)
        if overall.get("percent"):
            if not top:
                return None
            return round_half_up(_capped(points / top * 100, overall.get("cap")), overall.get("rounding"))
        return round_half_up(_capped(points, overall.get("cap")), overall.get("rounding"))
    weights = overall.get("weights") or {}
    if any(_num(weights.get(sid)) and weights[sid] > 0 for sid in skip):
        return None
    use_rounded = overall.get("use_rounded_sections") is True
    percents = {sid: (e["percent"] if use_rounded else raw.get(sid)) for sid, e in sections.items() if sid not in skip}
    return _weighted_mean(overall, percents, bonus)


def _weighted_mean(overall: dict, percents: dict, bonus=frozenset()):
    """Weighted mean of the section percents that exist; a section with nothing available for the
    row drops out and the remaining weights are rescaled. A bonus section's weighted percent is added
    on top, outside the weights it is divided by; then the overall's cap applies."""
    weights = overall.get("weights") or {}
    pairs = [(weights.get(sid, 0), p) for sid, p in percents.items() if p is not None and sid not in bonus]
    total_weight = sum(w for w, _ in pairs)
    if not pairs or not total_weight:
        return None
    extra = sum(weights.get(sid, 0) * p for sid, p in percents.items() if p is not None and sid in bonus)
    value = (sum(w * p for w, p in pairs) + extra) / total_weight
    return round_half_up(_capped(value, overall.get("cap")), overall.get("rounding"))


def _full(sections: dict) -> set:
    """The sections a row meets in full: every applicable question at its top points. A section where
    nothing applies is not met (ACES re-test, 15 Oct 2026: all 20 conservation criteria not applicable
    turned every Gold camp into Conservation)."""
    return {sid for sid, e in sections.items() if _num(e["max"]) and e["max"] > 0 and e["points"] >= e["max"]}


def _bands(sheet: dict, overall, section_scores: dict, full=None) -> dict:
    out = {}
    for group in sheet.get("bands") or []:
        of = group.get("of")
        score = overall if of == "overall" else section_scores.get(of)
        out[of] = band_for(score, group.get("bands"), full)
    return out


def _unit(project: Project, row):
    if isinstance(row, dict):
        return row
    unit = next((u for u in project.read("units").get("units", []) if u.get("id") == row), None)
    if unit is None:
        raise HandbookError(f"There is no unit {row!r} in units.json.")
    return unit


def scales(project: Project, cells_by_row: dict | None = None) -> dict:
    """{question id: (worst, best) or None} for every relative figure question: its ends among all the
    rows it applies to, None until every one of them is scored (an answer waiting for the owner's data
    is not scored). `cells_by_row` ({row: cells}) saves reading answers/ again."""
    qs = [q for q in project.read("questions").get("questions", []) if isinstance(q, dict) and answers.relative(q)]
    if not qs:
        return {}
    units = [u for u in project.read("units").get("units", []) if isinstance(u, dict)]
    if cells_by_row is None:
        cells_by_row = {u.get("id"): project.answers(u.get("id")).get("cells") or {} for u in units}
    held = needs.waiting(project)
    out = {}
    for q in qs:
        figures, complete = [], True
        for u in units:
            if not applies(q, u):
                continue
            c = (cells_by_row.get(u.get("id")) or {}).get(q.get("id")) or {}
            if q.get("id") in (held.get(u.get("id")) or {}) or c.get("state") not in SCORED + ("not_applicable",):
                complete = False
                break
            if c.get("state") == "answered" and _num(c.get("value")) and answers.level_of(q, c["value"]) is None:
                figures.append(c["value"])
        out[q.get("id")] = answers.ends(q, figures) if complete else None
    return out


def all_totals(project: Project) -> dict:
    """{row: row_totals} for every row, with the ends of relative figures worked out once, and each
    complete row's `rank` (1 is the highest overall; ties share a rank) when the sheet's overall has
    `rank` true."""
    units = [u for u in project.read("units").get("units", []) if isinstance(u, dict)]
    cells_by_row = {u.get("id"): project.answers(u.get("id")).get("cells") or {} for u in units}
    ends = scales(project, cells_by_row)
    out = {u.get("id"): row_totals(project, u, cells_by_row[u.get("id")], ends) for u in units}
    sheet = project.read("scoresheet") or {}
    if (sheet.get("overall") or {}).get("rank") is True:
        ranked = sorted((t["overall"] for t in out.values() if t and not t["partial"] and _num(t["overall"])), reverse=True)
        for t in out.values():
            if t and not t["partial"] and _num(t["overall"]):
                t["rank"] = ranked.index(t["overall"]) + 1
    return out


def row_totals(project: Project, row, cells: dict, scales_given: dict | None = None) -> dict | None:
    """Totals for one row from its answer cells (`{question id: cell}`).

    Returns `{"sections": {sid: {"points", "max", "percent"}}, "overall": number | None,
    "bands": {of: name | None}, "partial": bool}`, or None when the score sheet is not computable.
    `row` is a unit dict (or a unit id). `answered` and `no_evidence` cells add their points and the
    question's top points; `not_applicable` adds nothing; `not_run` (or a missing cell) adds
    nothing and makes the row partial, and a partial row gets no band (its `overall` is what the
    scored answers give so far, never shown as complete). A section's `percent` is None when nothing in it applies.
    An answer that waits for the owner's data (needs.py) counts as not run, never as no evidence,
    and the needs it waits for are listed under `waiting`. A counting figure is worth its place
    between its ends (answers.figure_points; `scales_given` holds a relative figure's, from `scales`),
    and a blank one adds nothing and makes the row partial. Sections the sheet does not work out are
    listed under `not_computed`.
    """
    sheet = project.read("scoresheet")
    if not sheet or sheet.get("computable") is False:
        return None
    unit = _unit(project, row)
    questions = project.read("questions").get("questions", [])
    held = needs.waiting_in_row(project, unit.get("id"), cells)
    if scales_given is None and any(answers.relative(q) for q in questions if isinstance(q, dict)):
        scales_given = scales(project)

    def points_for(q):
        if q.get("id") in held:
            return "not_run"
        c = (cells or {}).get(q.get("id")) or {}
        state = c.get("state", "not_run")
        if state not in SCORED:
            return "not_run" if state == "not_run" else None
        if answers.counts(q):                      # a figure that counts: never a blank as 0
            return _figure_points(q, c.get("value") if state == "answered" else None, scales_given)
        if c.get("points") is not None:
            return c["points"]
        value = c.get("value") if state == "answered" else q.get("no_evidence_value")
        return _level_points(q, value) or 0

    sections, raw, partial = _sections(sheet, questions, unit, points_for)
    overall = _overall_from_sections(sheet, sections, raw)
    scores = {s["id"]: _section_score(s, sections[s["id"]]) for s in sheet.get("sections") or []}
    bands = _bands(sheet, overall, scores, None if partial else _full(sections))
    if partial:                                    # a partial total gets no band until every answer is in (22 Oct 2026)
        bands = dict.fromkeys(bands)
    out = {"sections": sections, "overall": overall, "bands": bands, "partial": partial}
    waits = sorted({need for q in questions if q.get("id") in held and applies(q, unit) for need in [held[q.get("id")]]})
    if waits:
        out["waiting"] = waits
    skipped = [sec.get("id") for sec in sheet.get("sections") or [] if sec.get("computable") is False]
    if skipped:
        out["not_computed"] = skipped
    return out


# --- the rebuild check ------------------------------------------------------------------------

def _decimals(n) -> int:
    if isinstance(n, int) or float(n).is_integer():
        return 0
    text = repr(float(n))
    return len(text.split(".")[1]) if "." in text and "e" not in text else 9


def _rounding_for(sheet: dict, key: str):
    """The score sheet's rounding mode for a published key: a section's own, or the overall's."""
    if key in ("overall", "final"):
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
    mode = {0: "half_up_int", 1: "half_up_1dp", 2: "half_up_2dp"}.get(places)
    if mode:
        return round_half_up(ours, mode) == theirs
    return abs(ours - theirs) < 10 ** -places / 2


def _standard(sheet: dict, sections: dict, raw: dict, bonus) -> float | None:
    """The overall without the bonus sections' points: the standard part HRC publishes as its total score."""
    return _overall_from_sections(sheet, {sid: e for sid, e in sections.items() if sid not in bonus},
                                  {sid: v for sid, v in raw.items() if sid not in bonus})


def _like_with_like(out: dict, standard) -> dict:
    """With a bonus section the score sheet's overall is the final score (standard plus bonus, capped). Last
    edition's `overall` holds the standard part and `final`, when given, the final score (HRC's MEI, 22 Oct
    2026: the final score against the published standard total gave a false mismatch for every row)."""
    if out.get("overall") is not None:
        out["final"] = out["overall"]
        out["overall"] = standard
    return out


def _derive(sheet: dict, questions: list[dict], unit: dict, values: dict, level: str, ends: dict | None = None) -> dict:
    """Every key the score sheet can compute from a held-back row at its finest `level`. `ends` holds
    the relative figures' ends among the held-back rows. With a bonus section, `overall` is the standard
    part and `final` the final score (_like_with_like)."""
    section_list = sheet.get("sections") or []
    skip, bonus = _flagged(sheet, "computable"), _flagged(sheet, "bonus")
    if level == "question":
        def points_for(q):
            # Held-back values are points; null adds nothing. A number question's value is the figure
            # itself, which adds no points unless the figure counts (answers.py).
            value = values.get(q.get("id"))
            if answers.counts(q) and value is not None:
                return _figure_points(q, value, ends)
            if answers.is_number(q) and value is not None:
                return _level_points(q, value) or 0
            if isinstance(value, str):             # a level held as text: worth its level's points
                return _level_points(q, value)
            return value
        sections, raw, _ = _sections(sheet, questions, unit, points_for)
        scores = {s["id"]: _section_score(s, sections[s["id"]]) for s in section_list if s["id"] not in skip}
        overall = _overall_from_sections(sheet, sections, raw)
        out = {**scores, **({"overall": overall} if overall is not None or not skip else {})}
        bands = {f"band:{of}": name for of, name in _bands(sheet, overall, scores, _full(sections)).items() if of in out}
        if bonus:
            _like_with_like(out, _standard(sheet, sections, raw, bonus))
        return {**out, **bands}
    if level == "section":
        scores = {s["id"]: values.get(s["id"]) for s in section_list}
        overall_cfg = sheet.get("overall") or {}
        out = {}
        # The published sections are rounded. An overall built from the unrounded ones cannot be
        # rebuilt from them exactly, so it is not checked at this level.
        standard = None
        if overall_cfg.get("method") == "weighted_mean_of_section_percents" and \
                overall_cfg.get("use_rounded_sections") is True and \
                all(s.get("percent", True) for s in section_list):
            out["overall"] = _weighted_mean(overall_cfg, scores, bonus)
            standard = _weighted_mean(overall_cfg, {k: v for k, v in scores.items() if k not in bonus}, bonus)
        elif overall_cfg.get("method") == "sum_points" and not overall_cfg.get("percent") and \
                not any(s.get("percent", True) for s in section_list) and \
                all(v is not None for v in scores.values()):
            out["overall"] = round_half_up(_capped(sum(scores.values()), overall_cfg.get("cap")), overall_cfg.get("rounding"))
            standard = round_half_up(_capped(sum(v for k, v in scores.items() if k not in bonus), overall_cfg.get("cap")),
                                     overall_cfg.get("rounding"))
        overall = out.get("overall", values.get("final") if bonus and values.get("final") is not None else values.get("overall"))
        if bonus:
            _like_with_like(out, standard)
        full = {s["id"] for s in section_list if s.get("percent", True) and scores.get(s["id"]) == 100} \
            if all(s.get("percent", True) and scores.get(s["id"]) is not None for s in section_list) else None
        for of, name in _bands(sheet, overall, scores, full).items():
            out[f"band:{of}"] = name
        return out
    if level == "total":
        overall = values.get("final") if bonus and values.get("final") is not None else values.get("overall")
        return {f"band:{of}": name for of, name in _bands(sheet, overall, {}).items() if of == "overall"}
    return {}


def rebuild_check(project: Project) -> dict:
    """Score last edition's held-back results with the score sheet and compare.

    Returns the rebuild.json shape: `{"checked", "matched", "mismatches": [{"row", "key", "ours",
    "theirs"}], "only_total": [rows], "at"}`. A row is checked when it has at least one key the sheet can
    compute from its finest level; it matches when every such key agrees. A row whose results give
    nothing finer than a total (no question or section figure at that level) is listed under
    `only_total` instead. Does not write rebuild.json.
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
    # A relative figure's ends among the published rows' figures.
    ends = {q.get("id"): answers.ends(q, [(v or {}).get(q.get("id")) for v in (results.get("rows") or {}).values()])
            for q in questions if isinstance(q, dict) and answers.relative(q)}

    finer = ({q.get("id") for q in questions if isinstance(q, dict)} if level == "question"
             else {sec.get("id") for sec in sheet.get("sections") or [] if isinstance(sec, dict)} if level == "section"
             else None)
    checked, matched, mismatches, only_total = 0, 0, [], []
    for row, values in (results.get("rows") or {}).items():
        if finer is not None and not any(values.get(k) is not None for k in finer):
            # Only a total was published for this row: nothing finer to rebuild it from (KnowTheChain, 21 Oct
            # 2026: 43 such companies read as 43 mismatches).
            only_total.append(row)
            continue
        # A row missing from this edition's units: a question applies when a value was published.
        unit = units.get(row) or {"id": row, "labels": {}}
        qs = questions if row in units else [dict(q, applies_to=None) for q in questions
                                             if values.get(q.get("id")) is not None]
        ours = _derive(sheet, qs, unit, values, level, ends)
        keys = [k for k in ours if values.get(k) is not None]
        if not keys:
            continue
        checked += 1
        row_mismatches = [{"row": row, "key": k, "ours": ours[k], "theirs": values[k]}
                          for k in keys if not _same(ours[k], values[k], _rounding_for(sheet, k))]
        matched += not row_mismatches
        mismatches.extend(row_mismatches)
    out = {"checked": checked, "matched": matched, "mismatches": mismatches, "only_total": only_total, "at": now_stamp()}
    bonus = [s.get("name") or s.get("id") for s in sheet.get("sections") or [] if isinstance(s, dict) and s.get("bonus") is True]
    if bonus and checked and any((v or {}).get("final") is None for v in (results.get("rows") or {}).values()):
        out["note"] = (f"The score sheet adds {' and '.join(str(b) for b in bonus)} on top of the total, and last edition's "
                       "results give no final score for some rows (key final), so their published overall is read as the "
                       "standard part only, without the bonus, and checked against ours without it.")
    return out

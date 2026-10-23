"""What a question accepts as an answer, and what each answer is worth: one place for every module.

A question's `answer` is one of three kinds:

- `yes_no`: exactly the levels "yes" and "no".
- `levels`: whole-number levels, each with points and a meaning (a grade A to G is levels 7 to 1, with
  the letter in the meaning).
- `number`: a figure, such as the share of eggs that are cage-free. It has `unit` (shown after the
  figure: "%", "hens", "" for none), optional `min` and `max`, and optional `decimals` (how many places
  the owner publishes; `null` for any). Its `levels` hold the answers that are not figures, each with
  points and a meaning, for example {"value": "NR", "points": 0, "meaning": "Commitment, no progress
  published"}; they may be empty. A figure adds no points: a number question is tracked, not totalled,
  unless the owner's scheme turns ranges into levels instead.

Built 14 Oct 2026 for CIWF EggTrack, whose answers are percentages beside NR and NC.

A figure counts in the totals when its answer has `points` (21 Oct 2026, RSF and CCPI): it is then worth
`points` x (figure - min) / (max - min), or (max - figure) / (max - min) with `lower_is_better`. With
`relative`, the ends are the lowest and highest figures among all the rows instead (`best` and `worst`
fix either end), so the points are known only once every row is scored (scoresheet.py works the ends out).
"""
from __future__ import annotations

MISSING = object()


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def answer_of(question) -> dict:
    a = question.get("answer") if isinstance(question, dict) else None
    return a if isinstance(a, dict) else {}


def levels(question) -> list[dict]:
    return [lv for lv in answer_of(question).get("levels") or [] if isinstance(lv, dict)]


def is_number(question) -> bool:
    return answer_of(question).get("kind") == "number"


def level_of(question, value) -> dict | None:
    """The level dict for `value` (same value and type), or None. A figure has no level."""
    for lv in levels(question):
        v = lv.get("value")
        if v == value and (type(v) is type(value) or (_num(v) and _num(value))):
            return lv
    return None


def figure_ok(question, value) -> bool:
    """Whether `value` is a figure this number question accepts (inside its min and max)."""
    if not (is_number(question) and _num(value)):
        return False
    a = answer_of(question)
    lo, hi = a.get("min"), a.get("max")
    return (not _num(lo) or value >= lo) and (not _num(hi) or value <= hi)


def accepts(question, value) -> bool:
    return level_of(question, value) is not None or figure_ok(question, value)


def points_for(question, value):
    """The points `value` is worth: its level's points, 0 for an accepted figure, MISSING otherwise."""
    lv = level_of(question, value)
    if lv is not None:
        return lv.get("points")
    if figure_ok(question, value):
        return 0
    return MISSING


def counts(question) -> bool:
    """Whether a number question's figures count in the totals (its answer has `points`)."""
    return is_number(question) and _num(answer_of(question).get("points")) and answer_of(question)["points"] > 0


def relative(question) -> bool:
    """Whether a counting figure is scored against all the rows' figures."""
    return counts(question) and answer_of(question).get("relative") is True


def ends(question, figures=()) -> tuple | None:
    """(worst, best) for a counting figure: its min and max (the other way round with
    lower_is_better), or for a relative one the rows' `figures`, with `best` or `worst` fixed when the
    method fixes them. None when there is nothing to scale by."""
    a = answer_of(question)
    lower = a.get("lower_is_better") is True
    if relative(question):
        vals = [v for v in figures if _num(v)]
        lo, hi = (min(vals), max(vals)) if vals else (None, None)
        worst, best = (hi, lo) if lower else (lo, hi)
        worst = a["worst"] if _num(a.get("worst")) else worst
        best = a["best"] if _num(a.get("best")) else best
    else:
        lo, hi = a.get("min"), a.get("max")
        worst, best = (hi, lo) if lower else (lo, hi)
    return (worst, best) if _num(worst) and _num(best) else None


def figure_points(question, value, scale=None):
    """The points a figure earns in the totals: its place between the worst and best ends (`scale`,
    from `ends`; a relative figure needs it), times the answer's `points`, kept within 0 and those
    points. When every row has the same figure, each earns the full points. None when it cannot be
    worked out."""
    if not (counts(question) and _num(value)):
        return None
    scale = scale if scale is not None else (None if relative(question) else ends(question))
    if scale is None:
        return None
    worst, best = scale
    top = answer_of(question)["points"]
    if best == worst:
        return top
    share = (value - worst) / (best - worst)
    return top * min(1.0, max(0.0, share))


def top_points(question):
    tops = [lv.get("points") or 0 for lv in levels(question) if _num(lv.get("points"))]
    if counts(question):
        tops.append(answer_of(question)["points"])
    return max(tops, default=0)


def show_figure(question, value) -> str:
    """A figure with its unit, as the owner writes it: 73%, 1,200 hens."""
    unit = str(answer_of(question).get("unit") or "")
    text = f"{value:,.10g}" if _num(value) else str(value)
    if not unit:
        return text
    return f"{text}{unit}" if unit in ("%", "‰") else f"{text} {unit}"


def describe(question) -> str:
    """What the question accepts, for a problem message or a scorer's brief: 'yes', 'no' for yes_no and
    levels; for a number, 'a figure from 0 to 100 (%)' and any other answers it has."""
    vals = ", ".join(repr(lv.get("value")) for lv in levels(question))
    if not is_number(question):
        return vals
    a = answer_of(question)
    lo, hi, unit = a.get("min"), a.get("max"), a.get("unit")
    if _num(lo) and _num(hi):
        span = f"a figure from {lo:g} to {hi:g}"
    elif _num(lo):
        span = f"a figure of at least {lo:g}"
    elif _num(hi):
        span = f"a figure of at most {hi:g}"
    else:
        span = "a figure"
    span += f" ({unit})" if unit else ""
    return f"{span}, or one of {vals}" if vals else span


def same_answer(question, a, b) -> bool:
    """Whether two answers agree: figures to the question's published decimals, everything else exactly."""
    if is_number(question) and _num(a) and _num(b):
        d = answer_of(question).get("decimals")
        tol = 0.5 * 10 ** -d if isinstance(d, int) and not isinstance(d, bool) and d >= 0 else 1e-9
        return abs(a - b) <= tol + 1e-12
    return a == b and type(a) is type(b)

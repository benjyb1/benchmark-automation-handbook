"""What the page needs for its Generate report button (the Results tab, views/reportdraft.js):
whether the report is missing or out of date, the figures to draft from and the report guide.

    drafting(project) -> {"state": "none" | "current" | "out_of_date",
                          "facts": facts.compute(project) now, or None when they can't be worked out,
                          "guide": the rules of skills/results/report.md, or None}

The figures are worked out at build time rather than read from report/facts.json, so a draft
made in the page matches the folder as built. When the host adopts a draft it runs `facts` first,
which writes the same figures, then `check-report`. The report is out of date when facts.json is
missing or differs from the figures now, or when the report states a number they do not hold.
"""
from __future__ import annotations
from pathlib import Path

from .facts import compute, fact_numbers, unbacked
from .folder import HandbookError, Project, read_json

GUIDE = Path(__file__).resolve().parents[2] / "skills" / "results" / "report.md"
# Sections of the guide about files and commands, which mean nothing to a draft made in the page.
LEFT_OUT = ("What to read", "Before you run the check")


def guide(path: Path = GUIDE) -> str | None:
    """The report guide without the sections in LEFT_OUT; None when the file is not there."""
    if not path.is_file():
        return None
    kept, keep = [], True
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            keep = line[3:].strip() not in LEFT_OUT
        if keep:
            kept.append(line)
    return "\n".join(kept).strip() + "\n"


def _state(project: Project, facts: dict | None) -> str:
    path = project.path / "report" / "report.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if not text.strip():
        return "none"
    try:
        stored = read_json(project.file("report/facts"))
    except HandbookError:
        stored = None
    if facts is None or not stored or stored != facts:
        return "out_of_date"
    questions = [q.get("id") for q in project.read("questions").get("questions") or [] if isinstance(q, dict)]
    return "out_of_date" if unbacked(text, fact_numbers(facts), questions) else "current"


def drafting(project: Project) -> dict:
    try:
        facts = compute(project)
    except Exception:  # noqa: BLE001  the page can do without; a folder in set-up may not compute yet
        facts = None
    return {"state": _state(project, facts), "facts": facts, "guide": guide()}

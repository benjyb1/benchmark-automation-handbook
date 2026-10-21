"""The one place that says which Claude the tool runs, for every `claude -p` it starts.

Named in full, never an alias: an older Claude Code maps an alias, or no --model at all, to an older
model without saying so. Opus 5.5 at its default effort scored the Pecking Order gate rows closest to
WAP's published scores (0.7 points off on average; low effort 3.7, Sonnet 5.5 10.3, Haiku 5.5 11.7;
16 Oct 2026), and Claude Code before 2.1.280 does not know this id.
"""
from __future__ import annotations
import re

MODEL = "claude-opus-5-5"
# Finders search and save; they never score, so they run on the cheaper model (host/claude.md).
FINDER_MODEL = "claude-sonnet-5-5"
# One read (oneread.py): a kit whose questions are all lookups (a value read off a record, list or table)
# goes to Haiku 5.5, which matched Opus 5.5 on LCV votes and CTIS checks (21 Oct 2026) at about a fiftieth
# of the price; anything else is read by MODEL at this effort (Pecking Order: high 1.0 and 1.7 points off
# WAP's published overall, default 2.0, low 3.0).
LOOKUP_MODEL = "claude-haiku-5-5"
ONE_READ_EFFORT = "high"
MIN_VERSION = (2, 1, 280)


def version_of(text: str) -> tuple[int, ...] | None:
    """(2, 1, 301) from `claude --version` output such as "2.1.301 (Claude Code)"; None if none is there."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def too_old(text: str) -> bool:
    """Whether that version is older than MIN_VERSION (an unreadable version is not called too old)."""
    v = version_of(text)
    return v is not None and v < MIN_VERSION

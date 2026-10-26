"""What the page found wrong with its own text (views/layoutcheck.js): a word broken across lines,
text cut off with no full version on hover, text running out of its box.

The page scans each tab after it is drawn and POSTs what it found to `/api/layout`; `record` keeps
the latest scan of each tab in `page-data/layout.json`, so a problem that has been fixed drops off
the next time that tab is drawn. Both chats read the list (GET /api/layout) and fix the ones that
are the project's own words, such as a section name too long for its columns. Problems in the page's
own wording (no `source`) only a tool update can change; the release check looks for those.

    {"views": {"grid": {"seen": "...", "width": 1440, "problems": [{kind, text, view, source, key}]}}}
"""
from __future__ import annotations

from datetime import datetime, timezone

from .folder import read_json, write_json

KINDS = ("broken_word", "cut_off", "spills")
SOURCE_KINDS = ("section", "question", "unit", "label", "project", "band")
MAX_PER_VIEW = 50


def path(project):
    return project.path / "page-data" / "layout.json"


def _clean_source(src):
    if not isinstance(src, dict) or src.get("kind") not in SOURCE_KINDS:
        return None
    return {"kind": src["kind"], "id": None if src.get("id") is None else str(src["id"])[:120],
            "field": str(src.get("field") or "")[:40]}


def _clean(p, view: str):
    if not isinstance(p, dict) or p.get("kind") not in KINDS:
        return None
    text = " ".join(str(p.get("text") or "").split())[:120]
    if not text:
        return None
    return {"kind": p["kind"], "text": text, "view": view, "source": _clean_source(p.get("source")),
            "key": f"{p['kind']}|{view}|{text}", "el": str(p.get("el") or "")[:160]}


def record(project, body) -> dict:
    """Keep one tab's latest scan. `body` is {view, width, problems}; returns {ok, problems: n}."""
    if not isinstance(body, dict):
        return {"ok": False, "message": "Send {view, width, problems}."}
    view = " ".join(str(body.get("view") or "").split())[:40] or "start"
    found = [c for c in (_clean(p, view) for p in (body.get("problems") or [])[:MAX_PER_VIEW]) if c]
    data = read_json(path(project), {}) or {}
    views = data.get("views") if isinstance(data.get("views"), dict) else {}
    if found:
        width = body.get("width")
        views[view] = {"seen": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "width": width if isinstance(width, int) else None, "problems": found}
    else:
        views.pop(view, None)
    path(project).parent.mkdir(parents=True, exist_ok=True)
    write_json(path(project), {"views": views})
    return {"ok": True, "problems": len(found)}


def problems(project, own_words_only: bool = False) -> list[dict]:
    """Every tab's latest problems; with own_words_only, only those in the project's own words."""
    data = read_json(path(project), {}) or {}
    out = []
    for entry in (data.get("views") or {}).values():
        for p in (entry or {}).get("problems") or []:
            if not own_words_only or p.get("source"):
                out.append(p)
    return out

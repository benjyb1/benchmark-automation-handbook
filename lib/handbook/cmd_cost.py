"""hb cost [--rows R1,R2 | --all-rows] [--questions Q1,Q2] [--json]: what Claude has used on this
project so far, in tokens of the owner's Claude plan, and with --rows or --all-rows a rough range
for scoring those rows (on those questions, or every question that applies). See costs.py."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .costs import estimate_rows, summary
from .estimate import show_tokens

KIND_WORDS = {"trial": "trial runs", "full": "full runs", "rerun": "re-runs and re-reads", "setup": "set-up drafts",
              "chat": "the corner chat"}


def main(argv) -> int:
    parser = command_parser("cost", "What Claude has used on this project, and a rough range for scoring rows.")
    parser.add_argument("--rows", default=None, help="rows to estimate, comma-separated")
    parser.add_argument("--all-rows", action="store_true", help="estimate scoring every row")
    parser.add_argument("--questions", default=None, help="questions to estimate, comma-separated (default: all that apply)")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    s = summary(project)
    rows = ([u["id"] for u in project.read("units").get("units", []) if isinstance(u, dict) and u.get("id")]
            if args.all_rows else [r for r in (args.rows or "").split(",") if r])
    qs = [q for q in (args.questions or "").split(",") if q] or None
    est = estimate_rows(project, rows, qs, finding=True) if rows else None
    if args.json:
        print(json.dumps({"used": s["used"], "model": s["model"], "estimate": est}, ensure_ascii=False))
        return 0
    used = s["used"]
    if used["total"]:
        print(f"Used so far: {show_tokens(used['total'])} tokens of your Claude plan.")
        for kind, n in used["by_kind"].items():
            print(f"- {KIND_WORDS.get(kind, kind)}: {show_tokens(n)}")
    else:
        print("No Claude use is recorded for this project yet.")
    if est:
        m = s["model"]
        basis = (f"scaled to the {m['rows']} {'row' if m['rows'] == 1 else 'rows'} already scored here" if m["basis"] == "project"
                 else f"from {m['fit_rows']} rows scored in other benchmarks, by how long each row's documents are")
        f = est.get("finding") or {"rows": 0, "low": 0, "high": 0}
        score = (est["low"] - f["low"], est["high"] - f["high"])
        print(f"Scoring {est['rows']} {'row' if est['rows'] == 1 else 'rows'} ({est['cells']} answers): about "
              f"{show_tokens(score[0])} to {show_tokens(score[1])} tokens, {basis}.")
        if f["rows"]:
            print(f"Finding the documents of the {f['rows']} {'row' if f['rows'] == 1 else 'rows'} not searched yet: about "
                  f"{show_tokens(f['low'])} to {show_tokens(f['high'])} tokens more, "
                  + ("from the rows already searched here." if (est.get("find") or {}).get("basis") == "project"
                     else "from rows searched in other benchmarks (it often costs more than the scoring).")
                  + f" In all, about {show_tokens(est['low'])} to {show_tokens(est['high'])} tokens.")
        else:
            print("Their documents are already found, so nothing more is needed for finding.")
    return 0

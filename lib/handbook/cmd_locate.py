"""hb locate [--rows R,...] [--write] [--json] | --set FILE: the section, article or paragraph of
each quote (its "locator", such as s. 4(4)), for answers scored before the scorer recorded it.

Without --set, finds them with plain code from the headings above each quote (locate.py), no
tokens: prints how many were placed and lists those it could not place; --write saves them.
With --set FILE, saves sections found another way (Claude reading the pages): FILE is a JSON list
of {row, question, doc, page, quote, section}, each matched to its evidence item by row, question,
document, page and the start of the quote."""
from __future__ import annotations
import json

from .cli import command_parser, open_project
from .cmd_kit import id_list
from .folder import HandbookError, read_json
from .locate import backfill


def set_sections(project, items: list) -> dict:
    """Saves each item's section on the matching evidence item; returns {"set", "not_found"}."""
    done, missing, by_row = 0, [], {}
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("section"), str) and it["section"].strip():
            by_row.setdefault(str(it.get("row")), []).append(it)
    for row, wanted in by_row.items():
        answers = project.answers(row)
        cells = answers.get("cells") or {}
        changed = False
        for it in wanted:
            hit = None
            for ev in (cells.get(str(it.get("question"))) or {}).get("evidence") or []:
                if (ev.get("doc") == it.get("doc") and ev.get("page") == it.get("page")
                        and str(ev.get("quote") or "")[:60] == str(it.get("quote") or "")[:60]):
                    hit = ev
                    break
            if hit is None:
                missing.append(it)
                continue
            hit["section"], hit["section_by"] = it["section"].strip(), "claude"
            done += 1
            changed = True
        if changed:
            project.write_answers(row, answers)
    return {"set": done, "not_found": missing}


def main(argv) -> int:
    parser = command_parser("locate", "Find the section or article of each quote from the headings above it.")
    parser.add_argument("--rows", help="only these rows, comma separated")
    parser.add_argument("--write", action="store_true", help="save the sections found")
    parser.add_argument("--set", metavar="FILE", help="save sections found another way (a JSON list)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    if args.set:
        items = read_json(args.set, None)
        if not isinstance(items, list):
            raise HandbookError(f"{args.set} must hold a JSON list of {{row, question, doc, page, quote, section}}.")
        out = set_sections(project, items)
        print(json.dumps(out, ensure_ascii=False) if args.json else
              f"Saved {out['set']} section(s); {len(out['not_found'])} did not match an evidence item.")
        return 0
    out = backfill(project, id_list(args.rows), write=args.write)
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    verb = "Saved" if args.write else "Found"
    print(f"{verb} a section for {out['placed']} quote(s); {len(out['unplaced'])} could not be placed "
          f"from the headings; {out['already']} already had one.")
    for u in out["unplaced"][:50]:
        print(f"  {u['row']} {u['question']}: {u['doc']} p. {u['page']}: {u['quote'][:80]}")
    return 0

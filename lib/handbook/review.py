"""Review by colleagues: an Excel workbook of every answer, and reading it back.

Most teams have one person who runs the tool and one or two colleagues who check the work, and the
colleagues have no Claude plan and no copy of the tool. So the person running it sends the answers out
as one workbook, and brings the marked-up workbook back in.

`send` writes `<project>/review/<name> review <date time>.xlsx`. If the project folder sits in
OneDrive, SharePoint, Google Drive or Dropbox, colleagues open it there, and several of them can work in
it at once. The workbook has

- "Start here", which says what to do;
- "Review", one row per answer, with the answer, what it means, why, the evidence and where it is, and
  the possible answers. Those columns are locked. The yellow columns are the reviewer's own, which are
  their name, "Agree?", "Should be" and a comment. Rows can be filtered and sorted;
- a very hidden sheet `_review` with, for each row, the unit, the question and a fingerprint of the
  answer it showed (`team.cell_rev`), so the row is matched to its cell however the rows were sorted,
  and an answer changed since the workbook was made is never overwritten.

`bring_in` reads the newest workbook (or the one given) and, row by row,

- "Agree", with no different "Should be", puts a check mark on the answer under the reviewer's name;
- a "Should be" that differs from the answer is a correction, saved the way the page's Correct this
  saves one (`corrections.apply`), so the reviewer's answer stands in the cell and the cell's history
  keeps the earlier one;
- "Disagree" with no "Should be", "Not sure", or a comment on its own goes on the list to look at;
- a row whose answer changed since the workbook was made, or whose "Should be" is not one of the
  possible answers, is passed over and listed with the reason.

Reviewers are not team members. Each gets a hidden id from their name (`reviewer_id`), kept with the
name in `review/reviewers.json`, so the local page can put the name to a check mark and a published
page only ever shows that a person checked it. Reading the same workbook twice changes nothing more:
check marks are set again and each correction's id comes from the workbook and the row.

All of this is in the free edition. The feedback round for the companies themselves (packs.py,
replies.py) is a separate thing.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import answers as answer_kinds, team
from .folder import HandbookError, Project, read_json, write_json

REVIEW_DIR = "review"
VERSION = 1
START_SHEET = "Start here"
REVIEW_SHEET = "Review"
META_SHEET = "_review"
CHUNK = 30000
AGREE, DISAGREE, NOT_SURE = "Agree", "Disagree", "Not sure"
CHOICES = (AGREE, DISAGREE, NOT_SURE)

LOCKED = ["Ref", "{Unit}", "Section", "Question", "Answer", "What it means", "Why", "Evidence and where it is",
          "Possible answers"]
YELLOW = ["Your name", "Agree?", "Should be", "Comment"]
WIDTHS = [6, 24, 18, 40, 16, 34, 46, 60, 34, 16, 12, 16, 44]
GREEN, YELLOW_FILL, INK = "1F5F4A", "FFF4C2", "1A1A1A"


def _stamp(now: datetime | None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(v) -> str:
    return "" if v is None else str(v).strip()


# ---- Reviewers --------------------------------------------------------------------------------------

def reviewer_id(name: str) -> str:
    key = re.sub(r"\s+", " ", _s(name)).lower()
    return "r-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def reviewers(project: Project) -> dict:
    """Reviewer id -> name, for everyone whose review was brought in. A damaged file (a sync service's half-written
    or conflicted copy in a shared folder) reads as no names, since the page asks for this on every poll."""
    try:
        data = read_json(project.path / REVIEW_DIR / "reviewers.json", {"reviewers": {}})
    except HandbookError:
        return {}
    known = data.get("reviewers") if isinstance(data, dict) else None
    return {k: v for k, v in known.items() if isinstance(v, str)} if isinstance(known, dict) else {}


def _remember(project: Project, people: dict) -> None:
    if not people:
        return
    known = reviewers(project)
    if all(known.get(k) == v for k, v in people.items()):
        return
    known.update(people)
    write_json(project.path / REVIEW_DIR / "reviewers.json", {"reviewers": known})


# ---- What goes in the workbook --------------------------------------------------------------------

def _levels(question: dict) -> list[dict]:
    return [lv for lv in (question.get("answer") or {}).get("levels") or [] if isinstance(lv, dict)]


def _level_of(question: dict, value) -> dict | None:
    return next((lv for lv in _levels(question) if str(lv.get("value")) == str(value)), None)


def _label(lv: dict) -> str:
    return _s(lv.get("label") or lv.get("name"))


def value_words(question: dict, value) -> str:
    if value is None:
        return "No answer"
    lv = _level_of(question, value)
    label = _label(lv) if lv else ""
    return f"{value} ({label})" if label and label != str(value) else str(value)


def _options(question: dict) -> str:
    lines = []
    for lv in _levels(question):
        words = _label(lv) or _s(lv.get("meaning"))
        lines.append(f"{lv.get('value')} = {words}" if words and words != str(lv.get("value")) else str(lv.get("value")))
    if not lines and answer_kinds.is_number(question):
        return answer_kinds.describe(question)
    return "\n".join(lines)


def _evidence(project: Project, cell: dict, docs: dict) -> str:
    out = []
    for e in cell.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        meta = docs.get(e.get("doc")) or {}
        quote = e.get("quote_en") or e.get("quote") or ""
        page = f", page {e['page']}" if e.get("page") else ""
        out.append(f"“{quote}”\n{meta.get('title') or e.get('doc') or ''}{page}\n{meta.get('url') or ''}".rstrip())
    return "\n\n".join(out) or "No evidence was found for this."


def rows(project: Project) -> list[dict]:
    """One dict per answer a person can review, in unit order then question order."""
    sheet = project.read("scoresheet") or {}
    section_names = {s.get("id"): s.get("name") or s.get("id") for s in sheet.get("sections") or [] if isinstance(s, dict)}
    docs = project.documents()
    out = []
    for unit in project.read("units").get("units", []):
        if not isinstance(unit, dict) or not unit.get("id"):
            continue
        cells = project.answers(unit["id"]).get("cells") or {}
        for q in project.questions_for(unit):
            cell = cells.get(q["id"])
            if not isinstance(cell, dict) or cell.get("state") not in ("answered", "no_evidence"):
                continue
            value = cell.get("value") if cell.get("state") == "answered" else q.get("no_evidence_value")
            lv = _level_of(q, value)
            out.append({
                "row": unit["id"], "qid": q["id"], "rev": team.cell_rev(cell),
                "values": [unit.get("name") or unit["id"], section_names.get(q.get("section")) or _s(q.get("section")),
                           f"{q.get('short') or q['id']}\n{q.get('wording') or ''}".strip(),
                           value_words(q, value), _s((lv or {}).get("meaning")), _s(cell.get("note")),
                           _evidence(project, cell, docs), _options(q)],
                "choices": [str(x.get("value")) for x in _levels(q)],
            })
    return out


# ---- Sending ---------------------------------------------------------------------------------------

def _file_name(project: Project, now: datetime) -> str:
    proj = project.read("project")
    name = _s(proj.get("name")) or project.path.name
    name = re.sub(r'[\\/:*?"<>|]+', " ", name).strip() or "Benchmark"
    local = now.astimezone() if now.tzinfo else now
    return f"{name} review {local.strftime('%Y-%m-%d %H%M')}.xlsx"



def _put(ws, row: int, col: int, value=None):
    """A cell holding `value` exactly as text: characters Excel files cannot hold (control characters a web page or
    a scan can carry) are dropped, so one bad quote never stops the workbook, and text starting with "=" stays
    text, never a formula Excel would run on a reviewer's computer. The meta sheet goes through this too, since its
    JSON is cut into chunks and a chunk may start with "="."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
    c = ws.cell(row, col, value)
    if isinstance(value, str) and value.startswith("="):
        c.data_type = "s"
    return c

def _start_sheet(wb, project: Project, count: int, sender: str | None):
    from openpyxl.styles import Alignment, Font
    ws = wb.active
    ws.title = START_SHEET
    proj = project.read("project")
    plural = _s(proj.get("unit_word_plural")) or "rows"
    lines = [
        (f"{_s(proj.get('name')) or 'Benchmark'}, answers to review", Font(bold=True, size=16, color=GREEN)),
        (f"There are {count} answers on the Review tab, one per row, each with the evidence it rests on.", None),
        ("", None),
        ("How to review", Font(bold=True, size=12)),
        ("1. Open the Review tab. Filter it to the "
         f"{plural} or sections you're checking, so several people can share the work.", None),
        ("2. Write your name in the first yellow column of each row you look at.", None),
        ("3. Say whether you agree. If the answer is wrong, pick what it should be in Should be, and say why in "
         "Comment.", None),
        ("4. Leave rows you haven't looked at blank. Blank rows change nothing.", None),
        ("5. Save the file and leave it where it is. " + (f"Then tell {sender} you've finished, "
                                                           if sender else "Then tell the person who sent it ")
         + "so the reviews can be brought into the tool.", None),
        ("", None),
        ("Your ticks show as checked under your name. A different answer goes in as a correction, and the tool "
         "keeps the earlier answer in that answer's history. Please don't rename the tabs or move the columns.", None),
    ]
    for i, (text, font) in enumerate(lines, 1):
        c = _put(ws, i, 1, text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if font:
            c.font = font
    ws.column_dimensions["A"].width = 110
    return ws


def _review_sheet(wb, items: list[dict], unit_word: str):
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    line = Side(style="thin", color="D5DBE1")
    ws = wb.create_sheet(REVIEW_SHEET)
    head = [h.replace("{Unit}", unit_word[:1].upper() + unit_word[1:]) for h in LOCKED] + YELLOW
    for i, (title, width) in enumerate(zip(head, WIDTHS), 1):
        c = _put(ws, 1, i, title)
        yellow = i > len(LOCKED)
        c.font = Font(bold=True, color=INK if yellow else "FFFFFF")
        c.fill = PatternFill("solid", fgColor="F2C94C" if yellow else GREEN)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[c.column_letter].width = width
    ws.column_dimensions["A"].hidden = True
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "C2"
    agree = DataValidation(type="list", formula1='"' + ",".join(CHOICES) + '"', allow_blank=True)
    ws.add_data_validation(agree)
    lists: dict[str, DataValidation] = {}
    first_yellow = len(LOCKED) + 1
    for n, item in enumerate(items, 2):
        for i, v in enumerate([item["key"]] + item["values"], 1):
            c = _put(ws, n, i, v)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = Border(bottom=line)
            c.font = Font(color=INK, bold=(i == 5))
        for i in range(first_yellow, len(head) + 1):
            c = ws.cell(n, i)
            c.fill = PatternFill("solid", fgColor=YELLOW_FILL)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = Border(bottom=line, left=line)
            c.protection = Protection(locked=False)
        agree.add(ws.cell(n, first_yellow + 1))
        formula = '"' + ",".join(item["choices"]) + '"'
        if item["choices"] and len(formula) <= 250 and not any("," in x or '"' in x for x in item["choices"]):
            dv = lists.get(formula)
            if dv is None:
                dv = lists[formula] = DataValidation(type="list", formula1=formula, allow_blank=True)
                ws.add_data_validation(dv)
            dv.add(ws.cell(n, first_yellow + 2))
        ws.row_dimensions[n].height = 120
    ws.protection.sheet = True
    ws.protection.autoFilter = False          # filtering and sorting stay allowed on the locked sheet
    ws.protection.sort = False
    ws.protection.formatColumns = False
    ws.protection.formatRows = False
    ws.auto_filter.ref = f"A1:{ws.cell(1, len(head)).column_letter}{max(len(items), 1) + 1}"
    return ws


def _meta_sheet(wb, meta: dict):
    ws = wb.create_sheet(META_SHEET)
    text = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    for i in range(0, len(text), CHUNK):
        _put(ws, 1 + i // CHUNK, 1, text[i:i + CHUNK])
    ws.sheet_state = "veryHidden"
    ws.protection.sheet = True


def send(project: Project, sender: str | None = None, now: datetime | None = None) -> dict:
    """Write the review workbook into `<project>/review/`. Returns {path, file, folder, answers}."""
    from openpyxl import Workbook
    now = now or datetime.now(timezone.utc)
    items = rows(project)
    if not items:
        raise HandbookError("There are no answers to review yet. Run some first.")
    for n, item in enumerate(items, 1):
        item["key"] = f"{n:05d}"
    proj = project.read("project")
    wb = Workbook()
    _start_sheet(wb, project, len(items), _s(sender) or None)
    _review_sheet(wb, items, _s(proj.get("unit_word")) or "row")
    made = _stamp(now)
    _meta_sheet(wb, {"version": VERSION, "benchmark": proj.get("slug"), "made": made,
                     "rows": {i["key"]: {"row": i["row"], "qid": i["qid"], "rev": i["rev"]} for i in items}})
    folder = project.path / REVIEW_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _file_name(project, now)
    wb.save(path)
    log = read_json(folder / "sent.json", {"sent": []})
    log.setdefault("sent", []).append({"file": path.name, "made": made, "answers": len(items)})
    write_json(folder / "sent.json", log)
    return {"path": str(path), "file": path.name, "folder": str(folder), "answers": len(items), "made": made}


# ---- Bringing reviews in -------------------------------------------------------------------------------

def workbooks(project: Project) -> list[Path]:
    """The review workbooks in the review folder, newest first (Excel's own ~$ lock files left out)."""
    folder = project.path / REVIEW_DIR
    if not folder.is_dir():
        return []
    found = [p for p in folder.glob("*.xlsx") if not p.name.startswith(("~$", "."))]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def parse(path: Path) -> dict:
    """{made, rows: {key: {row, qid, rev}}, marks: [{key, name, agree, should_be, comment}], modified_by}."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path, data_only=True)
    except Exception as e:                       # a file Excel is still writing, or not a workbook
        raise HandbookError(f"‘{path.name}’ could not be opened as a workbook ({e}).") from None
    if META_SHEET not in wb.sheetnames or REVIEW_SHEET not in wb.sheetnames:
        raise HandbookError(f"‘{path.name}’ is not a review workbook from this tool (its {REVIEW_SHEET} tab or "
                            "hidden details are missing).")
    text = "".join(_s(r[0].value) for r in wb[META_SHEET].iter_rows(max_col=1) if r and r[0].value)
    try:
        meta = json.loads(text)
    except ValueError:
        raise HandbookError(f"The hidden details in ‘{path.name}’ are damaged.") from None
    ws = wb[REVIEW_SHEET]
    head = [_s(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1))]
    col = {name: i for i, name in enumerate(head)}
    if "Ref" not in col or any(y not in col for y in YELLOW):
        raise HandbookError(f"The Review tab in ‘{path.name}’ has lost some of its columns.")
    marks = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        key = _s(r[col["Ref"]]) if len(r) > col["Ref"] else ""
        if not key:
            continue
        got = {y: _s(r[col[y]]) if len(r) > col[y] else "" for y in YELLOW}
        if not any(got.values()):
            continue
        marks.append({"key": key.zfill(5), "name": got["Your name"], "agree": got["Agree?"],
                      "should_be": got["Should be"], "comment": got["Comment"]})
    return {"made": meta.get("made"), "benchmark": meta.get("benchmark"), "rows": meta.get("rows") or {},
            "marks": marks, "modified_by": _s(getattr(wb.properties, "lastModifiedBy", None))}


def _choice(text: str) -> str | None:
    t = _s(text).lower()
    if not t:
        return None
    for c in CHOICES:
        if t == c.lower():
            return c
    if t in ("yes", "y", "agreed", "correct", "ok", "✓"):
        return AGREE
    if t in ("no", "n", "wrong"):
        return DISAGREE
    if t in ("unsure", "maybe", "?", "not sure?"):
        return NOT_SURE
    return None


def parse_value(question: dict, text: str):
    """The answer a reviewer's "Should be" means, or raises ValueError with the possible answers."""
    t = _s(text)
    levels = _levels(question)
    for lv in levels:
        v = str(lv.get("value"))
        if t.lower() == v.lower() or t.lower() in {_label(lv).lower(), _s(lv.get("meaning")).lower()} - {""}:
            return lv.get("value")
    head = re.split(r"\s*[=(:\-–]\s*", t, maxsplit=1)[0].strip()
    for lv in levels:
        if head and head.lower() == str(lv.get("value")).lower():
            return lv.get("value")
    try:
        num = float(head.replace(",", "").rstrip("%"))
    except ValueError:
        num = None
    if num is not None:
        for lv in levels:
            try:
                if float(lv.get("value")) == num:
                    return lv.get("value")
            except (TypeError, ValueError):
                continue
        figure = int(num) if num.is_integer() else num
        if answer_kinds.figure_ok(question, figure):
            return figure
    raise ValueError(answer_kinds.describe(question))


def bring_in(project: Project, path: Path | str | None = None, now: datetime | None = None) -> dict:
    """Read a review workbook (default: the newest in the review folder) and apply it. Returns the
    summary: {file, checked, corrected, to_look_at, skipped, people, already}."""
    from . import corrections
    now = now or datetime.now(timezone.utc)
    stamp = _stamp(now)
    if path is None:
        found = workbooks(project)
        if not found:
            raise HandbookError("There's no review workbook in the review folder yet. Use Send for review first.")
        path = found[0]
    path = Path(path)
    if not path.is_file():
        raise HandbookError(f"‘{path}’ is not there.")
    got = parse(path)
    slug = project.read("project").get("slug")
    if got.get("benchmark") and slug and got["benchmark"] != slug:
        raise HandbookError(f"‘{path.name}’ was made for another project ({got['benchmark']}).")
    units = {u.get("id"): u for u in project.read("units").get("units", []) if isinstance(u, dict)}
    questions_by_row: dict[str, dict] = {}
    names: dict[str, str] = {}
    checked, corrected, to_look, skipped = [], [], [], []
    fixes = []
    changed_rows: dict[str, dict] = {}
    for m in got["marks"]:
        where = got["rows"].get(m["key"])
        if not where or where.get("row") not in units:
            skipped.append({"key": m["key"], "why": "This row isn't one the tool knows. It may have been pasted in."})
            continue
        row, qid = where["row"], where["qid"]
        if row not in questions_by_row:
            questions_by_row[row] = {q["id"]: q for q in project.questions_for(units[row])}
        q = questions_by_row[row].get(qid)
        name = m["name"] or got["modified_by"] or "A reviewer"
        rid = reviewer_id(name)
        names[rid] = name
        unit_name = units[row].get("name") or row
        item = {"row": row, "qid": qid, "unit": unit_name, "question": (q or {}).get("short") or qid,
                "by": rid, "name": name, "comment": m["comment"] or None, "agree": _choice(m["agree"])}
        if q is None:
            skipped.append({**item, "why": "This question no longer applies here."})
            continue
        answers = changed_rows.get(row) or project.answers(row)
        cell = (answers.get("cells") or {}).get(qid)
        fix_id = f"rv-{re.sub(r'[^0-9]', '', str(got.get('made') or ''))}-{m['key']}"
        owner = (cell or {}).get("owner") if isinstance((cell or {}).get("owner"), dict) else {}
        if owner.get("correction") == fix_id:      # this workbook's own change, brought in before
            corrected.append({**item, "from": None, "to": owner.get("value"), "already": True})
            continue
        if team.cell_rev(cell) != where.get("rev"):
            skipped.append({**item, "why": "The answer changed after the workbook was made, so this was left alone."})
            continue
        current = cell.get("value") if isinstance(cell, dict) and cell.get("state") == "answered" else q.get("no_evidence_value")
        new = None
        if m["should_be"]:
            try:
                new = parse_value(q, m["should_be"])
            except ValueError as e:
                skipped.append({**item, "why": f"‘{m['should_be']}’ isn't one of the possible answers ({e})."})
                continue
        if new is not None and str(new) != str(current):
            reason = m["comment"] or f"{name} said it should be {new} in the review workbook."
            fixes.append({"id": fix_id, "row": row, "qid": qid, "value": new, "reason": reason, "by": rid, "at": stamp})
            corrected.append({**item, "from": current, "to": new, "fix": fix_id})
            continue
        if item["agree"] == AGREE or (new is not None and item["agree"] in (None, AGREE)):
            cell["checked"] = {"at": stamp, "how": "review", "by": rid, "name": name}
            cell.pop("rescored", None)
            answers["row"] = row
            changed_rows[row] = answers
            checked.append(item)
            if m["comment"]:
                to_look.append({**item, "why": "Agreed, with a comment."})
            continue
        why = {DISAGREE: "Disagreed, with no answer given.", NOT_SURE: "Not sure."}.get(item["agree"], "A comment.")
        to_look.append({**item, "why": why})
    for row, answers in changed_rows.items():
        project.write_answers(row, answers)
    _remember(project, names)
    applied = corrections.apply(project, {"corrections": fixes}, now) if fixes else {"applied": [], "skipped": [], "already": 0}
    done_ids = {r.get("id") for r in applied["applied"]}
    bad = {x.get("id"): x.get("why") for x in applied["skipped"]}
    for item in list(corrected):
        fid = item.pop("fix", None)
        if fid is None:
            continue
        if fid in bad:
            corrected.remove(item)
            skipped.append({**item, "why": bad[fid]})
        elif fid not in done_ids:
            item["already"] = True
    if project.is_team():
        for it in checked:
            team.log(project, {"id": it["by"], "name": it["name"]}, "checked", row=it["row"], qid=it["qid"],
                     text=f"{it['name']} checked {it['unit']}, {it['question']} in the review workbook")
        for it in corrected:
            team.log(project, {"id": it["by"], "name": it["name"]}, "corrected", row=it["row"], qid=it["qid"],
                     text=f"{it['name']} changed {it['unit']}, {it['question']} from {it['from']} to {it['to']} "
                          "in the review workbook")
    people: dict[str, int] = {}
    for it in checked + corrected + to_look:
        people[it["name"]] = people.get(it["name"], 0) + 1
    summary = {"file": path.name, "at": stamp, "made": got.get("made"), "checked": checked, "corrected": corrected,
               "to_look_at": to_look, "skipped": skipped, "people": people,
               "already": sum(1 for it in corrected if it.get("already"))}
    folder = project.path / REVIEW_DIR / "brought-in"
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / f"{stamp.replace(':', '')}.json", summary)
    return summary


def last_brought_in(project: Project) -> dict | None:
    folder = project.path / REVIEW_DIR / "brought-in"
    if not folder.is_dir():
        return None
    found = sorted(folder.glob("*.json"))
    return read_json(found[-1], None) if found else None


def status(project: Project) -> dict:
    """What the page's Review section shows."""
    books = workbooks(project)
    return {"folder": str(project.path / REVIEW_DIR),
            "workbooks": [{"file": p.name, "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ")} for p in books[:10]],
            "last": last_brought_in(project), "reviewers": reviewers(project)}


def summary_words(s: dict) -> str:
    """One plain paragraph for the chat and the command line."""
    parts = []
    if s["checked"]:
        parts.append(f"{len(s['checked'])} checked")
    if s["corrected"]:
        parts.append(f"{len(s['corrected'])} changed")
    if s["to_look_at"]:
        parts.append(f"{len(s['to_look_at'])} to look at")
    if s["skipped"]:
        parts.append(f"{len(s['skipped'])} left alone")
    who = ", ".join(f"{n} ({c})" for n, c in s["people"].items())
    head = f"From ‘{s['file']}’, " + (", ".join(parts) if parts else "nothing was marked yet") + "."
    return head + (f" Reviewed by {who}." if who else "")


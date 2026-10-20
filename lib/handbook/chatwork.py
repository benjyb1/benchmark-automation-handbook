"""What the corner chat reaches through `hb serve` beyond the page's own data: a row's documents (to
read and search them), files it makes for the owner (CSV or Excel, in exports/), and the copies of
the project taken before a background job, so one Undo puts everything back.

    row_documents(project, row) -> [{doc, title, publisher, date, url, pages, kept}]
    read_pages(project, doc, page, count=1) -> {doc, title, pages: [{page, text}], last_page}
    search(project, words, rows=None, limit=25) -> [{row, doc, title, page, snippet}]
    make_file(project, name, columns, rows, fmt) -> Path         # exports/<name>.csv or .xlsx
    export_path(project, name) -> Path | None                     # a file made before, for download
    snapshot(project_path, folder) -> Path, restore(project_path, snap, keep_current) -> None
"""
from __future__ import annotations
import csv
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .folder import HandbookError, Project

MAX_READ = 20_000            # characters of page text in one read (a tool result may be 32 KB)
SNIPPET = 160                # characters either side of a search hit
EXPORTS = "exports"
USAGE_LOG = ("page-data", "_log")    # serve.Worker.usage_log's folder
KEEP_SNAPSHOTS = 6


def _rows_of(project: Project, rows=None) -> list[str]:
    ids = [u.get("id") for u in project.read("units").get("units", []) if isinstance(u, dict) and u.get("id")]
    if rows is None:
        return ids
    unknown = [r for r in rows if r not in ids]
    if unknown:
        raise HandbookError(f"There is no row {', '.join(unknown)}.")
    return list(rows)


def row_documents(project: Project, row: str) -> list[dict]:
    """The documents found for `row`, in the order the finder kept them."""
    _rows_of(project, [row])
    docs = project.documents()
    out = []
    for doc in project.found(row).get("documents") or []:
        m = docs.get(doc) or {}
        out.append({"doc": doc, "title": m.get("title"), "publisher": m.get("publisher"), "date": m.get("date"),
                    "url": m.get("url"), "pages": len(project.pages(doc)), "kept": m.get("kept") is not False})
    return out


def read_pages(project: Project, doc: str, page: int = 1, count: int = 1) -> dict:
    """Page `page` and the `count - 1` after it, cut at MAX_READ characters."""
    meta = project.documents().get(doc)
    if meta is None:
        raise HandbookError(f"There is no document {doc}.")
    pages = project.pages(doc)
    if not pages:
        raise HandbookError(f"Document {doc} has no saved text.")
    want = range(int(page), int(page) + max(1, min(int(count or 1), 10)))
    got, size = [], 0
    for p in pages:
        if p.get("page") in want:
            text = p.get("text") or ""
            if size + len(text) > MAX_READ and got:
                break
            got.append({"page": p.get("page"), "text": text[:MAX_READ - size]})
            size += len(got[-1]["text"])
    if not got:
        raise HandbookError(f"Document {doc} has pages 1 to {len(pages)}; there is no page {page}.")
    return {"doc": doc, "title": meta.get("title"), "pages": got, "last_page": max(p.get("page") or 0 for p in pages)}


def search(project: Project, words: str, rows=None, limit: int = 25) -> list[dict]:
    """Pages of the rows' documents that hold `words`: the exact phrase first, then pages holding
    every word. Case and spacing are ignored."""
    phrase = re.sub(r"\s+", " ", str(words or "")).strip().lower()
    if len(phrase) < 2:
        raise HandbookError("Give some words to search for.")
    terms = [t for t in re.findall(r"\w+", phrase) if len(t) > 1]
    docs = project.documents()
    seen, exact, loose = set(), [], []
    for row in _rows_of(project, rows):
        for doc in project.found(row).get("documents") or []:
            if (doc, row) in seen:
                continue
            seen.add((doc, row))
            for p in project.pages(doc):
                flat = re.sub(r"\s+", " ", p.get("text") or "")
                low = flat.lower()
                at = low.find(phrase)
                hit = at >= 0 or (terms and all(t in low for t in terms))
                if not hit:
                    continue
                if at < 0:
                    at = low.find(terms[0])
                item = {"row": row, "doc": doc, "title": (docs.get(doc) or {}).get("title"), "page": p.get("page"),
                        "snippet": ("..." if at > SNIPPET else "") + flat[max(0, at - SNIPPET): at + len(phrase) + SNIPPET] + "..."}
                (exact if low.find(phrase) >= 0 else loose).append(item)
    return (exact + loose)[:limit]


def _safe_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 _().-]+", "", str(name or "")).strip().strip(".")
    return re.sub(r"\s+", " ", s)[:80] or "export"


def make_file(project: Project, name: str, columns: list, rows: list, fmt: str = "xlsx") -> Path:
    """Writes the table to exports/<name>.<fmt> (a file of that name is replaced) and returns it."""
    if fmt not in ("csv", "xlsx"):
        raise HandbookError("A file is csv or xlsx.")
    if not isinstance(columns, list) or not columns or not isinstance(rows, list):
        raise HandbookError("A file needs columns and rows.")
    cols = [str(c) for c in columns]
    body = [[("" if v is None else v) if isinstance(v, (int, float, str)) else str(v) for v in (r if isinstance(r, list) else [])]
            for r in rows]
    folder = project.path / EXPORTS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_safe_name(name)}.{fmt}"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        w.writerows(body)
        path.write_text(buf.getvalue(), encoding="utf-8-sig")       # Excel opens UTF-8 with the mark
        return path
    try:
        from openpyxl import Workbook
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        from openpyxl.styles import Alignment, Font
    except ImportError:
        raise HandbookError("Excel files need openpyxl, which is not installed here: ask for a CSV file instead.") from None
    wb = Workbook()
    ws = wb.active
    ws.title = _safe_name(name)[:31] or "Sheet"
    ws.append(cols)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in body:
        ws.append([ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in r])
    for i, col in enumerate(cols, 1):
        longest = max([len(col)] + [len(str(r[i - 1])) for r in body if len(r) >= i] or [10])
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(max(10, longest + 2), 70)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def export_path(project: Project, name: str) -> Path | None:
    """A file in exports/ by its file name, never outside it."""
    folder = (project.path / EXPORTS).resolve()
    path = (folder / Path(str(name)).name).resolve()
    return path if path.parent == folder and path.is_file() else None


# --- Copies of the project for Undo -----------------------------------------------------------

def _copy_tree(src: Path, dest: Path) -> None:
    """A full copy; on a Mac a clone (`cp -c`), which takes no extra space until files change."""
    if sys.platform == "darwin":
        done = subprocess.run(["cp", "-cR", str(src), str(dest)], capture_output=True, text=True)
        if done.returncode == 0:
            return
        if dest.exists():
            shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=True)


def snapshot(project_path: Path, folder: Path, name: str) -> Path:
    """Copies the project to folder/name and keeps only the newest KEEP_SNAPSHOTS copies there."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if dest.exists():
        raise HandbookError(f"A copy named {name} already exists.")
    _copy_tree(project_path, dest)
    old = sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for p in old[:-KEEP_SNAPSHOTS]:
        shutil.rmtree(p, ignore_errors=True)
    return dest


def restore(project_path: Path, snap: Path, keep_current: Path) -> None:
    """Puts the project back as it was in `snap`, except the files made for the owner (exports/)
    and the record of tokens used (page-data/_log/), which stay as they are now: neither is part of
    the work an Undo takes back. What is
    there now is first copied to `keep_current`, so an Undo can itself be undone."""
    if not (snap / "project.json").is_file():
        raise HandbookError("That copy of the project is gone, so it cannot be put back.")
    _copy_tree(project_path, keep_current)
    for child in list(project_path.iterdir()):
        if child.name == EXPORTS:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in snap.iterdir():
        if child.name != EXPORTS:
            _copy_tree(child, project_path / child.name)
    # The record of what Claude's work used stays too: the tokens a job used were spent either way.
    log = keep_current.joinpath(*USAGE_LOG)
    if log.is_dir():
        dest = project_path.joinpath(*USAGE_LOG)
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _copy_tree(log, dest)


# --- Undo of one change only ------------------------------------------------------------------
# A whole-folder restore also took back what happened after the change: a colleague's correction in a
# shared folder, a later run, the team's log (20 Oct 2026 review). So each change records which files
# it touched (size and time, before and after), and Undo puts back only those, and only while they are
# still as the change left them.

# Not part of any change's Undo: files made for the owner, the record of tokens used, the team's shared
# files, and what `check` and `build` make again straight after.
NOT_UNDONE = (EXPORTS, "page-data/_log", "team", "team.json", "viewer", "checks")


def _undone(rel: str) -> bool:
    return not any(rel == x or rel.startswith(x + "/") for x in NOT_UNDONE)


def fingerprint(project_path: Path) -> dict[str, list]:
    """{relative path: [size, mtime_ns]} for the project's files that an Undo may put back."""
    out: dict[str, list] = {}
    for f in project_path.rglob("*"):
        try:
            if f.is_dir() and not f.is_symlink():
                continue
            rel = f.relative_to(project_path).as_posix()
            if _undone(rel):
                st = f.lstat()
                out[rel] = [st.st_size, st.st_mtime_ns]
        except OSError:
            continue
    return out


def changes(before: dict, after: dict) -> dict:
    """What a change did: {path: its fingerprint after the change, or None where it removed the file},
    with {"had": [paths that were there before]} kept beside it by the caller."""
    return {rel: after.get(rel) for rel in set(before) | set(after) if before.get(rel) != after.get(rel)}


def undo_changes(project_path: Path, snap: Path, changed: dict, had: list, keep_current: Path) -> int:
    """Puts back, from the copy `snap` taken just before the change, only the files the change touched.
    Refuses (HandbookError, naming the files) when any of them has changed again since: putting it back
    would lose that later work. What is there now is first copied to `keep_current`. Returns how many
    files were put back."""
    if not (snap / "project.json").is_file():
        raise HandbookError("That copy of the project is gone, so it cannot be put back.")
    now = fingerprint(project_path)
    moved = sorted(rel for rel, sig in changed.items() if now.get(rel) != sig)
    if moved:
        shown = ", ".join(moved[:6]) + (f" and {len(moved) - 6} more" if len(moved) > 6 else "")
        raise HandbookError("Undo would lose work done since this change (by a later change, a run or a colleague) "
                            f"in {shown}. Undo the later changes first, or ask the chat to put it back as it was.")
    had = set(had)
    for rel in changed:
        dest = project_path / rel
        if dest.exists() or dest.is_symlink():
            keep = keep_current / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, keep, follow_symlinks=False)
            dest.unlink()
        if rel in had:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snap / rel, dest, follow_symlinks=False)
    # Folders the change made and left empty go too.
    for rel in sorted({str(Path(r).parent) for r in changed if r not in had}, key=len, reverse=True):
        d = project_path / rel
        while d != project_path and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    return len(changed)

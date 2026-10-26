"""Checking a saved web page against the live site, and the copies of a page kept over time.

A document's copy is never replaced. When `recheck_document` finds that the live page's text differs from
the saved copy, the live page is saved as a new document of its own (its own id and fetch date), its
meta.json names the copy before it under `earlier` ({"doc", "added", "removed"}), and
`documents/<new>/changes.json` holds the lines that changed. The earlier copy and its meta.json are left
exactly as they were: they are the record of what the page said first (CIWF, 23 Oct 2026: Kefalonia's score
rose between two rounds months apart and no one could show whether its site had changed). When the text is
the same, the date of the check goes on the copy's `rechecks` list.

Which copy the scorer reads afterwards, row by row (found/<row>.json):
- a row whose cut-off has passed (render.wants_dated_copy) keeps the copy it had: it is the nearer to the
  date the benchmark scores as at, and a later copy says only what the page says now;
- any other row reads the new copy in its place, and its answers that cite the earlier copy are marked out
  of date, as `found --remove` does, except an answer a person checked or the owner gave, which nobody but a
  person changes. The page shows those with the later copy beside them;
- unless most of the page's text has gone (`gutted`): a redesign that leaves "welcome to our new website"
  where the policy was. Rows then stay on the copy they read, the new copy is saved and marked `held`, and
  the answers citing the earlier copy are flagged `page_gone`, until the owner either accepts the new copy
  (`accept`) or asks for the text to be looked for where it went (`look`, which opens the row's second
  search again). Switching at once would have the rows scored again from a page that no longer holds the
  policy, without a search for where it moved (CIWF notes, 23 Oct 2026).

A live page with no readable text (a PDF of pictures, an empty page) is never saved as a copy: the check
records that the page could not be read, as for a robot check. A row that took any copy of a page off
(`found --remove`) is never given another copy of it (fetch.add_to_found).
"""
from __future__ import annotations
import difflib
import hashlib
import re
import tempfile
import urllib.parse
from pathlib import Path

from . import fetch, readers, render
from .folder import HandbookError, now_stamp, write_json
from .hosts import on_results_site

MAX_LINES = 200            # changed lines kept in changes.json (the counts are always whole)
LINE_CHARS = 400           # each kept line cut to this many characters
KEEP_CHECKS = 50           # the newest checks kept on a copy's `rechecks`
POLITE_S = 1.0             # hb recheck asks any one site at most once a second
CHALLENGE_TEXT = 1500      # as cmd_fetch: a robot check shows less text than this
# A copy is "gutted" when more than half of the earlier copy's text (by characters) is in lines that went, and
# fewer than half of the earlier copy's words (of four letters or more) are anywhere in the new copy. Both: a
# paragraph reworded ("electrical stunning" becoming "percussive stunning") removes its line but keeps its words,
# and is a change to read; a redesign that leaves "Welcome to our new website" keeps few of them, however long
# the new text is (CIWF notes, 23 Oct 2026). A single edited line in a long page removes too little to count.
GUTTED_SHARE = 0.5
_WORD = re.compile(r"[^\W\d_]{4,}")
LIVE = ("answered", "no_evidence", "not_applicable")


# --- the copies of one page --------------------------------------------------------------------

def later_map(docs: dict) -> dict[str, str]:
    """{earlier copy: the copy saved after it}, from every meta.json's `earlier`."""
    out = {}
    for doc, meta in sorted(docs.items(), key=lambda kv: str((kv[1] or {}).get("fetched") or "")):
        earlier = (meta or {}).get("earlier") if isinstance(meta, dict) else None
        if isinstance(earlier, dict) and isinstance(earlier.get("doc"), str) and earlier["doc"] in docs:
            out.setdefault(earlier["doc"], doc)
    return out


def versions(docs: dict, doc: str, later: dict | None = None) -> list[str]:
    """Every copy of the page `doc` is a copy of, oldest first (just [doc] for a page saved once)."""
    later = later_map(docs) if later is None else later
    first, seen = doc, {doc}
    while True:
        earlier = ((docs.get(first) or {}).get("earlier") or {}) if isinstance(docs.get(first), dict) else {}
        before = earlier.get("doc") if isinstance(earlier, dict) else None
        if not isinstance(before, str) or before not in docs or before in seen:
            break
        first = before
        seen.add(first)
    out = [first]
    while later.get(out[-1]) and later[out[-1]] not in out:
        out.append(later[out[-1]])
    return out


def latest(docs: dict, doc: str, later: dict | None = None) -> str:
    return versions(docs, doc, later)[-1]


def copies_of(project, doc: str) -> set[str]:
    """Every saved copy of the page `doc` is a copy of, `doc` itself included."""
    docs = project.documents()
    return set(versions(docs, doc)) if doc in docs else {doc}


# --- what changed ------------------------------------------------------------------------------

def lines_of(texts) -> list[str]:
    """The text of a copy's pages as lines: spaces collapsed, blank lines dropped, so a change in layout
    alone is not a change."""
    out = []
    for text in texts:
        for line in str(text or "").splitlines():
            line = " ".join(line.split())
            if line:
                out.append(line)
    return out


def compare(old_texts, new_texts) -> dict:
    """{"added", "removed", "lines": [{"op": "+" or "-", "text"}], "cut"}: the lines that changed, in page
    order (removed before added where a line was replaced), at most MAX_LINES of them."""
    a, b = lines_of(old_texts), lines_of(new_texts)
    added = removed = gone = 0
    lines = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for line in a[i1:i2]:
            removed += 1
            gone += len(line)
            lines.append({"op": "-", "text": line[:LINE_CHARS]})
        for line in b[j1:j2]:
            added += 1
            lines.append({"op": "+", "text": line[:LINE_CHARS]})
    words = {w.casefold() for line in a for w in _WORD.findall(line)}
    kept = len(words & {w.casefold() for line in b for w in _WORD.findall(line)}) / len(words) if words else 1.0
    return {"added": added, "removed": removed, "lines": lines[:MAX_LINES], "cut": len(lines) > MAX_LINES,
            "chars": {"earlier": sum(map(len, a)), "later": sum(map(len, b)), "removed": gone},
            "words_kept": round(kept, 3)}


def gutted(diff: dict) -> bool:
    """Whether most of the earlier copy's text has gone from the page (see GUTTED_SHARE)."""
    chars = diff.get("chars") or {}
    before = chars.get("earlier") or 0
    return bool(before) and chars.get("removed", 0) > GUTTED_SHARE * before and diff.get("words_kept", 1.0) < GUTTED_SHARE


def is_held(meta) -> bool:
    """A later copy whose rows were kept on the earlier one (most of the page's text had gone), not yet accepted."""
    earlier = (meta or {}).get("earlier") if isinstance(meta, dict) else None
    return isinstance(earlier, dict) and earlier.get("held") is True


def in_use(docs: dict, doc: str, later: dict | None = None) -> str:
    """The copy of `doc`'s page that rows read now: the newest that is not held."""
    chain = versions(docs, doc, later)
    for d in reversed(chain):
        if not is_held(docs.get(d)):
            return d
    return chain[0]


def held_pages(docs: dict) -> dict[str, str]:
    """{copy rows still read: the held newer copy} for every page whose newest copy is held."""
    later = later_map(docs)
    out = {}
    for d in docs:
        if d in later:
            continue                                   # the newest copies only
        if is_held(docs.get(d)):
            for older in versions(docs, d, later)[:-1]:
                out[older] = d
    return out


def change_words(added: int, removed: int) -> str:
    """"3 lines added and 1 removed", "2 lines removed"."""
    def n(x):
        return f"{x} line{'s' if x != 1 else ''}"
    if added and removed:
        return f"{n(added)} added and {removed} removed"
    return f"{n(added)} added" if added else f"{n(removed)} removed" if removed else "no lines changed"


def changes(project, doc: str) -> dict | None:
    """documents/<doc>/changes.json, or None."""
    from .folder import read_json
    got = read_json(project.doc_dir(doc) / "changes.json", None)
    return got if isinstance(got, dict) else None


# --- the live page -----------------------------------------------------------------------------

def saved_texts(project, doc: str) -> list[str]:
    """The saved copy's text as the tool reads it today, from its original file, so both sides of a comparison
    are read the same way: pages.json may come from an older reader (the CIWF fish copy, 23 Oct 2026: a page
    read as one line then and as 79 now would have looked changed). pages.json when the original cannot be read."""
    from .prepare import find_original
    path = find_original(project, doc)
    if path is not None and readers.kind_for_name(path.name) is not None:
        try:
            return list(readers.read(path, evidence=True).pages)
        except HandbookError:
            pass
    return [p.get("text") or "" for p in project.pages(doc) if isinstance(p, dict)]


def _pages_of(content: bytes, kind: str, name: str) -> list[str]:
    """The page texts the tool would read from these bytes (as prepare does), never stored."""
    with tempfile.TemporaryDirectory(prefix="hb-recheck-") as tmp:
        path = Path(tmp) / f"original{readers.stored_extension(kind, content, name)}"
        path.write_bytes(content)
        return list(readers.read(path, kind, evidence=True).pages)


def live_copy(project, meta: dict) -> dict:
    """The live page for a saved copy: {"ok": True, "content", "kind", "name", "title", "text_source"} or
    {"ok": False, "why"}. The same rules as a first fetch: never from where the benchmark
    publishes its results, never a robot check, and a page that draws its words with a script is read as a
    browser shows it, as its saved copy was."""
    url = meta.get("print_url") or meta.get("url")
    sites = project.read("project").get("results_sites")
    site = on_results_site(url, sites)
    if site:
        return {"ok": False, "why": f"it is on {site}, where the benchmark publishes its results"}
    result = fetch.fetch_bytes(url)
    if result.failure == "too-large":
        return {"ok": False, "why": fetch.TOO_LARGE.lower()}
    if not result.ok:
        return {"ok": False, "why": fetch.describe_failure(result)}
    landed = on_results_site(result.final_url, sites) if result.final_url else None
    if landed:
        return {"ok": False, "why": f"it now leads to {result.final_url}, on {landed}, where the benchmark publishes its results"}
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    kind = fetch.kind_of(result.content, result.content_type, name)
    if kind is None or readers.cannot_read(name, result.content_type, result.content):
        return {"ok": False, "why": f"it now gives {result.content_type or 'something'}, which the tool cannot read"}
    content, title, text_source = result.content, "", None
    if kind == "html":
        words = fetch.extract_text_html(content)
        if render.is_challenge(content) and len(words) < CHALLENGE_TEXT:
            return {"ok": False, "why": "the site answered with a robot check, not the page"}
        title = fetch.html_title(content) or ""
        if fetch.dead_page(title, words):
            return {"ok": False, "why": f"it now shows a placeholder page (\"{title.strip()[:80]}\") where the page was"}
        if meta.get("text_source") in ("rendered", "archive_rendered") or render.is_shell(words):
            # The saved words came from the page as a browser shows it: so must today's, or every line differs.
            got = render.render_text(meta.get("url")) if render.renderer() else None
            if got is None or render.is_shell(got.get("text") or ""):
                return {"ok": False, "why": "the page draws its words with a script, and no browser was available "
                                            "to read it as it shows today"}
            kind, content, text_source = "text", got["text"].encode("utf-8"), "rendered"
            title = got.get("title") or title
    return {"ok": True, "content": content, "kind": kind, "name": name, "title": title, "text_source": text_source}


# --- re-checking -------------------------------------------------------------------------------

def _note_check(project, doc: str, result: str, why: str | None = None, at: str | None = None) -> None:
    from .folder import read_json
    path = project.doc_dir(doc) / "meta.json"
    meta = read_json(path)
    entry = {"at": at or now_stamp(), "result": result}
    if why:
        entry["why"] = why
    meta["rechecks"] = (list(meta.get("rechecks") or []) + [entry])[-KEEP_CHECKS:]
    write_json(path, meta)


def _cites(cell: dict, doc: str) -> bool:
    return any(isinstance(e, dict) and e.get("doc") == doc for e in cell.get("evidence") or [])


def left_alone(cell: dict) -> bool:
    """An answer only a person changes: checked by a person, the owner's own, or kept after a party's reply."""
    return (isinstance(cell.get("checked"), dict) or bool(cell.get("checked") is True)
            or isinstance(cell.get("owner"), dict)
            or (isinstance(cell.get("feedback"), dict) and cell["feedback"].get("kept") is False))


def move_rows(project, olds, new: str) -> dict:
    """File `new` in place of the earlier copies `olds` (one id or a list) on each row whose cut-off has not
    passed; mark that row's answers citing them out of date (not those a person checked). A row that took any
    copy of the page off is left alone. Returns {"moved", "kept", "out_of_date", "checked"} (rows, rows,
    [row, question] pairs, [row, question] pairs)."""
    olds = [olds] if isinstance(olds, str) else list(olds)
    copies = set(olds) | {new}
    moved, kept, stale, checked = [], [], [], []
    for unit in project.read("units").get("units") or []:
        row = unit.get("id") if isinstance(unit, dict) else None
        if not row:
            continue
        found = project.found(row)
        docs = found.get("documents") or []
        if not set(olds) & set(docs) or copies & set(found.get("removed") or []):
            continue
        if render.wants_dated_copy(project.cutoff_for([row])):
            kept.append(row)
            continue
        found["documents"] = list(dict.fromkeys(new if d in olds else d for d in docs))
        if set(olds) & set(found.get("shared") or []):
            found["shared"] = list(dict.fromkeys(new if d in olds else d for d in found["shared"]))
        project.write_found(row, found)
        moved.append(row)
        answers = project.answers(row)
        changed = False
        for qid, cell in (answers.get("cells") or {}).items():
            if not (isinstance(cell, dict) and cell.get("state") in LIVE and any(_cites(cell, d) for d in olds)):
                continue
            if left_alone(cell):
                checked.append([row, qid])
            elif not cell.get("out_of_date"):
                cell["out_of_date"] = True
                stale.append([row, qid])
                changed = True
        if changed:
            project.write_answers(row, answers)
    return {"moved": moved, "kept": kept, "out_of_date": stale, "checked": checked}


def recheck_document(project, doc: str) -> dict:
    """Check the latest saved copy of `doc`'s page against the live site. Returns {"doc" (the copy checked),
    "title", "url", "result": "same" | "changed" | "unreachable" | "skipped", "why", and when changed "new",
    "added", "removed", "moved", "kept", "out_of_date", "checked"}. Nothing is overwritten."""
    from . import prepare
    docs = project.documents()
    if doc not in docs:
        raise HandbookError(f"There is no document {doc} in this project.")
    doc = latest(docs, doc)
    meta = docs[doc]
    out = {"doc": doc, "title": meta.get("title") or doc, "url": meta.get("url")}
    if not meta.get("url"):
        return {**out, "result": "skipped", "why": "it was added as a file, not from a web address"}
    if meta.get("kept") is not True or not project.pages(doc):
        return {**out, "result": "skipped", "why": "it has no saved text to compare yet (prepare reads it)"}
    at = now_stamp()
    live = live_copy(project, meta)
    if not live["ok"]:
        _note_check(project, doc, "unreachable", live["why"], at)
        return {**out, "result": "unreachable", "why": live["why"]}
    if meta.get("sha1") and hashlib.sha1(live["content"]).hexdigest() == meta["sha1"]:
        _note_check(project, doc, "same", None, at)           # the very same bytes: nothing to read
        return {**out, "result": "same", "why": None}
    try:
        live_pages = _pages_of(live["content"], live["kind"], live["name"])
    except HandbookError as e:
        why = f"its text could not be read ({e})"
        _note_check(project, doc, "unreachable", why, at)
        return {**out, "result": "unreachable", "why": why}
    if prepare.is_stub(live_pages):
        why = "the live page now has no readable text (a page of pictures, or an empty page)"
        _note_check(project, doc, "unreachable", why, at)
        return {**out, "result": "unreachable", "why": why}
    diff = compare(saved_texts(project, doc), live_pages)
    if not diff["added"] and not diff["removed"]:
        _note_check(project, doc, "same", None, at)
        return {**out, "result": "same", "why": None}
    # Held or not is judged against the copy the rows read, which is an older one while a copy is held.
    using = in_use(docs, doc)
    held = gutted(diff if using == doc else compare(saved_texts(project, using), live_pages))

    new, n = fetch.doc_id_for_version(meta["url"], at), 1
    while fetch.has_document(project, new) or project.doc_dir(new).exists():     # two looks within a second
        n += 1
        new = fetch.doc_id_for_version(meta["url"], f"{at}#{n}")
    new_meta = fetch.new_meta(new, title=live["title"] or meta.get("title") or "", how=meta.get("how") or "plan",
                              url=meta["url"], host_type=meta.get("host_type") or "none",
                              sha1=hashlib.sha1(live["content"]).hexdigest(),
                              provided=meta.get("provided_by_owner") is True)
    new_meta["fetched"] = at
    new_meta["publisher"] = meta.get("publisher")
    for key in ("weak", "print_url"):
        if meta.get(key) is not None:
            new_meta[key] = meta[key]                  # the same source, marked the same way
    if live["text_source"]:
        new_meta["text_source"] = live["text_source"]
    said = fetch.page_date(live["content"], at[:10]) if live["kind"] == "html" else None
    if said:
        new_meta["date"], new_meta["date_basis"] = said, "page text"
    new_meta["earlier"] = {"doc": doc, "added": diff["added"], "removed": diff["removed"]}
    if held:
        new_meta["earlier"]["held"] = True
    try:
        fetch.store_document(project, new_meta, live["content"], readers.stored_extension(live["kind"], live["content"], live["name"]))
    except HandbookError as e:
        _note_check(project, doc, "unreachable", str(e), at)
        return {**out, "result": "unreachable", "why": str(e)}
    write_json(project.doc_dir(new) / "changes.json", {"earlier": doc, "later": new, "checked": at, **diff})
    prepare.prepare_document(project, new)
    if held:
        rows = {"moved": [], "kept": _rows_with(project, versions(project.documents(), new)[:-1]),
                "out_of_date": [], "checked": []}
    else:
        rows = move_rows(project, versions(project.documents(), new)[:-1], new)
    return {**out, "result": "changed", "why": None, "new": new, "added": diff["added"],
            "removed": diff["removed"], "held": held, **rows}


def _rows_with(project, docs) -> list[str]:
    """The rows that have any of `docs` filed, in units.json order."""
    docs = set(docs)
    return [u["id"] for u in project.read("units").get("units") or []
            if isinstance(u, dict) and u.get("id") and docs & set(project.found(u["id"]).get("documents") or [])]


def _held_copy(project, doc: str) -> tuple[dict, str]:
    """(documents, the held newest copy of `doc`'s page). Raises HandbookError when no copy is held."""
    docs = project.documents()
    if doc not in docs:
        raise HandbookError(f"There is no document {doc} in this project.")
    newest = latest(docs, doc)
    if not is_held(docs[newest]):
        raise HandbookError(f"{docs[newest].get('title') or newest} has no new copy waiting for a decision. Every row "
                            "already reads its newest copy, or keeps an earlier one because its cut-off has passed.")
    return docs, newest


def accept(project, doc: str) -> dict:
    """The owner accepts a held copy (most of the page's text had gone): rows switch to it as after any change,
    and their answers citing the earlier copies go out of date (not those a person checked). Returns the
    re-check's result shape, with "result" "accepted"."""
    from .folder import read_json
    docs, new = _held_copy(project, doc)
    path = project.doc_dir(new) / "meta.json"
    meta = read_json(path)
    meta["earlier"] = {**meta["earlier"], "held": False, "accepted": now_stamp()}
    write_json(path, meta)
    rows = move_rows(project, versions(project.documents(), new)[:-1], new)
    return {"doc": new, "title": meta.get("title") or new, "url": meta.get("url"), "result": "accepted", "why": None,
            "new": new, "added": meta["earlier"].get("added", 0), "removed": meta["earlier"].get("removed", 0), **rows}


def _day(stamp) -> str:
    """"23 October 2026" from a timestamp, or the text as it is."""
    from datetime import date
    try:
        d = date.fromisoformat(str(stamp or "")[:10])
    except ValueError:
        return str(stamp or "")
    return f"{d.day} {d:%B %Y}"


def look(project, doc: str) -> dict:
    """The owner wants the text that went from a page looked for: each row still on an earlier copy gets a note
    of what is missing (found `expected_not_found`, by "recheck", with the questions that cite it), its one
    second search opened again, and those answers marked to score again (not one a person checked). The next
    scoring run scores them, then its wider search (cmd_find.wider_search) looks for where the text moved and
    scores them again from what it finds. Nothing is fetched here, and no tokens are used until that run."""
    from .folder import read_json
    docs, new = _held_copy(project, doc)
    older = versions(docs, new)[:-1]
    first = docs[older[0]]
    title = first.get("title") or older[0]
    what = f"The text that was on ‘{title}’, which the site's new page no longer has"
    rows = []
    for row in _rows_with(project, older):
        cells = project.answers(row).get("cells") or {}
        qids = [q for q, c in cells.items() if isinstance(c, dict) and any(_cites(c, d) for d in older)]
        found = project.found(row)
        notes = found.setdefault("expected_not_found", [])
        if not any(isinstance(n, dict) and n.get("what") == what for n in notes):
            notes.append({"what": what, "where_looked": f"Its own address, which since {_day(docs[new].get('fetched'))} "
                                                        "shows a page with most of that text gone", "by": "recheck",
                          "questions": qids})
        found["row"] = row
        found["second_search"] = None
        project.write_found(row, found)
        rows.append(row)
        answers = project.answers(row)
        marked = False
        for q in qids:
            cell = answers["cells"][q]
            if cell.get("state") in LIVE and not left_alone(cell) and not cell.get("out_of_date"):
                cell["out_of_date"] = marked = True
        if marked:
            project.write_answers(row, answers)
    path = project.doc_dir(new) / "meta.json"
    meta = read_json(path)
    meta["earlier"] = {**meta["earlier"], "looking": now_stamp()}
    write_json(path, meta)
    return {"doc": new, "title": docs[new].get("title") or new, "url": docs[new].get("url"), "result": "looking",
            "why": None, "rows": rows}


def targets(project, docs=None, rows=None, every=False) -> list[str]:
    """The latest copy of each page asked for: the documents named, every document filed for the rows
    named, or every saved web page; in a stable order, each page once."""
    have = project.documents()
    later = later_map(have)
    asked: list[str] = []
    for d in docs or []:
        if d not in have:
            raise HandbookError(f"There is no document {d} in this project.")
        asked.append(d)
    if rows:
        from .fetch import resolve_rows
        for row in resolve_rows(project, rows):
            asked += [d for d in project.found(row).get("documents") or [] if d in have]
    if every:
        asked += [d for d, m in have.items() if isinstance(m, dict) and m.get("url") and m.get("kept") is True]
    return list(dict.fromkeys(latest(have, d, later) for d in asked))


def say(result: dict) -> str:
    """One plain paragraph for a re-check, for the owner."""
    name = f"{result['title']} ({result['doc']})"
    if result["result"] == "skipped":
        return f"{name} was not checked, because {result['why']}."
    if result["result"] == "unreachable":
        return f"Could not check {name} against the live page, because {result['why']}. The saved copy is unchanged."
    if result["result"] == "same":
        return f"{name} matches the live page. The date of this check is recorded."
    if result["result"] == "looking":
        rows = result.get("rows") or []
        return (f"The text that went from {name} will be looked for the next time you score "
                f"{', '.join(rows) if rows else 'its rows'}, which uses some of your Claude plan. The answers that cite "
                "it are marked to score again, and they keep reading the earlier copy until then.")
    if result["result"] == "accepted":
        words = [f"The new copy of {name} is now the one the scorer reads."]
    elif result.get("held"):
        return (f"Most of the text of {name} has gone from the live site ({change_words(result['added'], result['removed'])}). "
                f"The new copy is saved as {result['new']}, and the earlier copy is kept as it was. "
                + (f"{', '.join(result['kept'])} keep{'s' if len(result['kept']) == 1 else ''} reading the earlier copy "
                   if result["kept"] else "Rows keep reading the earlier copy ")
                + "until you choose. Either use the new copy, or have the text looked for where it went, in case "
                  "the site moved it to another page.")
    else:
        words = [f"{name} has changed on the live site ({change_words(result['added'], result['removed'])}). "
                 f"The new copy is saved as {result['new']}, and the earlier copy is kept as it was."]
    if result["moved"]:
        words.append(f"The scorer now reads the new copy for {', '.join(result['moved'])}.")
    if result["kept"]:
        words.append(f"{', '.join(result['kept'])} keep{'s' if len(result['kept']) == 1 else ''} the earlier copy, "
                     "as the cut-off has passed and it is the nearer to it.")
    if result["out_of_date"]:
        words.append(f"{len(result['out_of_date'])} answer{'s' if len(result['out_of_date']) != 1 else ''} citing "
                     "the earlier copy " + ("is" if len(result["out_of_date"]) == 1 else "are") + " out of date until scored again.")
    if result["checked"]:
        words.append(f"{len(result['checked'])} checked answer{'s' if len(result['checked']) != 1 else ''} citing it "
                     "stay as they are; the page shows the later copy beside them.")
    return " ".join(words)


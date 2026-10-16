"""Which edition of the benchmark tool this is: FREE or FULL, from one codebase.

The edition is the one word in `handbook/EDITION` ("free" or "full"). A folder without that file is a
development checkout and counts as full. `HB_EDITION=free|full` in the environment overrides the file; it
is for tests only.

What the free edition leaves out (the export script, scripts/export_tool.py, also leaves those files out of
the free copy, so the free copy has no dead code and no dead tabs):
- the Feedback tab and the business round (feedback.py, packs.py, replies.py, plainreasons.py,
  cmd_packs.py, cmd_feedback.py, views/feedback.js, the Feedback chat tools and set-up part);
- past editions beyond the latest one, and the trends and history views in the comparison;
- Word, Excel, PowerPoint, OpenDocument, email, JSON, XML, pictures and scanned pages as EVIDENCE.
  Set-up material (what the owner drops on the Set-up tab) is read in any format in both editions.

Callers use `is_full()`, `optional(name)` for a module the free copy may not have, and the plain-words
helpers `in_full(...)` and `evidence_refusal(...)`, so every refusal reads the same way.
"""
from __future__ import annotations
import importlib
import os
import re
from pathlib import Path

EDITIONS = ("free", "full")
FILE = Path(__file__).resolve().parents[2] / "EDITION"
FULL_VERSION = "the Full version of the benchmark tool"

# The modules only the Full edition has. The free export leaves their files out.
FULL_MODULES = ("feedback", "packs", "replies", "plainreasons", "cmd_packs", "cmd_feedback")

# The kinds of file (readers.KINDS) read as evidence in the free edition.
FREE_EVIDENCE_KINDS = ("pdf", "html", "text")


def edition() -> str:
    """"free" or "full": HB_EDITION if set to one of them, else the word in the EDITION file, else "full"."""
    forced = os.environ.get("HB_EDITION", "").strip().lower()
    if forced in EDITIONS:
        return forced
    try:
        word = FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "full"
    return word if word in EDITIONS else "full"


def is_full() -> bool:
    return edition() == "full"


def is_free() -> bool:
    return not is_full()


def in_full(what: str) -> str:
    """`what` (a short phrase such as "Following results across several past editions") as a sentence that
    says it is in the Full version: "<what> is in the Full version of the benchmark tool."."""
    what = what.strip().rstrip(".")
    return f"{what} is in {FULL_VERSION}."


def optional(name: str):
    """The module `handbook.<name>` when this edition has it, else None. The free edition has none of
    FULL_MODULES, even in a checkout that still holds their files (tests run the free edition on the full
    code); a free copy that was exported without them cannot import them either."""
    if name in FULL_MODULES and not is_full():
        return None
    try:
        return importlib.import_module(f"{__package__ or 'handbook'}.{name}")
    except ImportError:
        return None


def key_file() -> Path:
    """Where the Full edition keeps the owner's personal download key, so later updates work: ~/.benchmark-tool-key
    (mode 600). HB_KEY_FILE overrides the place, for tests. The key is never printed or logged."""
    return Path(os.environ.get("HB_KEY_FILE") or Path.home() / ".benchmark-tool-key")


def has_feedback() -> bool:
    """Whether the feedback round exists here: the Full edition, with its modules in place."""
    return optional("feedback") is not None


# What each kind of file is called, and what to do instead, for the free edition's refusal.
_EVIDENCE_WORDS = {
    "docx": ("Word files", "Save it as a PDF to use it here."),
    "doc": ("Word files", "Save it as a PDF to use it here."),
    "rtf": ("Rich text files", "Save it as a PDF to use it here."),
    "odt": ("OpenDocument files", "Save it as a PDF to use it here."),
    "sheet": ("Excel files", "Save it as a PDF to use it here."),
    "xls": ("Excel files", "Save it as a PDF to use it here."),
    "ods": ("OpenDocument files", "Save it as a PDF to use it here."),
    "pptx": ("PowerPoint files", "Save it as a PDF to use it here."),
    "odp": ("OpenDocument files", "Save it as a PDF to use it here."),
    "eml": ("Email files", "Save it as a PDF to use it here."),
    "msg": ("Email files", "Save it as a PDF to use it here."),
    "json": ("JSON files", "Save it as a PDF or a text file to use it here."),
    "xml": ("XML files", "Save it as a PDF or a text file to use it here."),
    "image": ("Pictures", "Save the words in it as a PDF or a text file to use them here."),
}


def evidence_refusal(kind: str | None) -> str | None:
    """Why the free edition does not take a file of this kind (a key of readers.KINDS) as evidence, in plain
    words, or None when it does (always None in the Full edition)."""
    if is_full() or kind is None or kind in FREE_EVIDENCE_KINDS:
        return None
    noun, advice = _EVIDENCE_WORDS.get(kind, ("Files like this", "Save it as a PDF to use it here."))
    return f"{noun} are read as evidence in {FULL_VERSION}. {advice}"


def scanned_pages_note(count: int) -> str:
    """The note for a PDF whose `count` scanned pages the free edition leaves unread."""
    pages = f"{count} page{'s' if count != 1 else ''}"
    return (f"{pages} of this PDF {'is a' if count == 1 else 'are'} scanned "
            f"picture{'s' if count != 1 else ''} of text, so {'it was' if count == 1 else 'they were'} not read. "
            f"Reading scanned pages as evidence is in {FULL_VERSION}.")


# --- text only the Full edition has ------------------------------------------------------------
# A part of a guide the free edition must not carry is wrapped in two HTML comments, FULL-ONLY and
# /FULL-ONLY. The export script drops the block (comments and text) from the free copy, and the page's
# Claude text (serve.page_claude_text) drops it when the edition is free. In the Full edition the
# comments are just comments.
_BLOCK = re.compile(r"[ \t]*<!--\s*FULL-ONLY\b[^>]*-->.*?<!--\s*/FULL-ONLY\s*-->[ \t]*\n?", re.S)
_MARKER = re.compile(r"[ \t]*<!--\s*/?FULL-ONLY\b[^>]*-->[ \t]*\n?")


def strip_full_only(text: str) -> str:
    """`text` without its FULL-ONLY blocks (the free edition's copy)."""
    return _BLOCK.sub("", text)


def drop_markers(text: str) -> str:
    """`text` with the FULL-ONLY comments taken out and what they wrap kept (the Full edition's copy)."""
    return _MARKER.sub("", text)


def for_this_edition(text: str) -> str:
    """`text` as this edition reads it."""
    return text if is_full() else strip_full_only(text)


def replaced_edition_line(new, old) -> str:
    """The one plain line for the owner when, in the free edition, a newer edition replaces the held-back one."""
    return f"The {new} edition now replaces {old} as the one compared with."


def past_editions_refusal(held: str | None, new: str | None) -> str:
    """Why the free edition does not hold back an older past edition (`held` is the one it has, `new` the
    one that was offered). A newer one replaces it instead (replaced_edition_line)."""
    have = f"the {held} edition" if held else "a past edition"
    return (f"The free version of the benchmark tool compares with one previous edition, and {have} is already held "
            f"back{f', so the {new} edition was not added' if new else ''}. "
            f"Keeping more past editions, to follow results over time, is in {FULL_VERSION}.")

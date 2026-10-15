"""Is a quote word for word on a page? The one test both sides of the quote check use.

`checks.py` runs it over the project folder after a scorer's answers are stored, and the kit
carries a copy of this file as `check_quotes.py` so the scorer can run the same test on its own
`answers.json` before it finishes (`python3 check_quotes.py` from inside the kit). The two can
only agree if they are the same code, so this module is standard library only and imports
nothing from the package: the copy has to run on its own.

It also gives the content hash of a page file and of a document's capture (its pages as the
scorer saw them), which is how an answer is bound to the text it was scored from.

`tokens`, `longest_run` and `is_grounded` were copied from scripts/audit.py, unchanged.
"""
from __future__ import annotations
import hashlib
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path

MIN_RUN = 5  # a grounded quote shares at least this many consecutive words with the source
SHORT_MIN = 3  # a quote shorter than MIN_RUN passes only if it appears whole, and has >= 3 words

# CJK ranges (Hiragana/Katakana, CJK ext-A, unified ideographs, compatibility, Hangul).
# These scripts have no inter-word spaces, so we segment each character as its own token;
# a run of MIN_RUN consecutive characters is then the grounding bar, as words are elsewhere.
_CJK = "぀-ヿ㐀-䶿一-鿿豈-﫿가-힯"


def tokens(s: str) -> list[str]:
    s = (s or "").replace("&amp;", "&")
    s = re.sub(f"([{_CJK}])", r" \1 ", s)  # space out CJK so each char is a token
    s = re.sub(r"\W+", " ", s, flags=re.UNICODE)
    return s.casefold().split()


def longest_run(evidence: str, src_join: str) -> int:
    """Longest run of consecutive evidence words that appears contiguously in the source."""
    et = tokens(evidence)
    best = 0
    for i in range(len(et)):
        k = i
        while k < len(et) and (" " + " ".join(et[i:k + 1]) + " ") in src_join:
            k += 1
            best = max(best, k - i)
    return best


def is_grounded(evidence: str, src_join: str) -> bool:
    """A quote is grounded if it shares a MIN_RUN-word run with the source. A whole quote
    shorter than MIN_RUN (a short sentence, e.g. 4 words of Polish) passes only if every word
    of it appears contiguously in the source and it has at least SHORT_MIN words.

    It stops at the first MIN_RUN-word window found in the source instead of measuring the
    longest run: the same verdict as `longest_run(...) >= MIN_RUN`, because any run that long
    begins with such a window."""
    et = tokens(evidence)
    n = len(et)
    if n < MIN_RUN:
        return n >= SHORT_MIN and (" " + " ".join(et) + " ") in src_join
    return any((" " + " ".join(et[i:i + MIN_RUN]) + " ") in src_join
               for i in range(n - MIN_RUN + 1))


# --- text read from a picture --------------------------------------------------------------------

OCR_SIMILARITY = 0.78  # two words of four letters or more are "the same word" read from a picture at this ratio


def _similar(a: str, b: str) -> bool:
    """Whether two words are one word that a text reader may have misread: equal, or (for words of four
    characters or more with no figure in them) alike enough. A figure must match exactly: "1130" is not
    "1180"."""
    if a == b:
        return True
    if len(a) < 4 or len(b) < 4 or any(ch.isdigit() for ch in a + b):
        return False
    return SequenceMatcher(None, a, b).ratio() >= OCR_SIMILARITY


def is_grounded_ocr(evidence: str, src_join: str) -> bool:
    """The test for a page whose words were read from a picture, which can hold a wrong letter ("Whooper"
    for "Whopper"). Like `is_grounded`, but a run of MIN_RUN words counts when all but one of them are the
    page's words or misreadings of them, in order (a quote of fewer than MIN_RUN words, when every one is).
    Figures and words shorter than four characters must match exactly, and a figure is never the one slip. `src_join` is `source_join` of the
    picture-read pages."""
    et = tokens(evidence)
    n = len(et)
    if n < SHORT_MIN:
        return False
    window = min(n, MIN_RUN)
    need = window if n < MIN_RUN else window - 1
    src = src_join.split()
    index: dict[str, list[int]] = {}
    for pos, tok in enumerate(src):
        index.setdefault(tok, []).append(pos)
    vocab = list(index)
    like: dict[str, list[str]] = {}

    def alike(word: str) -> list[str]:
        if word not in like:
            near = [word] if word in index else []
            if len(word) >= 4 and not any(ch.isdigit() for ch in word):
                near += [v for v in get_close_matches(word, vocab, n=6, cutoff=OCR_SIMILARITY) if v != word]
            like[word] = near
        return like[word]
    for i in range(n - window + 1):
        win = et[i:i + window]
        starts = {pos - j for j, w in enumerate(win) for v in alike(w) for pos in index[v]}
        for start in starts:
            if start < 0 or start + window > len(src):
                continue
            if any(any(ch.isdigit() for ch in w) and w != src[start + j] for j, w in enumerate(win)):
                continue                    # a figure is never the allowed slip
            if sum(_similar(w, src[start + j]) for j, w in enumerate(win)) >= need:
                return True
    return False


# --- text normalisation ------------------------------------------------------------------------

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
_QUOTES = {**dict.fromkeys(map(ord, "‘’‚‛′ʼ"), "'"),
           **dict.fromkeys(map(ord, "“”„‟″"), '"')}
_LINE_END_HYPHEN = re.compile(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)")


def _clean(text: str, join: str | None) -> str:
    """NFKC, soft hyphens out, curly quotes and dashes unified, then a hyphen at a line end
    replaced by `join` (None leaves it), then whitespace collapsed."""
    s = unicodedata.normalize("NFKC", text or "").replace("­", "")
    s = s.translate(_DASHES).translate(_QUOTES)
    if join is not None:
        s = _LINE_END_HYPHEN.sub(join, s)
    return re.sub(r"\s+", " ", s).strip()


def normalise(text: str) -> str:
    """Both sides of a quote check go through this: NFKC; soft hyphens removed; a hyphen at a line
    end joined (`wel-` + newline + `fare` becomes `welfare`); curly quotes and dashes unified;
    whitespace collapsed."""
    return _clean(text, "")


def source_join(texts) -> str:
    """The text the quotes are matched against: every page, as tokens, with a word broken by a
    line-end hyphen read both ways (joined, and kept as a real hyphen), since the page alone
    cannot say whether `well-` + `known` was one word."""
    text = "\n".join(t or "" for t in texts)
    variants = [_clean(text, "")]
    if _LINE_END_HYPHEN.search(unicodedata.normalize("NFKC", text).translate(_DASHES)):
        variants.append(_clean(text, "-"))
    # the lone "#" cannot match a quote token, so no run can cross from one reading to the other
    return " " + " # ".join(" ".join(tokens(v)) for v in variants) + " "


# --- captures ----------------------------------------------------------------------------------

def page_hash(text: str) -> str:
    """The SHA-1 of a page's text, exactly as the kit writes it to its page file."""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def capture_id(texts) -> str:
    """The id of a document's capture: the SHA-1 over its page hashes, in page order. The same
    pages give the same id wherever they are hashed, in the project or in a kit."""
    return hashlib.sha1("\n".join(page_hash(t) for t in texts).encode("ascii")).hexdigest()


# --- the scorer's self-check -------------------------------------------------------------------

_PAGE_FILE = re.compile(r"^p(\d+)\.txt$")


def kit_pages(kit: Path) -> dict[str, dict[int, str]]:
    """{doc: {page number: text}} for every page file in `kit/docs/`."""
    out: dict[str, dict[int, str]] = {}
    for folder in sorted((kit / "docs").iterdir()) if (kit / "docs").is_dir() else []:
        if not folder.is_dir():
            continue
        pages = {}
        for f in folder.iterdir():
            m = _PAGE_FILE.match(f.name)
            if m and f.is_file():
                pages[int(m.group(1))] = f.read_text(encoding="utf-8")
        out[folder.name] = pages
    return out


def kit_ocr_pages(kit: Path) -> dict[str, set[int]]:
    """{doc: page numbers read from a picture} from each document's meta.json in `kit/docs/`."""
    out: dict[str, set[int]] = {}
    for folder in sorted((kit / "docs").iterdir()) if (kit / "docs").is_dir() else []:
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pages = meta.get("ocr_pages") if isinstance(meta, dict) else None
        if isinstance(pages, list) and pages:
            out[folder.name] = {n for n in pages if isinstance(n, int)}
    return out


def check_kit(kit: Path, answers_path: Path | None = None) -> list[dict]:
    """Every quote in the answers file (`kit/answers.json` unless `answers_path` says otherwise)
    that is not word for word on a page of its document, as `{"question", "evidence", "doc",
    "page", "quote", "why"}`. `why` names the trouble: a document not in the kit, a page that is
    not one of its page files, or the words not found (then with the page that holds the longest
    run of them, when any does)."""
    answers = json.loads((answers_path or kit / "answers.json").read_text(encoding="utf-8"))
    docs = kit_pages(kit)
    ocr_pages = kit_ocr_pages(kit)
    joins: dict[str, str] = {}
    ocr_joins: dict[str, str] = {}
    problems = []
    for qid, cell in sorted((answers.get("cells") or {}).items()):
        for i, e in enumerate(cell.get("evidence") or [] if isinstance(cell, dict) else [], start=1):
            if not isinstance(e, dict):
                continue
            doc, page, quote = e.get("doc"), e.get("page"), e.get("quote") or ""
            item = {"question": qid, "evidence": i, "doc": doc, "page": page, "quote": quote}
            if doc not in docs:
                problems.append({**item, "why": f"document {doc} is not in this kit"})
                continue
            if not docs[doc]:
                problems.append({**item, "why": f"document {doc} has no page files, so it cannot be quoted"})
                continue
            if page not in docs[doc]:
                problems.append({**item, "why": f"page {page} is not a page file of {doc} "
                                               f"(it has p{min(docs[doc]):03d} to p{max(docs[doc]):03d})"})
                continue
            if doc not in joins:
                joins[doc] = source_join(docs[doc][n] for n in sorted(docs[doc]))
            if is_grounded(normalise(quote), joins[doc]):
                continue
            if doc in ocr_pages:
                if doc not in ocr_joins:
                    ocr_joins[doc] = source_join(docs[doc][n] for n in sorted(docs[doc]) if n in ocr_pages[doc])
                if is_grounded_ocr(normalise(quote), ocr_joins[doc]):
                    continue                # a small misreading on a page read from a picture
            best = max(((longest_run(normalise(quote), source_join([t])), n) for n, t in docs[doc].items()),
                       default=(0, None))
            where = (f"; the longest run of its words ({best[0]}) is on page {best[1]}" if best[0] else
                     "; none of its words run together on any page")
            note = (" (some of its pages were read from a picture: copy the words as the page file has them)"
                    if doc in ocr_pages else "")
            problems.append({**item, "why": f"not word for word on any page of {doc}{where}{note}"})
    return problems


def main(argv=None) -> int:
    """`python3 check_quotes.py [answers.json | second/answers.json | KIT]` from inside the kit: the
    answers file (default answers.json here), checked against the page files of the kit that
    holds it (the nearest folder up from the file with a docs/ folder)."""
    argv = sys.argv[1:] if argv is None else argv
    target = Path(argv[0]) if argv else Path.cwd()
    answers = target if target.is_file() or target.suffix == ".json" else target / "answers.json"
    kit = next((d for d in (answers.parent, *answers.parent.resolve().parents) if (d / "docs").is_dir()),
               answers.parent)
    if not answers.is_file():
        print(f"There is no {answers.name} in {answers.parent.resolve()}. Write it first, then run this "
              "check from the kit folder.")
        return 2
    try:
        problems = check_kit(kit, answers)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"{answers.name} is not valid JSON ({e}). Fix the file and run this again.")
        return 2
    if not problems:
        print("Every quote is word for word on a page of its document.")
        return 0
    print(f"{len(problems)} quote(s) to fix. Copy the words exactly from the page file, or cite the page that holds them:")
    for p in problems:
        shown = p["quote"] if len(p["quote"]) <= 90 else p["quote"][:87] + "..."
        print(f"  question {p['question']}, evidence {p['evidence']} ({p['doc']} p{p['page']}): {p['why']}")
        print(f"    quote: {shown!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

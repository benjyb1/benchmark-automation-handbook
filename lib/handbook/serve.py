"""`hb serve`: the project's page in the owner's own browser, with everything it saves applied at once.

    LocalServer(project, port=0).serve_forever()

Bound to 127.0.0.1 only, for the owner on this computer; never hosted or shared. The first address
carries a random token (`/?t=TOKEN`), which is swapped for a cookie, and every API call must also
send the token in a header, so other programs on the computer (and web pages) cannot drive it.

What it serves:
- `/`: the built viewer (`viewer/index.html`) with `local.js` loaded first. `local.js` gives the page
  the same `window.claude` the published Artifact has (db, user, assets, downloads, sample), backed
  by this server. The Artifact version of the page is unchanged: it never loads `local.js`.
- `/bundle.json`, `/downloads.json`, `/evidence/<row>.json`: the build's data files.
- `/api/...`: the store (`localstore.Store`), the owner's name, uploads (saved under `inbox/`),
  Claude calls (`sampler.Sampler`), the state the page polls, and "Download a copy".

What happens when the owner saves something (the Worker):
- edits and rule decisions: `apply-edits`; run bar requests: `control apply`; corrections:
  `apply-corrections`; documents sent from a step's brief: `add-document` or `fetch`, then
  `prepare`; a report saved in the page: `facts`, written to report/report.md, `check-report`; a
  chart confirmed in the corner chat: `chart add` (or `chart remove`); a reason accepted under Ask
  Claude why: `compare.fill_reason`.
  Then `build`, and the page refreshes its data.
- a re-run request (`{action: "rerun", rule, rows, questions}`): one background job. For each row,
  `kit --questions`, `score` (a clean headless scorer on the owner's plan), `run usage`,
  `ingest-answers`, `check` and `build`; then `run finish` and `control set --rerun-done`.
- a correction waiting on a link or a file (one on a saved page is checked by the page itself):
  the blind re-read as the run skill says, `kit --look`, `score`, `apply-corrections --record`.
Heavy work runs as one job at a time; the page polls its progress.
- the Feedback tab (`/api/feedback/...`, see `Handler._feedback`): make packs, mark them sent, read
  dropped reply files, decide a point, undo it, set a stage, move or remove a file, start a unit
  over. Each call runs the matching `feedback.py`, `packs.py` or `replies.py` function and then
  rebuilds before it answers, so the page's next look at the data is the new one.
"""
from __future__ import annotations
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import readers
from .folder import HandbookError, Project, read_json
from .localstore import Config, Store, home, user_id
from .page import VIEWER_SRC, json_for_script
from .sampler import Sampler, SampleError

HB = Path(__file__).resolve().parents[1] / "hb.py"
LOCAL_JS = VIEWER_SRC / "js" / "local.js"
PAGE_CLAUDE = Path(__file__).resolve().parents[2] / "guide" / "page-claude.md"
DATA_TAG = '<script type="application/json" id="hb-data">'
WATCHED = ("control", "edits", "decisions", "corrections", "supplied", "report", "charts", "reasons", "setup")
TICK_S = 3.0
MAX_UPLOAD = 40 * 1024 * 1024
SCORE_TIMEOUT_S = 1900
ROW_RE = re.compile(r"^[a-z0-9-]+\Z")
ASSET_RE = re.compile(r"^[a-f0-9]{16,32}\Z")
# The content types an upload may carry: every kind the tool reads (readers.UPLOAD_MIME is the one list, which
# the upload boxes' own lists are checked against) and the other types browsers send for them.
UPLOAD_TYPES = {}
for _ext, _mime in readers.UPLOAD_MIME.items():
    UPLOAD_TYPES.setdefault(_mime.lower(), _ext)
UPLOAD_TYPES.update(readers.MIME_ALIASES)
UPLOAD_GENERIC = ("", "application/octet-stream", "binary/octet-stream")   # a browser that did not know the type


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page_claude_text() -> str:
    """handbook/guide/page-claude.md without its HTML comments; empty when the file is missing."""
    try:
        text = PAGE_CLAUDE.read_text(encoding="utf-8")
    except OSError:
        return ""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


def open_folder(project: Project, folder: str) -> dict:
    """Show a folder of the project (the packs just made) in the computer's file manager. Only folders
    inside the project's `packs` folder open; anything else is refused."""
    path = (project.path / folder).resolve()
    packs_dir = (project.path / "packs").resolve()
    if path != packs_dir and packs_dir not in path.parents:
        raise HandbookError("Only the packs folder can be opened from here.")
    if not path.is_dir():
        raise HandbookError("That folder is not there any more.")
    cmd = ["open", str(path)] if sys.platform == "darwin" else ["explorer", str(path)] if sys.platform.startswith("win") \
        else ["xdg-open", str(path)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except OSError:
        return {"opened": False, "path": str(path)}
    return {"opened": True, "path": str(path)}


# ---- Running the existing commands ----------------------------------------------------------------

class Commands:
    """`python3 hb.py <command>` for one project, one write at a time (`lock`)."""

    def __init__(self, project: Project):
        self.project = project
        self.lock = threading.RLock()
        self.running: set[subprocess.Popen] = set()

    def run(self, *args, project: bool = True, timeout: int = 900) -> tuple[int, str]:
        """Each command runs in its own process group, so stopping the server also stops a scorer
        it started (`stop_all`), and no Claude session is left running on its own."""
        cmd = [sys.executable, str(HB), *args] + (["--project", str(self.project.path)] if project else [])
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             text=True, encoding="utf-8", start_new_session=True)
        self.running.add(p)
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill(p)
            p.communicate()
            return 124, f"hb {args[0]} ran for more than {timeout // 60} minutes and was stopped."
        finally:
            self.running.discard(p)
        return p.returncode, (out or "").strip()

    @staticmethod
    def _kill(p: subprocess.Popen) -> None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (OSError, AttributeError):
            try:
                p.terminate()
            except OSError:
                pass

    def stop_all(self) -> None:
        for p in list(self.running):
            if p.poll() is None:
                self._kill(p)

    def must(self, *args, **kw) -> str:
        code, out = self.run(*args, **kw)
        if code != 0:
            raise HandbookError(out or f"hb {args[0]} failed.")
        return out


def last_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


# ---- The worker: applying saves, and the one background job ---------------------------------------

class Worker:
    def __init__(self, project: Project, store: Store, sampler_log=None):
        self.project = project
        self.store = store
        self.cmd = Commands(project)
        # One feedback write at a time (the Feedback tab's calls and the plain-reasons job); always taken
        # before `cmd.lock`.
        self.feedback_lock = threading.RLock()
        self.version = 0                    # goes up after every build; the page reloads its data then
        self.activity: deque = deque(maxlen=30)
        self.job: dict | None = None
        self.queue: list[dict] = []
        self.queued_ids: set[str] = set()
        self.last_job: dict | None = None
        self.seen: dict[str, dict] = {c: {} for c in WATCHED}
        self.versions: dict[str, str] = {}
        self.wake = threading.Event()
        self.job_wake = threading.Event()
        self.stopping = False
        self.problem: str | None = None
        self.last_sample: dict | None = None
        self.api_key_source: str | None = None
        self.usage_log = project.path / "page-data" / "_log" / "usage.jsonl"

    # -- notes for the page ----------------------------------------------------------------------

    def note(self, text: str, tone: str = "neutral", detail: str | None = None) -> None:
        self.activity.appendleft({"at": now_iso(), "text": text, "tone": tone, "detail": detail})

    def log_usage(self, entry: dict) -> None:
        if entry.get("kind") == "sample":
            self.last_sample = {k: entry.get(k) for k in ("processed", "cache_read", "fresh_input", "output",
                                                          "turns", "resumed", "tier", "seconds")}
            if entry.get("api_key_source"):
                self.api_key_source = entry["api_key_source"]
        try:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": now_iso(), **entry}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def bundle(self) -> dict:
        return read_json(self.project.path / "viewer" / "bundle.json", {}) or {}

    def build(self) -> bool:
        with self.cmd.lock:
            code, out = self.cmd.run("build")
        if code != 0:
            self.problem = out
            self.note("The page could not be rebuilt.", "critical", out[-600:])
            return False
        self.problem = None
        self.version += 1
        return True

    # -- the loops -------------------------------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._apply_loop, daemon=True, name="hb-apply").start()
        threading.Thread(target=self._job_loop, daemon=True, name="hb-job").start()

    def poke(self) -> None:
        self.wake.set()

    def _apply_loop(self) -> None:
        while not self.stopping:
            try:
                self.apply_saved()
            except Exception as e:  # noqa: BLE001 - one bad save must not stop the server
                self.note("Something the page saved could not be applied.", "critical",
                          f"{type(e).__name__}: {e}")
                traceback.print_exc()
            self.wake.wait(TICK_S)
            self.wake.clear()

    def _job_loop(self) -> None:
        while not self.stopping:
            job = self.queue.pop(0) if self.queue else None
            if not job:
                self.job_wake.wait(2.0)
                self.job_wake.clear()
                continue
            self.job = job
            job.update({"status": "running", "started": now_iso()})
            try:
                job["run"](job)
                job["status"] = "done"
            except Exception as e:  # noqa: BLE001
                job["status"] = "failed"
                job["line"] = f"Stopped: {e}"
                self.note(job["title"] + " stopped.", "critical", str(e)[-800:])
                traceback.print_exc()
            finally:
                job["finished"] = now_iso()
                self.last_job = job
                self.job = None
                self.queued_ids.discard(job["key"])
                self.poke()

    def enqueue(self, key: str, kind: str, title: str, run) -> bool:
        if key in self.queued_ids:
            return False
        self.queued_ids.add(key)
        self.queue.append({"key": key, "kind": kind, "title": title, "run": run, "status": "queued",
                           "done": 0, "total": 0, "line": "Waiting to start", "tokens": 0, "steps": []})
        self.job_wake.set()
        return True

    # -- applying what the page saved --------------------------------------------------------------

    def _new(self, collection: str, docs: list[dict]) -> list[dict]:
        seen = self.seen[collection]
        fresh = []
        for d in docs:
            sig = json.dumps(d, sort_keys=True, ensure_ascii=False)
            if seen.get(d["id"]) != sig:
                fresh.append(d)
            seen[d["id"]] = sig
        return fresh

    def _payload_file(self, payload: dict) -> Path:
        f = tempfile.NamedTemporaryFile("w", suffix=".json", prefix="hb-page-", delete=False, encoding="utf-8")
        json.dump(payload, f, ensure_ascii=False)
        f.close()
        return Path(f.name)

    def _run_with(self, payload: dict, *args) -> tuple[int, str]:
        path = self._payload_file(payload)
        try:
            return self.cmd.run(*args, "--file", str(path))
        finally:
            path.unlink(missing_ok=True)

    def apply_saved(self) -> bool:
        """Apply whatever the page saved since the last look. Returns True when it rebuilt the page."""
        changed = [c for c in WATCHED if self.store.version(c) != self.versions.get(c)]
        if not changed:
            self._queue_rereads(None)
            return False
        rebuild = False
        with self.cmd.lock:
            for c in changed:
                self.versions[c] = self.store.version(c)
            docs = {c: self.store.list(c) for c in changed}
            if "control" in docs and self._new("control", docs["control"]):
                code, out = self._run_with({"requests": docs["control"]}, "control", "apply")
                self._said(code, out, "Your run request is applied.", "A run request could not be applied.")
                rebuild = True
            ed = {k: docs.get(k) for k in ("edits", "decisions") if k in docs}
            if ed and any([self._new(k, v) for k, v in ed.items()]):
                payload = {"edits": self.store.list("edits"), "decisions": self.store.list("decisions")}
                code, out = self._run_with(payload, "apply-edits", "--via", "viewer")
                self._said(code, out, self._edit_words(out), "Your change could not be applied.")
                rebuild = True
            if "corrections" in docs and self._new("corrections", docs["corrections"]):
                code, out = self._run_with({"corrections": [self._with_local(d) for d in docs["corrections"]]},
                                           "apply-corrections", "--json")
                self._corrections_said(code, out)
                rebuild = True
            if "supplied" in docs and self._new("supplied", docs["supplied"]):
                rebuild = self._take_supplied(docs["supplied"]) or rebuild
            if "report" in docs and docs["report"] and self._new("report", docs["report"]):
                rebuild = self._adopt_report(docs["report"]) or rebuild
            if "charts" in docs and self._new("charts", docs["charts"]):
                rebuild = self._take_charts(docs["charts"]) or rebuild
            if "reasons" in docs and self._new("reasons", docs["reasons"]):
                rebuild = self._take_reasons(docs["reasons"]) or rebuild
            if "setup" in docs and self._new("setup", docs["setup"]):
                self._take_setup(docs["setup"])
            if rebuild:
                self.build()
        self._queue_reruns()
        self._queue_rereads(None)
        return rebuild

    def _said(self, code: int, out: str, good: str, bad: str) -> None:
        if code == 0:
            self.note(good, "good", out[-800:] or None)
        else:
            self.note(bad, "critical", out[-800:] or None)

    @staticmethod
    def _edit_words(out: str) -> str:
        m = re.search(r"Applied (\d+) of", out)
        if m and m.group(1) == "0":
            return "Nothing new to apply."
        return "Your change is applied and the page is up to date."

    def _corrections_said(self, code: int, out: str) -> None:
        if code != 0:
            self.note("Your correction could not be applied.", "critical", out[-800:])
            return
        try:
            result = json.loads(last_line(out))
        except ValueError:
            self.note("Your correction is in.", "good", out[-800:])
            return
        for r in result.get("applied") or []:
            where = f"{r.get('row')} question {r.get('qid')}"
            v = r.get("verdict") or {}
            if r.get("status") == "checked" and v.get("agree") is True:
                self.note(f"Correction on {where}: Claude agrees with your answer.", "good", v.get("why"))
            elif r.get("status") == "checked" and v.get("agree") is None:
                self.note(f"Correction on {where}: Claude read it again with your evidence.", "good", v.get("why"))
            elif r.get("status") == "checked":
                self.note(f"Correction on {where}: Claude disagrees. Your answer stands.", "warning", v.get("why"))
            elif r.get("status") == "kept":
                self.note(f"Correction on {where}: your answer is kept.", "good")
            else:
                self.note(f"Correction on {where}: the quote checks out. Claude reads it again next.", "good")
        for s in result.get("skipped") or []:
            self.note("A correction was not applied yet.", "warning", s.get("why"))

    # -- uploads, documents sent from the page, the report --------------------------------------------

    def asset_path(self, asset_id: str) -> Path | None:
        if not isinstance(asset_id, str) or not ASSET_RE.match(asset_id):
            return None
        hits = sorted((self.project.path / "inbox" / "uploads").glob(asset_id + "--*"))
        return hits[0] if hits else None

    def _with_local(self, doc: dict) -> dict:
        d = json.loads(json.dumps(doc))
        ev = d.get("evidence")
        if isinstance(ev, dict) and ev.get("asset") and not ev.get("local"):
            path = self.asset_path(ev["asset"])
            if path:
                ev["local"] = str(path)
        return d

    def _take_supplied(self, docs: list[dict]) -> bool:
        filed = False
        for d in docs:
            rows = [r for r in d.get("rows") or [] if isinstance(r, str) and ROW_RE.match(r)]
            if not rows:
                continue
            ok = True
            if d.get("asset"):
                path = self.asset_path(d["asset"])
                if not path:
                    self.note("A document you sent could not be found among the uploads.", "warning")
                    continue
                code, out = self.cmd.run("add-document", str(path), "--rows", ",".join(rows),
                                         *(["--title", str(d["what"])] if d.get("what") else []))
                ok = code == 0
            elif d.get("link"):
                for row in rows:
                    code, out = self.cmd.run("fetch", str(d["link"]), "--row", row, "--how", "provided", timeout=300)
                    ok = ok and code == 0
            else:
                continue
            if ok:
                self.cmd.run("prepare", timeout=1800)
                self.store.delete("supplied", d["id"])
                self.note(f"Filed what you sent for {', '.join(rows)}.", "good", last_line(out))
                filed = True
            else:
                self.note("What you sent could not be filed.", "warning", out[-600:])
        return filed

    def _take_charts(self, docs: list[dict]) -> bool:
        """A chart the owner confirmed in the corner chat: {chart: {id, title, description, dataset,
        spec}} is added (or replaces the chart with that id) through `hb chart add`; {remove: id}
        takes one away. Each document is deleted once handled, whether or not it worked."""
        changed = False
        for d in sorted(docs, key=lambda x: str(x.get("at") or "")):
            if isinstance(d.get("remove"), str):
                code, out = self.cmd.run("chart", "remove", "--id", d["remove"])
                good = "The chart is removed."
            elif isinstance(d.get("chart"), dict):
                code, out = self._run_with(d["chart"], "chart", "add")
                good = "Your chart is on the Results tab."
            else:
                code, out, good = 1, "", ""
            self.store.delete("charts", d["id"])
            if code == 0:
                self.note(good, "good")
                changed = True
            else:
                self.note("The chart could not be added.", "warning", out[-800:] or None)
        return changed

    def _take_setup(self, docs: list[dict]) -> None:
        """"Draft my set-up" (`{action: "draft"}`): one background job running `hb draft-setup`."""
        for d in docs:
            self.store.delete("setup", d["id"])
        if any(d.get("action") == "draft" for d in docs):
            self.enqueue("setup-draft:" + now_iso(), "setup", "Drafting your set-up", self.draft_setup)

    def draft_setup(self, job: dict) -> None:
        job.update({"total": 1, "line": "Claude is reading your files and drafting the set-up"})
        code, out = self.cmd.run("draft-setup", "--json", timeout=2800)
        try:
            got = json.loads(out.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            got = {}
        job["tokens"] += int(got.get("tokens") or 0)
        self.log_usage({"kind": "setup-draft", "job": job["key"], "processed": job["tokens"], "ok": code == 0})
        with self.cmd.lock:
            self.build()
        job.update({"done": 1, "line": "Done"})
        if code == 0:
            self.note("Your set-up is drafted. Check it on the Set-up tab.", "good", got.get("result"))
        else:
            self.note("The set-up draft stopped before it finished.", "warning", (got.get("result") or out)[-800:])

    def plain_reasons(self, job: dict, ids: list[str]) -> None:
        """The plain-reasons job (`POST /api/feedback/plain-reasons`): one small Claude call per unit, outside
        any lock, then its result kept under the locks. Two failures in a row stop it (a rate limit, a
        broken sign-in) with what was done kept; the page shows progress as for any job."""
        from . import feedback, plainreasons
        names = {uid: u.get("name") or uid for uid, u in feedback.units_by_id(self.project).items()}
        job.update({"total": len(ids), "done": 0, "line": "Starting"})
        problems, in_a_row, done = [], 0, 0
        for n, uid in enumerate(ids, 1):
            if self.stopping:
                break
            job["line"] = f"Writing the reasons for {names.get(uid, uid)} ({n} of {len(ids)})"
            try:
                reasons, usage = plainreasons.ask(self.project, uid)
                with self.feedback_lock, self.cmd.lock:
                    got = plainreasons.save(self.project, uid, reasons, usage)
                job["tokens"] += got["tokens"]
                in_a_row, done = 0, done + 1
            except HandbookError as e:
                problems.append(f"{names.get(uid, uid)}: {e}")
                in_a_row += 1
                if plainreasons.spent(e):
                    with self.feedback_lock, self.cmd.lock:
                        job["tokens"] += plainreasons.save(self.project, uid, {}, plainreasons.spent(e))["tokens"]
            job["done"] = n
            if in_a_row >= 2:
                job["line"] = "Stopped after two failures in a row"
                break
        with self.feedback_lock:
            self.build()
        self.log_usage({"kind": "plain-reasons", "job": job["key"], "processed": job["tokens"], "units": done,
                        "failed": len(problems)})
        if not problems:
            self.note(f"The reasons are written in plain words for {done} of {len(ids)}.", "good")
        else:
            self.note(f"Plain reasons: {done} of {len(ids)} done, {len(problems)} not.", "warning", "\n".join(problems)[-800:])
        job["line"] = f"Done: {done} of {len(ids)}" + (f", {len(problems)} could not be written" if problems else "")

    def _take_reasons(self, docs: list[dict]) -> bool:
        """A reason the owner accepted under Ask Claude why: recorded in compare/compare.json by
        `compare.fill_reason`. Each document is deleted once handled."""
        from .compare import fill_reason
        changed = False
        for d in sorted(docs, key=lambda x: str(x.get("at") or "")):
            try:
                fill_reason(self.project, d)
                self.note("The reason is saved.", "good")
                changed = True
            except HandbookError as e:
                self.note("The reason could not be saved.", "warning", str(e))
            self.store.delete("reasons", d["id"])
        return changed

    def _adopt_report(self, docs: list[dict]) -> bool:
        newest = max(docs, key=lambda d: str(d.get("at") or ""))
        markdown = newest.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            return False
        self.cmd.run("facts")
        report = self.project.path / "report" / "report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(markdown, encoding="utf-8")
        code, out = self.cmd.run("check-report")
        for d in docs:
            self.store.delete("report", d["id"])
        if code == 0:
            self.note("Your report is saved.", "good")
        else:
            self.note("Your report is saved, but some numbers don't match the figures.", "warning", out[-800:])
        return True

    # -- re-runs and re-reads ------------------------------------------------------------------------

    def _queue_reruns(self) -> None:
        control = read_json(self.project.file("control"), {}) or {}
        for r in control.get("reruns") or []:
            if isinstance(r, dict) and isinstance(r.get("id"), str):
                rule = r.get("rule") or "?"
                self.enqueue("rerun:" + r["id"], "rerun", f"Re-run of rule {rule}",
                             lambda job, req=dict(r): self.rerun(job, req))

    def _queue_rereads(self, _unused) -> None:
        """A correction whose words are in a link or a file the owner sent is read again here, blind.
        One on a page saved in the project is checked by the page itself (views/cell.js), at once;
        if that could not happen, its "Check it now" button asks again, so no scorer starts unasked."""
        rounds = (read_json(self.project.file("corrections"), {}) or {}).get("corrections") or []
        for r in rounds:
            if r.get("status") != "to_check" or not r.get("id"):
                continue
            ev = r.get("evidence") or {}
            if not (ev.get("link") or ev.get("asset") or ev.get("local")):
                continue
            self.enqueue("reread:" + r["id"], "reread", f"Checking your correction on {r.get('row')} question {r.get('qid')}",
                         lambda job, rnd=dict(r): self.reread(job, rnd))

    def _score(self, job: dict, kit: str) -> int:
        """Run one scorer on a kit (outside the project lock) and return the tokens it processed."""
        code, out = self.cmd.run("score", "--kit", kit, project=False, timeout=SCORE_TIMEOUT_S)
        receipt = read_json(Path(kit) / "receipt.json", {}) or {}
        usage = receipt.get("usage") or {}
        tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens", "output_tokens"))
        job["tokens"] += tokens
        self.log_usage({"kind": "score", "job": job["key"], "kit": Path(kit).name, "processed": tokens,
                        "turns": receipt.get("num_turns"), "ok": code == 0})
        if code != 0 and not (Path(kit) / "answers.json").is_file():
            raise HandbookError(last_line(out) or "The scorer did not finish.")
        return tokens

    def _values(self, rows, qids) -> dict:
        cells = self.bundle().get("cells") or {}
        return {(r, q): (cells.get(r) or {}).get(q, {}).get("value") for r in rows for q in qids}

    def rerun(self, job: dict, req: dict) -> None:
        bundle = self.bundle()
        cells = bundle.get("cells") or {}
        names = {u.get("id"): u.get("name") for u in bundle.get("units") or []}
        questions = [str(q) for q in req.get("questions") or []]
        rows = [r for r in req.get("rows") or [] if r in cells]
        work = [(r, [q for q in questions if q in cells[r]]) for r in rows]
        work = [(r, qs) for r, qs in work if qs]
        job.update({"total": len(work), "done": 0, "rule": req.get("rule"), "request": req.get("id")})
        if not work:
            with self.cmd.lock:
                self.cmd.run("control", "set", "--rerun-done", req["id"])
            self.build()
            self.note(f"Re-run of rule {req.get('rule')}: nothing to score again.", "neutral")
            return
        before = self._values([r for r, _ in work], questions)
        all_q = sorted({q for _, qs in work for q in qs}, key=questions.index)
        with self.cmd.lock:
            out = self.cmd.must("run", "start", "--kind", "rerun", "--questions", ",".join(all_q),
                                "--rows", ",".join(r for r, _ in work), "--json")
        run_id = json.loads(last_line(out))["id"]
        self.build()                     # the run bar shows the re-run as the open run
        failed = []
        for i, (row, qs) in enumerate(work, 1):
            label = f"{names.get(row) or row} ({i} of {len(work)})"
            job["line"] = f"{label}: getting the documents ready"
            try:
                with self.cmd.lock:
                    kit = last_line(self.cmd.must("kit", "--row", row, "--run", run_id, "--questions", ",".join(qs)))
                job["line"] = f"{label}: Claude is scoring {len(qs)} answer{'s' if len(qs) != 1 else ''}"
                tokens = self._score(job, kit)
                job["line"] = f"{label}: checking the answers"
                with self.cmd.lock:
                    self.cmd.run("run", "usage", "--run", run_id, "--row", row, "--step", "score",
                                 "--receipt", str(Path(kit) / "receipt.json"))
                    self.cmd.must("ingest-answers", "--row", row, "--run", run_id, "--file", str(Path(kit) / "answers.json"))
                    self.cmd.run("check")
                job["steps"].append({"row": row, "questions": qs, "tokens": tokens})
            except HandbookError as e:
                failed.append(row)
                self.note(f"Re-run: {names.get(row) or row} could not be scored again.", "warning", str(e)[-600:])
            job["done"] = i
            self.build()
        with self.cmd.lock:
            self.cmd.run("run", "finish", "--run", run_id)
            self.cmd.run("control", "set", "--rerun-done", req["id"])
            self.cmd.run("check")
            if (self.project.path / "compare" / "compare.json").is_file():
                self.cmd.run("compare")   # the comparison follows the new answers: a re-run rule leaves Check these first
        self.build()
        after = self._values([r for r, _ in work], questions)
        moved = sum(1 for k in before if before[k] != after.get(k))
        done = len(work) - len(failed)
        job["line"] = (f"Done: {done} of {len(work)} rows scored again, {moved} answer{'s' if moved != 1 else ''} changed, "
                       f"{job['tokens']:,} tokens.")
        self.note(f"Re-run of rule {req.get('rule')} done: {done} of {len(work)} rows scored again, "
                  f"{moved} answer{'s' if moved != 1 else ''} changed.", "good" if not failed else "warning",
                  f"{job['tokens']:,} tokens processed on your Claude plan.")

    def reread(self, job: dict, rnd: dict) -> None:
        row, qid, cid = rnd["row"], rnd["qid"], rnd["id"]
        job.update({"total": 1, "done": 0})
        current = next((r for r in (read_json(self.project.file("corrections"), {}) or {}).get("corrections") or []
                        if r.get("id") == cid), None)
        if not current or current.get("status") != "to_check":
            return                                          # the page checked it meanwhile
        look = current.get("reread") or {}
        with self.cmd.lock:
            out = self.cmd.must("run", "start", "--kind", "rerun", "--questions", qid, "--rows", row, "--json")
            run_id = json.loads(last_line(out))["id"]
            args = ["kit", "--row", row, "--run", run_id, "--questions", qid]
            if look.get("doc") and look.get("page"):
                args += ["--look", f"{look['doc']}:{look['page']}"]
            kit = last_line(self.cmd.must(*args))
        job["line"] = f"Claude is reading {row} question {qid} again, blind"
        self._score(job, kit)
        with self.cmd.lock:
            self.cmd.run("run", "usage", "--run", run_id, "--row", row, "--step", "score",
                         "--receipt", str(Path(kit) / "receipt.json"))
            code, out = self.cmd.run("apply-corrections", "--record", cid, "--run", run_id,
                                     "--file", str(Path(kit) / "answers.json"))
            self.cmd.run("run", "finish", "--run", run_id)
            self.cmd.run("check")
        job["done"] = 1
        self.build()
        tone = "good" if code == 0 and "disagrees" not in out else "warning"
        self.note(last_line(out.split("The owner's answer stays")[0]) if code == 0 else "The re-read could not be recorded.",
                  tone, f"{job['tokens']:,} tokens processed on your Claude plan.")

    # -- for the page ----------------------------------------------------------------------------

    def state(self) -> dict:
        def public(job):
            if not job:
                return None
            return {k: job.get(k) for k in ("key", "kind", "title", "status", "done", "total", "line", "tokens",
                                            "started", "finished", "rule", "steps")}
        return {"version": self.version, "job": public(self.job), "queued": [public(j) for j in self.queue],
                "last_job": public(self.last_job), "activity": list(self.activity), "problem": self.problem,
                "last_sample": self.last_sample,
                # Claude bills an API key at API rates, not the owner's plan: the page says so.
                "api_key": bool(self.api_key_source) and self.api_key_source not in ("none", "oauth")}


# ---- "Download a copy" -------------------------------------------------------------------------

COPY_SCRIPT = r"""
(function () {
  "use strict";
  var el = document.getElementById("hb-copy-files");
  var files = {};
  try { files = JSON.parse(el.textContent) || {}; } catch (e) { files = {}; }
  var realFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (url, opts) {
    var name = String(url && url.url ? url.url : url).replace(/^\.?\//, "");
    if (Object.prototype.hasOwnProperty.call(files, name)) {
      return Promise.resolve(new Response(JSON.stringify(files[name]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (/^(evidence\/|downloads\.json)/.test(name)) return Promise.resolve(new Response("null", { status: 404 }));
    return realFetch ? realFetch(url, opts) : Promise.reject(new Error("offline"));
  };
  document.addEventListener("DOMContentLoaded", function () {
    var box = document.getElementById("hb-banner");
    var made = el.getAttribute("data-made") || "";
    var p = document.createElement("p");
    p.className = "notice";
    p.setAttribute("data-tone", "neutral");
    p.textContent = "A read-only copy, saved " + made + ". It shows the benchmark as it was then; nothing in it can be changed.";
    var host = document.querySelector(".hb-main");
    if (host && host.parentNode) host.parentNode.insertBefore(p, host);
  });
})();
"""


def copy_html(project: Project) -> str:
    """One self-contained, read-only HTML file of the page: the bundle, every evidence file and the
    download tables inlined. It has no `window.claude`, so nothing in it can be edited or ask Claude."""
    viewer = project.path / "viewer"
    page = (viewer / "page.html").read_text(encoding="utf-8")
    files = {}
    for p in sorted((viewer / "evidence").glob("*.json")):
        files[f"evidence/{p.name}"] = json.loads(p.read_text(encoding="utf-8"))
    if (viewer / "downloads.json").is_file():
        files["downloads.json"] = json.loads((viewer / "downloads.json").read_text(encoding="utf-8"))
    d = datetime.now()
    made = f"{d.day} {d:%B %Y} at {d:%H:%M}"
    head = ('<!doctype html>\n<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body>\n')
    extra = (f'<script type="application/json" id="hb-copy-files" data-made="{html.escape(made)}">'
             + json_for_script(json.dumps(files, ensure_ascii=False, separators=(",", ":"))) + "</script>\n"
             + "<script>" + COPY_SCRIPT + "</script>\n")
    return head + extra + page + "\n</body>\n</html>\n"


# ---- The HTTP server -------------------------------------------------------------------------------

class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, project: Project, port: int = 0, config: Config | None = None, claude: str = "claude"):
        super().__init__(("127.0.0.1", port), Handler)
        self.project = project
        self.token = secrets.token_urlsafe(24)
        self.alive_key = None              # set by cmd_serve: a hash of the project's path
        self.config = config or Config()
        self.store = Store(project.path)
        self.worker = Worker(project, self.store)
        self.feedback_lock = self.worker.feedback_lock     # one feedback write at a time (a Claude check can take a minute)
        slug = re.sub(r"[^a-z0-9-]+", "-", project.path.name.lower()).strip("-") or "project"
        self.sampler = Sampler(home() / "chat" / slug, self.bridge_url, page_claude_text,
                               log=self.worker.log_usage, claude=claude)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def first_url(self) -> str:
        return f"{self.url}?t={self.token}"

    def bridge_url(self, call_id: str) -> str:
        return f"{self.url}bridge/{call_id}"

    def start_background(self) -> None:
        self.worker.start()

    def stop_work(self) -> None:
        """Stop the background work: Claude calls from the page, and a scorer a job started."""
        self.worker.stopping = True
        for call in list(self.sampler.calls.values()):
            if not call.finished:
                call.cancel()
        self.worker.cmd.stop_all()

    def shutdown_all(self) -> None:
        self.stop_work()
        self.shutdown()


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):            # quiet: the terminal is for the owner
        pass

    # -- replies -------------------------------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def fail(self, code: int, message: str, err: str = "invalid_argument") -> None:
        self.json({"error": {"code": err, "message": message}}, code)

    def body(self, limit: int = 2_000_000) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise HandbookError("too_large")
        return self.rfile.read(n) if n else b""

    def body_json(self):
        raw = self.body()
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise HandbookError("The request body is not JSON.") from None

    # -- who may ask ---------------------------------------------------------------------------

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}")

    def _cookie_name(self) -> str:
        return f"hb_{self.server.port}"

    def _has_cookie(self) -> bool:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except cookies.CookieError:
            return False
        c = jar.get(self._cookie_name())
        return bool(c) and secrets.compare_digest(c.value, self.server.token)

    def _api_ok(self) -> bool:
        return self._has_cookie() and secrets.compare_digest(self.headers.get("X-HB-Token") or "", self.server.token)

    # -- routing -------------------------------------------------------------------------------

    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_PATCH(self):
        self._route("PATCH")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method: str) -> None:
        try:
            if not self._host_ok():
                return self.fail(403, "This page only answers on 127.0.0.1.", "forbidden")
            url = urlparse(self.path)
            path = url.path
            query = parse_qs(url.query)
            if path == "/hb-alive" and method == "GET":        # `hb serve` asks: is this project served here?
                return self.json({"project": self.server.alive_key})
            if path.startswith("/bridge/"):
                return self._bridge(method, path)
            if method == "GET" and path == "/" and query.get("t"):
                if secrets.compare_digest(query["t"][0], self.server.token):
                    return self._send(303, b"", "text/plain", {
                        "Location": "/",
                        "Set-Cookie": f"{self._cookie_name()}={self.server.token}; Path=/; HttpOnly; SameSite=Strict"})
                return self.fail(403, "That link is out of date. Open the page again from the terminal.", "forbidden")
            if not self._has_cookie():
                return self._send(403, ("<!doctype html><meta charset=utf-8><title>Benchmark tool</title>"
                                        "<p style='font:16px system-ui;margin:2rem'>Open this page with the link "
                                        "the terminal printed when it started.</p>").encode(), "text/html; charset=utf-8")
            if path.startswith("/api/"):
                if not self._api_ok():
                    return self.fail(403, "Missing the page's token.", "forbidden")
                return self._api(method, path[5:], query)
            if method != "GET":
                return self.fail(405, "Not allowed.")
            return self._static(path)
        except HandbookError as e:
            return self.fail(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self.fail(500, f"{type(e).__name__}: {e}", "unavailable")

    def _static(self, path: str) -> None:
        viewer = self.server.project.path / "viewer"
        if path in ("/", "/index.html"):
            return self._page()
        if path == "/local.js":
            return self._send(200, LOCAL_JS.read_bytes(), "text/javascript; charset=utf-8")
        name = path.lstrip("/")
        if name in ("bundle.json", "downloads.json") or re.fullmatch(r"evidence/[a-z0-9-]+\.json", name):
            f = viewer / name
            if f.is_file():
                return self._send(200, f.read_bytes(), "application/json; charset=utf-8")
        return self.fail(404, "Not found.", "not_found")

    def _page(self) -> None:
        index = self.server.project.path / "viewer" / "index.html"
        if not index.is_file():
            self.server.worker.build()
        text = index.read_text(encoding="utf-8")
        boot = json.dumps({"token": self.server.token, "version": self.server.worker.version})
        inject = f"<script>window.HB_LOCAL = {json_for_script(boot)};</script>\n<script src=\"/local.js\"></script>\n"
        if DATA_TAG not in text:
            return self.fail(500, "The built page has no data in it. Rebuild it with hb build.", "unavailable")
        text = text.replace(DATA_TAG, inject + DATA_TAG, 1)
        self._send(200, text.encode("utf-8"), "text/html; charset=utf-8",
                   {"Content-Security-Policy": "frame-ancestors 'none'"})

    # -- the API -------------------------------------------------------------------------------

    def _api(self, method: str, path: str, query: dict) -> None:
        srv = self.server
        parts = path.split("/")
        if path == "state" and method == "GET":
            name = srv.config.user_name()
            p = srv.project.read("project")
            return self.json({**srv.worker.state(), "user": {"name": name, "id": user_id(name)},
                              "project": {"name": p.get("name"), "path": str(srv.project.path)}})
        if parts[0] == "material" and method == "GET":
            from . import material
            try:
                if len(parts) == 1:
                    return self.json({"files": material.listing(srv.project)})
                q = lambda k, d: (query.get(k) or [d])[0]  # noqa: E731
                return self.json(material.read(srv.project, parts[1], int(q("start", 0)), int(q("size", 20000))))
            except (ValueError, OSError) as e:
                return self.fail(400, str(e), "unreadable")
        if parts[0] == "setup":
            return self._setup(method, parts[1:])
        if parts[0] == "feedback":
            return self._feedback(method, parts[1:])
        if path == "page-claude" and method == "GET":
            return self.json({"text": page_claude_text()})
        if path == "user" and method == "POST":
            name = srv.config.set_user_name((self.body_json() or {}).get("name"))
            return self.json({"name": name, "id": user_id(name)})
        if parts[0] == "db":
            return self._db(method, parts[1:], query)
        if parts[0] == "assets":
            return self._assets(method, parts[1:])
        if parts[0] == "sample":
            return self._sample(method, parts[1:], query)
        if path == "copy" and method == "GET":
            slug = re.sub(r"[^a-z0-9-]+", "-", str(srv.project.read("project").get("slug") or "benchmark").lower())
            name = f"{slug}-copy-{datetime.now().strftime('%Y-%m-%d')}.html"
            return self._send(200, copy_html(srv.project).encode("utf-8"), "text/html; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        return self.fail(404, "No such call.", "not_found")

    def _setup(self, method: str, parts: list[str]) -> None:
        """The Set-up tab's chat: GET the set-up as it stands (or `guide`, the file formats); POST a part
        ({part, content}) or a checklist status ({item, status}). Each write is checked before it is kept
        (setup_parts), then the page is rebuilt so the right-hand side fills in."""
        from . import setup_parts
        srv = self.server
        worker = srv.worker
        if method == "GET" and not parts:
            return self.json(setup_parts.describe(srv.project))
        if method == "GET" and parts == ["guide"]:
            return self.json({"text": setup_parts.guide()})
        if method != "POST" or parts not in ([], ["status"]):
            return self.fail(404, "No such call.", "not_found")
        body = self.body_json() or {}
        try:
            with worker.cmd.lock:
                if parts == ["status"]:
                    got = setup_parts.set_status(srv.project, str(body.get("item") or ""), str(body.get("status") or ""))
                else:
                    got = setup_parts.write_part(srv.project, str(body.get("part") or ""), body.get("content"))
        except HandbookError as e:
            return self.fail(400, str(e))
        if got.get("ok"):
            worker.build()
        return self.json(got)

    def _feedback(self, method: str, parts: list[str]) -> None:
        """The Feedback tab. GET `status`: the round as the bundle shows it (also before it has started).
        POST (JSON body), each answering `{result, built, feedback}`: `packs` {scope: ready|units|all,
        units?}, `mark-sent` {units}, `replies` {files: [{asset, name}], unit?, replace?}, `decide`
        {unit, question, action: keep|change, value?, reason?}, `undo` {unit, question}, `stage` {unit,
        stage}, `move-file` {file, units}, `remove-file` {file}, `confirm-group` {file, units?},
        `restart` {unit}, `settings` {set: {key: value}}; `stage`, `restart` also take `units` (a list),
        `remove-file` takes `files`, `review` takes `questions`. The corner chat uses the lists.
        Claude is called in three places only, each when the owner asks: `review` {unit, question} (one small
        call for a point), `read-file` {file} (one small call to say whose a waiting file is) and
        `plain-reasons` {units?, setting?} (a background job, one small call per unit; it sets the reasons to
        plain unless setting is false, and answers at once with the units queued and the tokens it will use).
        `preview` {unit, question, value} and `open-folder` {folder} change nothing.
        Every write rebuilds the page before it answers."""
        from . import feedback, packs, replies
        srv = self.server
        project = srv.project
        worker = srv.worker
        if method == "GET" and parts == ["status"]:
            return self.json({"feedback": feedback.bundle_part(project, always=True)})
        if method != "POST" or len(parts) != 1:
            return self.fail(404, "No such call.", "not_found")
        act = parts[0]
        body = self.body_json() or {}
        if not isinstance(body, dict):
            return self.fail(400, "The request body must be a JSON object.")

        def need(key):
            value = body.get(key)
            if value in (None, "", []):
                raise HandbookError(f"Say which {key.replace('_', ' ')}.")
            return value

        def units_of(key="units"):
            value = body.get(key)
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
                raise HandbookError("Say which units.")
            return value

        if act == "preview":
            return self.json({"result": feedback.preview(project, need("unit"), str(need("question")), body.get("value"))})
        if act == "open-folder":
            return self.json({"result": open_folder(project, str(need("folder")))})

        def do(fn, slow=False):
            """Run `fn` with the feedback lock, then rebuild so the page's data is the new one. A slow call
            (Claude reading a point) does not hold the lock the other saves and builds wait on."""
            with srv.feedback_lock:
                if slow:
                    result = fn()
                    built = worker.build()
                else:
                    with worker.cmd.lock:
                        result = fn()
                        built = worker.build()
                part = feedback.bundle_part(project, always=True)
            return self.json({"result": result, "built": built, "feedback": part})

        def make():
            got = packs.make_packs(project, scope, ids)
            got["path"] = str(project.path / got["folder"])
            return got

        if act == "packs":
            scope = str(body.get("scope") or "ready")
            ids = units_of() if scope == "units" else None
            return do(make)
        if act == "mark-sent":
            return do(lambda: {"units": feedback.mark_sent(project, units_of())})
        if act == "replies":
            files = body.get("files")
            if not isinstance(files, list) or not files:
                raise HandbookError("There are no files to read.")
            unit = body.get("unit")
            return do(lambda: self._read_replies(files, unit if isinstance(unit, str) and unit else None, bool(body.get("replace"))))
        if act == "decide":
            action = str(need("action"))
            return do(lambda: feedback.decide(project, need("unit"), str(need("question")), action, body.get("value"),
                                              str(body.get("reason") or "")))
        if act == "undo":
            return do(lambda: feedback.undo_decision(project, need("unit"), str(need("question"))))
        def either(one: str, many: str) -> list[str]:
            """`many` (a list) when given, else the single `one`: the chat sends lists, the tab one at a time."""
            if body.get(many):
                return units_of(many) if many == "units" else files_of()
            return [str(need(one))]

        def files_of() -> list[str]:
            value = body.get("files")
            if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
                raise HandbookError("Say which files.")
            return value

        def checked_units(names: list[str]) -> list[str]:
            return [feedback.unit_id(project, u) for u in names]      # every name is found before anything is written

        if act == "stage":
            stage, names = str(need("stage")), either("unit", "units")
            if body.get("units"):
                return do(lambda: {"units": [feedback.set_stage(project, u, stage) for u in checked_units(names)]})
            return do(lambda: feedback.set_stage(project, names[0], stage))
        if act == "move-file":
            return do(lambda: feedback.move_file(project, str(need("file")), units_of()))
        if act == "remove-file":
            def remove():
                ids = either("file", "files")
                state = feedback.read_state(project)
                for fid in ids:
                    feedback._find_file(state, fid)                    # all there before any goes
                gone = [feedback.remove_file(project, fid) for fid in ids]
                return gone[0] if not body.get("files") else {"files": gone}
            return do(remove)
        if act == "confirm-group":
            ids = units_of() if body.get("units") else None
            return do(lambda: feedback.confirm_group_file(project, str(need("file")), ids))
        if act == "restart":
            names = either("unit", "units")
            if body.get("units"):
                return do(lambda: {"units": [feedback.restart(project, u) for u in checked_units(names)]})
            return do(lambda: feedback.restart(project, names[0]))
        if act == "review":
            # A Claude call, only when the owner asks: one small `claude -p` for the points they name.
            qs = body.get("questions") if isinstance(body.get("questions"), list) and body.get("questions") else [str(need("question"))]
            return do(lambda: replies.review_unit(project, need("unit"), [str(q) for q in qs], True), slow=True)
        if act == "settings":
            changes = body.get("set")
            if not isinstance(changes, dict) or not changes:
                raise HandbookError("Say which settings to change.")
            return do(lambda: {"settings": feedback.set_settings(project, **changes)})
        if act == "read-file":
            # Another Claude call, only when asked: one small `claude -p` to say whose a waiting file is.
            return do(lambda: replies.read_unmatched(project, str(need("file"))), slow=True)
        if act == "plain-reasons":
            from . import plainreasons
            chosen = body.get("units")
            if chosen is not None and (not isinstance(chosen, list) or not all(isinstance(x, str) for x in chosen)):
                raise HandbookError("Say which units, as a list.")

            def start():
                ids = plainreasons.with_notes(project, chosen or None)
                if not ids:
                    raise HandbookError("None of those has a note to rewrite yet.")
                if body.get("setting", True):
                    feedback.set_settings(project, reasons="plain")
                queued = worker.enqueue("plain-reasons:" + now_iso(), "plain", "Writing the reasons in plain words",
                                        lambda job: worker.plain_reasons(job, ids))
                low, high = plainreasons.PER_UNIT
                return {"queued": queued, "units": ids, "tokens": {"low": low * len(ids), "high": high * len(ids)}}
            return do(start)
        return self.fail(404, "No such call.", "not_found")

    def _read_replies(self, files: list, unit, replace: bool) -> dict:
        """Read the files the page uploaded (as assets): each is copied under the name the owner dropped it
        with (the file name is how a reply finds its unit), read, and the upload removed."""
        from . import feedback, replies
        srv = self.server
        work = Path(tempfile.mkdtemp(prefix="hb-replies-"))
        assets, paths = [], []
        try:
            for n, item in enumerate(files):
                asset = item.get("asset") if isinstance(item, dict) else None
                src = srv.worker.asset_path(asset)
                if not src:
                    raise HandbookError("One of the files you dropped could not be found among the uploads. Drop it again.")
                assets.append(src)
                name = feedback.safe_name((item.get("name") or src.name.split("--", 1)[-1]))
                dest = work / f"{n}" / name
                dest.parent.mkdir()
                dest.write_bytes(src.read_bytes())
                paths.append(dest)
            return replies.read_replies(srv.project, paths, unit, replace)
        finally:
            for src in assets:
                src.unlink(missing_ok=True)
            shutil.rmtree(work, ignore_errors=True)

    def _db(self, method: str, parts: list[str], query: dict) -> None:
        store = self.server.store
        if len(parts) == 1 and method == "GET":
            version = store.version(parts[0])
            if query.get("v", [None])[0] == version:
                return self.json({"version": version, "same": True})
            return self.json({"version": version, "docs": store.list(parts[0])})
        if len(parts) == 2:
            col, doc_id = parts
            if method == "GET":
                return self.json({"doc": store.get(col, doc_id)})
            if method == "PUT":
                doc = store.set(col, doc_id, self.body_json())
            elif method == "PATCH":
                doc = store.update(col, doc_id, self.body_json())
            elif method == "DELETE":
                store.delete(col, doc_id)
                doc = None
            else:
                return self.fail(405, "Not allowed.")
            if col in WATCHED:
                self.server.worker.poke()
            return self.json({"doc": doc, "version": store.version(col)})
        return self.fail(404, "No such call.", "not_found")

    def _assets(self, method: str, parts: list[str]) -> None:
        srv = self.server
        folder = srv.project.path / "inbox" / "uploads"
        if not parts or parts == [""]:
            if method == "POST":
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                raw = unquote(self.headers.get("X-Filename") or "upload")
                ext = UPLOAD_TYPES.get(ctype)
                if not ext and ctype in UPLOAD_GENERIC and Path(raw).suffix.lower() in readers.UPLOAD_MIME:
                    ext = Path(raw).suffix.lower()          # a HEIC or Markdown file a browser sent with no type
                if not ext:
                    return self.fail(400, "Send a PDF, Word, Excel, text, picture or email file.", "unsupported_type")
                try:
                    data = self.body(MAX_UPLOAD)
                except HandbookError:
                    return self.fail(413, "That file is over 40 MB.", "too_large")
                stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(raw).stem).strip("-.")[:60] or "upload"
                asset_id = secrets.token_hex(16)
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{asset_id}--{stem}{ext}"
                path.write_bytes(data)
                return self.json({"id": asset_id, "url": f"inbox/uploads/{path.name}", "sizeBytes": len(data),
                                  "contentType": ctype})
            if method == "GET":
                items = []
                for p in sorted(folder.glob("*--*")) if folder.is_dir() else []:
                    st = p.stat()
                    items.append({"id": p.name.split("--")[0], "url": f"inbox/uploads/{p.name}",
                                  "contentType": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                                  "sizeBytes": st.st_size,
                                  "createdAt": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                return self.json({"assets": items, "usage": {"files": len(items), "bytes": sum(i["sizeBytes"] for i in items),
                                                              "maxFiles": 10000, "maxBytes": 10 ** 11}})
        if len(parts) == 1 and method == "DELETE":
            path = srv.worker.asset_path(parts[0])
            if path:
                path.unlink()
            return self.json({"deleted": bool(path)})
        return self.fail(404, "No such call.", "not_found")

    def _sample(self, method: str, parts: list[str], query: dict) -> None:
        sampler = self.server.sampler
        if parts == [""] or not parts:
            if method != "POST":
                return self.fail(405, "Not allowed.")
            body = self.body_json() or {}
            try:
                call = sampler.start(body.get("input"), body.get("tools"), body.get("modelTier"), bool(body.get("json")))
            except SampleError as e:
                return self.json({"error": {"code": e.code, "message": e.message}}, 400)
            return self.json({"call": call.id})
        call = sampler.calls.get(parts[0])
        if not call:
            return self.fail(404, "That call has ended.", "not_found")
        if len(parts) == 2 and parts[1] == "events" and method == "GET":
            after = int(query.get("after", ["0"])[0] or 0)
            return self.json({"events": call.events_after(after, 20.0)})
        if len(parts) == 2 and parts[1] == "result" and method == "POST":
            body = self.body_json() or {}
            ok = call.tool_result(str(body.get("id")), {"ok": bool(body.get("ok")), "value": body.get("value"),
                                                        "error": body.get("error")})
            return self.json({"taken": ok})
        if len(parts) == 2 and parts[1] == "cancel" and method == "POST":
            call.cancel()
            return self.json({"cancelled": True})
        return self.fail(404, "No such call.", "not_found")

    def _bridge(self, method: str, path: str) -> None:
        m = re.fullmatch(r"/bridge/([a-f0-9]+)/tool", path)
        call = self.server.sampler.calls.get(m.group(1)) if m else None
        if not call or method != "POST" or not secrets.compare_digest(self.headers.get("X-HB-Bridge") or "", call.secret):
            return self.fail(403, "Not allowed.", "forbidden")
        body = self.body_json() or {}
        return self.json(call.tool_request(str(body.get("name")), body.get("input") or {}))

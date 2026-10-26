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
- a project shared by a team (`team.json`; team.py, teamwork.py): this server applies only this person's
  saves and starts only their re-runs and re-reads; a save made before a colleague's newer change synced
  in is held as a clash; a request to pause someone else's run is refused. Every few seconds it merges
  the sync service's clash copies, notes colleagues' actions and rebuilds when their changes arrive.
  The page is built into this person's own home folder (`Project.viewer`). `/api/team/...`: the Team
  panel (clashes, the log, snapshots, share, send a copy) and the check mark on an answer.
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
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import chatwork, costs, edition, needs, readers, updates
from .estimate import show_tokens
from .folder import HandbookError, Project, read_json
from .localstore import Config, Store, home
from .page import VIEWER_SRC, json_for_script
from .sampler import Sampler, SampleError
from .updater import Updater, back_blockers, respawn, restart_when_idle

HB = Path(__file__).resolve().parents[1] / "hb.py"
LOCAL_JS = VIEWER_SRC / "js" / "local.js"
PAGE_CLAUDE = Path(__file__).resolve().parents[2] / "guide" / "page-claude.md"
DATA_TAG = '<script type="application/json" id="hb-data">'
WATCHED = ("control", "edits", "decisions", "corrections", "supplied", "report", "charts", "reasons", "setup", "scoring",
           "bulk")
TICK_S = 3.0
MAX_UPLOAD = 40 * 1024 * 1024
SCORE_TIMEOUT_S = 1900
TRIAL_ROWS = 3                  # rows a trial tries when the owner chose none (hb cover picks them)
# A page trial or run that has used this many times the top of its estimate pauses and waits for the owner
# (knowthechain, 21 Oct 2026: 4.06M tokens against 74k to 175k, and the page never paused).
OVER_ESTIMATE = 3
ARCHIVE_AGAIN = 20              # pages a job asks the web archive for again (serve.Worker._archive_again)
# A background job that fails (a correction's re-read, a re-run) is not queued again at once: its request
# is still on file, and the apply loop would otherwise start it again every tick (20 Oct 2026 review: 376
# scorer sessions in 75 seconds). It waits this long, doubling after each failure, up to RETRY_MAX_S.
RETRY_S = 600.0
RETRY_MAX_S = 7200.0
ROW_RE = re.compile(r"^[a-z0-9-]+\Z")
ASSET_RE = re.compile(r"^[a-f0-9]{16,32}\Z")
# The content types an upload may carry: every kind the tool reads (readers.UPLOAD_MIME is the one list, which
# the upload boxes' own lists are checked against) and the other types browsers send for them.
UPLOAD_TYPES = {}
for _ext, _mime in readers.UPLOAD_MIME.items():
    UPLOAD_TYPES.setdefault(_mime.lower(), _ext)
UPLOAD_TYPES.update(readers.MIME_ALIASES)
# A zip is not a kind the tool reads, but the bulk upload (bulk.py) opens one: documents sent together.
ZIP_TYPES = {"application/zip": ".zip", "application/x-zip-compressed": ".zip", "application/x-zip": ".zip"}
UPLOAD_TYPES.update(ZIP_TYPES)
UPLOAD_GENERIC = ("", "application/octet-stream", "binary/octet-stream")   # a browser that did not know the type


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page_claude_text() -> str:
    """handbook/guide/page-claude.md without its HTML comments; empty when the file is missing."""
    try:
        text = PAGE_CLAUDE.read_text(encoding="utf-8")
    except OSError:
        return ""
    return re.sub(r"<!--.*?-->", "", edition.for_this_edition(text), flags=re.S).strip()


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


def rerun_title(req: dict) -> str:
    """"Re-run of rule R3", or for answers the owner picked in the chat, "Re-run: <what for>"."""
    if req.get("rule"):
        return f"Re-run of rule {req['rule']}"
    why = str(req.get("why") or "").strip()
    return "Re-run: " + why[:80] if why else "Re-run of the answers you picked"


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
        self.team = None                    # the TeamSide (teamwork.py), set by LocalServer
        self.failed: dict[str, tuple[int, float]] = {}   # job key: (failures so far, not before this time)

    # -- notes for the page ----------------------------------------------------------------------

    def note(self, text: str, tone: str = "neutral", detail: str | None = None) -> None:
        self.activity.appendleft({"at": now_iso(), "text": text, "tone": tone, "detail": detail})

    def _usage_path(self) -> Path:
        if self.team is not None and self.team.on:        # one file per person: two computers never append to one
            return self.project.path / "page-data" / "_log" / f"usage-{self.team.me['id']}.jsonl"
        return self.usage_log

    def log_usage(self, entry: dict) -> None:
        if entry.get("kind") == "sample":
            self.last_sample = {k: entry.get(k) for k in ("processed", "cache_read", "fresh_input", "output",
                                                          "turns", "resumed", "tier", "seconds")}
            if entry.get("api_key_source"):
                self.api_key_source = entry["api_key_source"]
        try:
            path = self._usage_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": now_iso(), **entry}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def bundle(self) -> dict:
        return read_json(self.project.viewer / "bundle.json", {}) or {}

    def build(self) -> bool:
        with self.cmd.lock:
            code, out = self.cmd.run("build")
        if code != 0:
            self.problem = out
            self.note("The page could not be rebuilt.", "critical", out[-600:])
            return False
        self.problem = None
        self.version += 1
        if self.team is not None:
            self.team.built()
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
                if self.team is not None:
                    self.team.tick()
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
                self.failed.pop(job["key"], None)
            except Exception as e:  # noqa: BLE001
                job["status"] = "failed"
                job["line"] = f"Stopped: {e}"
                n = self.failed.get(job["key"], (0, 0.0))[0] + 1
                wait = min(RETRY_S * 2 ** (n - 1), RETRY_MAX_S)
                self.failed[job["key"]] = (n, time.monotonic() + wait)
                again = (f" It tries again in about {round(wait / 60)} minutes, or when the page is restarted."
                         if job["kind"] in ("rerun", "reread") else "")
                self.note(job["title"] + " stopped." + again, "critical", str(e)[-800:])
                traceback.print_exc()
            finally:
                job["finished"] = now_iso()
                self.last_job = job
                self.job = None
                self.queued_ids.discard(job["key"])
                self.poke()

    def enqueue(self, key: str, kind: str, title: str, run, estimate=None) -> bool:
        """Queue a job. `estimate` is a callable giving {low, high} tokens (costs.py), worked out once
        here so the page shows it while the job waits and the job's note can say how it compared."""
        if key in self.queued_ids:
            return False
        if key in self.failed and time.monotonic() < self.failed[key][1]:
            return False                    # failed lately: it waits (RETRY_S) rather than failing every tick
        self.queued_ids.add(key)
        est = None
        if estimate is not None:
            try:
                got = estimate()
                est = {"low": int(got["low"]), "high": int(got["high"])} if got and got.get("high") else None
            except Exception:  # noqa: BLE001 - a job runs whether or not its cost can be guessed
                est = None
        self.queue.append({"key": key, "kind": kind, "title": title, "run": run, "status": "queued",
                           "done": 0, "total": 0, "line": "Waiting to start", "tokens": 0, "steps": [],
                           "estimate": est})
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
        if self.team is not None and self.team.on and self.team.recheck:
            self.team.recheck = False
            for c in ("corrections", "decisions", "edits"):
                self.seen[c] = {}
                if c not in changed:
                    changed.append(c)
        if not changed:
            self._queue_rereads(None)
            return False
        rebuild = False
        with self.cmd.lock:
            for c in changed:
                self.versions[c] = self.store.version(c)
            docs = {c: self.store.list(c) for c in changed}
            screen = self.team.screen if self.team is not None else (lambda _c, d: d)
            if "control" in docs and self._new("control", docs["control"]):
                code, out = self._run_with({"requests": screen("control", docs["control"])}, "control", "apply")
                self._said(code, out, "Your run request is applied.", "A run request could not be applied.")
                rebuild = True
            ed = {k: docs.get(k) for k in ("edits", "decisions") if k in docs}
            if ed and any([self._new(k, v) for k, v in ed.items()]):
                # Only what is not applied yet: every earlier answer sent again came back as "Skipped 6,
                # Already applied earlier" under each new one (four format tests, 21 Oct 2026). A change that
                # could not be made stays unapplied (edits-applied `failed`), so it is tried again next time.
                done = set((self.project.read("edits-applied") or {}).get("applied") or [])
                payload = {c: [d for d in screen(c, self.store.list(c)) if d.get("id") not in done]
                           for c in ("edits", "decisions")}
                if payload["edits"] or payload["decisions"]:
                    pending = {c: self.team.before(c, payload[c]) for c in payload} if self.team is not None else {}
                    code, out = self._run_with(payload, "apply-edits", "--via", "viewer")
                    if code == 0:
                        words, tone = self._edit_words(out)
                        if tone == "warning":            # some could not be made: the page says which and why
                            self.note(words, tone, out[-800:] or None)
                        else:
                            said, detail = self._edit_said(out, payload)
                            self.note(said, "good" if detail is None else "warning", detail)
                    else:
                        self.note("Your change could not be applied.", "critical", out[-800:] or None)
                    for c, p in pending.items():
                        self.team.logged(c, p)
                    rebuild = True
            if "corrections" in docs and self._new("corrections", docs["corrections"]):
                mine = screen("corrections", docs["corrections"])
                pending = self.team.before("corrections", mine) if self.team is not None else {}
                code, out = self._run_with({"corrections": [self._with_local(d) for d in mine]},
                                           "apply-corrections", "--json")
                self._corrections_said(code, out)
                if self.team is not None:
                    self.team.logged("corrections", pending)
                rebuild = True
            if "supplied" in docs and self._new("supplied", docs["supplied"]):
                rebuild = self._take_supplied(screen("supplied", docs["supplied"])) or rebuild
            if "report" in docs and docs["report"] and self._new("report", docs["report"]):
                rebuild = self._adopt_report(screen("report", docs["report"])) or rebuild
            if "charts" in docs and self._new("charts", docs["charts"]):
                rebuild = self._take_charts(screen("charts", docs["charts"])) or rebuild
            if "reasons" in docs and self._new("reasons", docs["reasons"]):
                rebuild = self._take_reasons(screen("reasons", docs["reasons"])) or rebuild
            if "setup" in docs and self._new("setup", docs["setup"]):
                self._take_setup(screen("setup", docs["setup"]))
            if "scoring" in docs and self._new("scoring", docs["scoring"]):
                self._take_scoring(screen("scoring", docs["scoring"]))
            if "bulk" in docs and self._new("bulk", docs["bulk"]):
                rebuild = self._take_bulk(screen("bulk", docs["bulk"])) or rebuild
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
    def _edit_words(out: str) -> tuple[str, str]:
        """What apply-edits did, in the owner's words, and the note's tone. A change it could not make is said
        as such (ca-energy, 21 Oct 2026: one skipped at every open read "Nothing new to apply", in green); the
        page shows which, and why, from the bundle's failed_edits."""
        lines = out.splitlines()
        at = next((i for i, ln in enumerate(lines) if re.match(r"Skipped \d+:", ln)), None)
        turned = [ln for ln in (lines[at + 1:] if at is not None else []) if ln.startswith("  ")
                  and not ln.rstrip().endswith("Already applied earlier.")]
        m = re.search(r"Applied (\d+) of", out)
        made = int(m.group(1)) if m else None
        if turned:
            n = len(turned)
            head = "One change from the page could not be made" if n == 1 else f"{n} changes from the page could not be made"
            return head + (", and the rest are applied." if made else ", so nothing changed.") + " The page says why.", "warning"
        if made == 0:
            return "Nothing new to apply.", "neutral"
        return "Your change is applied and the page is up to date.", "good"

    @staticmethod
    def _edit_said(out: str, payload: dict) -> tuple[str, str | None]:
        """What this payload of edits and decisions changed, in the owner's words, and the command's own
        account as the note's detail only when something could not be applied."""
        decisions = [d for d in payload.get("decisions") or [] if isinstance(d, dict)]
        decided = list(dict.fromkeys(str(d.get("rule")) for d in decisions if d.get("rule") and not d.get("skip")))
        later = list(dict.fromkeys(str(d.get("rule")) for d in decisions if d.get("rule") and d.get("skip")))
        edits = len(payload.get("edits") or [])
        m = re.search(r"Applied (\d+) of", out)
        if m and m.group(1) == "0":
            return "That could not be applied.", out[-800:] or None
        parts = []
        if decided:
            parts.append(("Rule " if len(decided) == 1 else "Rules ") + ", ".join(decided) + (" is" if len(decided) == 1 else " are") + " decided")
        if later:
            parts.append(("Rule " if len(later) == 1 else "Rules ") + ", ".join(later) + " left for later")
        if edits:
            parts.append(f"{edits} change{'s' if edits != 1 else ''} saved")
        said = (". ".join(parts) or "Your change is applied") + "."
        stale = re.search(r"(\d+) answers? (?:is|are) now out of date", out)
        if stale:
            n = int(stale.group(1))
            said += f" {n} answer{'s are' if n != 1 else ' is'} now out of date and can be scored again."
        else:
            said += " The page is up to date."
        return said, (out[-800:] if "Skipped" in out else None)

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
            elif r.get("status") == "checked" and v.get("by") == "code":
                # The code's own check of the evidence (corrections._handle): nothing was read by Claude.
                why = str(v.get("why") or "")
                if why.startswith(("Claude could not open the link", "The uploaded file could not be filed",
                                   "The document could not be read")):
                    said = f"Correction on {where}: the document could not be opened, so it was not used. Send the file itself instead."
                else:
                    said = f"Correction on {where}: the evidence did not check out, so it was not used."
                if r.get("value") is not None:
                    said += " Your answer is in."
                self.note(said, "warning", why or None)
            elif r.get("status") == "checked":
                self.note(f"Correction on {where}: Claude disagrees. Your answer stands.", "warning", v.get("why"))
            elif r.get("status") == "kept":
                self.note(f"Correction on {where}: your answer is kept.", "good")
            else:
                ev = r.get("evidence") or {}
                if ev.get("quote"):
                    said = "the quote checks out. Claude reads it again next."
                elif ev.get("link") or ev.get("asset") or ev.get("local"):
                    said = ("your answer is in. " if r.get("value") is not None else "") + "Claude reads the document you sent next."
                else:
                    said = "your answer is in."
                self.note(f"Correction on {where}: {said}", "good")
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
                self._rescore_after(rows, d.get("what"))
                self.note(f"Filed what you sent for {', '.join(rows)}.", "good", last_line(out))
                if self.team is not None:
                    self.team.note_action("document_added", f"{self.team.me.get('name') or 'Someone'} added a document "
                                          f"for {', '.join(rows)}", rows=rows)
                filed = True
            else:
                self.note("What you sent could not be filed.", "warning", out[-600:])
        return filed

    def _take_bulk(self, docs: list[dict]) -> bool:
        """What the owner confirmed in "Add documents or instructions" (views/bulk.js): {plan, documents,
        instructions} goes to `hb bulk apply`, which files the documents, adds the instructions and keeps
        what it did on the plan (the page reads it there to offer the re-score); then `hb prepare`."""
        changed = False
        for d in docs:
            plan = d.get("plan")
            payload = {"documents": d.get("documents") or [], "instructions": d.get("instructions") or []}
            code, out = self._run_with(payload, "bulk", "apply", "--plan", str(plan), "--json")
            self.store.delete("bulk", d["id"])
            if code != 0:
                self._bulk_failed(plan, out)
                self.note("What you added could not be filed.", "critical", out[-800:] or None)
                continue
            try:
                result = json.loads(last_line(out))
            except ValueError:
                result = {}
            if result.get("filed"):
                self.cmd.run("prepare", timeout=1800)
            changed = True
            stale = sum(len(v) for v in (result.get("out_of_date") or {}).values())
            filed, failed = len(result.get("filed") or []), len(result.get("failed") or [])
            added = sum(1 for i in result.get("instructions") or [] if i.get("added"))
            said = []
            if filed:
                said.append(f"Filed {filed} document{'s' if filed != 1 else ''}")
            if added:
                said.append(f"added {added} instruction{'s' if added != 1 else ''}")
            if stale:
                said.append(f"{stale} answer{'s are' if stale != 1 else ' is'} now out of date")
            self.note((", ".join(said) or "Nothing was filed") + "." + (f" {failed} could not be filed." if failed else ""),
                      "good" if not failed else "warning")
        return changed

    def _bulk_failed(self, plan, out: str) -> None:
        from . import bulk
        try:
            p = bulk.load_plan(self.project, plan)
        except HandbookError:
            return
        p["status"], p["error"] = "failed", (out or "").strip()[-600:]
        bulk.save_plan(self.project, p)

    def _rescore_after(self, rows: list[str], what) -> None:
        """A document the owner sent in answer to a scorer's "missing" note: the note is taken off the
        row and the questions that needed it are queued to be scored again (rescore_missing)."""
        from .ingest import supplied_missing
        from .rowwatch import SLOW_WHAT, slow_questions
        if not isinstance(what, str) or not what.strip():
            return
        for row in rows:
            qs = supplied_missing(self.project, row, what)
            if not qs and what == SLOW_WHAT:
                # sent from a slow row's card: score again what a missing document could change, and
                # take the card down (the owner has acted on it)
                qs = slow_questions(self.project, row)
                from .control import set_control
                set_control(self.project, [{"action": "row_ok", "row": row, "what": "slow"}])
            if qs:
                self.enqueue(f"rescore:{row}:{now_iso()}", "rescore", f"Scoring again with the document you sent",
                             lambda job, r=row, q=list(qs), w=what: self.rescore_missing(job, r, q, w),
                             estimate=lambda r=row, q=list(qs): costs.estimate_rows(self.project, [r], q))

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

    def spent(self, job: dict) -> str:
        """What a job used, for its note: "1,234,567 tokens processed on your Claude plan (the estimate was
        about 900,000 to 2,100,000)."."""
        est = job.get("estimate")
        said = f"{job['tokens']:,} tokens processed on your Claude plan"
        if est:
            said += f" (the estimate was about {show_tokens(est['low'])} to {show_tokens(est['high'])})"
        return said + "."

    def _take_setup(self, docs: list[dict]) -> None:
        """"Draft my set-up" (`{action: "draft"}`): one background job running `hb draft-setup`."""
        for d in docs:
            self.store.delete("setup", d["id"])
        if any(d.get("action") == "draft" for d in docs):
            self.enqueue("setup-draft:" + now_iso(), "setup", "Drafting your set-up", self.draft_setup)

    def draft_setup(self, job: dict) -> None:
        from . import material
        try:        # worked out here, not when queued: reading every file can be slow, and no lock is held now
            est = costs.setup_draft(self.project, sum(f.get("chars") or 0 for f in material.listing(self.project)))
            job["estimate"] = {"low": est["low"], "high": est["high"]}
        except Exception:  # noqa: BLE001
            job["estimate"] = None
        job.update({"total": 1, "line": "Claude is reading your files and drafting the set-up"})
        done = threading.Event()
        threading.Thread(target=self._follow_draft, args=(job, done), daemon=True, name="hb-draft-progress").start()
        try:
            code, out = self.cmd.run("draft-setup", "--json", timeout=2800)
        finally:
            done.set()
        try:
            got = json.loads(out.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            got = {}
        job["tokens"] += int(got.get("tokens") or 0)
        self.log_usage({"kind": "setup-draft", "job": job["key"], "processed": job["tokens"], "ok": code == 0,
                        "estimate": job.get("estimate")})
        with self.cmd.lock:
            self.build()
        job.update({"done": 1, "line": "Done"})
        if code == 0:
            self.note("Your set-up is drafted. Check it on the Set-up tab. " + self.spent(job), "good", got.get("result"))
        else:
            self.note("The set-up draft stopped before it finished. " + self.spent(job), "warning", (got.get("result") or out)[-800:])

    # -- scoring started from the page (the Score button, or the chat) -----------------------------

    def _take_scoring(self, docs: list[dict]) -> None:
        """`{action: "start", kind: "trial" | "full", rows?, questions?}` from the Score panel: one job
        that finds the documents and scores, as the trial and run skills do from a Claude Code session."""
        for d in docs:
            self.store.delete("scoring", d["id"])
            if d.get("action") != "start" or d.get("kind") not in ("trial", "full"):
                continue
            req = {"id": d["id"], "kind": d["kind"], "rows": [str(r) for r in d.get("rows") or []],
                   "questions": [str(q) for q in d.get("questions") or []], "asked": now_iso()}
            title = ("Trying a few " if req["kind"] == "trial" else "Scoring ") + self._plural()
            # Finding the documents is priced too, for each row not looked for yet (21 Oct 2026: trials came in at
            # 3 to 14 times the top of an estimate that left it out). A trial with no rows chosen picks its own
            # when it starts (score_run), so it is priced on as many rows as it will try.
            self.enqueue("scoring:" + d["id"], "scoring", title, lambda job, r=req: self.score_run(job, r),
                         estimate=lambda r=req: costs.estimate_rows(
                             self.project, r["rows"] or (self._trial_rows() if r["kind"] == "trial" else self._row_ids()),
                             r["questions"] or None, finding=True))

    def _row_ids(self) -> list[str]:
        return [u.get("id") for u in self.project.read("units").get("units") or [] if u.get("id")]

    def _trial_rows(self) -> list[str]:
        """Rows to price a trial on before it picks its own: TRIAL_ROWS not looked for yet, else any."""
        ids = self._row_ids()
        fresh = [r for r in ids if not (self.project.path / "found" / f"{r}.json").is_file()]
        return (fresh or ids)[:TRIAL_ROWS]

    def _plural(self) -> str:
        return (self.bundle().get("project") or {}).get("unit_word_plural") or "rows"

    def _json_out(self, *args) -> dict:
        code, out = self.cmd.run(*args, "--json")
        try:
            return json.loads(last_line(out)) if code == 0 else {}
        except (json.JSONDecodeError, IndexError):
            return {}

    def _stop_now(self, job: dict, row: str | None = None, over: bool = True) -> str | None:
        """The run control check before every row (the run skill's): apply what the page asked, then
        say why to stop, or None to carry on. A trial or run far over its estimate is paused first, but
        only while work is left (`over`): at the end it finishes and its note says it used more than
        estimated (gh5050, 21 Oct 2026: a one-row trial was left paused and unfinished without a word)."""
        if over and self._over_estimate(job):
            return "over"
        ctl = self._json_out("control")
        if ctl.get("paused"):
            return "paused"
        if ctl.get("stop_now"):
            return "stopped"
        if row and ctl.get("stop_after_row") == row:
            return "stopped after " + row
        return None

    def _over_estimate(self, job: dict) -> bool:
        """True, with the run paused, once a scoring job has used OVER_ESTIMATE times the top of its estimate."""
        high = (job.get("estimate") or {}).get("high")
        if job.get("kind", "scoring") != "scoring" or not high or job.get("tokens", 0) <= OVER_ESTIMATE * high:
            return False
        with self.cmd.lock:
            self.cmd.run("control", "set", "--pause")
        return True

    def _clear_old_stop(self, asked: str | None) -> None:
        """A start the owner asked for clears a pause or stop that was already set when they asked: one left
        on from before (20 Oct 2026: "stop the run" with nothing running, and every later start ended at
        once with 0 tokens). A pause or stop pressed after the start was asked still holds."""
        from . import control
        with self.cmd.lock:
            try:
                c = control.read(self.project)
            except HandbookError:
                return
            if not (c["paused"] or c["stop_now"] or c["stop_after_row"]):
                return
            if asked and (c.get("updated") or "") > asked:
                return
            control.set_control(self.project, [{"action": "resume"}])

    def score_run(self, job: dict, req: dict) -> None:
        """Find and score, row by row, rebuilding the page after each so the Grid fills in as it goes. Then the
        scorers' rule doubts become questions on the Rules tab (doubts.py). Not done here yet (the run skill does
        it with judgement): second reads. The answers still carry their flags."""
        from .cmd_find import run_finder
        names = {u.get("id"): u.get("name") or u.get("id") for u in self.bundle().get("units") or []}
        project_meta = self.project.read("project")
        kind, rows, qids = req["kind"], list(req["rows"]), list(req["questions"])
        if not [q for q in self.project.read("questions").get("questions") or [] if isinstance(q, dict) and q.get("id")]:
            job["line"] = "Nothing to score"
            self.note("There are no questions to score yet. Finish the questions on the Set-up tab first.", "warning")
            return
        self._clear_old_stop(req.get("asked"))
        if kind == "trial" and not rows:
            first = next(iter(self.project.read("questions").get("questions") or []), {}).get("id")
            rows = (self._json_out("cover", "--question", str(first), "--size", str(TRIAL_ROWS)).get("rows") or [])[:TRIAL_ROWS] if first else []
        with self.cmd.lock:
            open_full = next((r for r in self.project.runs() if r.get("kind") == "full" and r.get("finished") is None), None)
            # Carrying on a paused trial (the run bar's Resume, the chat's resume) goes on in the same trial run
            # rather than opening a second one beside it.
            open_trial = next((r for r in self.project.runs() if r.get("kind") == "trial" and r.get("finished") is None
                               and rows and sorted(r.get("rows") or []) == sorted(rows)
                               and sorted(r.get("questions") or []) == sorted(qids)), None)
            if kind == "full" and open_full:
                run_id = open_full["id"]
            elif kind == "trial" and open_trial:
                run_id = open_trial["id"]
            else:
                args = ["run", "start", "--kind", kind] + (["--rows", ",".join(rows)] if rows else []) \
                    + (["--questions", ",".join(qids)] if qids else []) + ["--json"]
                run_id = json.loads(last_line(self.cmd.must(*args)))["id"]
        self.build()

        def in_scope(listed):
            return [x for x in listed if not rows or x in rows]

        # The finder looks only for the questions this run covers (sweep round 3: told every question, it searched
        # for all of them in a run scoped to a third).
        find_qs = qids or None
        if find_qs is None:
            from . import control
            try:
                find_qs = control.read(self.project)["scope"]["questions"]
            except HandbookError:
                find_qs = None

        # Phase 1: documents. Group-wide ones first (once per run), then each row's.
        # Only what the job's questions need (es-imco-bipe, 21 Oct 2026: a trial of 15 of 116 questions searched
        # for all 116); a row an earlier search covered for other questions only is listed for the rest.
        listing = self._json_out("todo", "--find", *(["--questions", ",".join(qids)] if qids else []))
        partly = listing.get("questions") or {}
        # What is left to score is decided first: finding never runs for a row with nothing to score (tpi-mq,
        # 22 Oct 2026: a row whose found/ file was gone but whose answers stood spent 335k tokens finding, then
        # was reported already scored), and a row's search is only for the questions it has left.
        pending = {}
        for w in self._json_out("todo").get("rows") or []:
            left = [q for q in w.get("questions") or [] if not qids or q in qids]
            if left and (not rows or w.get("row") in rows):
                pending[w.get("row")] = left
        for row, wanted in list(partly.items()):
            partly[row] = [q for q in wanted if q in (pending.get(row) or [])]
        to_find = [r for r in in_scope(listing.get("rows") or []) if r in pending
                   and (r not in partly or partly[r])]
        job.update({"left": list(rows or to_find), "questions": qids})      # for a pause far over the estimate
        run = self.project.run(run_id) or {}
        done_groups = set((run.get("group_usage") or {}).keys())
        units = [u for u in self.project.read("units").get("units") or [] if u.get("id") in to_find]
        for label in project_meta.get("group_labels") or []:
            values = {}
            for u in units:
                v = (u.get("labels") or {}).get(label)
                if v:
                    values[v] = values.get(v, 0) + 1
            for value, n in values.items():
                # A group finder only where the whole list has two or more such rows (the finder checks too).
                if n < 2 or f"{label}={value}" in done_groups:
                    continue
                if (why := self._stop_now(job)):
                    return self._scoring_stopped(job, why)
                job["line"] = f"Finding documents for every {label} {value}"
                got = self._find(job, run_finder, group=(label, value), **({"questions": find_qs} if find_qs else {}))
                with self.cmd.lock:
                    self.cmd.run("run", "usage", "--run", run_id, "--group", f"{label}={value}", "--step", "find",
                                 "--tokens", str(got.get("tokens") or 0))
        job.update({"total": len(to_find) * 2 or 1, "done": 0})
        unsearched = []                 # rows whose search broke off: not scored blind (20 Oct 2026 review)
        for i, row in enumerate(to_find, 1):
            if (why := self._stop_now(job, row)):
                return self._scoring_stopped(job, why)
            job["line"] = f"Finding documents for {names.get(row, row)} ({i} of {len(to_find)})"
            # the questions this row has left (todo --find), else the job's questions or the control's scope
            wanted = partly.get(row) or ([q for q in find_qs if q in pending[row]] if find_qs else None)
            got = self._find(job, run_finder, row=row, **({"questions": wanted} if wanted else {}))
            if got.get("broke"):
                unsearched.append(row)
            with self.cmd.lock:
                self.cmd.run("run", "usage", "--run", run_id, "--row", row, "--step", "find", "--tokens", str(got.get("tokens") or 0))
                job["line"] = f"Reading the documents for {names.get(row, row)}"
                self.cmd.run("prepare", timeout=1800)
            job["done"] = i
            self.build()

        self._archive_again(job, to_find)

        # Phase 2: score each row blind from a kit, check, rebuild.
        work = [w for w in self._json_out("todo").get("rows") or []
                if (not rows or w.get("row") in rows) and w.get("row") not in unsearched]
        if unsearched:
            self.note(f"The document search for {', '.join(names.get(r, r) for r in unsearched)} did not finish, so "
                      f"{'it was' if len(unsearched) == 1 else 'they were'} not scored. Start scoring again to try once more.",
                      "warning")
        if kind == "trial" or qids:
            work = [dict(w, questions=[q for q in w.get("questions") or [] if not qids or q in qids]) for w in work]
            work = [w for w in work if w["questions"]]
        job.update({"total": len(to_find) + len(work) or 1, "done": len(to_find), "left": [w["row"] for w in work]})
        failed = []
        for i, w in enumerate(work, 1):
            row, qs = w["row"], w.get("questions") or []
            if (why := self._stop_now(job, row)):
                return self._scoring_stopped(job, why)
            label = f"{names.get(row, row)} ({i} of {len(work)})"
            try:
                # The same per-row step as a re-run: a scorer stopped at its budget keeps its answers and the
                # row waits on the Grid for the owner, while the job goes on (rowwatch.py).
                self._score_row(job, run_id, row, qs, label, names.get(row, row))
            except HandbookError as e:
                failed.append(row)
                self.note(f"{names.get(row, row)} could not be scored.", "warning", str(e)[-600:])
            job["done"] = len(to_find) + i
            job["left"] = [r for r in job["left"] if r != row]
            self.build()

        # Phase 3: one wider second search for a row whose answers say its documents fell short, as the run skill's
        # (gh5050, 21 Oct 2026: one organisation's homepage only, 17 of 18 answers with no evidence), then its rule
        # doubts become questions on the Rules tab.
        stopped = {st["row"] for st in job["steps"] if st.get("left")}
        for w in work:
            row = w["row"]
            if row in failed or row in stopped:
                continue
            if (why := self._stop_now(job, row, over=False)):
                return self._scoring_stopped(job, why)
            if (why := self._wider_search(job, run_id, row, names.get(row, row), run_finder, qids)):
                return self._scoring_stopped(job, why)
        doubted, asked = self._rule_doubts(job, kind, [w["row"] for w in work if w["row"] not in failed])
        with self.cmd.lock:
            self.cmd.run("check")
            if (why := self._stop_now(job, over=False)) is None:      # nothing left to do: no pause for the estimate
                self.cmd.run("run", "finish", "--run", run_id)
            if (self.project.path / "compare" / "compare.json").is_file() or self.project.held_back.is_dir():
                self.cmd.run("compare")
        est = self._json_out("estimate") if kind == "trial" else {}
        self.build()
        # A row whose scorer stopped at its budget before answering anything waits on the Grid (its own note says so).
        waiting = {st["row"] for st in job["steps"] if st.get("left") and len(st["left"]) == len(st.get("questions") or [])}
        scored = len(work) - len(failed) - len(waiting)
        # What the answers came to, and what waits for the owner's data (needs.py): said plainly, never "good"
        # when almost nothing could be answered (Consumer Reports, 21 Oct 2026: four empty answers read as good,
        # with an offer to score the rest for 897,000 tokens).
        got = needs.outcome(self.project, rows or self._row_ids(), qids or None)
        held = sum(got["waiting"].values())
        if not work and not unsearched:
            job["line"] = "Nothing to score"
            self.log_usage({"kind": kind, "job": job["key"], "processed": job["tokens"], "estimate": job.get("estimate")})
            if held:
                self.note("Nothing could be scored, because every answer left rests " + self._owner_data_words(got), "warning",
                          self.spent(job) if job["tokens"] else None)
                return
            self.note(("Those are already scored, so nothing was scored. To score them again, ask the chat to "
                       "re-score them (it shows the cost first). " if rows or qids else "Everything is already scored. ")
                      + (self.spent(job) if job["tokens"] else ""), "neutral")
            return
        total = got["answered"] + got["no_evidence"] + held
        little = total > 0 and got["answered"] < needs.ALMOST_NOTHING * total
        said = f"Scored {scored} {self._plural() if scored != 1 else (project_meta.get('unit_word') or 'row')}"
        if failed:
            said += f"; {len(failed)} could not be scored"
        if waiting:
            said += f"; {len(waiting)} stopped at {'its' if len(waiting) == 1 else 'their'} token budget and wait{'s' if len(waiting) == 1 else ''} on the Grid for you"
        said += ". "
        # In the order it happened: the wider search (and the answers scored again), then what the answers came
        # to, what waits for the owner's data, and the new rule questions.
        if (wider := job.get("wider")):
            one = (project_meta.get("unit_word") or "row") if wider["rows"] == 1 else self._plural()
            said += (f"A wider search for {wider['rows']} {one} found "
                     + (f"{wider['saved']} more document{'s' if wider['saved'] != 1 else ''}, and those answers were "
                        "scored again. " if wider["saved"] else "nothing more. "))
        if little:
            said += (f"Only {got['answered']} of {total} answers could be found. " if got["answered"]
                     else f"None of the {total} answers could be found. ")
        if held:
            said += f"{held} answer{' waits' if held == 1 else 's wait'} because {'it rests' if held == 1 else 'they rest'} " \
                    + self._owner_data_words(got) + " "
        # A need is named only for the answers that rest on it: those waiting for it (above), and the empty answers
        # to its questions (gh5050, 21 Oct 2026: empty answers were put down to a need they had nothing to do with).
        for_needs, unexplained = got["no_evidence_for_needs"], got["no_evidence"] - got["no_evidence_for_needs"]
        if little and got["open_needs"]:
            said += ("The answers with no evidence rest" if not unexplained else
                     f"{for_needs} of the {got['no_evidence']} answers with no evidence {'rests' if for_needs == 1 else 'rest'}") \
                    + " on data you still need to supply (" + self._need_words(got["open_needs"]) + "). "
        if asked:
            said += (f"Claude was unsure how the rules apply to {doubted} answer{'s' if doubted != 1 else ''}, so there "
                     f"{'is 1 new question' if asked == 1 else f'are {asked} new questions'} for you on the Rules tab. ")
        said += "Check the answers on the Grid. " + self._spent_words(job)
        if little:
            if got["open_needs"] or (held and not unexplained):
                said += " Scoring the rest now would find little, so add that data first" \
                        + (", and check where Claude looked." if unexplained else ".")
            else:
                said += " Check where Claude looked before scoring the rest."
        elif est.get("point"):
            low, high = est.get("low") or est["point"], est.get("high") or est["point"]
            rough = (f" (from this trial alone, so a rough figure: trying a few more {self._plural()} would make it "
                     "surer)") if scored < 3 else ""
            said += (f" Scoring the rest should use about {show_tokens(est['point'])}" if round(low, -5) == round(high, -5)
                     else f" Scoring the rest should use about {show_tokens(low)} to {show_tokens(high)}") + rough + "."
        job["line"] = "Done"
        self.log_usage({"kind": kind, "job": job["key"], "processed": job["tokens"], "estimate": job.get("estimate")})
        top = (job.get("estimate") or {}).get("high")
        far_over = bool(top) and job["tokens"] > OVER_ESTIMATE * top
        self.note(said, "good" if not failed and not waiting and not little and not far_over else "warning")

    def _spent_words(self, job: dict) -> str:
        """What a finished job used (`spent`), said plainly as more than estimated, with the figures, when it went
        past the top of its estimate."""
        est = job.get("estimate") or {}
        high = est.get("high")
        if not high or job["tokens"] <= high:
            return self.spent(job)
        said = (f"It used more than estimated: {job['tokens']:,} tokens processed on your Claude plan, against an "
                f"estimate of about {show_tokens(est.get('low') or high)} to {show_tokens(high)}")
        times = job["tokens"] / high
        if times >= 2:
            said += " (about " + (f"{times:.1f}".rstrip("0").rstrip(".") if times < 10 else f"{round(times):,}") \
                + " times its top)"
        return said + "."

    def _need_words(self, ids: list[str]) -> str:
        """The needs `ids` in the owner's words: what each asks for, cut short."""
        listed = {n.get("id"): n for n in self.project.read("needs").get("needs") or [] if isinstance(n, dict)}
        out = []
        for nid in ids:
            what = " ".join(str((listed.get(nid) or {}).get("what") or nid).split())
            out.append(what if len(what) <= 120 else what[:120].rsplit(" ", 1)[0] + "…")
        return "; ".join(out)

    def _owner_data_words(self, got: dict) -> str:
        """The end of a sentence about answers waiting for the owner's data: which data, and how to send it."""
        return (f"on data only you hold ({self._need_words(list(got['waiting']))}). Claude does not search the web "
                "for it. Drop the file on the chat to add it, and those answers are scored from it.")

    def _archive_again(self, job: dict, rows: list[str]) -> None:
        """Web pages these rows kept as they are today because the web archive did not answer or was busy get one
        more try at its copy from before the cut-off, once the finding is done (glaad-smsi, 21 Oct 2026: 8 pages
        kept as today's with no retry). Bounded: ARCHIVE_AGAIN pages a job, one try each."""
        docs = self.project.documents()
        ids = sorted({d for r in rows for d in self.project.found(r).get("documents") or []
                      if re.search(r"did not answer|too busy",
                                   str(((docs.get(d) or {}).get("todays_version") or {}).get("why") or ""))})
        for doc in ids[:ARCHIVE_AGAIN]:
            job["line"] = "Asking the web archive again for pages as they stood at the cut-off"
            with self.cmd.lock:
                self.cmd.run("recover-shells", "--doc", doc, "--archive", timeout=900)

    def _wider_search(self, job: dict, run_id: str, row: str, name: str, run_finder, qids=None) -> str | None:
        """A row's one wider second search, when its answers call for it (cmd_find.wider_search): one finder
        session told what was missing; if it saved anything, the documents are read and those answers scored again.
        Returns "over", with the run paused, when the job is far over its estimate before a search it would make."""
        from .cmd_find import wider_search
        need = wider_search(self.project, row)
        if need and qids:
            need["questions"] = [q for q in need["questions"] if q in qids]     # only the job's questions
        if not need or not need["questions"]:
            return None
        if self._over_estimate(job):           # work is left (this search), so a job far over its estimate pauses
            return "over"
        job["line"] = f"Looking wider for {name}'s missing documents"
        before = set(self.project.found(row).get("documents") or [])
        got = self._find(job, run_finder, row=row, missing=need["missing"], rounds=1,
                         **({"questions": need["questions"]} if qids else {}))
        # Only documents new to the row count (a second address for one on file is not: tpi-mq, 22 Oct 2026).
        metas = self.project.documents()
        new = [d for d in self.project.found(row).get("documents") or []
               if d not in before and not (metas.get(d) or {}).get("not_kept_why")]
        wider = job.setdefault("wider", {"rows": 0, "saved": 0})
        wider["rows"] += 1
        wider["saved"] += len(new)
        with self.cmd.lock:
            self.cmd.run("run", "usage", "--run", run_id, "--row", row, "--step", "second_find",
                         "--tokens", str(got.get("tokens") or 0))
            if new:
                job["line"] = f"Reading the new documents for {name}"
                self.cmd.run("prepare", timeout=1800)
        if new:
            # The answers the search was for, read again from what it added and what they cited, not the whole kit
            # (tpi-mq and de-transparenzranking, 22 Oct 2026: 2.41M and 2.77M, most of it the kit read again).
            cells = self.project.answers(row).get("cells") or {}
            cited = [e.get("doc") for q in need["questions"] for e in ((cells.get(q) or {}).get("evidence") or [])
                     if isinstance(e, dict) and e.get("doc") in before]
            docs = list(dict.fromkeys(new + cited))
            try:
                self._score_row(job, run_id, row, need["questions"], f"{name} (again, with the new documents)", name,
                                docs=docs)
            except HandbookError as e:
                self.note(f"{name} could not be scored again with the new documents.", "warning", str(e)[-600:])
        if not got.get("broke"):
            with self.cmd.lock:
                self.cmd.run("found", "--row", row, "--second-search-done")

    def _rule_doubts(self, job: dict, kind: str, rows: list[str]) -> tuple[int, int]:
        """The scorers' rule doubts on `rows` become rule questions on the Rules tab, as the run skill's do
        (doubts.py): one small Claude call, its tokens counted in the job. Returns (doubts, questions raised)."""
        from . import doubts
        found = doubts.open_doubts(self.project, rows)
        if not found:
            return 0, 0
        job["line"] = "Turning Claude's doubts about the rules into questions for you"
        try:
            drafted, usage = doubts.draft(self.project, found)
        except HandbookError as e:
            self.note("Claude's doubts about how the rules apply could not be turned into questions this time. "
                      "The answers keep their doubts.", "warning", str(e)[-400:])
            return len(found), 0
        tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens", "output_tokens"))
        job["tokens"] += tokens
        self.log_usage({"kind": "rule-doubts", "job": job["key"], "processed": tokens, "doubts": len(found)})
        with self.cmd.lock:
            got = doubts.settle(self.project, drafted, "trial" if kind == "trial" else "run",
                                rows=sorted({d["row"] for d in found}))
        return len(found), len(got["raised"])

    def _find(self, job: dict, run_finder, **who) -> dict:
        try:
            got = run_finder(self.project, **who)
        except HandbookError as e:
            self.note("A document search could not run.", "warning", str(e)[-600:])
            return {"tokens": 0, "broke": True}
        job["tokens"] += int(got.get("tokens") or 0)
        self.log_usage({"kind": "find", "job": job["key"], "processed": got.get("tokens"), "ok": got.get("ok")})
        return got

    def _scoring_stopped(self, job: dict, why: str) -> None:
        self.build()
        if why == "over":
            # What was spent, against what was expected, and what the rest should use (with finding).
            high = job["estimate"]["high"]
            left = [r for r in job.get("left") or [] if r]
            plural = self._plural() if len(left) != 1 else ((self.bundle().get("project") or {}).get("unit_word") or "row")
            rest = costs.estimate_rows(self.project, left, job.get("questions") or None, finding=True) if left else {}
            job["line"] = "Stopped (paused, far over its estimate)"
            self.note(f"Scoring is paused because it has used {show_tokens(job['tokens'])} tokens, about "
                      f"{max(OVER_ESTIMATE, round(job['tokens'] / high))} times the top of its estimate "
                      f"({show_tokens(high)}). "
                      + (f"{len(left)} {plural} still to score should use about {show_tokens(rest['low'])} to "
                         f"{show_tokens(rest['high'])} more. " if rest.get("high") else "")
                      + "Nothing is lost. Press Carry on to go on, or leave it here.", "warning")
            return
        job["line"] = f"Stopped ({why})"
        self.note(f"Scoring {why}. Nothing is lost: press Carry on, or ask in the chat, to pick up where it left off. "
                  + self.spent(job), "neutral")

    def _follow_draft(self, job: dict, done: threading.Event) -> None:
        """While the set-up draft runs, show what Claude is doing (cmd_draft_setup's progress file) as the
        job's line, and rebuild the page whenever a set-up part has just been written, so the checklist
        ticks over part by part instead of all at the end (Benjy, 19 Oct 2026: "it only says 1 of 8")."""
        from .cmd_draft_setup import PROGRESS
        path = self.project.path.joinpath(*PROGRESS)
        landed: set[str] = set()
        while not done.wait(2.0):
            got = read_json(path, None) or {}
            if not got or got.get("finished"):
                continue
            if got.get("line"):
                job["line"] = got["line"]
            job["steps"] = [{"files_read": got.get("files_read") or 0, "files_total": got.get("files_total") or 0,
                             "wrote": got.get("wrote") or [], "started": got.get("started")}]
            fresh = set(got.get("wrote") or []) - landed
            if fresh and not str(got.get("line") or "").startswith(("Checking", "Building")):
                landed |= fresh
                try:
                    self.build()
                except Exception:  # noqa: BLE001 - a half-written draft may not build yet; the final build follows
                    pass

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
                        "failed": len(problems), "estimate": job.get("estimate")})
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
        if control.get("paused") is True or control.get("stop_now") is True:
            return                        # a re-run waits while the owner has paused or stopped (Resume starts it)
        for r in control.get("reruns") or []:
            if isinstance(r, dict) and isinstance(r.get("id"), str) \
                    and (self.team is None or self.team.owns(r.get("by"), r.get("on"))):
                self.enqueue("rerun:" + r["id"], "rerun", rerun_title(r),
                             lambda job, req=dict(r): self.rerun(job, req),
                             estimate=lambda req=dict(r): costs.estimate_rows(self.project, list(req.get("rows") or []),
                                                                              [str(q) for q in req.get("questions") or []]))

    def _queue_rereads(self, _unused) -> None:
        """A correction whose words are in a link or a file the owner sent is read again here, blind.
        One on a page saved in the project is checked by the page itself (views/cell.js), at once;
        if that could not happen, its "Check it now" button asks again, so no scorer starts unasked."""
        rounds = (read_json(self.project.file("corrections"), {}) or {}).get("corrections") or []
        for r in rounds:
            if r.get("status") != "to_check" or not r.get("id"):
                continue
            if self.team is not None and not self.team.owns(r.get("by"), r.get("on")):
                continue                      # a colleague's correction: their own tool reads it again
            ev = r.get("evidence") or {}
            if not (ev.get("link") or ev.get("asset") or ev.get("local")):
                continue
            self.enqueue("reread:" + r["id"], "reread", f"Checking your correction on {r.get('row')} question {r.get('qid')}",
                         lambda job, rnd=dict(r): self.reread(job, rnd),
                         estimate=lambda rnd=dict(r): costs.estimate_rows(self.project, [str(rnd.get("row"))], [str(rnd.get("qid"))]))

    def _score(self, job: dict, kit: str) -> int:
        """Run one scorer on a kit (outside the project lock) and return the tokens it processed."""
        code, out = self.cmd.run("score", "--kit", kit, project=False, timeout=SCORE_TIMEOUT_S)
        receipt = read_json(Path(kit) / "receipt.json", {}) or {}
        usage = receipt.get("usage") or {}
        tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens", "output_tokens"))
        job["tokens"] += tokens
        self.log_usage({"kind": "score", "job": job["key"], "kit": Path(kit).name, "processed": tokens,
                        "turns": receipt.get("num_turns"), "ok": code == 0, "job_estimate": job.get("estimate")})
        # A scorer stopped at its budget or turn cap before it wrote any answer is not a failure: _score_row says
        # so and the row waits on the Grid (20 Oct 2026: Argentina read "could not be scored. Receipt: /var/...").
        if code != 0 and not (Path(kit) / "answers.json").is_file() and not receipt.get("stopped"):
            raise HandbookError(last_line(out) or "The scorer did not finish.")
        return tokens

    def _values(self, rows, qids) -> dict:
        cells = self.bundle().get("cells") or {}
        return {(r, q): (cells.get(r) or {}).get(q, {}).get("value") for r in rows for q in qids}

    def _score_row(self, job: dict, run_id: str, row: str, qs: list[str], label: str, name: str,
                   docs: list[str] | None = None) -> int:
        """Kit, scorer, usage, ingest and check for one row's questions; returns the tokens. A scorer
        stopped at its budget or turn cap (cmd_score.py) keeps what it wrote: that is ingested, the
        questions left stay not scored, and the job goes on to its next row. The row then waits on the
        page (rowwatch.py) for the owner: Carry on (a re-run of what is left, in a fresh kit), upload a
        document, or leave it."""
        job["line"] = f"{label}: getting the documents ready"
        with self.cmd.lock:
            kit = last_line(self.cmd.must("kit", "--row", row, "--run", run_id, "--questions", ",".join(qs),
                                          *(["--docs", ",".join(docs)] if docs is not None else [])))
        job["line"] = f"{label}: Claude is scoring {len(qs)} answer{'s' if len(qs) != 1 else ''}"
        tokens = self._score(job, kit)
        receipt = read_json(Path(kit) / "receipt.json", {}) or {}
        job["line"] = f"{label}: checking the answers"
        with self.cmd.lock:
            self.cmd.run("run", "usage", "--run", run_id, "--row", row, "--step", "score",
                         "--receipt", str(Path(kit) / "receipt.json"))
            if (Path(kit) / "answers.json").is_file():
                code, out = self.cmd.run("ingest-answers", "--row", row, "--run", run_id,
                                         "--file", str(Path(kit) / "answers.json"))
                if code != 0 and not receipt.get("stopped"):
                    raise HandbookError(last_line(out) or "The answers could not be stored.")
            self.cmd.run("check")
        left = [q for q in receipt.get("left") or [] if q in qs] if receipt.get("stopped") else []
        job["steps"].append({"row": row, "questions": qs, "tokens": tokens, "stopped": receipt.get("stopped"), "left": left})
        if left:
            self.note(f"{name} was stopped at its token budget after {tokens:,} tokens, with {len(left)} "
                      f"question{'s' if len(left) != 1 else ''} left. It waits on the Grid for you: carry on, "
                      "upload a document, or leave it.", "warning", f"Questions left: {', '.join(left)}.")
        return tokens

    def rescore_missing(self, job: dict, row: str, qs: list[str], what: str) -> None:
        """The questions a scorer could not answer for want of a document the owner has now sent:
        scored again, on that row only, in a small fresh kit."""
        name = next((u.get("name") for u in self.bundle().get("units") or [] if u.get("id") == row), None) or row
        job.update({"total": 1, "done": 0})
        with self.cmd.lock:
            out = self.cmd.must("run", "start", "--kind", "rerun", "--questions", ",".join(qs), "--rows", row, "--json")
        run_id = json.loads(last_line(out))["id"]
        try:
            self._score_row(job, run_id, row, qs, name, name)
        finally:
            with self.cmd.lock:
                self.cmd.run("run", "finish", "--run", run_id)
                self.cmd.run("check")
            job["done"] = 1
            self.build()
        self.note(f"{name}: question{'s' if len(qs) != 1 else ''} {', '.join(qs)} scored again with {what}.", "good",
                  self.spent(job))

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
            self.note(f"{rerun_title(req)}: nothing to score again.", "neutral")
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
            if (why := self._stop_now(job)):             # Pause and Stop now hold for a re-run too
                return self._rerun_stopped(job, req, run_id, [r for r, _ in work[i - 1:]], i - 1, len(work), why)
            label = f"{names.get(row) or row} ({i} of {len(work)})"
            try:
                self._score_row(job, run_id, row, qs, label, names.get(row) or row)
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
        self.note(f"{rerun_title(req)} done: {done} of {len(work)} rows scored again, "
                  f"{moved} answer{'s' if moved != 1 else ''} changed.", "good" if not failed else "warning",
                  self.spent(job))

    def _rerun_stopped(self, job: dict, req: dict, run_id: str, left: list[str], done: int, total: int, why: str) -> None:
        """A re-run stopped by Pause or Stop now: its run is closed and the request stays on file with the
        rows still to do, so Resume picks it up from there (it is not queued again while the stop holds)."""
        from . import control
        with self.cmd.lock:
            self.cmd.run("run", "finish", "--run", run_id)
            c = control.read(self.project)
            for r in c["reruns"]:
                if r.get("id") == req.get("id"):
                    r["rows"] = left
            self.project.write("control", c)
            self.cmd.run("check")
        self.build()
        job["line"] = f"Stopped ({why})"
        self.note(f"{rerun_title(req)} {why} after {done} of {total} rows. The other {len(left)} wait: press Resume "
                  "in the run bar to finish them. " + self.spent(job), "neutral")

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
        try:
            self._score(job, kit)
        except HandbookError:
            with self.cmd.lock:          # the run is closed either way; the correction waits (RETRY_S)
                self.cmd.run("run", "finish", "--run", run_id)
            raise
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
                  tone, self.spent(job))

    # -- the corner chat's background jobs (chatwork.py, cmd_job.py) ---------------------------------
    # A job the owner confirmed in the chat: a copy of the project first, then one background Claude
    # session on the brief, then check and build. Its record (outside the project, so an Undo keeps
    # it) holds where the copy is; Undo puts the project back from it.

    def _slug(self) -> str:
        return re.sub(r"[^a-z0-9-]+", "-", self.project.path.name.lower()).strip("-") or "project"

    def _jobs_file(self) -> Path:
        return home() / "jobs" / f"{self._slug()}.json"

    def chat_jobs(self) -> list[dict]:
        return (read_json(self._jobs_file(), {}) or {}).get("jobs") or []

    def _save_job(self, record: dict) -> None:
        jobs = [j for j in self.chat_jobs() if j.get("id") != record["id"]] + [record]
        path = self._jobs_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"jobs": jobs[-40:]}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def queue_chat_job(self, req: dict) -> dict:
        jid = "job-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        title = " ".join(str(req.get("title") or "A task from the chat").split())[:90]
        record = {"id": jid, "title": title, "brief": str(req.get("brief") or "").strip(), "status": "queued",
                  "asked": now_iso(), "estimate": req.get("estimate") if isinstance(req.get("estimate"), dict) else None}
        if not record["brief"]:
            raise HandbookError("The job has no task.")
        self._save_job(record)
        est = record["estimate"]
        self.enqueue("chatjob:" + jid, "chat-job", title, lambda job, r=dict(record): self.chat_job(job, r),
                     estimate=(lambda: est) if est and est.get("high") else None)
        return record

    def chat_job(self, job: dict, record: dict) -> None:
        job.update({"total": 1, "done": 0, "line": "Taking a copy of the project first, so you can undo this"})
        with self.cmd.lock:
            before = chatwork.fingerprint(self.project.path)
            snap = chatwork.snapshot(self.project.path, home() / "snapshots" / self._slug(), record["id"])
        record.update({"status": "running", "started": now_iso(), "snapshot": str(snap)})
        self._save_job(record)
        brief = Path(tempfile.mkdtemp(prefix="hb-job-")) / "brief.txt"
        brief.write_text(record["brief"], encoding="utf-8")
        job["line"] = "Claude is working on it"
        code, out = self.cmd.run("job", "--brief", str(brief), "--json", timeout=2800)
        self._what_changed(record, before)
        try:
            got = json.loads(out.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            got = {}
        job["tokens"] += int(got.get("tokens") or 0)
        self.log_usage({"kind": "chat-job", "job": job["key"], "processed": job["tokens"], "turns": got.get("turns"),
                        "ok": code == 0, "estimate": job.get("estimate")})
        job["line"] = "Checking the answers and rebuilding the page"
        with self.cmd.lock:
            self.cmd.run("check")
        self.build()
        said = str(got.get("result") or "").strip() or last_line(out)
        record.update({"status": "done" if code == 0 else "failed", "finished": now_iso(), "tokens": job["tokens"],
                       "result": said[:1500]})
        self._save_job(record)
        self._team_log("chat_job", f"asked Claude in the chat to {record['title'][:1].lower() + record['title'][1:]}")
        job.update({"done": 1, "line": "Done"})
        self.note(("Done: " if code == 0 else "Stopped before it finished: ") + record["title"] + ". " + self.spent(job),
                  "good" if code == 0 else "warning", said[-800:] or None)

    # Commands the chat may not run behind the page: they start another server, make another
    # project or open windows. Everything else in hb is the chat's, with Confirm, a copy and Undo.
    CHAT_HB_BLOCKED = ("serve", "launcher", "init")
    CHAT_HB_READ_ONLY = ("help", "status", "validate", "todo", "cost", "estimate", "doctor", "material-text", "names")
    CHAT_HB_SLOW = ("find", "score", "draft-setup", "job", "fetch", "prepare", "second-finds", "second-reads", "compare")

    def changed_by_chat(self, title: str, run, hold: bool = True) -> dict:
        """A change the owner confirmed in the chat that writes to the project at once (hb change, or an hb
        command): a copy first, then `run()`, then check and build; listed with the jobs, so Undo puts it back."""
        rid = "change-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        with self.cmd.lock:
            before = chatwork.fingerprint(self.project.path)
            snap = chatwork.snapshot(self.project.path, home() / "snapshots" / self._slug(), rid)
            record = {"id": rid, "title": title[:90], "brief": "", "status": "running", "kind": "change",
                      "asked": now_iso(), "started": now_iso(), "snapshot": str(snap), "tokens": 0}
            self._save_job(record)
            if hold:
                code, out = run()
                self._what_changed(record, before)
        if not hold:                      # a slow command takes the lock itself when it writes
            code, out = run()
            self._what_changed(record, before)
        self._team_log("chat_change", f"changed in the chat: {record['title']}")
        with self.cmd.lock:
            self.cmd.run("check")
        self.build()
        record.update({"status": "done" if code == 0 else "failed", "finished": now_iso(), "result": out[-1500:]})
        self._save_job(record)
        return {"code": code, "out": out, "id": rid}

    def chat_hb(self, args: list) -> dict:
        """Runs one hb command the owner confirmed in the chat, on this project. Quick ones answer at
        once; slow ones (finding, scoring, a set-up draft) run as a job with progress in the run bar."""
        args = [str(a) for a in args if str(a) not in ("--project",)]
        if not args or args[0] in self.CHAT_HB_BLOCKED:
            raise HandbookError(f"hb {args[0] if args else ''} is not run from the chat: it would start another page or project.")
        if any(a.startswith("--project") for a in args):
            raise HandbookError("The chat's commands always work on this project.")
        if args[0] == "score" or args[:2] == ["run", "start"]:
            # 20 Oct 2026: run start from the chat left an open run that scored nothing, and the card said Done.
            raise HandbookError("That command on its own scores nothing here. To score, use start_scoring (rows, "
                                "questions); to score answers again, propose_rescore.")
        if args[0] in self.CHAT_HB_READ_ONLY:
            code, out = self.cmd.run(*args, timeout=300)
            return {"code": code, "out": out[-6000:]}
        if args[0] in self.CHAT_HB_SLOW or (args[0] == "recheck" and ("--row" in args or "--all" in args)):
            # A re-check of a row's or every saved page fetches each one politely: behind the page, with the run bar.
            title = "hb " + " ".join(args)[:80]

            def slow(job, a=list(args)):
                got = self.changed_by_chat(title, lambda: self.cmd.run(*a, timeout=2800), hold=False)
                job.update({"done": 1, "total": 1, "line": "Done" if got["code"] == 0 else "Stopped"})
                self.note(("Done: " if got["code"] == 0 else "Stopped: ") + title, "good" if got["code"] == 0 else "warning", got["out"][-800:])
            self.enqueue("chathb:" + now_iso(), "chat-hb", title, slow)
            return {"code": 0, "out": "Started behind the page; progress shows in the run bar.", "queued": True}
        got = self.changed_by_chat("hb " + " ".join(args)[:80], lambda: self.cmd.run(*args, timeout=900))
        return {"code": got["code"], "out": got["out"][-6000:], "undo": got["id"]}

    def undo_chat_job(self, jid: str) -> dict:
        record = next((j for j in self.chat_jobs() if j.get("id") == jid), None)
        if record is None:
            raise HandbookError("There is no such job.")
        if record.get("undone"):
            raise HandbookError("That job has already been undone.")
        if record.get("status") in ("queued", "running") or self.job is not None:
            raise HandbookError("Something is still running. Undo once it has finished.")
        keep = home() / "snapshots" / self._slug() / (jid + "-undo-" + datetime.now().strftime("%H%M%S"))
        snap = Path(record.get("snapshot") or "/nonexistent")
        later = []
        with self.cmd.lock:
            if isinstance(record.get("changed"), dict):
                # Only this change's own files, and only while they are as it left them (chatwork.undo_changes).
                chatwork.undo_changes(self.project.path, snap, record["changed"], record.get("had") or [], keep)
            elif self.team is not None and self.team.on:
                raise HandbookError("This change was made before the tool could undo one change on its own, and putting "
                                    "the whole project back would lose your colleagues' work since. Ask the chat to put "
                                    "it back as it was instead.")
            else:                         # a change recorded by 1.0.0: the whole copy, and the later changes with it
                later = [j for j in self.chat_jobs() if j.get("started", "") > record.get("started", "") and not j.get("undone")]
                chatwork.restore(self.project.path, snap, keep)
            self.cmd.run("check")
        record["undone"] = now_iso()
        self._save_job(record)
        for j in later:                   # the copy was taken before them, so they are undone too
            j["undone"] = record["undone"]
            self._save_job(j)
        self.build()
        self._team_log("undone", f"undid a change made in the chat: {record['title']}")
        self.note("Undone: " + record["title"] + (". The project is back as it was before." if later or "changed" not in record
                  else ". What it changed is back as it was; nothing else was touched."), "good")
        return record

    def _what_changed(self, record: dict, before: dict) -> None:
        """Which files a chat change or job touched, kept on its record so Undo puts back only those."""
        after = chatwork.fingerprint(self.project.path)
        record["changed"] = chatwork.changes(before, after)
        record["had"] = sorted(rel for rel in record["changed"] if rel in before)

    def _team_log(self, kind: str, text: str) -> None:
        """A chat change, job or Undo in the team's log, so colleagues see it (team.py; only in a shared folder)."""
        if self.team is None or not self.team.on:
            return
        from . import team
        person = self.team.person()
        team.log(self.project, person, kind, text=f"{person.get('name') or 'Someone'} {text}")

    # -- for the page ----------------------------------------------------------------------------

    @staticmethod
    def public_job(job):
        if not job:
            return None
        return {k: job.get(k) for k in ("key", "kind", "title", "status", "done", "total", "line", "tokens",
                                        "started", "finished", "rule", "steps", "estimate")}

    def state(self) -> dict:
        public = self.public_job
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
    viewer = project.viewer
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

    def __init__(self, project: Project, port: int = 0, config: Config | None = None, claude: str = "claude",
                 token: str | None = None):
        super().__init__(("127.0.0.1", port), Handler)
        self.project = project
        self.token = token or secrets.token_urlsafe(24)       # a restart after an update keeps the pass
        self.marker = None                 # set by cmd_serve: ~/.handbook/running/<key>.json
        self.restarting = False
        self.updater = Updater(self)
        self._update_seen = (0.0, None)
        self.alive_key = None              # set by cmd_serve: a hash of the project's path
        self.config = config or Config()
        self.store = Store(project.path)
        self.worker = Worker(project, self.store)
        from .teamwork import TeamSide
        self.worker.team = self.team = TeamSide(self.worker, self.config)
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

    # -- updates (updater.py) ------------------------------------------------------------------

    @property
    def project_path(self) -> Path:
        return self.project.path

    def chatting(self) -> bool:
        return any(not call.finished for call in list(self.sampler.calls.values()))

    def busy(self) -> str | None:
        """What this page is doing that an update must wait for, in a few words, or None."""
        w = self.worker
        if w.job:
            return w.job.get("title") or "a job"
        if w.queue:
            return "a queued job"
        if self.chatting():
            return "a Claude reply"
        return None

    def restart(self) -> None:
        """Start the (updated) tool again for this project on the same port and pass, then stop this one."""
        if self.restarting:
            return
        self.restarting = True
        self.stop_work()
        respawn(self, self.project.path, self.marker)

    def update_state(self) -> dict:
        """What the page shows about versions: updates.status without asking GitHub (the background check in
        cmd_serve does that once a day), read at most every 20 seconds, plus the update under way."""
        at, cached = self._update_seen
        if cached is None or time.time() - at > 20:
            try:
                cached = updates.status(check=False)
                cached["back_blocked"] = back_blockers([str(self.project.path)]) if cached.get("previous") else []
            except Exception as e:  # noqa: BLE001  (a broken cache file must not break the page)
                cached = {"current": updates.version(), "problem": str(e)}
            self._update_seen = (time.time(), cached)
        return {**cached, **self.updater.public()}

    def forget_update_state(self) -> None:
        self._update_seen = (0.0, None)


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
        viewer = self.server.project.viewer
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
        index = self.server.project.viewer / "index.html"
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
            p = srv.project.read("project")
            team_state = srv.team.state()
            return self.json({**srv.worker.state(), "edition": edition.edition(), "user": team_state["me"],
                              "team": team_state, "chatting": srv.chatting(), "update": srv.update_state(),
                              "project": {"name": p.get("name"), "path": str(srv.project.path)}})
        if parts[0] == "team":
            if parts[1:] == ["copy"] and method == "GET":
                from . import team as team_files
                from .teamwork import copy_name
                with tempfile.TemporaryDirectory(prefix="hb-copy-") as tmp:
                    made = team_files.copy_zip(srv.project, Path(tmp) / "copy.zip")
                    data = made.read_bytes()
                return self._send(200, data, "application/zip",
                                  {"Content-Disposition": f'attachment; filename="{copy_name(srv.project)}"'})
            try:
                code, data = srv.team.api(method, parts[1:], (self.body_json() or {}) if method == "POST" else {})
            except HandbookError as e:
                return self.fail(400, str(e))
            return self.json(data, code)
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
        if parts[0] == "bulk":
            return self._bulk(method, parts[1:])
        if parts[0] == "feedback":
            if not edition.has_feedback():                       # the free edition has no feedback round
                return self.fail(404, edition.in_full("The feedback round"), "not_found")
            return self._feedback(method, parts[1:])
        if path == "page-claude" and method == "GET":
            return self.json({"text": page_claude_text()})
        if path == "layout":                                     # the page's check of its own text (layout.py)
            from . import layout
            if method == "POST":
                return self.json(layout.record(srv.project, self.body_json()))
            if method == "GET":
                return self.json({"problems": layout.problems(srv.project)})
        if path == "user" and method == "POST":
            me = srv.team.rename((self.body_json() or {}).get("name"))
            return self.json({"name": me.get("name"), "id": me["id"]})
        if parts[0] == "db":
            return self._db(method, parts[1:], query)
        if parts[0] == "assets":
            return self._assets(method, parts[1:])
        if parts[0] == "sample":
            return self._sample(method, parts[1:], query)
        if parts[0] == "chat":
            return self._chat(method, parts[1:], query)
        if parts[0] == "update":
            return self._update(method, parts[1:])
        if path == "copy" and method == "GET":
            slug = re.sub(r"[^a-z0-9-]+", "-", str(srv.project.read("project").get("slug") or "benchmark").lower())
            name = f"{slug}-copy-{datetime.now().strftime('%Y-%m-%d')}.html"
            return self._send(200, copy_html(srv.project).encode("utf-8"), "text/html; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        return self.fail(404, "No such call.", "not_found")

    def _update(self, method: str, parts: list[str]) -> None:
        """GET update: versions and the update under way. POST update {"action": "check"|"install"|"back"|
        "back_restore"|"cancel"} (back_restore: go back and put projects the previous version cannot read back as
        they were before the upgrade): check asks GitHub now; install and back start the update (it waits for quiet, then swaps and
        restarts the page). GET/POST update/email {"email"} or {"stop": true}: the opt-in update emails.
        POST update/restart: another page updated the tool; restart once idle."""
        srv = self.server
        if parts == [] and method == "GET":
            return self.json(srv.update_state())
        if parts == ["email"]:
            if method == "POST":
                b = self.body_json() or {}
                updates.set_email(None if b.get("stop") else str(b.get("email") or ""))
                srv.forget_update_state()
            return self.json(updates.emails())
        if parts == ["restart"] and method == "POST":
            restart_when_idle(srv)
            return self.json({"ok": True})
        if parts == [] and method == "POST":
            action = str((self.body_json() or {}).get("action") or "")
            if action == "check":
                updates.status(check=True, force=True)
                srv.forget_update_state()
                return self.json(srv.update_state())
            if action in ("install", "back", "back_restore"):
                srv.updater.start("back" if action == "back_restore" else action, restore=action == "back_restore")
                srv.forget_update_state()
                return self.json(srv.update_state())
            if action == "cancel":
                srv.updater.cancel()
                return self.json(srv.update_state())
            return self.fail(400, "Say check, install, back or cancel.")
        return self.fail(404, "No such call.", "not_found")

    def _chat(self, method: str, parts: list[str], query: dict) -> None:
        """The corner chat's own calls (views/chat-tools.js): documents, search, files, instructions,
        sections, removing labels, background jobs and Undo. Reads answer at once; a change is made only after the
        owner clicked Confirm in the chat, and the page is rebuilt before the answer."""
        srv = self.server
        w = srv.worker
        q = lambda k, d=None: (query.get(k) or [d])[0]  # noqa: E731
        ids = lambda v: [x for x in (v if isinstance(v, list) else str(v or "").split(",")) if str(x).strip()] or None  # noqa: E731
        try:
            if parts == ["docs"] and method == "GET":
                if q("doc"):
                    return self.json(chatwork.read_pages(srv.project, q("doc"), int(q("page", 1)), int(q("count", 1))))
                return self.json({"documents": chatwork.row_documents(srv.project, q("row", ""))})
            if parts == ["search"] and method == "GET":
                return self.json({"hits": chatwork.search(srv.project, q("q", ""), ids(q("rows")), int(q("limit", 25)))})
            if parts == ["file"] and method == "POST":
                body = self.body_json() or {}
                path = chatwork.make_file(srv.project, body.get("name"), body.get("columns"), body.get("rows"),
                                          body.get("format") or "xlsx")
                w.note(f"Made {path.name} (in the project's exports folder).", "good")
                return self.json({"file": path.name, "rows": len(body.get("rows") or [])})
            if len(parts) == 2 and parts[0] == "file" and method == "GET":
                path = chatwork.export_path(srv.project, unquote(parts[1]))
                if path is None:
                    return self.fail(404, "That file is not there any more.", "not_found")
                ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                return self._send(200, path.read_bytes(), ctype,
                                  {"Content-Disposition": f'attachment; filename="{path.name}"'})
            if parts == ["instruction"] and method == "POST":
                body = self.body_json() or {}
                args = ["add-instruction", "--text", str(body.get("text") or ""), "--json"]
                if ids(body.get("questions")):
                    args += ["--questions", ",".join(ids(body.get("questions")))]
                if ids(body.get("rows")):
                    args += ["--rows", ",".join(ids(body.get("rows")))]
                with w.cmd.lock:
                    code, out = w.cmd.run(*args)
                if code != 0:
                    return self.fail(400, last_line(out) or "The instruction was not added.")
                w.build()
                got = json.loads(last_line(out))
                w.note(f"Instruction {got['id']} added: every scorer follows it from now on.", "good")
                return self.json(got)
            if parts == ["locate"] and method == "POST":
                body = self.body_json() or {}
                args = ["locate", "--json"] + (["--write"] if body.get("write") else [])
                if ids(body.get("rows")):
                    args += ["--rows", ",".join(ids(body.get("rows")))]
                with w.cmd.lock:
                    code, out = w.cmd.run(*args)
                if code != 0:
                    return self.fail(400, last_line(out) or "The sections could not be found.")
                got = json.loads(last_line(out))
                if body.get("write") and got.get("placed"):
                    w.build()
                    w.note(f"Section numbers added to {got['placed']} quotes.", "good")
                left: dict[str, int] = {}
                for u in got.get("unplaced", []):
                    left[u["row"]] = left.get(u["row"], 0) + 1
                got["left_by_row"] = left
                got["unplaced"] = got.get("unplaced", [])[:80]
                return self.json(got)
            if parts == ["remove-labels"] and method == "POST":
                from . import setup_parts
                with w.cmd.lock:
                    got = setup_parts.remove_labels(srv.project, (self.body_json() or {}).get("labels") or [])
                if got.get("ok"):
                    w.build()
                    w.note(f"Removed the label{'s' if len(got['removed']) != 1 else ''} {', '.join(got['removed'])}. No score changed.", "good")
                return self.json(got)
            if parts == ["change"] and method == "POST":
                body = self.body_json() or {}
                given = body.get("op") or {}            # one change, or a list applied together (changes.py)
                op = json.dumps(given)
                if not body.get("apply"):
                    code, out = w.cmd.run("change", "--op", op, "--json")
                else:
                    said = (", ".join(str(o.get("op")) for o in given if isinstance(o, dict)) if isinstance(given, list)
                            else str(given.get("op") or "a change")) if given else "a change"
                    got = w.changed_by_chat(str(body.get("title") or said), lambda: w.cmd.run("change", "--op", op, "--apply", "--json"))
                    code, out = got["code"], got["out"]
                try:
                    return self.json(json.loads(last_line(out)))
                except (json.JSONDecodeError, ValueError):
                    return self.fail(400, last_line(out) or "The change could not be made.")
            if parts == ["hb"] and method == "POST":
                body = self.body_json() or {}
                return self.json(w.chat_hb(body.get("args") or []))
            if parts == ["hb-help"] and method == "GET":
                cmd = q("command")
                code, out = w.cmd.run(*([cmd, "--help"] if cmd else ["help"]), project=False, timeout=60)
                return self.json({"text": out[-8000:]})
            if parts == ["job"] and method == "POST":
                return self.json(w.queue_chat_job(self.body_json() or {}))
            if parts == ["jobs"] and method == "GET":
                return self.json({"jobs": [{**{k: j.get(k) for k in ("id", "title", "status", "asked", "started", "finished",
                                                                     "tokens", "result", "undone", "estimate")},
                                            # Undo takes back only this one's files (1.0.1 on); else the whole copy
                                            "own_undo": isinstance(j.get("changed"), dict)}
                                           for j in w.chat_jobs()]})
            if parts == ["undo"] and method == "POST":
                got = w.undo_chat_job(str((self.body_json() or {}).get("job") or ""))
                return self.json({"undone": got["id"], "title": got["title"]})
        except HandbookError as e:
            return self.fail(400, str(e))
        except (ValueError, OSError) as e:
            return self.fail(400, str(e), "unreadable")
        return self.fail(404, "No such chat call.", "not_found")

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
                low, high = plainreasons.PER_UNIT
                queued = worker.enqueue("plain-reasons:" + now_iso(), "plain", "Writing the reasons in plain words",
                                        lambda job: worker.plain_reasons(job, ids),
                                        estimate=lambda: {"low": low * len(ids), "high": high * len(ids)})
                return {"queued": queued, "units": ids, "tokens": {"low": low * len(ids), "high": high * len(ids)}}
            return do(start)
        return self.fail(404, "No such call.", "not_found")

    def _bulk(self, method: str, parts: list[str]) -> None:
        """POST bulk/read {files: [{asset, name}]}: make the plan for what the owner dropped (bulk.read; one
        small Claude call); the uploads are taken into the plan's folder. GET bulk/<plan id>: the plan, with
        what Confirm did once it is done."""
        from . import bulk
        srv = self.server
        if parts == ["read"] and method == "POST":
            body = self.body_json() or {}
            files = body.get("files") if isinstance(body.get("files"), list) else []
            if not files:
                return self.fail(400, "Drop a list, some documents or a zip first.", "bad_request")
            taken, assets = [], []
            for item in files:
                asset = item.get("asset") if isinstance(item, dict) else None
                src = srv.worker.asset_path(asset)
                if not src:
                    return self.fail(400, "One of the files you dropped could not be found among the uploads. "
                                          "Drop it again.", "not_found")
                assets.append(src)
                taken.append((src, str(item.get("name") or src.name.split("--", 1)[-1])))
            try:
                plan = bulk.read(srv.project, taken)
            except HandbookError as e:
                return self.fail(400, str(e), "unreadable")
            finally:
                for src in assets:
                    src.unlink(missing_ok=True)
            if plan.get("tokens"):
                srv.worker.log_usage({"kind": "bulk", "processed": plan["tokens"], "plan": plan["id"]})
            return self.json(plan)
        if len(parts) == 1 and method == "GET":
            try:
                return self.json(bulk.load_plan(srv.project, parts[0]))
            except HandbookError as e:
                return self.fail(404, str(e), "not_found")
        return self.fail(404, "Not found.", "not_found")

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
            body = self.body_json()
            if col in WATCHED and isinstance(body, dict):
                body = {**body, "on": self.server.team.machine}     # which computer saved it (teamwork.py)
            if method == "PUT":
                doc = store.set(col, doc_id, body)
            elif method == "PATCH":
                doc = store.update(col, doc_id, body)
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
                if not ext and ctype in UPLOAD_GENERIC and (Path(raw).suffix.lower() in readers.UPLOAD_MIME
                                                            or Path(raw).suffix.lower() == ".zip"):
                    ext = Path(raw).suffix.lower()          # a HEIC, Markdown or zip file a browser sent with no type
                if not ext:
                    return self.fail(400, "Send a PDF, Word, Excel, text, picture, email or zip file.", "unsupported_type")
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
                from .material import name_plain_files
                name_plain_files(folder)         # a file put in the folder by hand shows as an upload too
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

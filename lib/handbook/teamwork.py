"""The team side of `hb serve` (see team.py for the files and the rules).

    side = TeamSide(worker, config)

serve.py calls into it at a few points:
- `screen(collection, docs)`: of what the page saved, only what this tool should apply. In a team
  project that is this person's own saves; a save made before a colleague's newer change to the same
  cell or rule synced in is held as a clash; a request to pause or stop someone else's run is refused.
- `before(...)`/`logged(...)`: this person's applied actions go in their log.
- `tick()`: every few seconds from the apply loop. Takes the day's backup; in a team project, merges the
  sync service's clash copies, notes colleagues' actions as they sync in, rebuilds the page when their
  changes arrive, and tells colleagues what this tool is running.
- `api(...)`: the page's `/api/team/...` calls, and `state()` for `/api/state`.
Nothing here changes a project that is not shared: there `screen` passes everything through and only
the backups and the check marks (`check`) apply.
"""
from __future__ import annotations
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

from . import team
from .folder import HandbookError, now_stamp, read_json

TICK_S = float(os.environ.get("HB_TEAM_TICK_S") or 15.0)   # how often a team project looks for colleagues' changes
ONLINE_EVERY_S = 600.0        # how often it counts online-only files
ME_EVERY_S = 60.0             # how often it re-reads the person's Claude account name
RUN_ACTIONS = ("pause", "resume", "stop_now", "stop_after_row", "scope", "stop_after")


class TeamSide:
    def __init__(self, worker, config):
        self.w = worker
        self.project = worker.project
        self.config = config
        self.me = team.me(config)
        self.me_at = time.monotonic()
        self.machine = team.machine_key(config)   # this computer: one person may have the project open on two
        self.print: str | None = None
        self.follow = team.LogFollower(self.project, self.me["id"])
        self.tick_at = 0.0
        self.online = 0
        self.online_at = 0.0
        self.backup_day = None
        self.recheck = False              # a clash was resolved: look at held saves again
        self.handled: set[str] = set()     # resolved clashes on this person's held saves, already acted on
        self.can_relaunch = False         # set by cmd_serve: only a real `hb serve` may restart itself
        self.app_window = False
        if self.on:
            team.join(self.project, self.me)
            self.follow.new()             # start from now: what happened before is in the log

    @property
    def on(self) -> bool:
        return self.project.is_team()

    def person(self) -> dict:
        if time.monotonic() - self.me_at > ME_EVERY_S:
            self.me, self.me_at = team.me(self.config), time.monotonic()
        return self.me

    def rename(self, name: str) -> dict:
        self.config.set_user_name(name)
        self.me, self.me_at = team.me(self.config), time.monotonic()
        if self.on:
            team.join(self.project, self.me)
        return self.me

    def first_member(self) -> bool:
        members = team.read_team(self.project)["members"]
        first = min(members.items(), key=lambda kv: str(kv[1].get("joined") or ""), default=(None, None))[0]
        return first == self.me["id"]

    # -- which saves this tool applies --------------------------------------------------------------------

    def mine(self, docs: list[dict]) -> list[dict]:
        """The saves made through this computer's page (`on`, stamped by this server), so neither a
        colleague's nor the same person's other computer applies them twice; and, from before the project
        was shared, the ones its first member made."""
        if not self.on:
            return docs
        return [d for d in docs if self.owns(d.get("by"), d.get("on"), d.get("at"))]

    def owns(self, by, on=None, at=None) -> bool:
        """Whether a save, re-run or correction round is this tool's to act on: made on this computer; or,
        with no computer recorded (made before sharing, or by an older tool), this person's, or nobody on
        the team's and this person shared the project."""
        if not self.on:
            return True
        if on:
            return on == self.machine
        members = team.read_team(self.project)
        if by and by in members["members"]:
            return by == self.me["id"]
        return self.first_member()

    def screen(self, collection: str, docs: list[dict]) -> list[dict]:
        if not self.on:
            return docs
        docs = self.mine(docs)
        if collection == "control":
            return self._screen_control(docs)
        if collection not in ("corrections", "decisions"):
            return docs
        held = {c.get("doc"): c for c in team.clashes(self.project) if c.get("doc")}
        done = self._applied(collection)
        # The newest save of this person's on each answer or rule: an older one still held is replaced by it.
        newest: dict = {}
        for d in docs:
            key = (d.get("row"), d.get("qid"), d.get("rule"))
            if d.get("kind") != "check" and str(d.get("at") or "") >= str((newest.get(key) or {}).get("at") or ""):
                newest[key] = d
        out = []
        for d in docs:
            clash = held.get(d.get("id"))
            if clash and clash.get("open") and newest.get((d.get("row"), d.get("qid"), d.get("rule"))) is not d:
                team.close_clash(self.project, clash["id"], self.person(), "replaced by a newer change")
                self.w.store.delete(collection, d["id"])
                continue
            if clash:
                if clash.get("open"):
                    continue                                   # waiting for someone to choose
                chose = (clash.get("resolved") or {}).get("chose")
                if chose == "b":
                    out.append(d)                              # this save was kept: apply it
                elif d["id"] not in done:
                    self.w.store.delete(collection, d["id"])   # the colleague's version was kept
                continue
            if d.get("id") in done:
                out.append(d)
                continue
            clash = self._stale(collection, d)
            if clash:
                self.w.note(f"Your change to {clash['title']} was not saved yet: {clash['sides'][0]['name'] or 'a colleague'} "
                            "changed it after you opened it. Choose which to keep under Clashes.", "warning")
                continue
            out.append(d)
        return out

    def _applied(self, collection: str) -> set:
        if collection == "corrections":
            rounds = (read_json(self.project.file("corrections"), {}) or {}).get("corrections") or []
            return {r.get("id") for r in rounds if isinstance(r, dict)}
        return set((read_json(self.project.file("edits-applied"), {}) or {}).get("applied") or [])

    def _stale(self, collection: str, d: dict) -> dict | None:
        """The clash this save makes, when the page that made it had not seen a colleague's newer change."""
        my = self.me["id"]
        seen = d.get("seen")
        if not seen:
            return None
        who = team.names(self.project)
        if collection == "corrections":
            if d.get("kind") == "check" or d.get("keep") or not d.get("row") or not d.get("qid"):
                return None
            try:
                cell = (self.project.answers(d["row"]).get("cells") or {}).get(d["qid"])
            except ValueError:
                return None
            owner = cell.get("owner") if isinstance(cell, dict) and isinstance(cell.get("owner"), dict) else {}
            if team.cell_rev(cell) == seen or not owner.get("by") or owner.get("by") == my:
                return None
            q = self._question(d["qid"])
            mine_words = team.value_words(q, d.get("value")) if d.get("value") is not None else "new evidence"
            return team.add_clash(self.project, {
                "kind": "save", "source": "save", "row": d["row"], "qid": d["qid"], "doc": d["id"],
                "title": f"{self._row_name(d['row'])}, {(q or {}).get('short') or d['qid']}",
                "sides": [team._side(owner.get("by"), who.get(owner.get("by")), owner.get("at"),
                                     team.value_words(q, cell.get("value")), cell, "a"),
                          team._side(my, self.me.get("name"), d.get("at"), mine_words,
                                     {"doc": d["id"], "value": d.get("value"), "reason": d.get("reason")}, "b")]},
                self.me)
        if collection == "decisions" and d.get("rule"):
            rule = next((r for r in self.project.read("rules").get("rules") or []
                         if isinstance(r, dict) and r.get("id") == d["rule"]), None)
            dec = (rule or {}).get("decision") if isinstance((rule or {}).get("decision"), dict) else {}
            if team.rule_rev(rule) == seen or not dec.get("by") or dec.get("by") == my:
                return None
            words = d.get("text") or (f"option {d.get('option')}" if d.get("option") else "skip it")
            return team.add_clash(self.project, {
                "kind": "decision", "source": "save", "rule": d["rule"], "doc": d["id"],
                "title": f"rule {d['rule']}",
                "sides": [team._side(dec.get("by"), who.get(dec.get("by")), dec.get("at"),
                                     dec.get("text") or f"option {dec.get('option')}", rule, "a"),
                          team._side(my, self.me.get("name"), d.get("at"), words, {"doc": d["id"]}, "b")]},
                self.me)
        return None

    def _screen_control(self, docs: list[dict]) -> list[dict]:
        applied = set((read_json(self.project.file("control"), {}) or {}).get("applied") or [])
        run = next((r for r in reversed(self.project.runs()) if isinstance(r, dict) and not r.get("finished")), None)
        owner = (run or {}).get("by")
        out = []
        for d in docs:
            if d.get("id") not in applied and owner and owner != self.me["id"] and d.get("action") in RUN_ACTIONS:
                name = run.get("by_name") or team.name_of(self.project, owner) or "a colleague"
                self.w.note(f"Only {name} can pause or change their run. Ask them, or wait until it finishes.", "warning")
                self.w.store.delete("control", d["id"])
                continue
            out.append(d)
        return out

    def _question(self, qid):
        return next((q for q in self.project.read("questions").get("questions") or [] if q.get("id") == qid), None)

    def _row_name(self, row):
        unit = next((u for u in self.project.read("units").get("units") or [] if u.get("id") == row), None)
        return (unit or {}).get("name") or row

    # -- the log of this person's actions -----------------------------------------------------------------------

    def before(self, collection: str, docs: list[dict]) -> dict:
        """What this person's not-yet-applied saves are about to change, to log what they did after."""
        if not self.on:
            return {}
        done = self._applied("corrections" if collection == "corrections" else "decisions")
        out = {}
        for d in docs:
            if d.get("id") in done or d.get("kind") == "check":
                continue
            entry = {"doc": d}
            if collection == "corrections" and d.get("row") and d.get("qid"):
                try:
                    cell = (self.project.answers(d["row"]).get("cells") or {}).get(d["qid"]) or {}
                except ValueError:
                    cell = {}
                entry["before"] = cell.get("value")
            out[d["id"]] = entry
        return out

    def logged(self, collection: str, pending: dict) -> None:
        if not self.on or not pending:
            return
        done = self._applied("corrections" if collection == "corrections" else "decisions")
        me, name = self.person(), self.me.get("name") or "Someone"
        for did, entry in pending.items():
            if did not in done:
                continue
            d = entry["doc"]
            if collection == "corrections":
                q = self._question(d.get("qid"))
                what = f"{self._row_name(d.get('row'))}, {(q or {}).get('short') or d.get('qid')}"
                after = d.get("value")
                text = (f"{name} corrected {what} from {team.value_words(q, entry.get('before'))} to "
                        f"{team.value_words(q, after)}" if after is not None else f"{name} added evidence to {what}")
                team.log(self.project, me, "corrected", row=d.get("row"), qid=d.get("qid"), before=entry.get("before"),
                         after=after, reason=d.get("reason"), doc=did, text=text)
            elif collection == "decisions":
                kind = "rule_skipped" if d.get("skip") else "rule_decided"
                text = (f"{name} left rule {d.get('rule')} to Claude's reading" if d.get("skip")
                        else f"{name} decided rule {d.get('rule')}: {d.get('text') or 'option ' + str(d.get('option'))}")
                team.log(self.project, me, kind, rule=d.get("rule"), option=d.get("option"), after=d.get("text"),
                         doc=did, text=text)
            else:
                team.log(self.project, me, "edited", target=d.get("target"), target_id=d.get("target_id"),
                         field=d.get("field"), after=d.get("value"), doc=did,
                         text=f"{name} changed the {d.get('field') or 'set-up'} of {d.get('target')} "
                              f"{d.get('target_id') or ''}".strip())

    def note_action(self, kind: str, text: str, **fields) -> None:
        team.log(self.project, self.person(), kind, text=text, **fields)

    # -- check marks ("verified by") ---------------------------------------------------------------------------

    def check(self, row: str, qid: str, checked: bool, seen: str | None) -> dict:
        """Mark an answer checked by this person, or take the mark off. Refused when the answer changed
        since the page showed it (the person has not seen what they would be ticking)."""
        me = self.person()
        with self.w.cmd.lock:
            answers = self.project.answers(row)
            cells = answers.setdefault("cells", {})
            cell = cells.get(qid)
            if not isinstance(cell, dict) or cell.get("state") in (None, "not_run", "not_applicable"):
                raise HandbookError("There is no answer here to check yet.")
            if seen and team.cell_rev(cell) != seen:
                raise HandbookError("This answer changed while you had it open. Have another look, then tick it again.")
            if checked:
                cell["checked"] = {"at": now_stamp(), "how": "owner", "by": me["id"], "name": me.get("name")}
            else:
                cell.pop("checked", None)
            if checked:
                cell.pop("rescored", None)        # ticking it means the person looked and kept this answer
            answers["row"] = row
            self.project.write_answers(row, answers)
        q = self._question(qid)
        what = f"{self._row_name(row)}, {(q or {}).get('short') or qid}"
        team.log(self.project, me, "checked" if checked else "unchecked", row=row, qid=qid, value=cell.get("value"),
                 text=f"{me.get('name') or 'Someone'} {'checked' if checked else 'took the check mark off'} {what}")
        self.w.build()
        return {"checked": checked, "row": row, "qid": qid}

    # -- every few seconds ------------------------------------------------------------------------------------

    def tick(self) -> None:
        self._daily_backup()
        if not self.on:
            return
        now = time.monotonic()
        if now - self.tick_at < TICK_S:
            return
        self.tick_at = now
        me = self.person()
        team.join(self.project, me)
        with self.w.cmd.lock:
            got = team.merge_conflicts(self.project, me)
        if got["clashes"]:
            n = len(got["clashes"])
            self.w.note(f"{n} clash{'es' if n > 1 else ''} to sort out: two people changed the same thing before "
                        "the sync service caught up. See Clashes at the top.", "warning")
        for e in self.follow.new()[-8:]:
            if e.get("text"):
                self.w.note(e["text"], "neutral")
        for c in team.clashes(self.project):
            # A save of this person's that was held as a clash, now resolved by anyone: act on it.
            if c.get("doc") and not c.get("open") and c["id"] not in self.handled \
                    and any(x.get("by") == me["id"] and x.get("key") == "b" for x in c.get("sides") or []):
                self.handled.add(c["id"])
                self.recheck = True
        fp = team.fingerprint(self.project)
        if self.print is None:
            self.print = fp
        elif fp != self.print:
            self.print = fp
            self.w.build()                   # a colleague's change arrived: show it
        if now - self.online_at > ONLINE_EVERY_S:
            self.online_at = now
            self.online = team.online_only(self.project)
        team.write_job(self.project, me, self.w.public_job(self.w.job))

    def built(self) -> None:
        """After this tool rebuilt the page: its own writes are not a colleague's change."""
        if self.on:
            self.print = team.fingerprint(self.project)

    def _daily_backup(self) -> None:
        today = date.today()
        if self.backup_day == today:
            return
        self.backup_day = today

        def run():
            try:
                team.take_backup(self.project)
            except Exception:  # noqa: BLE001 - a backup that fails must never stop the page
                pass
        threading.Thread(target=run, daemon=True, name="hb-backup").start()

    # -- what the page polls ------------------------------------------------------------------------------------

    def state(self) -> dict:
        me = self.person()
        out = {"on": self.on, "me": {"id": me["id"], "name": me.get("name")}, "machine": self.machine}
        if not self.on:
            return out
        open_ = team.clashes(self.project, only_open=True)
        out.update({"members": team.names(self.project), "clashes_open": len(open_),
                    "clash_ids": [c["id"] for c in open_],
                    "others_jobs": team.others_jobs(self.project, me["id"]), "online_only": self.online,
                    "service": team.sync_service(self.project.path)})
        return out

    # -- /api/team/... --------------------------------------------------------------------------------------------

    def api(self, method: str, parts: list[str], body: dict) -> tuple[int, dict]:
        p = self.project
        if method == "GET" and not parts:
            out = self.state()
            if self.on:
                every = team.clashes(p)
                out["clashes"] = [c for c in every if c.get("open")] + \
                    sorted((c for c in every if not c.get("open")), key=lambda c: str((c.get("resolved") or {}).get("at")),
                           reverse=True)[:20]
                out["log"] = team.read_log(p, limit=80)
            return 200, out
        if method == "GET" and parts == ["places"]:
            return 200, {"places": team.places(), "service": team.sync_service(p.path), "advice": team.ADVICE}
        if method == "GET" and parts == ["backups"]:
            return 200, {"backups": team.list_backups(p)}
        if method != "POST":
            return 404, {"error": "No such call."}
        if parts == ["check"]:
            row, qid = str(body.get("row") or ""), str(body.get("qid") or "")
            if not re.fullmatch(r"[a-z0-9-]+", row) or not qid:
                raise HandbookError("Say which answer to check.")
            return 200, self.check(row, qid, bool(body.get("checked", True)), body.get("seen"))
        if parts == ["resolve"]:
            return 200, self.resolve(body)
        if parts == ["restore"]:
            with self.w.cmd.lock:
                got = team.restore(p, str(body.get("name") or ""), self.person())
            self.w.build()
            self.w.note(f"The project is back as it was in the snapshot of {got['restored'][:10]}. "
                        "How it was a moment ago is kept as a snapshot too.", "good")
            return 200, got
        if parts == ["share"]:
            return 200, self.share(str(body.get("into") or ""))
        if parts == ["name"]:
            me = self.rename(str(body.get("name") or ""))
            return 200, {"name": me.get("name"), "id": me["id"]}
        return 404, {"error": "No such call."}

    def resolve(self, body: dict) -> dict:
        """{clash, chose} for one clash, or {clashes: [ids], keep: person id} to keep one person's side in
        each (the corner chat's "keep Toby's, not Raphael's"). Only clashes still open are touched."""
        me = self.person()
        picks = []
        if body.get("clash"):
            picks.append((str(body["clash"]), str(body.get("chose") or "")))
        elif isinstance(body.get("clashes"), list) and body.get("keep"):
            open_ = {c["id"]: c for c in team.clashes(self.project, only_open=True)}
            for cid in body["clashes"]:
                c = open_.get(cid)
                side = next((s for s in (c or {}).get("sides") or [] if s.get("by") == body["keep"]), None)
                if side:
                    picks.append((cid, side["key"]))
        if not picks:
            raise HandbookError("There was nothing to choose: those clashes are already sorted out, or that "
                                "person has no side in them.")
        done = []
        with self.w.cmd.lock:
            for cid, chose in picks:
                done.append(team.resolve_clash(self.project, cid, chose, me)["id"])
        self.w.build()
        self.recheck = True                   # a held save of this person's may now go through
        self.w.poke()
        return {"resolved": done}

    def share(self, into: str) -> dict:
        if not into:
            raise HandbookError("Pick the shared folder to put the project in.")
        with self.w.cmd.lock:
            new = team.share(self.project, into, self.person())
        out = {"path": str(new.path), "service": team.sync_service(new.path), "launcher": None, "restarting": False}
        if sys.platform == "darwin":
            code, said = self.w.cmd.run("launcher", "--project", str(new.path), project=False)
            out["launcher"] = said.splitlines()[0] if code == 0 and said else None
        if self.can_relaunch:
            self.relaunch(new.path)
            out["restarting"] = True
        return out

    def relaunch(self, path: Path) -> None:
        """Open the page again from the project's new place, then stop this server."""
        from .serve import HB
        args = [sys.executable, str(HB), "serve", "--project", str(path)] + (["--app-window"] if self.app_window else [])
        log = Path(tempfile.gettempdir()) / "hb-relaunch.log"
        with open(log, "a", encoding="utf-8") as out:
            subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        threading.Timer(2.0, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()


def copy_name(project) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", str(project.read("project").get("slug") or project.slug).lower())
    return f"{slug}-copy-{datetime.now():%Y-%m-%d}.zip"

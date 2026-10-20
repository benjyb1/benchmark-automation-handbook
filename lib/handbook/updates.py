"""Updates: which version of the tool this is, whether a newer one has been released, and installing it.

The version is the one line in the tool's VERSION file (`handbook/VERSION` in the source, `VERSION` in an
install). It is the only place the number lives: the page footer, `hb doctor`, the release script and the
plugin manifest all read it.

Checking. `latest()` asks GitHub for the latest release of this edition's repository: one request, at most
once a day (the answer is kept in ~/.handbook/updates.json). The free edition's request carries nothing about
the person or their projects. The Full edition's carries the personal download key (edition.key_file()), as
the download itself does; GitHub's answer then also says when that key runs out, so the page can remind the
owner a month ahead. A release's "what's new" lines are the lines of its notes that start with "- ".

Installing (`prepare`, `swap_in`). The new copy is downloaded and unpacked beside the current one, at
`<tool>.new`, checked (same edition, the version it says, a launcher that runs) and warmed up (uv fetches any
new libraries there and then, so the restart is quick). Only then is it swapped in, in one step: on a Mac the
two folders trade places atomically (renamex_np with RENAME_SWAP), so `<tool>` is always a whole copy, old or
new, never half of each. The copy that was replaced is kept as `<tool>.previous`; `go_back()` swaps it back in
the same way. Projects are never touched here: a newer tool upgrades a project's files when it opens it
(upgrade.py).

A development checkout (the tool inside a git repository) is never updated this way.
"""
from __future__ import annotations
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import edition
from .folder import HandbookError, read_json, write_json
from .localstore import home

REPOS = {"free": "benjyb1/benchmark-automation-handbook", "full": "benjyb1/benchmark-automation-handbook-full"}
API = "https://api.github.com"
CHECK_EVERY_S = 24 * 3600
KEY_REMIND_DAYS = 30
TIMEOUT_S = 20
USER_AGENT = "benchmark-tool-updates"
WARM_TIMEOUT_S = 900
# What the page and the README say about the check, in one place.
PRIVACY = ("Once a day, when the page starts, the tool asks GitHub (where it is published) whether a newer version "
           "is out. That one request sends nothing about you or your projects.")
PRIVACY_FULL = (" In the Full version it sends your download key, as a download does, so GitHub knows the "
                "request is yours.")


# ---- this copy ------------------------------------------------------------------------------------

def tool_dir() -> Path:
    """The installed tool's top folder (the one holding EDITION, VERSION and lib/). HB_TOOL_DIR overrides it,
    for tests."""
    forced = os.environ.get("HB_TOOL_DIR")
    return Path(forced).expanduser().resolve() if forced else Path(__file__).resolve().parents[2]


def read_version(folder: Path) -> str | None:
    try:
        text = (Path(folder) / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if re.fullmatch(r"\d+(\.\d+){0,3}", text) else None


def version() -> str:
    """This copy's version, such as "1.2.0"; "0" for a copy made before versions were numbered."""
    return read_version(tool_dir()) or "0"


def as_tuple(v: str | None) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", str(v or "0"))[:4]) or (0,)


def newer(a: str | None, b: str | None) -> bool:
    """Whether version `a` is newer than `b`."""
    return as_tuple(a) > as_tuple(b)


def is_development() -> bool:
    """Whether this is a development checkout (the tool inside a git repository): never updated from GitHub."""
    here = tool_dir()
    return any((p / ".git").exists() for p in (here, *here.parents))


def previous_dir() -> Path:
    return tool_dir().with_name(tool_dir().name + ".previous")


def new_dir() -> Path:
    return tool_dir().with_name(tool_dir().name + ".new")


def previous_version() -> str | None:
    """The version kept for "Go back to the previous version", or None when there is none."""
    p = previous_dir()
    return (read_version(p) or "0") if (p / "lib" / "hb").is_file() else None


# ---- the check -------------------------------------------------------------------------------------

def state_file() -> Path:
    return home() / "updates.json"


def _state() -> dict:
    try:
        s = read_json(state_file(), {})
    except HandbookError:
        s = {}
    return s if isinstance(s, dict) else {}


def _save(s: dict) -> None:
    write_json(state_file(), s)
    try:
        os.chmod(state_file(), 0o600)
    except OSError:
        pass


def _api() -> str:
    return (os.environ.get("HB_UPDATE_API") or API).rstrip("/")


def _key() -> str | None:
    if edition.is_free():
        return None
    try:
        key = edition.key_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D401  (we follow redirects ourselves)
        return None


_OPEN = urllib.request.build_opener(_NoRedirect)


def _get(url: str, key: str | None, accept: str = "application/vnd.github+json", hops: int = 5):
    """GET `url` (the key only ever goes to the first address, never to where it redirects); returns the
    response. Raises urllib.error.HTTPError for an error status, OSError when offline."""
    headers = {"Accept": accept, "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    for _ in range(hops):
        try:
            return _OPEN.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.request.urljoin(url, e.headers["Location"])
                headers.pop("Authorization", None)
                continue
            raise
    raise OSError("Too many redirects.")


def whats_new(body: str | None) -> list[str]:
    """The release notes' "what's new" lines: those starting with "- " (or "* ")."""
    out = []
    for line in str(body or "").splitlines():
        m = re.match(r"\s*[-*]\s+(.*\S)", line)
        if m:
            out.append(m.group(1))
    return out[:12]


def _expiry(header: str | None) -> str | None:
    """GitHub's `github-authentication-token-expiration` header ("2027-10-19 00:00:00 UTC") as a date."""
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", header or "")
    return m.group(1) if m else None


def check_now() -> dict:
    """Ask GitHub now. Returns what was learnt: {"checked", "version", "notes", "zip", "tag", "published",
    "key_expires", "problem"}; "problem" is "no_key", "key_refused" or "offline" when the check did not work."""
    ed = edition.edition()
    found = {"checked": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "edition": ed}
    key = _key()
    if ed == "full" and not key:
        return {**found, "problem": "no_key"}
    try:
        with _get(f"{_api()}/repos/{REPOS[ed]}/releases/latest", key) as res:
            release = json.loads(res.read().decode("utf-8"))
            found["key_expires"] = _expiry(res.headers.get("github-authentication-token-expiration"))
    except urllib.error.HTTPError as e:
        if ed == "full" and e.code in (401, 403, 404):
            return {**found, "problem": "key_refused"}
        return {**found, "problem": "no_release" if e.code == 404 else "offline"}
    except (OSError, ValueError):
        return {**found, "problem": "offline"}
    tag = str(release.get("tag_name") or "")
    found.update({"version": tag.lstrip("v") or None, "tag": tag, "notes": whats_new(release.get("body")),
                  "zip": release.get("zipball_url"), "published": release.get("published_at")})
    return found


def latest(force: bool = False) -> dict:
    """The last check's answer, asking GitHub again when it is more than a day old (or `force`)."""
    s = _state()
    last = s.get("last") if isinstance(s.get("last"), dict) else None
    fresh = False
    if last and last.get("edition") == edition.edition():
        try:
            age = time.time() - datetime.strptime(last["checked"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            fresh = 0 <= age < CHECK_EVERY_S
        except (KeyError, ValueError):
            fresh = False
    if fresh and not force:
        return last
    found = check_now()
    if found.get("problem") == "offline" and last and last.get("version"):
        found = {**last, "checked": found["checked"], "problem": "offline"}    # keep what we knew
    s["last"] = found
    _save(s)
    return found


def key_days_left(found: dict | None) -> int | None:
    exp = (found or {}).get("key_expires")
    if not exp:
        return None
    try:
        end = datetime.strptime(exp, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (end - datetime.now(timezone.utc)).days


def status(force: bool = False, check: bool = True) -> dict:
    """Everything the page, the chat and `hb update` show: {"current", "latest", "notes", "available",
    "previous", "development", "key_days", "key_note", "problem", "checked", "privacy"}. With `check` false it
    only reads what the last check found (no request)."""
    current = version()
    dev = is_development()
    found = latest(force) if check and not dev else (_state().get("last") or {})
    if found.get("edition") not in (None, edition.edition()):
        found = {}
    s = _state()
    went_back = s.get("went_back_from")
    available = bool(found.get("version")) and newer(found.get("version"), current) and not dev
    days = key_days_left(found) if edition.is_full() else None
    key_note = None
    if edition.is_full():
        if found.get("problem") == "no_key":
            key_note = ("No download key is saved on this computer, so updates of the Full version cannot be checked. "
                        "Ask whoever gave you the Full version for your key.")
        elif found.get("problem") == "key_refused":
            key_note = ("Your download key has run out or no longer works, so updates cannot be fetched. "
                        "Ask whoever gave you the Full version for a new one.")
        elif days is not None and days <= KEY_REMIND_DAYS:
            key_note = (f"Your download key runs out in {max(days, 0)} day{'s' if days != 1 else ''}. "
                        "Ask whoever gave you the Full version for a new one, so updates keep working.")
    return {"current": current, "latest": found.get("version"), "notes": found.get("notes") or [],
            "available": available, "quiet": available and went_back == found.get("version"),
            "previous": previous_version(), "development": dev, "edition": edition.edition(),
            "key_days": days, "key_note": key_note, "problem": found.get("problem"), "checked": found.get("checked"),
            "privacy": PRIVACY + (PRIVACY_FULL if edition.is_full() else ""),
            "email": emails()["email"], "email_privacy": EMAIL_PRIVACY}


# ---- installing ------------------------------------------------------------------------------------

def _trash(path: Path) -> None:
    """Move a folder this module made out of the way: to the Bin on a Mac (never deleted outright), else removed."""
    if not path.exists() and not path.is_symlink():
        return
    bin_ = Path.home() / ".Trash"
    if sys.platform == "darwin" and bin_.is_dir():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            shutil.move(str(path), str(bin_ / f"{path.name}-{stamp}"))
            return
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> Path:
    """Unpack `zf` into `dest` (no path may leave it; the execute bit is kept) and return its one top folder."""
    dest = dest.resolve()
    tops = set()
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or ".." in Path(name).parts:
            raise HandbookError(f"The download has an unsafe file name ({name}), so it was not used.")
        target = (dest / name).resolve()
        if dest not in target.parents and target != dest:
            raise HandbookError(f"The download has an unsafe file name ({name}), so it was not used.")
        tops.add(Path(name).parts[0])
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:      # a link inside the zip: never followed
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        mode = (info.external_attr >> 16) & 0o777
        os.chmod(target, (mode or 0o644) | 0o600)
    if len(tops) != 1:
        raise HandbookError("The download is not one folder, so it was not used.")
    return dest / tops.pop()


def prepare(found: dict, say=print, warm: bool = True) -> Path:
    """Download the release `found` describes (from status()/latest()) beside the current copy, at `<tool>.new`,
    check it and warm it up. Returns the folder. Nothing in use is touched."""
    if is_development():
        raise HandbookError("This is a development copy of the tool (inside a git repository), so it is not updated "
                            "from GitHub.")
    if not found.get("zip") or not found.get("version"):
        raise HandbookError("There is no newer version to install.")
    target = new_dir()
    _trash(target)
    say(f"Downloading version {found['version']}...")
    work = Path(tempfile.mkdtemp(prefix=".benchmark-tool-download-", dir=str(tool_dir().parent)))
    try:
        zpath = work / "release.zip"
        try:
            with _get(found["zip"], _key(), accept="application/vnd.github+json") as res, open(zpath, "wb") as out:
                shutil.copyfileobj(res, out)
        except urllib.error.HTTPError as e:
            if edition.is_full() and e.code in (401, 403, 404):
                raise HandbookError("Your download key has run out or no longer works, so the update could not be "
                                    "downloaded. Ask whoever gave you the Full version for a new one.") from None
            raise HandbookError(f"The download did not work (GitHub answered {e.code}). Try again later.") from None
        except OSError:
            raise HandbookError("The download did not work: is this computer online? Try again in a minute.") from None
        try:
            with zipfile.ZipFile(zpath) as zf:
                top = _safe_extract(zf, work / "unpacked")
        except zipfile.BadZipFile:
            raise HandbookError("The download was not a complete zip file. Try again.") from None
        problems = check_copy(top, found["version"])
        if problems:
            raise HandbookError("The downloaded copy did not pass its checks, so it was not used: " + "; ".join(problems))
        os.replace(top, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if warm:
        say("Getting it ready (the first start of a new version can take a minute)...")
        done = subprocess.run([str(target / "lib" / "hb"), "help"], capture_output=True, text=True,
                              timeout=WARM_TIMEOUT_S, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            _trash(target)
            tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or ["no output"]
            raise HandbookError(f"The new version did not start ({tail[0]}), so it was not used. Nothing has changed.")
    return target


def check_copy(folder: Path, want_version: str | None) -> list[str]:
    """What is wrong with an unpacked copy of the tool, in plain words (empty when it is fine)."""
    folder = Path(folder)
    problems = []
    hb = folder / "lib" / "hb"
    if not hb.is_file():
        problems.append("it has no launcher (lib/hb)")
    else:
        hb.chmod(hb.stat().st_mode | 0o111)
    if not (folder / "lib" / "handbook" / "cli.py").is_file():
        problems.append("it has no lib/handbook")
    try:
        ed = (folder / "EDITION").read_text(encoding="utf-8").strip().lower()
    except OSError:
        ed = None
    if ed != edition.edition():
        problems.append(f"it is the {ed or 'unknown'} edition, not the {edition.edition()} one")
    got = read_version(folder)
    if want_version and got != want_version:
        problems.append(f"it says version {got or 'none'}, not {want_version}")
    return problems


RENAME_SWAP = 0x2          # macOS <stdio.h>
RENAME_EXCHANGE = 0x2      # Linux <fcntl.h>


def swap_folders(a: Path, b: Path) -> None:
    """Make folders `a` and `b` trade places, in one step where the system can (macOS renamex_np, Linux
    renameat2), else with three renames."""
    a, b = str(a), str(b)
    libc = None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        libc = None
    if libc is not None and sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        if libc.renamex_np(a.encode(), b.encode(), ctypes.c_uint(RENAME_SWAP)) == 0:
            return
    elif libc is not None and sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        if libc.renameat2(-100, a.encode(), -100, b.encode(), ctypes.c_uint(RENAME_EXCHANGE)) == 0:
            return
    spare = b + ".swap"
    os.rename(b, spare)
    os.rename(a, b)
    os.rename(spare, a)


def _remember(**kw) -> None:
    s = _state()
    s.update(kw)
    hist = s.get("history") if isinstance(s.get("history"), list) else []
    if kw.get("event"):
        hist.append(kw.pop("event"))
        s.pop("event", None)
    s["history"] = hist[-30:]
    _save(s)


def swap_in(new: Path | None = None) -> dict:
    """Swap the prepared copy in. The copy it replaces becomes `<tool>.previous` (an older one there goes to the
    Bin). Returns {"from", "to"}."""
    new = Path(new or new_dir())
    old_version = version()
    new_version = read_version(new) or "0"
    swap_folders(new, tool_dir())             # tool_dir() now holds the new copy, `new` the old one
    _trash(previous_dir())
    os.rename(new, previous_dir())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _remember(went_back_from=None, event={"at": stamp, "from": old_version, "to": new_version, "how": "update"})
    return {"from": old_version, "to": new_version}


def go_back() -> dict:
    """Swap `<tool>.previous` back in; the copy in use becomes the previous one, so this can be undone the same
    way. Returns {"from", "to"}."""
    if is_development():
        raise HandbookError("This is a development copy of the tool, so there is no previous version to go back to.")
    prev = previous_dir()
    if not (prev / "lib" / "hb").is_file():
        raise HandbookError("There is no previous version on this computer to go back to.")
    problems = [p for p in check_copy(prev, None) if "edition" in p or "launcher" in p]
    if problems:
        raise HandbookError("The previous version cannot be used: " + "; ".join(problems))
    old_version = version()
    back_to = read_version(prev) or "0"
    swap_folders(prev, tool_dir())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _remember(went_back_from=old_version, event={"at": stamp, "from": old_version, "to": back_to, "how": "back"})
    return {"from": old_version, "to": back_to}


def history() -> list[dict]:
    return list(_state().get("history") or [])


# ---- "email me about big updates" (opt-in) ----------------------------------------------------------
# Only when the owner ticks the box (the page's first "What should we call you?" box, its footer, the chat or
# `hb update --email`): the address and the edition go to the tool's website, which keeps them in a list Benjy
# emails by hand for big releases. Unticking removes the address there. Nothing else is ever sent.

SITE_API = "https://the-benchmark-tool.vercel.app/api/updates"
EMAIL_RE = re.compile(r"^[^\s@<>\"']{1,64}@[^\s@<>\"']{1,190}\.[A-Za-z]{2,24}$")
EMAIL_PRIVACY = ("Only the address and which version you have (free or Full) go to the benchmark tool's website, "
                 "for a few emails a year about big changes. You can stop them at the bottom of the page at any time.")


def _site_api() -> str:
    return os.environ.get("HB_SITE_API") or SITE_API


def emails() -> dict:
    """{"email": the address this install gets update emails at, or None; "privacy": what is sent}."""
    e = _state().get("email")
    return {"email": e.get("address") if isinstance(e, dict) else None, "privacy": EMAIL_PRIVACY}


def set_email(address: str | None) -> dict:
    """Ask for update emails at `address`, or stop them (None). Raises HandbookError when the website says no or
    cannot be reached; the choice is only saved here once the website has it."""
    import secrets
    s = _state()
    install = s.get("install_id")
    if not isinstance(install, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", install):
        install = secrets.token_urlsafe(18)
        s["install_id"] = install
        _save(s)
    if address is not None:
        address = address.strip()
        if not EMAIL_RE.match(address):
            raise HandbookError("That doesn't look like an email address.")
        body = {"id": install, "email": address, "edition": edition.edition(), "subscribe": True}
    else:
        body = {"id": install, "subscribe": False}
    req = urllib.request.Request(_site_api(), data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            res.read()
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read().decode("utf-8")).get("error")
        except (ValueError, OSError):
            said = None
        raise HandbookError(said or f"The website said no ({e.code}). Try again later.") from None
    except OSError:
        raise HandbookError("The benchmark tool's website could not be reached. Is this computer online? "
                            "Try again in a minute.") from None
    s = _state()
    if address is None:
        s.pop("email", None)
    else:
        s["email"] = {"address": address, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _save(s)
    return emails()

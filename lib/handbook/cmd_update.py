"""hb update [--check] [--back] [--json]: get the latest version of the benchmark tool, or go back to the previous one.

`hb update --email ADDRESS` (or `--no-email`) starts or stops the opt-in emails about big updates.

`hb update --check` only asks GitHub whether a newer version is out and prints what is new (one request, which
sends nothing about you; the Full version sends its download key). `hb update` installs it: the new copy is
downloaded beside this one, checked, then swapped in, and the copy it replaces is kept for `hb update --back`.

When a page is open (`hb serve`), the update is handed to it, so it waits until no scoring job or Claude reply
is under way and then restarts every open page on the new version. Projects are not changed by updating; the new
version upgrades a project's files, after a backup, the first time it opens it.
"""
from __future__ import annotations
import json
import time

from . import updates
from .cli import command_parser
from .folder import HandbookError
from .updater import _call, back_blockers, blocked_words, other_pages, restore_before_upgrade


def _print_status(st: dict) -> None:
    print(f"This is version {st['current']} of the benchmark tool ({'Full' if st['edition'] == 'full' else 'free'}).")
    if st.get("development"):
        print("It is a development copy (inside a git repository), so it is not updated from GitHub.")
        return
    if st.get("problem") == "offline":
        print("GitHub could not be reached, so whether there is a newer version is not known.")
    elif st.get("problem") == "no_release":
        print("No version has been released yet.")
    if st.get("available"):
        print(f"Version {st['latest']} is out. What's new:")
        for line in st.get("notes") or []:
            print(f"  - {line}")
    elif st.get("latest"):
        print("It is the latest version.")
    if st.get("key_note"):
        print(st["key_note"])
    if st.get("previous"):
        print(f"Version {st['previous']} is kept: `hb update --back` goes back to it.")
    print(f"Emails about big updates go to {st['email']}." if st.get("email") else
          "No emails about updates (`hb update --email ADDRESS` asks for them).")


def _follow(info: dict) -> int:
    """Print what an open page's update is doing until it restarts, fails or stops."""
    said = None
    for _ in range(3600):
        try:
            s = _call(info, "GET", "update")
        except (OSError, ValueError):
            print("The page is restarting on the new version.")
            return 0
        if s.get("words") and s["words"] != said:
            said = s["words"]
            print(said, flush=True)
        if s.get("phase") in ("failed", "cancelled"):
            return 1
        if s.get("phase") == "restarting":
            return 0
        time.sleep(1)
    return 0


def main(argv) -> int:
    parser = command_parser("update", "Get the latest version of the benchmark tool, or go back to the previous one.",
                            project=False)
    parser.add_argument("--check", action="store_true", help="only say whether a newer version is out")
    parser.add_argument("--back", action="store_true", help="go back to the previous version")
    parser.add_argument("--restore", action="store_true",
                        help="with --back: put projects the previous version cannot read back as they were before "
                             "their upgrade (how they are now is kept in a copy)")
    parser.add_argument("--email", default=None, metavar="ADDRESS",
                        help="get an email about big updates at this address (only the address and the edition are sent)")
    parser.add_argument("--no-email", action="store_true", help="stop the emails about big updates")
    parser.add_argument("--json", action="store_true", help="print the status as JSON")
    args = parser.parse_args(argv)
    if args.email or args.no_email:
        got = updates.set_email(None if args.no_email else args.email)
        print(f"Emails about big updates go to {got['email']}." if got["email"] else "You won't get emails about updates.")
        if got["email"]:
            print(updates.EMAIL_PRIVACY)
        return 0

    st = updates.status(check=not args.back, force=args.check)
    if args.json and (args.check or (not st["available"] and not args.back)):
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 0
    if args.check or (not args.back and not st["available"]):
        _print_status(st)
        return 0
    if st.get("development"):
        raise HandbookError("This is a development copy of the tool (inside a git repository), so it is not updated "
                            "from GitHub.")
    how = "back" if args.back else "install"
    pages = other_pages()
    if pages:
        print("A page is open, so it does the update: it waits until nothing is running, then restarts on the "
              "new version.", flush=True)
        try:
            _call(pages[0], "POST", "update", {"action": "back_restore" if how == "back" and args.restore else how}, timeout=30)
        except OSError as e:
            raise HandbookError(f"The open page did not take the update ({e}). Close it and run hb update again.") from None
        return _follow(pages[0])
    if how == "back":
        blocked = back_blockers()
        if blocked and not (args.restore and all(b["backup"] for b in blocked)):
            raise HandbookError(blocked_words(blocked) + (" Add --restore to do that." if all(b["backup"] for b in blocked) else ""))
        for line in restore_before_upgrade(blocked) if blocked else []:
            print(line)
        done = updates.go_back()
        print(f"Back to version {done['to']}. `hb update --back` again returns to {done['from']}.")
        return 0
    print("What's new in version " + st["latest"] + ":")
    for line in st.get("notes") or []:
        print(f"  - {line}")
    new = updates.prepare(updates.latest(), say=lambda t: print(t, flush=True))
    done = updates.swap_in(new)
    print(f"Updated from version {done['from']} to {done['to']}. The previous version is kept: "
          "`hb update --back` goes back to it.")
    return 0

"""hb launcher --project DIR [--desktop]: make "Open <benchmark>.app", a double-click way back in.

The app starts `hb serve --app-window` for the project in the background (no Terminal window),
which opens the page in a window of its own (Chrome's app mode when Chrome is installed, else the
default browser), so an owner can stop and come back without asking Claude. It is a small
AppleScript app made with macOS's own `osacompile`; nothing is downloaded, and because it is made
on this Mac it opens without a security warning. It goes in the project folder, and with
`--desktop` a copy goes on the Desktop too. The server's output goes to `<project>/.serve.log`.

On Windows the same thing is a shortcut, "Open <benchmark>.lnk", made through PowerShell (built in):
it runs lib/hb.cmd in a minimised window, which keeps the page going; closing that window stops it.
It wears the same icon, written as an .ico. On Linux it is a desktop entry, "open-<benchmark>.desktop",
in the applications menu (~/.local/share/applications) and the project folder, running a small shell
script beside it. Being made on the computer, neither carries a downloaded-file warning.

The app wears the tool's own icon: a green rounded square with a white speech bubble and three
dots. The PNG is drawn here with the standard library alone (zlib and struct), turned into an
.iconset with `sips` and an .icns with `iconutil`, and copied over the app's applet.icns. If sips
or iconutil is missing, or anything in that goes wrong, the app keeps the stock script icon.
"""
from __future__ import annotations
import base64
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from . import system
from .cli import command_parser, open_project
from .folder import HandbookError, handbook_home

HB = Path(__file__).resolve().parents[1] / "hb"
HB_CMD = HB.with_name("hb.cmd")                  # the Windows launcher beside it
ICON_ICO = ".hb-icon.ico"                       # kept in the project folder for the shortcut
ICON_PNG = ".hb-icon.png"


def app_name(benchmark: str) -> str:
    """'Open <name>.app', with characters Finder dislikes in a name taken out."""
    clean = re.sub(r"\s+", " ", re.sub(r"[/:\\]+", " ", benchmark or "")).strip() or "my benchmark"
    return f"Open {clean}.app"


def windows_name(benchmark: str) -> str:
    """'Open <name>.lnk', with the characters Windows forbids in a file name taken out."""
    clean = re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", benchmark or "")).strip(" .") or "my benchmark"
    return f"Open {clean}.lnk"


def linux_name(benchmark: str) -> str:
    """'open-<name>.desktop', lower case with hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", (benchmark or "").lower()).strip("-") or "my-benchmark"
    return f"open-{slug}.desktop"


def _applescript_text(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def script(hb: Path, project: Path, log: Path | None = None) -> str:
    """The AppleScript: start `hb serve` in the background and return at once. Its output goes to
    `<project>/.serve.log`, or to `log` (a team project's goes to the person's own home folder)."""
    return "\n".join([
        f"set hb to {_applescript_text(str(hb))}",
        f"set proj to {_applescript_text(str(project))}",
        f"set logfile to {_applescript_text(str(log))}" if log else 'set logfile to proj & "/.serve.log"',
        'do shell script quoted form of hb & " serve --app-window --project " & quoted form of proj & '
        '" > " & quoted form of logfile & " 2>&1 &"',
    ])


GREEN = (0x1d, 0x5c, 0x4f)      # the tool's green
WHITE = (255, 255, 255)
ICON_SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]


def _box(x, y, cx, cy, hw, hh, r) -> float:
    """Signed distance (pixels; negative inside) to a rounded rectangle centred on (cx, cy)."""
    qx, qy = abs(x - cx) - (hw - r), abs(y - cy) - (hh - r)
    return (max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2) ** 0.5 + min(max(qx, qy), 0.0) - r


def _cover(d: float) -> float:
    """Anti-aliased coverage of a pixel whose centre is d pixels from an edge (negative = inside)."""
    return 0.0 if d >= 0.5 else 1.0 if d <= -0.5 else 0.5 - d


def icon_png(size: int = 1024) -> bytes:
    """The tool's icon as an RGBA PNG: green rounded square, white speech bubble, three green dots.
    Standard library only (zlib and struct); drawn from distance functions so the edges are smooth."""
    k = size / 1024.0
    square = (512 * k, 512 * k, 412 * k, 412 * k, 185 * k)          # cx, cy, half w, half h, radius
    bubble = (512 * k, 450 * k, 252 * k, 175 * k, 105 * k)
    tail = [(335 * k, 600 * k), (335 * k, 735 * k), (470 * k, 622 * k)]   # a triangle under the bubble's left
    dots = [(x * k, 450 * k, 36 * k) for x in (400, 512, 624)]
    edges = []
    for (ax, ay), (bx, by) in zip(tail, tail[1:] + tail[:1]):
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        edges.append((ax, ay, (by - ay) / length, -(bx - ax) / length))
    centre = (sum(p[0] for p in tail) / 3, sum(p[1] for p in tail) / 3)
    if all((centre[0] - ax) * nx + (centre[1] - ay) * ny > 0 for ax, ay, nx, ny in edges):
        edges = [(ax, ay, -nx, -ny) for ax, ay, nx, ny in edges]    # make "outside" positive
    rows = []
    for j in range(size):
        y = j + 0.5
        row = bytearray()
        for i in range(size):
            x = i + 0.5
            a = _cover(_box(x, y, *square))
            if a == 0.0:
                row += b"\x00\x00\x00\x00"
                continue
            r, g, b = GREEN
            if 230 * k < y < 800 * k and 240 * k < x < 790 * k:
                d = min(_box(x, y, *bubble), max((x - ax) * nx + (y - ay) * ny for ax, ay, nx, ny in edges))
                w = _cover(d)
                if w:
                    for dx, dy, dr in dots:
                        w *= 1.0 - _cover(((x - dx) ** 2 + (y - dy) ** 2) ** 0.5 - dr)    # the dots cut green holes
                    # back out of the bubble; w is now the white share of this pixel
                    r, g, b = (round(c * (1 - w) + 255 * w) for c in GREEN)
            row += bytes((r, g, b, round(255 * a)))
        rows.append(b"\x00" + bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def set_icon(app: Path) -> bool:
    """Give the app the tool's icon. Quietly does nothing (returns False) if sips or iconutil is
    missing or any step fails: the app works the same with its stock icon."""
    if shutil.which("sips") is None or shutil.which("iconutil") is None:
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "Tool.iconset"
            iconset.mkdir()
            master = Path(tmp) / "icon-1024.png"
            master.write_bytes(icon_png(1024))
            for base, scale in ICON_SIZES:
                name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
                px = base * scale
                if px == 1024:
                    shutil.copyfile(master, iconset / name)
                else:
                    got = subprocess.run(["sips", "-z", str(px), str(px), str(master), "--out", str(iconset / name)],
                                         capture_output=True, text=True)
                    if got.returncode != 0:
                        return False
            icns = Path(tmp) / "Tool.icns"
            if subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                              capture_output=True, text=True).returncode != 0:
                return False
            dest = app / "Contents" / "Resources" / "applet.icns"
            if not dest.parent.is_dir():
                return False
            shutil.copyfile(icns, dest)
        # Newer macOS also ships the stock icon in an asset catalogue named by CFBundleIconName,
        # and prefers it; drop both so the .icns is the one Finder shows, then re-seal the app.
        (app / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)
        if shutil.which("plutil"):
            subprocess.run(["plutil", "-remove", "CFBundleIconName", str(app / "Contents" / "Info.plist")],
                           capture_output=True, text=True)
        if shutil.which("codesign"):
            subprocess.run(["codesign", "--force", "--sign", "-", str(app)], capture_output=True, text=True)
        app.touch()                      # so Finder notices the new icon
        (app / "Contents").touch()
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def icon_ico(sizes=(16, 32, 48, 256)) -> bytes:
    """The icon as a Windows .ico: one PNG per size (Windows Vista and later read PNGs inside an .ico)."""
    pngs = [(s, icon_png(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, body = b"", b""
    for s, data in pngs:
        dim = 0 if s >= 256 else s                      # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return head + entries + body


def windows_target(hb_cmd: Path, project: Path) -> tuple[str, str]:
    """What the shortcut runs: cmd.exe, and its arguments. `cmd /c ""..." ..."`: with the whole line in
    one pair of outer quotes cmd keeps the inner quotes round paths with spaces."""
    comspec = os.environ.get("ComSpec") or r"C:\Windows\System32\cmd.exe"
    log = str(project) + "\\.serve.log"
    return comspec, f'/c ""{hb_cmd}" serve --app-window --project "{project}" > "{log}" 2>&1"'


SHORTCUT_PS = """$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:HB_LNK)
$s.TargetPath = $env:HB_TARGET
$s.Arguments = $env:HB_ARGS
$s.WorkingDirectory = $env:HB_DIR
$s.WindowStyle = 7
$s.IconLocation = $env:HB_ICON + ',0'
$s.Description = 'Opens the benchmark tool page'
$s.Save()
"""


def make_shortcut(target: Path, project: Path) -> None:
    """Make the Windows shortcut. The paths go to PowerShell in environment variables and the script
    as -EncodedCommand, so no quoting can go wrong between them."""
    if not HB_CMD.is_file():
        raise HandbookError(f"The Windows launcher {HB_CMD} is missing; install the tool again.")
    icon = project / ICON_ICO
    try:
        icon.write_bytes(icon_ico())
    except OSError:
        icon = None
    exe, args = windows_target(HB_CMD, project)
    env = dict(os.environ, HB_LNK=str(target), HB_TARGET=exe, HB_ARGS=args, HB_DIR=str(project),
               HB_ICON=str(icon) if icon else exe)
    encoded = base64.b64encode(SHORTCUT_PS.encode("utf-16-le")).decode("ascii")
    try:
        got = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                             capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise HandbookError(f"The shortcut could not be made: {e}") from None
    if got.returncode != 0 or not target.is_file():
        raise HandbookError(f"The shortcut could not be made: {(got.stderr or '').strip()[:200]}")


def _desktop_quote(arg: str) -> str:
    """One argument of a desktop entry's Exec line, quoted as the freedesktop spec asks: inside double
    quotes, \", `, $ and \\ take a backslash, and the whole value's backslashes are doubled again."""
    inner = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return ('"' + inner + '"').replace("\\", "\\\\")


def linux_script(hb: Path, project: Path) -> str:
    q = lambda s: "'" + str(s).replace("'", "'\\''") + "'"
    return "\n".join(["#!/bin/sh", "# Opens the benchmark tool page for this project (made by hb launcher).",
                      f"exec {q(hb)} serve --app-window --project {q(project)} > {q(str(project) + '/.serve.log')} 2>&1", ""])


def desktop_entry(script_path: Path, icon: Path | None, name: str) -> str:
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}", "Comment=Opens the benchmark tool page",
             f"Exec={_desktop_quote(str(script_path))}", "Terminal=false"]
    if icon:
        lines.append(f"Icon={icon}")
    return "\n".join(lines) + "\n"


def make_desktop_entry(places: list[Path], project: Path, benchmark: str) -> list[Path]:
    """The Linux launcher: a script in the project folder and a desktop entry in each place."""
    script_path = project / ".open-benchmark.sh"
    script_path.write_text(linux_script(HB, project), encoding="utf-8")
    script_path.chmod(0o755)
    icon = project / ICON_PNG
    try:
        icon.write_bytes(icon_png(256))
    except OSError:
        icon = None
    made = []
    entry = desktop_entry(script_path, icon, app_name(benchmark)[:-len(".app")])
    for place in places:
        place.mkdir(parents=True, exist_ok=True)
        path = place / linux_name(benchmark)
        path.write_text(entry, encoding="utf-8")
        path.chmod(0o755)
        made.append(path)
    return made


def make_app(target: Path, text: str) -> None:
    if shutil.which("osacompile") is None:
        raise HandbookError("Making the launcher needs a Mac (osacompile was not found).")
    if target.exists():
        shutil.rmtree(target)
    got = subprocess.run(["osacompile", "-o", str(target), "-e", text], capture_output=True, text=True)
    if got.returncode != 0:
        raise HandbookError(f"The launcher could not be made: {(got.stderr or '').strip()[:200]}")
    set_icon(target)


def main(argv) -> int:
    parser = command_parser("launcher", "Make a double-click app that opens this benchmark's page.")
    parser.add_argument("--desktop", action="store_true", help="also put a copy on the Desktop")
    args = parser.parse_args(argv)
    project = open_project(args.project)
    benchmark = (project.read("project") or {}).get("name")
    team = project.is_team()
    # A shared folder holds nothing that belongs to one person: the app (it names this computer's
    # copy of the tool) and its output go in this person's own places.
    own = [] if team else [project.path]
    desktop = [Path.home() / "Desktop"] if (args.desktop or team) else []
    if system.windows():
        for place in own + desktop:
            make_shortcut(place / windows_name(benchmark), project.path)
            print(f"Made {place / windows_name(benchmark)}.")
        print("Double-click it to open the benchmark's page. It keeps a small window open in the taskbar while "
              "the page runs; close that window to stop.")
        return 0
    if not system.mac():
        places = own + [Path.home() / ".local" / "share" / "applications"] + desktop
        for path in make_desktop_entry(places, project.path, benchmark):
            print(f"Made {path}.")
        print("Open it from your applications menu (or double-click it) to open the benchmark's page.")
        return 0
    name = app_name(benchmark)
    if team:
        log = handbook_home() / "logs" / f"{project.slug}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        text = script(HB, project.path, log)
    else:
        text = script(HB, project.path)
    places = own + desktop
    for place in places:
        make_app(place / name, text)
        print(f"Made {place / name}.")
    print("Double-click it to open the benchmark's page; it starts the tool in the background.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

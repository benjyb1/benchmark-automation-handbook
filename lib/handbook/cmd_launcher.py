"""hb launcher --project DIR [--desktop]: make "Open <benchmark>.app", a double-click way back in.

The app starts `hb serve --app-window` for the project in the background (no Terminal window),
which opens the page in a window of its own (Chrome's app mode when Chrome is installed, else the
default browser), so an owner can stop and come back without asking Claude. It is a small
AppleScript app made with macOS's own `osacompile`; nothing is downloaded, and because it is made
on this Mac it opens without a security warning. It goes in the project folder, and with
`--desktop` a copy goes on the Desktop too. The server's output goes to `<project>/.serve.log`.

The app wears the tool's own icon: a green rounded square with a white speech bubble and three
dots. The PNG is drawn here with the standard library alone (zlib and struct), turned into an
.iconset with `sips` and an .icns with `iconutil`, and copied over the app's applet.icns. If sips
or iconutil is missing, or anything in that goes wrong, the app keeps the stock script icon.
"""
from __future__ import annotations
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from .cli import command_parser, open_project
from .folder import HandbookError

HB = Path(__file__).resolve().parents[1] / "hb"


def app_name(benchmark: str) -> str:
    """'Open <name>.app', with characters Finder dislikes in a name taken out."""
    clean = re.sub(r"\s+", " ", re.sub(r"[/:\\]+", " ", benchmark or "")).strip() or "my benchmark"
    return f"Open {clean}.app"


def _applescript_text(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def script(hb: Path, project: Path) -> str:
    """The AppleScript: start `hb serve` in the background and return at once."""
    return "\n".join([
        f"set hb to {_applescript_text(str(hb))}",
        f"set proj to {_applescript_text(str(project))}",
        'do shell script quoted form of hb & " serve --app-window --project " & quoted form of proj & '
        '" > " & quoted form of (proj & "/.serve.log") & " 2>&1 &"',
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
        app.touch()                      # so Finder notices the new icon
        (app / "Contents").touch()
        return True
    except (OSError, subprocess.SubprocessError):
        return False


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
    name = app_name((project.read("project") or {}).get("name"))
    text = script(HB, project.path)
    places = [project.path] + ([Path.home() / "Desktop"] if args.desktop else [])
    for place in places:
        make_app(place / name, text)
        print(f"Made {place / name}.")
    print("Double-click it to open the benchmark's page; it starts the tool in the background.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

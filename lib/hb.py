# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdfium2>=5,<6", "openpyxl>=3.1,<4", "playwright>=1.45,<2", "pillow>=10,<13", "xlrd>=2.0.1,<3", "olefile>=0.47,<1"]
# ///
"""Entry point: `handbook/lib/hb <command> [--project FOLDER] [args]` runs this with the Python and
libraries listed above (fetched by uv); `python3 handbook/lib/hb.py ...` also works where they are
installed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import handbook.cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(handbook.cli.main(sys.argv[1:]))

"""Run a vendored audit script with the SSRF guard installed: python run_guarded.py <script.py> [args...]"""

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import guard  # noqa: E402

ALLOWED = {"geo_audit.py", "polish_audit.py"}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in ALLOWED:
        sys.exit("unknown script")
    guard.install()
    script = HERE / "vendor" / name
    sys.argv = [str(script)] + sys.argv[2:]
    runpy.run_path(str(script), run_name="__main__")

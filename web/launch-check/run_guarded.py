"""Run a vendored audit with the SSRF guard installed: python run_guarded.py <geo_audit.py|polish_audit.py> [args...]

The audit is imported (not run as a script) so Python reuses its compiled bytecode: on a small server CPU that
saves seconds per scan.
"""

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))
sys.path.insert(0, str(HERE))

import guard  # noqa: E402

def _bound_llms(module, limit=3.0):
    """Some sites generate llms.txt on the fly (10+ s): give that one optional check 3 s, then move on."""
    import threading
    original = module.Audit.load_llms

    def load_llms(self):
        t = threading.Thread(target=original, args=(self,), daemon=True)
        t.start()
        t.join(limit)
        if t.is_alive() or not getattr(self, "llms", None):
            self.llms = {"url": "", "found": False, "status": None, "html": False, "text": "",
                         "skipped": "llms.txt answered too slowly for the web check"}

    module.Audit.load_llms = load_llms


ALLOWED = {"geo_audit.py": "geo_audit", "polish_audit.py": "polish_audit"}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in ALLOWED:
        sys.exit("unknown script")
    guard.install()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # non-reconfigurable stream
        pass
    module = importlib.import_module(ALLOWED[name])
    if name == "geo_audit.py":  # web budget: read at most 3 sitemap files (a big sitemap index can take 10+ s)
        module.MAX_SITEMAP_FILES["network"] = 3
        _bound_llms(module)
    sys.exit(module.main(sys.argv[2:]))

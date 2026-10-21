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


def _cap_crawl_delay(module, cap=1.0):
    """The web check makes about 10 requests in total (no deep crawl), so a robots.txt Crawl-delay of 30 s would make
    it time out. Space requests at most 1 s apart instead: still slower than a normal visitor's browser."""
    original = module.Robots.delay_for

    def delay_for(self, token):
        return min(original(self, token), cap)

    module.Robots.delay_for = delay_for


CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote", "--disable-extensions",
                 "--disable-background-networking", "--disable-sync", "--mute-audio", "--no-first-run",
                 "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--js-flags=--max-old-space-size=192"]


def _browser_through_guard(module):
    """Phone render: headless Chromium whose every request goes through browser_proxy (same SSRF rules as the
    fetcher), and one JPEG screenshot of the first 1600 px instead of a 5000 px PNG."""
    import browser_proxy
    _srv, port = browser_proxy.start()

    def launch(pw):
        return pw.chromium.launch(headless=True, args=CHROMIUM_ARGS, chromium_sandbox=False,
                                  proxy={"server": f"http://127.0.0.1:{port}", "bypass": "<-loopback>"})

    def annotate(pages, findings, shots_dir, slug):
        out = {}
        shots_dir.mkdir(parents=True, exist_ok=True)
        for width, page in pages.items():
            marks = []
            for f in findings:
                color = {"HIGH": "#dc2626", "MED": "#ea580c", "LOW": "#ca8a04"}[f["severity"]]
                for bx in f.get("boxes", {}).get(width, [])[:4]:
                    marks.append({"box": bx, "label": "#%d" % f["rank"], "color": color})
            page.evaluate(module.ANNOTATE_JS, marks)
            path = shots_dir / ("%s-%d.jpg" % (slug, width))
            page.screenshot(path=str(path), type="jpeg", quality=62,
                            clip={"x": 0, "y": 0, "width": width, "height": 1600}, full_page=True)
            out[width] = str(path)
        return out

    module.launch = launch
    module.annotate = annotate


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
        _cap_crawl_delay(module)
    if name == "polish_audit.py":
        _browser_through_guard(module)
    sys.exit(module.main(sys.argv[2:]))

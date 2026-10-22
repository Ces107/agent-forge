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


STYLE_CHECKS = {"spacing", "type-sprawl", "type-scale", "palette", "line-length"}  # style notes, not spots
BLOCK_HOSTS = ("googletagmanager.com", "google-analytics.com", "doubleclick.net", "segment.com", "segment.io",
               "hotjar.com", "intercom.io", "intercomcdn.com", "facebook.net", "clarity.ms", "mixpanel.com",
               "amplitude.com", "posthog.com", "plausible.io/js", "fullstory.com", "logrocket.com", "sentry-cdn.com")

DISMISS_JS = r"""() => {
  const sels = ['#onetrust-accept-btn-handler', '#didomi-notice-agree-button', '.fc-cta-consent', '.fc-button.fc-cta-consent',
    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll', '#CybotCookiebotDialogBodyButtonAccept',
    '[data-testid="uc-accept-all-button"]', '.cc-allow', '.cc-dismiss', '#truste-consent-button', '#cookie-accept',
    '.cookie-accept', '#accept-cookies', '.js-accept-cookies', 'button[data-cookiebanner="accept_button"]',
    '.cky-btn-accept', '#wt-cli-accept-all-btn', '.cmplz-accept', '#hs-eu-confirmation-button'];
  let n = 0;
  for (const s of sels) { const e = document.querySelector(s); if (e) { try { e.click(); n++; } catch (_) {} } }
  if (!n) {
    const re = /^(accept( all)?( cookies)?|accept and close|agree|i agree|ok|okay|got it|allow( all)?( cookies)?|aceptar( todo| todas)?|accepter( tout)?|tout accepter|akzeptieren|alle akzeptieren|accetta( tutto)?|aceitar)$/i;
    const cands = [...document.querySelectorAll('button,[role=button],a,input[type=button],input[type=submit]')]
      .filter(b => re.test(((b.innerText || b.value || '') + '').trim()));
    for (const b of cands.slice(0, 3)) {
      const box = b.closest('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],[id*=gdpr i],[class*=gdpr i],[class*=cmp],[id*=cmp],[role=dialog],[aria-modal=true],[class*=banner i]');
      if (box) { try { b.click(); n++; } catch (_) {} }
    }
  }
  return n;
}"""

BOTCHECK_JS = r"""() => {
  const t = (document.title || '') + ' ' + (document.body ? document.body.innerText.slice(0, 3000) : '');
  if (/just a moment|performing security verification|checking your browser|verify you are human|attention required|ddos protection|are you a robot|please enable cookies and reload|access denied|request blocked/i.test(t)) return true;
  return !!document.querySelector('#challenge-form,#challenge-running,#cf-challenge-running,.cf-turnstile,iframe[src*="challenges.cloudflare.com"],iframe[src*="captcha"],#px-captcha,.g-recaptcha,.h-captcha');
}"""

STYLED_JS = r"""() => [...document.querySelectorAll('link[rel~="stylesheet"]')]
  .filter(l => !l.disabled && (!l.media || l.media === 'all' || matchMedia(l.media).matches)).every(l => !!l.sheet)"""


def _browser_through_guard(module):
    """Renders through browser_proxy (same SSRF rules as the fetcher). Cookie banners are dismissed before the
    screenshot, bot-check pages are detected (never boxed), fonts/video/analytics are not loaded, and the free
    phone render is one viewport tall."""
    import browser_proxy
    _srv, port = browser_proxy.start()

    import json
    import os
    import re
    web = "LC_MARKS" in os.environ  # free phone render: box the listed problems, numbered as in the result's list
    ranks = json.loads(os.environ.get("LC_MARKS") or "{}")
    if isinstance(ranks, list):  # older [key, ...] form
        ranks = {k: i + 1 for i, k in enumerate(ranks)}
    ranks = {k: int(v) for k, v in ranks.items()}
    order = sorted(ranks, key=ranks.get)
    listn = int(os.environ.get("LC_LISTN") or max(ranks.values(), default=0))  # length of the result's list
    state = {"botcheck": False}
    clip_h = 812 if web else 1600

    def launch(pw):
        return pw.chromium.launch(headless=True, args=CHROMIUM_ARGS, chromium_sandbox=False,
                                  proxy={"server": f"http://127.0.0.1:{port}", "bypass": "<-loopback>"})

    def key_of(f):
        if f["check"] == "images":
            m = re.search(r"stretched (\d+)%", f.get("detail", ""))
            return "images-stretched" if m and int(m.group(1)) > 10 else "images"
        return f["check"]

    def boxes_in_view(f, width, cap=3, y0=0):
        return [bx for bx in f.get("boxes", {}).get(width, [])
                if bx and len(bx) == 4 and bx[2] >= 2 and bx[3] >= 2 and y0 <= bx[1] < y0 + clip_h - 12
                and bx[0] < width][:cap]

    def visible_spot(f):
        return key_of(f) != "images"

    def best_window(spots, width, doc_h):
        """The first screen if it shows at least 2 spots; otherwise the screen-tall stretch of the page that does."""
        def count(y0):  # distinct kinds of visible problems in this stretch of the page
            return len({key_of(f) for f in spots if visible_spot(f) and boxes_in_view(f, width, 1, y0)})
        if count(0) >= 2:
            return 0
        cands = sorted({int(max(0, min(bx[1] - 140, max(0, doc_h - clip_h))))
                        for f in spots if visible_spot(f) for bx in f.get("boxes", {}).get(width, [])[:3]
                        if bx and len(bx) == 4} | {0})
        near = [y for y in cands if y <= 3 * clip_h]  # deep in the page, lazy content shifts boxes off their targets
        for pool in (near, cands):
            scored = [(count(y), -y) for y in pool]
            n, y = max(scored)
            if n:
                return -y
        return 0

    def annotate(pages, findings, shots_dir, slug):
        out = {}
        shots_dir.mkdir(parents=True, exist_ok=True)
        spots = [f for f in findings if f["check"] not in STYLE_CHECKS]
        if web:  # near-1:1 "contrast" is an icon or a decorative element, not text anyone reads: never shown
            spots = [f for f in spots if not (f["check"] == "contrast" and re.search(r"\b1\.[0-2]\d?:1", f.get("detail", "")))]
        if not web:  # paid renders: number the boxed problems 1..N with no gaps (style notes are never boxed)
            for i, f in enumerate(spots):
                f["rank"] = i + 1
            for f in findings:
                if f["check"] in STYLE_CHECKS:
                    f["rank"] = None
        for width, page in pages.items():
            marks = []
            legend = []
            kinds = {}
            boxed = 0
            if state["botcheck"]:
                mode = "botcheck"
            elif web and state.get("unstyled"):
                mode = "unstyled"  # the CSS never arrived: a measurement of this would be fiction
            elif web:
                doc_h = page.evaluate("() => document.documentElement.scrollHeight")
                top = [f for f in spots if ranks.get(key_of(f), 99) <= 3 and visible_spot(f)]
                listed = [f for f in spots if key_of(f) in ranks and visible_spot(f)]
                y0 = best_window(top or listed or spots, width, doc_h)  # the screen that shows the free top 3
                for key in order:  # only problems from the result's list, with its numbers: the free top 3 first,
                    if ranks[key] > 3 and len(legend) >= 2:  # then later ones until at least 2 are boxed
                        break
                    if ranks[key] > 3 and key == "images":  # invisible (missing alt/size): never a filler box
                        continue
                    fs = [f for f in spots if key_of(f) == key]
                    got = [bx for f in fs for bx in boxes_in_view(f, width, 3, y0)][:3]
                    if got:
                        n = ranks[key]
                        marks += [{"box": bx, "label": "#%d" % n, "color": "#dc2626"} for bx in got]
                        legend.append({"n": n, "key": key, "detail": fs[0].get("detail", ""),
                                       "fix": fs[0].get("fix", ""), "sev": fs[0]["severity"]})
                        boxed += sum(1 for f in fs if boxes_in_view(f, width, 3, y0))
                extra = listn  # problems only the real render sees: they join the list as #N+1, #N+2 (paid pack)
                for f in spots:
                    if len(legend) >= 2:
                        break
                    key = key_of(f)
                    if key in ranks or not visible_spot(f) or any(x["key"] == key for x in legend):
                        continue
                    got = boxes_in_view(f, width, 3, y0)
                    if got:
                        extra += 1
                        marks += [{"box": bx, "label": "#%d" % extra, "color": "#dc2626"} for bx in got]
                        legend.append({"n": extra, "key": key, "detail": f.get("detail", ""), "fix": f.get("fix", ""),
                                       "sev": f["severity"], "x": 1 if listn else 0})
                        boxed += 1
                mode = "marks" if legend else ("clean" if not spots else "unboxed")
                for f in spots:  # every kind of problem the real browser saw (the result list is built from these)
                    key = key_of(f)
                    if visible_spot(f) or key == "images":
                        k = kinds.setdefault(key, {"key": key, "sev": f["severity"], "detail": f.get("detail", ""),
                                                   "fix": f.get("fix", ""), "count": 0})
                        k["count"] += max(1, int(f.get("count", 1)))
            else:
                mode = "all"
                for f in spots:
                    color = {"HIGH": "#dc2626", "MED": "#ea580c", "LOW": "#ca8a04"}[f["severity"]]
                    for bx in boxes_in_view(f, width, cap=4):
                        marks.append({"box": bx, "label": "#%d" % f["rank"], "color": color})
            if not web or state["botcheck"] or state.get("unstyled"):
                y0 = 0
            (shots_dir / ("marks-%d.json" % width)).write_text(json.dumps({
                "mode": mode, "legend": legend, "boxed": boxed, "total": len(spots), "y0": y0,
                "kinds": list(kinds.values()),
                "style": [f.get("detail", "") for f in findings if f["check"] in STYLE_CHECKS][:20]}), encoding="utf-8")
            if web:
                (shots_dir / "marks.json").write_text((shots_dir / ("marks-%d.json" % width)).read_text(encoding="utf-8"),
                                                      encoding="utf-8")
            if marks:
                page.evaluate(module.ANNOTATE_JS, marks)
            path = shots_dir / ("%s-%d.jpg" % (slug, width))
            page.screenshot(path=str(path), type="jpeg", quality=64,
                            clip={"x": 0, "y": y0, "width": width, "height": clip_h}, full_page=True)
            out[width] = str(path)
        return out

    def route(r):
        req = r.request
        if req.resource_type in ("media", "websocket", "eventsource", "manifest", "texttrack") or \
                any(h in req.url for h in BLOCK_HOSTS):
            return r.abort()
        return r.continue_()

    def measure_browser(target, viewports, timeout, offline, shots_dir, max_elements=None):
        max_elements = max_elements or (1500 if web else 3000)  # the free phone render measures the top of the page
        """Fast render for a small server CPU: DOM ready, load (max 1.5 s), cookie banner dismissed, short settle."""
        url, _kind = module.to_url(target)
        pw = module.load_playwright()().start()
        browser = launch(pw)
        try:
            results, pages = {}, {}
            for width in viewports:
                mobile = width < 1024
                ctx = browser.new_context(viewport={"width": width, "height": 812 if mobile else 900},
                                          is_mobile=mobile, has_touch=mobile, device_scale_factor=1)
                ctx.route("**/*", route)
                ctx.add_init_script(module.INIT_JS)
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                try:
                    page.wait_for_load_state("load", timeout=1500)
                except Exception:  # slow images: measure what is there
                    pass
                try:  # never measure an unstyled page: wait for the stylesheets (slow server CPU), else give up
                    page.wait_for_function(STYLED_JS, timeout=8000)
                except Exception:
                    if not page.evaluate(STYLED_JS):
                        state["unstyled"] = True
                try:
                    if page.evaluate(BOTCHECK_JS):
                        state["botcheck"] = True
                    elif page.evaluate(DISMISS_JS):
                        page.wait_for_timeout(500)
                except Exception:  # page navigated while we looked
                    pass
                page.evaluate("() => window.scrollTo(0, 0)")
                data = page.evaluate(module.MEASURE_JS, {"maxElements": max_elements, "taps": mobile})
                data["viewport"] = width
                results[width] = data
                pages[width] = page
            return results, pages, (pw, browser)
        except Exception:
            browser.close()
            pw.stop()
            raise

    module.launch = launch
    module.annotate = annotate
    module.measure_browser = measure_browser


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
        module.MAX_SITEMAP_FILES["network"] = 4  # the index + up to 3 child sitemaps
        _bound_llms(module)
        _cap_crawl_delay(module)
    if name == "polish_audit.py":
        _browser_through_guard(module)
    sys.exit(module.main(sys.argv[2:]))

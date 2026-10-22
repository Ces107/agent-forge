"""Run a vendored audit with the SSRF guard installed: python run_guarded.py <geo_audit.py|polish_audit.py> [args...]

The audit is imported (not run as a script) so Python reuses its compiled bytecode: on a small server CPU that
saves seconds per scan.
"""

import importlib
import re
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
  if (/just a moment|performing security verification|checking your browser|verify you are human|attention required|ddos protection|are you a robot|please enable cookies and reload|access denied|request blocked|complete the security check|human verification|unusual traffic|press (and|&) hold/i.test(t)) return true;
  if (/captcha/i.test(t) && t.length < 2500) return true;
  return !!document.querySelector('#challenge-form,#challenge-running,#cf-challenge-running,.cf-turnstile,iframe[src*="challenges.cloudflare.com"],iframe[src*="captcha"],#px-captcha,.g-recaptcha,.h-captcha');
}"""

HIDE_CONSENT_JS = r"""() => {
  const q = '[id*="cookie" i],[class*="cookie" i],[id*="consent" i],[class*="consent" i],[id*="gdpr" i],[class*="gdpr" i],' +
    '[aria-label*="cookie" i],[aria-label*="consent" i],[id*="privacy-banner" i],[class*="cmp-" i]';
  let n = 0;
  for (const el of document.querySelectorAll(q)) {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const p = getComputedStyle(e).position;
      if (p === 'fixed' || p === 'sticky') {  // a fixed bar that talks about cookies, not a sticky site header
        const t = e.innerText || '';
        if (t.length < 1500 && /cookie|consent|privacy|gdpr|tracking/i.test(t)) { e.style.setProperty('display', 'none', 'important'); n++; }
        break;
      }
    }
  }
  // banners with neutral class names: any fixed/sticky box (not the site header at the top) that asks about tracking
  const ask = /cookie|consent|privacy|gdpr|tracking|analytics|accept (all|optional)|reject (all|optional)/i;
  for (const e of document.body ? document.body.querySelectorAll('*') : []) {
    const c = getComputedStyle(e);
    if (c.position !== 'fixed' && c.position !== 'sticky') continue;
    const r = e.getBoundingClientRect();
    if (r.top < 10 && r.height < 130) continue;  // a sticky header
    const t = e.innerText || '';
    if (t.length < 1500 && ask.test(t) && e.querySelector('button,a,[role=button]')) {
      e.style.setProperty('display', 'none', 'important'); n++;
    }
  }
  return n;
}"""

MOBILE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 "
             "Mobile Safari/537.36")
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 "
              "Safari/537.36")

APPLY_JS = r"""(a) => {
  // only the element(s) drawn inside the boxed spots: a broad selector must not restyle other buttons
  const sx = window.scrollX, sy = window.scrollY;
  const hit = (e) => { const r = e.getBoundingClientRect(); const L = r.left + sx, T = r.top + sy;
    return a.boxes.some(b => L < b[0] + b[2] && L + r.width > b[0] && T < b[1] + b[3] && T + r.height > b[1]
      && Math.abs(r.width - b[2]) < 40 && Math.abs(r.height - b[3]) < 40); };
  let n = 0;
  for (const sel of a.sel.split(/;\s*\d+ selectors?:\s*|,\s*(?=[a-z#.])/i).slice(0, 2)) {
    try { document.querySelectorAll(sel.trim()).forEach(e => { if (hit(e)) { e.style.cssText += ';' + a.css; n++; } }); }
    catch (e) {}
  }
  return n;
}"""


def changed(png_a, png_b, min_pixels=600):
    """True when the two unmarked screenshots differ visibly (not just a green outline)."""
    try:
        import io
        from PIL import Image, ImageChops
        a, b = (Image.open(io.BytesIO(x)).convert("L") for x in (png_a, png_b))
        if a.size != b.size:
            return True
        return sum(1 for v in ImageChops.difference(a, b).getdata() if v > 24) >= min_pixels
    except Exception:  # can't compare: keep it
        return True


def fix_css(item):
    """The CSS a free fix stands for, applied to the boxed element for the before/after."""
    fix, key = item.get("fix", ""), item.get("key", "")
    m = re.search(r"use color: (#[0-9a-fA-F]{3,8})", fix) or re.search(r"color: #[0-9a-fA-F]{3,8} -> (#[0-9a-fA-F]{3,8})", fix)
    if key == "contrast" and m:  # exactly what the free fix says: opacity only when the fix names it
        op = "opacity: 1 !important; " if "opacity" in fix else ""
        return "%scolor: %s !important" % (op, m.group(1))
    m = re.search(r"background #[0-9a-fA-F]{3,8} -> (#[0-9a-fA-F]{3,8})", fix)
    if key == "contrast" and m:
        return "background: %s !important" % m.group(1)
    if key == "tap-target":
        return ("min-height: 44px !important; min-width: 44px !important; display: inline-flex !important; "
                "align-items: center !important; justify-content: center !important")
    return ""


STYLED_JS = r"""() => {
  const links = [...document.querySelectorAll('link[rel~="stylesheet"]')]
    .filter(l => !l.disabled && (!l.media || l.media === 'all' || matchMedia(l.media).matches));
  if (!links.length) return true;
  const rules = s => { try { return s.cssRules.length; } catch (e) { return 1; } };  // cross-origin: assume fine
  if (!links.every(l => l.sheet && rules(l.sheet) > 0)) return false;  // a 403/HTML answer gives an empty sheet
  const f = getComputedStyle(document.body || document.documentElement).fontFamily.toLowerCase();
  return !/^"?times new roman"?$|^serif$/.test(f.trim());  // site CSS loaded but the page still in browser defaults
}"""


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

    def visible_spot(f):  # structure problems (heading order, image labels) are listed, never boxed on the picture
        return key_of(f) not in ("images", "headings")

    tap_boxes = {}  # width -> every small tap target's box, for WCAG 2.2's spacing exception

    def spaced(f):  # WCAG 2.5.8: a target under 24 px passes when a 24 px circle on it touches no other target
        for width, bxs in (f.get("boxes") or {}).items():
            for bx in bxs:
                if not bx or len(bx) != 4:
                    continue
                cx, cy = bx[0] + bx[2] / 2, bx[1] + bx[3] / 2
                for o in tap_boxes.get(width, []):
                    if o is bx or o == bx:
                        continue
                    nx, ny = max(o[0], min(cx, o[0] + o[2])), max(o[1], min(cy, o[1] + o[3]))
                    if (nx - cx) ** 2 + (ny - cy) ** 2 < 12 ** 2:
                        return False
        return bool(f.get("boxes"))

    def petty(f):  # passes WCAG AA or misses by a hair: listed, never boxed or roasted for free
        if f["check"] == "tap-target":  # 24-43 px (logo, badge, theme toggle), or small but well spaced
            m = re.search(r"(\d+)x(\d+) px", f.get("detail", ""))
            return (bool(m) and min(int(m.group(1)), int(m.group(2))) >= 24) or spaced(f)
        if f["check"] == "overlap":  # a small touch (wrapping next to an emoji) is not text on top of text
            m = re.search(r"\((\d+) px2\)", f.get("detail", ""))
            return bool(m) and int(m.group(1)) < 3000
        if f["check"] == "contrast":  # 4.45:1 against 4.5 is invisible to anyone: box only clear failures
            m = re.search(r"contrast (\d+(?:\.\d+)?):1 \(needs (\d+(?:\.\d+)?)\)", f.get("detail", ""))
            return bool(m) and float(m.group(1)) >= 0.9 * float(m.group(2))
        return False

    def best_window(spots, width, doc_h):
        """The first screen if it shows at least 2 spots; otherwise the screen-tall stretch of the page that does."""
        def count(y0):  # distinct kinds of visible problems in this stretch of the page
            return len({key_of(f) for f in spots if visible_spot(f) and not petty(f) and boxes_in_view(f, width, 1, y0)})
        if count(0) >= 2:
            return 0
        cands = sorted({int(max(0, min(bx[1] - 140, max(0, doc_h - clip_h))))
                        for f in spots if visible_spot(f) and not petty(f) for bx in f.get("boxes", {}).get(width, [])[:3]
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
        for f in findings:  # all small targets, so spacing can be judged against the neighbours
            if f["check"] == "tap-target":
                for width, bxs in (f.get("boxes") or {}).items():
                    tap_boxes.setdefault(width, []).extend(b for b in bxs if b and len(b) == 4)
        if web:  # near-1:1 "contrast" is an icon or a decorative element, not text anyone reads: never shown
            # text over a photo/gradient we could not sample ("page background assumed") is not provably unreadable
            spots = [f for f in spots if not (f["check"] == "contrast" and (
                re.search(r"\b1\.[0-2]\d?:1", f.get("detail", "")) or "page background assumed" in f.get("detail", "")))]
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
            elif web and (state.get("unstyled") or state.get("status")):
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
                    fs = [f for f in spots if key_of(f) == key and not petty(f)]
                    got = [bx for f in fs for bx in boxes_in_view(f, width, 3, y0)][:3]
                    if got:
                        n = ranks[key]
                        first = next(f for f in fs if boxes_in_view(f, width, 3, y0))  # its numbers match the box
                        marks += [{"box": bx, "label": "#%d" % n, "color": "#dc2626"} for bx in got]
                        legend.append({"n": n, "key": key, "detail": first.get("detail", ""),
                                       "fix": first.get("fix", ""), "sev": first["severity"],
                                       "sel": first.get("selector", "")})
                        boxed += sum(1 for f in fs if boxes_in_view(f, width, 3, y0))
                extra = listn  # problems only the real render sees: they join the list as #N+1, #N+2 (paid pack)
                for f in sorted(spots, key=lambda s: key_of(s) != "contrast"):  # the most exact fix (colours) is #1
                    if len(legend) >= 3:  # three boxed problems: the free top 3, and three burns you can check
                        break
                    key = key_of(f)
                    if key in ranks or not visible_spot(f) or petty(f) or any(x["key"] == key for x in legend):
                        continue
                    got = boxes_in_view(f, width, 3, y0)
                    if got:
                        extra += 1
                        marks += [{"box": bx, "label": "#%d" % extra, "color": "#dc2626"} for bx in got]
                        legend.append({"n": extra, "key": key, "detail": f.get("detail", ""), "fix": f.get("fix", ""),
                                       "sel": f.get("selector", ""),
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
            if not web or state["botcheck"] or state.get("unstyled") or state.get("status"):
                y0 = 0
            (shots_dir / ("marks-%d.json" % width)).write_text(json.dumps({
                "mode": mode, "legend": legend, "boxed": boxed, "total": len(spots), "y0": y0,
                "kinds": list(kinds.values()), "css": state.get("css", [])[:8],
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
            # only a fix you can see makes a before/after (a taller tap target looks the same in a screenshot)
            pick = next((x for x in legend if x.get("key") == "contrast" and x.get("sel") and fix_css(x)), None) if web else None
            if pick:  # the same screen with one boxed fix applied (contrast first: the most visible): free before/after
                try:
                    page.evaluate("() => { const l = document.getElementById('__pa_layer'); if (l) l.remove(); }")
                    clip = {"x": 0, "y": y0, "width": width, "height": clip_h}
                    plain = page.screenshot(type="png", clip=clip, full_page=True)  # unmarked, before the fixes
                    done = 0
                    for x in legend:  # every boxed contrast fix at once: the after image shows the whole improvement
                        if x.get("key") == "contrast" and x.get("sel") and fix_css(x):
                            xb = [m["box"] for m in marks if m.get("label") == "#%d" % x["n"]]
                            done += page.evaluate(APPLY_JS, {"sel": x["sel"], "css": fix_css(x), "boxes": xb}) or 0
                    if done:
                        page.wait_for_timeout(120)
                        if not changed(plain, page.screenshot(type="png", clip=clip, full_page=True)):
                            raise ValueError("fix not visible")  # a before/after you can't see is not shown
                        fixed = [{"box": m["box"], "label": "", "color": "#16a34a"}  # outline only: nothing covers the fix
                                 for x in legend if x.get("key") == "contrast"
                                 for m in marks if m.get("label") == "#%d" % x["n"]]
                        if fixed:
                            page.evaluate(module.ANNOTATE_JS, fixed)
                        state["after_n"] = pick["n"]
                        page.screenshot(path=str(shots_dir / "after.jpg"), type="jpeg", quality=64,
                                        clip={"x": 0, "y": y0, "width": width, "height": clip_h}, full_page=True)
                        mj = shots_dir / "marks.json"
                        mj.write_text(json.dumps({**json.loads(mj.read_text(encoding="utf-8")), "after_n": pick["n"],
                                                  "after_fix": fix_css(pick)}), encoding="utf-8")
                except Exception:  # the before/after is a bonus: never break the render
                    pass
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
                                          is_mobile=mobile, has_touch=mobile, device_scale_factor=1,
                                          user_agent=MOBILE_UA if mobile else DESKTOP_UA)  # some sites 404 "Headless"
                ctx.route("**/*", route)
                ctx.add_init_script(module.INIT_JS)
                page = ctx.new_page()
                page.on("response", lambda resp: resp.request.resource_type == "stylesheet" and state.setdefault(
                    "css", []).append(f"{resp.status} {resp.headers.get('content-type', '')[:24]}"))
                page.on("requestfailed", lambda req: req.resource_type == "stylesheet" and state.setdefault(
                    "css", []).append(f"failed {req.failure or ''}"[:60]))
                resp = page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                if resp is not None and not 200 <= resp.status < 300:
                    state["status"] = resp.status  # an error page (404, 403, 5xx) is never measured or roasted
                try:
                    page.wait_for_load_state("load", timeout=1500)
                except Exception:  # slow images: measure what is there
                    pass
                for attempt in (1, 2):  # never measure an unstyled page: wait for the stylesheets, else give up
                    try:
                        page.wait_for_function(STYLED_JS, timeout=8000)
                        state.pop("unstyled", None)
                        break
                    except Exception:
                        state["unstyled"] = not page.evaluate(STYLED_JS)
                        if not state["unstyled"]:
                            break
                        if attempt == 1 and any(c.startswith("429") for c in state.get("css", [])):
                            page.wait_for_timeout(3000)  # rate-limited right after the crawl: one polite reload
                            state["css"] = []
                            page.reload(wait_until="domcontentloaded", timeout=int(timeout * 1000))
                        else:
                            break
                try:
                    if page.evaluate(BOTCHECK_JS):
                        state["botcheck"] = True
                    elif page.evaluate(DISMISS_JS):
                        page.wait_for_timeout(500)
                    if os.environ.get("LC_HIDE_CONSENT", "1") == "1":  # banners no button matched: hidden, never boxed
                        state["hidden"] = page.evaluate(HIDE_CONSENT_JS)
                except Exception:  # page navigated while we looked
                    pass
                page.evaluate("() => window.scrollTo(0, 0)")
                try:  # fade-ins caught halfway read as faint text: jump every running animation to its end state
                    page.evaluate("() => document.getAnimations().forEach(a => { try { a.finish(); } catch (e) {} })")
                    page.wait_for_timeout(150)
                except Exception:  # infinite animations can't finish: measure as they are
                    pass
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

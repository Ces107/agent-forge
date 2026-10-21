"""One passive Launch Check scan for the web: same checks and report card as the CLI, inside a time budget.

Differences from the CLI (honest by design):
  * no --owner-check, no --repo: passive only;
  * phone design uses the static HTML/CSS checks (no browser on the server) and is labelled "basic";
  * no page-load metrics (no browser);
  * every connection goes through guard.py (public addresses, ports 80/443 only), redirects re-checked per hop.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 - fixed argument lists, vendored scripts only
import sys
import threading
import time
from pathlib import Path
from urllib import parse as urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))
sys.path.insert(0, str(HERE))

import guard  # noqa: E402
import launch_check as lc  # noqa: E402

guard.install()

REQ_TIMEOUT = 8.0
BUDGET = 30.0          # seconds for the whole scan (typical: 5-20 s)
SUB_CAP = 20.0         # cap for each vendored audit subprocess
MAX_ASSETS = 40        # first-party scripts scanned for keys (3 MB each, 15 MB in all, inside the time budget)
SCRIPT_WORKERS = 6
SCRIPT_PHASE = 8.0     # seconds: no new batch of scripts after this (keeps the scan near 20 s on a small CPU)
lc.USER_AGENT = lc.USER_AGENT.replace("passive pre-launch check", "passive pre-launch check, web")
lc.AREAS = [("security", "Security"), ("phone", "Phone layout (1 size)"), ("search", "Found by Google & AI"),
            ("speed", "Speed & basics")]
lc.MAX_LINKS = 6
lc.PHONE_PLAIN["images"] = "Some images are missing a size or alt text"
lc.PHONE_PLAIN["images-stretched"] = "Some images are visibly stretched out of shape"
STRETCH = re.compile(r"stretched (\d+)%")


def phone_key(f):
    """Polish check name, with visibly stretched images (> 10 % distortion) split from missing alt/size."""
    if f.get("check") == "images":
        m = STRETCH.search(f.get("detail", ""))
        return "images-stretched" if m and int(m.group(1)) > 10 else "images"
    return f.get("check")
lc.PHONE_PLAIN.setdefault("fixed-width", "Some parts have a fixed width that is wider than a phone screen")
lc.PHONE_PLAIN.setdefault("type-scale", "Font sizes do not follow a clear scale: the page looks unplanned")
lc.LINK_DELAY = 0.05
# Google API keys (AIza..., Maps/Firebase/Gemini): once Gemini is enabled on the project, an unrestricted key in the
# page is a billable credential (reported bills: $82K in 48 h, ~$6K in 40 min). Amber, not red: a Firebase web key
# restricted to the domain and APIs is fine.
GOOGLE_TITLE = "Your Google/Gemini API key is visible in your website's code: anyone can use it on your bill"
GOOGLE_FIX = ("Restrict the key to your domain and APIs in Google Cloud, or call Gemini from a server function; "
              "then rotate it.")
lc.SECRETS["google-api"] = (lc.SECRETS["google-api"][0], "MEDIUM", GOOGLE_TITLE, GOOGLE_FIX)


class ScanError(Exception):
    """A message that is safe to show the visitor."""


# ------------------------------------------------------------------ which scripts are the site's own
TWO_LEVEL_TLDS = {"co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "com.au", "net.au", "org.au",
                  "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "com.br", "net.br", "com.mx", "com.ar", "co.in",
                  "net.in", "org.in", "co.za", "com.sg", "com.hk", "com.tr", "com.cn", "com.tw", "co.kr", "co.il",
                  "com.es", "com.pl", "co.id", "com.my", "com.ph", "com.vn", "com.ng", "com.eg", "com.co", "com.pe"}
# Hosts where every customer gets a subdomain: treat them as public suffixes (a.vercel.app is not b.vercel.app).
SHARED_HOSTS = ("vercel.app", "netlify.app", "pages.dev", "workers.dev", "lovable.app", "lovableproject.com",
                "github.io", "web.app", "firebaseapp.com", "onrender.com", "herokuapp.com", "replit.app", "repl.co",
                "fly.dev", "railway.app", "surge.sh", "glitch.me", "azurestaticapps.net", "amplifyapp.com",
                "cloudfront.net", "b-cdn.net", "supabase.co", "bolt.host", "webflow.io", "framer.app", "wixsite.com")
# App hosts / CDNs that serve a site's OWN bundles: scanned even when the host differs from the page.
APP_HOSTS = ("vercel.app", "netlify.app", "pages.dev", "lovable.app", "lovableproject.com", "cloudfront.net",
             "b-cdn.net")
THIRD_PARTY = ("googletagmanager.com", "google-analytics.com", "googleapis.com", "gstatic.com", "google.com",
               "doubleclick.net", "segment.com", "segment.io", "hotjar.com", "intercom.io", "intercomcdn.com",
               "stripe.com", "stripe.network", "jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "cloudflare.com",
               "cloudflareinsights.com", "facebook.net", "connect.facebook.net", "clarity.ms", "posthog.com",
               "mixpanel.com", "amplitude.com", "sentry-cdn.com", "sentry.io", "crisp.chat", "tawk.to",
               "hubspot.com", "hs-scripts.com", "hs-analytics.net", "plausible.io", "umami.is", "vercel-insights.com",
               "vercel-scripts.com", "twitter.com", "x.com", "linkedin.com", "licdn.com", "tiktok.com", "youtube.com",
               "paypal.com", "recaptcha.net", "hcaptcha.com", "cookiebot.com", "onetrust.com", "cookielaw.org",
               "jquery.com", "bootstrapcdn.com", "fontawesome.com", "typekit.net", "calendly.com", "zdassets.com",
               "zendesk.com", "drift.com", "fullstory.com", "logrocket.com", "datadoghq-browser-agent.com")


def registrable(host):
    """Simple eTLD+1: example.co.uk, myapp.vercel.app, example.com."""
    labels = (host or "").lower().rstrip(".").split(".")
    if len(labels) < 2:
        return host or ""
    last2 = ".".join(labels[-2:])
    if last2 in TWO_LEVEL_TLDS or last2 in SHARED_HOSTS or ".".join(labels[-3:]) in SHARED_HOSTS:
        n = 4 if ".".join(labels[-3:]) in SHARED_HOSTS else 3
        return ".".join(labels[-n:])
    return last2


def _under(host, suffixes):
    return any(host == s or host.endswith("." + s) for s in suffixes)


def script_party(url, base):
    """'first' (same site, same registrable domain, or an app host serving bundles) | 'third' | 'bad'."""
    try:
        parts = urlparse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "bad"
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return "bad"
    base_host = (urlparse.urlsplit(base).hostname or "").lower()
    if registrable(host) == registrable(base_host):
        return "first"
    if _under(host, THIRD_PARTY):
        return "third"
    if host.endswith(".supabase.co") and parts.path.startswith("/storage/v1/object/public/"):
        return "first"
    if _under(host, APP_HOSTS):
        return "first"
    return "third"


def scan_scripts(fetcher, base, resp, parser):
    """Secrets in the HTML, inline scripts and the site's own JS bundles (same registrable domain or an app host),
    fetched in parallel. Third-party scripts (analytics, widgets, npm CDNs) are counted, never fetched."""
    base = resp.url
    text = lc.decode(resp.body)
    hits = lc.scan_text(text, "page HTML")
    backend = lc.find_backends(text, {})
    firebase = bool(FIREBASE_CONFIG.search(text))
    seen, queue, third = set(), [], 0
    for src in parser.scripts:
        full = urlparse.urljoin(base, src.strip()).split("#", 1)[0]
        party = script_party(full, base)
        if party == "third":
            third += 1
        elif party == "first" and full not in seen:
            seen.add(full)
            queue.append(full)
    assets = {"same_origin": len(queue), "scanned": 0, "bytes": 0, "skipped_offsite": third}
    lock = threading.Lock()
    started = time.time()

    def fetch(src):
        r, _chain = fetcher.get(src, lc.MAX_ASSET, start=src)  # redirects only within the script's own host
        if r.status != 200 or script_party(r.url, base) != "first":
            return src, None
        return src, lc.decode(r.body)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=SCRIPT_WORKERS) as pool:
        while queue and assets["scanned"] < MAX_ASSETS and assets["bytes"] < lc.MAX_TOTAL:
            if fetcher.deadline - time.time() < 2 or time.time() - started > SCRIPT_PHASE:
                break
            take = min(12, MAX_ASSETS - assets["scanned"])
            batch, queue = queue[:take], queue[take:]
            for src, body in pool.map(fetch, batch):
                if body is None:
                    continue
                with lock:
                    if assets["scanned"] >= MAX_ASSETS or assets["bytes"] >= lc.MAX_TOTAL:
                        break
                    assets["scanned"] += 1
                    assets["bytes"] += len(body)
                where = urlparse.urlsplit(src)
                label = where.path if script_party(src, base) == "first" and where.hostname == urlparse.urlsplit(base).hostname \
                    else f"{where.hostname}{where.path}"
                hits += lc.scan_text(body, label or src)
                lc.find_backends(body, backend)
                firebase = firebase or bool(FIREBASE_CONFIG.search(body))
                for m in re.finditer(r"""(?:import\(|from\s*|import\s*)["'`](\.{0,2}/[^"'`\s]{1,200}\.m?js)["'`]""", body):
                    nxt = urlparse.urljoin(src, m.group(1)).split("#", 1)[0]
                    if nxt not in seen and script_party(nxt, base) == "first":
                        seen.add(nxt)
                        queue.append(nxt)
                        assets["same_origin"] += 1
    assets["backend"] = backend
    assets["firebase_config"] = firebase or bool(backend.get("firebase_urls"))
    return classify_google(lc.secret_findings(hits), assets["firebase_config"]), assets


FIREBASE_CONFIG = re.compile(r"""\b(?:authDomain|projectId|storageBucket|messagingSenderId)["']?\s*:\s*["'`]""")
FIREBASE_TITLE = "Your Firebase/Google API key is in your website's code: restrict it to your domain"
FIREBASE_FIX = ("A Firebase web key is public by design, but restrict it in Google Cloud > Credentials to your domain "
                "(HTTP referrers) and to the Firebase APIs you use, and make sure the Gemini API is not enabled for it.")


def classify_google(findings, firebase):
    """AIza... key next to a Firebase config -> MEDIUM (restrict it). Without one (Gemini/Maps called straight
    from the browser) -> HIGH with the bill wording."""
    for f in findings:
        if f["rule"] != "secret-google-api":
            continue
        if firebase:
            f.update(severity="MEDIUM", title=FIREBASE_TITLE, fix=FIREBASE_FIX)
        else:
            f.update(severity="HIGH", title=GOOGLE_TITLE, fix=GOOGLE_FIX)
    return findings


class BudgetFetcher(lc.Fetcher):
    """The CLI fetcher with a wall-clock budget: no new request after the deadline, per-request timeout <= 8 s."""

    def __init__(self, deadline):
        super().__init__(REQ_TIMEOUT, user_agent=lc.USER_AGENT)
        self.deadline = deadline

    def raw(self, url, cap, method="GET"):
        left = self.deadline - time.time()
        if left < 1:
            return lc.Resp(url, 0, {}, b"", 0.0, "time budget used")
        self.timeout = min(REQ_TIMEOUT, left)
        return super().raw(url, cap, method)


def validate(raw):
    """Normalise and pre-check the address. Raises ScanError with a visitor-safe message."""
    raw = (raw or "").strip()
    if not raw or len(raw) > 2000:
        raise ScanError("Paste your app's address, for example https://myapp.lovable.app")
    try:
        url = lc.normalise_target(raw)
    except ValueError as exc:
        raise ScanError(str(exc).capitalize())
    parts = urlparse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ScanError("Only http:// and https:// addresses can be checked")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise ScanError("That port is not valid")
    try:
        guard.resolve_public(parts.hostname or "", port)
    except guard.BlockedAddress:
        raise ScanError("That address is private or reserved. Only public websites can be checked.")
    except OSError:
        raise ScanError(f"Could not find {lc.clean(parts.hostname, 80)}. Check the address and try again.")
    return url


def run_vendored(script, args, timeout, extra_env=None):
    """The CLI's run_vendored, but through run_guarded.py (SSRF guard installed in the child too)."""
    cmd = [sys.executable, str(HERE / "run_guarded.py"), script] + args
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update({"PYTHONIOENCODING": "utf-8"})
    env.update(extra_env or {})
    try:
        run = subprocess.run(cmd, capture_output=True, timeout=max(2.0, timeout), stdin=subprocess.DEVNULL,  # nosec B603
                             env=env, cwd=str(HERE))
    except subprocess.TimeoutExpired:
        return None, "took too long on this site (time limit of the free web check)"
    try:
        return json.loads(run.stdout.decode("utf-8", "replace")), ""
    except json.JSONDecodeError:
        return None, "could not run on this site"


BROWSER = threading.BoundedSemaphore(1)   # one Chromium at a time (512 MB instance)
RENDER_BUDGET = float(os.environ.get("LC_RENDER_BUDGET", "12"))  # hard wall clock for the phone render
RENDER_MODE = os.environ.get("LC_RENDER", "0")      # "0" off, "sync" inside the scan, "async" after the result
RENDER_ENABLED = RENDER_MODE == "sync"
ASYNC_BUDGET = 45.0


RENDER_QUEUE_MAX = 3
_queue_lock = threading.Lock()
_queued = [0]


def render_slot_free():
    """True when a render can start now or wait in the queue (max 3 waiting)."""
    with _queue_lock:
        return _queued[0] < RENDER_QUEUE_MAX


def render_phone(url, marks, budget=ASYNC_BUDGET):
    """Background render (after the result is shown): the page at 375 px with the given problems boxed.
    marks = {phone key: label} (may be empty). If none of them matches an element, the render's own top phone
    problem is boxed instead; with no phone problem at all the page comes back clean.
    Returns (JPEG bytes, stats) or None when the queue is full, the render failed or took too long.
    stats = {"mode": "marks"|"fallback"|"clean", "matched": [labels], "boxed": n, "total": n, "fallback": check}."""
    import json as _json
    import shutil
    import tempfile
    with _queue_lock:
        if _queued[0] >= RENDER_QUEUE_MAX:
            return None
        _queued[0] += 1
    try:
        got = BROWSER.acquire(timeout=120)
    finally:
        with _queue_lock:
            _queued[0] -= 1
    if not got:
        return None
    shots = tempfile.mkdtemp(prefix="lc-async-")
    try:
        t0 = time.time()
        report, err = run_vendored("polish_audit.py", [url, "--viewports", "375", "--json", "--shots", shots,
                                                        "--timeout", "25"], budget,
                                   extra_env={"LC_MARKS": _json.dumps(marks)})
        sys.stderr.write(f"async render {time.time() - t0:.1f}s engine={(report or {}).get('engine')}\n")
        path = ((report or {}).get("screenshots") or {}).get("375")
        if report and report.get("engine") == "playwright" and path and os.path.isfile(path) \
                and os.path.getsize(path) < 900_000:
            stats_path = os.path.join(shots, "marks.json")
            stats = {}
            if os.path.isfile(stats_path):
                with open(stats_path, encoding="utf-8") as fh:
                    stats = _json.load(fh)
            with open(path, "rb") as fh:
                return fh.read(), stats
        return None
    finally:
        BROWSER.release()
        shutil.rmtree(shots, ignore_errors=True)


def _group_phone(report, prefix):
    grouped = {}
    for f in report.get("findings", []):
        sev = {"HIGH": "HIGH", "MED": "MEDIUM", "MEDIUM": "MEDIUM"}.get(f.get("severity", ""), "LOW")
        g = grouped.setdefault(phone_key(f), {"sev": sev, "count": 0, "first": f})
        g["count"] += max(1, int(f.get("count", 1) or 1))
        if lc.SEV_ORDER[sev] < lc.SEV_ORDER[g["sev"]]:
            g["sev"] = sev
    out = []
    for check, g in grouped.items():
        f, count = g["first"], g["count"]
        title = lc.PHONE_PLAIN.get(check, f.get("detail", ""))
        out.append(lc.finding("phone", f"phone-{check}", g["sev"], title + (f" ({count} places)" if count > 1 else ""),
                              f"{prefix}{f.get('fix', '')}", where=f.get("selector", ""), detail=f.get("detail", "")))
    out.sort(key=lambda x: lc.SEV_ORDER[x["severity"]])
    return out[:8]


def check_phone_render(url, timeout):
    """Real Chromium render at 375 px (through the SSRF proxy) with an annotated screenshot; static fallback."""
    import base64
    import shutil
    import tempfile
    why_static = ""
    if RENDER_ENABLED and BROWSER.acquire(timeout=4):
        shots = tempfile.mkdtemp(prefix="lc-shot-")
        try:
            t_render = time.time()
            report, err = run_vendored("polish_audit.py", [url, "--viewports", "375", "--json", "--shots", shots,
                                                            "--timeout", "9"], min(timeout, RENDER_BUDGET))
            sys.stderr.write(f"render {time.time() - t_render:.1f}s engine={(report or {}).get('engine')}\n")
            if report and report.get("engine") == "playwright":
                img = None
                path = (report.get("screenshots") or {}).get("375")
                if path and os.path.isfile(path) and os.path.getsize(path) < 900_000:
                    with open(path, "rb") as fh:
                        img = base64.b64encode(fh.read()).decode()
                legend = [{"rank": f.get("rank"), "sev": f.get("severity"),
                           "title": lc.PHONE_PLAIN.get(f.get("check"), f.get("detail", ""))}
                          for f in report.get("findings", []) if f.get("severity") in ("HIGH", "MED")][:8]
                phone = {"mode": "render", "image": img, "legend": legend,
                         "issues": sum(1 for f in report.get("findings", []) if f.get("severity") in ("HIGH", "MED"))}
                return int(report.get("score", 0)), _group_phone(report, "On a phone: "), "", phone
            why_static = err or "the browser could not render this page in time"
        finally:
            BROWSER.release()
            shutil.rmtree(shots, ignore_errors=True)
    else:
        why_static = "the phone renderer was busy" if RENDER_ENABLED else "no browser on this server"
    score, findings, why = check_phone_basic(url, timeout)
    return score, findings, why, {"mode": "static", "reason": why_static}


def check_phone_basic(url, timeout):
    report, err = run_vendored("polish_audit.py", [url, "--static", "--json", "--no-shots",
                                                    "--timeout", str(int(REQ_TIMEOUT))], timeout)
    if report is None:
        return None, [], f"skipped: {err}"
    return int(report.get("score", 0)), _group_phone(report, "On a phone: "), ""


FIX_FILE_CAP = 300_000


def read_fix_files(folder):
    """The files the vendored GEO + SEO engine wrote for this site (--emit-fixes): {relative name: text}."""
    files = {}
    for root, _dirs, names in os.walk(folder):
        for name in sorted(names):
            if name == "README.md":
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            try:
                if os.path.getsize(full) <= FIX_FILE_CAP:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        files[rel] = fh.read()
            except OSError:
                continue
    return files


DEMO_BASE = (os.environ.get("BASE_URL") or os.environ.get("PUBLIC_URL") or "").rstrip("/")
DEMO_PATH = "/demo/flowpilot/"
DEMO_SITE = "https://flowpilot.example/"


def is_demo(url):
    """The seeded FlowPilot page we host for the sample Fix Pack."""
    parts = urlparse.urlsplit(url)
    return bool(DEMO_BASE) and parts.path == DEMO_PATH and \
        (parts.hostname or "") == (urlparse.urlsplit(DEMO_BASE).hostname or "-")


def check_search(url, timeout):
    import shutil
    import tempfile
    emit = tempfile.mkdtemp(prefix="lc-fix-")
    site = None
    try:
        if is_demo(url):
            # The demo page lives on our own host, whose robots/sitemap/llms belong to Launch Check: audit the page
            # as the stand-alone site it pretends to be (geo --local), so the sample pack is what a real buyer gets.
            site = tempfile.mkdtemp(prefix="lc-demo-")
            shutil.copy(os.path.join(HERE, "static", "demo-flowpilot.html"), os.path.join(site, "index.html"))
            args = ["--local", site, "--base-url", DEMO_SITE, "--json", "--emit-fixes", emit, "--force"]
        else:
            args = [url, "--json", "--max-pages", "2", "--delay", "0.1", "--link-budget", "3",
                    "--timeout", str(int(REQ_TIMEOUT)), "--emit-fixes", emit, "--force"]
        report, err = run_vendored("geo_audit.py", args, timeout)
        fix_files = read_fix_files(emit) if report is not None else {}
    finally:
        shutil.rmtree(emit, ignore_errors=True)
        if site:
            shutil.rmtree(site, ignore_errors=True)
    if report is None:
        return None, [], f"skipped: {err}", {}
    out = []
    for f in report.get("findings", []):
        if f.get("severity") == "info" or f.get("rule") in lc.SEARCH_DUPES:
            continue
        statuses = re.findall(r"-> (\d{3})\b", " ".join(str(d) for d in f.get("details") or []))
        if f.get("rule") in ("sitemap-broken", "broken-link") and statuses and not any(lc.is_broken(int(s)) for s in statuses):
            continue
        sev = f.get("severity", "low").upper()
        if sev not in lc.SEV_ORDER:
            sev = "LOW"
        title = lc.SEARCH_PLAIN.get(f.get("rule"), f.get("title", ""))
        where = (f.get("urls") or [""])[0]
        out.append(lc.finding("search", f"search-{f.get('rule')}", sev, title, f.get("fix", ""), where=where,
                              detail=f"{f.get('count')}/{f.get('checked')} {f.get('title', '')}"))
    summary = ""
    llms = fix_files.get("llms.txt", "")
    m = re.search(r"^> (.+)$", llms, re.M)
    if m:
        summary = m.group(1).strip()[:300]
    return int(report.get("score", 0)), out[:12], "", {"pages": report.get("pages_crawled"), "fix_files": fix_files,
                                                       "summary": summary,
                                                       "pack_url": DEMO_SITE if site else "",
                                                       "sitemap_note": (report.get("sitemap_emit") or {}).get("note", "")}


def backend_findings(backend):
    """Supabase / Firebase config seen in the page (passive: nothing is queried)."""
    out = []
    host = lambda u: lc.clean(urlparse.urlsplit(u).hostname or u, 80)  # noqa: E731
    if backend.get("supabase_urls"):
        has_key = bool(backend.get("anon_keys"))
        out.append(lc.finding(
            "security", "supabase-found", "MEDIUM" if has_key else "LOW",
            "Supabase found: make sure Row Level Security is on for every table (the free Launch Check skill can "
            "test it on your own app)",
            "Turn on Row Level Security with per-user policies for every Supabase table (anonymous visitors must not "
            "read private tables), then test it with the full Launch Check on your own app.",
            where=host(backend["supabase_urls"][0]),
            detail="Supabase URL and public key in the page" if has_key else "Supabase URL in the page"))
    if backend.get("firebase_urls"):
        out.append(lc.finding(
            "security", "firebase-found", "MEDIUM",
            "Firebase found: make sure your database rules require sign-in (the free Launch Check skill can test it "
            "on your own app)",
            "Set Firebase Realtime Database / Firestore rules so .read and .write require sign-in and each user reaches "
            "only their own data, then test it with the full Launch Check on your own app.",
            where=host(backend["firebase_urls"][0])))
    return out


PRIVATE_MSG = "That address (or a redirect from it) points to a private or reserved address."


def fetch_page(fetcher, url, max_hops=5):
    """GET the page, following redirects across hosts (http->https, apex<->www, domain moves), at most 5 hops.
    Every hop connects through the SSRF guard, so a hop to a private/reserved address is refused."""
    chain = []
    for _ in range(max_hops + 1):
        resp = fetcher.raw(url, lc.MAX_HTML)
        if resp.status == 0:
            msg = resp.error or "no answer"
            if "refused" in msg or "private" in msg:
                raise ScanError(PRIVATE_MSG)
            raise ScanError(f"Could not load {lc.clean(url, 120)}: {lc.clean(msg, 100)}")
        if resp.status in (301, 302, 303, 307, 308) and resp.header("location"):
            nxt = urlparse.urljoin(url, resp.header("location").strip()).split("#", 1)[0]
            if not re.match(r"^https?://", nxt, re.I):
                raise ScanError("The address redirects to something that is not a web page.")
            try:
                parts = urlparse.urlsplit(nxt)
                guard.resolve_public(parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80))
            except guard.BlockedAddress:
                raise ScanError(PRIVATE_MSG)
            except (OSError, ValueError):
                raise ScanError(f"The address redirects to {lc.clean(nxt, 120)}, which could not be found.")
            chain.append((url, resp.status, nxt))
            url = nxt
            continue
        return resp, chain
    raise ScanError("The address redirects more than 5 times. Paste the final address of your app.")


FIX_ORDER = ("phone", "search", "speed", "security")
KEY_RULES = ("supabase-found", "firebase-found")


def is_key_item(f):
    return f["severity"] != "INFO" and (f["rule"].startswith("secret-") or f["rule"] in KEY_RULES)


def things_to_fix(findings):
    """THE count used everywhere on the result page: HIGH + MEDIUM, keys excluded (they have their own section),
    phone layout and Google/ChatGPT first, security headers last."""
    items = [f for f in findings if f["severity"] in ("HIGH", "MEDIUM") and not is_key_item(f)]
    return sorted(items, key=lambda f: (f["rule"] in DEMOTED, FIX_ORDER.index(f["area"]), lc.SEV_ORDER[f["severity"]]))


DEMOTED = {"phone-images"}  # missing alt/size is real but invisible: never ahead of visible problems


def area_note(key, fix, skipped):
    if skipped:
        return skipped
    fs = [f for f in fix if f["area"] == key]
    if not fs:
        return "nothing important found"
    more = f" (+{len(fs) - 1} more)" if len(fs) > 1 else ""
    return lc.short(fs[0]["title"], 74 - len(more)) + more


FLAG = re.compile(r"--[a-z][a-z0-9-]+")


def plain(text):
    """No command-line jargon in anything a visitor reads: drop '(... --flag ...)' asides and '; ... --flag' clauses."""
    if not text or "--" not in text:
        return text
    text = re.sub(r"\s*\([^()]*--[a-z][^()]*\)", "", text)
    text = "; ".join(part for part in text.split("; ") if not FLAG.search(part))
    text = re.sub(r"[^.;]*--[a-z][a-z0-9-]+[^.;]*[.;]?", "", text).strip()
    return text if text.endswith((".", "!", "?", ")")) or not text else text + "."


def scan(raw_url):
    t0 = time.time()
    deadline = t0 + BUDGET
    url = validate(raw_url)
    fetcher = BudgetFetcher(deadline)
    resp, chain = fetch_page(fetcher, url)
    if resp.status >= 400:
        raise ScanError(f"We couldn't reach your site (it returned {resp.status} to our checker). Try another URL "
                        "or check it's public.")
    final = resp.url
    parser = lc.PageParser()
    try:
        parser.feed(lc.decode(resp.body))
    except Exception:  # malformed HTML: keep what was parsed
        pass

    results = {}

    def bg(name, fn):
        try:
            results[name] = fn()
        except Exception:  # an audit crashed: the area is skipped, the rest still renders
            results[name] = None

    left = max(5.0, min(SUB_CAP, deadline - time.time()))
    threads = [threading.Thread(target=bg, args=("phone", lambda: check_phone_render(final, left)), daemon=True),
               threading.Thread(target=bg, args=("search", lambda: check_search(final, left)), daemon=True)]
    for t in threads:
        t.start()

    notes, skipped = [], {}
    findings, assets = scan_scripts(fetcher, final, resp, parser)
    if assets["skipped_offsite"] and not assets["scanned"]:
        notes.append(f"{assets['skipped_offsite']} scripts load from third-party hosts: not scanned for keys")
    findings += lc.check_headers(resp, final)
    https_f, note = lc.check_https(fetcher, final, chain)
    findings += https_f
    if note:
        notes.append(note)
    findings += lc.check_cookies(resp, final) + lc.check_mixed(parser, final)
    backend = assets.pop("backend", {})
    findings += backend_findings(backend)
    if any(f["rule"] == "supabase-found" for f in findings):
        findings = [f for f in findings if f["rule"] != "supabase-anon-ok"]  # one Supabase item, not two
    scores = {"security": lc.area_score([f for f in findings if f["area"] == "security"])}
    if any(f["severity"] == "HIGH" for f in findings if f["area"] == "security"):
        scores["security"] = min(scores["security"], 30)

    basic_f, basics_info = lc.check_basics(fetcher, resp, parser, None, assets)
    findings += basic_f
    scores["speed"] = lc.area_score(basic_f)

    for t in threads:
        t.join(timeout=max(0.5, deadline + 3 - time.time()))
    phone = results.get("phone") or (None, [], "skipped: did not finish in time", {"mode": "none"})
    scores["phone"], phone_f, why, phone_view = phone
    sys.stderr.write(f"phone mode={phone_view.get('mode')} img={bool(phone_view.get('image'))} "
                     f"reason={str(phone_view.get('reason', ''))[:120]}\n")
    findings += phone_f
    if why:
        skipped["phone"] = why
    search = results.get("search") or (None, [], "skipped: did not finish in time", {})
    scores["search"], search_f, why, search_info = search
    findings += search_f
    if why:
        skipped["search"] = why

    for f in findings:
        f.update(title=plain(f["title"]), fix=plain(f["fix"]), detail=plain(f.get("detail", "")))
    notes = [plain(n) for n in notes]
    findings.sort(key=lambda f: (lc.SEV_ORDER[f["severity"]], [k for k, _ in lc.AREAS].index(f["area"])))
    for area in ("phone", "search", "speed"):  # a category with a HIGH finding is never "Good"
        if scores.get(area) is not None and any(f["severity"] == "HIGH" and f["area"] == area for f in findings):
            scores[area] = min(scores[area], 79)
    overall = lc.combine(scores, findings)
    highs = sum(1 for f in findings if f["severity"] == "HIGH")
    fix_first = things_to_fix(findings)
    sec_high = sum(1 for f in findings if f["severity"] == "HIGH" and f["area"] == "security")
    word, sub = lc.verdict(overall, highs, len(fix_first), sec_high)
    return {
        "tool": "launch-check", "version": lc.VERSION + "-web", "url": lc.clean(url, 300),
        "final_url": lc.clean(final, 300), "seconds": round(time.time() - t0, 1), "mode": "passive, web",
        "score": overall, "verdict": word, "verdict_detail": sub, "pass": overall >= 80 and highs == 0,
        "fail_under": 80,
        "areas": {k: {"label": label, "score": scores.get(k), "grade": lc.grade(scores.get(k)),
                      "note": area_note(k, fix_first, skipped.get(k))} for k, label in lc.AREAS},
        "counts": {s.lower(): sum(1 for f in findings if f["severity"] == s) for s in lc.SEV_ORDER},
        "fix_first": fix_first, "findings": findings,
        "facts": {"assets": assets, "basics": basics_info, "requests_made": fetcher.requests,
                  "search": {k: v for k, v in search_info.items() if k != "fix_files"}},
        "fix_files": search_info.get("fix_files", {}),
        "pack_url": search_info.get("pack_url") or lc.clean(final, 300),
        "page_meta": {"title": lc.clean(parser.title.strip(), 200),
                      "description": lc.clean(parser.meta.get("description", "").strip(), 400),
                      "og": {k: lc.clean(v, 400) for k, v in parser.meta.items() if k.startswith(("og:", "twitter:"))},
                      "lang": lc.clean(parser.lang or "", 12), "summary": search_info.get("summary", "")},
        "notes": notes, "skipped": skipped, "screenshot": None, "phone_view": phone_view,
    }


def fix_list_text(report):
    return lc.ai_prompt(report)


def render(report):
    return lc.render_html(report, None)

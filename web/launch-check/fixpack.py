"""Fix Pack: the paid, install-free deliverable for one scanned site.

What is in it (all generated for THIS site):
  * the fix files from the vendored GEO + SEO engine (sitemap.xml, robots.txt, llms.txt, JSON-LD) plus
    meta-tags.html built from the page's own title/description/Open Graph tags;
  * real renders at 375 / 768 / 1440 px with every problem boxed (vendored design auditor, through the SSRF proxy);
  * every finding with its fix, and one numbered fix prompt for Lovable, Bolt, Cursor or Claude Code;
  * a zip (fix-pack-<host>.zip) with all of it and a README.txt saying where each file goes per platform.

Checkout: Polar (merchant of record). Switched on only when PAY_PROVIDER=polar and its env vars exist; otherwise the
site runs in DEMO mode (the buy button opens /fix/demo, a full pack for the seeded FlowPilot demo page).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from urllib import parse as urlparse

import scan as scanner

PRICE, PRICE_WAS = "$9", "$15"
# Real before/afters from re-scans, shown next to the buy button: LC_PROOF="site.com|62|94;other.app|55|90"
PROOF = [p.split("|") for p in os.environ.get("LC_PROOF", "").split(";") if p.count("|") == 2][:3]


def proof_line() -> str:
    if not PROOF:
        return ""
    items = " · ".join(f"{esc(h)} <b>{esc(a)} → {esc(b)}</b>" for h, a, b in PROOF)
    return f'<div class="fp-proof">After their Fix Pack: {items}</div>'
PAY_PROVIDER = os.environ.get("PAY_PROVIDER", "").strip().lower()
POLAR_TOKEN = os.environ.get("POLAR_ACCESS_TOKEN", "")
POLAR_PRODUCT = os.environ.get("POLAR_PRODUCT_ID", "")
POLAR_SERVER = os.environ.get("POLAR_SERVER", "sandbox").strip().lower()
POLAR_WEBHOOK_SECRET = os.environ.get("POLAR_WEBHOOK_SECRET", "")
PAY_SECRET = os.environ.get("PAY_SECRET") or secrets.token_hex(32)  # tokens die with the process if unset
CHECKOUT_ON = PAY_PROVIDER == "polar" and bool(POLAR_TOKEN and POLAR_PRODUCT and os.environ.get("PAY_SECRET"))
PACK_DAYS = 7
RENDER_WIDTHS = (375, 768, 1440)
SHOT_NAMES = {375: "phone-375.jpg", 768: "tablet-768.jpg", 1440: "desktop-1440.jpg"}

esc = lambda s: html.escape(str(s), quote=True)  # noqa: E731


# ---------------------------------------------------------------------------------------------- tokens
def token_for(scan_id: str) -> str:
    return hmac.new(PAY_SECRET.encode(), f"fixpack:{scan_id}".encode(), hashlib.sha256).hexdigest()[:32]


def token_ok(scan_id: str, token: str) -> bool:
    return bool(token) and hmac.compare_digest(token_for(scan_id), token)


# ------------------------------------------------------------------------------------------------ files
def _attr(value: str) -> str:
    return esc(re.sub(r"\s+", " ", value or "").strip())


def sentence_description(report: dict) -> str:
    """120-160 characters when possible, built from whole sentences of the page (h1 + first paragraph)."""
    pm, pt = report.get("page_meta") or {}, report.get("page_text") or {}
    current = (pm.get("description") or "").strip()
    if 70 <= len(current) <= 160 and not current.endswith(("…", "...")):
        return current
    para = re.sub(r"\s+", " ", (pt.get("p") or "")).strip()
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", para):
        sent = sent.strip()
        if not sent:
            continue
        if len(out) + len(sent) + 1 > 160:
            break
        out = (out + " " + sent).strip()
    h1 = (pt.get("h1") or pm.get("title") or "").strip().rstrip(".")
    if len(out) < 60 and h1:
        lead = h1 + "."
        out = (lead + " " + out).strip() if out and len(lead) + len(out) + 1 <= 160 else (out or lead)
    if not out.endswith((".", "!", "?")) and out:
        out += "."
    return out


def meta_tags_html(report: dict) -> str:
    pm = report.get("page_meta") or {}
    url = report.get("pack_url") or report.get("final_url", "")
    host = urlparse.urlsplit(url).hostname or url
    title = pm.get("title") or host
    desc = sentence_description(report)
    og = pm.get("og") or {}
    image = og.get("og:image") or ""
    lines = [f"<!-- Launch Check Fix Pack for {url}: paste inside <head> on the home page, replacing the old tags. -->",
             f"<title>{_attr(title)}</title>",
             f'<meta name="description" content="{_attr(desc)}">',
             f'<link rel="canonical" href="{_attr(url)}">',
             f'<meta property="og:type" content="website">',
             f'<meta property="og:url" content="{_attr(url)}">',
             f'<meta property="og:site_name" content="{_attr(og.get("og:site_name") or host)}">',
             f'<meta property="og:title" content="{_attr(og.get("og:title") or title)}">',
             f'<meta property="og:description" content="{_attr(og.get("og:description") or desc)}">',
             (f'<meta property="og:image" content="{_attr(image)}">' if image else
              '<meta property="og:image" content="https://YOUR-SITE/og-image.png"><!-- add a 1200x630 image -->'),
             '<meta name="twitter:card" content="summary_large_image">',
             f'<meta name="twitter:title" content="{_attr(og.get("og:title") or title)}">',
             f'<meta name="twitter:description" content="{_attr(og.get("og:description") or desc)}">']
    if image:
        lines.append(f'<meta name="twitter:image" content="{_attr(image)}">')
    return "\n".join(lines) + "\n"


def jsonld_html(fix_files: dict) -> str:
    try:
        manifest = json.loads(fix_files.get("jsonld/manifest.json") or "[]")
    except ValueError:
        manifest = []
    parts = []
    for item in manifest:
        body = fix_files.get(item.get("file", ""))
        if body:
            parts.append(f"<!-- {', '.join(item.get('types', []))} for {item.get('url', '')}: paste inside <head> -->\n"
                         + body.strip())
    return ("\n\n".join(parts) + "\n") if parts else ""


NOT_CONTENT = re.compile(r"/(api|auth|admin|login|logout|signin|sign-in|signup|sign-up|account|cart|checkout)(/|$)", re.I)


def sitemap_xml(pages: list) -> str:
    urls = []
    for pg in pages:
        u = pg.get("final_url") or pg.get("url")
        if pg.get("status") == 200 and not pg.get("noindex") and u and u not in urls and                 not NOT_CONTENT.search(urlparse.urlsplit(u).path):
            urls.append(u)
    if not urls:
        return ""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(f"  <url><loc>{html.escape(u)}</loc></url>" for u in urls) + "\n</urlset>\n")


def llms_txt(report: dict, pages: list) -> str:
    pm = report.get("page_meta") or {}
    name = (pm.get("og") or {}).get("og:site_name") or (pm.get("title") or "").split("|")[0].split(" - ")[0].strip() \
        or urlparse.urlsplit(report.get("pack_url") or report["final_url"]).hostname
    lines = [f"# {name}", "", f"> {sentence_description(report)}", "", "## Main pages", ""]
    seen = set()
    for pg in pages:
        u = pg.get("final_url") or pg.get("url")
        if pg.get("status") != 200 or pg.get("noindex") or not u or u in seen or                 NOT_CONTENT.search(urlparse.urlsplit(u).path):
            continue
        seen.add(u)
        title = re.sub(r"\s+", " ", (pg.get("title") or u).replace("�", "-")).strip()[:90]
        desc = re.sub(r"\s+", " ", (pg.get("description") or "").replace("�", "-")).strip()
        if desc.endswith(("…", "...")) or len(desc) < 20:
            desc = f"the {title.split('|')[0].strip()} page"
        lines.append(f"- [{title}]({u}): {desc[:200]}")
    return "\n".join(lines) + "\n"


def pack_files(report: dict) -> dict:
    """Ordered {file name: text} for this site (only files that change something)."""
    ff = report.get("fix_files") or {}
    pages = report.get("crawl_pages") or []
    if pages:  # paid pack: the deep crawl's own sitemap and llms.txt
        sm = sitemap_xml(pages)
        ff = {**ff, **({"sitemap.xml": sm} if sm else {}), "llms.txt": llms_txt(report, pages)}
    ff = {k: v.replace("geo-seo-audit", "Launch Check") for k, v in ff.items()}
    files = {}
    for name in ("sitemap.xml", "robots.txt", "llms.txt"):
        if ff.get(name):
            files[name] = ff[name]
    jl = jsonld_html(ff)
    if jl:
        files["json-ld.html"] = jl
    files["meta-tags.html"] = meta_tags_html(report)
    return files


FILE_ABOUT = {
    "sitemap.xml": "every page Google and AI search should know about",
    "robots.txt": "lets ChatGPT, Perplexity and Claude search bots in; keeps every rule you already had",
    "llms.txt": "a short map of your site for AI assistants",
    "json-ld.html": "structured data (Organization, WebSite, pages) so Google and AI know who you are",
    "meta-tags.html": "title, description, Open Graph and Twitter card for your home page",
}

README = """LAUNCH CHECK FIX PACK for {url}
Generated {date}. Score at the time: {score}/100.

WHAT IS IN HERE
{listing}
  screenshots/   your page at phone (375 px), tablet (768 px) and desktop (1440 px), every problem boxed
  FIX-PROMPT.txt one numbered prompt: paste it into Lovable, Bolt, v0, Cursor or Claude Code
  FINDINGS.txt   every problem we found, with the fix

WHERE EACH FILE GOES
  Lovable / Bolt / v0 (Vite or React apps): put sitemap.xml, robots.txt and llms.txt in the public/ folder.
     Paste meta-tags.html and json-ld.html into index.html inside <head>. Or simply attach the files and paste
     FIX-PROMPT.txt into the chat: the AI places them.
  Next.js: App Router: put the three text files in public/ (or use app/sitemap.ts and app/robots.ts), and move the
     tags from meta-tags.html into the metadata export of app/layout.tsx. Pages Router: public/ and _document.tsx.
  Plain HTML site: put the three text files at the site root (next to index.html) and paste the two .html snippets
     inside <head> of index.html.
  WordPress / Webflow / Framer: use the SEO settings for title, description and social image; upload robots.txt
     and llms.txt where the platform allows custom files.

AFTER YOU DEPLOY
  Re-scan for free for 7 days: {rescan}
"""


def full_steps(report: dict, files: dict) -> list:
    """Every fix as one step, most important first, then the files to add."""
    steps = []
    keys = [f for f in report["findings"] if scanner.is_key_item(f)]
    rest = scanner.things_to_fix(report["findings"])
    low = [f for f in report["findings"] if f["severity"] == "LOW" and not scanner.is_key_item(f)
           and f["rule"] not in scanner.STYLE_RULES]
    for f in keys + rest + low:
        where = f" (on {f['where']})" if f.get("where") and f["area"] != "phone" else ""
        steps.append(f"{scanner.lc.short(f['title'], 160)}{where}. {f['fix']}".strip())
    for name in files:
        steps.append(f"Add the attached {name}: {FILE_ABOUT.get(name, '')}.")
    steps = [s for s in steps if not any(r in s for r in ("4/8 px", "font sizes", "distinct colours", "near-duplicate"))]
    return steps


def prompt_text(report: dict, steps: list) -> str:
    head = (f"My site {report['final_url']} was checked before launch. Please fix these, in order, using the attached "
            "files where a step mentions one, then tell me what you changed:")
    return head + "\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) + "\n"


def findings_text(report: dict, render: dict | None) -> str:
    out = [f"Findings for {report['final_url']} (score {report['score']}/100)", ""]
    for f in report["findings"]:
        if f["severity"] == "INFO":
            continue
        out.append(f"[{f['severity']}] {f['title']}" + (f" ({f['where']})" if f.get("where") else ""))
        out.append(f"    Fix: {f['fix']}")
    if render and render.get("findings"):
        out += ["", "Marked on the screenshots (number = box label):"]
        for f in render["findings"]:
            out.append(f"#{f.get('rank')} [{f.get('severity')}] {f.get('detail', '')} -> {f.get('fix', '')}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------------- renders
def render_sizes(url: str, budget: float = 150.0):
    """Every problem boxed at 375 / 768 / 1440 px. Returns ({width: jpg}, polish report) or ({}, None)."""
    with scanner._queue_lock:  # noqa: SLF001 - same queue as the phone render
        if scanner._queued[0] >= scanner.RENDER_QUEUE_MAX:  # noqa: SLF001
            return {}, None
        scanner._queued[0] += 1  # noqa: SLF001
    try:
        got = scanner.BROWSER.acquire(timeout=180)
    finally:
        with scanner._queue_lock:  # noqa: SLF001
            scanner._queued[0] -= 1  # noqa: SLF001
    if not got:
        return {}, None
    shots = tempfile.mkdtemp(prefix="lc-pack-")
    try:
        t0 = time.time()
        report, _err = scanner.run_vendored("polish_audit.py", [url, "--viewports", ",".join(map(str, RENDER_WIDTHS)),
                                                                 "--json", "--shots", shots, "--timeout", "25"], budget)
        sys.stderr.write(f"pack render {time.time() - t0:.1f}s engine={(report or {}).get('engine')}\n")
        out = {}
        try:
            with open(os.path.join(shots, "marks-375.json"), encoding="utf-8") as fh:
                style = json.load(fh).get("style", [])
        except (OSError, ValueError):
            style = []
        if report:
            report["style_notes"] = style
            report["findings"] = [f for f in report.get("findings", [])
                                  if f.get("check") not in ("spacing", "type-sprawl", "type-scale", "palette",
                                                            "line-length")]
        for w in RENDER_WIDTHS:
            path = ((report or {}).get("screenshots") or {}).get(str(w))
            if path and os.path.isfile(path) and os.path.getsize(path) < 2_500_000:
                with open(path, "rb") as fh:
                    out[w] = fh.read()
        return out, (report if report and report.get("engine") == "playwright" else None)
    finally:
        scanner.BROWSER.release()
        shutil.rmtree(shots, ignore_errors=True)


def deep_crawl(report: dict) -> dict:
    """Paid pack: crawl up to 20 pages and regenerate the files from that crawl."""
    if report.get("pack_url") and report.get("pack_url") != report.get("final_url"):
        return report  # the hosted demo page: its files already come from the stand-alone audit
    emit = tempfile.mkdtemp(prefix="lc-deep-")
    try:
        geo, _err = scanner.run_vendored("geo_audit.py", [report["final_url"], "--json", "--max-pages", "20", "--delay",
                                                          "0.2", "--link-budget", "10", "--timeout", "8",
                                                          "--emit-fixes", emit, "--force"], 90)
        if not geo:
            return report
        return {**report, "fix_files": scanner.read_fix_files(emit) or report.get("fix_files", {}),
                "crawl_pages": [{k: p.get(k) for k in ("url", "final_url", "status", "title", "description", "noindex")}
                                for p in geo.get("pages", [])][:20]}
    finally:
        shutil.rmtree(emit, ignore_errors=True)


def build_pack(report: dict, rescan_url: str) -> dict:
    report = deep_crawl(report)
    files = pack_files(report)
    steps = full_steps(report, files)
    shots, render = render_sizes(report["final_url"])
    host = (urlparse.urlsplit(report["final_url"]).hostname or "site").replace(":", "-")
    listing = "\n".join(f"  {n:<15}{FILE_ABOUT.get(n, '')}" for n in files)
    readme = README.format(url=(report.get("pack_url") or report["final_url"]), date=time.strftime("%Y-%m-%d"), score=report["score"],
                           listing=listing, rescan=rescan_url)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme)
        z.writestr("FIX-PROMPT.txt", prompt_text(report, steps))
        z.writestr("FINDINGS.txt", findings_text(report, render))
        for name, text in files.items():
            z.writestr(name, text)
        for w, jpg in shots.items():
            z.writestr("screenshots/" + SHOT_NAMES[w], jpg)
    return {"state": "done", "files": files, "steps": steps, "shots": shots, "report": report,
            "style": (render or {}).get("style_notes", []),
            "render": render, "zip": buf.getvalue(), "zip_name": f"fix-pack-{host}.zip", "built": time.time(),
            "zip_listing": sorted(["README.txt", "FIX-PROMPT.txt", "FINDINGS.txt"] + list(files)
                                  + ["screenshots/" + SHOT_NAMES[w] for w in shots])}


# ------------------------------------------------------------------------------------------------ Polar
def _polar_base() -> str:
    return "https://sandbox-api.polar.sh" if POLAR_SERVER == "sandbox" else "https://api.polar.sh"


def _polar(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(_polar_base() + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + POLAR_TOKEN, "Accept": "application/json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - fixed https host
        return json.loads(resp.read(1_000_000))


def polar_checkout_url(scan_id: str, base_url: str) -> str:
    data = _polar("POST", "/v1/checkouts/", {
        "products": [POLAR_PRODUCT],
        "success_url": f"{base_url}/fix/{scan_id}?checkout_id={{CHECKOUT_ID}}",
        "metadata": {"scan_id": scan_id}})
    return data["url"]


def polar_checkout_paid(checkout_id: str, scan_id: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", checkout_id or ""):
        return False
    data = _polar("GET", f"/v1/checkouts/{checkout_id}")
    return data.get("status") == "succeeded" and (data.get("metadata") or {}).get("scan_id") == scan_id


def polar_webhook_ok(headers, body: bytes) -> bool:
    """Standard Webhooks signature (Polar): HMAC-SHA256 over '<id>.<timestamp>.<body>'."""
    if not POLAR_WEBHOOK_SECRET:
        return False
    wid, ts, sig = (headers.get("webhook-id") or ""), (headers.get("webhook-timestamp") or ""), (
        headers.get("webhook-signature") or "")
    if not (wid and ts and sig) or not ts.isdigit() or abs(time.time() - int(ts)) > 600:
        return False
    secret = POLAR_WEBHOOK_SECRET
    key = base64.b64decode(secret[6:]) if secret.startswith("whsec_") else secret.encode()
    expected = base64.b64encode(hmac.new(key, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, part.split(",", 1)[-1]) for part in sig.split())


# ------------------------------------------------------------------------------------------ page pieces
def _blur_lines(n: int, widths=(34, 52, 41, 60, 27, 48, 56, 38)) -> str:
    return "\n".join("▆" * widths[i % len(widths)] for i in range(n))


def locked_preview(report: dict, job_id: str, phone_more: int | None, base_url: str, demo: bool) -> str:
    """The free page's view of the paid pack: real first lines, the rest blurred (and NOT in the HTML)."""
    files = pack_files(report)
    steps = full_steps(report, files)
    host = urlparse.urlsplit(report["final_url"]).hostname or report["final_url"]
    cards = []
    for name, text in files.items():
        lines = text.splitlines()
        shown = "\n".join(lines[:3])
        hidden = max(0, len(lines) - 3)
        cards.append(f'<div class="fp-file"><div class="fp-fn">{esc(name)} <span>{esc(FILE_ABOUT.get(name, ""))}</span>'
                     f'</div><pre>{esc(shown)}</pre>'
                     + (f'<pre class="fp-blur" aria-hidden="true">{_blur_lines(min(hidden, 5))}</pre>'
                        f'<div class="fp-more">+{hidden} more line{"s" if hidden != 1 else ""}</div>' if hidden else "")
                     + "</div>")
    spots = (f"{phone_more} more spot{'s' if phone_more != 1 else ''} marked"
             if phone_more else "every problem marked")
    thumbs = ('<div class="fp-thumbs" aria-hidden="true"><div class="fp-t fp-ph"></div><div class="fp-t fp-tab"></div>'
              f'<div class="fp-t fp-desk"></div></div><div class="fp-tl">Phone · tablet · desktop renders, {esc(spots)}</div>')
    first = esc(steps[0]) if steps else ""
    prompt = (f'<div class="fp-prompt"><div class="fp-fn">The full fix prompt for Lovable, Bolt, Cursor or Claude Code '
              f'<span>({len(steps)} steps)</span></div><pre>1. {first}</pre>'
              + (f'<pre class="fp-blur" aria-hidden="true">{_blur_lines(min(len(steps) - 1, 5))}</pre>'
                 if len(steps) > 1 else "") + "</div>")
    if CHECKOUT_ON:
        label = (f"Unlock all {len(steps)} fix steps" if len(steps) > 1 else "Get my Fix Pack")
        button = (f'<form method="post" action="/buy/{esc(job_id)}"><button class="fp-buy" type="submit">{label} '
                  f'— {PRICE} <s>{PRICE_WAS}</s></button></form>'
                  '<div class="fp-terms">One-time · instant download · re-scan free for 7 days · refund if it doesn\'t '
                  'help</div>')
    else:
        button = ('<a class="fp-buy" href="/fix/demo">See a sample Fix Pack</a>'
                  f'<div class="fp-terms">Checkout opens soon · launch price {PRICE} <s>{PRICE_WAS}</s> · one-time · '
                  'instant download · re-scan free for 7 days · refund if it doesn\'t help</div>')
    return (f'<section class="fp"><h2>Your Fix Pack for {esc(host)}</h2>'
            f'<p class="fp-sub">Generated for your site: the files, the renders and the prompt. Locked until checkout.</p>'
            f'<div class="fp-files">{"".join(cards)}</div>{thumbs}{prompt}<div class="fp-cta">{proof_line()}{button}</div></section>')


FP_CSS = """
.fp{background:#fff;border:2px solid #0f172a;border-radius:22px;padding:22px 24px;margin:0 0 14px}
.fp h2{font-size:24px;margin:0 0 4px;letter-spacing:-.015em}.fp-sub{color:#475569;margin:0 0 14px;font-size:14px}
.fp-files{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.fp-file,.fp-prompt{border:1px solid #e2e8f0;border-radius:12px;padding:10px 12px;overflow:hidden;background:#f8fafc}
.fp-fn{font-weight:800;font-size:14px;margin-bottom:6px}.fp-fn span{font-weight:500;color:#64748b;font-size:12.5px}
.fp pre{margin:0;font:12px/1.5 Consolas,"Cascadia Mono",monospace;white-space:pre-wrap;overflow-wrap:anywhere;color:#0f172a;
max-height:none}
.fp-blur{filter:blur(4px);color:#94a3b8!important;user-select:none;pointer-events:none}
.fp-more{font-size:12px;color:#64748b;margin-top:4px}
.fp-thumbs{display:flex;gap:12px;align-items:flex-end;margin:16px 0 4px}
.fp-t{border:6px solid #0f172a;border-radius:12px;filter:blur(2px);background:repeating-linear-gradient(180deg,#e2e8f0 0 8px,
#fff 8px 18px,#fecaca 18px 22px,#fff 22px 34px)}
.fp-ph{width:58px;height:110px}.fp-tab{width:110px;height:140px}.fp-desk{width:220px;height:130px}
.fp-tl{font-size:14px;font-weight:700;color:#c2410c;margin-bottom:12px}
.fp-prompt{margin-bottom:16px}
.fp-cta{text-align:center}
.fp-proof{font-size:14px;color:#14532d;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:8px 12px;margin:0 0 12px}
.fp-buy{display:inline-block;background:#16a34a;color:#fff;border:0;border-radius:14px;padding:16px 28px;font:inherit;
font-size:20px;font-weight:800;text-decoration:none;cursor:pointer}.fp-buy:hover{background:#15803d}
.fp-buy s{opacity:.7;font-weight:600;margin-left:6px}
.fp-terms{color:#475569;font-size:13px;margin-top:8px}.fp-terms s{opacity:.8}
@media (max-width:700px){.fp-files{grid-template-columns:1fr}.fp{padding:16px}.fp-desk{width:140px}.fp-buy{display:block}}
"""

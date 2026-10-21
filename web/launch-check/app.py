"""Launch Check web: paste your URL, get the report card. Python standard library only.

Run: PORT=8000 python app.py   (binds 0.0.0.0:$PORT)
Routes: GET / (?v=key|stat|supabase headline variants), POST /check (starts a scan, 303 to /r/<id>),
GET /r/<id> (progress page, then the report), GET /r/<id>/status (JSON), GET /healthz, /robots.txt, /sitemap.xml,
/llms.txt, /favicon.svg, /og.png.
Nothing is written anywhere: a finished report is kept in memory for 10 minutes so its page can load, then dropped.
Logs carry the method, path (never the query string), status and the target HOST only.
"""

from __future__ import annotations

import html
import json
import os
import re
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import parse as urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scan as scanner  # noqa: E402  (installs the SSRF guard)

SITE = os.environ.get("PUBLIC_URL", "https://launch-check-upz2.onrender.com").rstrip("/")
GITHUB = "https://github.com/Ces107/agent-forge/tree/main/skills/launch-check"
KIT_WEB = "https://www.agensi.io/bundles/web-design-seo-toolkit-pro-sites-with-ai?utm_source=launchcheck"
KIT_GUARD = "https://www.agensi.io/bundles/claude-code-guardrails-safe-honest-agent-kit?utm_source=launchcheck"
KIT_BASE = "https://www.agensi.io/bundles/web-design-seo-toolkit-pro-sites-with-ai"
GO = {  # outbound links go through /go/<name> so clicks can be counted (no cookies, no IPs, no scanned URLs)
    "kit": KIT_BASE + "?utm_source=launchcheck&utm_medium=result&utm_content=offer",
    "kit-fixlist": KIT_BASE + "?utm_source=launchcheck&utm_medium=result&utm_content=fixlist",
    "skill": GITHUB,
    "single-design": "https://www.agensi.io/skills/website-design-auditor-make-ai-built-sites-look-pro"
                     "?utm_source=launchcheck&utm_medium=result&utm_content=single",
    "single-geo": "https://www.agensi.io/skills/geo-seo-audit-crawler-get-found-by-ai-search"
                  "?utm_source=launchcheck&utm_medium=result&utm_content=single",
}
KIT_PRICE = "$39"
STATS_TOKEN = os.environ.get("STATS_TOKEN", "")
_stats_lock = threading.Lock()
STATS = {"since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "totals": defaultdict(int),
         "by": defaultdict(lambda: defaultdict(int))}
RATE_MAX, RATE_WINDOW = 10, 600         # scans per IP per 10 minutes
CONCURRENCY = threading.BoundedSemaphore(2)
QUEUE_WAIT = 40                         # seconds a scan may wait for a free scanner
JOB_TTL = 600                           # finished reports live in memory this long
JOB_MAX = 300
STUCK_AFTER = scanner.BUDGET + 40       # a job still running after this is reported as failed
MAX_BODY = 4096
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()
JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()
JOB_ID = re.compile(r"[A-Za-z0-9_-]{20,40}")

VARIANTS = {
    "launch": {"h1": "See your site on a phone, problems marked. Free.", "source": "",
               "sub": "Your page in a real phone browser with the top 3 problems boxed, plus what Google & ChatGPT "
                      "can't read. Under a minute, no signup.",
               "button": "See my site on a phone", "example_img": True,
               "ex": "", "fix": ""},
    "dev": {"h1": "Built your site with Claude Code or Cursor? See what's still broken.", "source": "",
            "sub": "Free scan in under 20 s: phone layout, what Google & ChatGPT can't read, keys in your code. "
                   "Then your AI fixes it.",
            "sev": "MEDIUM", "ex": "Your page is wider than a phone screen, and Google sees no description",
            "fix": "Fix: remove fixed widths wider than the screen and add a 120-160 character meta description."},
    "key": {"h1": "Is a secret key sitting in your site's code?", "source": "",
            "ex": "Anyone can see your Stripe secret key in your website's code",
            "fix": "Fix: move it to a server-side environment variable, then roll the key in Stripe."},
    "stat": {"h1": "1 in 5 AI-built sites leaks a secret key. Is yours one?",
             "source": "Red Hunt Labs, 2025: ~26,000 of ~130,000 vibe-coded sites",
             "ex": "Your OpenAI API key is visible in your website's code: anyone can run up your bill",
             "fix": "Fix: call OpenAI from a server function, keep the key in an environment variable, then rotate it."},
    "supabase": {"h1": "Is your Supabase key showing in your site's code?",
                 "source": "Moltbook: a Supabase key in client-side JavaScript, 1.5M API keys exposed (Wiz, Feb 2026)",
                 "ex": "Your Supabase service_role key is in your website's code: anyone can read and change your database",
                 "fix": "Fix: use the anon key in the browser, keep service_role on the server only, and rotate it."},
    "devtools": {"h1": "Press F12 on your site. Is your API key in there?", "source": "",
                 "sub": "Launch Check looks for you, usually in under 20 seconds. Free, no signup, nothing stored.",
                 "ex": "Your OpenAI API key is visible in your website's code: anyone can run up your bill",
                 "fix": "Fix: call OpenAI from a server function, keep the key in an environment variable, then rotate it."},
    "polish": {"h1": "Does your AI-built site look unfinished?", "source": "",
               "sub": "Free scan: what breaks on phones, what Google and ChatGPT can't read, and whether a key is "
                      "showing. Usually under 20 seconds.",
               "sev": "MEDIUM", "ex": "Your page is wider than a phone screen, and Google sees no description",
               "fix": "Fix: remove fixed widths wider than the screen and add a 120-160 character meta description."},
    "ready": {"h1": "Is your AI-built app ready to launch?", "source": "",
              "sub": "Free scan in under 20 seconds: secret keys, phone layout, Google & ChatGPT, speed.",
              "ex": "Your OpenAI API key is visible in your website's code: anyone can run up your bill",
              "fix": "Fix: call OpenAI from a server function, keep the key in an environment variable, then rotate it."},
    "bill": {"h1": "A leaked Gemini key cost a 3-person startup $82,314 in 48 hours.",
             "source": "Reported by the developer, via The Register, March 2026",
             "sub": "Is your key showing in your site's code? Check in under 20 seconds. Free, no signup.",
             "ex": scanner.GOOGLE_TITLE, "fix": "Fix: " + scanner.GOOGLE_FIX[0].lower() + scanner.GOOGLE_FIX[1:]},
    "gemini": {"h1": "$6,000 in 40 minutes, from one key in a site's JavaScript.",
               "source": "Developer's report on Google's AI developer forum, April 2026",
               "sub": "Is your key showing in your site's code? Check in under 20 seconds. Free, no signup.",
               "ex": scanner.GOOGLE_TITLE, "fix": "Fix: " + scanner.GOOGLE_FIX[0].lower() + scanner.GOOGLE_FIX[1:]},
}
SUB ="See the result usually in under 20 seconds. Free, no signup, nothing stored."

FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
           'fill="#0b1220"/><circle cx="32" cy="32" r="17" fill="none" stroke="#22c55e" stroke-width="7"/>'
           '<path d="M24 33l6 6 11-13" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" '
           'stroke-linejoin="round"/></svg>')

DESC = ("Free check for apps built with Lovable, Bolt, v0, Replit or Cursor: secret keys in your site's code, "
        "Supabase/Firebase config, security headers, plus Google & ChatGPT basics. Usually under 20 seconds, no signup, "
        "nothing stored.")

CSS = """
:root{--ink:#0b1220;--mut:#5b6678;--line:#e3e8ef;--bg:#f6f8fb;--card:#fff;--acc:#16a34a;--acc2:#22c55e;--red:#dc2626}
*{box-sizing:border-box}html,body{margin:0}body{background:var(--bg);color:var(--ink);
font:16px/1.5 "Inter","Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
.hero{min-height:100svh;padding:44px 16px 56px;
background:radial-gradient(1200px 600px at 50% -10%,#dcfce7 0,rgba(220,252,231,0) 60%),var(--bg)}
.in{width:100%;max-width:760px;margin:0 auto}
.brand{font-weight:800;font-size:17px;letter-spacing:-.01em;margin-bottom:26px;display:flex;align-items:center;gap:8px}
.brand i{font-style:normal;color:var(--acc)}.brand img{width:26px;height:26px}
h1{font-size:clamp(34px,7vw,60px);line-height:1.04;letter-spacing:-.033em;margin:0 0 22px;font-weight:850}
form{display:flex;gap:10px;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:18px;
padding:10px;box-shadow:0 1px 2px rgba(11,18,32,.05),0 18px 50px rgba(11,18,32,.08)}
input[type=text]{flex:1 1 260px;min-width:0;border:0;outline:0;font:inherit;font-size:18px;
padding:14px 12px;background:transparent;color:var(--ink)}
button{flex:0 0 auto;border:0;border-radius:12px;background:var(--ink);color:#fff;font:inherit;font-weight:750;
font-size:17px;padding:14px 22px;cursor:pointer}button:hover{background:#1e293b}
button[disabled]{opacity:.75;cursor:wait}
.sub{color:#1e293b;margin-top:14px;font-size:16px;font-weight:600}
.trust{color:#334155;font-size:15px;margin-top:6px}.trust a{color:#334155;font-weight:650}
.src{color:#475569;font-size:13px;margin-top:8px}
.biz{color:#475569;font-size:13px;margin-top:4px}
.ex{margin-top:22px;background:var(--card);border:1px solid var(--line);border-left:5px solid var(--red);
border-radius:14px;padding:12px 16px}
.exl{font-size:12px;font-weight:750;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin-bottom:4px}
.exr{display:flex;gap:10px;align-items:flex-start;font-weight:700;font-size:16px}
.tag{flex:none;font-size:11px;font-weight:800;border-radius:6px;padding:3px 7px;background:#fee2e2;color:#b91c1c;margin-top:2px}
.ex.amber{border-left-color:#d97706}.ex.amber .tag{background:#fef3c7;color:#b45309}
.exf{color:#334155;font-size:14px;margin-top:4px;padding-left:52px}
.exph{margin-top:22px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.exphr{display:flex;gap:18px;align-items:flex-start}.exphr img{width:190px;height:auto;border:7px solid #0b1220;
border-radius:24px;display:block;flex:none}
.exphr ol{list-style:none;margin:6px 0 0;padding:0;font-size:16px;font-weight:650;color:#1e293b}
.exphr li{margin:0 0 10px}.exphr li b{background:#dc2626;color:#fff;border-radius:5px;padding:1px 6px;font-size:13px;
margin-right:6px}.also{color:#475569;font-size:13px;margin-top:10px}
@media (max-width:520px){.exphr img{width:150px}.exphr ol{font-size:14px}}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px;align-items:center}
.chip{background:var(--card);border:1px solid var(--line);border-radius:99px;padding:6px 12px;font-size:13px;
font-weight:650;color:#334155;display:flex;align-items:center;gap:7px}
.chip b{width:9px;height:9px;border-radius:50%;display:inline-block}
.more{font-size:13px;font-weight:650;color:var(--mut);padding:6px 4px}
.err{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;border-radius:14px;padding:12px 16px;
margin:0 0 16px;font-weight:600}
.bar{height:6px;border-radius:6px;background:var(--line);overflow:hidden;margin-top:14px;display:none}
.bar i{display:block;height:100%;width:0;background:var(--acc2);transition:width 20s cubic-bezier(.2,.6,.4,1)}
.busy .bar{display:block}.busy .bar i{width:94%}
.prog{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px 22px 18px;
box-shadow:0 18px 50px rgba(11,18,32,.08)}
.prog b{font-size:20px}.prog .bar{display:block}.prog .bar i{width:94%;animation:fill 20s cubic-bezier(.2,.6,.4,1)}
@keyframes fill{from{width:4%}to{width:94%}}.prog p{color:var(--mut);margin:10px 0 0}
@media (max-width:520px){.hero{padding-top:26px;min-height:auto}button{width:100%}form{padding:8px}
input[type=text]{font-size:17px}.exf{padding-left:0}}
"""

FUNNEL_CSS = """
.lc-top{max-width:1080px;margin:0 auto;padding:16px 28px 0;display:flex;justify-content:flex-end}
.lc-top a{color:#0f172a;font-weight:700;text-decoration:none;border:1px solid #e2e8f0;background:#fff;
border-radius:10px;padding:8px 14px;font-size:14px}
.lc-mid{background:#fef3c7;border:1px solid #fcd34d;color:#78350f}
.lc-ok,.lc-bad,.lc-mid{display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-radius:16px;padding:14px 18px;margin:16px 0 0;
font-size:17px;font-weight:750}
.lc-ok{background:#dcfce7;border:1px solid #86efac;color:#14532d}.lc-bad{background:#fee2e2;border:1px solid #fca5a5;color:#7f1d1d}
.lc-ok small,.lc-bad small,.lc-mid small{font-weight:600;font-size:14px;opacity:.85}
.lc-next{max-width:1080px;margin:0 auto;padding:0 28px 48px}
.lc-next h2{font-size:17px;margin:8px 0 10px;color:#334155}
.lc-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.lc-cards a{display:block;background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:14px 16px;
color:#0f172a;text-decoration:none;font-size:14px}
.lc-cards a:hover{border-color:#94a3b8}.lc-cards b{display:block;font-size:15px;margin-bottom:3px}
.lc-cards span{color:#64748b}.lc-cards em{font-style:normal;color:#16a34a;font-weight:700}
.ask button{max-width:calc(100% - 28px)}
@media (max-width:820px){.lc-cards{grid-template-columns:1fr}.lc-top,.lc-next{padding-left:16px;padding-right:16px}
.wrap{padding:20px 16px 32px!important}.ask{padding-top:58px!important}.grid{grid-template-columns:1fr 1fr!important}}
@media (max-width:420px){.verdict{font-size:26px!important}.num{font-size:32px!important}}
"""

KEY_CSS = """
.lc-key{background:#fff;border:1px solid #e2e8f0;border-radius:22px;padding:24px 28px;margin:0 0 14px;
box-shadow:0 1px 2px rgba(15,23,42,.04),0 12px 32px rgba(15,23,42,.06)}
.lc-key h1{font-size:14px;text-transform:uppercase;letter-spacing:.07em;color:#64748b;margin:0 0 14px}
.lc-ans{display:flex;gap:18px;align-items:center}
.lc-ico{flex:none;width:76px;height:76px;border-radius:50%;display:grid;place-items:center;font-size:42px;font-weight:900;color:#fff}
.k-ok .lc-ico{background:#16a34a}.k-bad .lc-ico{background:#dc2626}.k-mid .lc-ico{background:#d97706}
.lc-ans b{display:block;font-size:30px;line-height:1.12;letter-spacing:-.02em}
.k-ok .lc-ans b{color:#14532d}.k-bad .lc-ans b{color:#991b1b}.k-mid .lc-ans b{color:#92400e}
.lc-scope{color:#475569;font-size:15px;margin-top:6px}
.lc-checked{color:#334155;font-size:14px;margin-top:16px;overflow-wrap:anywhere}.lc-checked b{font-weight:700}
.lc-keys{list-style:none;padding:0;margin:16px 0 0;display:grid;gap:10px}
.lc-keys li{border:1px solid #e2e8f0;border-left:5px solid #dc2626;border-radius:14px;padding:12px 16px}
.lc-keys li.m{border-left-color:#d97706}.lc-keys b{font-size:16px}
.lc-keys small{display:block;color:#64748b;font-family:Consolas,monospace;font-size:12px;margin-top:2px;overflow-wrap:anywhere}
.lc-keys p{margin:6px 0 0;color:#334155;font-size:14px}
details.lc-other{background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:0;margin:14px 0 0}
details.lc-other>summary{padding:16px 20px;font-size:18px;font-weight:800;cursor:pointer}
details.lc-other>summary span{font-weight:600;color:#64748b;font-size:14px;margin-left:6px}
details.lc-other[open]>summary{border-bottom:1px solid #e2e8f0}.lc-oin{padding:16px 20px 20px}
@media (max-width:600px){.lc-key{padding:18px 16px}.lc-ans b{font-size:22px}.lc-ico{width:56px;height:56px;font-size:30px}
.lc-oin{padding:12px}}
"""

OFFER_CSS = """
.lc-offer{background:#fff;border:2px solid #16a34a;border-radius:22px;padding:22px 26px;margin:0 0 14px;
box-shadow:0 12px 32px rgba(22,163,74,.10)}
.lc-offer h2{font-size:24px;line-height:1.2;letter-spacing:-.015em;margin:0 0 12px;color:#0f172a}
.lc-offer ul{margin:0 0 16px;padding-left:20px;color:#334155;font-size:15px}.lc-offer li{margin:4px 0}
.lc-buy{display:inline-block;background:#16a34a;color:#fff;font-weight:800;font-size:17px;text-decoration:none;
border-radius:12px;padding:13px 20px}.lc-buy:hover{background:#15803d}
.lc-offer small{display:block;color:#475569;font-size:13px;margin-top:10px}
.lc-cats{color:#475569;font-size:14px;font-weight:600;margin:-4px 0 12px}
.lc-choices{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.lc-phone{background:#fff;border:1px solid #e2e8f0;border-radius:22px;padding:20px 24px;margin:0 0 14px}
.lc-phone h2{font-size:20px;margin:0 0 12px}.lc-ph img[hidden]{display:none!important}.lc-ph{display:grid;grid-template-columns:300px 1fr;gap:20px;align-items:start}
.lc-ph img{width:300px;max-height:620px;object-fit:cover;object-position:top;border:8px solid #0f172a;border-radius:26px;
display:block;background:#fff}
.lc-ph ul{margin:0;padding-left:18px;color:#334155;font-size:14px}.lc-ph li{margin:5px 0}.lc-ph li b{color:#c2410c}
.lc-wait{width:300px;height:460px;border:8px solid #0f172a;border-radius:26px;display:grid;place-items:center;
color:#64748b;font-size:14px;background:repeating-linear-gradient(45deg,#f8fafc,#f8fafc 10px,#f1f5f9 10px,#f1f5f9 20px)}
.lc-more{margin:6px 0 10px;font-weight:700;color:#c2410c;font-size:15px}
.lc-phn{margin:0 0 8px;color:#475569;font-size:14px}.lc-phs{font-size:15px;color:#334155;padding:14px 20px}
.lc-compare{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:10px 14px;color:#14532d;font-size:14px;
margin:0 0 12px}
@media (max-width:700px){.lc-ph{grid-template-columns:1fr}.lc-ph img{width:100%;max-width:300px;margin:0 auto}}
.lc-runs{margin:6px 2px 0;color:#475569;font-size:12.5px;line-height:1.4}
.lc-choice{display:block;border-radius:14px;padding:14px 16px;text-decoration:none;font-size:14px}
.lc-choice b{display:block;font-size:17px;margin-bottom:2px}.lc-choice span{opacity:.9}
.lc-choice.p{background:#16a34a;color:#fff}.lc-choice.p:hover{background:#15803d}
.lc-choice.s{background:#fff;color:#0f172a;border:2px solid #cbd5e1}.lc-choice.s:hover{border-color:#64748b}
@media (max-width:700px){.lc-choices{grid-template-columns:1fr}}
.lc-congrats{border:1px solid #bbf7d0;box-shadow:none;padding:16px 20px;font-size:16px}
.lc-congrats a,.lc-free a{color:#15803d;font-weight:700}
.lc-free{max-width:1080px;margin:0 auto;padding:0 28px 40px;color:#334155;font-size:15px}
@media (max-width:600px){.lc-offer{padding:18px 16px}.lc-offer h2{font-size:20px}.lc-buy{display:block;text-align:center}
.lc-free{padding:0 16px 32px}}
"""


def esc(s):
    return html.escape(str(s), quote=True)


def clean_src(raw):
    raw = (raw or "").strip().lower()[:32]
    return raw if re.fullmatch(r"[a-z0-9_.-]{1,32}", raw) else ""


def count(event, v="dev", src=""):
    """Aggregate counters only: event name, landing variant, utm_source. Logged as one line for durability."""
    dim = f"v={variant(v)},src={clean_src(src) or '-'}"
    with _stats_lock:
        STATS["totals"][event] += 1
        STATS["by"][event][dim] += 1
    sys.stderr.write(f"stat {event} {dim}\n")


def stats_json():
    with _stats_lock:
        return json.dumps({"since": STATS["since"], "totals": dict(STATS["totals"]),
                           "by": {k: dict(v) for k, v in STATS["by"].items()}}, indent=1, sort_keys=True)


ALIASES = {}  # attribution tags that reuse another variant's copy, e.g. {"launch2": "launch"}


def variant(v):
    """The TAG kept for tracking (form, result page, /go/ links, stats). Aliases stay as typed."""
    return v if v in VARIANTS or v in ALIASES else "dev"


def copy_for(v):
    """The landing COPY for a tag (aliases resolved)."""
    v = variant(v)
    return VARIANTS[ALIASES.get(v, v)]


def shell(inner, nonce, v="dev", extra_head="", script=""):
    h1 = copy_for(v)["h1"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(h1)} | Launch Check</title><meta name="description" content="{esc(DESC)}">
<link rel="canonical" href="{SITE}/"><link rel="icon" href="/favicon.svg" type="image/svg+xml">
<meta property="og:title" content="{esc(h1)}"><meta property="og:description" content="{esc(DESC)}">
<meta property="og:type" content="website"><meta property="og:url" content="{SITE}/">
<meta property="og:image" content="{SITE}/og.png"><meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json" nonce="{nonce}">{{"@context":"https://schema.org","@type":"WebApplication","name":"Launch Check","url":"{SITE}/","applicationCategory":"DeveloperApplication","offers":{{"@type":"Offer","price":"0"}},"description":"{esc(DESC)}"}}</script>
{extra_head}<style>{CSS}</style></head><body>
<main class="hero"><div class="in">
<div class="brand"><img src="/favicon.svg" alt="" width="26" height="26"><span>Launch<i>Check</i></span></div>
{inner}
</div></main>{script}</body></html>"""


def page(nonce, v="dev", url_value="", error="", src=""):
    v = variant(v)
    d = copy_for(v)
    err = f'<div class="err" role="alert">{esc(error)}</div>' if error else ""
    src_html = f'<div class="src">Source: {esc(d["source"])}</div>' if d["source"] else ""
    if d.get("example_img"):
        example = ('<div class="exph"><div class="exl">Example result</div><div class="exphr">'
                   '<img src="/example-phone.jpg" width="190" height="547" alt="An AI-built landing page at 375 px '
                   'with its top 3 problems boxed: text under a badge, a cut-off button, low-contrast text">'
                   '<ol><li><b>#1</b> Text hidden under a badge</li><li><b>#2</b> Button text cut off</li>'
                   '<li><b>#3</b> Grey text too faint to read</li></ol></div></div>'
                   '<div class="also">Also checks: leaked API keys · Supabase/Firebase keys · security headers</div>')
    else:
        example = (f'<div class="ex{" amber" if d.get("sev") == "MEDIUM" else ""}"><div class="exl">Example result</div>'
                   f'<div class="exr"><span class="tag">{d.get("sev", "HIGH")}</span><span>{esc(d["ex"])}</span></div>'
                   f'<div class="exf">{esc(d["fix"])}</div></div>'
                   '<div class="chips"><span class="chip"><b style="background:#dc2626"></b>Leaked API keys</span>'
                   '<span class="chip"><b style="background:#d97706"></b>Supabase/Firebase keys</span>'
                   '<span class="chip"><b style="background:#2563eb"></b>Security headers</span>'
                   '<span class="more">+ Google &amp; ChatGPT basics</span></div>')
    inner = f"""<h1>{esc(d["h1"])}</h1>
{err}<form method="post" action="/check" id="f">
<input type="text" name="url" inputmode="url" autocomplete="url" spellcheck="false" autocapitalize="off" required
 placeholder="https://your-app.com" value="{esc(url_value)}" aria-label="Your app's address" maxlength="2000">
<input type="hidden" name="v" value="{v}"><input type="hidden" name="src" value="{esc(clean_src(src))}">
<button type="submit" id="b">{esc(d.get("button", "Check my app — free"))}</button>
</form>
<div class="bar" aria-hidden="true"><i></i></div>
<div class="sub">{esc(d.get("sub", SUB))}</div>
<div class="trust">Reads only what any visitor's browser downloads · <a href="{GITHUB}">open source</a> · nothing stored · not a penetration test</div>
<div class="biz">Free scan. Optional fix kits on Agensi.</div>
{src_html}
{example}"""
    script = f"""<script nonce="{nonce}">
document.getElementById('f').addEventListener('submit',function(){{var b=document.getElementById('b');
document.body.classList.add('busy');setTimeout(function(){{b.disabled=true;b.textContent='Checking… usually 10–20 s';}},0);}});
</script>"""
    return shell(inner, nonce, v, script=script)


def progress_page(job_id, job, nonce):
    host = esc(job.get("host", "your site"))
    waiting = job["state"] == "queued"
    inner = f"""<div class="prog" role="status"><b>{'Waiting for a free scanner…' if waiting else f'Checking {host}…'}</b>
<div class="bar" aria-hidden="true"><i></i></div>
<p>Usually 10–20 s. This page updates by itself.</p></div>"""
    head = f'<noscript><meta http-equiv="refresh" content="3"></noscript><meta name="robots" content="noindex">'
    script = f"""<script nonce="{nonce}">(function(){{var u='/r/{job_id}/status';
function poll(){{fetch(u,{{cache:'no-store'}}).then(function(r){{return r.json();}}).then(function(d){{
if(d.state==='done'||d.state==='error'||d.state==='gone'){{location.reload();}}else{{setTimeout(poll,1000);}}
}}).catch(function(){{setTimeout(poll,2000);}});}}setTimeout(poll,1200);}})();</script>"""
    return shell(inner, nonce, job.get("v", "dev"), extra_head=head, script=script)


KEY_RULES = ("supabase-found", "firebase-found")


def key_items(report):
    return [f for f in report["findings"] if scanner.is_key_item(f)]


def secret_banner(report):
    """The first section of the result: is a secret key showing?"""
    items = key_items(report)
    assets = report["facts"].get("assets") or {}
    n, off = assets.get("scanned", 0), assets.get("skipped_offsite", 0)
    red = [f for f in items if f["severity"] == "HIGH" and f["rule"].startswith("secret-")]
    s = lambda k: "s" if k != 1 else ""  # noqa: E731
    if red:
        cls, ico, head = "k-bad", "✗", f"Yes: {len(red)} secret key{s(len(red))} showing in your site's code"
    elif items:
        cls, ico, head = "k-mid", "!", f"No secret keys, but {len(items)} key{s(len(items))} to lock down"
    elif n == 0 and off:
        cls, ico, head = "k-mid", "?", "No secret keys in your page HTML"
    else:
        cls, ico, head = "k-ok", "✓", "No secret keys found in your site's code"
    scope = f"page HTML + {n} script{s(n)} checked"
    if off:
        scope += f" · {off} third-party script{s(off)} (analytics, widgets) not checked"
    scope += f" · {report.get('seconds', 0):.0f} s"
    entered = report.get("url", "")
    moved = (f' <span>(you entered {esc(entered)})</span>'
             if entered and entered.rstrip("/") != report["final_url"].rstrip("/") else "")
    cards = []
    for f in items:
        meta = " · ".join(esc(x) for x in (f["where"], f["value"]) if x)
        cards.append(f'<li class="{"" if f["severity"] == "HIGH" else "m"}"><b>{esc(f["title"])}</b>'
                     f'{"<small>" + meta + "</small>" if meta else ""}<p>{esc(f["fix"])}</p></li>')
    listing = '<ul class="lc-keys">' + "".join(cards) + "</ul>" if cards else ""
    return (f'<section class="lc-key {cls}"><h1>Is a secret key showing?</h1>'
            f'<div class="lc-ans"><div class="lc-ico" aria-hidden="true">{ico}</div><div><b>{esc(head)}</b>'
            f'<div class="lc-scope">{esc(scope)}</div></div></div>{listing}'
            f'<div class="lc-checked"><b>Checked:</b> {esc(report["final_url"])}{moved}</div></section>')


SINGLES = {  # live Agensi prices, checked 2026-10-21 ("price":19 on each skill page, 39 on the bundle)
    "phone": ("single-design", "Let my AI fix the phone layout — $19", "Website Design Auditor"),
    "search": ("single-geo", "Let my AI fix Google & ChatGPT visibility — $19",
               "GEO + SEO Audit: also writes your sitemap, JSON-LD and llms.txt"),
}
COMPARE = ("What the full skill adds: on news.ycombinator.com this basic check (page code, 1 screen size) flags 10 "
           "spots; the full skill found 18 there in its published benchmark (score 64/100), rendering phone, tablet "
           "and desktop with every problem marked on a screenshot, and it re-scores after each fix.")
RUNS = ("Runs inside Claude Code, Cursor or Codex: your AI renders the page, fixes each issue and re-scores until "
        "it passes.")
CAT_LABEL = {"phone": "Phone layout", "search": "Google & ChatGPT", "speed": "Speed & basics",
             "security": "Security headers"}


def other_items(report):
    """THE things-to-fix list (HIGH + MEDIUM, keys excluded), phone and Google/ChatGPT first."""
    return scanner.things_to_fix(report["findings"])


def go(name, v, src):
    return f"/go/{name}?" + urlparse.urlencode({"v": variant(v), "src": clean_src(src)})


def offer_block(report, v="dev", src="", phone=None):
    fix = other_items(report)
    n = len(fix)
    if not any(f["area"] in ("phone", "search", "speed") for f in fix):
        rest = f" The free fix list below covers the {n} security-header item{'s' if n != 1 else ''}." if n else ""
        return ('<section class="lc-offer lc-congrats"><b>Nothing to fix in design or search:</b> no phone-layout, '
                f'Google or speed problems worth fixing before launch.{rest}</section>')
    search_ran = (report.get("areas", {}).get("search") or {}).get("score") is not None
    cats = [(a, sum(1 for f in fix if f["area"] == a)) for a in scanner.FIX_ORDER]
    cat_line = " · ".join(f"{CAT_LABEL[a]}: {c}" for a, c in cats if c)
    s = "s" if n != 1 else ""
    top = "".join(f"<li>{esc(scanner.lc.short(f['title'], 110))}</li>" for f in fix[:3])
    lead = next((f["area"] for f in fix if f["area"] in SINGLES), None)
    if lead == "search" and not search_ran:
        lead = None
    choices = ""
    compare = f'<p class="lc-compare">{esc(COMPARE)}</p>' if lead == "phone" else ""
    if lead:
        name, verb, product = SINGLES[lead]
        if lead == "phone" and phone and phone.get("total"):
            verb = f"Let my AI fix these {phone['total']} spots — $19"
        choices += (f'<div><a class="lc-choice p" href="{esc(go(name, v, src))}" rel="noopener"><b id="lc-b1" '
                    f'data-lead="{lead}">{esc(verb)}</b>'
                    f'<span>{esc(product)}</span></a><p class="lc-runs">{RUNS}</p></div>')
    kit_what = ("Design + Google & ChatGPT: Web + SEO kit (3 skills)" if search_ran
                else "Design + SEO: Web + SEO kit (3 skills)")
    choices += (f'<div><a class="lc-choice {"s" if lead else "p"}" href="{esc(go("kit", v, src))}" rel="noopener">'
                f'<b>Fix everything with my AI — {KIT_PRICE}</b><span>{esc(kit_what)}</span></a>'
                f'<p class="lc-runs">{RUNS}</p></div>')
    return (f'<section class="lc-offer"><h2>We found {n} thing{s} to fix before launch</h2>'
            f'<div class="lc-cats">{esc(cat_line)}</div><ul>{top}</ul>{compare}<div class="lc-choices">{choices}</div>'
            f'<small>Free: the list below says what is wrong. Paid: the skill does the fixing loop and writes the '
            f'files (sitemap, JSON-LD, llms.txt). One-time · refund if it does not do what it says (Agensi guarantee).'
            f'</small></section>')


def fix_prompt(report, v="dev", src=""):
    """Free list: WHAT is wrong (key items + top 3), one sentence each. Key items keep their fix (security is free)."""
    keys = key_items(report)
    fix = other_items(report)
    items = keys + fix[:3]
    if not items:
        return ""
    lines = [f"My site {report['final_url']} was checked before launch. Please fix these problems, in order, and "
             "tell me what you changed:"]
    for i, f in enumerate(items, 1):
        where = f" (on {f['where']})" if f["where"] and f["area"] != "phone" and f not in keys else ""
        sentence = f["title"] if f in keys else scanner.lc.short(f["title"], 140)
        extra = f" {f['fix']}" if f in keys else ""
        lines.append(f"{i}. {sentence}{where}.{extra}")
    text = "\n".join(lines)
    if text and len(fix) > 3:
        text += (f"\n\nWant your AI to fix the other {len(fix) - 3} too? Web + SEO kit: "
                 f"{SITE}{go('kit-fixlist', v, src)}")
    return text + "\n\nNot checked here: tablet and desktop renders, hover states, re-scoring after fixes."


def phone_section(report):
    pv = report.get("phone_view") or {}
    if pv.get("mode") == "render" and pv.get("image"):
        groups = {}
        for item in pv.get("legend", []):
            groups.setdefault(item["title"], []).append(item["rank"])
        legend = "".join(f'<li><b>{esc(", ".join("#%d" % r for r in ranks[:5]) + (" …" if len(ranks) > 5 else ""))}'
                         f'</b> {esc(title)}</li>' for title, ranks in groups.items())
        return (f'<section class="lc-phone"><h2>Your site on a phone</h2><div class="lc-ph">'
                f'<img alt="Your page at 375 px wide, problems marked with numbered boxes" '
                f'src="data:image/jpeg;base64,{pv["image"]}"><div><p class="lc-phn">Rendered in a real browser at 375 px '
                f'(1 screen size). Numbered boxes = problems:</p><ul>{legend or "<li>No phone problems marked.</li>"}'
                f'</ul></div></div></section>')
    if pv.get("mode") in ("static", "render"):  # render without a usable screenshot counts as basic
        reason = pv.get("reason") or "no screenshot"
        if reason == "no browser on this server":
            return ('<section class="lc-phone lc-phs"><b>Phone layout: basic check (page code, 1 screen size).</b> '
                    'This free scan reads your page code; it does not render it. The full skill renders your page in a '
                    'real browser at phone, tablet and desktop size and marks each problem on a screenshot.</section>')
        return (f'<section class="lc-phone lc-phs"><b>Phone layout: basic check from the page code only</b> (no browser '
                f'render this time: {esc(reason)}).</section>')
    return ('<section class="lc-phone lc-phs"><b>Phone layout: not checked</b> (the check did not finish in time).'
            '</section>')


def phone_counts(stats):
    """Real counts from the render: labels that matched an element, spots boxed, spots left."""
    if not stats:
        return {}
    total, boxed = int(stats.get("total", 0)), int(stats.get("boxed", 0))
    return {"matched": stats.get("matched", []), "boxed": boxed, "more": max(0, total - boxed), "total": total}


def phone_slot(report, job_id, state, stats=None):
    """Async phone render: a slot that fills itself when the background render is done, or hides."""
    if state not in ("rendering", "done") or not job_id:
        return ""
    counts = phone_counts(stats)
    matched = set(counts.get("matched", []))
    top3 = other_items(report)[:3]
    legend = "".join(f'<li data-l="#{i + 1}"{" hidden" if state == "done" and f"#{i + 1}" not in matched else ""}>'
                     f'<b>#{i + 1}</b> {esc(scanner.lc.short(f["title"], 90))}</li>'
                     for i, f in enumerate(top3) if f["area"] == "phone")
    more = counts.get("more", 0)
    more_html = (f'<p class="lc-more" id="lc-more"{"" if more else " hidden"}>+{more} more spot{"s" if more != 1 else ""} '
                 f'on your page</p>')
    src = f' src="/r/{job_id}/phone.jpg"' if state == "done" else ""
    wait = "" if state == "done" else '<div class="lc-wait" id="lc-pw">rendering… (about 30 s)</div>'
    return (f'<section class="lc-phone" id="lc-ps" data-u="/r/{job_id}/phone" data-done="{1 if state == "done" else 0}">'
            f'<h2>Your site on a phone</h2><div class="lc-ph"><div>{wait}<img id="lc-pi" alt="Your page at 375 px wide '
            f'with the top problems boxed"{src}{"" if state == "done" else " hidden"}></div><div><p class="lc-phn">'
            f'Rendered in a real browser at 375 px. Numbered boxes match the free top-3 list:</p><ul>{legend}</ul>'
            f'{more_html}'
            f'<p class="lc-phn">The $19 skill gives your agent every box, plus tablet and desktop, and re-scores after '
            f'each fix.</p></div></div></section>')


def result_page(report, nonce, v="dev", src="", job_id=None, phone_state=None, phone_stats=None):
    fix = other_items(report)
    n = len(fix)
    # the collapsed section shows exactly the things-to-fix list: one count everywhere
    doc = scanner.render({**report, "findings": fix, "fix_first": fix})
    doc = re.sub(r'<div class="ask">.*?</pre></div>', "", doc, count=1, flags=re.S)
    prompt = fix_prompt(report, v, src)
    top_n = min(3, n)
    keys_n = len(key_items(report))
    parts = ([f"{keys_n} key item{'s' if keys_n != 1 else ''}"] if keys_n else []) + ([f"top {top_n} of {n}"] if n else [])
    title = "Free basic check (1 screen size): what is wrong" + (f" ({' + '.join(parts)})" if parts else "")
    ask = (f'<div class="ask"><div class="h">{esc(title)}</div>'
           f'<button id="lc-copy" type="button">Copy fix list for your AI</button><pre id="p">{esc(prompt)}</pre></div>'
           if prompt else "")
    keys = key_items(report)
    red = any(f["severity"] == "HIGH" and f["rule"].startswith("secret-") for f in keys)
    offer = offer_block(report, v, src, phone_counts(phone_stats) if phone_state == "done" else None)
    slot = phone_slot(report, job_id, phone_state, phone_stats)
    first = secret_banner(report) + (slot or phone_section(report)) + (ask + offer if red else offer + ask)
    summary = (f'<details class="lc-other"><summary>All {n} thing{"s" if n != 1 else ""} to fix <span>score '
               f'{report["score"]}/100 · phone, Google &amp; ChatGPT, speed, security headers</span></summary>'
               f'<div class="lc-oin">')
    script = f"""<script nonce="{nonce}">(function(){{var b=document.getElementById('lc-copy');if(!b)return;
b.addEventListener('click',function(){{var t=document.getElementById('p').innerText;
function ok(){{b.textContent='Copied — paste it to your AI';}}
if(navigator.clipboard&&navigator.clipboard.writeText){{navigator.clipboard.writeText(t).then(ok,function(){{fallback();}});}}else{{fallback();}}
function fallback(){{var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();
try{{document.execCommand('copy');ok();}}catch(e){{}}document.body.removeChild(a);}}}});}})();
(function(){{var s=document.getElementById('lc-ps');if(!s||s.getAttribute('data-done')==='1')return;
var u=s.getAttribute('data-u'),n=0;function poll(){{n++;fetch(u,{{cache:'no-store'}}).then(function(r){{return r.json();}})
.then(function(d){{if(d.state==='done'){{var i=document.getElementById('lc-pi');i.onload=function(){{i.hidden=false;
var w=document.getElementById('lc-pw');if(w)w.remove();var m=d.matched||[];
document.querySelectorAll('#lc-ps li[data-l]').forEach(function(li){{if(m.indexOf(li.getAttribute('data-l'))<0)li.hidden=true;}});
var mo=document.getElementById('lc-more');if(mo&&d.more>0){{mo.textContent='+'+d.more+' more spot'+(d.more===1?'':'s')+' on your page';mo.hidden=false;}}
var b=document.getElementById('lc-b1');if(b&&b.getAttribute('data-lead')==='phone'&&d.total>0){{b.textContent='Let my AI fix these '+d.total+' spots \u2014 $19';}}
}};i.onerror=function(){{s.remove();}};i.src=u+'.jpg';}}
else if(d.state==='rendering'&&n<40){{setTimeout(poll,2000);}}else{{s.remove();}}}}).catch(function(){{s.remove();}});}}
setTimeout(poll,3000);}})();</script>"""
    top = '<div class="lc-top"><a href="/">← Check another app</a></div>'
    doc = doc.replace("</style></head><body>", FUNNEL_CSS + KEY_CSS + OFFER_CSS + '</style><meta name="robots" '
                      'content="noindex"><link rel="icon" href="/favicon.svg" type="image/svg+xml"></head><body>' + top, 1)
    doc = doc.replace('<section class="hero">', first + summary + '<section class="hero">', 1)
    doc = doc.replace("<h2>Fix this first</h2>", f"<h2>All {n} things to fix, most important first</h2>", 1)
    doc = doc.replace("<h2>All findings</h2>", "<h2>By category</h2>", 1)
    doc = doc.replace('<div class="foot">', '</div></details><div class="foot">', 1)
    doc = doc.replace("A good score is not a security audit.</div>",
                      "A good score is not a security audit. Web version: phone findings come from the page code (1 screen size); the phone screenshot is a real "
                      "browser render of the top 3 only. No open-database test, minor (low) items hidden. Nothing was written to disk: this "
                      "report stays in memory for 10 minutes so the page can load, then it is dropped."
                      + "".join(f"<br>Note: {esc(x)}" for x in report.get("notes", [])[:4]) + "</div>", 1)
    tail = "</div></body></html>"
    if doc.endswith(tail):
        doc = doc[: -len(tail)] + "</div>" + script + "</body></html>"
    return doc


def client_ip(handler):
    for name in ("CF-Connecting-IP", "True-Client-IP"):
        v = (handler.headers.get(name) or "").strip()
        if v:
            return v[:64]
    xff = (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return (xff or handler.client_address[0])[:64]


def rate_ok(ip, now=None):
    now = now or time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_MAX:
            return False, int(RATE_WINDOW - (now - q[0])) + 1
        q.append(now)
        if len(_hits) > 50_000:  # memory bound
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW][:25_000]:
                _hits.pop(k, None)
        return True, 0


def _prune(now):
    for k in [k for k, j in JOBS.items() if now - j["t"] > JOB_TTL]:
        JOBS.pop(k, None)


def start_job(url, v, src=""):
    """Register a scan and run it in the background. Returns the job id, or None when too many are pending."""
    now = time.time()
    host = (urlparse.urlsplit(url if "://" in url else "https://" + url).hostname or "your site")[:80]
    with _jobs_lock:
        _prune(now)
        if len(JOBS) >= JOB_MAX:
            return None
        job_id = secrets.token_urlsafe(18)
        JOBS[job_id] = {"state": "queued", "t": now, "url": url, "v": variant(v), "src": clean_src(src), "host": host}

    def render_later(job, report):
        """After the result is ready: render the page at 375 px with the free top-3 phone problems boxed."""
        top3 = other_items(report)[:3]
        marks = {f["rule"][6:]: str(i + 1) for i, f in enumerate(top3) if f["area"] == "phone"}
        if not marks:
            job["phone"] = {"state": "none"}
            return
        try:
            out = scanner.render_phone(report["final_url"], marks)
        except Exception:  # never break the result page
            out = None
        if out:
            jpg, stats = out
            job["phone"] = {"state": "done", "jpg": jpg, "stats": stats}
        else:
            job["phone"] = {"state": "none"}

    def work():
        job = JOBS.get(job_id)
        if job is None:
            return
        if not CONCURRENCY.acquire(timeout=QUEUE_WAIT):
            job.update(state="error", error="Two checks are already running. Try again in a minute.")
            return
        try:
            job.update(state="running", started=time.time())
            report = scanner.scan(url)
            if scanner.RENDER_MODE == "async" and scanner.BROWSER._value > 0:  # noqa: SLF001 - skip if busy
                job["phone"] = {"state": "rendering"}
            job.update(state="done", report=report)
            if job.get("phone", {}).get("state") == "rendering":
                threading.Thread(target=render_later, args=(job, report), daemon=True).start()
            count("scans_done", job["v"], job["src"])
            if any(f["severity"] != "INFO" for f in report["findings"]):
                count("results_with_findings", job["v"], job["src"])
            sys.stderr.write(f"scan {host} score={report['score']} {report['seconds']}s\n")
        except scanner.ScanError as exc:
            job.update(state="error", error=str(exc))
            sys.stderr.write(f"scan {host} refused-or-failed\n")
        except Exception:  # never leak a stack trace to the visitor
            job.update(state="error", error="Something went wrong while checking this site. Try again, or try "
                                            "your preview address.")
            sys.stderr.write(f"scan {host} crashed\n")
        finally:
            CONCURRENCY.release()

    threading.Thread(target=work, daemon=True).start()
    return job_id


def job_state(job):
    if job["state"] in ("queued", "running") and time.time() - job.get("started", job["t"]) > STUCK_AFTER:
        job.update(state="error", error="This site took too long to check. Try again, or try a lighter page.")
    return job["state"]


class Handler(BaseHTTPRequestHandler):
    server_version = "launch-check"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # one request per connection: no stale keep-alive sockets behind the proxy

    def log_message(self, fmt, *args):  # path only: never the query string, never the body
        path = urlparse.urlsplit(self.path).path
        path = re.sub(r"^/r/[^/]+", "/r/<id>", path)[:80]
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.command} {path} {getattr(self, '_status', '-')}\n")

    def send(self, status, body, ctype="text/html; charset=utf-8", nonce=None, cache="no-store", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("Connection", "close")
        script = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header("Content-Security-Policy",
                         f"default-src 'none'; script-src {script}; style-src 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parts = urlparse.urlsplit(self.path)
        path = parts.path
        if path == "/":
            q = urlparse.parse_qs(parts.query)
            v = (q.get("v") or ["dev"])[0]
            n = secrets.token_urlsafe(16)
            return self.send(200, page(n, v, src=(q.get("utm_source") or q.get("src") or [""])[0]), nonce=n,
                             cache="no-cache")
        if path == "/healthz":
            return self.send(200, "ok", "text/plain; charset=utf-8")
        m = re.fullmatch(r"/r/([A-Za-z0-9_-]+)(/status|/phone|/phone\.jpg)?", path)
        if m and JOB_ID.fullmatch(m.group(1)):
            job = JOBS.get(m.group(1))
            if m.group(2) == "/phone":
                ph = (job or {}).get("phone") or {"state": "none"}
                if ph["state"] == "rendering" and time.time() - job["t"] > 120:
                    ph = {"state": "none"}
                return self.send(200, json.dumps({"state": ph["state"], **phone_counts(ph.get("stats"))}),
                                 "application/json")
            if m.group(2) == "/phone.jpg":
                jpg = ((job or {}).get("phone") or {}).get("jpg")
                if not jpg:
                    return self.send(404, "not found", "text/plain; charset=utf-8")
                return self.send(200, jpg, "image/jpeg", cache="private, max-age=600")
            if m.group(2):
                state = job_state(job) if job else "gone"
                return self.send(200, json.dumps({"state": state}), "application/json")
            n = secrets.token_urlsafe(16)
            if not job:
                return self.send(404, page(n, error="This result has expired (results are kept for 10 minutes). "
                                                    "Check your app again."), nonce=n)
            state = job_state(job)
            if state == "done":
                return self.send(200, result_page(job["report"], n, job["v"], job.get("src", ""), m.group(1),
                                                  (job.get("phone") or {}).get("state"),
                                                  (job.get("phone") or {}).get("stats")), nonce=n)
            if state == "error":
                return self.send(422, page(n, job["v"], url_value=job["url"], error=job.get("error", ""),
                                           src=job.get("src", "")), nonce=n)
            return self.send(200, progress_page(m.group(1), job, n), nonce=n)
        if path in ("/favicon.svg", "/favicon.ico"):
            return self.send(200, FAVICON, "image/svg+xml", cache="public, max-age=86400")
        if path == "/example-phone.jpg":
            ex = Path(__file__).resolve().parent / "static" / "example-phone.jpg"
            if ex.is_file():
                return self.send(200, ex.read_bytes(), "image/jpeg", cache="public, max-age=86400")
        if path == "/og.png":
            og = Path(__file__).resolve().parent / "static" / "og.png"
            if og.is_file():
                return self.send(200, og.read_bytes(), "image/png", cache="public, max-age=86400")
        if path == "/robots.txt":
            return self.send(200, f"User-agent: *\nAllow: /\nDisallow: /r/\nSitemap: {SITE}/sitemap.xml\n",
                             "text/plain; charset=utf-8", cache="public, max-age=3600")
        if path == "/sitemap.xml":
            return self.send(200, f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/'
                                  f'schemas/sitemap/0.9"><url><loc>{SITE}/</loc></url></urlset>', "application/xml",
                             cache="public, max-age=3600")
        if path == "/llms.txt":
            return self.send(200, f"# Launch Check\n\n> {DESC}\n\n- [Check a site]({SITE}/): paste a URL, get a 0-100 "
                                  f"report card.\n- [Open source skill]({GITHUB}): the full check for Claude Code / "
                                  "Cursor.\n", "text/plain; charset=utf-8", cache="public, max-age=3600")
        if path == "/check":
            return self.send(303, "", extra={"Location": "/"})
        go = re.fullmatch(r"/go/([a-z-]{1,20})", path)
        if go and go.group(1) in GO:
            q = urlparse.parse_qs(parts.query)
            count("click:" + go.group(1), (q.get("v") or ["dev"])[0], (q.get("src") or [""])[0])
            return self.send(302, "", extra={"Location": GO[go.group(1)]})
        if path == "/stats":
            token = (urlparse.parse_qs(parts.query).get("token") or [""])[0]
            if STATS_TOKEN and secrets.compare_digest(token.encode(), STATS_TOKEN.encode()):
                return self.send(200, stats_json(), "application/json")
            return self.send(404, "not found", "text/plain; charset=utf-8")
        n = secrets.token_urlsafe(16)
        return self.send(404, page(n, error="Page not found."), nonce=n)

    def do_POST(self):
        path = urlparse.urlsplit(self.path).path
        if path != "/check":
            return self.send(404, "not found", "text/plain; charset=utf-8")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self.send(413, "bad request", "text/plain; charset=utf-8")
        form = urlparse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        url = (form.get("url") or [""])[0].strip()
        v = variant((form.get("v") or ["dev"])[0])
        src = clean_src((form.get("src") or [""])[0])
        n = secrets.token_urlsafe(16)
        try:
            scanner.validate(url)
        except scanner.ScanError as exc:
            return self.send(400, page(n, v, url_value=url, error=str(exc), src=src), nonce=n)
        ok, wait = rate_ok(client_ip(self))
        if not ok:
            return self.send(429, page(n, v, url_value=url, error=f"{RATE_MAX} checks per 10 minutes per visitor. Try again in "
                                                                  f"{max(1, wait // 60)} min."), nonce=n,
                             extra={"Retry-After": str(wait)})
        job_id = start_job(url, v, src)
        if job_id:
            count("scans_started", v, src)
        if not job_id:
            return self.send(503, page(n, v, url_value=url, error="Too many checks right now. Try again in a minute."),
                             nonce=n)
        return self.send(303, "", extra={"Location": f"/r/{job_id}"})


def main():
    port = int(os.environ.get("PORT", "8000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # nosec B104 - Render requires 0.0.0.0:$PORT
    srv.daemon_threads = True
    sys.stderr.write(f"launch-check web on 0.0.0.0:{port}\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()

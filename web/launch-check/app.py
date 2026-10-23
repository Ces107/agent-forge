"""Launch Check web: paste your URL, get the report card. Python standard library only.

Run: PORT=8000 python app.py   (binds 0.0.0.0:$PORT)
Routes: GET / (?v=key|stat|supabase headline variants), POST /check (starts a scan, 303 to /r/<id>),
GET /r/<id> (progress page, then the report), GET /r/<id>/status (JSON), GET /healthz, /robots.txt, /sitemap.xml,
/llms.txt, /favicon.svg, /og.png.
Nothing is written to disk: a finished report stays in memory while fresh; a compact summary (LC_RESULT log line) keeps
the result link working for 14 days by re-scanning under the same id.
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
import fixpack  # noqa: E402
import fixpages  # noqa: E402
import landing  # noqa: E402

DEMO_ID = "demo"
DEMO_PATH = "/demo/flowpilot/"
PAID: dict[str, dict] = {}  # scan id -> {"url", "t"}; rebuilt from LC_PAID log lines after a restart
RESULTS: dict[str, dict] = {}  # scan id -> compact free view (14 days); rebuilt from LC_RESULT log lines
RESULT_DAYS = 14
CODES: dict[str, dict] = {}  # one-use free-pack codes: code -> {"t", "used"}; rebuilt from LC_CODE lines
LASTING_PHONE: dict[str, dict] = {}  # scan id -> phone render regenerated for an old result (small LRU)

SITE = (os.environ.get("BASE_URL") or os.environ.get("PUBLIC_URL") or "https://launch-check-upz2.onrender.com").rstrip("/")
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
JOB_TTL = 6 * 3600                      # finished reports stay in memory this long (oldest evicted when full)
JOB_MAX = 300
STUCK_AFTER = scanner.BUDGET + 40       # a job still running after this is reported as failed
RENDER_WAIT = float(os.environ.get("LC_RENDER_WAIT", "100"))  # the result waits this long for the real phone render
MAX_BODY = 4096
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()
JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()
JOB_ID = re.compile(r"[A-Za-z0-9_-]{20,40}")

VARIANTS = {
    "roast": {"h1": "Roast my site. Your page on a phone, brutally honest.", "source": "",
              "sub": "We open your page in a real phone browser, box what's broken and roast it. Every burn is a real "
                     "problem with a real fix. Free, no signup.",
              "button": "Roast my site", "example_img": True, "ex": "", "fix": ""},
    "launch": {"h1": "See your site on a phone, problems marked. Free.", "source": "",
               "sub": "Your page in a real phone browser with the top 3 problems boxed, plus what Google & ChatGPT "
                      "can't read. Under a minute, no signup.",
               "button": "See my site on a phone", "example_img": True,
               "ex": "", "fix": ""},
    "dev": {"h1": "Built your site with Claude Code or Cursor? See what's still broken.", "source": "",
            "sub": "Free scan in under a minute: phone layout, what Google & ChatGPT can't read, keys in your code. "
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
                 "sub": "Launch Check looks for you, usually in under a minute. Free, no signup, no account.",
                 "ex": "Your OpenAI API key is visible in your website's code: anyone can run up your bill",
                 "fix": "Fix: call OpenAI from a server function, keep the key in an environment variable, then rotate it."},
    "polish": {"h1": "Does your AI-built site look unfinished?", "source": "",
               "sub": "Free scan: what breaks on phones, what Google and ChatGPT can't read, and whether a key is "
                      "showing. Usually under a minute.",
               "sev": "MEDIUM", "ex": "Your page is wider than a phone screen, and Google sees no description",
               "fix": "Fix: remove fixed widths wider than the screen and add a 120-160 character meta description."},
    "ready": {"h1": "Is your AI-built app ready to launch?", "source": "",
              "sub": "Free scan in under a minute: secret keys, phone layout, Google & ChatGPT, speed.",
              "ex": "Your OpenAI API key is visible in your website's code: anyone can run up your bill",
              "fix": "Fix: call OpenAI from a server function, keep the key in an environment variable, then rotate it."},
    "bill": {"h1": "A leaked Gemini key cost a 3-person startup $82,314 in 48 hours.",
             "source": "Reported by the developer, via The Register, March 2026",
             "sub": "Is your key showing in your site's code? Check in under a minute. Free, no signup.",
             "ex": scanner.GOOGLE_TITLE, "fix": "Fix: " + scanner.GOOGLE_FIX[0].lower() + scanner.GOOGLE_FIX[1:]},
    "gemini": {"h1": "$6,000 in 40 minutes, from one key in a site's JavaScript.",
               "source": "Developer's report on Google's AI developer forum, April 2026",
               "sub": "Is your key showing in your site's code? Check in under a minute. Free, no signup.",
               "ex": scanner.GOOGLE_TITLE, "fix": "Fix: " + scanner.GOOGLE_FIX[0].lower() + scanner.GOOGLE_FIX[1:]},
}
SUB ="See the result usually in under a minute. Free, no signup, no account."

FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
           'fill="#0b1220"/><circle cx="32" cy="32" r="17" fill="none" stroke="#22c55e" stroke-width="7"/>'
           '<path d="M24 33l6 6 11-13" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" '
           'stroke-linejoin="round"/></svg>')

DESC = ("See your site on a real phone with every problem marked: text too faint to read, buttons too small to tap, overlapping or cut-off text, pages wider than the screen. Exact CSS fix for each, free before/after. For sites built with Lovable, Bolt, v0, Replit or Cursor. Usually under a minute, no signup.")

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
    sys.stderr.write("LC_EVENT " + json.dumps({"e": event, "v": variant(v), "src": clean_src(src) or "-",
                                                "t": int(time.time())}, separators=(",", ":")) + "\n")


def stats_json():
    with _stats_lock:
        return json.dumps({"since": STATS["since"], "totals": dict(STATS["totals"]),
                           "by": {k: dict(v) for k, v in STATS["by"].items()},
                           "history": STATS.get("history", "in-memory since boot only")}, indent=1, sort_keys=True)


LEGACY_STAT = re.compile(r"^stat (\S+) v=([^,\s]+),src=(\S+)\s*$")


def parse_event(message):
    """One log line -> (event, v, src) or None. Reads LC_EVENT {json} lines and the older 'stat e v=..,src=..'."""
    message = message.strip()
    if message.startswith("LC_EVENT "):
        try:
            d = json.loads(message[9:])
            return str(d["e"])[:40], str(d.get("v", "dev"))[:20], str(d.get("src", "-"))[:32]
        except (ValueError, KeyError, TypeError):
            return None
    m = LEGACY_STAT.match(message)
    return (m.group(1)[:40], m.group(2)[:20], m.group(3)[:32]) if m else None


def rebuild_stats_from_logs(days=7, max_pages=150):
    """/stats survives deploys: on boot, re-count the event lines this service logged in the last 7 days (Render
    logs API). Needs RENDER_API_KEY and RENDER_OWNER_ID in the environment; otherwise counters start at zero."""
    key, owner = os.environ.get("RENDER_API_KEY", ""), os.environ.get("RENDER_OWNER_ID", "")
    svc = os.environ.get("RENDER_SERVICE_ID", "")
    if not (key and owner and svc):
        return
    import urllib.request
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    found = 0
    for text in ("LC_EVENT", "stat ", "LC_PAID", "LC_RESULT", "LC_CODE"):
        if text == "LC_RESULT":
            days = RESULT_DAYS
        start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
        stop = end
        for _ in range(max_pages):
            q = urlparse.urlencode({"ownerId": owner, "resource": svc, "text": text, "startTime": start,
                                    "endTime": stop, "limit": 100, "direction": "forward"})
            req = urllib.request.Request("https://api.render.com/v1/logs?" + q,
                                         headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - fixed https host
                    data = json.loads(resp.read(5_000_000))
            except Exception as exc:  # API down or key missing rights: keep in-memory counting
                sys.stderr.write(f"stats rebuild stopped: {type(exc).__name__}\n")
                break
            with _stats_lock:
                for log in data.get("logs", []):
                    msg = log.get("message", "").strip()
                    if text in ("LC_RESULT", "LC_CODE"):
                        if msg.startswith(text + " "):
                            try:
                                d = json.loads(msg[len(text) + 1:])
                                if text == "LC_RESULT" and time.time() - d["t"] < RESULT_DAYS * 86400:
                                    RESULTS[str(d["id"])[:40]] = d
                                elif text == "LC_CODE":
                                    c = CODES.setdefault(str(d["code"])[:12], {"t": d["t"], "used": None})
                                    if d.get("used"):
                                        c["used"] = str(d["used"])[:40]
                            except (ValueError, KeyError, TypeError):
                                pass
                        continue
                    if text == "LC_PAID":
                        if msg.startswith("LC_PAID "):
                            try:
                                d = json.loads(msg[8:])
                                if time.time() - d["t"] < fixpack.PACK_DAYS * 86400:
                                    PAID[str(d["scan"])[:40]] = {"url": str(d["url"])[:2000], "t": d["t"]}
                            except (ValueError, KeyError, TypeError):
                                pass
                        continue
                    ev = parse_event(log.get("message", ""))
                    if ev and (text == "LC_EVENT") == log.get("message", "").lstrip().startswith("LC_EVENT"):
                        e, v, src = ev
                        STATS["totals"][e] += 1
                        STATS["by"][e][f"v={v},src={src}"] += 1
                        found += 1
            if not data.get("hasMore"):
                break
            start, stop = data.get("nextStartTime") or start, data.get("nextEndTime") or stop
    with _stats_lock:
        STATS["history"] = f"rebuilt from {found} log lines of the last {days} days, then counted live"
    sys.stderr.write(f"stats rebuilt from {found} log lines\n")


ALIASES = {}  # attribution tags that reuse another variant's copy, e.g. {"launch2": "launch"}


def variant(v):
    """The TAG kept for tracking (form, result page, /go/ links, stats). Aliases stay as typed."""
    return v if v in VARIANTS or v in ALIASES else "dev"


def copy_for(v):
    """The landing COPY for a tag (aliases resolved)."""
    v = variant(v)
    return VARIANTS[ALIASES.get(v, v)]


def head(nonce, h1, extra_head=""):
    """<head> shared by the home and progress pages (landing.CSS), ending with <body>."""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(h1)} | Launch Check</title><meta name="description" content="{esc(DESC)}">
<link rel="canonical" href="{SITE}/"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#070b14"><link rel="apple-touch-icon" href="/icon-192.png">
<link rel="preload" href="/inter-var.woff2" as="font" type="font/woff2" crossorigin>
<meta property="og:title" content="{esc(h1)}"><meta property="og:description" content="{esc(DESC)}">
<meta property="og:type" content="website"><meta property="og:url" content="{SITE}/">
<meta property="og:image" content="{SITE}/og.png"><meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json" nonce="{nonce}">{{"@context":"https://schema.org","@type":"WebApplication","name":"Launch Check","url":"{SITE}/","applicationCategory":"DeveloperApplication","offers":{{"@type":"Offer","price":"0"}},"description":"{esc(DESC)}"}}</script>
{extra_head}<style>{landing.CSS}</style></head><body>"""


def shell(inner, nonce, v="dev", extra_head="", script=""):
    """The progress page: the home's nav and look, one centred card."""
    return (head(nonce, copy_for(v)["h1"], extra_head) + landing.NAV_MIN
            + f'<main class="hero"><div class="w prog-w">{inner}</div></main>{script}</body></html>')


def page(nonce, v="dev", url_value="", error="", src="", autostart=False, keys=True):
    """The home page (landing.py): hero with the form, how it works, what it checks, pricing, FAQ."""
    v = variant(v)
    d = copy_for(v)
    body = landing.render(d=d, v=v, url_value=url_value, error=error, src=clean_src(src), keys=keys, github=GITHUB,
                          price=fixpack.PRICE, price_was=fixpack.PRICE_WAS if fixpack.CHECKOUT_ON else "",
                          dfy_price=fixpack.DFY_PRICE, dfy_on=bool(fixpack.FIX_URL), stripe_on=fixpack.STRIPE_ON)
    auto = ("var f=document.getElementById('f');if(f.url.value){document.body.classList.add('busy');"
            "document.getElementById('b').textContent='Checking… usually under a minute';f.submit();}") if autostart else ""
    script = f"""<script nonce="{nonce}">
document.querySelectorAll('form.lcf').forEach(function(f){{f.addEventListener('submit',function(){{
var b=f.querySelector('button');document.body.classList.add('busy');
setTimeout(function(){{b.disabled=true;b.textContent='Checking… usually under a minute';}},0);}});}});
if('serviceWorker' in navigator){{navigator.serviceWorker.register('/sw.js').catch(function(){{}});}}
{auto}</script>"""
    return head(nonce, d["h1"]) + body + script + "</body></html>"


def progress_page(job_id, job, nonce):
    host = esc(job.get("host", "your site"))
    waiting = job["state"] == "queued"
    rendering = job["state"] == "rendering"
    what = ("Waiting for a free scanner…" if waiting else f"Opening {host} in a real phone browser…" if rendering
            else f"Checking {host}…")
    inner = f"""<div class="prog" role="status"><b>{what}</b>
<div class="bar" aria-hidden="true"><i></i></div>
<p>{'Marking what is broken on the screen. About a minute.' if rendering else 'Usually under a minute.'} This page updates by itself.</p></div>"""
    head = f'<noscript><meta http-equiv="refresh" content="3"></noscript><meta name="robots" content="noindex">'
    script = f"""<script nonce="{nonce}">(function(){{var u='/r/{job_id}/status';
function poll(){{fetch(u,{{cache:'no-store'}}).then(function(r){{return r.json();}}).then(function(d){{
if(d.state==='done'||d.state==='error'||d.state==='gone'){{location.reload();}}else{{if(d.state==='rendering'){{var b=document.querySelector('.prog b');if(b)b.textContent='Opening your site in a real phone browser…';}}setTimeout(poll,1000);}}
}}).catch(function(){{setTimeout(poll,2000);}});}}setTimeout(poll,1200);}})();</script>"""
    return shell(inner, nonce, job.get("v", "dev"), extra_head=head, script=script)


KEY_RULES = ("supabase-found", "firebase-found")


def key_items(report):
    return [f for f in report["findings"] if scanner.is_key_item(f)]


def secret_banner(report):
    """The first section of the result: is a secret key showing?"""
    items = key_items(report)
    assets = report["facts"].get("assets") or {}
    if assets.get("keys_off"):
        return ('<section class="lc-key k-mid"><h1>Is a secret key showing?</h1><div class="lc-ans"><div class="lc-ico" '
                'aria-hidden="true">?</div><div><b>Not checked on this link</b><div class="lc-scope">This link was made '
                'for someone else\'s site, so we did not look for keys in its code; everything else here comes from '
                'what any visitor\'s browser receives. Your site? <a href="/?v=launch">Check it yourself</a> for '
                'exposed keys too.</div></div></div></section>')
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
    """Free copy-paste prompt: problem #1 with its exact fix (the prompt for every problem is in the Fix Pack)."""
    top = other_items(report)[:1]
    if not top:
        return ""
    f = top[0]
    where = f" (on {f['where']})" if f["where"] and f["area"] != "phone" else ""
    return (f"My site {report['final_url']} was checked before launch. Please fix this and tell me what you changed:\n"
            f"{scanner.lc.short(f['title'], 140)}{where}. Fix: {fix_with_background(f)}")


ROAST = {  # one true, funny line per problem: every burn is a finding on the page, never invented
    "phone-tap-target": "Your buttons are so small only ants can tap them.",
    "phone-contrast": "Your text is playing hide-and-seek with the background. The background is winning.",
    "phone-overflow": "Your page is wider than the phone. Sideways scrolling: very 2009.",
    "phone-fixed-width": "Parts of your page are wider than the phone, so it wobbles sideways.",
    "phone-viewport-meta": "No viewport tag: on a phone your site is a postage stamp.",
    "phone-overlap": "Your text is stacking on top of other text. It's crowded in there.",
    "phone-clipped": "Some of your words got cut off. They had so much to say.",
    "phone-layout-shift": "Your page jumps while it loads. Visitors tap the wrong thing and leave.",
    "phone-images": "Your images have no labels. Screen readers just say 'image, image, image'.",
    "phone-headings": "Your headings skip levels. Screen readers get lost, and so does Google.",
    "search-sitemap-missing": "No sitemap. Google is touring your site blindfolded.",
    "search-og-missing": "Share your link and the preview is a blank box. Mystery marketing.",
    "og-image-missing": "Share your link and the preview has no picture. Nobody clicks a grey box.",
    "search-schema-home-org": "Google and ChatGPT don't know who you are. Your landing page isn't telling them.",
    "search-llms-missing": "No llms.txt, so ChatGPT gets to guess what you do. It guesses wrong.",
    "search-img-alt-missing": "Your images have no descriptions. To Google they're just mystery rectangles.",
    "search-js-only-content": "Google sees an empty page until your JavaScript wakes up. It doesn't wait.",
    "search-ai-search-bot-blocked": "Your robots.txt shows ChatGPT the door, then you wonder why it never mentions you.",
    "search-h1-missing": "No main heading. Your page is a book with no title.",
    "desc-missing": "No description, so Google writes one for you. It's not flattering.",
    "title-missing": "No page title. Your browser tab just says the web address.",
    "title-default": "Your page title is still the template default. We see you, 'Vite + React'.",
    "heavy-js": "So much JavaScript before anything works, the phone needs a coffee break.",
    "slow-load": "Your page loads slowly enough to read a book in the meantime.",
    "favicon-missing": "No favicon. Your tab is the generic grey globe of shame.",
    "broken-links": "Some links lead to error pages. A treasure hunt with no treasure.",
    "csp-missing": "Your security headers took the day off.",
    "hsts-missing": "Browsers aren't told to always use HTTPS. Trust issues, but backwards.",
    "clickjacking": "Any site can frame yours invisibly. Very trusting of you.",
    "search-render-blocking-js": "Scripts in the page head make phones stare at a blank screen first.",
    "search-html-heavy": "Your HTML is heavy enough to slow down the first paint on a phone.",
    "search-llms-invalid": "Your llms.txt exists, but AI assistants can't make sense of it.",
    "search-llms-soft404": "Ask for your llms.txt and you get a page that pretends to be one.",
    "search-sitemap-invalid": "You have a sitemap. Google just can't read it.",
    "search-robots-soft404": "Your robots.txt is really a web page in disguise. Crawlers are confused.",
    "search-title-duplicate": "Every page has the same title. Google can't tell them apart either.",
    "search-desc-duplicate": "Every page has the same description. Copy-paste energy.",
    "search-answer-not-concise": "Ask what you do and your page answers with a novel.",
    "search-jsonld-required": "Your structured data is missing the fields Google actually reads.",
    "search-no-compression": "Your HTML travels uncompressed. Every visitor pays for it.",
    "speed-heavy-page": "Your page is heavy enough to need its own moving truck.",
    "heavy-page": "Your page is heavy enough to need its own moving truck.",
    "js-errors": "Your page throws errors in the console while it says hello.",
    "nosniff-missing": "One small security header is missing. Small, but it's free to add.",
    "mixed-content": "Some files still load over plain http. Browsers notice.",
}


FACTS = {  # several wordings per problem, picked per site, so two cards never read the same
    "phone-tap-target": ("Your buttons are {v} px at the thin end. Even WCAG's minimum is 24.",
                         "Tap target: {v} px. That's a fingertip lottery.",
                         "{v} px buttons. Thumbs are not styluses.",
                         "{v} px links: built for ants, used by thumbs.",
                         "A {v} px tap target. Good luck, everyone."),
    "phone-contrast": ("Some text sits at {v}:1 contrast. Readable starts at 4.5.",
                       "{v}:1 contrast. Your text is whispering.",
                       "Text at {v}:1 against its background. Squinting is not a feature.",
                       "Contrast {v}:1. Sunlight will erase it.",
                       "{v}:1 contrast: readable by owls only.",
                       "At {v}:1, your copy is a ghost story."),
    "heavy-js": ("{v} KB of JavaScript before anything works. The phone needs a coffee break.",
                 "{v} KB of scripts to show a landing page. Bold."),
    "phone-overflow": ("{v} parts of your page are wider than the phone, so it wobbles sideways.",),
    "phone-clipped": ("\"{v}\" gets cut off on a phone.", "On a phone, \"{v}\" loses its last letters."),
    "phone-overlap": ("\"{v}\" is printed on top of other text on a phone.", "On a phone, \"{v}\" sits on top of something else."),
    "phone-fixed-width": ("{v} parts of your page are wider than the phone, so it wobbles sideways.",),
}


def fact_value(rule, title, detail):
    t = f"{title} {detail}"
    if rule == "phone-tap-target":
        m = re.search(r"(\d+)x(\d+) px", t)
        return str(min(int(m.group(1)), int(m.group(2)))) if m else ""
    if rule == "phone-contrast":
        m = re.search(r"(\d+(?:\.\d+)?):1", t)
        return m.group(1) if m else ""
    if rule == "heavy-js":
        m = re.search(r"(\d[\d,]*) KB", t)
        return m.group(1) if m else ""
    if rule in ("phone-fixed-width", "phone-overflow"):
        m = re.search(r"\((\d+) places\)", t)
        return m.group(1) if m else ""
    if rule in ("phone-clipped", "phone-overlap"):  # the words themselves are the proof on the picture
        m = re.search(r'"([^"]{3,40})"', detail)
        return m.group(1).strip() if m else ""
    return ""


def fact_line(rule, title, detail, host=""):
    """A burn built from this site's own numbers, when the finding carries them."""
    v = fact_value(rule, title, detail)
    if not v or rule not in FACTS:
        return ""
    forms = FACTS[rule]
    return forms[sum(map(ord, host + rule)) % len(forms)].format(v=v)


def roast_lines(items, host=""):
    """items = [(rule, title[, detail])] in list order -> up to 3 burns, each one a real, checkable finding."""
    burns, used = [], set()
    for item in items:
        rule, title, detail = (tuple(item) + ("",))[:3]
        if rule == "phone-tap-target":  # only what fails even WCAG AA (24 px) is roasted
            v = fact_value(rule, title, detail)
            if v and int(v) >= 24:
                continue
        if not rule.startswith("phone-"):
            continue  # every burn is something you can see boxed on the phone picture
        line = fact_line(rule, title, detail, host) or ROAST.get(rule, "")  # every boxed problem gets its burn
        if not line or rule in used or line in burns:
            continue
        used.add(rule)
        burns.append(line)
        if len(burns) == 3:
            break
    return burns


VERDICTS = {90: ("Annoyingly good, and still we found these.", "Hard to roast. We managed anyway.",
                 "Clean from far away. Up close on a phone, not quite."),
            80: ("Close, but a phone still catches these.", "Your laptop is happy. Your visitors' phones less so.",
                 "Almost launch-ready on a phone. Almost."),
            65: ("Looks fine on a laptop. Your phone visitors disagree.", "Close, but a phone tells a different story.",
                 "Good bones. The phone version needs a pass."),
            50: ("Ship it? After these.", "Your phone visitors would notice these first.",
                 "Fixable in an afternoon. Ignored, it costs signups."),
            0: ("We need to talk before you launch.", "Your phone visitors are having a rough time.",
                "This needs a pass before anyone sees it.")}


def roast_verdict(score, host=""):
    band = next(b for b in (90, 80, 65, 50, 0) if score >= b)
    return VERDICTS[band][(sum(map(ord, host)) + score) % 3]


def roast_items(report, phone=None):
    """Burn order shared by the page and the card: the boxed problems first, then the problem list."""
    legend = ((phone or {}).get("stats") or {}).get("legend") or [] if (phone or {}).get("jpg") else []
    boxed = {"phone-" + x.get("key", "") for x in legend}
    items = [("phone-" + x.get("key", ""), "", x.get("detail", "")) for x in legend]
    # a phone burn needs a box you can see on the card; search, speed and security burns are checkable as they are
    items += [(f["rule"], scanner.lc.short(f["title"], 90), f.get("detail", "")) for f in other_items(report)
              if f["area"] != "phone" or f["rule"] in boxed]
    return items


def roast_card(report, job_id, phone=None, n_all=None):
    if not (phone or {}).get("jpg"):  # no picture, no roast: a burn must be checkable against the boxed screenshot
        return ('<section class="lc-roast"><div class="rk">ROAST ON HOLD</div><p class="rv">The phone render didn\'t '
                'finish, so there is no roast this time: we only roast what we can show you boxed.</p>'
                '<a class="rb" href="#lc-ps">Try the render again below</a></section>')
    fix = other_items(report)
    burns = roast_lines(roast_items(report, phone), urlparse.urlsplit(report["final_url"]).hostname or "")
    legend = ((phone or {}).get("stats") or {}).get("legend") or []
    boxes = len(legend)
    if boxes < 2 or len(burns) < 2:  # a roast needs two boxed problems; one is a fix, none is a pass
        host = urlparse.urlsplit(report["final_url"]).hostname or ""
        if boxes and burns:
            total = n_all or len(fix)  # lead with the whole count: one boxed, the rest in the pack
            return (f'<section class="lc-roast"><div class="rk">{total} PROBLEM{"S" if total != 1 else ""} FOUND · 1 BOXED '
                    f'ON YOUR PHONE · {esc(host)}</div>'
                    f'<div class="rs">{report["score"]}<span>/100</span></div><ol><li>{esc(burns[0])}</li></ol>'
                    f'<p class="rv">One problem is boxed on your phone screen; the full list and every fix are below.</p>'
                    + (f'<div style="margin-top:10px">{fixpack.fix_link(job_id)}</div>' if fixpack.FIX_URL else "")
                    + '</section>')
        return (f'<section class="lc-roast"><div class="rk">PASSES ON A PHONE · {esc(host)}</div>'
                f'<p class="rv">Hard to roast: nothing worth boxing on a phone.</p></section>')
    n_all = n_all or len(fix)
    host = urlparse.urlsplit(report["final_url"]).hostname or ""
    lis = "".join(f"<li>{esc(b)}</li>" for b in burns) or "<li>We looked hard. Nothing to roast. Rude.</li>"
    page_url = f"{SITE}/r/{job_id}"
    tweet = urlparse.quote(f"I got my site roasted: {report['score']}/100. {burns[0] if burns else ''} {page_url}")
    return (f'<section class="lc-roast"><div class="rk">ROASTED · {esc(host)}</div><div class="rs">{report["score"]}'
            f'<span>/100</span></div><p class="rv">{esc(roast_verdict(report["score"], host))}</p><ol>{lis}</ol>'
            f'<div class="rsh"><a class="rb" href="https://x.com/intent/post?text={tweet}" rel="noopener" '
            f'target="_blank">Share your roast</a> <a class="rb" href="/share/{esc(job_id)}.png?roast=1" '
            f'rel="noopener">Roast card image</a> <span>Every burn is a real problem.</span></div>'
            + (f'<div style="margin-top:10px">{fixpack.fix_link(job_id)}</div>' if fixpack.FIX_URL else "")
            + (f'<form method="post" action="/buy/{esc(job_id)}"><button class="rbuy" type="submit">Fix all '
               f'<span class="lc-n">{n_all}</span> before anyone else sees them — {fixpack.PRICE}</button></form>'
               if fixpack.CHECKOUT_ON else '<a class="rbuy" href="#lc-fp">See every fix below</a>')
            + '</section>')


ROAST_CSS = """
.lc-roast{background:#0f172a;color:#f8fafc;border-radius:22px;padding:22px 24px;margin:0 0 14px}
.lc-roast .rbuy{display:inline-block;margin-top:12px;background:#22c55e;color:#04210f;border:0;border-radius:12px;padding:12px 18px;font:inherit;font-size:17px;font-weight:800;text-decoration:none;cursor:pointer}
.lc-roast .rk{font-weight:800;letter-spacing:.08em;color:#fb923c;font-size:13px}
.lc-roast .rs{font-size:64px;font-weight:900;line-height:1}.lc-roast .rs span{font-size:22px;color:#94a3b8}
.lc-roast .rv{font-size:18px;font-weight:700;margin:4px 0 10px}.lc-roast ol{margin:0 0 12px;padding-left:22px}
.lc-roast li{font-size:17px;margin:6px 0}.lc-roast .rsh span{color:#94a3b8;font-size:13px;margin-left:6px}
.lc-roast a.rb{display:inline-block;padding:9px 14px;border-radius:10px;background:#f8fafc;color:#0f172a;font-weight:700;text-decoration:none;margin:4px 6px 4px 0}
"""


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
                    'This free scan reads your page code; it does not render it. The Fix Pack renders your page at '
                    'phone, tablet and desktop size and marks each problem on a screenshot.</section>')
        return (f'<section class="lc-phone lc-phs"><b>Phone layout: basic check from the page code only</b> (no browser '
                f'render this time: {esc(reason)}).</section>')
    return ('<section class="lc-phone lc-phs"><b>Phone layout: not checked</b> (the check did not finish in time).'
            '</section>')


def phone_marks(report):
    """{phone key: rank in the problem list} for every phone problem: the render boxes only these, with these numbers."""
    marks = {}
    for i, f in enumerate(other_items(report)):
        if f["area"] == "phone":
            marks.setdefault(f["rule"][6:], i + 1)
    return marks


def plain_phone(key, detail=""):
    return scanner.lc.PHONE_PLAIN.get(key) or scanner.lc.short(detail or key.replace("-", " "), 90)


def phone_counts(stats):
    """Real counts from the render: the boxed spots (one numbering), spots left, and what kind of render it was."""
    if not stats:
        return {}
    total, boxed = int(stats.get("total", 0)), int(stats.get("boxed", 0))
    legend = [{"n": int(x.get("n", i + 1)), "title": plain_phone(x.get("key", ""), x.get("detail", "")),
               "x": int(x.get("x", 0))} for i, x in enumerate(stats.get("legend") or [])]
    return {"mode": stats.get("mode", "marks"), "legend": legend, "boxed": boxed, "more": max(0, total - boxed),
            "extra": sum(x["x"] for x in legend),
            "total": total, "lower": int(stats.get("y0", 0) or 0) > 0}


def fixpack_cta(job_id, n, free=3):
    """One count everywhere: n = all problems (list + what the real render adds), free = how many the page shows."""
    more = (f"{n - free} more problem{'s' if n - free != 1 else ''} in your Fix Pack · every one boxed on phone, tablet and "
            f"desktop" if n > free else "Fix Pack: every fix boxed on phone, tablet and desktop, with the files")
    if fixpack.CHECKOUT_ON and job_id:
        label = f'Get all <span class="lc-n">{n}</span> fixes' if n > 3 else "Get my Fix Pack: files + renders"
        button = (f'<form method="post" action="/buy/{esc(job_id)}"><button class="fp-buy fp-sm" type="submit">{label} '
                  f'— {fixpack.PRICE}</button></form>')
    else:
        button = '<a class="fp-buy fp-sm" href="/fix/demo">See a sample Fix Pack</a>'
    return (f'<div class="lc-side"><p class="lc-more" id="lc-more">{esc(more)} · {fixpack.PRICE} (refund if it '
            f'doesn\'t help)</p>{fixpack.proof_line()}{button}</div>')


def phone_slot(report, job_id, state, stats=None):
    """The real 375 px render: fills itself when the background render is done; honest when it can't be made."""
    if not job_id or (state not in ("rendering", "done", "botcheck") and scanner.RENDER_MODE != "async"):
        return ""
    if state not in ("rendering", "done", "botcheck"):  # queue was full or the render failed: say so, offer a retry
        return (f'<section class="lc-phone" id="lc-ps" data-u="/r/{job_id}/phone" data-done="0" data-failed="1">'
                f'<h2>Your site on a phone</h2></section>')
    if state == "botcheck":
        return ('<section class="lc-phone lc-phs"><b>Your site didn\'t let our browser in this time</b> (some hosts block '
                'cloud servers or show them a bot check), so there is no phone render. The scan above still read your '
                'page code.</section>')
    counts = phone_counts(stats)
    n = len(other_items(report))
    lg = (stats or {}).get("legend") or []
    passes = state == "done" and not lg  # no pitch when nothing is boxed
    legend = "".join(f'<li><b>#{x["n"]}</b> {esc(x["title"])}'
                     + (' <span class="lc-inpk">in your Fix Pack</span>' if x["n"] > 3 or x["x"] else "") + '</li>'
                     for x in counts.get("legend", []))
    mode = counts.get("mode")
    note = ""
    if state == "done" and mode == "clean":
        note = "No phone layout problems found on the first screen."
    elif state == "done" and mode == "unboxed":
        note = "None of the listed problems shows on the first screen; the Fix Pack renders the whole page."
    elif state == "done" and counts.get("lower"):
        note = "Your first screen had no problems; this is the part of the page further down where they start."
    bx = ("Rendered in a real phone browser (375 px). Numbers match the problem list."
          if counts.get("legend") or state != "done" else "Rendered in a real phone browser (375 px).")
    src = f' src="/r/{job_id}/phone.jpg"' if state == "done" else ""
    wait = "" if state == "done" else '<div class="lc-wait" id="lc-pw">rendering… (about 30 s)</div>'
    return (f'<section class="lc-phone" id="lc-ps" data-u="/r/{job_id}/phone" data-n="{n}" '
            f'data-done="{1 if state == "done" else 0}"><h2>Your site on a phone</h2><div class="lc-ph"><div>{wait}<img id="lc-pi" alt="Your page at 375 px wide '
            f'with problems boxed"{src}{"" if state == "done" else " hidden"}></div><div><p class="lc-phn" id="lc-bx">'
            f'{esc(bx)}</p><ul id="lc-leg">{legend}</ul><p class="lc-phn" id="lc-note">'
            f'{esc(note)}</p>{"" if passes else fixpack_cta(job_id, n + counts.get("extra", 0), min(3, n))}</div></div>'
            + before_after(job_id, stats if state == "done" else None) + '</section>')


def more_tools():
    """The other small tools, each only when its link is set (no dead links)."""
    links = [("/og", "OG image + favicon maker", True),
             (os.environ.get("LC_PACK_URL", ""), "40 copy-paste fix prompts for Lovable/Bolt/Cursor ($1+)", False),
             (os.environ.get("LC_EXT_URL", ""), "Phone Check for Chrome (free)", False)]
    items = [f'<a href="{esc(u)}"{"" if local else " rel=\"noopener\""}>{esc(t)}</a>' for u, t, local in links if u]
    return f'<p class="lc-nc" style="text-align:center">More tools: {" · ".join(items)}</p>' if items else ""


def before_after(job_id, stats):
    """Free proof: the same phone screen with one boxed fix applied, next to the original."""
    if not stats or not stats.get("_after"):
        return ""
    n = stats.get("after_n", 1)
    return (f'<div class="lc-ba"><figure><img src="/r/{job_id}/phone.jpg" alt="Your page now"><figcaption>Now</figcaption>'
            f'</figure><figure><img src="/r/{job_id}/after.jpg" alt="Your page with fix #{n} applied"><figcaption>With fix '
            f'#{n} applied: <code>{esc(stats.get("after_fix", ""))}</code></figcaption></figure>'
            + (f'<form method="post" action="/buy/{esc(job_id)}" class="lc-bab"><button class="fp-buy fp-sm" type="submit">'
               f'Get every fix like this — {fixpack.PRICE}</button></form>' if fixpack.CHECKOUT_ON else
               '<div class="lc-bab"><a class="fp-buy fp-sm" href="/fix/demo">See every fix in a sample pack</a></div>')
            + '</div>')


def summary_of(report, job):
    """The compact free view kept 14 days (logged as LC_RESULT, rebuilt after restarts)."""
    keys = key_items(report)
    red = [f for f in keys if f["severity"] == "HIGH" and f["rule"].startswith("secret-")]
    fix = other_items(report)
    assets = report["facts"].get("assets") or {}
    return {"id": job["id"], "url": job["url"], "final": report["final_url"], "t": int(time.time()),
            "score": report["score"], "v": job.get("v", "dev"), "src": job.get("src", ""),
            "key": "bad" if red else ("mid" if keys else "ok"), "keys": [scanner.lc.short(f["title"], 120) for f in keys][:3],
            "top3": [{"t": scanner.lc.short(f["title"], 140), "a": f["area"], "r": f["rule"]} for f in fix[:3]],
            "n": len(fix), "scripts": assets.get("scanned", 0), "off": assets.get("skipped_offsite", 0),
            "k": 0 if assets.get("keys_off") else 1}


def lasting_page(sm, nonce, phone_state=None, phone_stats=None):
    """A result older than the in-memory report: summary, top 3 and the phone render (regenerated), one-click re-scan."""
    host = urlparse.urlsplit(sm["final"]).hostname or sm["final"]
    head = {"bad": "Yes: a secret key is showing in your site's code", "mid": "No secret keys, but keys to lock down",
            "ok": "No secret keys found in your site's code"}[sm.get("key", "ok")]
    keys = "".join(f"<li>{esc(t)}</li>" for t in sm.get("keys", []))
    top = "".join(f"<li>{esc(x['t'])}</li>" for x in sm.get("top3", []))
    when = time.strftime("%d %b %Y", time.gmtime(sm["t"]))
    fake = {"findings": [], "facts": {}}
    slot = phone_slot(fake, sm["id"], phone_state, phone_stats) if phone_state else ""
    again = (f'<form method="post" action="/check"><input type="hidden" name="url" value="{esc(sm["url"])}">'
             f'<input type="hidden" name="v" value="{esc(sm.get("v", "dev"))}"><button class="btn" type="submit">Scan '
             f'{esc(host)} again</button></form>')
    body = (f'<div class="card"><h1>{esc(host)}: score {sm["score"]}/100</h1><p>Checked {esc(when)}. '
            f'{esc(head)}.</p>{"<ul>" + keys + "</ul>" if keys else ""}<div class="acts">{again}</div></div>{slot}'
            f'<div class="card"><h2>Top {len(sm.get("top3", []))} problems{f" of {sm["n"]}" if sm.get("n", 0) > 3 else ""}</h2>'
            f'<ol>{top}</ol></div><div class="card"><h2>Your Fix Pack</h2><p>Every problem boxed on phone, tablet and '
            f'desktop, the files for your site and one fix prompt. Scan again to see your pack preview.</p><div class="acts">'
            f'{again}<a class="btn s" href="/fix/demo">See a sample Fix Pack</a></div></div>')
    return fixpages._doc(f"{host}: Launch Check result", body, nonce, PHONE_POLL_JS)


PHONE_POLL_JS = r"""(function(){var s=document.getElementById('lc-ps');if(!s||s.getAttribute('data-done')==='1')return;
var u=s.getAttribute('data-u'),n=0,again=false;
function fail(msg){s.className='lc-phone lc-phs';s.innerHTML='';var b=document.createElement('b');
b.textContent=msg||'The phone render didn’t finish: our server was busy.';var t=document.createElement('button');
t.type='button';t.className='btn s';t.style.marginLeft='10px';t.textContent='Render it again';s.appendChild(b);s.appendChild(t);
t.onclick=function(){t.disabled=true;t.textContent='rendering… (about 30 s)';again=true;
fetch(u+'?retry=1',{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){
if(d.state==='rendering'){n=0;setTimeout(poll,3000);}else if(d.state==='done'){location.reload();}
else{fail('Still busy. Try again in a minute.');}}).catch(function(){fail();});};}
if(s.getAttribute('data-failed')==='1'){fail();return;}
function poll(){n++;fetch(u,{cache:'no-store'}).then(function(r){return r.json();})
.then(function(d){if(d.state==='done'&&again){location.reload();return;}
if(d.state==='done'){var i=document.getElementById('lc-pi');i.onload=function(){i.hidden=false;
var w=document.getElementById('lc-pw');if(w)w.remove();var leg=document.getElementById('lc-leg');leg.textContent='';
(d.legend||[]).forEach(function(x){var li=document.createElement('li');var b=document.createElement('b');b.textContent='#'+x.n;
li.appendChild(b);li.appendChild(document.createTextNode(' '+x.title));if(x.n>3||x.x){var k=document.createElement('span');
k.className='lc-inpk';k.textContent='in your Fix Pack';li.appendChild(document.createTextNode(' '));li.appendChild(k);}
leg.appendChild(li);});
if(!(d.legend||[]).length){var bx=document.getElementById('lc-bx');if(bx)bx.textContent='Rendered in a real phone browser (375 px).';}
var tn=+(s.getAttribute('data-n')||0)+(d.extra||0),mo=document.getElementById('lc-more');
if(d.extra){document.querySelectorAll('.lc-n').forEach(function(e){e.textContent=tn;});
var th=document.getElementById('lc-t3h');if(th){var sh=+th.getAttribute('data-shown');th.textContent='Top '+sh+' problems of '+tn;}
var fs=document.getElementById('fp-sub');if(fs&&tn>3)fs.textContent=tn+' problems found: '+Math.min(3,tn)+' free above, all '+tn+' fixed here, plus the files your site is missing. Locked until checkout.';}
var fr=Math.min(3,+(s.getAttribute('data-n')||0));if(mo&&d.extra&&tn>fr)mo.firstChild.textContent=(tn-fr)+' more problem'+(tn-fr===1?'':'s')+' in your Fix Pack · every one boxed on phone, tablet and desktop · $9 (refund if it doesn’t help)';
var note=document.getElementById('lc-note');if(d.mode==='clean')note.textContent='No phone layout problems found on the first screen.';
if(d.mode==='unboxed')note.textContent='None of the listed problems shows on the first screen; the Fix Pack renders the whole page.';
if(d.mode==='marks'&&d.lower)note.textContent='Your first screen had no problems; this is the part of the page further down where they start.';
};i.onerror=function(){fail();};i.src=u+'.jpg';}
else if(d.state==='botcheck'){s.className='lc-phone lc-phs';s.innerHTML='<b>Your site didn\u2019t let our browser in this time</b> (some hosts block cloud servers or show them a bot check), so there is no phone render. The scan still read your page code.';}
else if(d.state==='rendering'&&n<75){setTimeout(poll,2000);}else{fail();}}).catch(function(){fail();});}
setTimeout(poll,3000);})();"""
SW_JS = """self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>self.clients.claim());
self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;e.respondWith(fetch(e.request).catch(()=>
new Response('<meta name=viewport content=width=device-width><body style="font:16px system-ui;padding:24px">You are offline. '
+'Launch Check needs the internet to open your site on a phone.</body>',{headers:{'Content-Type':'text/html'}})));});"""
PHONE_POLL_JS = PHONE_POLL_JS.replace("$9 (refund", fixpack.PRICE + " (refund")  # one price setting


def result_page(report, nonce, v="dev", src="", job_id=None, phone_state=None, phone_stats=None, before=None):
    fix = other_items(report)
    n = len(fix)
    doc = scanner.render({**report, "findings": [], "fix_first": []})
    prompt = fix_prompt(report, v, src)
    top3 = "".join(f"<li>{esc(scanner.lc.short(f['title'], 140))}"
                   + (f'<small class="lc-fx">{esc(fix_with_background(f)[:260])}</small>' if i == 0 and f.get("fix")
                      else '<small class="lc-fx">exact fix in your Fix Pack</small>' if f.get("fix") else "") + "</li>"
                   for i, f in enumerate(fix[:3]))
    total = n + (phone_counts(phone_stats).get("extra", 0) if phone_state == "done" else 0)  # one number, also on reload
    free = (f'<section class="lc-top3"><h2 id="lc-t3h" data-shown="{min(3, n)}">Top {min(3, n)} problems'
            f'{f" of {total}" if total > min(3, n) else ""}</h2><ol>{top3}</ol>'
            f'<div class="ask"><div class="h">Free: fix #1, ready to paste to your AI (the prompt for all of them is in the Fix Pack)</div><button id="lc-copy" type="button">Copy for '
            f'your AI</button><pre id="p">{esc(prompt)}</pre></div><p class="lc-nc">Not checked in the free scan: tablet '
            f'and desktop renders, hover states, re-scoring after fixes.</p></section>' if n else
            '<section class="lc-top3"><h2>No problems worth fixing before launch</h2></section>')
    counts = phone_counts(phone_stats) if phone_state == "done" else {}
    if phone_state == "done" and not (phone_stats or {}).get("legend"):  # a pass: no pack pitch, one quiet line
        preview = (f'<section class="lc-top3" id="lc-fp"><p class="lc-nc">Your site passes on a phone. The {n} smaller '
                   f'items above have their exact fixes and files in the <a href="/fix/demo">Fix Pack</a> ({fixpack.PRICE}), if you '
                   f'want them.</p></section>')
    else:
        preview = fixpack.locked_preview(report, job_id or "", counts.get("more"), SITE, demo=False, n_all=total)
    slot = phone_slot(report, job_id, phone_state, phone_stats)
    then = ""
    share_meta = ""
    if before is not None and job_id:
        img = f"{SITE}/share/{job_id}.png?b={before}"
        host = urlparse.urlsplit(report["final_url"]).hostname or ""
        then = (f'<section class="lc-then">Score <b>{before}</b> → <b>{report["score"]}</b> after the fixes. '
                f'<a href="{esc(img)}" rel="noopener">Share image</a> · this page link shows it as the preview.</section>')
        share_meta = (f'<meta property="og:title" content="{esc(host)}: launch score {before} → {report["score"]}">'
                      f'<meta property="og:image" content="{esc(img)}"><meta name="twitter:card" '
                      f'content="summary_large_image"><meta name="twitter:image" content="{esc(img)}">')
    ph = (JOBS.get(job_id) or {}).get("phone") if job_id else None
    roast = (roast_card(report, job_id, ph, n + phone_counts((ph or {}).get("stats")).get("extra", 0))
             if v == "roast" and job_id else "")
    if roast and not share_meta:  # the result link previews as the roast card wherever it's pasted
        img = f"{SITE}/share/{job_id}.png?roast=1"
        host = urlparse.urlsplit(report["final_url"]).hostname or ""
        share_meta = (f'<meta property="og:title" content="{esc(host)} got roasted: {report["score"]}/100">'
                      f'<meta property="og:image" content="{esc(img)}"><meta name="twitter:card" '
                      f'content="summary_large_image"><meta name="twitter:image" content="{esc(img)}">')
    keys_off = (report["facts"].get("assets") or {}).get("keys_off")  # a link made for someone else's site
    rules = {f["rule"] for f in report["findings"]}
    if rules & {"og-image-missing", "search-og-missing"}:  # no link preview card: the free maker makes one
        preview += ('<section class="lc-top3"><p class="lc-nc"><b>Your links show no preview card.</b> Make one in 30 '
                    'seconds with the <a href="/og">free OG image maker</a> (1200x630 card + favicon set).</p></section>')
    preview += more_tools()
    first = (roast + then + ("" if keys_off else secret_banner(report)) + (slot or phone_section(report)) + free + preview
             + (secret_banner(report) if keys_off else ""))
    script = f"""<script nonce="{nonce}">(function(){{var b=document.getElementById('lc-copy');if(!b)return;
b.addEventListener('click',function(){{var t=document.getElementById('p').innerText;
function ok(){{b.textContent='Copied — paste it to your AI';}}
if(navigator.clipboard&&navigator.clipboard.writeText){{navigator.clipboard.writeText(t).then(ok,function(){{fallback();}});}}else{{fallback();}}
function fallback(){{var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();
try{{document.execCommand('copy');ok();}}catch(e){{}}document.body.removeChild(a);}}}});}})();
{PHONE_POLL_JS}</script>"""
    top = '<div class="lc-top"><a href="/">← Check another app</a></div>'
    doc = doc.replace("</style></head><body>", ROAST_CSS + FUNNEL_CSS + KEY_CSS + OFFER_CSS + fixpack.FP_CSS + RESULT_CSS
                      + '</style><meta name="robots" content="noindex"><link rel="icon" href="/favicon.svg" '
                      'type="image/svg+xml">' + share_meta + '</head><body>' + top, 1)
    doc = re.sub(r'<section class="hero">.*?(?=<div class="foot">)', lambda _m: first, doc, count=1, flags=re.S)
    doc = doc.replace("A good score is not a security audit.</div>",
                      "A good score is not a security audit. " + (
                          "Phone findings are measured in a real phone browser (375 px, 1 screen size) and the screenshot is "
                          "that render. " if report.get("phone_measured") else
                          "The phone render didn't finish, so phone findings are only what the page code shows (viewport tag, "
                          "image sizes). ") + "This page link works for 14 days: opening it later re-checks your site, so it "
                      "always shows today's result."
                      + "".join(f"<br>Note: {esc(x)}" for x in report.get("notes", [])[:4]) + "</div>", 1)
    tail = "</div></body></html>"
    if doc.endswith(tail):
        doc = doc[: -len(tail)] + "</div>" + script + "</body></html>"
    return doc


RESULT_CSS = """
.lc-top3{background:#fff;border:1px solid #e2e8f0;border-radius:22px;padding:20px 24px;margin:0 0 14px}
.lc-top3 h2{font-size:20px;margin:0 0 8px}.lc-top3 ol{margin:0 0 12px;padding-left:22px;font-size:16px;font-weight:600}
.lc-top3 li{margin:4px 0}.lc-ba{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.lc-ba img{width:100%;border:6px solid #0f172a;border-radius:18px}.lc-ba figcaption{font-size:13px;color:#475569}.lc-ba code{font-size:11px;word-break:break-all}.lc-bab{grid-column:1/-1;text-align:center}.lc-fx{display:block;font-weight:500;font-size:13px;color:#475569;font-family:Consolas,monospace}.lc-nc{color:#64748b;font-size:13px;margin:10px 0 0}
.lc-side{margin-top:14px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:14px;padding:12px 14px}
.lc-side .lc-more{margin:0 0 10px;color:#14532d}.lc-inpk{font-size:12px;color:#c2410c;font-weight:700}.fp-sm{font-size:16px!important;padding:11px 18px!important}
.lc-then{background:#eff6ff;border:1px solid #bfdbfe;border-radius:14px;padding:12px 16px;margin:0 0 14px;font-size:16px}
"""


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
    for k in [k for k, j in JOBS.items() if now - j["t"] > JOB_TTL and j.get("keep_until", 0) < now]:
        JOBS.pop(k, None)
    if len(JOBS) >= JOB_MAX:  # full: drop the oldest finished reports (their links re-check when opened)
        old = sorted((j["t"], k) for k, j in JOBS.items() if j.get("state") in ("done", "error")
                     and j.get("keep_until", 0) < now)
        for _, k in old[: len(JOBS) - JOB_MAX + 20]:
            JOBS.pop(k, None)


def start_job(url, v, src="", job_id=None, keep=False, keys=True):
    """Register a scan and run it in the background. Returns the job id, or None when too many are pending."""
    now = time.time()
    host = (urlparse.urlsplit(url if "://" in url else "https://" + url).hostname or "your site")[:80]
    with _jobs_lock:
        _prune(now)
        if len(JOBS) >= JOB_MAX:
            return None
        job_id = job_id or secrets.token_urlsafe(18)
        JOBS[job_id] = {"state": "queued", "t": now, "url": url, "v": variant(v), "src": clean_src(src), "host": host,
                        "id": job_id, "keep_until": now + fixpack.PACK_DAYS * 86400 if keep else 0,
                        "keys": bool(keys)}

    def work():
        job = JOBS.get(job_id)
        if job is None:
            return
        if not CONCURRENCY.acquire(timeout=QUEUE_WAIT):
            job.update(state="error", error="Two checks are already running. Try again in a minute.")
            return
        try:
            job.update(state="running", started=time.time())
            report = scanner.scan(url, keys=job.get("keys", True))
            report["job"] = job_id  # stamped: the page refuses to show a report from any other job
            if scanner.RENDER_MODE == "async" and RENDER_WAIT > 0 and scanner.render_slot_free():
                # the result waits for the real phone render, and its list is built from what the browser saw
                job.update(state="rendering", report=report)
                try:
                    out = scanner.render_phone(report["final_url"], {}, budget=RENDER_WAIT, listn=0)
                except Exception:  # never break the result
                    out = None
                job["phone"] = phone_state_from(out)
                if job["phone"]["state"] == "done":
                    merge_render(report, job["phone"]["stats"])
                else:
                    drop_phone_guesses(report)  # no measurement, no phone claims (the retry fills them in)
            else:
                drop_phone_guesses(report)
                start_phone(job, report)  # before "done", so the first result page already shows "rendering"
            job.update(state="done", report=report)
            count("scans_done", job["v"], job["src"])
            sm = summary_of(report, job)
            RESULTS[job_id] = sm
            sys.stderr.write("LC_RESULT " + json.dumps(sm, separators=(",", ":"), ensure_ascii=True) + "\n")
            if any(f["severity"] != "INFO" for f in report["findings"]):
                count("results_with_findings", job["v"], job["src"])
            sys.stderr.write(f"scan {host} score={report['score']} {report['seconds']}s\n")
        except scanner.ScanError as exc:
            job.update(state="error", error=str(exc))
            sys.stderr.write(f"scan {host} refused-or-failed\n")
        except Exception as exc:  # never leak a stack trace to the visitor (the log keeps the exception type)
            job.update(state="error", error="Something went wrong while checking this site. Try again, or try "
                                            "your preview address.")
            sys.stderr.write(f"scan {host} crashed: {type(exc).__name__}\n")
        finally:
            CONCURRENCY.release()

    threading.Thread(target=work, daemon=True).start()
    return job_id


def start_phone(job, report):
    """Render the page at 375 px with the free top-3 phone problems boxed, after the result is ready.
    False when renders are off or the queue (3) is full: the page then offers a retry."""
    if scanner.RENDER_MODE != "async" or not scanner.render_slot_free():
        return False
    job["phone"] = {"state": "rendering", "t": time.time()}

    def work():
        measured = report.get("phone_measured")
        try:
            if measured:
                out = scanner.render_phone(report["final_url"], phone_marks(report), listn=len(other_items(report)))
            else:  # the result has no phone measurement yet: this render becomes it
                out = scanner.render_phone(report["final_url"], {}, budget=RENDER_WAIT, listn=0)
        except Exception:  # never break the result page
            out = None
        ph = phone_state_from(out)
        if ph["state"] == "done" and not measured:
            merge_render(report, ph["stats"])
        job["phone"] = ph

    threading.Thread(target=work, daemon=True).start()
    return True


KEEP_STATIC = {"phone-viewport-meta", "phone-images"}  # read from the page code reliably; the rest comes from the render


def fix_with_background(f):
    """A contrast fix names the background it was measured on, so "make it lighter" never looks backwards."""
    m = re.search(r" on (#[0-9a-fA-F]{3,8})", f.get("detail", "")) if f["rule"] == "phone-contrast" else None
    return f'{f["fix"]} (on background {m.group(1)})' if m and "background" not in f["fix"] else f["fix"]


def drop_phone_guesses(report):
    report["findings"] = [f for f in report["findings"] if f["area"] != "phone" or f["rule"] in KEEP_STATIC]


def merge_render(report, stats):
    """Replace the page-code guesses about phone layout with what the real browser measured (boxed ones first)."""
    kinds = stats.get("kinds") or []
    report["phone_measured"] = True  # a finished render is a measurement, even when it found nothing to box
    boxed = {x["key"]: int(x["n"]) for x in stats.get("legend") or []}
    # the render measured the page itself (viewport included): only image facts from the code stay
    report["findings"] = [f for f in report["findings"] if f["area"] != "phone" or f["rule"] == "phone-images"]
    have = {f["rule"] for f in report["findings"]}
    for k in kinds:
        rule = "phone-" + k["key"]
        if rule in have or rule in scanner.STYLE_RULES:
            continue
        sev = {"HIGH": "HIGH", "MED": "MEDIUM"}.get(k.get("sev"), "LOW")
        if k["key"] in boxed and sev == "LOW":
            sev = "MEDIUM"  # what the picture shows is always on the list
        n = int(k.get("count", 1))
        f = scanner.lc.finding("phone", rule, sev, plain_phone(k["key"], k.get("detail", "")) + (f" ({n} places)" if n > 1 else ""),
                               k.get("fix") or "", detail=k.get("detail", ""))
        if k["key"] in boxed:
            f["boxed"] = boxed[k["key"]]
        f["measured"] = True
        report["findings"].append(f)
        have.add(rule)
    report["phone_measured"] = True


def job_state(job):
    if job["state"] in ("queued", "running") and time.time() - job.get("started", job["t"]) > STUCK_AFTER:
        job.update(state="error", error="This site took too long to check. Try again, or try a lighter page.")
    return job["state"]


def phone_state_from(out):
    if not out:
        return {"state": "none"}
    jpg, stats = out
    if (stats or {}).get("mode") == "unstyled":  # the page's CSS never loaded: show nothing rather than fiction
        return {"state": "none"}
    if (stats or {}).get("mode") == "botcheck":
        return {"state": "botcheck", "stats": stats}
    return {"state": "done", "jpg": jpg, "stats": stats}


def start_lasting_phone(scan_id, sm):
    """Regenerate the phone render of an old result (the JPEG is not kept across restarts)."""
    if len(LASTING_PHONE) > 40:
        LASTING_PHONE.pop(next(iter(LASTING_PHONE)))
    marks = {x["r"][6:]: str(i + 1) for i, x in enumerate(sm.get("top3", [])) if x.get("a") == "phone"}
    if scanner.RENDER_MODE != "async" or not scanner.render_slot_free():
        LASTING_PHONE[scan_id] = {"state": "none"}
        return LASTING_PHONE[scan_id]
    LASTING_PHONE[scan_id] = {"state": "rendering", "t": time.time()}

    def work():
        try:
            out = scanner.render_phone(sm["final"], marks, listn=sm.get("n", 0))
        except Exception:  # never break the page
            out = None
        LASTING_PHONE[scan_id] = phone_state_from(out)

    threading.Thread(target=work, daemon=True).start()
    return LASTING_PHONE[scan_id]


def mint_code():
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    CODES[code] = {"t": time.time(), "used": None}
    sys.stderr.write("LC_CODE " + json.dumps({"code": code, "t": int(time.time())}, separators=(",", ":")) + "\n")
    return code


def use_code(code, scan_id):
    c = CODES.get((code or "").strip().upper())
    if not c or (c["used"] and c["used"] != scan_id):
        return False
    if not c["used"]:
        c["used"] = scan_id
        sys.stderr.write("LC_CODE " + json.dumps({"code": code.strip().upper(), "used": scan_id, "t": int(time.time())},
                                                 separators=(",", ":")) + "\n")
    return True


def _font(size):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def roast_png(host, score, burns, phone_jpg=None, title="ROASTED"):
    """1200x630 roast card: the boxed phone render on the left, score and the first burns on the right."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    import io as _io
    import textwrap
    img = Image.new("RGB", (1200, 630), (15, 23, 42))
    d = ImageDraw.Draw(img)
    x = 70
    if phone_jpg:
        try:
            ph = Image.open(_io.BytesIO(phone_jpg)).convert("RGB")
            ph.thumbnail((330, 530))  # the whole screen: the boxes can sit anywhere on it
            frame = Image.new("RGB", (ph.width + 20, ph.height + 20), (2, 6, 23))
            frame.paste(ph, (10, 10))
            img.paste(frame, (60, (630 - frame.height) // 2))
            x = 60 + frame.width + 50
        except Exception:  # a broken render never breaks the card
            x = 70
    d.text((x, 50), title, font=_font(30 if len(title) < 22 else 24), fill=(251, 146, 60))
    d.text((x, 92), host[:38], font=_font(24), fill=(148, 163, 184))  # its own line: never cut off
    if title.startswith("PASSES"):  # a pass carries no score
        y = 160
    else:
        d.text((x, 125), f"{score}", font=_font(120), fill=(248, 113, 113) if score < 75 else (250, 204, 21))
        d.text((x + (85 if score < 10 else 160 if score < 100 else 235), 190), "/100", font=_font(38),
               fill=(148, 163, 184))
        y = 275
    width = 34 if x > 300 else 48
    for b in burns[:3]:  # the same three burns as the page
        for line in textwrap.wrap(b, width + 6)[:3]:
            if y > 520:
                break
            d.text((x, y), line, font=_font(25), fill=(241, 245, 249))
            y += 33
        y += 12
    d.text((x, 560), f"Get roasted: {urlparse.urlsplit(SITE).hostname}/roast", font=_font(22), fill=(148, 163, 184))
    buf = _io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def roast_hold_png(host):
    """The share image while there is no phone render: no score, no burns, nothing that can't be checked."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    import io as _io
    img = Image.new("RGB", (1200, 630), (15, 23, 42))
    d = ImageDraw.Draw(img)
    d.text((80, 90), f"ROAST ON HOLD · {host[:34]}", font=_font(40), fill=(251, 146, 60))
    d.text((80, 200), "We only roast what we can show", font=_font(52), fill=(241, 245, 249))
    d.text((80, 270), "boxed on a real phone screenshot.", font=_font(52), fill=(241, 245, 249))
    d.text((80, 380), "This render didn't finish. Try it again.", font=_font(34), fill=(148, 163, 184))
    d.text((80, 560), f"{urlparse.urlsplit(SITE).hostname}/roast", font=_font(26), fill=(148, 163, 184))
    buf = _io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def share_png(host, before, after):
    """1200x630 before/after card for social posts."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    import io as _io
    img = Image.new("RGB", (1200, 630), (15, 23, 42))
    d = ImageDraw.Draw(img)

    def font(size):
        for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default(size=size)

    d.text((70, 70), "Launch Check", font=font(40), fill=(34, 197, 94))
    d.text((70, 150), host[:40], font=font(56), fill=(226, 232, 240))
    d.text((70, 260), f"{before}", font=font(170), fill=(248, 113, 113))
    d.text((420, 300), "→", font=font(120), fill=(148, 163, 184))
    d.text((620, 260), f"{after}", font=font(170), fill=(74, 222, 128))
    d.text((70, 520), "launch score, before and after the Fix Pack", font=font(34), fill=(148, 163, 184))
    buf = _io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def mark_paid(scan_id, url, tier="pack"):
    PAID[scan_id] = {"url": url, "t": time.time(), "tier": tier}
    job = JOBS.get(scan_id)
    if job:
        job["keep_until"] = time.time() + fixpack.PACK_DAYS * 86400
    sys.stderr.write("LC_PAID " + json.dumps({"scan": scan_id, "url": url, "t": int(time.time()), "tier": tier},
                                             separators=(",", ":")) + "\n")


def ensure_pack(job):
    """Start building the Fix Pack for a finished scan (once)."""
    if job.get("pack") or job.get("state") != "done":
        return
    job["pack"] = {"state": "building"}

    def work():
        try:
            rescan = f"{SITE}/fix/{job['id']}"
            pack = fixpack.build_pack(job["report"], rescan)
            if job["id"] == DEMO_ID:
                pack["zip_name"] = "fix-pack-flowpilot-demo.zip"
            job["pack"] = pack
        except Exception as exc:  # never leave the buyer on a spinner
            sys.stderr.write(f"pack failed: {type(exc).__name__}\n")
            job["pack"] = {"state": "failed"}

    threading.Thread(target=work, daemon=True).start()


def demo_job():
    job = JOBS.get(DEMO_ID)
    if job is None or job.get("state") == "error":
        start_job(SITE + DEMO_PATH, "demo", job_id=DEMO_ID, keep=True)
        job = JOBS.get(DEMO_ID)
    return job


class Handler(BaseHTTPRequestHandler):
    server_version = "launch-check"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # one request per connection: no stale keep-alive sockets behind the proxy

    def log_message(self, fmt, *args):  # path only: never the query string, never the body
        path = urlparse.urlsplit(self.path).path
        path = re.sub(r"^/(r|fix|rescan|buy)/[^/]+", lambda m: f"/{m.group(1)}/<id>", path)[:80]
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.command} {path} {getattr(self, '_status', '-')}\n")

    def send(self, status, body, ctype="text/html; charset=utf-8", nonce=None, cache="no-store", extra=None, csp=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("Connection", "close")
        script = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header("Content-Security-Policy", csp or (
                         f"default-src 'none'; script-src {script}; style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
                         "connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
                         # a /buy POST redirects to the checkout host: browsers check form redirects against this list
                         "form-action 'self' https://*.polar.sh https://buy.stripe.com https://checkout.stripe.com; "
                         "base-uri 'none'; "
                         "frame-ancestors 'none'"))
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
        if (path == "/og" or path.startswith("/og/")) and path != "/og/paid":  # OG maker: all in ogmaker.py
            import ogmaker
            return ogmaker.handle_get(self, path, parts.query)
        if path == "/":
            q = urlparse.parse_qs(parts.query)
            v = (q.get("v") or ["launch"])[0]  # the home page speaks to Lovable/Bolt/v0 makers (every channel's audience)
            n = secrets.token_urlsafe(16)
            pre = (q.get("url") or [""])[0].strip()[:2000]
            return self.send(200, page(n, v, url_value=pre, src=(q.get("utm_source") or q.get("src") or [""])[0],
                                       autostart=bool(pre)), nonce=n, cache="no-cache")
        if path == "/roast" or path.startswith("/roast/"):  # the URL is the app: /roast/yoursite.com
            n = secrets.token_urlsafe(16)
            pre = urlparse.unquote(path[len("/roast/"):]).strip()[:2000] if path.startswith("/roast/") else ""
            src = (urlparse.parse_qs(parts.query).get("src") or [""])[0]
            keys = (urlparse.parse_qs(parts.query).get("k") or ["1"])[0] != "0"  # k=0: a link made for someone else
            return self.send(200, page(n, "roast", url_value=pre, src=src, autostart=bool(pre), keys=keys), nonce=n,
                             cache="no-cache")
        if path == "/og/paid" and fixpack.STRIPE_KEY:  # the $2 OG pack's Payment Link comes back here
            import ogmaker
            n = secrets.token_urlsafe(16)
            try:
                ref = fixpack.stripe_paid_ref((urlparse.parse_qs(parts.query).get("session_id") or [""])[0],
                                              int(os.environ.get("LC_OG_CENTS", "200")))
            except Exception:  # Stripe unreachable
                ref = ""
            link = ogmaker.pack_link_for_ref(ref) if ref else None
            if not link:
                return self.send(402, page(n, error="We couldn't confirm the payment yet, or the preview expired. Reload "
                                                    "in a minute; if it still fails, reply to your Stripe receipt."), nonce=n)
            count("og_paid", "dev", "")
            return self.send(303, "", extra={"Location": link})
        if path == "/paid" and fixpack.STRIPE_ON:  # Stripe Payment Link comes back here: verify, unlock, open the pack
            n = secrets.token_urlsafe(16)
            sid_q = (urlparse.parse_qs(parts.query).get("session_id") or [""])[0]
            try:
                scan_id = fixpack.stripe_paid_scan(sid_q)
            except Exception:  # Stripe unreachable: the buyer reloads
                scan_id = ""
            url = (JOBS.get(scan_id) or {}).get("url") or (RESULTS.get(scan_id) or {}).get("url") if scan_id else ""
            if not (scan_id and url):
                return self.send(402, page(n, error="We couldn't confirm the payment yet. Reload this page in a minute; "
                                                    "if it still fails, reply to your Stripe receipt and we'll fix it."),
                                 nonce=n)
            mark_paid(scan_id, url)
            count("checkout_paid", (JOBS.get(scan_id) or {}).get("v", "dev"), "")
            return self.send(303, "", extra={"Location": f"/fix/{scan_id}?token={fixpack.token_for(scan_id)}"})
        if path == "/healthz":
            return self.send(200, "ok", "text/plain; charset=utf-8")
        m = re.fullmatch(r"/r/([A-Za-z0-9_-]+)(/status|/phone|/phone\.jpg|/after\.jpg)?", path)
        if m and JOB_ID.fullmatch(m.group(1)):
            job = JOBS.get(m.group(1))
            if m.group(2) == "/phone":
                ph = (job or {}).get("phone") or LASTING_PHONE.get(m.group(1)) or {"state": "none"}
                if "retry" in urlparse.parse_qs(parts.query) and ph["state"] == "none":
                    if job and job_state(job) == "done":
                        start_phone(job, job["report"])
                        ph = job.get("phone") or ph
                    elif not job and m.group(1) in RESULTS:
                        LASTING_PHONE.pop(m.group(1), None)
                        ph = start_lasting_phone(m.group(1), RESULTS[m.group(1)])
                if ph["state"] == "rendering" and time.time() - ph.get("t", (job or {}).get("t", 0)) > 240:
                    ph = {"state": "none"}
                pc = {k: v for k, v in phone_counts(ph.get("stats")).items() if k not in ("total", "more", "boxed")}
                return self.send(200, json.dumps({"state": ph["state"], **pc}),  # problems, never raw render spots
                                 "application/json")
            if m.group(2) == "/phone.jpg" and not job:
                jpg = (LASTING_PHONE.get(m.group(1)) or {}).get("jpg")
                if not jpg:
                    return self.send(404, "not found", "text/plain; charset=utf-8")
                return self.send(200, jpg, "image/jpeg", cache="private, max-age=600")
            if m.group(2) == "/after.jpg":  # the same screen with one boxed fix applied
                after = (((job or {}).get("phone") or {}).get("stats") or {}).get("_after")
                if not after:
                    return self.send(404, "not found", "text/plain; charset=utf-8")
                return self.send(200, after, "image/jpeg", cache="private, max-age=600")
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
                sm = RESULTS.get(m.group(1))
                if sm and time.time() - sm["t"] < RESULT_DAYS * 86400:
                    # A shared link outlives the in-memory report: re-scan under the same id (~20 s) so the visitor
                    # gets the full result, the Fix Pack preview and checkout, not a summary.
                    if start_job(sm["url"], sm.get("v", "dev"), sm.get("src", ""), job_id=m.group(1), keys=sm.get("k", 1) != 0):
                        count("result_reopened", sm.get("v", "dev"), sm.get("src", ""))
                        return self.send(200, progress_page(m.group(1), JOBS[m.group(1)], n), nonce=n)
                    ph = LASTING_PHONE.get(m.group(1))
                    if ph is None:
                        ph = start_lasting_phone(m.group(1), sm)
                    return self.send(200, lasting_page(sm, n, ph.get("state"), ph.get("stats")), nonce=n)
                return self.send(404, page(n, error="This result is no longer available. Scan your site again below."),
                                 nonce=n)
            state = job_state(job)
            if state == "done" and job["report"].get("job") != m.group(1):
                sys.stderr.write("ISOLATION: report/job mismatch refused\n")
                return self.send(500, page(n, job["v"], error="Something went wrong. Check your app again."), nonce=n)
            if state == "done":
                before = (urlparse.parse_qs(parts.query).get("before") or [""])[0]
                return self.send(200, result_page(job["report"], n, job["v"], job.get("src", ""), m.group(1),
                                                  (job.get("phone") or {}).get("state"),
                                                  (job.get("phone") or {}).get("stats"),
                                                  int(before) if before.isdigit() and int(before) <= 100 else None),
                                 nonce=n)
            if state == "error":
                return self.send(422, page(n, job["v"], url_value=job["url"], error=job.get("error", ""),
                                           src=job.get("src", "")), nonce=n)
            return self.send(200, progress_page(m.group(1), job, n), nonce=n)
        if path == "/manifest.webmanifest":  # installable app (Android/iOS home screen; Play Store later via TWA)
            return self.send(200, json.dumps({
                "name": "Launch Check: your site on a phone", "short_name": "Launch Check", "start_url": "/roast",
                "display": "standalone", "background_color": "#0b1220", "theme_color": "#0b1220",
                "description": "See your site on a real phone, problems boxed, fixes ready for your AI.",
                "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}),
                "application/manifest+json", cache="public, max-age=86400")
        if path in ("/icon-192.png", "/icon-512.png"):
            return self.send(200, (Path(__file__).resolve().parent / "static" / path[1:]).read_bytes(), "image/png",
                             cache="public, max-age=86400")
        if path == "/sw.js":  # minimal service worker: installable, network-first, offline page for the shell
            return self.send(200, SW_JS, "text/javascript; charset=utf-8", cache="no-cache",
                             extra={"Service-Worker-Allowed": "/"})
        if path in ("/favicon.svg", "/favicon.ico"):
            return self.send(200, FAVICON, "image/svg+xml", cache="public, max-age=86400")
        if path == "/inter-var.woff2":
            return self.send(200, (Path(__file__).resolve().parent / "static" / "inter-var.woff2").read_bytes(), "font/woff2",
                             cache="public, max-age=31536000, immutable")
        if path == "/example-phone.jpg":
            ex = Path(__file__).resolve().parent / "static" / "example-phone.jpg"
            if ex.is_file():
                return self.send(200, ex.read_bytes(), "image/jpeg", cache="public, max-age=86400")
        if path == "/og.png":
            og = Path(__file__).resolve().parent / "static" / "og.png"
            if og.is_file():
                return self.send(200, og.read_bytes(), "image/png", cache="public, max-age=86400")
        if path == "/robots.txt":
            return self.send(200, f"User-agent: *\nAllow: /\nDisallow: /r/\nDisallow: /fix/\nDisallow: /*?url=\nSitemap: {SITE}/sitemap.xml\n",
                             "text/plain; charset=utf-8", cache="public, max-age=3600")
        if path == "/sitemap.xml":
            return self.send(200, f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/'
                                  f'schemas/sitemap/0.9"><url><loc>{SITE}/</loc></url><url><loc>{SITE}/roast</loc></url></urlset>', "application/xml",
                             cache="public, max-age=3600")
        if path == "/llms.txt":
            return self.send(200, f"# Launch Check\n\n> {DESC}\n\n- [Check a site]({SITE}/): paste a URL, get a 0-100 "
                                  f"report card.\n- [Open source skill]({GITHUB}): the full check for Claude Code / "
                                  "Cursor.\n", "text/plain; charset=utf-8", cache="public, max-age=3600")
        if path == "/check":
            return self.send(303, "", extra={"Location": "/"})
        sh = re.fullmatch(r"/share/([A-Za-z0-9_-]{20,40})\.png", path)
        if sh:
            q = urlparse.parse_qs(parts.query)
            b = (q.get("b") or [""])[0]
            job = JOBS.get(sh.group(1))
            now = ((job or {}).get("report") or {}).get("score")
            if now is None:
                now = (RESULTS.get(sh.group(1)) or {}).get("score")
            host = ((job or {}).get("host") or urlparse.urlsplit((RESULTS.get(sh.group(1)) or {}).get("final", "")).hostname
                    or "")
            if "roast" in q and now is not None:
                if job and job.get("report"):
                    items = [(f["rule"], scanner.lc.short(f["title"], 90)) for f in other_items(job["report"])]
                else:
                    items = [(x.get("r", ""), x.get("t", "")) for x in (RESULTS.get(sh.group(1)) or {}).get("top3", [])]
                ph = (job or {}).get("phone") or LASTING_PHONE.get(sh.group(1)) or {}
                jpg = ph.get("jpg")
                if job and job.get("report"):
                    items = roast_items(job["report"], ph)
                elif jpg:
                    items = [("phone-" + x.get("key", ""), "") for x in (ph.get("stats") or {}).get("legend") or []] + items
                if not jpg and job and job.get("report") and ph.get("state", "none") == "none" and not job.get("retried"):
                    job["retried"] = True  # one retry: a card without its phone picture renders it for the next fetch
                    start_phone(job, job["report"])
                # no picture, no roast: the card says ON HOLD, with no score and no burns, like the page
                bh = urlparse.urlsplit(((job or {}).get("report") or {}).get("final_url", "")).hostname or host
                lines = roast_lines(items, bh)
                lg = (ph.get("stats") or {}).get("legend") or []
                nbox = len(lg)
                title = "ROASTED"  # same verdict and wording as the page
                if nbox < 2 or len(lines) < 2:
                    nprob = len(other_items(job["report"])) if job and job.get("report") else 0
                    title = (f"{nprob} PROBLEMS FOUND · 1 BOXED" if nprob > 1 else "ONE THING TO FIX ON A PHONE") \
                        if nbox and lines else "PASSES ON A PHONE"
                    lines = lines[:1] if nbox and lines else ["Hard to roast: nothing worth boxing on a phone."]
                png = roast_png(host, now, lines, jpg, title) if jpg else roast_hold_png(host)
                if not png:
                    return self.send(404, "not found", "text/plain; charset=utf-8")
                return self.send(200, png, "image/png", cache="public, max-age=600" if jpg else "public, max-age=60")
            png = share_png(host, int(b), now) if b.isdigit() and int(b) <= 100 and now is not None else None
            if not png:
                return self.send(404, "not found", "text/plain; charset=utf-8")
            return self.send(200, png, "image/png", cache="public, max-age=86400")
        if path == "/admin/code":
            token = (urlparse.parse_qs(parts.query).get("token") or [""])[0]
            if not (STATS_TOKEN and secrets.compare_digest(token.encode(), STATS_TOKEN.encode())):
                return self.send(404, "not found", "text/plain; charset=utf-8")
            code = mint_code()
            return self.send(200, json.dumps({"code": code, "use": f"{SITE}/fix/<scan-id>?code={code}",
                                              "open": sorted(k for k, c in CODES.items() if not c["used"])}),
                             "application/json")
        if path == DEMO_PATH:
            demo = Path(__file__).resolve().parent / "static" / "demo-flowpilot.html"
            return self.send(200, demo.read_bytes(), extra={"X-Robots-Tag": "noindex"}, csp=(
                "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; script-src 'unsafe-inline'; "
                "frame-ancestors 'none'"))
        fx = re.fullmatch(r"/fix/([A-Za-z0-9_-]{4,40})(/status|/render-(375|768|1440)\.jpg|/(fix-pack-[A-Za-z0-9.-]+\.zip))?", path)
        if fx:
            return self.fix_route(fx, urlparse.parse_qs(parts.query))
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

    def fix_route(self, fx, q):
        scan_id, sub = fx.group(1), fx.group(2) or ""
        demo = scan_id == DEMO_ID
        token = (q.get("token") or [""])[0]
        n = secrets.token_urlsafe(16)
        code = (q.get("code") or [""])[0]
        if code and not demo and not fixpack.token_ok(scan_id, token):
            job = JOBS.get(scan_id)
            url = (job or {}).get("url") or (RESULTS.get(scan_id) or {}).get("url")
            if url and use_code(code, scan_id):
                mark_paid(scan_id, url)
                count("code_redeemed", (job or {}).get("v", "dev"), "")
                return self.send(303, "", extra={"Location": f"/fix/{scan_id}?token={fixpack.token_for(scan_id)}"})
        if not demo and not fixpack.token_ok(scan_id, token):
            checkout = (q.get("checkout_id") or [""])[0]
            job = JOBS.get(scan_id)
            if fixpack.CHECKOUT_ON and checkout and job and job.get("state") == "done":
                try:
                    paid = fixpack.polar_checkout_tier(checkout, scan_id)
                except Exception:  # Polar unreachable: let the buyer retry
                    paid = ""
                if paid:
                    mark_paid(scan_id, job["url"], paid)
                    return self.send(303, "", extra={"Location": f"/fix/{scan_id}?token={fixpack.token_for(scan_id)}"})
            if sub == "/status":
                return self.send(200, json.dumps({"state": "locked"}), "application/json")
            return self.send(403, fixpages.locked_page(n), nonce=n)
        job = demo_job() if demo else JOBS.get(scan_id)
        if job is None and not demo:
            info = PAID.get(scan_id)
            if not info:
                return self.send(404, fixpages.locked_page(n, "This Fix Pack has expired"), nonce=n)
            start_job(info["url"], "dev", job_id=scan_id, keep=True)  # regenerate after a restart (~30 s)
            job = JOBS.get(scan_id)
        state = job_state(job)
        pack = job.get("pack") or {}
        if sub == "/status":
            if state == "error":
                return self.send(200, json.dumps({"state": "failed"}), "application/json")
            if state == "done":
                ensure_pack(job)
                return self.send(200, json.dumps({"state": (job.get("pack") or {}).get("state", "building")}),
                                 "application/json")
            return self.send(200, json.dumps({"state": "scanning"}), "application/json")
        if sub.startswith("/render-"):
            jpg = (pack.get("shots") or {}).get(int(fx.group(3)))
            if not jpg:
                return self.send(404, "not found", "text/plain; charset=utf-8")
            return self.send(200, jpg, "image/jpeg", cache="private, max-age=3600")
        if sub.startswith("/fix-pack-"):
            if pack.get("state") != "done" or fx.group(4) != pack.get("zip_name"):
                return self.send(404, "not found", "text/plain; charset=utf-8")
            count("pack_download", job.get("v", "dev"), job.get("src", ""))
            return self.send(200, pack["zip"], "application/zip", cache="private, no-store", extra={
                "Content-Disposition": f'attachment; filename="{pack["zip_name"]}"'})
        if state == "error":
            return self.send(422, fixpages.locked_page(n, job.get("error") or "The scan for this pack failed"), nonce=n)
        if state != "done":
            return self.send(200, fixpages.building_page(scan_id, token, n, "Scanning the site for your Fix Pack…"),
                             nonce=n)
        ensure_pack(job)
        pack = job.get("pack") or {}
        if pack.get("state") == "done":
            if demo:
                count("demo_pack_view", "dev", "")
            return self.send(200, fixpages.pack_page(job["report"], pack, scan_id, token, n, demo,
                                                                  (PAID.get(scan_id) or {}).get("tier") == "dfy"), nonce=n)
        if pack.get("state") == "failed":
            job.pop("pack", None)
            return self.send(500, fixpages.locked_page(n, "The pack could not be built. Reload to try again."), nonce=n)
        return self.send(200, fixpages.building_page(scan_id, token, n, "Building your Fix Pack…"), nonce=n)

    def do_POST(self):
        parts = urlparse.urlsplit(self.path)
        path = parts.path
        if path.startswith("/og/"):  # OG image + favicon maker: all in ogmaker.py
            import ogmaker
            return ogmaker.handle_post(self, path)
        buy = re.fullmatch(r"/buy/([A-Za-z0-9_-]{20,40})", path)
        if buy:
            if not fixpack.CHECKOUT_ON:
                return self.send(303, "", extra={"Location": "/fix/demo"})
            job = JOBS.get(buy.group(1))
            if not job and buy.group(1) in RESULTS:  # an older result: rebuild it, then the page offers checkout again
                return self.send(303, "", extra={"Location": f"/r/{buy.group(1)}"})
            if not job or job.get("state") != "done":
                n = secrets.token_urlsafe(16)
                return self.send(404, page(n, error="This result has expired. Check your site again."), nonce=n)
            try:
                tier = (urlparse.parse_qs(parts.query).get("tier") or ["pack"])[0]
                url = (fixpack.stripe_checkout_url(buy.group(1)) if fixpack.STRIPE_ON else
                       fixpack.polar_checkout_url(buy.group(1), SITE, "dfy" if tier == "dfy" and fixpack.dfy_on() else "pack"))
            except Exception:  # Polar unreachable
                n = secrets.token_urlsafe(16)
                return self.send(502, page(n, error="Checkout is not reachable right now. Try again in a minute."),
                                 nonce=n)
            count("checkout_started", job.get("v", "dev"), job.get("src", ""))
            return self.send(303, "", extra={"Location": url})
        rs = re.fullmatch(r"/rescan/([A-Za-z0-9_-]{4,40})", path)
        if rs:
            sid = rs.group(1)
            token = (urlparse.parse_qs(parts.query).get("token") or [""])[0]
            job = JOBS.get(sid)
            if sid != DEMO_ID and not fixpack.token_ok(sid, token):
                n = secrets.token_urlsafe(16)
                return self.send(403, fixpages.locked_page(n), nonce=n)
            url = (job or {}).get("url") or (PAID.get(sid) or {}).get("url")
            if not url:
                n = secrets.token_urlsafe(16)
                return self.send(404, fixpages.locked_page(n, "This Fix Pack has expired"), nonce=n)
            before = ((job or {}).get("report") or {}).get("score")
            new = start_job(url, (job or {}).get("v", "dev"), (job or {}).get("src", ""))
            if not new:
                n = secrets.token_urlsafe(16)
                return self.send(503, page(n, error="Too many checks right now. Try again in a minute."), nonce=n)
            count("rescan", (job or {}).get("v", "dev"), "")
            return self.send(303, "", extra={"Location": f"/r/{new}" + (f"?before={before}" if before is not None else "")})
        if path == "/webhook/polar":
            length = min(int(self.headers.get("Content-Length") or 0), 200_000)
            body = self.rfile.read(length) if length > 0 else b""
            if not fixpack.polar_webhook_ok(self.headers, body):
                return self.send(403, "bad signature", "text/plain; charset=utf-8")
            try:
                event = json.loads(body)
            except ValueError:
                return self.send(400, "bad json", "text/plain; charset=utf-8")
            if event.get("type") == "order.paid":
                data = event.get("data") or {}
                sid = ((data.get("metadata") or {}).get("scan_id")
                       or ((data.get("checkout") or {}).get("metadata") or {}).get("scan_id") or "")
                job = JOBS.get(sid)
                meta = (data.get("metadata") or {}) or ((data.get("checkout") or {}).get("metadata") or {})
                if sid and job:
                    mark_paid(sid, job["url"], "dfy" if meta.get("tier") == "dfy" else "pack")
            return self.send(202, "ok", "text/plain; charset=utf-8")
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
        kit = bool(STATS_TOKEN) and secrets.compare_digest(self.headers.get("X-LC-Token") or "", STATS_TOKEN)
        ok, wait = (True, 0) if kit else rate_ok(client_ip(self))  # the founder's reply kit pre-runs in bulk
        if not ok:
            return self.send(429, page(n, v, url_value=url, error=f"{RATE_MAX} checks per 10 minutes per visitor. Try again in "
                                                                  f"{max(1, wait // 60)} min."), nonce=n,
                             extra={"Retry-After": str(wait)})
        job_id = start_job(url, v, src, keys=(form.get("k") or ["1"])[0] != "0")
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
    threading.Thread(target=rebuild_stats_from_logs, daemon=True).start()
    if os.environ.get("LC_WARM_DEMO", "1") == "1":
        threading.Thread(target=warm_demo, daemon=True).start()
    srv.serve_forever()


def warm_demo():
    """Build the sample Fix Pack shortly after boot, so the first visitor to /fix/demo doesn't wait ~70 s."""
    time.sleep(8)
    job = demo_job()
    for _ in range(60):
        if job_state(job) in ("done", "error"):
            break
        time.sleep(5)
    if job.get("state") == "done":
        ensure_pack(job)


if __name__ == "__main__":
    main()

"""Launch Check web: paste your URL, get the report card. Python standard library only.

Run: PORT=8000 python app.py   (binds 0.0.0.0:$PORT)
Routes: GET / (form), POST /check (scan), GET /healthz, /robots.txt, /sitemap.xml, /llms.txt, /favicon.svg
Nothing is stored: no database, no files, no analytics. Logs carry the method, path, status and target HOST only.
"""

from __future__ import annotations

import html
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

SITE = os.environ.get("PUBLIC_URL", "https://launch-check.onrender.com").rstrip("/")
GITHUB = "https://github.com/Ces107/agent-forge/tree/main/skills/launch-check"
KIT_WEB = "https://www.agensi.io/bundles/web-design-seo-toolkit-pro-sites-with-ai?utm_source=launchcheck"
KIT_GUARD = "https://www.agensi.io/bundles/claude-code-guardrails-safe-honest-agent-kit?utm_source=launchcheck"
RATE_MAX, RATE_WINDOW = 5, 600          # scans per IP per 10 minutes
CONCURRENCY = threading.BoundedSemaphore(2)
WAIT_FOR_SLOT = 12                      # seconds a visitor waits for a free scanner before "busy"
HARD_WAIT = scanner.BUDGET + 6          # the handler never waits longer than this for a scan
MAX_BODY = 4096
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()

FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
           'fill="#0b1220"/><circle cx="32" cy="32" r="17" fill="none" stroke="#22c55e" stroke-width="7"/>'
           '<path d="M24 33l6 6 11-13" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" '
           'stroke-linejoin="round"/></svg>')

DESC = ("Free pre-launch check for apps built with Lovable, Bolt, v0, Replit or Cursor: leaked API keys, security "
        "headers, phone basics, Google & ChatGPT visibility and speed. No signup, nothing stored.")

CSS = """
:root{--ink:#0b1220;--mut:#5b6678;--line:#e3e8ef;--bg:#f6f8fb;--card:#fff;--acc:#16a34a;--acc2:#22c55e}
*{box-sizing:border-box}html,body{margin:0}body{background:var(--bg);color:var(--ink);
font:16px/1.5 "Inter","Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
.hero{min-height:100svh;display:flex;flex-direction:column;justify-content:center;padding:40px 16px 56px;
background:radial-gradient(1200px 600px at 50% -10%,#dcfce7 0,rgba(220,252,231,0) 60%),var(--bg)}
.in{width:100%;max-width:760px;margin:0 auto}
.brand{font-weight:800;font-size:17px;letter-spacing:-.01em;margin-bottom:28px;display:flex;align-items:center;gap:8px}
.brand i{font-style:normal;color:var(--acc)}.brand img{width:26px;height:26px}
h1{font-size:clamp(38px,8.4vw,72px);line-height:1.02;letter-spacing:-.035em;margin:0 0 26px;font-weight:850}
form{display:flex;gap:10px;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:18px;
padding:10px;box-shadow:0 1px 2px rgba(11,18,32,.05),0 18px 50px rgba(11,18,32,.08)}
input[type=url],input[type=text]{flex:1 1 260px;min-width:0;border:0;outline:0;font:inherit;font-size:18px;
padding:14px 12px;background:transparent;color:var(--ink)}
button{flex:0 0 auto;border:0;border-radius:12px;background:var(--ink);color:#fff;font:inherit;font-weight:750;
font-size:17px;padding:14px 22px;cursor:pointer}button:hover{background:#1e293b}
button[disabled]{opacity:.75;cursor:wait}
.sub{color:var(--mut);margin-top:16px;font-size:15px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:28px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:99px;padding:6px 12px;font-size:13px;
font-weight:650;color:#334155;display:flex;align-items:center;gap:7px}
.chip b{width:9px;height:9px;border-radius:50%;display:inline-block}
.err{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;border-radius:14px;padding:12px 16px;
margin:0 0 16px;font-weight:600}
.bar{height:4px;border-radius:4px;background:var(--line);overflow:hidden;margin-top:14px;display:none}
.bar i{display:block;height:100%;width:0;background:var(--acc2);transition:width 45s linear}
.busy .bar{display:block}.busy .bar i{width:96%}
.foot{color:var(--mut);font-size:13px;margin-top:36px}.foot a{color:inherit}
@media (max-width:520px){.hero{justify-content:flex-start;padding-top:28px;min-height:auto}button{width:100%}form{padding:8px}input[type=url],input[type=text]{font-size:17px}}
"""

FUNNEL_CSS = """
.lc-top{max-width:1080px;margin:0 auto;padding:16px 28px 0;display:flex;justify-content:flex-end}
.lc-top a{color:#0f172a;font-weight:700;text-decoration:none;border:1px solid #e2e8f0;background:#fff;
border-radius:10px;padding:8px 14px;font-size:14px}
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


def esc(s):
    return html.escape(str(s), quote=True)


def page(body, nonce, title="Is your AI-built app safe to launch? | Launch Check", url_value="", error=""):
    err = f'<div class="err" role="alert">{esc(error)}</div>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(DESC)}">
<link rel="canonical" href="{SITE}/"><link rel="icon" href="/favicon.svg" type="image/svg+xml">
<meta property="og:title" content="Is your AI-built app safe to launch?"><meta property="og:description" content="{esc(DESC)}">
<meta property="og:type" content="website"><meta property="og:url" content="{SITE}/">
<meta property="og:image" content="{SITE}/og.png"><meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json" nonce="{nonce}">{{"@context":"https://schema.org","@type":"WebApplication","name":"Launch Check","url":"{SITE}/","applicationCategory":"DeveloperApplication","offers":{{"@type":"Offer","price":"0"}},"description":"{esc(DESC)}"}}</script>
<style>{CSS}</style></head><body>
<main class="hero"><div class="in">
<div class="brand"><img src="/favicon.svg" alt="" width="26" height="26"><span>Launch<i>Check</i></span></div>
<h1>Is your AI-built app safe to launch?</h1>
{err}
<form method="post" action="/check" id="f">
<input type="text" name="url" inputmode="url" autocomplete="url" spellcheck="false" autocapitalize="off" required
 placeholder="https://your-app.lovable.app" value="{esc(url_value)}" aria-label="Your app's address" maxlength="2000">
<button type="submit" id="b">Check my app — free</button>
</form>
<div class="bar" aria-hidden="true"><i></i></div>
<div class="sub">Security · phone basics · Google &amp; ChatGPT · speed. No signup. Nothing stored.</div>
<div class="chips"><span class="chip"><b style="background:#16a34a"></b>Leaked API keys</span>
<span class="chip"><b style="background:#d97706"></b>Security headers</span>
<span class="chip"><b style="background:#2563eb"></b>Google &amp; AI search</span>
<span class="chip"><b style="background:#9333ea"></b>Speed &amp; basics</span></div>
{body}
<div class="foot">Passive and free: it reads only what any visitor's browser downloads. Not a penetration test.
 · <a href="{GITHUB}">Open source</a></div>
</div></main>
<script nonce="{nonce}">
document.getElementById('f').addEventListener('submit',function(){{var b=document.getElementById('b');
document.body.classList.add('busy');setTimeout(function(){{b.disabled=true;b.textContent='Checking… up to 45 s';}},0);}});
</script></body></html>"""


def result_page(report, nonce):
    doc = scanner.render(report)
    prompt_btn = '<button id="lc-copy" type="button">Copy fix list for your AI</button>'
    doc, n = re.subn(r'<button onclick="[^"]*">Copy</button>', prompt_btn, doc, count=1)
    funnel = f"""<section class="lc-next"><h2>Next</h2><div class="lc-cards">
<a href="{GITHUB}" rel="noopener"><b>Fix it with your AI, free</b><span>Run the full Launch Check in Claude Code or
Cursor: real phone test, open-database test on your own site, re-check after each fix.</span> <em>GitHub →</em></a>
<a href="{KIT_WEB}" rel="noopener"><b>Want the fixes done for you?</b><span>Web + SEO kit: landing page, polish and
search fixes, applied by your AI.</span> <em>See the kit →</em></a>
<a href="{KIT_GUARD}" rel="noopener"><b>Building with Claude Code?</b><span>Guardrails kit: stops risky commands,
leaked keys and "done" claims that are not done.</span> <em>See the kit →</em></a>
</div></section>"""
    script = f"""<script nonce="{nonce}">(function(){{var b=document.getElementById('lc-copy');if(!b)return;
b.addEventListener('click',function(){{var t=document.getElementById('p').innerText;
function ok(){{b.textContent='Copied — paste it to your AI';}}
if(navigator.clipboard&&navigator.clipboard.writeText){{navigator.clipboard.writeText(t).then(ok,function(){{fallback();}});}}else{{fallback();}}
function fallback(){{var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();
try{{document.execCommand('copy');ok();}}catch(e){{}}document.body.removeChild(a);}}}});}})();</script>"""
    top = '<div class="lc-top"><a href="/">← Check another app</a></div>'
    doc = doc.replace("</style></head><body>", FUNNEL_CSS + '</style><meta name="robots" content="noindex">'
                      '<link rel="icon" href="/favicon.svg" type="image/svg+xml"></head><body>' + top, 1)
    doc = doc.replace("A good score is not a security audit.</div>",
                      "A good score is not a security audit. Web version: phone design uses basic checks (no "
                      "browser), no open-database test. Nothing about this scan was stored.</div>", 1)
    tail = "</div></body></html>"
    if doc.endswith(tail):
        doc = doc[: -len(tail)] + "</div>" + funnel + script + "</body></html>"
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


def run_scan(url):
    """Returns (report | None, error message | None). Never blocks longer than HARD_WAIT."""
    if not CONCURRENCY.acquire(timeout=WAIT_FOR_SLOT):
        return None, "Two checks are already running. Try again in a minute."
    box = {}

    def work():
        try:
            box["report"] = scanner.scan(url)
        except scanner.ScanError as exc:
            box["error"] = str(exc)
        except Exception:  # never leak a stack trace to the visitor
            box["error"] = "Something went wrong while checking this site. Try again, or try your preview address."
        finally:
            CONCURRENCY.release()

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(HARD_WAIT)
    if t.is_alive():
        return None, "This site took too long to check (the free web check stops at 45 s). Try a lighter page."
    return box.get("report"), box.get("error")


class Handler(BaseHTTPRequestHandler):
    server_version = "launch-check"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # path only: never the query string, never the body
        path = urlparse.urlsplit(self.path).path[:80]
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.command} {path} {getattr(self, '_status', '-')}\n")

    def send(self, status, body, ctype="text/html; charset=utf-8", nonce=None, cache="no-store", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        script = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header("Content-Security-Policy",
                         f"default-src 'none'; script-src {script}; style-src 'unsafe-inline'; img-src 'self' data:; "
                         "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
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
        path = urlparse.urlsplit(self.path).path
        if path == "/":
            n = secrets.token_urlsafe(16)
            return self.send(200, page("", n), nonce=n, cache="public, max-age=300")
        if path == "/healthz":
            return self.send(200, "ok", "text/plain; charset=utf-8")
        if path in ("/favicon.svg", "/favicon.ico"):
            return self.send(200, FAVICON, "image/svg+xml", cache="public, max-age=86400")
        if path == "/og.png":
            og = Path(__file__).resolve().parent / "static" / "og.png"
            if og.is_file():
                return self.send(200, og.read_bytes(), "image/png", cache="public, max-age=86400")
        if path == "/robots.txt":
            return self.send(200, f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n", "text/plain; charset=utf-8",
                             cache="public, max-age=3600")
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
        n = secrets.token_urlsafe(16)
        return self.send(404, page("", n, error="Page not found."), nonce=n)

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
        body = self.rfile.read(length).decode("utf-8", "replace")
        url = (urlparse.parse_qs(body).get("url") or [""])[0].strip()
        n = secrets.token_urlsafe(16)
        try:
            scanner.validate(url)
        except scanner.ScanError as exc:
            return self.send(400, page("", n, url_value=url, error=str(exc)), nonce=n)
        ok, wait = rate_ok(client_ip(self))
        if not ok:
            return self.send(429, page("", n, url_value=url, error=f"5 checks per 10 minutes per visitor. Try again in "
                                                                  f"{max(1, wait // 60)} min."), nonce=n,
                             extra={"Retry-After": str(wait)})
        report, error = run_scan(url)
        host = (urlparse.urlsplit(url if "://" in url else "https://" + url).hostname or "?")[:80]
        if error or not report:
            sys.stderr.write(f"scan {host} refused-or-failed\n")
            return self.send(422, page("", n, url_value=url, error=error or "Could not check this site."), nonce=n)
        sys.stderr.write(f"scan {host} score={report['score']} {report['seconds']}s\n")
        return self.send(200, result_page(report, n), nonce=n)


def main():
    port = int(os.environ.get("PORT", "8000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # nosec B104 - Render requires 0.0.0.0:$PORT
    srv.daemon_threads = True
    sys.stderr.write(f"launch-check web on 0.0.0.0:{port}\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()

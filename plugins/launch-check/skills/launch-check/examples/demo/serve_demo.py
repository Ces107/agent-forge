#!/usr/bin/env python3
"""Seeded demo for launch-check: a typical AI-built landing page (Vite/React-style SPA) served locally.

  python serve_demo.py                 # the "before" app on http://127.0.0.1:8765/
  python serve_demo.py --fixed         # the same app after the founder pasted the fix list to their AI
  python serve_demo.py --port 9000

Seeded defects in the "before" app (each one is common in apps built with AI site builders):
  - a Stripe live secret key of the right SHAPE inside the JavaScript bundle (fake: the string is assembled at
    runtime from parts, so marketplace secret scanners never see a key in this file)
  - a Supabase service_role JWT (fake, signature is junk) next to the normal anon key
  - a mock Supabase REST API (/rest/v1/) whose "profiles" and "orders" tables answer the anon key (no Row Level
    Security); "admin_notes" is protected. Only fake rows; launch-check never prints them.
  - no security headers, a session cookie without flags, the template title, no meta description,
    no favicon, no preview image, a 720 px wide pricing table (phone overflow), a broken /pricing link,
    and a failing API call that logs a JavaScript error.
Nothing here is a real credential. Stop with Ctrl+C.
"""

import argparse
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")


def fake_jwt(role):
    return ".".join([b64({"alg": "HS256", "typ": "JWT"}),
                     b64({"iss": "supabase", "ref": "demolaunchcheck00000", "role": role, "iat": 1760000000}),
                     "DEMO" + "x" * 39])


STRIPE_FAKE = "sk_" + "live_" + "51DEMO" + "NotARealKey" + "LaunchCheckDemo" + "0000000000"
ANON = fake_jwt("anon")
SERVICE = fake_jwt("service" + "_role")

TABLES = {
    "profiles": [{"id": 1, "email": "ana@example.com", "full_name": "Ana Demo", "plan": "pro", "created_at": "2026-10-01"}],
    "orders": [{"id": 7, "user_id": 1, "amount": 4900, "status": "paid"}],
    "admin_notes": [],
}


def markup(fixed):
    nav = '<a href="/#pricing">Pricing</a>' if fixed else '<a href="/pricing">Pricing</a>'
    return f"""<nav><b>FreshPlate</b><span>{nav}<a href="/#faq">FAQ</a></span></nav>
<section class="hero"><h1>Dinner, sorted.</h1><p>Turn what is in your fridge into a week of meals and one
shopping list.</p><a class="btn" href="/#start">Start free</a></section>
<section class="pricing" id="pricing"><div class="plan"><h3>Free</h3><p>3 plans a week</p></div>
<div class="plan"><h3>Pro</h3><p>$9 a month, unlimited plans</p></div><div class="plan"><h3>Family</h3>
<p>$15 a month, 6 people</p></div></section>"""


def page(fixed):
    head_extra = ("<meta name=\"description\" content=\"Plan meals in two minutes: FreshPlate turns what is in your "
                  "fridge into a week of recipes and one shopping list.\">"
                  "<meta property=\"og:title\" content=\"FreshPlate\"><meta property=\"og:description\" "
                  "content=\"A week of meals from what is in your fridge.\"><meta property=\"og:image\" "
                  "content=\"https://freshplate.example/og.png\"><link rel=\"icon\" href=\"/favicon.svg\">") if fixed else ""
    title = "FreshPlate: meal plans from your fridge" if fixed else "Vite + React + TS"
    # before: an empty #root filled by JavaScript (what most AI builders ship); after: prerendered HTML
    root = markup(True) if fixed else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>{head_extra}
<style>
body{{margin:0;font-family:system-ui,sans-serif;color:#0f172a;background:#fff}}
nav{{display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-bottom:1px solid #e2e8f0}}
nav a{{color:#0f172a;text-decoration:none;margin-left:8px;font-weight:600;{'display:inline-block;padding:12px 8px' if fixed else ''}}}
.hero{{padding:56px 20px;text-align:center;background:linear-gradient(135deg,#ecfdf5,#eff6ff)}}
.hero h1{{font-size:40px;margin:0 0 12px}}.hero p{{font-size:18px;color:#334155}}
.btn{{display:inline-block;background:{'#15803d' if fixed else '#22c55e'};color:#fff;padding:14px 26px;border-radius:12px;font-weight:700;text-decoration:none}}
.pricing{{display:flex;gap:16px;padding:40px 20px;{'flex-wrap:wrap' if fixed else 'width:720px'}}}
.plan{{flex:1;min-width:{'200px' if fixed else '220px'};border:1px solid #e2e8f0;border-radius:14px;padding:20px}}
</style></head><body><div id="root">{root}</div>
<script type="module" src="/assets/index-4f9c2a.js"></script></body></html>"""


def bundle(fixed, port):
    secrets = "" if fixed else f'const STRIPE_SECRET="{STRIPE_FAKE}";const SB_SERVICE="{SERVICE}";'
    body = markup(False).replace("`", "")
    return f"""// bundled by vite (demo)
const supabaseUrl="http://127.0.0.1:{port}";const supabaseKey="{ANON}";{secrets}
const root=document.getElementById("root");
if(!root.children.length)root.innerHTML=`{body}`;
fetch("{'/rest/v1/profiles?select=plan&limit=1' if fixed else '/api/me'}",{{headers:{{apikey:supabaseKey}}}})
  .then(r=>{{if(!r.ok)throw new Error("GET /api/me failed: "+r.status)}}).catch(e=>console.error(e.message));
"""


class Handler(BaseHTTPRequestHandler):
    fixed = False
    port = 8765

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype, extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if self.fixed:
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            cookie = "session=demo123; Path=/; HttpOnly; Secure; SameSite=Lax" if self.fixed else "session=demo123; Path=/"
            return self.send(200, page(self.fixed), "text/html; charset=utf-8", {"Set-Cookie": cookie})
        if path == "/assets/index-4f9c2a.js":
            return self.send(200, bundle(self.fixed, self.port), "application/javascript")
        if path == "/favicon.svg" and self.fixed:
            return self.send(200, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><circle cx="4" cy="4" r="4" fill="#16a34a"/></svg>',
                             "image/svg+xml")
        if path == "/rest/v1/":
            spec = {"swagger": "2.0", "paths": {"/": {}, **{f"/{t}": {} for t in TABLES}, "/rpc/do_thing": {}},
                    "definitions": {t: {"properties": {k: {} for k in (rows[0] if rows else {"id": 1})}}
                                    for t, rows in TABLES.items()}}
            return self.send(200, json.dumps(spec), "application/json")
        if path.startswith("/rest/v1/"):
            table = path[len("/rest/v1/"):]
            if table not in TABLES:
                return self.send(404, '{"message":"not found"}', "application/json")
            if self.headers.get("apikey") not in (ANON,):
                return self.send(401, '{"message":"no api key"}', "application/json")
            rows = [] if self.fixed else TABLES[table][:1]
            return self.send(200, json.dumps(rows), "application/json")
        return self.send(404, "<h1>404</h1>", "text/html")

    def do_POST(self):  # the mock refuses every write; launch-check never sends one
        self.send(405, '{"message":"read-only demo"}', "application/json")

    do_PATCH = do_DELETE = do_PUT = do_POST


def make_server(port=8765, fixed=False, host="127.0.0.1"):
    handler = type("DemoHandler", (Handler,), {"fixed": fixed})
    server = ThreadingHTTPServer((host, port), handler)
    handler.port = server.server_address[1]
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--fixed", action="store_true", help="serve the app after the fixes")
    args = ap.parse_args()
    server = make_server(args.port, args.fixed)
    print(f"demo app ({'fixed' if args.fixed else 'before'}) on http://127.0.0.1:{server.server_address[1]}/  (Ctrl+C stops)",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

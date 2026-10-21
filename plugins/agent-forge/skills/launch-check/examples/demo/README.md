# launch-check demo: a seeded AI-built landing page

`serve_demo.py` serves "FreshPlate", a typical single-page landing app of the kind AI site builders produce,
with a mock Supabase REST API on the same local server. Every key in it is fake and assembled at runtime from
parts (so secret scanners never see a key in this folder).

Reproduce (two terminals, from the skill folder):

```
python examples/demo/serve_demo.py                     # before: http://127.0.0.1:8765/
python scripts/launch_check.py http://127.0.0.1:8765/ --owner-check --report before.html
```

```
python examples/demo/serve_demo.py --fixed --port 8766 # after the fix list was applied
python scripts/launch_check.py http://127.0.0.1:8766/ --owner-check --report after.html
```

Seeded in the "before" app: a Stripe live secret key shape and a Supabase service_role JWT in the JS bundle,
`profiles` and `orders` tables readable with the anon key (`admin_notes` is protected), no CSP / X-Frame-Options /
nosniff, a session cookie without HttpOnly/SameSite, the template title "Vite + React + TS", no meta description,
favicon or preview image, a 720 px pricing row (phone overflow), a broken `/pricing` link, a failing API call that
logs JavaScript errors, and all text rendered by JavaScript.

Measured (2026-10-21, Windows 11, Python 3.14, Playwright 1.59): before = 30/100 NOT READY, 8 HIGH, exit 1;
after = 93/100 READY TO LAUNCH, 0 HIGH, exit 0; about 7 s per run.

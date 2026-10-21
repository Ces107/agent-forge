# launch-check

A free Claude Code plugin (MIT) with one skill: a pre-launch report card for apps and sites built with AI
(Lovable, Bolt, Replit, Cursor, v0, Claude). Give it a live URL and it scores four areas 0-100: security
(secret keys visible in the page JavaScript, Supabase service_role keys, open Supabase or Firebase tables,
missing security headers, HTTPS, cookies), phone design at 375 px, being found by Google and AI search,
and speed and basics. It ends with a plain-English "fix this first" list to paste to your AI and a
one-page HTML report.

No install needed to try it: the hosted free version is at https://launch-check-upz2.onrender.com

## Install

```
/plugin marketplace add Ces107/agent-forge
/plugin install launch-check@agent-forge
```

Then ask Claude "is my app ready to launch? https://your-app.com" or run `/launch-check https://your-app.com`.
Other agents (Codex, Cursor, Copilot, Gemini CLI): `npx skills add Ces107/agent-forge --skill launch-check`.

Requires Python 3.9+. Phone design and page-load metrics also need Playwright
(`python -m pip install playwright && python -m playwright install chromium`); without it those checks
are skipped with a note and the rest still runs.

## What it runs

`skills/launch-check/scripts/launch_check.py` (Python standard library), which calls the two bundled audits
`polish_audit.py` (phone design) and `geo_audit.py` (search) with `--json`. Nothing is installed, and no
files are written except the HTML report you ask for with `--report`.

## What it fetches (network disclosure)

Only the URL you pass, and that site's public files:

- the page, the scripts it loads from the same site, links on the home page, `/favicon.ico`,
  `robots.txt`, `sitemap.xml`, `llms.txt`, and up to 5 pages of the same site for the search check;
- with Playwright installed, one headless Chromium visit to the page at phone size, which loads the page
  the way any visitor's browser does (including the assets the page itself requests);
- only with `--owner-check`, and only when you confirm you own the site: the Supabase project
  (`https://<ref>.supabase.co`) or Firebase database the page itself references, read-only, at most 1 row
  per table and 20 tables. Row contents are never printed or saved.

It never probes hidden paths such as `/.env` or `/admin`, never writes to any server, and sends nothing
anywhere else: no telemetry, no analytics, no calls to the hosted version or to any third-party API.
Secrets it finds are cut to their first 4 characters in all output.

## Limits

Not a penetration test: no login flows, no API fuzzing, no server-side auth checks. A good score means
none of these common launch mistakes were found, not that the app is secure. Scripts served from another
host are not scanned.

## License

MIT. Source: https://github.com/Ces107/agent-forge/tree/main/plugins/launch-check

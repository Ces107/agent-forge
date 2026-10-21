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
BUDGET = 42.0          # seconds for the whole scan (the request handler waits a little longer)
SUB_CAP = 30.0         # cap for each vendored audit subprocess
MAX_ASSETS = 15
lc.USER_AGENT = lc.USER_AGENT.replace("passive pre-launch check", "passive pre-launch check, web")
lc.AREAS = [("security", "Security"), ("phone", "Phone design (basic)"), ("search", "Found by Google & AI"),
            ("speed", "Speed & basics")]
lc.MAX_LINKS = 10
lc.LINK_DELAY = 0.2


class ScanError(Exception):
    """A message that is safe to show the visitor."""


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


def run_vendored(script, args, timeout):
    """The CLI's run_vendored, but through run_guarded.py (SSRF guard installed in the child too)."""
    cmd = [sys.executable, str(HERE / "run_guarded.py"), script] + args
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    try:
        run = subprocess.run(cmd, capture_output=True, timeout=max(2.0, timeout), stdin=subprocess.DEVNULL,  # nosec B603
                             env=env, cwd=str(HERE))
    except subprocess.TimeoutExpired:
        return None, "took too long on this site (time limit of the free web check)"
    try:
        return json.loads(run.stdout.decode("utf-8", "replace")), ""
    except json.JSONDecodeError:
        return None, "could not run on this site"


def check_phone_basic(url, timeout):
    report, err = run_vendored("polish_audit.py", [url, "--static", "--json", "--no-shots",
                                                    "--timeout", str(int(REQ_TIMEOUT))], timeout)
    if report is None:
        return None, [], f"skipped: {err}"
    grouped = {}
    for f in report.get("findings", []):
        sev = {"HIGH": "HIGH", "MED": "MEDIUM", "MEDIUM": "MEDIUM"}.get(f.get("severity", ""), "LOW")
        g = grouped.setdefault(f.get("check"), {"sev": sev, "count": 0, "first": f})
        g["count"] += max(1, int(f.get("count", 1) or 1))
        if lc.SEV_ORDER[sev] < lc.SEV_ORDER[g["sev"]]:
            g["sev"] = sev
    out = []
    for check, g in grouped.items():
        f, count = g["first"], g["count"]
        title = lc.PHONE_PLAIN.get(check, f.get("detail", ""))
        out.append(lc.finding("phone", f"phone-{check}", g["sev"], title + (f" ({count} places)" if count > 1 else ""),
                              f"On a phone: {f.get('fix', '')}", where=f.get("selector", ""), detail=f.get("detail", "")))
    out.sort(key=lambda x: lc.SEV_ORDER[x["severity"]])
    return int(report.get("score", 0)), out[:8], ""


def check_search(url, timeout):
    report, err = run_vendored("geo_audit.py", [url, "--json", "--max-pages", "3", "--delay", "0.3",
                                                "--link-budget", "8", "--timeout", str(int(REQ_TIMEOUT))], timeout)
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
    return int(report.get("score", 0)), out[:12], "", {"pages": report.get("pages_crawled")}


def scan(raw_url):
    t0 = time.time()
    deadline = t0 + BUDGET
    url = validate(raw_url)
    fetcher = BudgetFetcher(deadline)
    try:
        resp, chain = fetcher.get(url)
    except guard.BlockedAddress:
        raise ScanError("That address (or a redirect from it) points to a private or reserved address.")
    if resp.status == 0:
        msg = resp.error or "no answer"
        if "refused:" in msg or "private" in msg:
            raise ScanError("That address (or a redirect from it) points to a private or reserved address.")
        if "another site" in msg:
            raise ScanError("The address redirects to another website. Paste the final address of your app.")
        raise ScanError(f"Could not load {lc.clean(url, 120)}: {lc.clean(msg, 100)}")
    if resp.status in (301, 302, 303, 307, 308):
        raise ScanError("The address redirects to another website. Paste the final address of your app.")
    if resp.status >= 400:
        hint = (" The site blocks automated visitors: try your preview or staging address."
                if resp.status in (401, 403, 429, 503) else "")
        raise ScanError(f"{lc.clean(url, 120)} answered HTTP {resp.status}.{hint}")
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
    threads = [threading.Thread(target=bg, args=("phone", lambda: check_phone_basic(final, left)), daemon=True),
               threading.Thread(target=bg, args=("search", lambda: check_search(final, left)), daemon=True)]
    for t in threads:
        t.start()

    notes, skipped = [], {}
    findings, assets = lc.scan_site(fetcher, final, resp, parser, MAX_ASSETS)
    if assets["skipped_offsite"] and not assets["same_origin"]:
        notes.append(f"{assets['skipped_offsite']} scripts load from another host (CDN): not scanned for keys")
    findings += lc.check_headers(resp, final)
    https_f, note = lc.check_https(fetcher, final, chain)
    findings += https_f
    if note:
        notes.append(note)
    findings += lc.check_cookies(resp, final) + lc.check_mixed(parser, final)
    backend = assets.pop("backend", {})
    if backend.get("supabase_urls") or backend.get("firebase_urls"):
        which = "Supabase" if backend.get("supabase_urls") else "Firebase"
        findings.append(lc.finding(
            "security", "db-not-tested", "LOW",
            f"{which} database found in your page: this web check does not test whether anyone can read your tables",
            f"Run the full Launch Check with --owner-check on your own site (free, in Claude Code or Cursor), or open "
            f"the {which} security advisor and confirm every table has access rules."))
        notes.append(f"{which} found: the open-table test runs only in the full Launch Check, on your own site")
    scores = {"security": lc.area_score([f for f in findings if f["area"] == "security"])}
    if any(f["severity"] == "HIGH" for f in findings if f["area"] == "security"):
        scores["security"] = min(scores["security"], 30)

    basic_f, basics_info = lc.check_basics(fetcher, resp, parser, None, assets)
    findings += basic_f
    scores["speed"] = lc.area_score(basic_f)

    for t in threads:
        t.join(timeout=max(0.5, deadline + 3 - time.time()))
    phone = results.get("phone") or (None, [], "skipped: did not finish in time")
    scores["phone"], phone_f, why = phone
    findings += phone_f
    if why:
        skipped["phone"] = why
    search = results.get("search") or (None, [], "skipped: did not finish in time", {})
    scores["search"], search_f, why, search_info = search
    findings += search_f
    if why:
        skipped["search"] = why

    findings.sort(key=lambda f: (lc.SEV_ORDER[f["severity"]], [k for k, _ in lc.AREAS].index(f["area"])))
    overall = lc.combine(scores, findings)
    highs = sum(1 for f in findings if f["severity"] == "HIGH")
    fix_first = [f for f in findings if f["severity"] in ("HIGH", "MEDIUM")][:5]
    sec_high = sum(1 for f in findings if f["severity"] == "HIGH" and f["area"] == "security")
    word, sub = lc.verdict(overall, highs, len(fix_first), sec_high)
    return {
        "tool": "launch-check", "version": lc.VERSION + "-web", "url": lc.clean(url, 300),
        "final_url": lc.clean(final, 300), "seconds": round(time.time() - t0, 1), "mode": "passive, web",
        "score": overall, "verdict": word, "verdict_detail": sub, "pass": overall >= 80 and highs == 0,
        "fail_under": 80,
        "areas": {k: {"label": label, "score": scores.get(k), "grade": lc.grade(scores.get(k)),
                      "note": lc.area_note(k, findings, scores.get(k), skipped.get(k))} for k, label in lc.AREAS},
        "counts": {s.lower(): sum(1 for f in findings if f["severity"] == s) for s in lc.SEV_ORDER},
        "fix_first": fix_first, "findings": findings,
        "facts": {"assets": assets, "basics": basics_info, "search": search_info, "requests_made": fetcher.requests},
        "notes": notes, "skipped": skipped, "screenshot": None,
    }


def fix_list_text(report):
    return lc.ai_prompt(report)


def render(report):
    return lc.render_html(report, None)

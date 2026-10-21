"""SSRF guard tests: run with  python -m pytest web/launch-check/tests -q  (or python -m unittest)."""

import http.client
import http.server
import json as _json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import guard  # noqa: E402
import scan  # noqa: E402  (installs the guard)
import app  # noqa: E402


def serve(handler_cls):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Redirector(http.server.BaseHTTPRequestHandler):
    target = "http://169.254.169.254/latest/meta-data/"

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class Hello(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        Hello.hits += 1
        body = b"<html><title>secret internal page</title></html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class IpRules(unittest.TestCase):
    def test_blocked(self):
        for ip in ["127.0.0.1", "127.8.9.10", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254",
                   "169.254.1.1", "100.64.0.1", "0.0.0.0", "224.0.0.1", "239.255.255.250", "255.255.255.255",
                   "240.0.0.1", "192.0.2.1", "198.18.0.1", "::1", "::", "fe80::1", "fd00::1", "fc00::1",
                   "fd00:ec2::254", "ff02::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:10.0.0.1",
                   "64:ff9b::a9fe:a9fe", "2002:7f00:0001::1", "2002:a9fe:a9fe::1", "2001:db8::1", "::127.0.0.1"]:
            with self.subTest(ip=ip):
                self.assertFalse(guard.ip_allowed(ip), ip)

    def test_allowed(self):
        for ip in ["1.1.1.1", "8.8.8.8", "93.184.215.14", "2606:4700:4700::1111", "::ffff:8.8.8.8"]:
            with self.subTest(ip=ip):
                self.assertTrue(guard.ip_allowed(ip), ip)

    def test_hostnames(self):
        for host in ["localhost", "foo.localhost", "metadata.google.internal", "127.0.0.1", "169.254.169.254",
                     "2130706433", "0x7f000001", "0", "[::1]", "LOCALHOST."]:
            with self.subTest(host=host):
                with self.assertRaises(OSError):
                    guard.resolve_public(host, 80)

    def test_ports(self):
        with self.assertRaises(guard.BlockedAddress):
            guard.resolve_public("1.1.1.1", 22)
        guard.resolve_public("1.1.1.1", 443)


class Connections(unittest.TestCase):
    def test_direct_loopback_refused_before_any_request(self):
        srv = serve(Hello)
        before = Hello.hits
        with self.assertRaises((urllib.error.URLError, OSError)):
            urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/", timeout=3)
        self.assertEqual(Hello.hits, before)
        srv.shutdown()

    def test_metadata_refused(self):
        with self.assertRaises((urllib.error.URLError, OSError)):
            urllib.request.urlopen("http://169.254.169.254/latest/meta-data/", timeout=3)

    def test_file_scheme_refused(self):
        with self.assertRaises(guard.BlockedAddress):
            urllib.request.urlopen(Path(__file__).resolve().as_uri(), timeout=3)

    def test_redirect_to_metadata_refused(self):
        """An allowed server redirects to 169.254.169.254: the hop is re-checked and refused."""
        srv = serve(Redirector)
        guard._EXTRA_ALLOWED.add(("127.0.0.1", srv.server_port))
        try:
            with self.assertRaises((urllib.error.URLError, OSError)) as ctx:
                urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/", timeout=3)
            self.assertIn("refused", str(ctx.exception))
        finally:
            guard._EXTRA_ALLOWED.discard(("127.0.0.1", srv.server_port))
            srv.shutdown()

    def test_redirect_to_private_same_host_refused_in_fetcher(self):
        """Same-site redirect to another (private) port: the launch_check Fetcher follows it, the guard blocks it."""
        inner = serve(Hello)
        Redirector.target = f"http://127.0.0.1:{inner.server_port}/"
        outer = serve(Redirector)
        guard._EXTRA_ALLOWED.add(("127.0.0.1", outer.server_port))
        before = Hello.hits
        try:
            f = scan.BudgetFetcher(deadline=__import__("time").time() + 10)
            resp, chain = f.get(f"http://127.0.0.1:{outer.server_port}/")
            self.assertEqual(resp.status, 0)
            self.assertIn("refused", resp.error)
            self.assertEqual(len(chain), 1)
            self.assertEqual(Hello.hits, before)
        finally:
            guard._EXTRA_ALLOWED.discard(("127.0.0.1", outer.server_port))
            Redirector.target = "http://169.254.169.254/latest/meta-data/"
            outer.shutdown()
            inner.shutdown()


class WebLayer(unittest.TestCase):
    def test_validate_refuses_private(self):
        for url in ["http://127.0.0.1", "http://169.254.169.254", "http://localhost:8000", "http://[::1]/",
                    "http://10.1.2.3/", "file:///etc/passwd", "gopher://example.com", "ftp://example.com",
                    "http://user@example.com/", "https://example.com:22/"]:
            with self.subTest(url=url):
                with self.assertRaises(scan.ScanError):
                    scan.validate(url)

    def test_validate_accepts_public(self):
        self.assertTrue(scan.validate("example.com").startswith("https://example.com"))

    def test_rate_limit(self):
        ip = "203.0.113.77"
        results = [app.rate_ok(ip, now=1000 + i)[0] for i in range(app.RATE_MAX + 1)]
        self.assertEqual(results, [True] * app.RATE_MAX + [False])
        self.assertTrue(app.rate_ok(ip, now=1000 + 601)[0])

    def test_subprocess_child_is_guarded(self):
        """The vendored audits run through run_guarded.py: a private target fails inside the child too."""
        report, err = scan.run_vendored("polish_audit.py", ["http://127.0.0.1/", "--static", "--json", "--no-shots",
                                                             "--timeout", "3"], 20)
        self.assertTrue(report is None or not report.get("findings"))


FAKE_GOOGLE_KEY = "AIza" + "SyFAKEkeyForTestsOnly0123456789abcd"  # 39 chars, not a real key


class FakeApp(http.server.BaseHTTPRequestHandler):
    """A tiny 'AI-built app': the Google key sits only in the same-origin JS bundle."""
    bundle = ""

    def do_GET(self):
        if self.path.startswith("/assets/app.js"):
            body = FakeApp.bundle.encode()
            ctype = "application/javascript"
        else:
            body = (b'<html><head><title>My test app for launch</title><script type="module" src="/assets/app.js">'
                    b'</script></head><body><h1>Hi</h1></body></html>')
            ctype = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def scan_fake(bundle):
    FakeApp.bundle = bundle
    srv = serve(FakeApp)
    guard._EXTRA_ALLOWED.add(("127.0.0.1", srv.server_port))
    try:
        return scan.scan(f"http://127.0.0.1:{srv.server_port}/")
    finally:
        guard._EXTRA_ALLOWED.discard(("127.0.0.1", srv.server_port))
        srv.shutdown()


FIREBASE_BUNDLE = ('const firebaseConfig={apiKey:"%s",authDomain:"demo-app.firebaseapp.com",projectId:"demo-app"};'
                   % FAKE_GOOGLE_KEY)
GEMINI_BUNDLE = ('fetch("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=%s")'
                 % FAKE_GOOGLE_KEY)


class GoogleKey(unittest.TestCase):
    def test_pattern_redacted(self):
        out = scan.lc.secret_findings(scan.lc.scan_text(f'x="{FAKE_GOOGLE_KEY}"', "bundle.js"))
        self.assertEqual(out[0]["rule"], "secret-google-api")
        self.assertNotIn(FAKE_GOOGLE_KEY, out[0]["value"])

    def test_classify(self):
        f = scan.lc.secret_findings(scan.lc.scan_text(f'x="{FAKE_GOOGLE_KEY}"', "b.js"))
        self.assertEqual(scan.classify_google([dict(f[0])], firebase=True)[0]["severity"], "MEDIUM")
        g = scan.classify_google([dict(f[0])], firebase=False)[0]
        self.assertEqual(g["severity"], "HIGH")
        self.assertEqual(g["title"], "Your Google/Gemini API key is visible in your website's code: anyone can use it "
                                     "on your bill")
        self.assertIn("call Gemini from a server function", g["fix"])

    def test_firebase_config_in_bundle_is_medium(self):
        report = scan_fake(FIREBASE_BUNDLE)
        g = [f for f in report["findings"] if f["rule"] == "secret-google-api"]
        self.assertEqual((len(g), g[0]["severity"], g[0]["where"]), (1, "MEDIUM", "/assets/app.js"))
        self.assertIn("restrict it to your domain", g[0]["title"])
        self.assertIn("key to lock down", app.secret_banner(report))

    def test_gemini_key_without_firebase_is_high(self):
        report = scan_fake(GEMINI_BUNDLE)
        g = [f for f in report["findings"] if f["rule"] == "secret-google-api"]
        self.assertEqual((len(g), g[0]["severity"]), (1, "HIGH"))
        self.assertLessEqual(report["score"], 39)
        page = app.result_page(report, "n")
        self.assertIn("Yes: 1 secret key showing", page)
        self.assertIn("things to fix", page)
        self.assertNotIn(FAKE_GOOGLE_KEY, page)


class Hop(http.server.BaseHTTPRequestHandler):
    target = ""

    def do_GET(self):
        self.send_response(301)
        self.send_header("Location", Hop.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class Redirects(unittest.TestCase):
    def test_follows_cross_host_redirect_and_reports_final(self):
        FakeApp.bundle = "console.log(1)"
        dest = serve(FakeApp)
        Hop.target = f"http://127.0.0.1:{dest.server_port}/landing"
        src = serve(Hop)
        for s_ in (src, dest):
            guard._EXTRA_ALLOWED.add(("127.0.0.1", s_.server_port))
        try:
            report = scan.scan(f"http://127.0.0.1:{src.server_port}/")
        finally:
            for s_ in (src, dest):
                guard._EXTRA_ALLOWED.discard(("127.0.0.1", s_.server_port))
                s_.shutdown()
        self.assertEqual(report["final_url"], Hop.target)
        self.assertIn("Checked:</b> " + Hop.target, app.secret_banner(report))

    def test_redirect_hop_to_private_is_refused(self):
        Hop.target = "http://169.254.169.254/latest/meta-data/"
        src = serve(Hop)
        guard._EXTRA_ALLOWED.add(("127.0.0.1", src.server_port))
        try:
            with self.assertRaises(scan.ScanError) as ctx:
                scan.scan(f"http://127.0.0.1:{src.server_port}/")
            self.assertIn("private or reserved", str(ctx.exception))
        finally:
            guard._EXTRA_ALLOWED.discard(("127.0.0.1", src.server_port))
            src.shutdown()

    def test_too_many_hops(self):
        src = serve(Hop)
        Hop.target = f"http://127.0.0.1:{src.server_port}/again"
        guard._EXTRA_ALLOWED.add(("127.0.0.1", src.server_port))
        try:
            with self.assertRaises(scan.ScanError) as ctx:
                scan.scan(f"http://127.0.0.1:{src.server_port}/")
            self.assertIn("more than 5", str(ctx.exception))
        finally:
            guard._EXTRA_ALLOWED.discard(("127.0.0.1", src.server_port))
            src.shutdown()


class ScriptScope(unittest.TestCase):
    def test_registrable(self):
        self.assertEqual(scan.registrable("frontend-assets.supabase.com"), "supabase.com")
        self.assertEqual(scan.registrable("cdn.shop.example.co.uk"), "example.co.uk")
        self.assertEqual(scan.registrable("my-app.vercel.app"), "my-app.vercel.app")

    def test_party(self):
        base = "https://supabase.com/"
        for url, want in [("https://frontend-assets.supabase.com/a.js", "first"), ("https://d1.cloudfront.net/a.js", "first"),
                          ("https://x.supabase.co/storage/v1/object/public/a.js", "first"),
                          ("https://x.supabase.co/rest/v1/", "third"), ("https://www.googletagmanager.com/gtm.js", "third"),
                          ("https://cdn.jsdelivr.net/npm/a", "third"), ("https://js.stripe.com/v3", "third"),
                          ("https://other-site.com/a.js", "third"), ("file:///etc/passwd", "bad"),
                          ("https://user@cdn.supabase.com/a.js", "bad")]:
            with self.subTest(url=url):
                self.assertEqual(scan.script_party(url, base), want)
        self.assertEqual(scan.script_party("https://js.stripe.com/v3", "https://stripe.com/"), "first")


class Presentation(unittest.TestCase):
    def test_variants_render(self):
        for v, needle in [("key", "secret key sitting"), ("stat", "Red Hunt Labs"), ("supabase", "service_role"),
                          ("bogus", "secret key sitting"), ("devtools", "Press F12"), ("bill", "$82,314"), ("gemini", "40 minutes")]:
            with self.subTest(v=v):
                self.assertIn(needle, app.page("n", v))

    def test_supabase_found_is_amber(self):
        out = scan.backend_findings({"supabase_urls": ["https://abcdefghijklmnopqrst.supabase.co"], "anon_keys": ["x"]})
        self.assertEqual(out[0]["severity"], "MEDIUM")
        self.assertTrue(out[0]["title"].startswith("Supabase found: make sure Row Level Security"))

    def test_banner(self):
        base = {"facts": {"assets": {"scanned": 3}}, "url": "https://a.com/", "final_url": "https://a.com/", "seconds": 5}
        self.assertIn("No secret keys found", app.secret_banner({**base, "findings": []}))
        leak = {"rule": "secret-stripe-secret", "severity": "HIGH", "title": "t", "fix": "f", "where": "", "value": ""}
        self.assertIn("Yes: 1 secret key showing", app.secret_banner({**base, "findings": [leak]}))


def _f(area, sev, title, rule="x"):
    return {"area": area, "severity": sev, "title": title, "fix": "fix " + title, "where": "", "value": "",
            "rule": rule, "detail": ""}


REPORT = {"url": "https://a.com/", "final_url": "https://a.com/", "seconds": 5, "score": 70,
          "facts": {"assets": {"scanned": 3}},
          "findings": [_f("security", "MEDIUM", "No CSP", "csp-missing"),
                       _f("phone", "MEDIUM", "Some text is hard to read"), _f("search", "HIGH", "No sitemap"),
                       _f("speed", "MEDIUM", "No meta description"), _f("search", "MEDIUM", "No llms.txt"),
                       _f("phone", "LOW", "Uneven spacing")]}


class Money(unittest.TestCase):
    def test_offer_uses_real_count_and_tracked_link(self):
        html_ = app.offer_block(REPORT, "bill", "reddit")
        self.assertIn("We found 5 things to fix before launch", html_)
        self.assertIn("Phone layout: 1", html_)
        self.assertIn("/go/single-design?v=bill&amp;src=reddit", html_)   # top finding is phone layout
        self.assertIn("Website Design Auditor, $19", html_)
        self.assertIn("/go/kit?v=bill&amp;src=reddit", html_)
        self.assertNotIn("Google &amp; ChatGPT)", html_)  # search area did not run in this report
        self.assertEqual(html_.count("<li>"), 3)
        ran = app.offer_block({**REPORT, "areas": {"search": {"score": 80}},
                               "findings": [_f("search", "HIGH", "No sitemap")]})
        self.assertIn("/go/single-geo?", ran)
        self.assertIn("Everything (design + Google &amp; ChatGPT)", ran)

    def test_offer_when_nothing_else(self):
        clean = {**REPORT, "findings": [REPORT["findings"][0]]}
        self.assertIn("Nothing to fix in design or search", app.offer_block(clean))
        self.assertNotIn("/go/kit?", app.offer_block(clean))

    def test_fix_list_capped_with_kit_line(self):
        text = app.fix_prompt(REPORT, "key", "")
        self.assertIn("Some text is hard to read", text)   # phone / Google first, headers last
        self.assertNotIn("No CSP", text)
        self.assertEqual(sum(1 for line in text.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.", "6.")), 3)
        self.assertIn("the other 2 too", text)
        self.assertTrue(text.rstrip().endswith("/go/kit-fixlist?v=key&src="))

    def test_one_count_everywhere(self):
        page = app.result_page({**REPORT, "score": 70, "verdict": "ALMOST READY", "verdict_detail": "",
                                "areas": {k: {"label": k, "score": 70, "grade": "amber", "note": ""}
                                          for k in ("security", "phone", "search", "speed")}}, "n")
        self.assertIn("We found 5 things", page)
        self.assertIn("top 3 of 5", page)
        self.assertIn("All 5 things to fix", page)
        self.assertNotIn("Uneven spacing", page)  # LOW items are hidden

    def test_high_finding_caps_grade(self):
        f = [_f("phone", "HIGH", "Page wider than the phone", "phone-overflow")]
        self.assertEqual(scan.area_note("phone", scan.things_to_fix(f), None), "Page wider than the phone")

    def test_go_redirect_counts_and_stats_need_token(self):
        app.STATS_TOKEN = "t0ken-for-tests"
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        guard._EXTRA_ALLOWED.add(("127.0.0.1", srv.server_port))
        try:
            def get(path):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
                c.request("GET", path)
                r = c.getresponse()
                return r.status, r.getheader("Location"), r.read()
            before = app.STATS["totals"].get("click:kit", 0)
            st, loc, _ = get("/go/kit?v=stat&src=x")
            self.assertEqual((st, loc), (302, app.GO["kit"]))
            self.assertIn("utm_content=offer", loc)
            self.assertEqual(get("/go/evil")[0], 404)
            self.assertEqual(get("/stats?token=wrong")[0], 404)
            st, _, body = get("/stats?token=t0ken-for-tests")
            data = _json.loads(body)
            self.assertEqual(data["totals"]["click:kit"], before + 1)
            self.assertIn("v=stat,src=x", data["by"]["click:kit"])
            self.assertNotIn("127.0.0.1", body.decode())
        finally:
            guard._EXTRA_ALLOWED.discard(("127.0.0.1", srv.server_port))
            srv.shutdown()

    def test_new_variants(self):
        self.assertIn("look unfinished", app.page("n", "polish"))
        self.assertIn("ready to launch", app.page("n", "ready"))
        self.assertIn("Optional fix kits on Agensi", app.page("n", "key"))
        self.assertIn('name="src" value="reddit"', app.page("n", "key", src="Reddit"))


if __name__ == "__main__":
    unittest.main()

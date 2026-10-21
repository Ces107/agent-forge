"""SSRF guard tests: run with  python -m pytest web/launch-check/tests -q  (or python -m unittest)."""

import http.server
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


class Presentation(unittest.TestCase):
    def test_variants_render(self):
        for v, needle in [("key", "secret key sitting"), ("stat", "Red Hunt Labs"), ("supabase", "service_role"),
                          ("bogus", "secret key sitting"), ("devtools", "Press F12")]:
            with self.subTest(v=v):
                self.assertIn(needle, app.page("n", v))

    def test_supabase_found_is_amber(self):
        out = scan.backend_findings({"supabase_urls": ["https://abcdefghijklmnopqrst.supabase.co"], "anon_keys": ["x"]})
        self.assertEqual(out[0]["severity"], "MEDIUM")
        self.assertTrue(out[0]["title"].startswith("Supabase found: make sure Row Level Security"))

    def test_banner(self):
        ok = {"findings": [], "facts": {"assets": {"scanned": 3}}}
        self.assertIn("No secret keys found", app.secret_banner(ok))
        leak = {"findings": [{"rule": "secret-stripe-secret", "severity": "HIGH"}], "facts": {"assets": {}}}
        self.assertIn("Secret key found", app.secret_banner(leak))


if __name__ == "__main__":
    unittest.main()

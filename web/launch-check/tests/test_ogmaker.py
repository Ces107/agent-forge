"""OG image + favicon maker: run with  python -m pytest -q tests  from web/launch-check."""

import http.client
import io
import sys
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import parse as urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402  (installs the SSRF guard: our test server port is allowed below)
import guard  # noqa: E402
import ogmaker  # noqa: E402

GOOD = {"title": "Ship it faster", "subtitle": "The launch checklist", "site": "example.com", "icon": "🚀",
        "bg": "#0b1220", "accent": "#22c55e", "template": "bar"}


def _server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    guard._EXTRA_ALLOWED.add(("127.0.0.1", srv.server_port))
    return srv


def _req(srv, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=20)
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if body is not None else {}
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


class OgMaker(unittest.TestCase):
    def test_render_is_a_1200x630_png(self):
        from PIL import Image
        for tpl in ogmaker.TEMPLATES:
            img = ogmaker.render(ogmaker.validate(dict(GOOD, template=tpl)))
            self.assertEqual(img.size, (1200, 630))
        srv = _server()
        try:
            status, headers, body = _req(srv, "POST", "/og/render", urlparse.urlencode(GOOD))
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertEqual(Image.open(io.BytesIO(body)).size, (1200, 630))
            status, headers, page = _req(srv, "GET", "/og")
            self.assertEqual(status, 200)
            nonce = headers["Content-Security-Policy"].split("'nonce-")[1].split("'")[0]
            self.assertIn(f'<script nonce="{nonce}">', page.decode())
        finally:
            srv.shutdown()

    def test_pack_has_every_file_and_needs_a_valid_signature(self):
        z = zipfile.ZipFile(io.BytesIO(ogmaker.make_pack(GOOD)))
        self.assertEqual(set(z.namelist()), {"og-image.png", "twitter-image.png", "favicon.ico", "apple-touch-icon.png",
                                             "icon-192.png", "icon-512.png", "site.webmanifest", "meta-tags.html"})
        self.assertIn('content="https://example.com/og-image.png"', z.read("meta-tags.html").decode())
        url = ogmaker.pack_url(GOOD)
        self.assertEqual(ogmaker.check_pack_query(url.split("?", 1)[1])["title"], GOOD["title"])
        srv = _server()
        try:
            status, headers, body = _req(srv, "GET", url)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/zip")
            self.assertIn("og-image.png", zipfile.ZipFile(io.BytesIO(body)).namelist())
            tampered = url.replace("Ship", "Shop")
            self.assertEqual(_req(srv, "GET", tampered)[0], 403)
            self.assertEqual(_req(srv, "GET", ogmaker.pack_url(GOOD, ttl=-10))[0], 403)  # expired
            self.assertEqual(_req(srv, "GET", "/og/pack?t=x")[0], 403)
        finally:
            srv.shutdown()

    def test_bad_input_is_rejected(self):
        bad = [{"title": ""}, {"title": "x" * 71}, {"subtitle": "s" * 121}, {"site": "d" * 61},
               {"bg": "red"}, {"accent": "#12345"}, {"bg": "#0b1220;x"}, {"template": "fancy"},
               {"icon": "abc"}, {"icon": "<b>"}, {"icon": "🚀" * 13}, {"icon": "a🚀"}]
        for change in bad:
            with self.assertRaises(ValueError, msg=change):
                ogmaker.validate(dict(GOOD, **change))
        clean = ogmaker.validate(dict(GOOD, title="  Hi\x00\x1b there  ", icon="ab"))
        self.assertEqual((clean["title"], clean["icon"]), ("Hi there", "AB"))
        srv = _server()
        try:
            status, _, body = _req(srv, "POST", "/og/render", urlparse.urlencode(dict(GOOD, bg="javascript:")))
            self.assertEqual(status, 400)
            self.assertEqual(_req(srv, "POST", "/og/render", "t=" + "x" * 5000)[0], 413)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()

"""A tiny forward proxy that every Chromium request goes through, so the headless browser obeys the same SSRF rules
as the Python fetcher: the PROXY resolves each host (Chromium never does), every address must be public, only ports
80/443, and it connects to the address it checked (no DNS-rebinding gap). Byte and time caps per connection.

Used by run_guarded.py for the phone render: Chromium is started with --proxy-server=127.0.0.1:<port> and
--proxy-bypass-list=<-loopback> (so even localhost goes through the proxy and is refused).
"""

from __future__ import annotations

import select
import socket
import socketserver
import threading
import time

import guard

MAX_BYTES = 12_000_000      # per connection, both directions
CONN_SECONDS = 15           # wall clock per connection
HEADER_MAX = 16_384


def _pipe(a, b, deadline):
    total = 0
    socks = [a, b]
    while time.time() < deadline and total < MAX_BYTES:
        r, _, x = select.select(socks, [], socks, 0.5)
        if x:
            return
        for s in r:
            try:
                data = s.recv(65536)
            except OSError:
                return
            if not data:
                return
            total += len(data)
            (b if s is a else a).sendall(data)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(8)
        deadline = time.time() + CONN_SECONDS
        head = b""
        try:
            while b"\r\n\r\n" not in head and len(head) < HEADER_MAX:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
        except OSError:
            return
        line, _, rest = head.partition(b"\r\n")
        try:
            method, target, version = line.decode("latin-1").split(" ", 2)
        except ValueError:
            return
        upstream = None
        try:
            if method.upper() == "CONNECT":
                host, _, port = target.rpartition(":")
                upstream = guard.guarded_create_connection((host.strip("[]"), int(port)), timeout=8)
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                extra = rest.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in rest else b""
                if extra:
                    upstream.sendall(extra)
            else:
                if not target.lower().startswith("http://"):
                    raise guard.BlockedAddress("refused: only http:// through the proxy")
                hostport, _, path = target[7:].partition("/")
                host, _, port = hostport.rpartition(":") if ":" in hostport.split("]")[-1] else (hostport, "", "80")
                upstream = guard.guarded_create_connection((host.strip("[]"), int(port or 80)), timeout=8)
                body_split = rest.split(b"\r\n\r\n", 1)
                body = body_split[1] if len(body_split) > 1 else b""
                hdr_lines = [h for h in body_split[0].split(b"\r\n") if h and not h.lower().startswith(b"proxy-")]
                req = f"{method} /{path} {version}\r\n".encode("latin-1") + b"\r\n".join(hdr_lines) + b"\r\n\r\n" + body
                upstream.sendall(req)
            _pipe(client, upstream, deadline)
        except (OSError, ValueError):
            try:
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def start():
    """Start the proxy on a free loopback port; returns (server, port)."""
    srv = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def selftest_refuses(host, port=80):
    """Used by tests: True when a CONNECT to host:port through the proxy is refused."""
    srv, p = start()
    try:
        s = socket.socket()
        s.settimeout(5)
        s.connect(("127.0.0.1", p))  # direct socket to our own proxy (not via guard)
        s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        reply = s.recv(200)
        s.close()
        return b" 403 " in reply
    finally:
        srv.shutdown()

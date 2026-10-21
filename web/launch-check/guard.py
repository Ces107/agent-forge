"""SSRF guard for the hosted Launch Check.

install() patches, for the whole process:
  * socket.create_connection (used by http.client, so by every urllib request, including each redirect hop):
    the host is resolved once, EVERY resolved address must be public, and the socket connects to the address
    that was checked (no second DNS lookup, so DNS rebinding cannot swap in a private address).
  * urllib.request.OpenerDirector.open: only http:// and https:// URLs (no file://, ftp://, data:).
  * proxy environment variables are removed (a proxy would hide the real destination).

Refused: loopback, private (RFC 1918, fc00::/7), link-local (169.254.0.0/16 incl. the cloud metadata address,
fe80::/10), shared/CGNAT, multicast, reserved, unspecified, documentation, benchmarking, IPv4 embedded in IPv6
(mapped, NAT64, 6to4, Teredo) when the embedded address is not public, and every port except 80 and 443.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.request

ALLOWED_PORTS = {80, 443}
_EXTRA_ALLOWED: set[tuple[str, int]] = set()  # tests only: (ip, port) pairs to let through
_ORIG_CREATE = socket.create_connection
_ORIG_OPEN = urllib.request.OpenerDirector.open
_INSTALLED = False

METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data", "metadata.azure.com"}
_EXTRA_BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "240.0.0.0/4", "255.255.255.255/32", "169.254.0.0/16",
    "fc00::/7", "fd00::/8", "fe80::/10", "fec0::/10", "ff00::/8", "::/128", "::1/128", "2001:db8::/32",
    "100::/64", "64:ff9b:1::/48")]


class BlockedAddress(OSError):
    """The destination is not a public internet address (or not port 80/443)."""


def ip_allowed(ip) -> bool:
    ip = ipaddress.ip_address(ip) if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ip
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("::/96"):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None and not ip_allowed(embedded):
            return False
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or not ip.is_global):
        return False
    return not any(ip.version == net.version and ip in net for net in _EXTRA_BLOCKED)


def resolve_public(host: str, port: int) -> list[tuple]:
    """getaddrinfo, then refuse unless EVERY address is public. Returns the addrinfo list to connect to."""
    if not host:
        raise BlockedAddress("no host")
    h = host.strip("[]").lower().rstrip(".")
    if h in METADATA_HOSTS or h == "localhost" or h.endswith((".localhost", ".internal", ".local")):
        raise BlockedAddress(f"refused: {h} is not a public internet address")
    try:
        infos = socket.getaddrinfo(h, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OSError(f"could not find the address {h}") from exc
    if not infos:
        raise OSError(f"could not find the address {h}")
    for info in infos:
        ip = info[4][0].split("%", 1)[0]
        if (ip, port) in _EXTRA_ALLOWED:
            continue
        if port not in ALLOWED_PORTS:
            raise BlockedAddress(f"refused: port {port} (only 80 and 443 are checked)")
        if not ip_allowed(ip):
            raise BlockedAddress(f"refused: {h} points to a private or reserved address")
    return infos


def guarded_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kw):
    host, port = address[0], int(address[1])
    infos = resolve_public(host, port)
    last = None
    for family, socktype, proto, _canon, sockaddr in infos:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)  # the checked address, not a fresh lookup
            return sock
        except OSError as exc:
            last = exc
            if sock is not None:
                sock.close()
    raise last or OSError("connection failed")


def guarded_open(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
    url = fullurl if isinstance(fullurl, str) else fullurl.full_url
    if not url.lower().startswith(("http://", "https://")):
        raise BlockedAddress("refused: only http and https")
    return _ORIG_OPEN(self, fullurl, data, timeout)


def install() -> None:
    global _INSTALLED
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(var, None)
    if _INSTALLED:
        return
    socket.create_connection = guarded_create_connection
    urllib.request.OpenerDirector.open = guarded_open
    _INSTALLED = True

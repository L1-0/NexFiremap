"""Where the server is allowed to make outbound requests to.

Several features make the *server* fetch an operator- or feed-supplied URL: the
tile proxy, the WMS/WMTS layer registry, the CAP alert poller. That is a
server-side request forgery surface, and `layers._require_http_url` only ever
checked the scheme - enough to stop `file://` reading the host's disk, but not
to stop `http://127.0.0.1:8000/api/...` or a cloud metadata endpoint.

**The distinction this module is built around** is not "public versus private
address". NexFiremap is a local-first, incident-LAN tool: an operator adding a
district WMS on `192.168.1.40`, or a tile server on the incident LAN, is doing
exactly what the application is for. Blocking RFC1918 outright would break a
supported deployment to close a hole that mostly is not there - an
administrator typing a LAN address is stating intent.

What is *not* intent is a URL that arrived inside a document the server just
downloaded. `AlertManager._poll_feed` follows links out of a CAP index feed, so
a spoofed or compromised upstream can name any address it likes and have the
server connect to it, blind. That is the real vector, and it is why the policy
is per-caller:

    trusted=True   an administrator typed this. Loopback, link-local and the
                   cloud metadata address are still refused; the LAN is allowed.
    trusted=False  this came out of fetched content. The LAN is refused too -
                   nothing a remote feed says should reach inside the perimeter.

Both modes refuse loopback, because "fetch this URL" must never become "call my
own API as though from inside", and refuse link-local 169.254.0.0/16 - which
carries 169.254.169.254, the cloud instance metadata service and the single
most-targeted SSRF destination there is.

**Redirects are re-checked.** A public URL that 302s to `10.0.0.1` defeats any
check done only on the URL the operator supplied, so `guarded_client` installs
the check as an httpx request hook: it fires for every request the client makes,
including each redirect hop, rather than only the first.

DNS is resolved here and every returned address is checked, so a hostname
resolving to several addresses cannot pass on one and connect on another. This
does not close the DNS-rebinding window - the name is resolved again by the
connection itself, and defeating that needs pinning the socket to the checked
address - so this is a strong barrier against SSRF by URL, not a guarantee
against an attacker who also controls a DNS server's TTLs.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger("nexfiremap.netguard")


class BlockedAddressError(ValueError):
    """The URL resolves somewhere the server declines to connect to."""


def _classify(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(address)


def _refuse(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, trusted: bool) -> str | None:
    """The reason to refuse this address, or None to allow it."""
    if ip.is_loopback:
        return "a loopback address"
    if ip.is_link_local:
        # 169.254.169.254 lives here: the cloud metadata service, and the
        # destination almost every real-world SSRF exploit is aimed at.
        return "a link-local address"
    if ip.is_unspecified:
        return "an unspecified address"
    if ip.is_multicast:
        return "a multicast address"
    if ip.is_reserved:
        return "a reserved address"
    # `is_private` covers RFC1918, unique-local fc00::/7 and carrier-grade NAT.
    # Allowed for an administrator - see the module docstring on incident LANs -
    # and refused for anything a remote document asked for.
    if ip.is_private and not trusted:
        return "a private address, which a fetched document may not name"
    return None


def check_url(url: str, *, trusted: bool, field: str = "url") -> None:
    """Raise `BlockedAddressError` if this URL resolves somewhere disallowed.

    :param url: An absolute http(s) URL.
    :param trusted: True when an administrator supplied this URL directly;
        False when it came out of content the server fetched.
    :param field: Name used in the error message.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BlockedAddressError(f"{field} must be an http:// or https:// URL")

    host = parsed.hostname
    try:
        # A literal address needs no DNS; a name gets every address it has.
        addresses = [_classify(host)]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                          proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            # A name that will not resolve is *allowed through* on purpose, to
            # fail as the ordinary network error it is. This matters far more
            # than it looks: with the WAN down - the state this application is
            # built for - every lookup fails, and refusing here would turn each
            # tile fetch into a guard error instead of a connection error,
            # bypassing `TileCache`'s stale-tile fallback and blanking the map.
            # Refusing an unresolvable host buys no security either; there is
            # nothing to connect to.
            log.debug("Allowing unresolvable host %s to fail as a network error", host)
            return
        addresses = []
        for info in resolved:
            try:
                addresses.append(_classify(info[4][0]))
            except ValueError:  # pragma: no cover - getaddrinfo returning a non-address
                continue
        if not addresses:
            raise BlockedAddressError(f"{field}: {host} resolved to no usable address")

    # *Every* address must pass. A name resolving to one public and one private
    # address must not be admitted on the strength of the public one.
    for ip in addresses:
        reason = _refuse(ip, trusted=trusted)
        if reason is not None:
            raise BlockedAddressError(f"{field}: {host} resolves to {reason} ({ip})")


def request_guard(*, trusted: bool):
    """An httpx request event hook enforcing `check_url` on every request.

    Installed as a hook rather than checked once up front because httpx follows
    redirects internally: a permitted public URL answering 302 to an internal
    one would otherwise sail straight through a check done only on the original.
    The hook fires per request, so each hop is checked in turn.

    A refusal is raised as `httpx.RequestError`, deliberately, rather than as
    `BlockedAddressError`. Every caller already wraps its fetches in
    ``except httpx.HTTPError`` to degrade gracefully - `TileCache` falls back to
    a stale tile, `AlertManager` keeps the warnings it has, `LayerRegistry`
    reports an unreachable endpoint. A blocked address *is* a request that did
    not happen, so surfacing it as anything else would sail straight past those
    handlers and turn a refused fetch into an unhandled error - blanking the map
    rather than degrading it.
    """
    import httpx

    async def hook(request) -> None:
        try:
            check_url(str(request.url), trusted=trusted, field="request URL")
        except BlockedAddressError as exc:
            raise httpx.RequestError(str(exc), request=request) from exc

    return hook


__all__ = ["BlockedAddressError", "check_url", "request_guard"]

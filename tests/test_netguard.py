"""Outbound-request policy: what the server will and will not connect to.

Three server-side fetch paths take a URL that did not come from this codebase -
the tile proxy, the WMS/WMTS probe, and the CAP alert poller - and the only
check any of them had was on the URL *scheme*. That stops `file://` reading the
host's disk and nothing else.

The distinction under test is not "public versus private". This is a
local-first, incident-LAN application: an administrator pointing it at a
district WMS on 192.168.1.40 is using the feature as designed. What must not
happen is a URL that arrived *inside a fetched document* naming an internal
address - `AlertManager._poll_feed` follows links out of a CAP index feed, so a
spoofed upstream would otherwise get blind requests against the LAN for free.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.netguard import BlockedAddressError, check_url, request_guard


def main() -> None:
    check_always_refused()
    check_private_depends_on_trust()
    check_public_allowed()
    check_unresolvable_is_not_a_refusal()
    check_bad_schemes()
    check_redirect_hops_are_rechecked()
    check_refusal_is_a_request_error()
    check_wired_into_every_fetch_path()
    print("Outbound request guard checks passed.")


def _blocked(url: str, *, trusted: bool) -> bool:
    try:
        check_url(url, trusted=trusted)
        return False
    except BlockedAddressError:
        return True


def check_always_refused() -> None:
    """Refused whoever asked - an administrator has no legitimate use for these."""
    for url in (
        "http://127.0.0.1:8000/api/settings",   # our own API, from inside
        "http://localhost:8000/api/auth/session",
        "http://[::1]:8000/",
        "http://0.0.0.0/",
        # The cloud instance metadata service: the single most-targeted SSRF
        # destination there is, and the reason link-local is refused outright.
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://224.0.0.1/",                    # multicast
    ):
        for trusted in (True, False):
            assert _blocked(url, trusted=trusted), f"{url} allowed with trusted={trusted}"


def check_private_depends_on_trust() -> None:
    """RFC1918 is the incident LAN: fine when an operator typed it, not when a
    downloaded document did."""
    for url in ("http://192.168.1.40/geoserver/wms",
                "http://10.0.5.9:8080/wmts",
                "http://172.16.4.4/tiles/1/2/3.png"):
        assert not _blocked(url, trusted=True), f"{url} should be reachable for an administrator"
        assert _blocked(url, trusted=False), f"{url} must not be reachable from fetched content"


def check_public_allowed() -> None:
    """The ordinary case must keep working, or the guard has broken the app.

    Literal public addresses, not hostnames: this suite runs offline, and a
    name lookup here would make the test depend on DNS.
    """
    for url in ("https://93.184.216.34/1/2/3.png",   # public IPv4
                "http://8.8.8.8/tiles/1/2/3.png",
                "https://[2606:2800:220:1:248:1893:25c8:1946]/x"):
        for trusted in (True, False):
            assert not _blocked(url, trusted=trusted), f"{url} was blocked with trusted={trusted}"


def check_unresolvable_is_not_a_refusal() -> None:
    """A name that will not resolve must pass the guard, not be refused.

    This is an offline-behaviour requirement, not a security one. With the WAN
    down - the state this application is built for - every lookup fails. If the
    guard refused on that, each tile fetch would raise a guard error instead of
    a connection error, sail past `TileCache`'s ``except httpx.HTTPError``
    stale-tile fallback, and blank a map that was working fine from cache a
    moment earlier. There is also nothing to protect: an unresolvable host
    cannot be connected to.
    """
    assert not _blocked("https://no-such-host.invalid/tiles/1/2/3.png", trusted=True)
    assert not _blocked("https://no-such-host.invalid/cap.xml", trusted=False)


def check_bad_schemes() -> None:
    for url in ("file:///etc/passwd", "ftp://example.org/x", "gopher://example.org/", "not-a-url"):
        assert _blocked(url, trusted=True), url


def check_redirect_hops_are_rechecked() -> None:
    """A public URL that redirects inward must be stopped at the hop.

    This is what makes the check a request hook rather than a one-off
    validation: httpx follows redirects internally, so a check performed only
    on the configured URL never sees where it actually ends up.
    """
    import asyncio

    import httpx

    hook = request_guard(trusted=False)

    async def drive() -> None:
        # The hook is what a real client runs per request; feeding it the
        # redirect *target* is exactly the call httpx would make next.
        await hook(httpx.Request("GET", "https://93.184.216.34/1/2/3.png"))
        for internal in ("http://169.254.169.254/latest/meta-data/",
                         "http://127.0.0.1:8000/api/settings",
                         "http://10.1.2.3/admin"):
            try:
                await hook(httpx.Request("GET", internal))
                raise AssertionError(f"redirect hop to {internal} was allowed")
            except httpx.RequestError:
                pass

    asyncio.run(drive())


def check_refusal_is_a_request_error() -> None:
    """A refused address must look like a failed request to every caller.

    Each fetch site already degrades on ``except httpx.HTTPError`` - stale tile,
    keep existing warnings, report an unreachable endpoint. Raising anything
    else out of the hook would slip past all of them and turn a refusal into an
    unhandled error, which is a worse outcome than the request being refused.
    """
    import asyncio

    import httpx

    hook = request_guard(trusted=False)

    async def drive() -> None:
        try:
            await hook(httpx.Request("GET", "http://127.0.0.1/api/settings"))
        except httpx.HTTPError:
            return
        raise AssertionError("a blocked address did not surface as an httpx error")

    asyncio.run(drive())


def check_wired_into_every_fetch_path() -> None:
    """The guard is only worth anything if the real clients install it.

    Asserted against the constructed clients rather than by reading the source,
    and with the trust level each one should have: the CAP poller follows URLs
    out of fetched documents and must be untrusted, while the tile cache serves
    operator-configured layers and must still reach the LAN.
    """
    import dataclasses
    import tempfile

    from nexfiremap.alerts import AlertManager
    from nexfiremap.config import load_settings
    from nexfiremap.db import Database
    from nexfiremap.tiles import TileCache

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(load_settings(), tile_cache_dir=root / "tiles",
                                       db_path=root / "g.sqlite3",
                                       cap_feeds=["https://example.org/cap.xml"])

        cache = TileCache(settings)
        hooks = cache._client.event_hooks.get("request", [])
        assert hooks, "the tile cache fetches upstream with no address guard installed"

        db = Database(root / "g.sqlite3")
        try:
            import asyncio

            manager = AlertManager(settings, db)
            asyncio.run(manager.start())
            try:
                alert_hooks = manager._client.event_hooks.get("request", [])
                assert alert_hooks, "the CAP poller fetches feed-supplied URLs with no guard"

                # ...and it must be the *untrusted* policy, since those URLs
                # come out of the feed document itself.
                import httpx

                async def probe() -> bool:
                    try:
                        for hook in alert_hooks:
                            await hook(httpx.Request("GET", "http://10.1.2.3/internal"))
                        return False
                    except httpx.RequestError:
                        return True

                assert asyncio.run(probe()), \
                    "the CAP poller would follow a feed link into the LAN"
            finally:
                asyncio.run(manager.stop())
        finally:
            db.close()


if __name__ == "__main__":
    main()

"""Composition root for the NexFiremap HTTP server.

`create_app()` is the sole entry point - it builds one FastAPI app per
call (settings and all per-app state are closed over rather than held at
module scope, so tests can spin up independent instances). The app is:
a `lifespan` that constructs the long-lived services and publishes them on
`app.state`; a single SecurityMiddleware doing auth/session/CSRF/role
gating ahead of every route (see its own docstring for why the checks are
ordered the way they are); a handful of `@app.exception_handler`s that
keep every error response in the same ``{"detail": ...}`` JSON shape the
frontend's fetch helpers all assume; and the routers from
`nexfiremap.routes`, which hold the ~117 route handlers themselves.

Nothing in this module handles a request. Route handlers live in
`routes/` (one module per subject area - see that package's docstring for
the layout), their request bodies in `schemas.py`, and the helpers they
share in `routes/common.py`. Handlers reach the long-lived services
(Database, CacheManager, JobManager, ...) via ``request.app.state``,
wired up once per app in `lifespan()` below - see that function's
docstring for the startup/shutdown contract and the ordering dependencies
between those services.
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

from .backups import BackupManager
from .cache import CacheManager
from .config import STATIC_DIR, Settings, load_settings
from .db import Database
from .jobs import JobManager, JobQueueFull
from .field_import import FieldImportManager
from .drone import DroneError, DroneManager
from .geocode import GeocodeService
from .map_packs import MapPackError, MapPackManager
from .merge import MergeManager
from .offline_sources import OfflineSourceError, OfflineSourceManager
from .products import ProductManager
from .operations import (
    NotFoundError,
    OperationsError,
    OperationsStore,
    PackageConflict,
    RevisionConflict,
)
from .routes import ROUTERS
from .routes.common import AVAILABLE_FEATURES, FEATURE_ERRORS, _json
from .tiles import TileCache
from .tactics import TacticsManager
from .security import SecurityManager
from .telemetry import TelemetryError, TelemetryManager, TelemetryRateLimit
from .wind import WindError, WindManager

log = logging.getLogger("nexfiremap.api")

# Re-exported for backwards compatibility: these moved to routes/common.py
# (routers need them and importing api.py from a router would be circular),
# but `nexfiremap.api.AVAILABLE_FEATURES` was the public name for them.
__all__ = ["create_app", "app", "AVAILABLE_FEATURES", "FEATURE_ERRORS"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return one fully wired NexFiremap FastAPI app.

    `lifespan` and `SecurityMiddleware` are defined inside this function,
    not at module scope, so each call gets its own closed-over `settings`
    - that's what lets tests build multiple independent apps (e.g. one
    per settings fixture) without them sharing module-level state. The
    pieces are assembled in this order: `lifespan` (startup/shutdown of
    the manager singletons on `app.state`), the `FastAPI(...)` instance
    itself, `SecurityMiddleware` (registered via `add_middleware` - it
    still wraps every route regardless of where in this function the
    routers get included, since the middleware stack isn't built until
    the app actually starts serving requests), exception handlers, then
    the routers and the `/static` mount.
    """
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Construct every long-lived service the route handlers rely on,
        publish them onto `app.state` (the only channel a handler has to
        reach them - see e.g. `request.app.state.db` throughout
        `routes/`), start the ones with a background loop/worker, `yield`
        for the life of the server, then stop everything in reverse-ish
        order on shutdown. FastAPI runs this exactly once per app instance
        around the whole run, not per-request, so anything expensive to
        construct (the sqlite connection, background threads) belongs here
        rather than in a route handler.

        Construction order below isn't arbitrary: `db` comes first because
        almost everything else either takes it directly or takes
        `app.state.operations`, which itself wraps `db` - and
        `OperationsStore` has to exist before the incident-domain managers
        (`telemetry`, `drone`, `wind`, `field_imports`, `tactics`,
        `products`, `merges`) that are constructed from it a few lines
        down. `offline_sources` similarly has to exist before `map_packs`
        and `drone`, which both take it as a constructor argument.
        `SecurityMiddleware` (added further below, outside this function)
        reads `app.state.security` on every request, so it must already be
        set by the time `yield` hands control back to Starlette - which it
        is, since everything in this function runs to completion before
        the `yield`.
        """
        if settings.lan_mode and len(settings.admin_password) < 12:
            raise RuntimeError("LAN mode requires NEXFIREMAP_ADMIN_PASSWORD with at least 12 characters")
        db = Database(settings.db_path)
        cache = CacheManager(settings, db)
        tiles = TileCache(settings)
        jobs = JobManager(settings, db)
        backups = BackupManager(settings, db)
        # No corresponding `await geocode.start()` below - unlike
        # cache/tiles/jobs/backups, GeocodeService has no background loop
        # to start, only an HTTP client to close on shutdown (see
        # `geocode.stop()` in the `finally` block).
        geocode = GeocodeService(settings)
        # Every handler that used to close over `create_app`'s `settings`
        # now reads it from here, so this assignment is load-bearing for
        # the routers, not just informational.
        app.state.settings = settings
        app.state.db = db
        app.state.cache = cache
        app.state.tiles = tiles
        app.state.jobs = jobs
        app.state.backups = backups
        app.state.geocode = geocode
        app.state.offline_sources = OfflineSourceManager(settings.tile_cache_dir)
        # map_packs bundles tiles from both the live TileCache and any
        # imported OfflineSourceManager layers, so it needs both to
        # already exist.
        app.state.map_packs = MapPackManager(tiles, app.state.offline_sources)
        # Every manager from here down is built on top of OperationsStore,
        # which is why it's constructed first among them.
        app.state.operations = OperationsStore(db)
        app.state.telemetry = TelemetryManager(app.state.operations, settings)
        app.state.drone = DroneManager(app.state.operations, app.state.offline_sources, settings)
        app.state.wind = WindManager(app.state.operations)
        app.state.field_imports = FieldImportManager(db, app.state.operations)
        app.state.tactics = TacticsManager(app.state.operations)
        app.state.products = ProductManager(db, app.state.operations)
        app.state.merges = MergeManager(app.state.operations)
        # Independent of the operations/incident managers above - only
        # needs settings and db - but must be set before `yield`, since
        # SecurityMiddleware reads app.state.security on every request
        # from here on.
        app.state.security = SecurityManager(settings, db)
        app.state.key_status = {"checked_at": 0.0, "payload": None}
        # Only these four own a background loop/worker pool that needs
        # explicitly starting; every other manager above does its work
        # synchronously (via asyncio.to_thread from route handlers) and
        # needs no start() call.
        await cache.start()
        await tiles.start()
        await jobs.start()
        await backups.start()
        log.info(
            "NexFiremap ready on http://%s:%d (%d job worker%s)",
            settings.host,
            settings.port,
            jobs.worker_count,
            "" if jobs.worker_count == 1 else "s",
        )
        try:
            yield
        finally:
            # Runs even if startup above raised or the server is shutting
            # down after a crash mid-request - `finally` guarantees every
            # background loop gets a chance to stop cleanly and `db` gets
            # closed, rather than leaking a worker thread or an open
            # sqlite handle.
            await backups.stop()
            await cache.stop()
            await tiles.stop()
            await jobs.stop()
            await geocode.stop()
            db.close()

    app = FastAPI(
        title="NexFiremap",
        description="Local NASA FIRMS fire-detection cache and map server.",
        version="1.0.0",
        lifespan=lifespan,
    )

    class SecurityMiddleware(BaseHTTPMiddleware):
        """The whole auth/session/CSRF/role gate for every request, in one
        place so there's a single choke point to audit rather than each
        route handler doing its own checks. The order of the checks below
        matters: a session must be established before any role can be
        read off it, the public-path allowlist has to be evaluated before
        that (those paths are the ones a client hits *before* it has a
        session at all - the login page, the login call itself, health
        checks), and CSRF has to be checked before the write-permission
        check so a request that isn't even a same-site request never gets
        far enough to matter what role it would have needed.

        The allowlist below matches on `request.url.path`, which is
        independent of how routes are registered - splitting the handlers
        across routers changes nothing here.
        """

        async def dispatch(self, request: Request, call_next):
            # getattr with a default, not request.app.state.security
            # directly - during the brief window before lifespan() has
            # finished (or in a test app built without running lifespan
            # at all) app.state.security may not exist yet.
            security: SecurityManager | None = getattr(request.app.state, "security", None)
            path = request.url.path
            # The telemetry ingest endpoint authenticates each request with
            # its own per-feed X-Feed-Token (checked inside the handler
            # itself, see api_position_feed_ingest in routes/feeds.py), not
            # a session cookie - field devices posting positions have no
            # browser session to send. It's carved out of the gate below
            # rather than folded into the may_read/may_write role tables.
            feed_ingest = request.method == "POST" and path.startswith("/api/feeds/positions/")
            # Everything else in this allowlist has to work before a
            # client has ever logged in: the SPA shell and its assets, the
            # health check (used by process supervisors, not a logged-in
            # operator), the login call itself, and /api/config (the
            # frontend reads it to render the login screen before any
            # session exists).
            public_path = feed_ingest or path in {"/", "/health", "/service-worker.js", "/api/auth/login", "/api/config"} or path.startswith("/static/")
            # security is None only in the getattr fallback above; when
            # LAN mode is off, security.enabled is False and single-user
            # loopback mode trusts every request the same way a local CLI
            # tool would (see _require_administrator's docstring) - the
            # entire gate below is skipped.
            if security and security.enabled and not public_path:
                session = security.session(request.cookies.get("nexfiremap_session"))
                if session is None:
                    return _json({"detail": "authentication required"}, 401)
                # Stashed on request.state so route handlers (via
                # _operator()/_require_administrator()) and the account-
                # management check just below can read the caller's
                # identity without re-parsing the cookie themselves.
                request.state.identity = {"username": session.username, "role": session.role}
                # Account management is administrator-only outright - it
                # isn't expressed in the generic may_read/may_write role
                # tables below, so it needs its own check, and that check
                # has to come before the generic may_read call so a role
                # that's otherwise allowed to GET most paths still can't
                # list/create accounts.
                if path.startswith("/api/auth/accounts") and session.role != "administrator":
                    return _json({"detail": "administrator role required"}, 403)
                if request.method in {"GET", "HEAD"} and not security.may_read(session.role, path):
                    return _json({"detail": "role is not permitted to read this resource"}, 403)
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    # Double-submit CSRF check: the token was handed to the
                    # frontend at login (session.csrf) and must be echoed
                    # back in a header on every state-changing request - a
                    # cross-site form/script can forge the cookie-borne
                    # session but has no way to read this header's value.
                    # compare_digest avoids leaking the token a byte at a
                    # time through response-timing differences.
                    if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf):
                        return _json({"detail": "CSRF token required"}, 403)
                    # Logout is exempted from the write-permission table -
                    # every authenticated role must always be able to log
                    # itself out, even one with no other write permissions
                    # at all.
                    if path != "/api/auth/logout" and not security.may_write(session.role, path):
                        return _json({"detail": "role is not permitted for this operation"}, 403)
            response = await call_next(request)
            # StaticFiles (the /static mount) sets Last-Modified/ETag but no
            # Cache-Control at all, so a browser's own heuristic freshness
            # guess can keep serving an old app.js/app.css/operations.js
            # indefinitely after a server update - no network request at
            # all, not even a conditional one - with nothing to signal
            # anything changed. Same bug already fixed for / and
            # /service-worker.js (see routes/static_files.py); this covers
            # every other static asset the same way. no-cache (not
            # no-store) still lets the browser skip re-downloading unchanged
            # bytes via a 304, it just can't skip *asking*. Deliberately
            # applies to vendored third-party libraries too, not just this
            # project's own files - they get re-fetched in place on a
            # version bump (scripts/vendor_assets.py) without their URL
            # changing, so the same staleness risk applies to them.
            # setdefault, not a plain assignment, so a route that already
            # set a more specific Cache-Control (job result files, a couple
            # of long-lived assets) isn't overridden.
            if path.startswith("/static/"):
                response.headers.setdefault("Cache-Control", "no-cache")
            # These apply to every response that actually reaches a route
            # handler (the 401/403s returned early above skip this and go
            # out with only Starlette's default headers).
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; worker-src 'self' blob:")
            if settings.tls_cert_file:
                # Only when TLS is actually configured - HSTS on a plain-HTTP
                # LAN deployment would make browsers refuse to ever connect
                # over HTTP again, breaking exactly the deployment this
                # header must stay silent for. When TLS is on, it still
                # matters on a LAN: an attacker on the same network segment
                # (ARP spoofing, a rogue AP) can otherwise strip TLS on a
                # repeat visit by intercepting the first plain-HTTP redirect.
                response.headers.setdefault("Strict-Transport-Security", "max-age=15552000; includeSubDomains")
            return response

    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> Response:
        # Without this, any bug the routers don't already turn into an
        # HTTPException falls through to Starlette's default plain-text 500
        # - breaking the "thin JSON client" contract the frontend assumes
        # for every other response (see app.js's fetch helpers, which all
        # expect a JSON body even on failure). HTTPException itself keeps
        # using FastAPI's own handler (matched by the more specific type
        # first), which already returns this same {"detail": ...} shape.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _json({"detail": "Internal server error"}, status_code=500)

    @app.exception_handler(OperationsError)
    async def _operations_exception(request: Request, exc: OperationsError) -> Response:
        if isinstance(exc, PackageConflict):
            return _json(
                {"detail": str(exc), "code": "package_conflict", "report": exc.report},
                status_code=409,
            )
        if isinstance(exc, NotFoundError):
            return _json({"detail": str(exc)}, status_code=404)
        if isinstance(exc, RevisionConflict):
            return _json(
                {"detail": str(exc), "code": "revision_conflict", "current": exc.entity},
                status_code=409,
            )
        return _json({"detail": str(exc)}, status_code=400)

    @app.exception_handler(TelemetryError)
    @app.exception_handler(DroneError)
    @app.exception_handler(WindError)
    async def _sensor_exception(request: Request, exc: Exception) -> Response:
        return _json({"detail": str(exc)}, status_code=400)

    @app.exception_handler(TelemetryRateLimit)
    async def _telemetry_rate_limit(request: Request, exc: TelemetryRateLimit) -> Response:
        return _json({"detail": str(exc)}, status_code=429)

    @app.exception_handler(JobQueueFull)
    async def _job_queue_full(request: Request, exc: JobQueueFull) -> Response:
        return _json({"detail": str(exc)}, status_code=429)

    @app.exception_handler(MapPackError)
    async def _map_pack_exception(request: Request, exc: MapPackError) -> Response:
        return _json({"detail": str(exc)}, status_code=400)

    @app.exception_handler(OfflineSourceError)
    async def _offline_source_exception(request: Request, exc: OfflineSourceError) -> Response:
        return _json({"detail": str(exc)}, status_code=400)

    # Every route handler in the app, grouped by subject area - see
    # nexfiremap/routes/__init__.py for the include order and what lives
    # where.
    for router in ROUTERS:
        app.include_router(router)

    # Mounted last so it can't shadow a declared route; the two static
    # files served by hand (/, /service-worker.js) are in
    # routes/static_files.py, and everything else under /static/ comes
    # from here.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()

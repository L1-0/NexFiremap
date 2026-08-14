"""The two hand-served static files: the SPA shell and the service worker.

Everything else under `/static/` is served by the `StaticFiles` mount
registered in `create_app`. These two are routes instead because they
need an explicit `Cache-Control` and, for the service worker, a
`media_type` - and because both are on `SecurityMiddleware`'s public
allowlist, since a client fetches them before it can possibly have a
session.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..config import STATIC_DIR

router = APIRouter()


@router.get("/")
async def index() -> FileResponse:
    # No explicit header here used to mean "whatever the browser's own
    # heuristic freshness guesses" (FileResponse sets Last-Modified but
    # no Cache-Control), so a client could keep serving a stale app
    # shell after a server update with no signal anything changed,
    # discovered live: editing this exact file mid-session still showed
    # the old markup until a cache-busted reload. no-cache (not
    # no-store) still lets the browser revalidate cheaply via a
    # conditional GET, it just cannot skip asking - same rule already
    # applied to /service-worker.js below for the same reason.
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@router.get("/service-worker.js")
async def service_worker() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "service-worker.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )

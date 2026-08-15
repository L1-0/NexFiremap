"""Every HTTP route in NexFiremap, grouped by subject area.

`create_app()` in `api.py` owns the app object, its middleware, its
exception handlers and the lifespan that builds the long-lived services;
this package owns nothing but `APIRouter`s. The dependency runs one way -
`api` imports `routes`, `routes.*` imports `routes.common`, and no module
here imports `api` - which is what keeps the composition root free to
change without touching handlers.

Handlers reach shared services the same way they always have, through
``request.app.state`` (`db`, `jobs`, `operations`, `settings`, ...), all
of which `lifespan()` publishes before the first request is served. There
is deliberately no `Depends()` indirection layer: `request.app.state` is
already the one uniform accessor, and every handler in this package uses
it.

`ROUTERS` is the include order `create_app()` walks. Ordering is mostly
cosmetic - it decides how endpoints group in the generated OpenAPI docs -
with **one exception that is genuinely load-bearing**: `links` must be
included before `incidents`, because it declares literal path segments
where `incidents` declares a parameter
(`/api/operations/incidents/covering` vs
`/api/operations/incidents/{incident_id}`). Starlette resolves
first-match-wins, so the wrong order routes `covering` into the incident
lookup and answers a confusing 404. See the comment on that entry below.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    alerts,
    auth,
    backups,
    cache,
    coverage,
    detections,
    drone,
    eumetsat,
    events,
    features,
    feeds,
    field_imports,
    incidents,
    industrial,
    jobs,
    layers,
    links,
    map_packs,
    meta,
    offline_sources,
    products,
    resources,
    static_files,
    structures,
    tactics,
    tiles,
    transfer,
    webhooks,
)

# Roughly the order the original single-file api.py declared them, which
# is also roughly the order the frontend calls them: session and server
# metadata first, then the incident/operations domain, then the
# fire-data reads, then infrastructure, then the static shell last.
ROUTERS: tuple[APIRouter, ...] = (
    auth.router,
    meta.router,
    backups.router,
    offline_sources.router,
    map_packs.router,
    transfer.router,
    field_imports.router,
    # links MUST precede incidents. It declares literal paths that sit where
    # incidents declares a parameter - `/api/operations/incidents/covering`
    # against `/api/operations/incidents/{incident_id}` - and Starlette matches
    # first-registered-wins, so the other order silently routes `covering` into
    # the incident lookup and answers 404 "incident not found". This is the one
    # place in the app where include order is load-bearing rather than cosmetic.
    links.router,
    incidents.router,
    features.router,
    tactics.router,
    resources.router,
    feeds.router,
    drone.router,
    products.router,
    detections.router,
    coverage.router,
    events.router,
    structures.router,
    industrial.router,
    eumetsat.router,
    alerts.router,
    cache.router,
    layers.router,
    tiles.router,
    jobs.router,
    webhooks.router,
    static_files.router,
)

__all__ = ["ROUTERS"]

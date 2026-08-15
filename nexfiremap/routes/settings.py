"""Operator-editable settings: read, write, clear.

Administrator-only, all three, including the read. That is stricter than most
read routes get and it is deliberate for the same reason `routes/layers.py`
gates its listing: these rows concern credentials. Even though `public_view`
never returns a key's value, the shape of the answer - which services are
configured, which came from `.env` and which an operator typed in - is
reconnaissance, and there is no operational need for a non-administrator to
see it.

What the *whole room* does need is the shared display preference (coordinate
format), and that is deliberately not here: it rides along on `/api/config`,
which every client already fetches at startup, so a browser picks up the
incident's chosen coordinate framework without asking for anything extra and
without being administrator.

The write path stores into `app.state.setting_overrides` as well as the
database, so `/api/config` reflects a change immediately. What it cannot do is
re-make the long-lived managers that read `Settings` at construction time, so
the response names the fields that need a restart instead of pretending the
change took effect. See `settings_store.RESTART_REQUIRED`.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .. import settings_store
from ..schemas import SettingsUpdateRequest
from .common import _json, _operator, _require_administrator

router = APIRouter()


def _view(request: Request, overrides: dict) -> dict:
    """The settings payload, identical in shape from all three handlers.

    Shared rather than built per-handler because the client re-renders the pane
    from whatever a write returns. A response that omitted `symbology_profiles`
    would leave the profile `<select>` with no options after a save, and the
    next save would then post an empty profile - which is exactly what happened
    when the read route built this on its own.
    """
    from ..symbology import PROFILES, profile_label

    view = settings_store.public_view(request.app.state.settings, overrides)
    # The symbology profile is a closed vocabulary, so the UI should render a
    # picker rather than a free-text box that can be typo'd into a fallback.
    view["symbology_profiles"] = [{"id": name, "label": profile_label(name)}
                                  for name in sorted(PROFILES)]
    return view


@router.get("/api/settings")
async def api_settings(request: Request) -> Response:
    """Current effective settings, with credentials masked to a hint."""
    _require_administrator(request)
    db = request.app.state.db
    overrides = await asyncio.to_thread(settings_store.read_overrides, db)
    request.app.state.setting_overrides = overrides
    return _json(_view(request, overrides))


@router.put("/api/settings")
async def api_settings_update(request: Request, body: SettingsUpdateRequest) -> Response:
    """Store settings. Returns which of them need a restart to take effect."""
    _require_administrator(request)
    db = request.app.state.db
    try:
        restart = await asyncio.to_thread(settings_store.write, db, body.values, _operator(request))
    except settings_store.SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc

    overrides = await asyncio.to_thread(settings_store.read_overrides, db)
    request.app.state.setting_overrides = overrides
    # Fold the new values over the *original* environment-derived settings, not
    # over the currently applied ones - otherwise clearing an override could
    # never restore the environment value beneath it, because the environment
    # value would already have been overwritten in memory.
    request.app.state.settings = settings_store.apply_overrides(
        request.app.state.base_settings, overrides)
    view = _view(request, overrides)
    view["restart_needed_for"] = restart
    return _json(view)


@router.delete("/api/settings/{name}")
async def api_settings_clear(request: Request, name: str) -> Response:
    """Drop one override, falling back to whatever `.env` supplies beneath it."""
    _require_administrator(request)
    db = request.app.state.db
    try:
        await asyncio.to_thread(settings_store.clear, db, name, _operator(request))
    except settings_store.SettingsError as exc:
        raise HTTPException(404, str(exc)) from exc

    overrides = await asyncio.to_thread(settings_store.read_overrides, db)
    request.app.state.setting_overrides = overrides
    request.app.state.settings = settings_store.apply_overrides(
        request.app.state.base_settings, overrides)
    return _json(_view(request, overrides))

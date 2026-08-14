"""Full-database backup and recovery management (administrator only).

Every handler here calls `_require_administrator` explicitly. That is not
redundant with `SecurityMiddleware`: a backup is a full-database export -
every incident, every account's password hash, everything regardless of
classification - not the incident-scoped data the middleware's generic
"any non-public role may read" rule was written for.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..backups import BackupManager
from ..schemas import RecoveryCreateRequest
from .common import _json, _require_administrator

router = APIRouter()


@router.get("/api/operations/backups")
async def api_backups(request: Request) -> Response:
    # A backup is a full-database export - every incident, every
    # account's password hash, everything regardless of classification -
    # not the incident-scoped data may_read's generic "any non-public
    # role" rule was written for. List and download both need the
    # explicit administrator check the generic role/path gating doesn't
    # give them.
    _require_administrator(request)
    backups: BackupManager = request.app.state.backups
    return _json({"status": backups.status(), "backups": backups.list_backups()})


@router.post("/api/operations/backups")
async def api_backup_create(request: Request) -> Response:
    _require_administrator(request)
    backups: BackupManager = request.app.state.backups
    result = await asyncio.to_thread(backups.create_backup, "manual")
    return _json(result, 201)


@router.post("/api/operations/backups/{name}/verify")
async def api_backup_verify(request: Request, name: str) -> Response:
    _require_administrator(request)
    backups: BackupManager = request.app.state.backups
    try:
        result = await asyncio.to_thread(backups.verify, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "backup not found") from exc
    return _json(result)


@router.get("/api/operations/backups/{name}/download")
async def api_backup_download(request: Request, name: str) -> Response:
    _require_administrator(request)
    backups: BackupManager = request.app.state.backups
    try:
        path = backups.path_for_download(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "backup not found") from exc
    return FileResponse(
        path, media_type="application/vnd.sqlite3",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/operations/recoveries")
async def api_recoveries(request: Request) -> Response:
    _require_administrator(request)
    backups: BackupManager = request.app.state.backups
    return _json(await asyncio.to_thread(backups.list_recoveries))


@router.post("/api/operations/recoveries")
async def api_recovery_create(request: Request, body: RecoveryCreateRequest) -> Response:
    _require_administrator(request)
    backups: BackupManager = request.app.state.backups
    try:
        result = await asyncio.to_thread(backups.create_recovery, body.backup_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "backup not found") from exc
    return _json(result, 201)


@router.get("/api/operations/recoveries/{name}/download")
async def api_recovery_download(request: Request, name: str) -> Response:
    _require_administrator(request)
    backups: BackupManager = request.app.state.backups
    try:
        path = backups.recovery_path_for_download(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "recovery database not found") from exc
    return FileResponse(path, media_type="application/vnd.sqlite3", filename=path.name,
                        headers={"Cache-Control": "no-store"})

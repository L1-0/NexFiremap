"""Ingest of field-collected observation files (GPX/KML/CSV/GeoJSON).

Preview and apply take the same request body deliberately: the operator
previews, adjusts the column `mapping`/`default_feature_type`, and
re-posts the identical payload to apply - so the thing that was reviewed
is exactly the thing that gets written. The original uploaded bytes are
retained and served back by the `/original` route, which is what makes an
import auditable after the fact.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..field_import import FieldImportManager
from ..schemas import FieldImportRequest
from .common import _json, _model_payload, _operator

router = APIRouter()


@router.post("/api/operations/incidents/{incident_id}/field-imports/preview")
async def api_field_import_preview(
    request: Request, incident_id: str, body: FieldImportRequest,
) -> Response:
    manager: FieldImportManager = request.app.state.field_imports
    report, _ = await asyncio.to_thread(manager.prepare, incident_id, _model_payload(body))
    return _json(report)


@router.post("/api/operations/incidents/{incident_id}/field-imports/apply")
async def api_field_import_apply(
    request: Request, incident_id: str, body: FieldImportRequest,
) -> Response:
    manager: FieldImportManager = request.app.state.field_imports
    actor = _operator(request)
    result = await asyncio.to_thread(manager.apply, incident_id, _model_payload(body), actor)
    return _json(result, 201)


@router.get("/api/operations/incidents/{incident_id}/field-imports")
async def api_field_imports(request: Request, incident_id: str) -> Response:
    manager: FieldImportManager = request.app.state.field_imports
    rows = await asyncio.to_thread(manager.list_imports, incident_id)
    for row in rows:
        row["report"] = json.loads(row.pop("report_json"))
    return _json(rows)


@router.get("/api/operations/incidents/{incident_id}/field-imports/{import_id}/original")
async def api_field_import_original(
    request: Request, incident_id: str, import_id: str,
) -> Response:
    manager: FieldImportManager = request.app.state.field_imports
    filename, data = await asyncio.to_thread(manager.original, incident_id, import_id)
    safe_name = Path(filename).name.replace('"', "") or "source-data"
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}"',
                             "Cache-Control": "no-store"})

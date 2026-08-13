# Vehicle telemetry and drone imagery - implementation plan

Status: implementation checklist for the incident-LAN groundwork. The design is deliberately provider-neutral and has no mobile-network dependency.

## Operational contract

- [x] Accept replay-safe batches of vehicle/GPS observations through a source-specific token without requiring an interactive user session.
- [x] Preserve the received payload, receive time, observation time and SHA-256. Never replace raw observations with a smoothed track.
- [x] Validate coordinates, timestamps and identifiers. Label late, out-of-order, inaccurate and physically implausible observations.
- [x] Expose latest positions, freshness, temporal tracks and explicitly estimated interpolation to authenticated planners.
- [x] Break tracks at time gaps and implausible jumps so a map cannot draw a trustworthy-looking line through missing/bad data.
- [x] Permit revocation and token rotation. Store only token digests and show a new token once.
- [x] Create drone missions and ingest bounded JPEG/PNG/TIFF stills while preserving originals and provenance.
- [x] Treat unreferenced/oblique imagery as evidence only. Create a map layer only when an operator supplies four ordered WGS84 ground corners for a nadir/orthorectified image.
- [x] Create deterministic north-up GeoTIFF derivatives, thumbnails, footprints and multi-image mosaics, registered in the existing offline tile catalogue.
- [x] Enforce incident ownership, roles, CSRF for interactive mutations, feed-token isolation, bounded request/image/pixel sizes and audit records.
- [x] Exercise all APIs and algorithms with malformed input, replay, temporal ordering, quality, georeferencing, clean-root relocation and offline tile tests.

## Data and trust model

Vehicle feed sources are administrator-created incident objects. A random bearer secret is returned only at creation/rotation. The database retains a salted-independent SHA-256 digest. A sender supplies a stable `external_id` per observation. The pair `(source_id, external_id)` is idempotent: an identical retry is a replay, while reuse with different content is rejected as a conflict.

Every accepted report is immutable and records its canonical payload hash. Derived flags include clock skew, out-of-order arrival, poor accuracy and implausible segment speed. Latest-position and track responses carry age/freshness and quality. Interpolation is returned as `estimated: true` and is never written back as an observation. No automatic position is converted into a confirmed tactical observation.

Drone uploads retain the original file, content hash, operator metadata and a small JPEG preview. Four corners are ordered top-left, top-right, bottom-right, bottom-left in the image's display orientation. The service applies EXIF orientation before derivation, then warps through four ground-control points into a north-up WGS84 GeoTIFF. The footprint and a provenance chain identify the source image and algorithm. Images without acceptable ground control remain downloadable but cannot silently appear as geospatial truth.

Mosaics use only georeferenced assets from one incident, order inputs deterministically, calculate an output-size bound before allocation, and register the result as a local raster source. Overlaps are composited in stable source order. This is an operational visual mosaic, not photogrammetric bundle adjustment or a survey-grade orthomosaic.

## API surface

- `POST/GET /api/operations/incidents/{incident}/position-feeds`
- `POST /api/operations/incidents/{incident}/position-feeds/{source}/rotate-token`
- `PATCH /api/operations/incidents/{incident}/position-feeds/{source}` (enable/revoke and metadata)
- `POST /api/feeds/positions/{source}` using `X-Feed-Token`
- `GET /api/operations/incidents/{incident}/vehicle-positions/latest`
- `GET /api/operations/incidents/{incident}/vehicle-tracks`
- `GET /api/operations/incidents/{incident}/vehicle-positions/interpolate`
- `POST/GET /api/operations/incidents/{incident}/drone-missions`
- `POST/GET /api/operations/incidents/{incident}/drone-missions/{mission}/assets`
- `GET .../assets/{asset}/original|thumbnail`
- `POST/GET /api/operations/incidents/{incident}/drone-missions/{mission}/mosaics`

## Acceptance and remaining real-world gates

Local completion requires schema migration, implementation, API protection, audit records, deterministic algorithms and automated tests. Deployment acceptance additionally requires a brigade-selected GPS/radio gateway adapter, device certificate or protected LAN design, field-tested maximum-speed/freshness profiles, aircraft/camera metadata conventions, aviation/privacy policy, imagery classification and retention policy, calibrated ground-control or onboard RTK validation, a representative high-volume soak test, and a disconnected multi-device exercise. Those are explicit operational gates - not claims the generic base can satisfy in software alone.

# All-zoom offline tile coverage

## Completed software contract

- [x] Enumerate every integer zoom between the selected minimum and maximum.
- [x] Report expected, present, missing, modified and completeness values per layer and zoom.
- [x] Require every zoom row to pass before declaring an AOI ready for offline use.
- [x] Pin ordinary cached files referenced by a complete manifest against TTL and size-cap pruning.
- [x] Retire/delete a manifest to unpin its files without destructively deleting tiles immediately.
- [x] Cache the pin index efficiently and recover it from manifests after restart.
- [x] Validate imported MBTiles tile presence and hashes at every selected zoom.
- [x] Treat a validated local GeoTIFF covering the full AOI as locally renderable at every selected zoom and verify its source hash.
- [x] Reject unsupported zooms, excessive tile counts, incomplete local-raster coverage and changed/missing files.
- [x] Expose all zoom rows in the incident planning interface.

## Provider boundary

NexFiremap does not bulk-prefetch the default public raster providers. In particular,
OpenStreetMap's official tile policy explicitly prohibits pre-seeding multiple zoom
levels and offline download features from `tile.openstreetmap.org`. Deliberate offline
coverage must therefore come from an authorised local MBTiles/GeoTIFF/raster
GeoPackage, a self-hosted tile service, or a provider workflow that explicitly grants
offline export. Ordinary human-viewed public tiles remain cached on demand. Once a
complete manifest is created, those exact files are pinned.

## Operational rule

“Every zoom cached” means every tile intersecting the selected AOI for every integer
zoom in the selected range is either present and hashed locally, or can be rendered
entirely from a validated local raster whose bounds cover the AOI. A pack is not ready
when aggregate coverage is merely high: every per-zoom row must be complete and a
subsequent verification must report no missing or modified content.

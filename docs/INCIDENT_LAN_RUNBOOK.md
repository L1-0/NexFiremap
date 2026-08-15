# NexFiremap incident-LAN runbook

This runbook is for an authorised incident-system administrator. NexFiremap is
not certified by the software itself. Local command, security and GIS leads
remain responsible for accepting the deployment and its data.

## Prepare before deployment

1. Use a dedicated, patched command laptop and a trusted incident-only LAN.
2. Set `NEXFIREMAP_LAN_MODE=true`, a unique 12+ character
 `NEXFIREMAP_ADMIN_PASSWORD`, and a short `NEXFIREMAP_SESSION_MINUTES` value.
3. Configure both `NEXFIREMAP_TLS_CERT_FILE` and `NEXFIREMAP_TLS_KEY_FILE`.
 The certificate must name the address field devices use. Install and verify
 its issuing CA on every device before the briefing. Never teach users to
 click through certificate warnings.
4. Start with an explicit non-loopback address, for example
 `python run.py --host 192.168.50.10`. Startup must show the LAN warning and
 an `https://` URL. If TLS is intentionally terminated at a hardened reverse
 proxy instead, bind NexFiremap only to that proxy's private interface.
5. Sign in as `admin`, open **Incident LAN accounts**, and issue a named account
 with the least-privileged role required: viewer, field editor, plans, safety,
 public, or administrator. Do not share the administrator account.
6. Import and verify the AOI map pack, create a verified backup, disconnect the
 WAN, reload two field devices, and confirm map, incident, drawing, products,
 logout and re-login all work.
 Review every per-zoom row: each selected level must say `complete`. Complete
 manifests pin ordinary cached files, but public tile servers are not bulk-prefetched.
 For deliberate all-zoom preparation, use an authorised local MBTiles/GeoTIFF/raster
 GeoPackage or an explicitly permitted/self-hosted export source.
7. For each approved vehicle gateway, an administrator creates a position feed and
 transfers its one-time token through the brigade's protected provisioning process.
 Test token rotation and revocation before deployment. Never place feed tokens in a
 shared briefing document or browser bookmark.
8. Confirm the air desk's classification, retention and aviation/privacy procedure.
 Test representative nadir frames with known ground control and verify that an
 oblique/unreferenced image remains evidence-only.

## During operations

- Keep the command server on protected power and monitor the local-server pill.
- Treat stale observations and model warnings as unresolved until a named
 operator updates or explicitly reviews them.
- Create immutable snapshots at operational-period changes and before handover.
- Export classified products only to their intended audience. Use the public
 template for public release. Other product classes contain command records.
- Download verified backups to separate encrypted media. A backup left only on
 the command laptop is not a recovery copy.
- Watch the telemetry panel for stale/quality warnings. A feed position is not a
 confirmed tactical observation, and interpolation is only a display estimate.
- Drone originals/previews are under `NEXFIREMAP_DRONE_DIR`. GeoTIFF/mosaic layers are
 under `NEXFIREMAP_TILE_DIR/offline_sources`. A database-only backup does not contain
 those files.

## Emergency access and recovery

If a user cannot sign in, an administrator creates a replacement named account.
do not disable authentication or expose the loopback-only mode to the LAN. If
the administrator credential is lost, stop the server, set a new strong
`NEXFIREMAP_ADMIN_PASSWORD`, and restart: the local `admin` password is rotated
at startup without deleting incident data. Record the emergency change in the
incident log and replace any shared credential immediately.

### Sign-in fallback order when an identity provider is configured

If OIDC or LDAP is configured (`NEXFIREMAP_OIDC_ISSUER`, `NEXFIREMAP_LDAP_HOST`),
sign-in is attempted in this order, and **local accounts are always tried first**:

1. **Local account** - the `admin` password from `NEXFIREMAP_ADMIN_PASSWORD`, plus
   any named local accounts.
2. **LDAP** - only if the local check fails and a directory is configured.
3. **OIDC** - a separate button on the login screen, not part of the password flow.

This ordering is deliberate and must not be changed. An incident LAN's identity
provider is frequently on the far side of the WAN link that has just failed,
which is exactly when the map matters most. **Configuring OIDC or LDAP never
disables local password login**, so the local `admin` credential remains the
break-glass path: if the directory or the IdP is unreachable, sign in locally
and continue. `GET /api/auth/providers` reports which methods this install
offers.

Note for security review: the OIDC flow does **not** verify ID-token signatures
locally. It uses the authorization-code flow with PKCE and reads claims from the
provider's token and userinfo endpoints directly over TLS, which OIDC Core
§3.1.3.7 permits for exactly this case. The consequence is that **the provider's
TLS certificate is the entire trust anchor** - so an `https://` issuer is
required (loopback excepted for testing) and certificate verification is never
relaxed.

### Unauthenticated ingest surfaces

Three endpoints deliberately sit outside the session gate because the senders are
machines with no browser session. Each has its own credential, and none of them
can read anything:

| Surface | Credential | Notes |
|---|---|---|
| `POST /api/feeds/positions/{id}` | `X-Feed-Token` | JSON, CoT XML or NMEA, selected by `Content-Type` |
| `GET\|POST .../{id}/osmand` | `X-Feed-Token` or `?token=` | OsmAnd/Traccar hardware cannot set headers |
| `POST /api/ingest/webhook/{id}` | `X-Webhook-Token` or `?token=` | CAD/dispatch deliveries |

The CoT/MAVLink gateway (`NEXFIREMAP_COT_ENABLED`) is different and needs care:
**CoT over UDP carries no authentication at all**, and MAVLink v2 signing is not
verified. It is therefore off by default, binds loopback unless
`NEXFIREMAP_COT_HOST` says otherwise, and accepts packets only from the CIDR list
in `NEXFIREMAP_COT_ALLOW` - which defaults to loopback and falls back to loopback
if every entry is malformed. Do not expose that port to anything but a
physically controlled incident LAN, and never to the internet.

For database failure, stop NexFiremap and use **Create recovery database** from
the newest verified backup on another machine. Preserve the failed database and
its WAL/SHM files for investigation. Never overwrite the only copy. Migration
startup also creates a `*.pre-migration-vN.sqlite3` backup beside an older
database. Test the restored copy before substituting it.

## Shutdown and handover

1. Complete safety checks, create a named snapshot, and generate the classified
 handover product/package.
2. Create and download a final verified backup. Verify its SHA-256 on the
 receiving medium.
 Also copy `NEXFIREMAP_DRONE_DIR` and the `offline_sources` directory beneath
 `NEXFIREMAP_TILE_DIR`, preserving relative paths and independently hashing the copy.
3. On the receiving installation, preview the package. Resolve every divergent
 UUID record side by side with a named resolver. Never re-import over an
 existing incident.
4. Sign out field clients, stop the server, inventory exported media, and revoke
 or destroy temporary incident credentials according to local policy.
 Position-feed tokens are installation secrets: rotate/re-provision them on the
 receiving command system or leave their sources disabled until the gateway is owned.

## Release gates

Before real incident use, the responsible authority must complete the target
tablet WAN-off drill, multi-user load exercise, dependency/security scan,
accessibility/outdoor review, power-loss recovery drill, and a brigade tabletop
and field exercise. Track these gates in `END_TO_END_TODO.md`.

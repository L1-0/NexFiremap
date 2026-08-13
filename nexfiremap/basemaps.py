"""Selectable basemap layers.

All defaults are OpenStreetMap based (or OSM-data derived) apart from the two
imagery layers, which are useful for reading burn scars. Add your own by
appending to ``BASEMAPS``: the frontend builds its layer switcher from this
list, so nothing else needs changing.
"""

from __future__ import annotations

OSM_ATTRIB = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

# Each entry drives both the /tiles proxy (id/url/subdomains/tile_ext) and the
# frontend's layer switcher (name/attribution/dark). "dark" flags a basemap
# whose imagery is dark-toned, so the UI can pick readable overlay colours
# against it; "default" (only ever True on one entry) marks the layer shown
# on first load. "max_native_zoom" is set only when a provider's own tiles
# stop gaining real detail before "max_zoom" - see esri-terrain below.
BASEMAPS: list[dict] = [
    {
        "id": "osm",
        "name": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": OSM_ATTRIB,
        "max_zoom": 19,
        "dark": False,
        "default": True,
    },
    {
        "id": "osm-hot",
        "name": "Humanitarian (HOT)",
        "url": "https://tile-{s}.openstreetmap.fr/hot/{z}/{x}/{y}.png",
        "subdomains": "abc",
        "attribution": OSM_ATTRIB + ', tiles by <a href="https://www.hotosm.org/">HOT</a>',
        "max_zoom": 19,
        "dark": False,
    },
    {
        "id": "opentopomap",
        "name": "OpenTopoMap",
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "subdomains": "abc",
        "attribution": OSM_ATTRIB + ', <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
        "max_zoom": 17,
        "dark": False,
    },
    {
        "id": "cyclosm",
        "name": "CyclOSM",
        "url": "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
        "subdomains": "abc",
        "attribution": OSM_ATTRIB + ', <a href="https://www.cyclosm.org/">CyclOSM</a>',
        "max_zoom": 20,
        "dark": False,
    },
    {
        "id": "carto-light",
        "name": "Carto Positron (light)",
        "url": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "subdomains": "abcd",
        "attribution": OSM_ATTRIB + ', &copy; <a href="https://carto.com/attributions">CARTO</a>',
        "max_zoom": 20,
        "dark": False,
    },
    {
        "id": "carto-dark",
        "name": "Carto Dark Matter",
        "url": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "subdomains": "abcd",
        "attribution": OSM_ATTRIB + ', &copy; <a href="https://carto.com/attributions">CARTO</a>',
        "max_zoom": 20,
        "dark": True,
    },
    {
        "id": "esri-imagery",
        "name": "Esri World Imagery",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery &copy; Esri, Maxar, Earthstar Geographics",
        "max_zoom": 19,
        "dark": True,
    },
    {
        "id": "esri-terrain",
        "name": "Esri Terrain",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
        "max_zoom": 19,
        # The service's own tileInfo metadata advertises LODs up to 13 and
        # answers 200 OK for every one of them, but confirmed live: z10-13
        # tiles for a real location are byte-for-byte identical in size
        # (2521 bytes, vs.3005/2421 at z8/z9) - no genuine extra detail past
        # z9, just the same content repeated. max_native_zoom stops the
        # frontend requesting those pointless deeper tiles directly - Leaflet
        # upscales the z9 tile instead when the user zooms in further, same
        # as it would for any other layer that runs out of real resolution.
        "max_native_zoom": 9,
        "dark": False,
        # Unlike every other basemap here, this service actually serves
        # JPEG bytes (confirmed against its own /MapServer?f=json metadata,
        # "format": "JPEG") - proxying/caching/serving it mislabelled as
        # PNG is what made this layer render as broken.
        "tile_ext": "jpg",
    },
]

# Elevation source for the 3D terrain view (app.js's MapLibre view, not the
# 2D Leaflet switcher - deliberately not part of BASEMAPS/OVERLAYS above, so
# it's proxied/cached through the same /tiles machinery without showing up
# as a selectable 2D layer). "Terrarium" PNG-encoded elevation, originally
# a Mapzen product now hosted as part of AWS's Open Data programme - free,
# keyless, the same free-data bar as everything else this project uses.
TERRAIN_DEM: dict = {
    "id": "terrain-dem",
    "name": "Terrain elevation (Terrarium)",
    "url": "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
    "attribution": "Terrain data via AWS Open Data (sources include SRTM, USGS 3DEP, ETOPO1)",
    "max_zoom": 15,
    "dark": False,
}

# Optional transparent layers drawn on top of the basemap.
OVERLAYS: list[dict] = [
    {
        "id": "osm-labels",
        "name": "Place labels",
        "url": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png",
        "subdomains": "abcd",
        "attribution": OSM_ATTRIB + ', &copy; <a href="https://carto.com/attributions">CARTO</a>',
        "max_zoom": 20,
    },
]


  NexFiremap -> http://127.0.0.1:8000

INFO:     Started server process [8936]
INFO:     Waiting for application startup.
22:51:54 INFO    nexfiremap.api | NexFiremap ready on http://127.0.0.1:8000 (5 job workers)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:58401 - "GET / HTTP/1.1" 200 OK
INFO:     127.0.0.1:58401 - "GET /static/vendor/leaflet/leaflet.css HTTP/1.1" 200 OK
INFO:     127.0.0.1:58402 - "GET /static/vendor/markercluster/MarkerCluster.css HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /static/vendor/markercluster/MarkerCluster.Default.css HTTP/1.1" 200 OK
INFO:     127.0.0.1:58404 - "GET /static/css/app.css HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /static/vendor/markercluster/leaflet.markercluster.js HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /static/vendor/leaflet/leaflet.js HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /static/vendor/heat/leaflet-heat.js HTTP/1.1" 200 OK
INFO:     127.0.0.1:58401 - "GET /static/js/app.js HTTP/1.1" 200 OK
INFO:     127.0.0.1:58407 - "GET /static/vendor/markercluster/leaflet.markercluster.js HTTP/1.1" 200 OK
INFO:     127.0.0.1:58407 - "GET /static/vendor/leaflet/leaflet.js HTTP/1.1" 200 OK
INFO:     127.0.0.1:58407 - "GET /static/vendor/heat/leaflet-heat.js HTTP/1.1" 200 OK
INFO:     127.0.0.1:58408 - "GET /static/js/app.js HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /api/config HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /favicon.ico HTTP/1.1" 404 Not Found
INFO:     127.0.0.1:58401 - "GET /api/summary?bbox=-163.8281%2C-53.0148%2C173.6719%2C71.4132&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /api/detections?bbox=-163.8281%2C-53.0148%2C173.6719%2C71.4132&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=3&autofetch=true&limit=40000 HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/3/3.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/4/3.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /tiles/osm/3/3/3.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/4/2.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/3/2.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58401 - "GET /tiles/osm/3/4/3.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/3/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /api/events?bbox=-163.8281%2C-53.0148%2C173.6719%2C71.4132&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58404 - "GET /tiles/osm/3/4/2.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/3/3/2.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /tiles/osm/3/3/4.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/4/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/3/4/4.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/5/3.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/2/3.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /tiles/osm/3/2/3.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58404 - "GET /tiles/osm/3/5/3.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/2/2.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/5/2.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/2/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/3/5/2.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/3/2/2.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58402 - "GET /tiles/osm/3/2/4.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/5/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /tiles/osm/3/5/4.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/3/1.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/4/1.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/3/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/3/4/1.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58404 - "GET /tiles/osm/3/3/1.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/3/3/5.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/4/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/3/4/5.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/2/1.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /tiles/osm/3/2/1.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/5/1.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/3/5/1.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/1/3.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/2/5.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/6/3.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/3/1/3.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/5/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/3/2/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/3/6/3.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /tiles/osm/3/5/5.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/1/2.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/3/1/2.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/6/2.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/1/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/3/6/2.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58402 - "GET /tiles/osm/3/1/4.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/1/1.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/6/4.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/6/1.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58406 - "GET /tiles/osm/3/6/4.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /tiles/osm/3/1/1.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /tiles/osm/3/6/1.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/1/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/3/1/5.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/6/5.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/0/3.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/3/6/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/3/0/3.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/7/3.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/0/2.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /tiles/osm/3/7/3.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /tiles/osm/3/0/2.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/7/2.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/0/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/3/7/2.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58402 - "GET /tiles/osm/3/0/4.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/0/1.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/7/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /tiles/osm/3/0/1.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/3/7/4.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/7/1.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/0/5.png "HTTP/1.1 200 OK"
22:52:01 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/3/7/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/3/7/1.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58404 - "GET /tiles/osm/3/0/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58402 - "GET /tiles/osm/3/7/5.png HTTP/1.1" 200 OK
22:52:01 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY=63fa1ee93ab783af359e8bf00c5fde52 "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58401 - "GET /api/status HTTP/1.1" 200 OK
22:52:02 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/world/4/2026-08-03 "HTTP/1.1 200 OK"
22:52:02 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/world/4/2026-08-03 "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/world/4/2026-08-03 "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/7/6.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/9/6.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58406 - "GET /tiles/osm/4/7/6.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/8/5.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/7/5.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/8/7.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/4/9/6.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/8/6.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58401 - "GET /tiles/osm/4/8/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58402 - "GET /tiles/osm/4/7/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /tiles/osm/4/8/6.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /tiles/osm/4/8/7.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/9/5.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/7/7.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58406 - "GET /tiles/osm/4/9/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58404 - "GET /tiles/osm/4/7/7.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/9/7.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/6/6.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58401 - "GET /tiles/osm/4/9/7.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /tiles/osm/4/6/6.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/8/4.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/10/6.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/8/8.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/7/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/4/10/6.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/4/8/8.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58402 - "GET /tiles/osm/4/8/4.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58404 - "GET /tiles/osm/4/7/4.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/9/4.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/6/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /tiles/osm/4/6/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58401 - "GET /tiles/osm/4/9/4.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/10/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/4/10/5.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/6/7.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/7/8.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58406 - "GET /tiles/osm/4/6/7.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/10/7.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/6/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/4/7/8.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58402 - "GET /tiles/osm/4/10/7.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58401 - "GET /tiles/osm/4/6/4.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/9/8.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/10/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58405 - "GET /tiles/osm/4/9/8.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /tiles/osm/4/10/4.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/5/6.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/4/5/6.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/6/8.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/10/8.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/11/6.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/11/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/4/10/8.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/4/6/8.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /tiles/osm/4/11/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58401 - "GET /tiles/osm/4/11/6.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/5/7.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/5/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/4/5/7.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /tiles/osm/4/5/5.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/11/7.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/4/11/7.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/11/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/4/11/4.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/5/4.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/11/8.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/4/6.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/5/8.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/4/11/8.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/4/5/4.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /tiles/osm/4/4/6.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58401 - "GET /tiles/osm/4/5/8.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/12/6.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/4/5.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58404 - "GET /tiles/osm/4/12/6.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58403 - "GET /tiles/osm/4/4/5.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/12/5.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/12/7.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/4/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/4/12/5.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58405 - "GET /tiles/osm/4/12/7.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58401 - "GET /tiles/osm/4/4/4.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/4/8.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/4/7.png "HTTP/1.1 200 OK"
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/12/4.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58403 - "GET /tiles/osm/4/4/8.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58404 - "GET /tiles/osm/4/12/4.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58406 - "GET /tiles/osm/4/4/7.png HTTP/1.1" 200 OK
22:52:03 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/4/12/8.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58402 - "GET /tiles/osm/4/12/8.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/detections?bbox=8.1876%2C45.4832%2C10.8243%2C46.3877&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=3&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/summary?bbox=8.1876%2C45.4832%2C10.8243%2C46.3877&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/5/16/12.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/5/17/12.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/5/17/11.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/5/16/10.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/5/17/10.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/5/16/11.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/5/20/13.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/6/34/23.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/6/33/22.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /api/events?bbox=8.1876%2C45.4832%2C10.8243%2C46.3877&limit=30 HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/6/33/23.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/6/33/21.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/68/46.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/67/45.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/67/47.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/68/45.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/66/48.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/64/45.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/71/45.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/71/47.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/8/134/91.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/7/67/46.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/8/136/91.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/8/135/92.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/8/136/89.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/8/139/93.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/8/135/91.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/9/269/182.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/9/270/182.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/9/269/181.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/539/365.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/9/269/180.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/10/539/365.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/8/135/90.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/9/269/184.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/9/268/180.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/9/270/184.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/9/270/180.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/538/364.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/538/363.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58496 - "GET /tiles/osm/10/538/364.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/539/363.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /tiles/osm/10/538/363.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58499 - "GET /tiles/osm/10/539/363.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/539/364.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/538/365.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58497 - "GET /tiles/osm/10/539/364.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /tiles/osm/10/538/365.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/537/364.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/540/364.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/537/363.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58496 - "GET /tiles/osm/10/537/364.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/540/363.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /tiles/osm/10/540/364.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58499 - "GET /tiles/osm/10/537/363.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /tiles/osm/10/540/363.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/537/365.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/540/365.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58497 - "GET /tiles/osm/10/537/365.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/538/362.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58500 - "GET /tiles/osm/10/540/365.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /tiles/osm/10/538/362.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/539/362.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/538/366.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /tiles/osm/10/539/362.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/539/366.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/537/362.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/10/539/366.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/540/362.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/10/538/366.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/536/364.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58497 - "GET /tiles/osm/10/537/362.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /tiles/osm/10/540/362.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/541/364.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58496 - "GET /tiles/osm/10/536/364.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58498 - "GET /tiles/osm/10/541/364.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/537/366.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/10/537/366.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/540/366.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/536/363.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/10/540/366.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/541/363.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/536/365.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58497 - "GET /tiles/osm/10/536/363.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/541/365.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58500 - "GET /tiles/osm/10/541/363.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /tiles/osm/10/536/365.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58498 - "GET /tiles/osm/10/541/365.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/536/362.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/541/362.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/10/536/362.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/536/366.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/10/541/362.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/10/536/366.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/541/366.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/535/364.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58500 - "GET /tiles/osm/10/541/366.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /tiles/osm/10/535/364.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/542/364.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /tiles/osm/10/542/364.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/535/363.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/542/363.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/542/365.png "HTTP/1.1 200 OK"
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/535/365.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/10/535/363.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/535/362.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/10/542/363.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/10/535/365.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /tiles/osm/10/542/365.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/542/362.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58496 - "GET /tiles/osm/10/535/362.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58498 - "GET /tiles/osm/10/542/362.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/535/366.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/10/535/366.png HTTP/1.1" 200 OK
22:52:10 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/10/542/366.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/10/542/366.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1078/729.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1076/728.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1077/728.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1077/730.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1077/729.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1076/729.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /tiles/osm/11/1076/728.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1078/729.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /tiles/osm/11/1077/728.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1077/729.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /api/summary?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58499 - "GET /tiles/osm/11/1076/729.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /api/detections?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=3&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1077/730.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1078/728.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1078/728.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /api/events?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&limit=30 HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1076/730.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1078/730.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58500 - "GET /tiles/osm/11/1076/730.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1075/729.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1077/727.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/11/1078/730.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1077/727.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1075/729.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1079/729.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1077/731.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1079/729.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /tiles/osm/11/1077/731.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1076/727.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1078/727.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1075/728.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/11/1076/727.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1078/727.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1075/728.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1079/728.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /tiles/osm/11/1079/728.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1075/730.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1075/730.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1079/730.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1076/731.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1078/731.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1079/730.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1076/731.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58498 - "GET /tiles/osm/11/1078/731.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1075/727.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1075/727.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1079/727.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1079/727.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1075/731.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1079/731.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1074/729.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1075/731.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58498 - "GET /tiles/osm/11/1079/731.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1074/729.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1080/729.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/11/1080/729.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1074/728.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1080/728.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58500 - "GET /tiles/osm/11/1074/728.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1080/728.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1074/730.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1074/727.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1080/730.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1074/730.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58498 - "GET /tiles/osm/11/1080/730.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1074/727.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1080/727.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1074/731.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/11/1080/727.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /tiles/osm/11/1074/731.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1073/729.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1081/729.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1080/731.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1073/729.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1073/728.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1080/731.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1081/728.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1073/730.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /tiles/osm/11/1081/729.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1073/728.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58499 - "GET /tiles/osm/11/1081/728.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /tiles/osm/11/1073/730.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1081/730.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1081/730.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1073/727.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1073/727.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1081/727.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58498 - "GET /tiles/osm/11/1081/727.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1081/731.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1073/731.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1078/726.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1077/726.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/11/1081/731.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1073/731.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1078/726.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58500 - "GET /tiles/osm/11/1077/726.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1076/726.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1079/726.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58496 - "GET /tiles/osm/11/1076/726.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58498 - "GET /tiles/osm/11/1079/726.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1075/726.png "HTTP/1.1 200 OK"
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1074/726.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58499 - "GET /tiles/osm/11/1075/726.png HTTP/1.1" 200 OK
22:52:21 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1080/726.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/11/1074/726.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58497 - "GET /tiles/osm/11/1080/726.png HTTP/1.1" 200 OK
22:52:30 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/11/1081/726.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58500 - "GET /tiles/osm/11/1081/726.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:22903 - "GET /api/summary?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22902 - "GET /api/detections?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=7&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/events?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:22907 - "GET /api/summary?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/detections?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=14&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/events?bbox=8.7904%2C45.7584%2C10.1088%2C46.2102&limit=30 HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2154/1458.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2155/1458.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2154/1457.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2155/1457.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/12/2154/1458.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2154/1459.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22908 - "GET /tiles/osm/12/2154/1457.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2155/1459.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22907 - "GET /tiles/osm/12/2155/1458.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22909 - "GET /tiles/osm/12/2155/1457.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22910 - "GET /tiles/osm/12/2154/1459.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22911 - "GET /tiles/osm/12/2155/1459.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2153/1458.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm/12/2153/1458.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2156/1458.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2153/1457.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22908 - "GET /tiles/osm/12/2156/1458.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2156/1457.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2156/1459.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2153/1459.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22907 - "GET /tiles/osm/12/2153/1457.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22909 - "GET /tiles/osm/12/2156/1457.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22911 - "GET /tiles/osm/12/2156/1459.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22910 - "GET /tiles/osm/12/2153/1459.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2154/1456.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22908 - "GET /tiles/osm/12/2154/1456.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2155/1456.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2154/1460.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2155/1460.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2153/1456.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22907 - "GET /tiles/osm/12/2155/1456.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2156/1456.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22911 - "GET /tiles/osm/12/2155/1460.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22910 - "GET /tiles/osm/12/2153/1456.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22909 - "GET /tiles/osm/12/2154/1460.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22908 - "GET /tiles/osm/12/2156/1456.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2152/1458.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22907 - "GET /tiles/osm/12/2152/1458.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2157/1458.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2153/1460.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2152/1457.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2156/1460.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22911 - "GET /tiles/osm/12/2157/1458.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22908 - "GET /tiles/osm/12/2152/1457.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2157/1457.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22910 - "GET /tiles/osm/12/2153/1460.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22909 - "GET /tiles/osm/12/2156/1460.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22907 - "GET /tiles/osm/12/2157/1457.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2152/1459.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2157/1459.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2152/1456.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2157/1456.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2152/1460.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2157/1460.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2158/1458.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4308/2917.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22922 - "GET /tiles/osm/13/4308/2917.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4308/2918.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/12/2151/1458.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4308/2916.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22924 - "GET /tiles/osm/13/4308/2918.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4309/2917.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4309/2918.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22926 - "GET /tiles/osm/13/4308/2916.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22925 - "GET /tiles/osm/13/4309/2918.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22923 - "GET /tiles/osm/13/4309/2917.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4309/2916.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22922 - "GET /tiles/osm/13/4309/2916.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4307/2917.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22924 - "GET /tiles/osm/13/4307/2917.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4310/2917.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4307/2918.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4308/2919.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4310/2918.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22926 - "GET /tiles/osm/13/4310/2917.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22925 - "GET /tiles/osm/13/4307/2918.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22922 - "GET /tiles/osm/13/4308/2919.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22923 - "GET /tiles/osm/13/4310/2918.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4309/2919.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22924 - "GET /tiles/osm/13/4309/2919.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4307/2916.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22926 - "GET /tiles/osm/13/4307/2916.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4310/2916.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4306/2917.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4310/2919.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4307/2919.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/13/4310/2916.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22924 - "GET /tiles/osm/13/4306/2917.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22922 - "GET /tiles/osm/13/4307/2919.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22923 - "GET /tiles/osm/13/4310/2919.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4311/2917.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22926 - "GET /tiles/osm/13/4311/2917.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4306/2918.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4306/2916.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4311/2916.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4311/2918.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/13/4306/2918.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22922 - "GET /tiles/osm/13/4306/2916.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22924 - "GET /tiles/osm/13/4311/2918.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4306/2919.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22923 - "GET /tiles/osm/13/4311/2916.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22926 - "GET /tiles/osm/13/4306/2919.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4311/2919.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/13/4311/2919.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4305/2917.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4312/2917.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22922 - "GET /tiles/osm/13/4305/2917.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4312/2918.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4305/2918.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22924 - "GET /tiles/osm/13/4312/2917.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22926 - "GET /tiles/osm/13/4312/2918.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22923 - "GET /tiles/osm/13/4305/2918.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4305/2916.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4312/2916.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/13/4305/2916.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22922 - "GET /tiles/osm/13/4312/2916.png HTTP/1.1" 200 OK
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4305/2919.png "HTTP/1.1 200 OK"
22:52:58 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/13/4312/2919.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22924 - "GET /tiles/osm/13/4305/2919.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22926 - "GET /tiles/osm/13/4312/2919.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8617/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22926 - "GET /tiles/osm/14/8617/5836.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8618/5835.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8618/5836.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8617/5837.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8617/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/14/8618/5835.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22922 - "GET /tiles/osm/14/8617/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22923 - "GET /tiles/osm/14/8618/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22924 - "GET /tiles/osm/14/8617/5835.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8618/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22926 - "GET /tiles/osm/14/8618/5837.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8616/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/14/8616/5836.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8616/5835.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8619/5835.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8619/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22923 - "GET /tiles/osm/14/8616/5835.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22924 - "GET /tiles/osm/14/8619/5835.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8616/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22922 - "GET /tiles/osm/14/8619/5836.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8619/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22926 - "GET /tiles/osm/14/8616/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22925 - "GET /tiles/osm/14/8619/5837.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8618/5834.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8617/5834.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8617/5838.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8618/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22924 - "GET /tiles/osm/14/8618/5834.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22923 - "GET /tiles/osm/14/8617/5834.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22922 - "GET /tiles/osm/14/8617/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22926 - "GET /tiles/osm/14/8618/5838.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8616/5834.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/14/8616/5834.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8619/5834.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8615/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22924 - "GET /tiles/osm/14/8619/5834.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22923 - "GET /tiles/osm/14/8615/5836.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8620/5836.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8616/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22922 - "GET /tiles/osm/14/8620/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22926 - "GET /tiles/osm/14/8616/5838.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8619/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/14/8619/5838.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8615/5835.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8620/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22922 - "GET /tiles/osm/14/8615/5835.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22926 - "GET /tiles/osm/14/8620/5835.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8615/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/14/8615/5837.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8620/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22922 - "GET /tiles/osm/14/8620/5837.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8615/5834.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22926 - "GET /tiles/osm/14/8615/5834.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8620/5834.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22925 - "GET /tiles/osm/14/8620/5834.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8615/5838.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8620/5838.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8614/5836.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/14/8621/5836.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17235/11674.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17234/11674.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17234/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17235/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17234/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17234/11673.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17235/11673.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17235/11675.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17234/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17235/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17235/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17234/11675.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17233/11674.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17236/11674.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17233/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17233/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17236/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17233/11673.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17233/11675.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17236/11673.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17236/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17236/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17233/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17236/11675.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17234/11672.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17235/11672.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17234/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17234/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17235/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17234/11676.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17235/11676.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17233/11672.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17236/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17233/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17235/11676.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17236/11672.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17237/11674.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17232/11674.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17233/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17237/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17232/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17233/11676.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17236/11676.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17237/11673.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17232/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17232/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17236/11676.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17237/11673.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17232/11675.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17232/11672.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17237/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17232/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17232/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17237/11675.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17237/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17237/11672.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17237/11676.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17232/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17232/11676.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17237/11676.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17231/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17231/11674.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17238/11674.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17231/11673.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17238/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17231/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17238/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17238/11673.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17231/11675.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17238/11675.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17231/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17231/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17238/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17231/11672.png HTTP/1.1" 200 OK
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17238/11672.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17238/11676.png "HTTP/1.1 200 OK"
22:52:59 INFO    httpx | HTTP Request: GET https://tile.openstreetmap.org/15/17231/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22932 - "GET /tiles/osm/15/17238/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /tiles/osm/15/17231/11676.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /tiles/osm/15/17238/11676.png HTTP/1.1" 200 OK
22:53:00 INFO    nexfiremap.cache | VIIRS_SNPP_NRT world 2026-08-03..2026-08-06 -> 211827 rows (211827 new)
22:53:00 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT world 2026-08-03..2026-08-06 -> 349477 rows (349476 new)
22:53:01 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT world 2026-08-03..2026-08-06 -> 347719 rows (347718 new)
INFO:     127.0.0.1:22933 - "GET /api/summary?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /api/detections?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=14&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /api/events?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:01 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-30,30,-20,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:01 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-40,30,-30,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:01 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (15, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:01 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (14, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:02 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/world/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:02 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-10,30,0,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:02 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (17, 12) 2026-08-03..2026-08-06 -> 213 rows (0 new)
22:53:02 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/30,30,40,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:02 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (21, 12) 2026-08-03..2026-08-06 -> 448 rows (0 new)
22:53:02 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-30,30,-20,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:02 INFO    nexfiremap.cache | MODIS_NRT cell (15, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:02 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,40,20,50/5/2026-08-02 "HTTP/1.1 200 OK"
22:53:02 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,30,20,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:02 INFO    nexfiremap.cache | MODIS_NRT cell (19, 13) 2026-08-02..2026-08-06 -> 200 rows (200 new)
22:53:02 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (19, 12) 2026-08-03..2026-08-06 -> 359 rows (0 new)
22:53:04 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-30,20,-20,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:04 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/5/2026-08-02 "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /api/detections?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=1&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /api/summary?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
22:53:05 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY=63fa1ee93ab783af359e8bf00c5fde52 "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /api/events?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /api/summary?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/detections?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=3&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/events?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /api/status HTTP/1.1" 200 OK
22:53:13 INFO    nexfiremap.cache | MODIS_NRT cell (15, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:13 INFO    nexfiremap.cache | MODIS_NRT world 2026-08-03..2026-08-06 -> 73114 rows (72966 new)
22:53:13 INFO    nexfiremap.cache | MODIS_NRT cell (18, 13) 2026-08-02..2026-08-06 -> 96 rows (24 new)
INFO:     127.0.0.1:22933 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:22931 - "GET /api/summary?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/detections?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=7&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22933 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/events?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&limit=30 HTTP/1.1" 200 OK
22:53:14 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,40,20,50/5/2026-08-02 "HTTP/1.1 200 OK"
22:53:14 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,20,20,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:14 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (19, 11) 2026-08-03..2026-08-06 -> 335 rows (0 new)
22:53:14 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (19, 13) 2026-08-02..2026-08-06 -> 704 rows (177 new)
22:53:14 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-40,40,-30,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:14 INFO    nexfiremap.cache | MODIS_NRT cell (14, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:14 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/5/2026-08-02 "HTTP/1.1 200 OK"
22:53:14 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (18, 13) 2026-08-02..2026-08-06 -> 333 rows (131 new)
22:53:14 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,40,20,50/5/2026-08-02 "HTTP/1.1 200 OK"
22:53:14 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/30,20,40,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:14 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (21, 11) 2026-08-03..2026-08-06 -> 118 rows (0 new)
22:53:15 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (19, 13) 2026-08-02..2026-08-06 -> 990 rows (214 new)
22:53:15 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-40,30,-30,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:15 INFO    nexfiremap.cache | MODIS_NRT cell (14, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:15 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,50,10,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:15 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (18, 14) 2026-08-03..2026-08-06 -> 415 rows (0 new)
22:53:15 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-40,20,-30,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:15 INFO    nexfiremap.cache | MODIS_NRT cell (14, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:15 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/2/2026-08-05 "HTTP/1.1 200 OK"
22:53:15 INFO    nexfiremap.cache | MODIS_NRT cell (18, 13) 2026-08-05..2026-08-06 -> 40 rows (0 new)
22:53:16 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/5/2026-08-02 "HTTP/1.1 200 OK"
22:53:16 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (18, 13) 2026-08-02..2026-08-06 -> 431 rows (92 new)
22:53:16 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,40,20,50/5/2026-08-02 "HTTP/1.1 200 OK"
22:53:16 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (19, 13) 2026-08-02..2026-08-06 -> 1043 rows (194 new)
22:53:16 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:16 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (18, 13) 2026-08-03..2026-08-06 -> 339 rows (0 new)
22:53:16 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/50,50,60,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:16 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (23, 14) 2026-08-03..2026-08-06 -> 504 rows (0 new)
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:17 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/5/2026-08-02 "HTTP/1.1 200 OK"
22:53:17 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/50,40,60,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:18 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (18, 13) 2026-08-02..2026-08-06 -> 590 rows (94 new)
22:53:18 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (23, 13) 2026-08-03..2026-08-06 -> 161 rows (0 new)
22:53:18 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:18 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:18 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:18 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:18 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-40,50,-30,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:18 INFO    nexfiremap.cache | MODIS_NRT cell (14, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:19 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:19 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:21 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:21 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:21 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:21 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:22 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:22 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:24 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:24 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:24 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:24 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:25 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/50,50,60,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:25 INFO    nexfiremap.cache | MODIS_NRT cell (23, 14) 2026-08-03..2026-08-06 -> 106 rows (0 new)
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:25 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/50,40,60,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:25 INFO    nexfiremap.cache | MODIS_NRT cell (23, 13) 2026-08-03..2026-08-06 -> 60 rows (0 new)
22:53:25 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:25 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:27 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:27 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:27 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:27 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:29 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:29 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:30 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:30 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:31 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:31 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:32 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/50,30,60,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:32 INFO    nexfiremap.cache | MODIS_NRT cell (23, 12) 2026-08-03..2026-08-06 -> 49 rows (0 new)
22:53:32 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/50,20,60,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:32 INFO    nexfiremap.cache | MODIS_NRT cell (23, 11) 2026-08-03..2026-08-06 -> 231 rows (0 new)
22:53:33 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:33 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:34 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:34 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:34 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:34 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:34 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:34 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:34 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:34 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:34 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:34 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:34 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/40,50,50,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:34 INFO    nexfiremap.cache | MODIS_NRT cell (22, 14) 2026-08-03..2026-08-06 -> 99 rows (0 new)
22:53:35 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/40,40,50,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:35 INFO    nexfiremap.cache | MODIS_NRT cell (22, 13) 2026-08-03..2026-08-06 -> 413 rows (0 new)
22:53:35 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:35 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:35 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:35 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:35 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/40,30,50,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:35 INFO    nexfiremap.cache | MODIS_NRT cell (22, 12) 2026-08-03..2026-08-06 -> 497 rows (0 new)
22:53:36 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/40,20,50,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:36 INFO    nexfiremap.cache | MODIS_NRT cell (22, 11) 2026-08-03..2026-08-06 -> 50 rows (0 new)
22:53:36 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/30,50,40,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:36 INFO    nexfiremap.cache | MODIS_NRT cell (21, 14) 2026-08-03..2026-08-06 -> 118 rows (0 new)
22:53:36 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/30,40,40,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:36 INFO    nexfiremap.cache | MODIS_NRT cell (21, 13) 2026-08-03..2026-08-06 -> 820 rows (0 new)
22:53:36 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/30,30,40,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:36 INFO    nexfiremap.cache | MODIS_NRT cell (21, 12) 2026-08-03..2026-08-06 -> 94 rows (0 new)
22:53:36 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/30,20,40,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:36 INFO    nexfiremap.cache | MODIS_NRT cell (21, 11) 2026-08-03..2026-08-06 -> 22 rows (0 new)
22:53:36 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/20,50,30,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:36 INFO    nexfiremap.cache | MODIS_NRT cell (20, 14) 2026-08-03..2026-08-06 -> 20 rows (0 new)
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/20,40,30,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (20, 13) 2026-08-03..2026-08-06 -> 247 rows (0 new)
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/20,30,30,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (20, 12) 2026-08-03..2026-08-06 -> 55 rows (0 new)
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:37 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/20,20,30,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (20, 11) 2026-08-03..2026-08-06 -> 16 rows (0 new)
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,40,20,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (19, 13) 2026-08-03..2026-08-06 -> 148 rows (0 new)
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,30,20,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (19, 12) 2026-08-03..2026-08-06 -> 41 rows (0 new)
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,20,20,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (19, 11) 2026-08-03..2026-08-06 -> 73 rows (0 new)
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,50,10,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (18, 14) 2026-08-03..2026-08-06 -> 49 rows (0 new)
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,50,20,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (19, 14) 2026-08-03..2026-08-06 -> 33 rows (0 new)
22:53:37 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:37 INFO    nexfiremap.cache | MODIS_NRT cell (18, 13) 2026-08-03..2026-08-06 -> 72 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,40,20,50/8/2026-07-30 "HTTP/1.1 400 Bad Request"
22:53:38 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,30,10,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (18, 12) 2026-08-03..2026-08-06 -> 102 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,20,10,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (18, 11) 2026-08-03..2026-08-06 -> 30 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-10,50,0,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (17, 14) 2026-08-03..2026-08-06 -> 12 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-10,40,0,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (17, 13) 2026-08-03..2026-08-06 -> 24 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-10,30,0,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (17, 12) 2026-08-03..2026-08-06 -> 17 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-10,20,0,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (17, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-20,50,-10,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (16, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-20,40,-10,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (16, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-20,30,-10,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (16, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-20,20,-10,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (16, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:38 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-30,50,-20,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:38 INFO    nexfiremap.cache | MODIS_NRT cell (15, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:39 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/-30,40,-20,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:39 INFO    nexfiremap.cache | MODIS_NRT cell (15, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:39 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-10,20,0,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:39 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (17, 11) 2026-08-03..2026-08-06 -> 10 rows (0 new)
22:53:39 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/20,50,30,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:39 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (20, 14) 2026-08-03..2026-08-06 -> 158 rows (0 new)
22:53:39 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,30,10,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:39 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/50,20,60,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:39 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (23, 11) 2026-08-03..2026-08-06 -> 449 rows (0 new)
22:53:39 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/50,30,60,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:39 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (23, 12) 2026-08-03..2026-08-06 -> 217 rows (0 new)
22:53:39 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (18, 12) 2026-08-03..2026-08-06 -> 708 rows (0 new)
22:53:39 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/40,50,50,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:39 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,20,10,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:40 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (22, 14) 2026-08-03..2026-08-06 -> 72 rows (0 new)
22:53:40 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (18, 11) 2026-08-03..2026-08-06 -> 323 rows (0 new)
22:53:40 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/40,40,50,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:40 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (22, 13) 2026-08-03..2026-08-06 -> 658 rows (0 new)
22:53:40 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/20,40,30,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:40 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/40,30,50,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:40 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-10,50,0,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:40 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (17, 14) 2026-08-03..2026-08-06 -> 106 rows (0 new)
22:53:40 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (20, 13) 2026-08-03..2026-08-06 -> 996 rows (0 new)
22:53:40 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (22, 12) 2026-08-03..2026-08-06 -> 1440 rows (0 new)
22:53:40 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/40,20,50,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:40 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (22, 11) 2026-08-03..2026-08-06 -> 207 rows (0 new)
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:40 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/30,40,40,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:40 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-10,40,0,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:40 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (17, 13) 2026-08-03..2026-08-06 -> 89 rows (0 new)
22:53:41 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/30,50,40,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (21, 13) 2026-08-03..2026-08-06 -> 3002 rows (0 new)
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (21, 14) 2026-08-03..2026-08-06 -> 531 rows (0 new)
22:53:41 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/30,30,40,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (21, 12) 2026-08-03..2026-08-06 -> 298 rows (0 new)
22:53:41 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/30,20,40,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (21, 11) 2026-08-03..2026-08-06 -> 77 rows (0 new)
22:53:41 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/20,40,30,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:41 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/20,50,30,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (20, 13) 2026-08-03..2026-08-06 -> 475 rows (0 new)
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (20, 14) 2026-08-03..2026-08-06 -> 120 rows (0 new)
22:53:41 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/20,30,30,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (20, 12) 2026-08-03..2026-08-06 -> 215 rows (0 new)
22:53:41 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,50,20,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (19, 14) 2026-08-03..2026-08-06 -> 121 rows (0 new)
22:53:41 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/20,20,30,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:41 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (20, 11) 2026-08-03..2026-08-06 -> 248 rows (0 new)
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,40,20,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (19, 13) 2026-08-03..2026-08-06 -> 527 rows (0 new)
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,30,20,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (19, 12) 2026-08-03..2026-08-06 -> 211 rows (0 new)
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,20,20,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (19, 11) 2026-08-03..2026-08-06 -> 278 rows (0 new)
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,50,10,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (18, 14) 2026-08-03..2026-08-06 -> 223 rows (0 new)
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,30,10,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (18, 12) 2026-08-03..2026-08-06 -> 458 rows (0 new)
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (18, 13) 2026-08-03..2026-08-06 -> 202 rows (0 new)
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,20,10,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (18, 11) 2026-08-03..2026-08-06 -> 239 rows (0 new)
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-10,40,0,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (17, 13) 2026-08-03..2026-08-06 -> 41 rows (0 new)
22:53:42 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-10,50,0,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:42 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (17, 14) 2026-08-03..2026-08-06 -> 63 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-10,30,0,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (17, 12) 2026-08-03..2026-08-06 -> 91 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-10,20,0,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (17, 11) 2026-08-03..2026-08-06 -> 5 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-20,50,-10,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (16, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-20,40,-10,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (16, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-20,30,-10,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (16, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-20,20,-10,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (16, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-30,50,-20,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (15, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-30,40,-20,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (15, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-30,30,-20,40/4/2026-08-03 "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (15, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-30,20,-20,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (15, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:43 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-40,50,-30,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:43 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (14, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:44 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-40,40,-30,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:44 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (14, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17235/11673.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17234/11673.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17236/11674.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17234/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22952 - "GET /tiles/osm-hot/15/17235/11673.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-40,30,-30,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17233/11674.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17234/11675.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17235/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22951 - "GET /tiles/osm-hot/15/17234/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22956 - "GET /tiles/osm-hot/15/17236/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /tiles/osm-hot/15/17234/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22955 - "GET /tiles/osm-hot/15/17233/11674.png HTTP/1.1" 200 OK
22:53:44 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (14, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
INFO:     127.0.0.1:22953 - "GET /tiles/osm-hot/15/17234/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22954 - "GET /tiles/osm-hot/15/17235/11675.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17233/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22957 - "GET /tiles/osm-hot/15/17233/11673.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17238/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22952 - "GET /tiles/osm-hot/15/17238/11672.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17238/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22951 - "GET /tiles/osm-hot/15/17238/11676.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17231/11675.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17231/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22954 - "GET /tiles/osm-hot/15/17231/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22956 - "GET /tiles/osm-hot/15/17231/11672.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17231/11676.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/-40,20,-30,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17235/11674.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17236/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm-hot/15/17231/11676.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17233/11675.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17238/11675.png "HTTP/1.1 200 OK"
22:53:44 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (14, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
INFO:     127.0.0.1:22950 - "GET /tiles/osm-hot/15/17235/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22953 - "GET /tiles/osm-hot/15/17236/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22957 - "GET /tiles/osm-hot/15/17233/11675.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17238/11673.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17236/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22955 - "GET /tiles/osm-hot/15/17238/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22952 - "GET /tiles/osm-hot/15/17238/11673.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17231/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22951 - "GET /tiles/osm-hot/15/17236/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22954 - "GET /tiles/osm-hot/15/17231/11673.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17234/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22956 - "GET /tiles/osm-hot/15/17234/11672.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17235/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22950 - "GET /tiles/osm-hot/15/17235/11672.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17238/11674.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17231/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm-hot/15/17238/11674.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17234/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22953 - "GET /tiles/osm-hot/15/17231/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22957 - "GET /tiles/osm-hot/15/17234/11676.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17237/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22955 - "GET /tiles/osm-hot/15/17237/11676.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17235/11676.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17232/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22952 - "GET /tiles/osm-hot/15/17235/11676.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17233/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22951 - "GET /tiles/osm-hot/15/17232/11676.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17237/11672.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17236/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22954 - "GET /tiles/osm-hot/15/17233/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22956 - "GET /tiles/osm-hot/15/17237/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22950 - "GET /tiles/osm-hot/15/17236/11672.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17232/11672.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17232/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/osm-hot/15/17232/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22953 - "GET /tiles/osm-hot/15/17232/11674.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17237/11675.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17237/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22957 - "GET /tiles/osm-hot/15/17237/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22955 - "GET /tiles/osm-hot/15/17237/11674.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17232/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22952 - "GET /tiles/osm-hot/15/17232/11675.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17233/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22951 - "GET /tiles/osm-hot/15/17233/11676.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17237/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22954 - "GET /tiles/osm-hot/15/17237/11673.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17236/11676.png "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://tile-a.openstreetmap.fr/hot/15/17232/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22950 - "GET /tiles/osm-hot/15/17232/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22956 - "GET /tiles/osm-hot/15/17236/11676.png HTTP/1.1" 200 OK
22:53:44 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/50,50,60,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:44 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/50,40,60,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:44 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (23, 14) 2026-08-03..2026-08-06 -> 737 rows (0 new)
22:53:44 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (23, 13) 2026-08-03..2026-08-06 -> 245 rows (0 new)
22:53:44 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/50,30,60,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:44 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (23, 12) 2026-08-03..2026-08-06 -> 404 rows (0 new)
22:53:44 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/40,50,50,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:44 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (22, 14) 2026-08-03..2026-08-06 -> 198 rows (0 new)
22:53:45 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/50,20,60,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:45 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (23, 11) 2026-08-03..2026-08-06 -> 780 rows (0 new)
22:53:45 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/40,30,50,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:45 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/40,40,50,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:45 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (22, 13) 2026-08-03..2026-08-06 -> 1024 rows (0 new)
22:53:45 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/40,20,50,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:45 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (22, 11) 2026-08-03..2026-08-06 -> 316 rows (0 new)
22:53:45 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (22, 12) 2026-08-03..2026-08-06 -> 2020 rows (0 new)
22:53:45 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/30,40,40,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:45 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/30,50,40,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:45 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/30,30,40,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:45 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (21, 12) 2026-08-03..2026-08-06 -> 412 rows (0 new)
22:53:46 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (21, 14) 2026-08-03..2026-08-06 -> 792 rows (0 new)
22:53:46 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (21, 13) 2026-08-03..2026-08-06 -> 3621 rows (0 new)
22:53:46 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/30,20,40,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:46 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (21, 11) 2026-08-03..2026-08-06 -> 126 rows (0 new)
22:53:46 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/20,50,30,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:46 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (20, 14) 2026-08-03..2026-08-06 -> 174 rows (0 new)
22:53:46 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/20,40,30,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:46 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (20, 13) 2026-08-03..2026-08-06 -> 913 rows (0 new)
22:53:46 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/20,20,30,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:46 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (20, 11) 2026-08-03..2026-08-06 -> 316 rows (0 new)
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:46 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/20,30,30,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:46 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (20, 12) 2026-08-03..2026-08-06 -> 368 rows (0 new)
22:53:47 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,50,20,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:47 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (19, 14) 2026-08-03..2026-08-06 -> 243 rows (0 new)
22:53:47 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,40,20,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:47 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-30,20,-20,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:47 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (15, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:47 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (19, 13) 2026-08-03..2026-08-06 -> 776 rows (0 new)
22:53:47 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-20,50,-10,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:47 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (16, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:47 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-10,30,0,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:47 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (17, 12) 2026-08-03..2026-08-06 -> 175 rows (0 new)
22:53:47 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/20,30,30,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:47 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (20, 12) 2026-08-03..2026-08-06 -> 454 rows (0 new)
22:53:47 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-10,20,0,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:47 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (17, 11) 2026-08-03..2026-08-06 -> 22 rows (0 new)
22:53:48 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/20,20,30,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:48 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (20, 11) 2026-08-03..2026-08-06 -> 263 rows (0 new)
22:53:48 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-20,40,-10,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:48 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (16, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:48 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-20,50,-10,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:48 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (16, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:48 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-20,40,-10,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:48 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (16, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:48 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-20,30,-10,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:48 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (16, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:48 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,50,20,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:48 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (19, 14) 2026-08-03..2026-08-06 -> 294 rows (0 new)
22:53:48 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-20,20,-10,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:48 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (16, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-30,40,-20,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (15, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,40,20,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-30,50,-20,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (15, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (19, 13) 2026-08-03..2026-08-06 -> 849 rows (0 new)
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-30,30,-20,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (15, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-30,20,-20,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (15, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-40,50,-30,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (14, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-40,30,-30,40/4/2026-08-03 "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (14, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-40,40,-30,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (14, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:49 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/-40,20,-30,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:49 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (14, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:50 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/50,50,60,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/50,40,60,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:50 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (23, 13) 2026-08-03..2026-08-06 -> 212 rows (0 new)
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17235/11673.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17234/11673.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17236/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22971 - "GET /tiles/cyclosm/15/17235/11673.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17235/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22975 - "GET /tiles/cyclosm/15/17236/11674.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/50,30,60,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17233/11674.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17234/11675.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17234/11674.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17235/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22970 - "GET /tiles/cyclosm/15/17234/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22969 - "GET /tiles/cyclosm/15/17235/11674.png HTTP/1.1" 200 OK
22:53:50 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (23, 14) 2026-08-03..2026-08-06 -> 747 rows (0 new)
INFO:     127.0.0.1:22974 - "GET /tiles/cyclosm/15/17233/11674.png HTTP/1.1" 200 OK
22:53:50 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (23, 12) 2026-08-03..2026-08-06 -> 433 rows (0 new)
INFO:     127.0.0.1:58422 - "GET /tiles/cyclosm/15/17234/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22972 - "GET /tiles/cyclosm/15/17234/11675.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17233/11673.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17238/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22973 - "GET /tiles/cyclosm/15/17235/11675.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17236/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22971 - "GET /tiles/cyclosm/15/17238/11676.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22975 - "GET /tiles/cyclosm/15/17236/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22976 - "GET /tiles/cyclosm/15/17233/11673.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17231/11676.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17233/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22970 - "GET /tiles/cyclosm/15/17231/11676.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17238/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22969 - "GET /tiles/cyclosm/15/17233/11675.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17236/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/cyclosm/15/17236/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22974 - "GET /tiles/cyclosm/15/17238/11672.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17234/11672.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17231/11672.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17235/11672.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17238/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22973 - "GET /tiles/cyclosm/15/17234/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22975 - "GET /tiles/cyclosm/15/17235/11672.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17231/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22971 - "GET /tiles/cyclosm/15/17238/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22972 - "GET /tiles/cyclosm/15/17231/11672.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17234/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22976 - "GET /tiles/cyclosm/15/17231/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22970 - "GET /tiles/cyclosm/15/17234/11676.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17238/11673.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17235/11676.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17231/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22969 - "GET /tiles/cyclosm/15/17238/11673.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17233/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/cyclosm/15/17235/11676.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22973 - "GET /tiles/cyclosm/15/17233/11672.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17238/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22974 - "GET /tiles/cyclosm/15/17231/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22975 - "GET /tiles/cyclosm/15/17238/11674.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17231/11674.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17232/11674.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17236/11672.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17237/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22972 - "GET /tiles/cyclosm/15/17231/11674.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22970 - "GET /tiles/cyclosm/15/17237/11676.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22976 - "GET /tiles/cyclosm/15/17232/11674.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17237/11674.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22971 - "GET /tiles/cyclosm/15/17236/11672.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17232/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22969 - "GET /tiles/cyclosm/15/17237/11674.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17233/11676.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17237/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /tiles/cyclosm/15/17232/11676.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22973 - "GET /tiles/cyclosm/15/17233/11676.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17236/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22974 - "GET /tiles/cyclosm/15/17237/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22975 - "GET /tiles/cyclosm/15/17236/11676.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17232/11672.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17232/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22972 - "GET /tiles/cyclosm/15/17232/11672.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17237/11675.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17237/11673.png "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17232/11675.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:22976 - "GET /tiles/cyclosm/15/17237/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22970 - "GET /tiles/cyclosm/15/17232/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22969 - "GET /tiles/cyclosm/15/17232/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:22971 - "GET /tiles/cyclosm/15/17237/11673.png HTTP/1.1" 200 OK
22:53:50 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/40,50,50,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:50 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/50,20,60,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:50 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (22, 14) 2026-08-03..2026-08-06 -> 261 rows (0 new)
22:53:51 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/40,40,50,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:51 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (23, 11) 2026-08-03..2026-08-06 -> 915 rows (0 new)
22:53:51 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (22, 13) 2026-08-03..2026-08-06 -> 1091 rows (0 new)
22:53:51 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/40,30,50,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:51 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/40,20,50,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:51 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (22, 11) 2026-08-03..2026-08-06 -> 353 rows (0 new)
22:53:51 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (22, 12) 2026-08-03..2026-08-06 -> 1693 rows (0 new)
22:53:51 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/30,50,40,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:51 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (21, 14) 2026-08-03..2026-08-06 -> 771 rows (0 new)
22:53:51 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/30,40,40,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:51 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-40,20,-30,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:51 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (14, 11) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:52 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-20,30,-10,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:52 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-40,50,-30,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:52 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (16, 12) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:52 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (14, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:52 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (21, 13) 2026-08-03..2026-08-06 -> 3831 rows (0 new)
22:53:52 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-20,20,-10,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:52 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (16, 11) 2026-08-03..2026-08-06 -> 1 rows (0 new)
22:53:52 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,30,20,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:52 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,20,20,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:52 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (19, 12) 2026-08-03..2026-08-06 -> 319 rows (0 new)
22:53:52 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (19, 11) 2026-08-03..2026-08-06 -> 259 rows (0 new)
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:52 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:52 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (18, 13) 2026-08-03..2026-08-06 -> 496 rows (0 new)
22:53:53 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,50,10,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:53 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (18, 14) 2026-08-03..2026-08-06 -> 371 rows (0 new)
22:53:53 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-40,40,-30,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:53 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (14, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:53 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-30,50,-20,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:53 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (15, 14) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:53 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,30,10,40/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:53 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,20,10,30/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:53 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (18, 11) 2026-08-03..2026-08-06 -> 470 rows (0 new)
22:53:53 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (18, 12) 2026-08-03..2026-08-06 -> 994 rows (0 new)
22:53:53 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-30,40,-20,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:53 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (15, 13) 2026-08-03..2026-08-06 -> 0 rows (0 new)
22:53:53 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-10,40,0,50/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:53 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (17, 13) 2026-08-03..2026-08-06 -> 67 rows (0 new)
22:53:54 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/-10,50,0,60/4/2026-08-03 "HTTP/1.1 200 OK"
22:53:54 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (17, 14) 2026-08-03..2026-08-06 -> 104 rows (0 new)
22:53:54 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/4/2026-07-30 "HTTP/1.1 200 OK"
22:53:54 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/4/2026-07-30 "HTTP/1.1 200 OK"
22:53:54 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/4/2026-07-30 "HTTP/1.1 200 OK"
22:53:54 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (18, 13) 2026-07-30..2026-08-02 -> 447 rows (316 new)
22:53:54 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (18, 13) 2026-07-30..2026-08-02 -> 421 rows (329 new)
22:53:54 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (18, 13) 2026-07-30..2026-08-02 -> 426 rows (332 new)
22:53:55 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:53:55 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:55 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:53:55 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:55 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:53:55 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:53:58 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:53:58 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:53:58 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:58 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:53:58 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:53:58 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:54:01 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:01 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:01 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:01 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:01 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:01 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:54:04 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:04 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:04 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:04 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:04 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:04 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:54:04 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:04 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:04 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:04 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:05 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:05 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:05 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:05 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:07 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:07 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:07 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY=63fa1ee93ab783af359e8bf00c5fde52 "HTTP/1.1 200 OK"
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:54:08 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:08 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:08 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:08 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:08 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:08 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:54:11 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:11 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:11 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:11 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:54:11 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/10,40,20,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:11 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
22:54:14 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/10/2026-07-23 "HTTP/1.1 400 Bad Request"
22:54:14 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:58422 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:22990 - "GET /api/summary?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/detections?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=7&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:58422 - "GET /api/events?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/coverage?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/industrial/sources?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/coverage?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "POST /api/industrial/scan HTTP/1.1" 202 Accepted
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/coverage?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/jobs/3 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/industrial/sources?bbox=9.3104%2C45.8562%2C9.3928%2C45.8845 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23007 - "GET /api/summary?bbox=9.3104%2C45.8566%2C9.3928%2C45.8849&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/detections?bbox=9.3104%2C45.8566%2C9.3928%2C45.8849&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=7&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23007 - "GET /api/events?bbox=9.3104%2C45.8566%2C9.3928%2C45.8849&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23008 - "GET /api/industrial/sources?bbox=9.3104%2C45.8566%2C9.3928%2C45.8849 HTTP/1.1" 200 OK
INFO:     127.0.0.1:22992 - "GET /api/coverage?bbox=9.3104%2C45.8566%2C9.3928%2C45.8849&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
INFO:     127.0.0.1:23011 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:23013 - "GET /api/summary?bbox=9.3104%2C45.8567%2C9.3928%2C45.8850&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23012 - "GET /api/detections?bbox=9.3104%2C45.8567%2C9.3928%2C45.8850&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=7&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23013 - "GET /api/events?bbox=9.3104%2C45.8567%2C9.3928%2C45.8850&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23014 - "GET /api/industrial/sources?bbox=9.3104%2C45.8567%2C9.3928%2C45.8850 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23012 - "GET /api/coverage?bbox=9.3104%2C45.8567%2C9.3928%2C45.8850&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8616/5836.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8617/5837.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8617/5838.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8616/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23019 - "GET /tiles/cyclosm/14/8616/5836.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8616/5838.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8617/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/14/8617/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/14/8616/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/14/8617/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23022 - "GET /tiles/cyclosm/14/8617/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23021 - "GET /tiles/cyclosm/14/8616/5838.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8615/5837.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8618/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/14/8618/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23019 - "GET /tiles/cyclosm/14/8615/5837.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8615/5836.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8618/5836.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8615/5838.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8618/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/14/8615/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23021 - "GET /tiles/cyclosm/14/8618/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23022 - "GET /tiles/cyclosm/14/8615/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/14/8618/5836.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8616/5835.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8617/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/14/8616/5835.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8616/5839.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8617/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23019 - "GET /tiles/cyclosm/14/8617/5835.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8615/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23021 - "GET /tiles/cyclosm/14/8617/5839.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/14/8616/5839.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23022 - "GET /tiles/cyclosm/14/8615/5835.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8618/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/14/8618/5835.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8614/5837.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8619/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/14/8614/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23019 - "GET /tiles/cyclosm/14/8619/5837.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8619/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/14/8619/5836.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8618/5839.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8614/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/14/8618/5839.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8615/5839.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8619/5838.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8614/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/14/8614/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23019 - "GET /tiles/cyclosm/14/8619/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23021 - "GET /tiles/cyclosm/14/8615/5839.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8614/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23022 - "GET /tiles/cyclosm/14/8614/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/14/8614/5835.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8619/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/14/8619/5835.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8614/5839.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8619/5839.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8613/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/14/8614/5839.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23019 - "GET /tiles/cyclosm/14/8619/5839.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8613/5836.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8620/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23021 - "GET /tiles/cyclosm/14/8613/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/14/8613/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23022 - "GET /tiles/cyclosm/14/8620/5837.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8620/5836.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8613/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/14/8620/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/14/8613/5838.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8613/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23022 - "GET /tiles/cyclosm/14/8613/5839.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8620/5838.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8620/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/14/8620/5839.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23019 - "GET /tiles/cyclosm/14/8620/5838.png HTTP/1.1" 200 OK
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8613/5835.png "HTTP/1.1 200 OK"
22:55:22 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8620/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23021 - "GET /tiles/cyclosm/14/8613/5835.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/14/8620/5835.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23022 - "GET /api/summary?bbox=9.2587%2C45.8433%2C9.4235%2C45.8999&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /api/detections?bbox=9.2587%2C45.8433%2C9.4235%2C45.8999&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=7&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /api/events?bbox=9.2587%2C45.8433%2C9.4235%2C45.8999&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23017 - "GET /api/industrial/sources?bbox=9.2587%2C45.8433%2C9.4235%2C45.8999 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /api/coverage?bbox=9.2587%2C45.8433%2C9.4235%2C45.8999&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
22:55:23 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17230/11674.png "HTTP/1.1 200 OK"
22:55:23 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17230/11676.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/15/17230/11674.png HTTP/1.1" 200 OK
22:55:23 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17230/11673.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23022 - "GET /tiles/cyclosm/15/17230/11676.png HTTP/1.1" 200 OK
22:55:23 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17230/11675.png "HTTP/1.1 200 OK"
22:55:23 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/15/17230/11672.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/15/17230/11673.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/15/17230/11675.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23021 - "GET /tiles/cyclosm/15/17230/11672.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /api/summary?bbox=9.3054%2C45.8561%2C9.3878%2C45.8844&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /api/detections?bbox=9.3054%2C45.8561%2C9.3878%2C45.8844&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=7&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /api/events?bbox=9.3054%2C45.8561%2C9.3878%2C45.8844&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /api/industrial/sources?bbox=9.3054%2C45.8561%2C9.3878%2C45.8844 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /api/coverage?bbox=9.3054%2C45.8561%2C9.3878%2C45.8844&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
22:55:27 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8621/5837.png "HTTP/1.1 200 OK"
22:55:27 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8621/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23020 - "GET /tiles/cyclosm/14/8621/5837.png HTTP/1.1" 200 OK
22:55:27 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8621/5838.png "HTTP/1.1 200 OK"
22:55:27 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8621/5835.png "HTTP/1.1 200 OK"
22:55:27 INFO    httpx | HTTP Request: GET https://a.tile-cyclosm.openstreetmap.fr/cyclosm/14/8621/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23017 - "GET /tiles/cyclosm/14/8621/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23022 - "GET /tiles/cyclosm/14/8621/5839.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23021 - "GET /tiles/cyclosm/14/8621/5835.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /tiles/cyclosm/14/8621/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /api/summary?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /api/detections?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=7&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23018 - "GET /api/events?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23017 - "GET /api/industrial/sources?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /api/coverage?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
22:55:32 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY=63fa1ee93ab783af359e8bf00c5fde52 "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23020 - "GET /api/status HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8617/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23035 - "GET /tiles/osm-labels/14/8617/5838.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8618/5837.png "HTTP/1.1 200 OK"
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8617/5836.png "HTTP/1.1 200 OK"
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8617/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23034 - "GET /tiles/osm-labels/14/8618/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23032 - "GET /tiles/osm-labels/14/8617/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /tiles/osm-labels/14/8617/5837.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8616/5836.png "HTTP/1.1 200 OK"
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8618/5836.png "HTTP/1.1 200 OK"
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8616/5837.png "HTTP/1.1 200 OK"
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8616/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23036 - "GET /tiles/osm-labels/14/8616/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23037 - "GET /tiles/osm-labels/14/8618/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23038 - "GET /tiles/osm-labels/14/8616/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23033 - "GET /tiles/osm-labels/14/8616/5837.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8621/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23032 - "GET /tiles/osm-labels/14/8621/5838.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8621/5839.png "HTTP/1.1 200 OK"
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8618/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23034 - "GET /tiles/osm-labels/14/8621/5839.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23039 - "GET /tiles/osm-labels/14/8618/5838.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8621/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23037 - "GET /tiles/osm-labels/14/8621/5835.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8613/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23020 - "GET /tiles/osm-labels/14/8613/5839.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8613/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23036 - "GET /tiles/osm-labels/14/8613/5838.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8613/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23035 - "GET /tiles/osm-labels/14/8613/5835.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8613/5836.png "HTTP/1.1 200 OK"
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8621/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23032 - "GET /tiles/osm-labels/14/8613/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23038 - "GET /tiles/osm-labels/14/8621/5836.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8617/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23033 - "GET /tiles/osm-labels/14/8617/5835.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8621/5837.png "HTTP/1.1 200 OK"
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8619/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23039 - "GET /tiles/osm-labels/14/8621/5837.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23037 - "GET /tiles/osm-labels/14/8619/5837.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8615/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23034 - "GET /tiles/osm-labels/14/8615/5837.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8616/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23032 - "GET /tiles/osm-labels/14/8616/5835.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8617/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23036 - "GET /tiles/osm-labels/14/8617/5839.png HTTP/1.1" 200 OK
22:55:35 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8613/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23020 - "GET /tiles/osm-labels/14/8613/5837.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8620/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23035 - "GET /tiles/osm-labels/14/8620/5839.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8614/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23038 - "GET /tiles/osm-labels/14/8614/5839.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8618/5835.png "HTTP/1.1 200 OK"
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8615/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23033 - "GET /tiles/osm-labels/14/8618/5835.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23037 - "GET /tiles/osm-labels/14/8615/5836.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8620/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23039 - "GET /tiles/osm-labels/14/8620/5835.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8614/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23034 - "GET /tiles/osm-labels/14/8614/5835.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8619/5836.png "HTTP/1.1 200 OK"
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8619/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23032 - "GET /tiles/osm-labels/14/8619/5836.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8620/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23038 - "GET /tiles/osm-labels/14/8619/5838.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8620/5836.png "HTTP/1.1 200 OK"
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8615/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23036 - "GET /tiles/osm-labels/14/8620/5838.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23033 - "GET /tiles/osm-labels/14/8620/5836.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23020 - "GET /tiles/osm-labels/14/8615/5838.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8614/5838.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23035 - "GET /tiles/osm-labels/14/8614/5838.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8616/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23037 - "GET /tiles/osm-labels/14/8616/5839.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8618/5839.png "HTTP/1.1 200 OK"
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8614/5836.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23034 - "GET /tiles/osm-labels/14/8618/5839.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23039 - "GET /tiles/osm-labels/14/8614/5836.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8614/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23036 - "GET /tiles/osm-labels/14/8614/5837.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8620/5837.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23032 - "GET /tiles/osm-labels/14/8620/5837.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8615/5835.png "HTTP/1.1 200 OK"
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8619/5835.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23038 - "GET /tiles/osm-labels/14/8615/5835.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23033 - "GET /tiles/osm-labels/14/8619/5835.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8619/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23020 - "GET /tiles/osm-labels/14/8619/5839.png HTTP/1.1" 200 OK
22:55:36 INFO    httpx | HTTP Request: GET https://a.basemaps.cartocdn.com/rastertiles/voyager_only_labels/14/8615/5839.png "HTTP/1.1 200 OK"
INFO:     127.0.0.1:23035 - "GET /tiles/osm-labels/14/8615/5839.png HTTP/1.1" 200 OK
INFO:     127.0.0.1:23053 - "GET /api/summary?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23052 - "GET /api/detections?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=14&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23053 - "GET /api/events?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23055 - "GET /api/industrial/sources?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23052 - "GET /api/coverage?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
INFO:     127.0.0.1:23052 - "GET /api/summary?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23055 - "GET /api/detections?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=30&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23052 - "GET /api/events?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23053 - "GET /api/industrial/sources?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23055 - "GET /api/coverage?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
22:55:58 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/5/2026-07-18 "HTTP/1.1 200 OK"
22:55:58 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/5/2026-07-18 "HTTP/1.1 200 OK"
22:55:58 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/5/2026-07-18 "HTTP/1.1 200 OK"
22:55:58 INFO    nexfiremap.cache | VIIRS_NOAA21_NRT cell (18, 13) 2026-07-18..2026-07-22 -> 665 rows (665 new)
22:55:58 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/5/2026-07-18 "HTTP/1.1 200 OK"
22:55:59 INFO    nexfiremap.cache | VIIRS_NOAA20_NRT cell (18, 13) 2026-07-18..2026-07-22 -> 661 rows (661 new)
22:55:59 INFO    nexfiremap.cache | VIIRS_SNPP_NRT cell (18, 13) 2026-07-18..2026-07-22 -> 668 rows (668 new)
22:55:59 INFO    nexfiremap.cache | MODIS_NRT cell (18, 13) 2026-07-18..2026-07-22 -> 213 rows (213 new)
22:55:59 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:55:59 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:55:59 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:55:59 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:55:59 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:55:59 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:23053 - "GET /api/status HTTP/1.1" 200 OK
22:56:03 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:03 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:56:03 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:03 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:56:03 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:03 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:23053 - "GET /api/status HTTP/1.1" 200 OK
22:56:06 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/MODIS_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:06 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:56:06 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:06 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:56:06 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:06 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:56:06 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_SNPP_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:06 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:23053 - "GET /api/status HTTP/1.1" 200 OK
22:56:09 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA21_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:09 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
22:56:09 INFO    httpx | HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/63fa1ee93ab783af359e8bf00c5fde52/VIIRS_NOAA20_NRT/0,40,10,50/10/2026-07-08 "HTTP/1.1 400 Bad Request"
22:56:09 WARNING nexfiremap.cache | FIRMS fetch problem: FIRMS returned HTTP 400: Invalid day range. Expects [1..5].
INFO:     127.0.0.1:23053 - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:23062 - "GET /api/summary?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&days=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23053 - "GET /api/detections?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&sources=VIIRS_NOAA20_NRT%2CVIIRS_NOAA21_NRT%2CVIIRS_SNPP_NRT%2CMODIS_NRT&confidence=nominal%2Chigh&days=30&autofetch=true&limit=40000 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23062 - "GET /api/events?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&limit=30 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23063 - "GET /api/industrial/sources?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994 HTTP/1.1" 200 OK
INFO:     127.0.0.1:23053 - "GET /api/coverage?bbox=9.2618%2C45.8428%2C9.4266%2C45.8994&day=2026-08-06&autofetch=true HTTP/1.1" 200 OK
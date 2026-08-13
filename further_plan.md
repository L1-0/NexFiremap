> **Implementation status (13 August 2026):** The locally testable implementation
> phases in this document have been actioned. The authoritative item-by-item status and
> remaining external brigade, hardware, licensing, doctrine and security gates are in
> [`END_TO_END_TODO.md`](END_TO_END_TODO.md). Deployment and emergency procedures are in
> [`INCIDENT_LAN_RUNBOOK.md`](INCIDENT_LAN_RUNBOOK.md). Checked software does not imply
> operational certification.

## Recommended framing

Build this as a **probabilistic spatiotemporal reconstruction**, not as a polygon generated from FIRMS points.

The finished map should keep four things visually and technically separate:

| Layer | Meaning |
| ------------------------------- | ----------------------------------------------------------------- |
| **Observed thermal detections** | What a satellite actually detected at an overpass |
| **Estimated affected extent** | Where the fire or burn scar probably existed |
| **Interpolated propagation** | A modelled path between observations |
| **Uncertainty** | Where the available observations do not constrain the result well |

That distinction matters because a FIRMS row represents the centre of a satellite pixel containing one or more thermal anomalies - not the location of the flame within that pixel. NASA also explicitly advises against treating active-fire pixels as burned area. Fires can be missed because of cloud, smoke, canopy, timing between overpasses, size, temperature, or instrument availability. ([NASA Earthdata][1])

Google’s result is not simply a union of FIRMS rectangles. Its published system combines imagery from multiple satellite types, applies a neural-network boundary model, updates from frequent imagery where available, and evaluates results against later fire scars or authoritative boundaries. ([sites.research.google][2])

---

# 1. Define the exact outputs first

I would define three model products.

### A. Active-heat likelihood

A rapidly decaying layer answering:

> “Where was active thermal activity likely present near this timestamp?”

This is driven mainly by FIRMS detections and should disappear or weaken after a configurable number of hours. It is **not cumulative burned area**.

### B. Estimated arrival-time surface

For every model cell, store:

* earliest plausible arrival time
* median estimated arrival time
* latest plausible arrival time
* probability the cell had been affected by time (t)

This becomes the main historical visualization. Colour each cell by its estimated first-arrival time:

* oldest: deep burgundy
* progressively newer: increasingly light red or salmon
* latest modelled front: a distinct bright-red outline

A raster or a set of non-overlapping age bands is better than stacking translucent polygons. Overlapping transparent polygons become artificially darker and would corrupt the age meaning.

### C. Observation-constrained propagation

This answers:

> “Given the previous observation and the next observation, what are the plausible paths connecting them?”

It should normally interpolate **between known observations**, rather than projecting indefinitely into the future.

Output at least:

* median path or perimeter
* 50% probability envelope
* 80% or 90% probability envelope
* optionally, hourly or six-hourly arrival-time contours

---

# 2. Build a tri-state observation model

For every satellite pass and every model cell, classify the observation as:

1. **Fire detected**
2. **Observed, valid, and no fire detected**
3. **Unknown**

Unknown includes:

* cloud
* smoke-obscured imagery
* missing coverage
* invalid retrieval
* instrument outage
* insufficient sensitivity
* outside the swath

This is one of the largest potential accuracy improvements. The absence of a FIRMS row is not evidence that the fire was absent. NASA lists numerous conditions under which MODIS or VIIRS can miss a fire. ([NASA Earthdata][3])

You therefore need more than your FIRMS point table. Retrieve or retain:

* satellite swath coverage
* fire-mask classifications
* cloud and quality masks
* geolocation information
* acquisition timestamp
* collection and processing version

This lets you establish interval-censored timing:

* **arrival interval:** last valid no-fire observation → first positive detection
* **cooling interval:** last positive detection → next valid no-fire observation

Those intervals are more honest and generally more useful than assigning the exact overpass timestamp as the moment a place burned.

---

# 3. Required data inputs

## Essential inputs

| Input | Purpose |
| ---------------------------------------------------- | --------------------------------------------- |
| FIRMS MODIS and VIIRS detections | Positive thermal observations |
| Level-2 fire masks and swath coverage | Valid negative observations and unknown areas |
| Pixel footprint/geolocation data | Correct spatial uncertainty |
| Digital elevation model | Elevation, slope and aspect |
| Fuel or vegetation classification | Baseline spread behaviour |
| Hourly wind, humidity, temperature and precipitation | Dynamic spread conditions |
| Water, bare rock and urban masks | Non-fuel and low-fuel constraints |
| High-resolution post-fire imagery | Burn-scar anchoring and validation |

### FIRMS fields to preserve

Keep the original values, not only processed values:

* platform and satellite
* sensor and product
* UTC acquisition time
* latitude and longitude
* scan and track
* confidence
* FRP
* brightness temperatures
* day/night
* collection/version
* NRT versus standard product
* original source row identifier

Confidence must be interpreted per sensor. MODIS provides a numeric confidence, while VIIRS uses low, nominal and high categories based on different internal conditions. NASA notes there is no universally optimal threshold. ([NASA Earthdata][3])

Use FRP as a feature, but do not interpret it as a directly comparable physical intensity value across every sensor and geometry. FRP is affected by spatial resolution, view angle, saturation, overpass time and other sensor characteristics. ([forum.earthdata.nasa.gov][4])

### Pixel geometry

For a basic display, use `scan × track` centred on the FIRMS coordinate. For the most accurate version, use the underlying swath product and its geolocation companion rather than relying only on axis-aligned CSV rectangles.

Also treat the footprint as an **uncertainty region**, not a burned rectangle. A MODIS detection can represent a much smaller sub-pixel fire, and the stated coordinate remains only the pixel centre. ([NASA Earthdata][1])

For historical reconstruction, replace NRT records with standard/science-quality records when available, while retaining provenance. Standard processing can use improved orbital information and quality control compared with the original near-real-time record. ([NASA Earthdata][5])

---

# 4. High-resolution boundary anchoring

FIRMS gives you timing and active-heat evidence. It should not be the sole source for the final cumulative extent.

Use surface-reflectance imagery from:

* Sentinel-2
* Landsat 8/9
* optionally Harmonized Landsat-Sentinel products
* optionally Sentinel-1 as a cloud-gap aid

Sentinel-2 provides bands at 10, 20 and 60 metres, making it much more suitable for local burn-scar delineation than 375 m VIIRS or 1 km MODIS detections. ([Copernicus Data Space Ecosystem][6])

Compute or ingest:

* NBR
* pre/post-fire dNBR
* NDVI change
* SWIR reflectance change
* optionally burn-area segmentation from a small classifier

USGS documents NBR as an NIR/SWIR index used to identify burned areas and assess burn severity. ([US Geological Survey][7])

A useful workflow is:

1. Select a cloud-free pre-event baseline.
2. Select every usable image during and shortly after the event.
3. Normalize surface reflectance.
4. Calculate NBR and spectral changes.
5. Generate a burn-likelihood raster.
6. Exclude water, permanent bare ground, cloud and shadow.
7. Use the result as an anchor or likelihood term, not as an unquestionable truth.

This is also close to the logic used by EFFIS in Europe: thermal hotspots support event discovery, while higher-resolution optical imagery is used to delineate and refine burned perimeters, including unburned islands. ([Joint Research Centre][8])

---

# 5. Spatial and temporal event association

Before modelling spread, group detections into coherent fire events.

Create a graph in which two detections can be linked when:

[
d(i,j) \leq r_i+r_j+v_{\max}\Delta t
]

where:

* (d(i,j)) is their spatial distance
* (r_i,r_j) reflect their footprint uncertainty
* (\Delta t) is the time difference
* (v_{\max}) is a deliberately generous possible spread speed

Use projected metric coordinates. For the coordinates shown in your screenshot, EPSG:32632 is an appropriate local UTM projection.

The event association should support:

* one front splitting into multiple fronts
* separate fires merging spatially
* temporary observation gaps
* multiple sensors detecting the same front within minutes
* persistent non-wildfire thermal sources

A practical implementation is:

* initial spatiotemporal clustering
* graph-based split/merge tracking
* explicit event IDs and front IDs
* de-duplication of overlapping observations from different satellites
* a static exclusion layer for industrial heat sources, volcanoes and other recurring anomalies

Do not use one convex hull around four weeks of points. It will fill valleys, lakes, unburned islands and disconnected activity.

---

# 6. Observation-likelihood surface

Rasterize each positive detection as a probability kernel rather than a solid rectangle.

For detection (i), define:

* (K_i(x)): spatial kernel over its possible footprint
* (q_i): sensor/confidence/quality weight
* (f_i): bounded FRP contribution
* (\tau): active-heat decay time

A simple active-observation likelihood is:

[
P_{\text{active}}(x,t)
======================

1-
\prod_i
\left[
1-q_iK_i(x)e^{-(t-t_i)/\tau}
\right]
]

Recommended behaviour:

* VIIRS footprints are narrower than MODIS footprints.
* Low-confidence daytime detections receive less weight.
* Observations near swath edges receive greater positional uncertainty.
* FRP is log-scaled and normalized separately for each sensor.
* Repeated observations increase confidence but do not multiply estimated burned area.
* A valid clear non-fire observation actively reduces the likelihood.
* An unknown observation does nothing.

Calibrate these weights empirically against historical fires. Avoid hand-tuning them solely for the example event.

---

# 7. Propagation model

The best balance for your project is an:

> **Ensemble anisotropic fast-marching or level-set model with sequential observation assimilation**

This is considerably more credible than pushing polygons in the observed movement direction, while remaining practical on a local machine.

## Spread-speed field

For every cell and time step, calculate a directional rate:

[
v(x,t,\theta)
=============

v_0(\text{fuel},\text{moisture})
\cdot
\phi_w(\text{wind},\theta)
\cdot
\phi_s(\text{slope},\theta)
\cdot
b(\text{land cover})
]

Inputs include:

* fuel category
* dead and live fuel moisture
* wind speed and direction
* slope and aspect
* recent precipitation
* water and non-fuel barriers
* canopy characteristics where available

The Rothermel model is the standard starting point for surface-fire spread and provides a documented relationship among fuels, moisture, wind and slope, although it still requires local calibration and has known limitations. ([US Forest Service Forschung][9])

FlamMap/FARSITE can serve as a reference implementation or validation benchmark. Its landscape inputs include elevation, slope, aspect, fuel models and multiple canopy layers. ([US Forest Service Forschung][10])

For Europe, the EFFIS fuel map provides a useful initial fuel classification mapped to Anderson/NFFL-style fuel categories. Treat it as a baseline and recalibrate it using local vegetation and historical spread observations. ([Copernicus EFFIS][11])

## Wind

Raw global weather grids are too coarse to directly represent valley, ridge and slope winds.

Use:

1. reanalysis or forecast wind as the large-scale input
2. local weather-station observations where possible
3. terrain-aware wind downscaling

WindNinja is specifically designed to calculate spatially varying near-surface winds in complex terrain and can use forecast data, station observations or a prescribed regional wind. ([US Forest Service Forschung][12])

For historical reconstruction, ERA5-Land provides hourly land variables globally at a native resolution of approximately 9 km, but it should be considered background forcing rather than local wind truth. ([cds.climate.copernicus.eu][13])

## Ensemble members

Each member should sample uncertainties such as:

* initial fire position inside the detected pixel
* wind speed and direction bias
* fuel-model category
* fuel moisture
* spread-rate multiplier
* detection confidence
* barrier permeability
* optional spotting distance

Start with perhaps dozens or a few hundred members, depending on AOI size. A GPU is not necessarily required for a raster fast-marching implementation.

## Sequential assimilation

At every new valid satellite observation:

1. Propagate each ensemble member from the previous observation.
2. Compare its predicted state with:

 * positive detections
 * valid non-fire pixels
 * optical burn-likelihood imagery
3. Assign each member a likelihood score.
4. Remove or down-weight inconsistent members.
5. Resample and continue.

A particle-filter approach is particularly suitable because the possible fire extent can split, merge and remain multimodal. The output is a probability field rather than a falsely precise single perimeter.

---

# 8. Grid resolution and timestep

For this local area:

* **static/model grid:** 20-60 m
* **propagation timestep:** 10-30 minutes
* **assimilation timestamps:** actual acquisition times
* **display contours:** hourly, six-hourly or daily depending on zoom

A 30 m grid does **not** imply 30 m observational accuracy. It allows slope, fuels and barriers to be represented at useful resolution, while uncertainty envelopes retain the coarser satellite uncertainty.

For MODIS-only periods, the uncertainty envelope may remain hundreds of metres or more even though the computation runs at 30 m.

---

# 9. Visual design

## Historical age layer

Store the model’s median arrival time as a single raster band.

Suggested encoding:

* 21-28 days old: dark burgundy
* 14-21 days: dark red
* 7-14 days: medium red
* 2-7 days: coral red
* under 48 hours: pale or bright salmon
* latest observed front: solid, highly visible red outline

Interpolate colours in a perceptual colour space such as HCL or OKLCH rather than simple RGB interpolation.

Keep age colour and uncertainty separate:

* **lightness:** age
* **stipple or hatching:** modelled/inferred region
* **outline style:** data source
* **optional opacity:** confidence, within a narrow range

Avoid making very recent areas nearly transparent merely because they are lighter.

## Recommended map layers

From bottom to top:

1. terrain/satellite basemap
2. land-cover or fuel layer, optional
3. cumulative arrival-time raster
4. uncertainty stipple
5. modelled time contours
6. raw satellite footprints
7. latest observed front
8. labels and legend

Use different outlines:

* solid: directly observed or optical-derived
* dashed: modelled median
* dotted: uncertainty envelope

The tooltip should include:

* acquisition time in UTC and local time
* sensor/platform
* confidence
* FRP
* footprint size
* observed versus inferred status
* model run/version

---

# 10. Local system architecture

A practical stack would be:

### Storage

* PostgreSQL/PostGIS for detections, events, footprints and perimeters
* Cloud-Optimized GeoTIFFs for static rasters
* Zarr for time-varying weather and model cubes
* Parquet for intermediate feature tables

### Processing

* GDAL/rasterio
* pyproj
* shapely/geopandas
* xarray/rioxarray
* scipy/scikit-image
* numba, C++ or Rust for the spread solver
* PyTorch only when you have enough labelled data to justify learned segmentation

### Model products

Persist:

* median arrival-time raster
* lower and upper arrival-time bounds
* probability-of-impact raster for each time frame
* active-heat likelihood raster
* vector probability contours
* provenance and model-configuration JSON

### Delivery

* raster tiles for arrival-time and probability fields
* vector or PMTiles for contours and observation footprints
* MapLibre GL JS with deck.gl for the client
* WebGL colour ramp controlled by the time slider

For one local AOI, precomputed raster tiles will usually be simpler and more stable than trying to animate thousands of overlapping browser polygons.

---

# 11. Validation programme

Do not validate only on the same four weeks used to tune the model.

Build a historical dataset of fires from similar:

* vegetation
* terrain
* season
* climate
* satellite viewing conditions

For Europe, EFFIS or national/regional perimeter data can provide final reference boundaries for suitable larger events. EFFIS notes that final delineations are refined with high-resolution imagery and preserve significant unburned islands. ([Joint Research Centre][8])

## Rolling holdout test

For each event:

1. Provide the model observations through time (t_k).
2. Hide the next observation.
3. Predict through (t_{k+1}).
4. Compare with the hidden positive and negative observations.
5. Repeat throughout the event.

## Metrics

Measure separately:

* Intersection over Union
* precision and recall
* area bias
* 95th-percentile boundary distance
* centroid displacement
* arrival-time error
* Brier score for probabilities
* probability calibration
* missed disconnected fronts
* false bridging across barriers

Google’s published evaluation similarly uses fire scars, precision/recall and spatial error distances rather than judging only whether a polygon looks reasonable. ([Google Research][14])

Compare your model against simple baselines:

* buffered FIRMS footprints
* time-decayed kernel density
* concave hull
* constant radial growth
* wind-only elliptical growth
* physical model without observation assimilation

Do not proceed to the visually polished model unless it reliably beats these baselines.

---

# 12. Recommended build order

## Stage 1 - Observation-correct visualization

Deliver:

* sensor-correct footprints
* acquisition-time animation
* age colour ramp
* confidence and source filtering
* no propagation
* explicit unknown/coverage layer

This immediately improves the first screenshot without making unsupported area claims.

## Stage 2 - Probabilistic observation reconstruction

Deliver:

* tri-state observations
* sensor-specific likelihood kernels
* active-heat decay
* event association
* uncertainty contours

## Stage 3 - Optical burn-scar anchoring

Deliver:

* Sentinel-2/Landsat pre/post comparison
* NBR/dNBR-derived likelihood
* cumulative extent with unburned islands
* official-perimeter comparison where available

## Stage 4 - Observation-constrained propagation

Deliver:

* terrain/fuel/weather speed field
* ensemble propagation
* sequential assimilation
* arrival-time raster
* calibrated probability envelopes

## Stage 5 - Optional learned model

Only after collecting a substantial training archive, consider:

* U-Net-style burn segmentation
* temporal convolution or recurrent models
* learned correction of physical-model residuals
* sensor-fusion models using raw thermal and optical bands

Do not begin with a neural network trained on one location and four weeks of data. Use ML later to correct a tested physical/statistical baseline, not to replace basic observation geometry and quality handling.

---

# 13. Highest-value accuracy improvements

In order of likely impact:

1. **Valid no-fire observations and cloud/coverage masks**
2. **Correct sensor footprint and geolocation handling**
3. **Sentinel-2/Landsat burn-scar anchoring**
4. **Terrain-downscaled winds**
5. **Locally calibrated fuel mapping**
6. **Ensemble uncertainty rather than a single perimeter**
7. **Sequential assimilation at every observation**
8. **Historical validation on comparable fires**
9. **Only then, machine learning**

The biggest mistake would be investing heavily in a sophisticated spread equation while treating missing FIRMS points as negative observations and treating each detection rectangle as fully burned.

The most defensible final interface would label results plainly as **Observed**, **Estimated**, or **Modelled**, show the time of the latest usable source, and retain a visible research/non-authoritative designation.

[1]: https://www.earthdata.nasa.gov/data/tools/firms/faq?utm_source=chatgpt.com "FIRMS FAQ"
[2]: https://sites.research.google/gr/wildfires/boundary-tracking/ "Boundary tracking - Wildfires"
[3]: https://www.earthdata.nasa.gov/data/tools/firms/faq "FIRMS FAQ | NASA Earthdata"
[4]: https://forum.earthdata.nasa.gov/viewtopic.php?t=5188 "What caveats should be considered when using active fire data from FIRMS? - Earthdata Forum"
[5]: https://www.earthdata.nasa.gov/s3fs-public/2025-06/arset-firms2025-part3-qa.pdf?utm_source=chatgpt.com "Part 3 FIRMS 2025 Q&A Session.docx"
[6]: https://dataspace.copernicus.eu/data-collections/copernicus-sentinel-missions/sentinel-2 "Sentinel-2 | Copernicus Data Space Ecosystem"
[7]: https://www.usgs.gov/landsat-missions/landsat-normalized-burn-ratio "Landsat Normalized Burn Ratio | U.S. Geological Survey"
[8]: https://joint-research-centre.ec.europa.eu/projects-and-activities/natural-and-man-made-hazards/forest-fires/current-wildfire-situation-europe_en?prefLang=es "Current wildfire situation in Europe - Joint Research Centre"
[9]: https://research.fs.usda.gov/treesearch/55928 "The Rothermel surface fire spread model and associated developments: A comprehensive explanation | US Forest Service Research and Development"
[10]: https://research.fs.usda.gov/firelab/products/dataandtools/flammap "FlamMap | US Forest Service Research and Development"
[11]: https://forest-fire.emergency.copernicus.eu/about-effis/technical-background/fuels "EFFIS - Fuels"
[12]: https://research.fs.usda.gov/firelab/products/dataandtools/windninja "WindNinja | US Forest Service Research and Development"
[13]: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=overview "ERA5-Land hourly data from 1950 to present"
[14]: https://research.google/blog/real-time-tracking-of-wildfire-boundaries-using-satellite-imagery/?m=0 "Real-time tracking of wildfire boundaries using satellite imagery"
Do **not** exclude entire industrial areas. Instead, build a **persistent thermal-source classifier** that treats OpenStreetMap as one source of prior evidence and historical satellite behaviour as the decisive evidence.

A factory can contain a genuine vegetation fire, an accidental industrial fire, or a wildfire passing through it. A broad `landuse=industrial` mask would therefore reduce false positives but create dangerous false negatives.

NASA follows a similar general strategy with its experimental Static Thermal Anomalies layer: it first identifies recurrent thermal detections, then spatially filters them using industrial and power-plant datasets. NASA’s current implementation uses S-NPP VIIRS detections summarized on a 400 m grid and initially selects cells with at least five detections during 2023. ([NASA Earthdata][1])

## 1. Define the classification target

First distinguish these classes:

| Class | Treatment in your wildfire map |
| ---------------------------------- | ------------------------------------------ |
| Vegetation fire | Include normally |
| Persistent industrial heat | Exclude from the wildfire layer |
| Accidental industrial fire | Show separately. Do not call it a wildfire |
| Agricultural or prescribed burning | Separate class where possible |
| Reflective/sensor artefact | Exclude |
| Volcano or geothermal source | Separate static-source class |
| Uncertain | Retain with low confidence |

This matters because an industrial fire is not necessarily a false satellite detection. It is only a false positive relative to a **vegetation-wildfire product**.

FIRMS NRT MODIS and VIIRS records normally do not attribute the type of thermal anomaly, and the reported coordinate is the centre of a potentially much larger sensor pixel rather than the exact heat-source location. ([NASA Earthdata][1])

## 2. Build a persistent thermal-source registry

Create a PostGIS table such as:

```text
thermal_source
--------------
source_id
geometry
source_type
facility_id
facility_name
operational_status
source_data
source_confidence
first_seen
last_seen
learned_hotspot_geometry
expected_sensor_signature
review_status
```

Maintain two geometries:

* `facility_geometry`: the complete industrial site
* `learned_hotspot_geometry`: the particular flare, kiln, furnace or stack area producing detections

The second is much more important. A refinery might occupy several square kilometres while its recurring thermal detections come from two flare stacks occupying a tiny portion of the property.

NASA lists power plants, cement plants, gas flares, steel plants, nonferrous-metal facilities and petrochemical facilities among the external datasets used in its own static-anomaly work. ([NASA Earthdata][1])

For Europe, supplement OSM with the European Industrial Emissions Portal. Its downloadable dataset contains facility locations and administrative data for major industrial complexes, including detailed information for large combustion plants. ([industry.eea.europa.eu][2])

## 3. Use OSM tags as weighted evidence

### Strong point-source evidence

These should receive the strongest industrial prior:

| OSM feature | Suggested interpretation |
| ------------------------------------------ | ------------------------------------------------------ |
| `man_made=flare` | Known gas-flare location |
| `man_made=kiln` | Possible cement, lime, ceramic or industrial kiln |
| `industrial=refinery` | Refinery facility |
| `power=plant` | Power-generation facility |
| `power=generator` with thermal source tags | Individual generator or combustion unit |
| `man_made=petroleum_well` | Oil/gas extraction context |
| `man_made=works` with `product=*` | Manufacturing facility with useful product information |

OSM explicitly defines `man_made=flare` as a structure that burns excess gas, while `man_made=kiln`, `industrial=refinery`, `power=plant`, and `man_made=works` describe relevant industrial structures and facilities. ([OpenStreetMap][3])

### Moderate evidence

These provide context but should not cause automatic suppression:

```text
landuse=industrial
building=industrial
man_made=chimney
industrial=*
power=generator
power=plant
```

For example, a chimney may be inactive, used only occasionally or associated with a facility that does not normally produce a satellite-detectable thermal signal.

### Weak or misleading evidence

Do not suppress detections merely because they intersect:

```text
landuse=industrial
building=warehouse
industrial=storage
commercial areas
rail yards
ports
power=substation
```

Many such areas contain no large heat source.

Solar plants should generally be placed in a separate **reflection-risk** layer rather than the persistent-heat layer. MODIS and VIIRS already apply tests intended to reduce false alarms from bright surfaces and sun glint, including reflective factory roofs, but artefacts can still warrant contextual treatment. ([forum.earthdata.nasa.gov][4])

## 4. Learn the industrial signature from historical FIRMS data

OSM tells you where an industrial source may exist. Historical FIRMS data tells you whether it actually behaves like one.

For each candidate source, process several years of MODIS and VIIRS observations and calculate:

### Persistence

Do not use only the raw number of detections. Calculate:

[
\text{occupancy} =
\frac{\text{positive detections}}
{\text{valid clear satellite observations}}
]

This prevents cloudier locations from appearing less persistent simply because they were observed less often.

Useful persistence features include:

* number of distinct detected days
* number of distinct months
* detections across multiple years
* longest interval between detections
* seasonal concentration
* day-versus-night frequency
* detections per valid overpass

A genuine static source commonly recurs at approximately the same location over long periods. NASA also uses persistence to distinguish industrial sources from shorter-duration wildfire events. ([NASA Earthdata][5])

### Spatial stationarity

Calculate the detection distribution separately by sensor:

* weighted centroid
* covariance ellipse
* median distance from centroid
* 90% and 99% containment regions
* multimodal clusters where a facility contains several hot units

A persistent industrial source should normally produce a compact, repeatable distribution once pixel size, scan angle and geolocation uncertainty are considered.

Do not compare the raw MODIS and VIIRS coordinate spread directly. Their footprints and off-nadir behaviour differ, and the actual thermal source can lie anywhere inside the flagged pixel. ([NASA Earthdata][1])

### Radiometric fingerprint

Maintain separate distributions for each sensor and day/night state:

```text
FRP median and interquartile range
brightness temperature
brightness-temperature difference
confidence distribution
scan-angle distribution
day/night ratio
```

Use these as supporting features, not decisive rules. NASA cautions that confidence has different meanings for MODIS and VIIRS and can vary in usefulness geographically. FRP is also influenced by sensor resolution, view angle and observation time. ([NASA Earthdata][1])

### Temporal fingerprint

Industrial activity may exhibit:

* regular nightly detections
* weekday or production-cycle patterns
* stable year-round operation
* shutdown periods followed by resumed activity
* highly repetitive FRP ranges

Wildfires instead usually produce an event-shaped sequence: appearance, spatial growth and eventual disappearance.

Do not require year-round persistence. Cement kilns, biomass plants, foundries and flares may operate intermittently.

## 5. Match using sensor footprints, not point distance

A fixed “within 500 m of a factory” rule will perform poorly.

For every detection:

1. Construct its approximate pixel footprint from sensor, scan and track.
2. Intersect the footprint with known source geometries.
3. Calculate intersection proportion and distance to the learned thermal centroid.
4. Expand uncertainty for large scan angles.
5. Calculate the same features against the complete facility polygon.

Useful matching features are:

```text
footprint intersects exact flare or kiln
percentage of footprint inside facility
normalized distance to learned hot centroid
distance to facility boundary
distance to surrounding burnable vegetation
sensor and scan angle
```

For a point source such as a flare, a provisional matching region can be:

[
r =
\frac{1}{2}\text{pixel diagonal}
+
\text{geolocation allowance}
+
\text{historical dispersion}
]

However, the learned sensor-specific containment ellipse will eventually outperform a circular buffer.

## 6. Classify events, not only individual points

A single FIRMS row near a refinery is ambiguous. A sequence is much more informative.

Build features over a rolling event window:

* Does the centroid remain stationary?
* Are detections confined to the same one or two pixels?
* Is the number of affected cells expanding?
* Is movement aligned across successive overpasses?
* Do detections emerge in nearby vegetation?
* Does the event connect to an existing wildfire object?
* Is there an optical burn scar?
* Do valid no-fire observations surround the facility?
* Is the source active both before and after the candidate event?

A static source generally remains compact. A vegetation fire tends to create new detections outside the historical industrial envelope.

One useful feature is:

[
G =
\frac{\text{new detection area outside static-source envelope}}
{\Delta t}
]

A sustained positive (G), particularly into burnable vegetation, should rapidly lower the static-source probability.

## 7. Use a probabilistic classifier with an abstention class

Produce at least:

```text
P(persistent_industrial)
P(vegetation_fire)
P(reflective_artifact)
P(other_thermal_source)
P(unknown)
```

A practical first implementation is:

1. Transparent rule-based score
2. Calibrated logistic regression or gradient-boosted trees
3. Probability calibration on held-out facilities
4. More complex sequence model only after sufficient labels exist

A conceptual score could be:

[
S_{\text{industrial}} =
w_1O
+w_2P
+w_3S
+w_4R
-w_5G
-w_6B
]

where:

* (O): OSM/registry source evidence
* (P): historical persistence
* (S): spatial stationarity
* (R): matching radiometric signature
* (G): current outward growth
* (B): optical burn evidence outside the facility

Use three decisions rather than a binary cutoff:

| Result | Model action |
| --------------------------- | ---------------------------------------------------------- |
| High industrial probability | Suppress from vegetation-fire rendering |
| High wildfire probability | Include normally |
| Ambiguous | Retain as uncertain and give it reduced propagation weight |

Choose the exclusion threshold from validation data to achieve very high **industrial-class precision**. A false industrial classification removes a genuine fire, so suppression should require substantially stronger evidence than merely displaying a possible detection.

## 8. Add a wildfire-overrides-industrial rule

This is essential for reducing false negatives.

Even a normally persistent source should be temporarily “unmasked” when:

* a wildfire approaches the site from outside
* adjacent vegetation pixels begin activating
* detections expand beyond the historical source envelope
* multiple sensors confirm the expansion
* Sentinel-2, Landsat or HLS shows new burn change
* the source’s FRP or spatial pattern changes dramatically
* detections continue along a plausible propagation path after leaving the facility

Do not permanently change the source classification. Create an event-specific state:

```text
NORMAL_STATIC
STATIC_WITH_ANOMALOUS_ACTIVITY
EXTERNAL_FIRE_INTERSECTION
POSSIBLE_INDUSTRIAL_INCIDENT
UNKNOWN
```

Thus the same flare can be excluded on ordinary days but included as contextual evidence when a vegetation fire moves through its surroundings.

## 9. Never physically delete excluded detections

Keep every original record and add fields such as:

```text
classification
classification_probability
classification_reason
matched_source_id
model_version
excluded_from_wildfire_layer
override_reason
```

Your public map can hide classified static sources by default, but provide a toggle such as:

> Industrial/static thermal detections

This makes debugging possible and prevents silent data loss.

NASA’s own Static Thermal Anomalies layer is currently described as reference-only and unavailable for distribution, so you would need your own source registry for the backend even though FIRMS can be useful for visual QA. ([NASA Earthdata][1])

## 10. Validation design

Build a labelled dataset containing:

* known persistent industrial sources
* ordinary wildfires far from industry
* wildfires close to industrial areas
* wildfires entering facility grounds
* accidental industrial fires
* agricultural burns near industry
* solar-reflection artefacts
* industrial shutdowns
* newly opened or unmapped facilities

Split validation by **facility and fire event**, not by individual detection. Otherwise, detections from the same facility can leak into both training and testing and make the results look unrealistically strong.

Measure:

| Metric | Why it matters |
| ----------------------------- | ---------------------------------------------------------------- |
| Static-source precision | How often suppression is correct |
| Static-source recall | How many recurring sources remain visible |
| Wildfire recall near industry | Detects over-aggressive masks |
| False-suppression rate | Real fires incorrectly hidden |
| Event-level false-alarm rate | More meaningful than row-level errors |
| Probability calibration | Whether 90% predictions are correct about 90% of the time |
| Time to reclassify | How quickly a static location is unmasked when behaviour changes |

Give additional weight to the **false-suppression rate**, because suppressing a real event is less visible and harder to discover than showing an extra uncertain point.

## Recommended implementation order

1. Import exact OSM flare, kiln, refinery, thermal-plant and works features.
2. Add EEA and other facility inventories.
3. Generate multi-year, sensor-specific recurrence maps.
4. Learn compact thermal-source envelopes around each facility.
5. Implement high-confidence static suppression.
6. Add an ambiguous class instead of forcing every point into fire or non-fire.
7. Add event growth and neighbourhood features.
8. Add the wildfire-overrides-industrial mechanism.
9. Validate specifically on fires occurring near industrial sites.
10. Add optical burn-change confirmation.

The central rule should be:

> **OSM creates a candidate, recurrence confirms it, stationarity strengthens it, propagation or burn evidence overrides it.**

[1]: https://www.earthdata.nasa.gov/data/tools/firms/faq "FIRMS FAQ | NASA Earthdata"
[2]: https://industry.eea.europa.eu/industrial-emissions/about?utm_source=chatgpt.com "About - European Industrial Emissions Portal"
[3]: https://wiki.openstreetmap.org/wiki/Tag%3Aman_made%3Dflare?utm_source=chatgpt.com "Tag:man_made=flare - OpenStreetMap Wiki"
[4]: https://forum.earthdata.nasa.gov/viewtopic.php?t=5188 "What caveats should be considered when using active fire data from FIRMS? - Earthdata Forum"
[5]: https://earthdata.nasa.gov/s3fs-public/2025-12/arset-2025-advfirms-part2-qa.pdf?VersionId=lKzbmPrSwOPdT2FxZqydueHVyUx5L75V&utm_source=chatgpt.com "Part 2 Questions & Answers Session"


Yes. For a genuinely independent second thermal source in Europe, the strongest choice is **EUMETSAT’s LSA SAF Fire Radiative Power products**.

## Recommended European data stack

### 1. EUMETSAT LSA SAF - best independent confirmation source

LSA SAF produces Fire Radiative Power detections from European Meteosat satellites rather than from MODIS or VIIRS:

* **MSG/SEVIRI FRP-Pixel** for the established historical/operational record
* **MTG/FCI FRP-Pixel** as the newer-generation product
* detection position, acquisition time and FRP
* European and African coverage
* frequent geostationary observations

The newer MTG product is currently published as a demonstration product with approximately:

* 1 km spatial sampling
* observations every 10 minutes
* typical latency around 20 minutes
* availability back-processed to January 2025

Because it observes every few minutes, it is particularly useful for distinguishing a stationary industrial heat source from a moving or expanding wildfire. Its current demonstration status means it should not be your only source, however. ([lsa-saf.eumetsat.int][1])

The older Meteosat/SEVIRI product has coarser spatial resolution, but it provides a much longer historical record. Geostationary data generally give much better temporal sampling than polar-orbiting satellites, while polar sensors such as VIIRS provide better spatial resolution. ([lsa-saf.eumetsat.int][1])

**Best use in your model:** temporal confirmation and behavioural classification.

For example:

```text
FIRMS detection
 +
Meteosat detections remain stationary for months
 +
known industrial facility
 =
strong persistent-source evidence
```

Conversely:

```text
FIRMS detection
 +
Meteosat shows expansion over successive 10-minute observations
 =
strong evidence against static-source suppression
```

---

### 2. Copernicus Sentinel-3 SLSTR FRP - best independent polar-orbiting source

The Copernicus Sentinel-3 satellites produce a dedicated **SLSTR Level-2 Fire Radiative Power product**. Both near-real-time and non-time-critical versions are available through the Copernicus Data Space catalogue. The product reports detected fires and FRP on the SLSTR fire grid, approximately 1 km for the thermal-fire product. ([documentation.dataspace.copernicus.eu][2])

This is useful because it gives you:

* a European-operated satellite source
* a separate instrument
* a separately maintained processing chain
* NRT data for current processing
* NTC data for later historical reprocessing
* fire locations, quality information and FRP

Use NRT immediately, then replace or reconcile it with NTC when the latter becomes available.

Compared with VIIRS:

| Source | Approximate strength |
| -------------------- | -------------------------------------------------- |
| VIIRS 375 m | Better localization and smaller-fire sensitivity |
| Sentinel-3 SLSTR FRP | Independent confirmation and additional overpasses |
| Meteosat MTG/FCI | Excellent temporal development |
| Sentinel-2 | Detailed burned-surface confirmation |

A detection found by both VIIRS and Sentinel-3 is stronger evidence than two detections from different FIRMS feeds that originate from closely related processing systems.

---

### 3. EFFIS - excellent, but understand which layers are independent

The **European Forest Fire Information System**, part of Copernicus Emergency Management Service, is valuable for:

* European burned-area perimeters
* fire-event context
* historical validation
* Sentinel-2-refined fire scars
* land-cover filtering
* comparison with your modelled perimeter

However, **EFFIS’s active-fire hotspot layer is sourced from NASA FIRMS**. Therefore, an EFFIS hotspot must not count as independent confirmation of a FIRMS hotspot. ([EFFIS][3])

EFFIS does apply additional filtering based on factors including land cover, artificial surfaces, urban proximity and hotspot confidence. That filtering may be useful as another classifier output, but the underlying thermal detection is still FIRMS-derived. ([EFFIS][3])

EFFIS’s burned-area product is more useful as an independent or partially independent validation layer. It uses semi-automatic processing, ancillary information, visual verification and Sentinel-2 imagery at 20 m to refine fire perimeters. ([EFFIS][4])

Therefore, represent the EFFIS products differently:

```text
EFFIS active hotspot:
 derived evidence, not independent sensor confirmation

EFFIS Sentinel-2-refined burned perimeter:
 valuable perimeter and retrospective validation evidence
```

---

### 4. European Industrial Emissions Portal - best addition to OSM

For factory filtering, combine OSM with the **European Industrial Emissions Portal**, maintained by the European Environment Agency.

Its downloadable industrial reporting dataset contains:

* facility locations
* industrial activities
* major industrial complexes
* pollutant releases
* large combustion plants
* energy-input and emissions information

The portal covers more than 60,000 industrial sites across approximately 65 economic activities. ([industry.eea.europa.eu][5])

This is better than relying on OSM alone because it is based on regulatory reporting. OSM will usually have better local geometries and details such as flare stacks, while the EEA dataset provides stronger evidence that a major regulated facility actually exists.

Use them together:

```text
OSM:
precise buildings, flare stacks, chimneys, land use and plant boundaries

EEA Industrial Emissions Portal:
official facility identity, industrial category and operational context

Historical thermal detections:
proof that the facility actually produces a repeatable satellite signature
```

Do not mask every EEA facility. Some facilities do not emit detectable heat, and real wildfires can occur within or beside them.

---

## The combination I would implement

### Immediate pipeline

1. **FIRMS VIIRS/MODIS**

 * primary high-resolution thermal observations

2. **EUMETSAT LSA SAF Meteosat FRP**

 * high-frequency temporal corroboration
 * stationarity versus movement
 * rapid development evidence

3. **Sentinel-3 SLSTR FRP**

 * independent polar-orbiting confirmation
 * additional acquisition geometry

4. **EFFIS burned-area perimeters**

 * event identification
 * retrospective validation
 * perimeter comparison

5. **Sentinel-2 imagery**

 * local burned-surface evidence
 * final scar and unburned-island mapping

6. **OSM + EEA Industrial Emissions Portal**

 * industrial-source priors

## Evidence independence should be explicit

Store a lineage group for every observation:

```text
NASA_FIRMS_MODIS
NASA_FIRMS_VIIRS
EUMETSAT_MSG_SEVIRI
EUMETSAT_MTG_FCI
COPERNICUS_SENTINEL3_SLSTR
COPERNICUS_SENTINEL2_OPTICAL
EFFIS_FIRMS_DERIVED
EFFIS_OPTICAL_PERIMETER
```

Then avoid double-counting derived information.

For example, this would be incorrect:

```text
VIIRS FIRMS + EFFIS active hotspot = two independent confirmations
```

It is really the same underlying detection.

This is stronger:

```text
VIIRS FIRMS
+ Sentinel-3 SLSTR
+ Meteosat temporal sequence
+ Sentinel-2 burn change
= four materially different evidence streams
```

## Suggested classification logic

A detection near industry should only be automatically suppressed when all or nearly all of these are true:

* inside or near an OSM/EEA industrial facility
* matches a historically learned thermal centroid
* repeatedly observed over months or years
* stationary in Meteosat observations
* radiometric behaviour matches the facility baseline
* no new detections expand into vegetation
* no Sentinel-2 burn change appears outside the facility
* no existing wildfire approaches from outside

Immediately revoke suppression when:

* the detected area expands
* its centroid moves
* surrounding vegetation activates
* Sentinel-3 independently detects an abnormal pattern
* Meteosat shows rapid temporal growth
* optical imagery indicates a new burn scar

For your European implementation, **FIRMS + LSA SAF + Sentinel-3 SLSTR + Sentinel-2/EFFIS + EEA industrial facilities** would provide a substantially stronger and more independent system than using FIRMS and OSM alone.

[1]: https://lsa-saf.eumetsat.int/en/data/products/fire-products/ "Fire Products"
[2]: https://documentation.dataspace.copernicus.eu/APIs/STAC.html "STAC product catalogue - Documentation"
[3]: https://effis.jrc.ec.europa.eu/about-effis/technical-background/active-fire-detection "EFFIS - Active Fire Detection"
[4]: https://effis.jrc.ec.europa.eu/about-effis/technical-background/rapid-damage-assessment "EFFIS - Rapid Damage Assessment"
[5]: https://industry.eea.europa.eu/industrial-emissions/about?utm_source=chatgpt.com "About - European Industrial Emissions Portal"


MAP KEY: 63fa1ee93ab783af359e8bf00c5fde52

Note: The MAP KEY is valid for both FIRMS (Global) and FIRMS (US/Canada) sites.

Transaction limit: 5000 transactions / 10 minutes (view status) 

FIRMS MAP_KEY limit is 5000 transactions / 10-minute interval.
Larger transactions may count as multiple requests (ex. requesting 7 days).
Contact us if you need limit increase. 


**OpenStreetMap does not provide a complete, continuous terrain-height dataset** such as a digital elevation model (DEM).

It does contain some elevation-related information:

* Individual mapped features - such as mountain peaks, passes, survey points, and sometimes entrances or stations - may have an `ele=*` tag. This records elevation in metres above mean sea level, using the EGM96 vertical datum. Coverage is voluntary and uneven. ([OpenStreetMap][1])
* The separate `height=*` tag generally describes the physical height of an object, such as a building, rather than the ground elevation. ([OpenStreetMap][2])
* OSM’s core geometry is essentially two-dimensional. It does not give a reliable terrain elevation for every latitude/longitude coordinate. ([OpenStreetMap][3])

Topographic products based on OSM usually combine OSM roads, trails, buildings, and labels with an external elevation model. For example, **OpenTopoMap** uses OSM map data together with DEM data from SRTM and Sonny to produce contours, hill shading, and 3D terrain. ([OpenTopoMap][4])

Therefore:

* **Need the elevation of a mapped summit?** Check its OSM `ele` tag.
* **Need terrain height at arbitrary coordinates, contours, slope, or elevation profiles?** Use a DEM such as Copernicus DEM, SRTM, national LiDAR/DTM data, or another dedicated elevation service, optionally combined with OSM.

[1]: https://wiki.openstreetmap.org/wiki/Key%3Aele?utm_source=chatgpt.com "Key:ele - OpenStreetMap Wiki"
[2]: https://wiki.openstreetmap.org/wiki/Key%3Aheight?utm_source=chatgpt.com "Key:height - OpenStreetMap Wiki"
[3]: https://wiki.openstreetmap.org/wiki/Altitude?utm_source=chatgpt.com "Altitude - OpenStreetMap Wiki"
[4]: https://opentopomap.org/?utm_source=chatgpt.com "OpenTopoMap - Topographische Karten aus OpenStreetMap"


**Yes - but “free” depends on what you are using.**

| Item | Free of charge? | Reuse terms |
| -------------------------------- | -----------------------------: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **OpenTopoMap website** | Yes | You can browse it without payment. |
| **Online map tiles** | Generally yes for ordinary use | The online map is licensed under **CC BY-SA**. You must provide attribution and share adaptations under compatible terms. OpenTopoMap asks users planning larger projects or significant tile traffic to contact them. Free access is not an unlimited hosting guarantee. ([GitHub][1]) |
| **OpenStreetMap source data** | Yes | Licensed under **ODbL 1.0**. It permits commercial and non-commercial use, but requires attribution and share-alike treatment of publicly distributed derived databases. ([OpenStreetMap Foundation][2]) |
| **SRTM elevation data** | Yes | The underlying US government SRTM elevation data is available as public-domain data. ([US Geological Survey][3]) |
| **OpenTopoMap Garmin downloads** | Yes to download | These have a different licence: **CC BY-NC-SA 4.0**, so they may not be resold or used commercially. ([garmin.opentopomap.org][4]) |

### Practical interpretation

For a small website or personal application, you can normally display OpenTopoMap tiles with attribution such as:

> Map data © OpenStreetMap contributors, map rendering © OpenTopoMap

For a commercial, high-traffic, offline, or bulk-download product, it is safer to **host your own tiles** using the open OpenTopoMap rendering code plus downloaded OSM and elevation data, rather than relying heavily on the public tile server. The rendering project’s necessary server-building files are publicly available. ([GitHub][1])

So the **underlying data is genuinely reusable**, but the public tile server is a donated service, not a limitless free map API.

[1]: https://github.com/der-stefan/OpenTopoMap?utm_source=chatgpt.com "der-stefan/OpenTopoMap: A topographic map ..."
[2]: https://osmfoundation.org/licence?utm_source=chatgpt.com "Licence"
[3]: https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm-1?utm_source=chatgpt.com "Shuttle Radar Topography Mission (SRTM) 1 Arc-Second ..."
[4]: https://garmin.opentopomap.org/?utm_source=chatgpt.com "OpenTopoMap Garmin Maps"



Also check if we can use some of the following models/algorithms to better model fire behaviour.

The FlamMap fire mapping and analysis system (Finney 2006) describes potential fire behavior for constant environmental conditions (weather and fuel moisture). Fire behavior is calculated for each pixel within the landscape file independently. Potential fire behavior calculations include surface fire spread, flame length, crown fire activity type, crown fire initiation, and crown fire spread. Dead fuel moisture and conditioning of dead fuels in each pixel based on slope, shading, elevation, aspect, and weather. With the inclusion of FARSITE, FlamMap can now compute wildfire growth and behavior with detailed sequences of weather conditions.

The FlamMap fire mapping and analysis system includes FARSITE (Finney 1998, 2004) and FlamMap BASIC (Finney 2006), Minimum Travel Time (MTT, Finney 2002, 2006), Treatment Optimization Model (Finney 2001, 2006, 2007), and Conditional Burn Probability (Finney 2005, 2006). It incorporates the following fire behavior models:

 Rothermel's (1972) surface fire spread model,
 Van Wagner's (1977) crown fire initiation model,
 Rothermel's (1991) crown fire spread model,
 Albini's (1979) spotting model,
 Finney’s (1998) or Scott and Reinhardt’s (2001) crown fire calculation method, and
 Nelson's (2000) dead fuel moisture model. This allows conditioning of dead fuels in each pixel based on slope, shading, elevation, aspect, and weather.

Because environmental conditions remain constant when using FlamMap, MTT, Burn Probability, and TOM it will not simulate temporal variations in fire behavior caused by weather and diurnal fluctuations as FARSITE does. Nor will it display spatial variations caused by backing or flanking fire behavior. These limitations need to be considered when viewing FlamMap output using these models in a relative sense rather than absolute sense. However, these outputs are well-suited for landscape level comparisons of fuel treatment effectiveness because fuel is the only variable that changes. Outputs and comparisons can be used to identify combinations of hazardous fuel and topography, aiding in prioritizing fuel treatments.

The FlamMap software creates a variety of vector and raster maps of potential fire behavior characteristics (for example, spread rate, flame length, crown fire activity) and environmental conditions (dead fuel moistures, mid-flame wind speeds, and solar irradiance) over an entire landscape or for specific modeling applications these same outputs are limited to the simulation footprint (MTT and FARSITE). These raster maps can be viewed in FlamMap or exported for use in a GIS, or image format.

The FlamMap software also creates a variety of vector outputs specific to each modeling system within the application. Gridded wind vectors are produced whenever WindNinja is used within the application and information on spotting (tabular and shapefile format) are also created. MTT creates MTT flow paths and MTT Arrival Contours. Within FARSITE, Wind and Spread Vectors, and FARSITE Perimeters are also produced.

We will also need starting scripts for linux, mac and windows.

We will also need a easy installation wizard script that basically works through setting everything up, even the API keys needed as well as test of infrastructure/API reachability. This should also be made for mac, windows and linux.

----------------
------------------
--------------------


The following should not be a feature right now:

**Yes, but there is no single worldwide - or even Europe-wide - set of fire-brigade tactical symbols.** Several overlapping standards and operational conventions exist, depending on what the symbols represent.

| Purpose | Europe / example systems | United States |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Units, vehicles, command posts and tactical actions on incident maps** | Primarily national systems. Germany uses the *Taktische Zeichen im Bevölkerungsschutz* recommendations, often referred to as **DV/FwDV 102**. | **ICS/NIMS map symbols** for facilities and organization. Additional standardized wildfire symbols under NWCG guidance. |
| **Fire-service building and pre-incident plans** | National standards such as Germany’s **DIN 14034-6** and **DIN 14095**. | **NFPA 170**. |
| **Public fire-equipment and evacuation signs** | **ISO 7010**, generally adopted as EN ISO 7010 in Europe. | NFPA 170 and locally adopted building/fire codes. ISO signs may also be encountered. |
| **International urban search and rescue** | UN **INSARAG** marking systems. | Also used by internationally deployable USAR teams. |

## European situation

I could not identify a current **EN, ISO or EU standard defining a complete common tactical symbol set** for municipal fire brigades. European countries generally use their own fire-service or civil-protection doctrine.

Germany has one of the more systematic systems. The DV 102/SKK recommendations cover:

* fire brigades, police, rescue organizations and technical assistance.
* units such as teams, groups, platoons and formations.
* vehicles, equipment, command facilities and hazards.
* actions such as firefighting, rescue, water supply and technical assistance.
* colour coding - red for fire brigade, blue for THW, yellow for command facilities, green for police, and so forth.

The document describes itself as an open system intended to be interoperable and potentially suitable as a basis for future European or international standardization. That wording also indicates that it is **not itself a pan-European norm**. 

For German **building and pre-incident fire plans**, the relevant normative documents are different:

* **DIN 14034-6:2024-06** specifies graphical symbols for fire-brigade and fire-service operational plans.
* **DIN 14095:2025-07** specifies the requirements and contents of fire-brigade plans for buildings. ([dinmedia][1])

ISO 7010 covers standardized safety signs for fire protection, evacuation and accident prevention - for example extinguishers and fire alarms - but it does **not** describe tactical units or operational deployments. ([ISO][2])

## United States

The US similarly has several layers rather than one all-purpose symbol standard.

**NFPA 170, Standard for Fire Safety and Emergency Symbols**, is the principal consensus standard for symbols used in fire-safety plans, engineering drawings, pre-incident plans and emergency-management applications. The current published edition is the **2024 edition**. ([NFPA][3])

For operational incident maps, the **Incident Command System** uses common symbols for such facilities as:

* Incident Command Post.
* staging area.
* base and camp.
* helibase and helispots.
* branches and divisions.
* hazard or incident origin.

Wildland fire operations have a much more extensive operational mapping system. The **NWCG Standards for Geospatial Operations, PMS 936**, prescribe standard point, line and polygon symbology, map products and geospatial data structures for wildfire incidents. ([nwcg.gov][4])

There is also a broader US public-safety GIS symbol framework maintained by NAPSG. NAPSG explicitly notes that existing national and international standards do not cover every incident-level requirement, which is why supplementary symbol libraries are used. ([napsgfoundation.org][5])

## International specialist systems

For collapsed-structure and earthquake response, **INSARAG** defines internationally recognized USAR worksite-triage and structure-marking conventions. These complement the affected country’s own system. They are not a general fire-brigade tactical alphabet. ([insarag.org][6])

## Practical selection

For a symbol library or operational-planning project, the appropriate baseline would normally be:

* **Europe-wide public signs:** ISO/EN ISO 7010.
* **German tactical situation maps:** DV/FwDV 102 recommendations.
* **German building fire plans:** DIN 14034-6 and DIN 14095.
* **US preplans/building symbols:** NFPA 170.
* **US incident-command maps:** ICS conventions, supplemented by NWCG or NAPSG.
* **International USAR:** INSARAG.

For multinational operations, the safest method is to state the selected standard on every map, include a legend, and avoid relying on colour alone.

[1]: https://www.dinmedia.de/de/norm/din-14034-6/377898786?utm_source=chatgpt.com "DIN 14034-6 - 2024-06"
[2]: https://www.iso.org/standard/54432.html?utm_source=chatgpt.com "ISO 7010:2011 - Registered safety signs"
[3]: https://www.nfpa.org/product/nfpa-170-standard/p0170code?utm_source=chatgpt.com "Buy NFPA 170, Standard"
[4]: https://www.nwcg.gov/publications/pms936/symbology?utm_source=chatgpt.com "Symbology | NWCG"
[5]: https://www.napsgfoundation.org/all-resources/symbology-library/?utm_source=chatgpt.com "Symbol Library » NAPSG Foundation"
[6]: https://insarag.org/guidance-notes/guidelines-annex/volume-3/?utm_source=chatgpt.com "Volume III - INSARAG"


For **wildfire fighting**, the laptop service should be an **offline-first tactical planning system**, not merely a map viewer. It should help commanders answer:

1. Where is the fire now?
2. Where could it go?
3. What is at risk?
4. Where can crews safely work?
5. What is the primary plan, and what are the fallback plans?
6. What changed since the last operational period?

## 1. Core incident map

The initial screen should show an immediately usable map without requiring internet access.

Essential offline layers:

* topographic map, contours, elevation, slope and aspect.
* recent orthophotos or satellite imagery.
* vegetation, fuel type and fuel treatments.
* roads, forestry tracks, trails, gates and barriers.
* road width/class, surface, turning points and bridge restrictions.
* rivers, reservoirs, hydrants, tanks, drafting points and other water sources.
* buildings, addresses and wildland-urban interface areas.
* electricity lines, pipelines, substations and other infrastructure.
* administrative boundaries, land ownership and protected areas.
* historical fire perimeters and existing firebreaks.
* aviation obstacles, helipads and possible helicopter landing areas.

The incident workspace should be stored as a self-contained package. **GeoPackage** is a good primary format because it can hold vector features, raster-map tiles and attributes in one portable SQLite file and is specifically suitable for disconnected or bandwidth-limited environments. ([Open Geospatial Consortium][1])

## 2. Current fire situation

Every observation must have:

* observation time.
* source.
* observer or importing system.
* confidence or accuracy.
* whether it is confirmed or unconfirmed.

The system should represent:

* confirmed fire perimeter.
* contained and uncontained perimeter.
* active edge.
* inactive or cold edge.
* spot fires.
* smoke reports.
* satellite thermal detections.
* drone observations.
* observed direction and rate of spread.
* fire intensity or flame-length category.
* crown-fire activity.
* last known wind direction and speed.

Satellite detections must be displayed differently from a confirmed perimeter. NASA FIRMS supplies near-real-time MODIS and VIIRS thermal detections, but these are observations of thermal anomalies rather than exact tactical fire boundaries. ([NASA-FIRMS][2])

When connectivity becomes available, the application could update from services such as EFFIS, national fire services, weather services and FIRMS. EFFIS provides European fire-danger, active-fire, damage, fuel and risk-related information, but the locally confirmed incident data should remain authoritative for tactical use. ([EFFIS][3])

## 3. Tactical planning tools

The most important function is drawing and managing the firefighting plan.

### Tactical lines

Users should be able to create lines classified as:

* proposed.
* approved or planned.
* under construction.
* completed.
* held.
* breached.
* abandoned.
* requiring patrol.
* requiring mop-up or repair.

Each line should also state the method:

* hand line.
* machine or dozer line.
* road used as control line.
* wet line.
* hose lay.
* retardant line.
* natural barrier.
* burnout or backburn line.
* indirect attack line.
* structure-protection line.

The North American NWCG strategic-map concept distinguishes primary, secondary and proposed strategic lines. That is a useful model even where local terminology differs: one visible main plan, one contingency plan and alternatives that have not yet been activated. ([nwcg.gov][4])

### Tactical points and areas

The symbol palette should include:

* anchor point.
* lookout.
* safety zone.
* escape route.
* trigger or decision point.
* hazard.
* division or sector boundary.
* branch boundary.
* staging area.
* command post.
* drop point.
* water source and restricted water source.
* helibase and helispot.
* dip site.
* mobile weather station.
* communications repeater.
* road closure.
* evacuation area.
* structure-protection area.
* critical value at risk.

These closely reflect the features used on NWCG operations, strategic and Incident Action Plan maps. ([nwcg.gov][4])

## 4. Plans must contain more than graphics

Every planned object should have a small operational record attached to it:

* objective.
* responsible division or unit.
* assigned crews and vehicles.
* planned start and completion time.
* priority.
* current status.
* required equipment.
* water requirement.
* prerequisites.
* hazards.
* escape route and safety zone.
* communications channel.
* notes and last update.

For example, selecting a proposed line might display:

> Construct 1.8 km machine line from Point A to the quarry road.
> Start only after Division North confirms evacuation of Sector 3.
> Two dozers, one engine and one lookout assigned.
> Trigger for withdrawal: fire reaches Decision Point DP-4.

That turns the map into an executable plan rather than a drawing.

## 5. Operational periods and scenarios

The service should never overwrite yesterday’s plan with today’s map.

It should support:

* defined operational periods.
* time-stamped snapshots.
* a fire-progression timeline.
* comparison between previous and current perimeters.
* “Plan A”, “Plan B” and worst-case scenarios.
* copying a plan into the next operational period.
* recording who approved each plan.
* reconstructing exactly what the map showed at a particular time.

A slider should allow the user to move through:

* observed fire progression.
* planned actions.
* completed actions.
* forecast scenarios.

## 6. Fire-behaviour decision support

A practical system should offer two levels.

### Simple field assessment

This should always work:

* manual spread arrows.
* estimated arrival-time lines.
* wind arrows.
* slope direction.
* distance and area measurements.
* rate-of-spread calculator.
* manually drawn forecast perimeters.
* configurable uncertainty buffers.

### Optional local simulation

A more advanced module can use:

* elevation, slope and aspect.
* fuel models.
* canopy characteristics.
* fuel moisture.
* wind.
* weather sequence.
* current perimeter or ignition points.

For example, FlamMap/FARSITE can calculate potential spread, flame length, fireline intensity, crown-fire activity and projected growth using terrain, fuel, moisture and weather inputs. Its official guidance also makes clear that trained interpretation is required and that different simulation modes have important limitations. ([US Forest Service Forschung][5])

The interface should therefore display simulation results as:

* **scenario**, not observation.
* input time and data sources.
* model name and version.
* assumptions.
* uncertainty or confidence.
* forecast validity period.
* warnings when weather or fuel data are stale.

Local wind modelling could be an optional component, especially in complex terrain. WindNinja was designed to produce terrain-adjusted wind fields under operational constraints including laptop-level processing, but any implementation needs validation for the relevant region and weather inputs. ([US Forest Service Forschung][6])

## 7. Safety should be built into the workflow

Before a plan can be marked “approved,” the application should check whether it contains:

* identified hazards.
* lookouts.
* communications.
* escape routes.
* safety zones.
* medical extraction routes.
* alternative access and egress.
* trigger points for withdrawal.
* weather and fire-behaviour update time.

This should be a warning checklist, not an automated declaration that the plan is safe.

The map should also make it easy to see when:

* a planned line is on an unsafe slope.
* an escape route crosses the forecast fire path.
* a unit has no recorded safety zone.
* a road is a dead end.
* a water source is unsuitable for helicopters.
* an assigned resource is outside its practical operating range.

## 8. Resource and logistics planning

A useful local service should show resources as more than moving dots.

For each resource:

* unit type.
* callsign.
* crew size.
* capabilities.
* water capacity.
* current assignment.
* availability.
* contact channel.
* last reported position and time.
* status: available, assigned, working, returning, unavailable.

Planning tools could calculate:

* distance to assignment.
* estimated road travel time.
* control-line length.
* hose-lay length.
* elevation profile.
* number and spacing of water relays.
* approximate resource requirement.
* access suitability for different vehicle classes.

Production-rate and arrival-time calculations should be configurable according to national doctrine and local conditions. They should expose their assumptions rather than producing unexplained “optimal” answers.

## 9. Disconnected collaboration

Although the system runs on one laptop, the laptop could act as a small local server:

```text
Command laptop
 │
Local Wi-Fi / cable network
 ├── Planning laptop
 ├── Operations tablet
 ├── Safety officer tablet
 └── Briefing display
```

No internet connection would be required. Browser-based clients could view or edit according to their roles.

It should also support:

* import from USB drives and SD cards.
* GPS receivers.
* GPX tracks and waypoints.
* drone imagery and perimeters.
* radio-room position reports.
* later synchronization with headquarters.
* conflict detection when two teams edited the same feature.

Synchronization should merge objects using unique IDs and revision history - not silently use whichever file was copied last.

## 10. Required outputs

The service should generate different products from the same incident database:

* command or strategic operations map.
* field operations map.
* Incident Action Plan map.
* briefing map.
* transportation and access map.
* air-operations map.
* evacuation-support map.
* progression map.
* public-information map.
* handover package for the next command team.

NWCG uses these as separate products because different audiences require different detail. Operational information should not automatically appear in a public map. ([nwcg.gov][7])

Every export should automatically contain:

* incident name and identifier.
* operational period.
* production and validity time.
* author or responsible unit.
* map scale.
* coordinate grid.
* projection.
* north arrow.
* legend.
* data-source and freshness statement.
* page or sector index.
* “operational,” “draft” or “public” classification.

Outputs should include ordinary PDF, georeferenced PDF, GeoPackage and common interchange formats such as GeoTIFF, KML/KMZ, GPX, CSV and GeoJSON.

## 11. Symbology

The symbol system should be implemented as configurable **profiles**, not hard-coded into the program:

* NWCG/ICS wildfire profile.
* German or other national fire-service profile.
* regional profile.
* simplified multinational profile.
* user-defined profile.

Underneath, the data object should remain semantic. For example:

```text
feature_type: safety_zone
status: approved
valid_from: 2026-08-06T20:00+02:00
operational_period: Night 3
```

The German profile and US profile may draw that object differently, but it remains the same type of feature. This makes cross-border and international deployments much easier.

## Minimum viable version

The first operational release should concentrate on seven capabilities:

1. Fully offline maps and incident storage.
2. Fire perimeter and observation management.
3. Standard tactical drawing and symbols.
4. Primary, contingency and alternative plans.
5. Operational-period history and audit trail.
6. Reliable map printing and export.
7. Crash recovery, backups and clear data-age warnings.

Fire-spread simulation, automatic resource optimization and live vehicle tracking should come later. A dependable map that preserves decisions is more valuable in the first version than an impressive predictive model that users cannot verify.

## Implemented follow-on groundwork: vehicle and drone feeds

The later central-planning groundwork is now implemented and tracked in
[`TELEMETRY_DRONE_PLAN.md`](TELEMETRY_DRONE_PLAN.md). It adds provider-neutral,
revocable vehicle-feed tokens. Immutable replay-safe positions. Freshness, gap and
implausibility-aware temporal tracks. Labelled interpolation. Drone mission/evidence
retention. Four-corner nadir/orthorectified georeferencing. Local GeoTIFF layers. And
deterministic bounded visual mosaics. These capabilities operate on the incident LAN
without mobile service. They deliberately do not claim survey accuracy, automatic
image interpretation, aviation authorization, or operational acceptance. The
hardware, calibration, privacy, load and field-exercise gates remain explicit.

## Implemented follow-on groundwork: temporal wind situation

The offline temporal wind layer is implemented and tracked in
[`WIND_MAP_PLAN.md`](WIND_MAP_PLAN.md). It uses structured incident wind observations
and weather provenance already attached to completed spread/ensemble runs. It performs
meteorological vector-component interpolation, optional observation-residual correction
over an AOI-wide model background, historical cutoffs, age weighting and visible support
and disagreement reporting. It deliberately does not claim terrain-channelled wind,
station calibration, live forecast acquisition or WindNinja-equivalent downscaling.

## Implemented follow-on groundwork: all-zoom offline assurance

[`TILE_CACHE_PLAN.md`](TILE_CACHE_PLAN.md) records the completed all-zoom contract.
Map-pack readiness is now calculated separately for every layer and integer zoom,
complete manifests pin ordinary cached files against pruning, and imported MBTiles or
local rasters participate directly. Public provider tiles are not automatically scraped.
authorised offline packages remain the required preparation path.

[1]: https://docs.ogc.org/is/12-128r19/12-128r19.html "OGC® GeoPackage Encoding Standard"
[2]: https://firms.modaps.eosdis.nasa.gov/active_fire/?utm_source=chatgpt.com "NASA | LANCE | FIRMS - Active Fire Data"
[3]: https://effis.jrc.ec.europa.eu/ "EFFIS - Welcome to EFFIS"
[4]: https://www.nwcg.gov/publications/pms936/map-product-standards/strategic-operations-map?utm_source=chatgpt.com "Strategic Operations Map | NWCG"
[5]: https://research.fs.usda.gov/firelab/products/dataandtools/flammap "FlamMap | US Forest Service Research and Development"
[6]: https://research.fs.usda.gov/firelab/products/dataandtools/windninja?utm_source=chatgpt.com "WindNinja | US Forest Service Research and Development"
[7]: https://www.nwcg.gov/publications/pms936/map-product-standards?utm_source=chatgpt.com "Map Product Standards | NWCG"

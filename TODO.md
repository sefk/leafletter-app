# Leafletter — Development TODO

## Open

### 1. City search: disambiguate cities with the same name
Currently OSM segments are fetched by matching on city name alone (e.g. `"Springfield"`),
which returns results from every city with that name worldwide. Need a better way to
identify the intended city — options include:
- Let the manager specify a bounding box or lat/lon center + radius alongside the city name
- Use a geocoder (Nominatim) to resolve the city name to an OSM area ID, then query by area ID
- Store cities as structured objects `{name, osm_area_id}` rather than plain strings

### 2. Campaign map: bounding box from cities; enforce zoom limits
After the OSM fetch completes, compute the bounding box of all imported streets and
store it on the Campaign. On the worker map view:
- Initialize the Leaflet map to fit that bounding box
- Set `maxBounds` and a minimum zoom level so workers can't pan/zoom outside
  the campaign area

### 3. Trip selection: block-level granularity, not whole streets
OSM ways can span many blocks. Workers need to select individual block segments
(way portion between two intersections), not the full way. Options:
- Split imported ways at intersection nodes during the OSM fetch
- Store sub-segments with start/end node IDs so each selectable unit is one block

### 4. Trip UX: improve block selection flow
The current tap-to-select model is basic. Consider:
- **Selection loop / lasso**: draw a shape on the map to select all enclosed segments at once
- **Undo**: deselect the last-added segment with a single tap
- **Visual feedback**: highlight selected segments in a distinct color with a count badge
- **Drag-to-select**: hold and drag along a street to select contiguous blocks

### 5. Bug: multiple trips overwrite each other
The second trip submitted for a campaign appears to replace the first rather than
accumulating. Likely cause: the coverage GeoJSON query or the frontend reload
is not merging trips correctly. Investigate:
- Whether `Trip.streets` M2M rows are being correctly persisted for each trip
- Whether the coverage endpoint is filtering by all trips vs. only the latest
- Whether the frontend is replacing the GeoJSON layer instead of merging it

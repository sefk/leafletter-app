# Leafletter — Development TODO

Priority to fix in brackets, with P0 highest.

## Open

### 1. [P0] Campaign map: bounding box from cities; enforce zoom limits — [#1](https://github.com/sefk/leafletter-app/issues/1)
After the OSM fetch completes, compute the bounding box of all imported streets and
store it on the Campaign. On the worker map view:
- Initialize the map to fit that bounding box
- Set `maxBounds` and a minimum zoom level so workers can't pan/zoom outside
  the campaign area

### 2. [P0] Trip selection: block-level granularity, not whole streets — [#2](https://github.com/sefk/leafletter-app/issues/2)
OSM ways can span many blocks. Workers need to select individual block segments
(portion between two intersections), not the full street. Options:
- Split imported ways at intersection nodes during the OSM fetch
- Store sub-segments with start/end node IDs so each selectable unit is one block

### 3. [P0] Trip UX: improve block selection flow — [#3](https://github.com/sefk/leafletter-app/issues/3)
The current tap-to-select model is basic. Consider:
- **Selection loop / lasso**: draw a shape on the map to select all enclosed segments at once
- **Undo**: deselect the last-added segment with a single tap
- **Visual feedback**: highlight selected segments in a distinct color with a count badge
- **Drag-to-select**: hold and drag along a street to select contiguous blocks

### 4. [P0] Bug: multiple trips overwrite each other — [#4](https://github.com/sefk/leafletter-app/issues/4)
The second trip submitted for a campaign appears to replace the first rather than
accumulating. Likely cause: the coverage GeoJSON query or the frontend reload
is not merging trips correctly. Investigate:
- Whether `Trip.streets` M2M rows are being correctly persisted for each trip
- Whether the coverage endpoint is filtering by all trips vs. only the latest
- Whether the frontend is replacing the GeoJSON layer instead of merging it

### 5. [P1] Campaign manager UI: replace Django Admin with a purpose-built interface — [#5](https://github.com/sefk/leafletter-app/issues/5)
Campaign managers currently use the full Django Admin, which exposes unrelated
models and has no guided workflow. Replace (or supplement) it with a custom
management interface that:
- Shows only the manager's campaigns in a clear list with status indicators
- Guides the manager through discrete steps: Create → Configure cities → Publish → Monitor coverage
- Surfaces map_status prominently with actionable next steps (e.g. "Re-fetch streets" on error)
- Hides irrelevant admin machinery (log entries, auth tables, etc.)
- Still requires authentication but does not require superuser/staff privileges

### 6. [P1] City search: disambiguate cities with the same name — [#6](https://github.com/sefk/leafletter-app/issues/6)
Currently OSM segments are fetched by matching on city name alone (e.g. `"Springfield"`),
which returns results from every city with that name worldwide. Need a better way to
identify the intended city — options include:
- Let the manager specify a bounding box or lat/lon center + radius alongside the city name
- Use a geocoder (Nominatim) to resolve the city name to an OSM area ID, then query by area ID
- Store cities as structured objects `{name, osm_area_id}` rather than plain strings

### 7. [P2] Landing page: show list of published campaigns instead of admin redirect — [#7](https://github.com/sefk/leafletter-app/issues/7)
The root URL (`/`) currently redirects to `/admin/`, which requires a login.
Instead it should render a simple public page listing all published campaigns,
each linking to its worker map view (`/c/<slug>/`). The page needs no authentication.
Consider showing campaign name, goal summary, and date range for each entry.

## P2 Backlog

### [P2] ACL-based per-manager campaign visibility
Add a `created_by` ForeignKey to Campaign linking to the User who created it.
Update the manager UI to filter campaigns by the logged-in user (all campaigns
for superusers). This lets organizations have multiple campaign managers without
each seeing the others' campaigns.

### [P2] Self-service manager account signup / registration page
Currently manager accounts are created manually by a superuser via Django Admin.
Add a public `/manage/signup/` page where new campaign managers can register
with email + password, subject to email verification or admin approval before
the account is activated.

## Done

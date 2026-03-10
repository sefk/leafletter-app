# Leafletter Developer Agent Memory

## Railway Production Access

- **`railway ssh`** opens an interactive shell inside the running container — use this for Django shell commands in production
- **`railway run`** and **`railway shell`** only run locally with env vars injected; they cannot reach `mysql.railway.internal`
- **`railway logs --service worker`** and **`railway logs --service web`** work for read-only log inspection
- `MYSQL_PUBLIC_URL` is available as a Railway variable and can be used to connect to the DB from local, but tasks queued this way won't reach the production Celery broker

## Celery / Task Architecture

- Celery broker is `sqla+mysql` (MySQL-as-broker via SQLAlchemy) — not Redis; known reliability issues
- Tasks do NOT use `acks_late=True` — if the worker is restarted mid-task, the task is lost and CityFetchJob stays in `generating` forever
- No stuck-job watchdog exists yet (tracked in issue #69)
- Issues #68, #69, #70 track the recommended fixes from the big-fresno-fair incident

## Async Polling Pattern (issue #67)
When a city street fetch is in progress, use AJAX polling instead of `location.reload()`:
- Endpoint: `GET /manage/<slug>/fetch-status/` returns JSON with `map_status`, `city_fetch_jobs[]`, `total_blocks`
- Template stores URL in `data-fetch-status-url` / initial state in `data-initial-map-status` on the card element
- JS polls every 5s; stops when `map_status` is terminal (`ready`/`warning`/`error`)
- Per-city rows have stable `id="sm-city-row-<city_index>"` for surgical DOM updates
- CSRF injected into dynamically-built action forms via hidden input in the `actionHtml()` helper

## MAP_STATUS flow (as of issue #78)

- `pending` → `generating` (city fetches running) → `rendering` (GeoJSON building async) → `ready` or `warning`
- `rendering` is a non-terminal state; polling continues automatically on the frontend
- `render_campaign_geojson(campaign_id, final_status)` is the Celery task that builds GeoJSON; final_status is 'ready' or 'warning'
- `_sync_campaign_map_status` computes bbox synchronously, sets status to 'rendering', dispatches `render_campaign_geojson.delay()`
- `manage_campaign_update_geo_limit` returns `{'status': 'rendering', 'bbox': ...}` — not 'ok'

## Known Issues / Technical Debt

- `fetch_city_osm_data` missing `acks_late=True` (issue #68)
- Issues #68, #69, #70, #78 now resolved

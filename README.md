# Leafletter

A web app for coordinating volunteer leafletting campaigns.

## Current State

### What's built

This is basically at MVP or Beta stage.

Workers visit a campaign URL, see a Leaflet map of OSM street segments, tap streets to select them, and submit a trip. Campaign managers use the `/manage/` UI to create campaigns, add cities (which triggers a background OSM street import), and publish them. There is also an iOS app that wraps the public-facing pages in a native shell.

### Tech stack

| Layer | Choice |
|---|---|
| Web framework | Django 5+ / GeoDjango |
| Database | PostgreSQL 14+ with PostGIS 3+ |
| DB driver | `psycopg2-binary` |
| Spatial backend | `django.contrib.gis.db.backends.postgis` |
| Background tasks | Celery (Postgres broker via Kombu SQLAlchemy transport) |
| Task results/debugging | `django-celery-results` (stores in Postgres, visible in admin) |
| File watching | `watchdog` / `watchmedo` (auto-restarts Celery worker) |
| Frontend map | Leaflet.js via CDN |
| OSM data | Overpass API via `requests` |

### Key decisions and constraints
- **PostgreSQL + PostGIS** — GeoDjango spatial backend via `django.contrib.gis.db.backends.postgis`
- **Coverage GeoJSON returns individual features** — covered streets are returned as individual GeoJSON features rather than a merged geometry (kept this way for simplicity; switching to `ST_Union` is an option now that PostGIS supports it)
- **Soft delete** on Campaign — sets `status='deleted'`, never removes rows
- **Cities are editable on published campaigns** — editing triggers a fresh OSM fetch and resets `map_status` to pending
- **Task trigger** — in the manage UI, adding cities queues an OSM fetch immediately; in Django Admin, publishing or changing cities on a published campaign triggers the fetch via `save_model` and `response_change`
- **383 tests** covering models, all views (public, manage, admin), the Overpass task, and API endpoints

---

## System Requirements

**macOS**
```bash
brew install gdal postgresql@18 postgis
brew services start postgresql@18
```

**Ubuntu / Debian**
```bash
sudo apt-get update
sudo apt-get install -y \
  gdal-bin libgdal-dev \
  postgresql postgresql-contrib postgis \
  python3-dev python3-venv
```

## Setup

### 1. Install dependencies

```bash
uv sync
```

`uv` manages the virtual environment automatically. Run subsequent commands with `uv run <cmd>` or activate the venv with `source .venv/bin/activate`.

### 2. Set up Postgres database

**macOS** — Postgres installed via Homebrew runs as your user with no password:
```bash
createuser -s leafletter || true
createdb -O leafletter leafletter
psql -d leafletter -c "ALTER USER leafletter WITH PASSWORD 'leafletter';"
```

**Linux** — use the `postgres` superuser via `sudo`:
```bash
sudo -u postgres psql -c "
  CREATE USER leafletter WITH PASSWORD 'leafletter' CREATEDB;
  CREATE DATABASE leafletter OWNER leafletter;
"
```

> `CREATEDB` on the role is required so the test runner can create and drop `test_leafletter`.

The first `manage.py migrate` automatically installs the PostGIS extension (via `CreateExtension('postgis')` in migration `0001_beta`), so you don't need to enable it by hand.

### 3. Run migrations

```bash
python manage.py migrate
```

### 4. Create a superuser (for Django Admin)

```bash
python manage.py createsuperuser
```

### 5. Collect static files (production)

```bash
python manage.py collectstatic
```

## Running locally

**macOS** — Postgres is already running from the brew services step above; just start honcho:
```bash
honcho start
```

**Linux** — start Postgres as a system service, then use honcho:
```bash
sudo systemctl start postgresql
honcho start
```

`honcho start` reads the `Procfile` and starts the Celery worker (with auto-restart on `.py` changes) and the Django dev server in a single terminal with colour-coded output.

## Usage

- `/` — Public landing page listing active campaigns
- `/manage/` — Campaign manager UI
  - Create a campaign, add cities via the search widget, then publish
  - Adding cities triggers the background OSM street import; wait for `map_status` to become **Ready**
- `/admin/` — Django Admin (superuser access; useful for debugging)
- `/c/<slug>/` — Worker map view; tap streets, log trips

## Debugging Celery tasks

Task results are stored in Postgres via `django-celery-results` and visible in Django Admin at:

```
/admin/django_celery_results/taskresult/
```

Each row shows the task name, status (`SUCCESS` / `FAILURE` / `PENDING`), arguments, return value, and full traceback on failure.

**Useful patterns:**

Run a task synchronously in a shell (bypasses Celery entirely):

```bash
uv run python manage.py shell
```

```python
from campaigns.tasks import fetch_osm_segments
fetch_osm_segments(campaign_id=1)   # runs inline, prints log output
```

Trigger a task and inspect its result:

```python
result = fetch_osm_segments.delay(1)
print(result.status)   # PENDING / SUCCESS / FAILURE
print(result.result)   # return value or exception
```

Run the Celery worker with verbose logging to see tasks execute in real time:

```bash
watchmedo auto-restart --directory=. --pattern='*.py' --recursive -- \
  celery -A leafletter worker -l debug
```

## Deployment / Branch Strategy

This app runs on [Railway](https://railway.app/) with two environments:

| Environment | Branch | Purpose |
|---|---|---|
| `staging` | `main` | Auto-deploys on every push to `main`; used for testing |
| `production` | `prod` | Deploys only when `prod` is updated manually; stable releases |

**To release to production:** fast-forward `prod` to `main` (or whichever commit you want to ship) and push it:

```bash
git checkout prod
git merge --ff-only main
git push origin prod
git checkout main
```

Railway will pick up the push to `prod` and deploy to the production environment automatically.

---

## URLs

| URL | Description |
|-----|-------------|
| `/` | Public campaign landing page |
| `/about/` | About, privacy policy, and legal |
| `/manage/` | Campaign manager UI |
| `/manage/new/` | Create a new campaign |
| `/manage/usage-report/` | CSV export of usage events (superuser only); supports `from=YYYY-MM-DD`, `to=YYYY-MM-DD`, and `campaign=<slug>` query params |
| `/manage/<slug>/` | Campaign detail / edit page |
| `/admin/` | Django Admin |
| `/c/<slug>/` | Worker campaign map |
| `/c/<slug>/streets.geojson` | All street segments (GeoJSON) |
| `/c/<slug>/coverage.geojson` | Covered street segments (GeoJSON) |
| `/c/<slug>/trip/` | POST: log a trip |

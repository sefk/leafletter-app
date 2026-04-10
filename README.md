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
| Database | MySQL 8.0+ with spatial support |
| DB driver | `mysqlclient` |
| Spatial backend | `django.contrib.gis.db.backends.mysql` |
| Background tasks | Celery (MySQL broker) |
| Task results/debugging | `django-celery-results` (stores in MySQL, visible in admin) |
| File watching | `watchdog` / `watchmedo` (auto-restarts Celery worker) |
| Frontend map | Leaflet.js via CDN |
| OSM data | Overpass API via `requests` |

### Key decisions and constraints
- **MySQL** (not PostgreSQL) — GeoDjango spatial backend via `django.contrib.gis.db.backends.mysql`
- **Coverage GeoJSON returns individual features** — MySQL doesn't support spatial union aggregates, so covered streets are returned as individual GeoJSON features rather than a merged geometry
- **Soft delete** on Campaign — sets `status='deleted'`, never removes rows
- **Cities are editable on published campaigns** — editing triggers a fresh OSM fetch and resets `map_status` to pending
- **Task trigger** — in the manage UI, adding cities queues an OSM fetch immediately; in Django Admin, publishing or changing cities on a published campaign triggers the fetch via `save_model` and `response_change`
- **383 tests** covering models, all views (public, manage, admin), the Overpass task, and API endpoints

---

## System Requirements

**macOS**
```bash
brew install gdal mysql
```

**Ubuntu / Debian**
```bash
sudo apt-get update
sudo apt-get install -y \
  gdal-bin libgdal-dev \
  mysql-server libmysqlclient-dev \
  python3-dev python3-venv
```

> On Ubuntu, MySQL 8.0+ is available from the default repos on 22.04+.
> If your distro ships an older version, add the [MySQL APT repository](https://dev.mysql.com/downloads/repo/apt/) first.

## Setup

### 1. Install dependencies

```bash
uv sync
```

`uv` manages the virtual environment automatically. Run subsequent commands with `uv run <cmd>` or activate the venv with `source .venv/bin/activate`.

### 2. Set up MySQL database

**macOS** — MySQL runs as your user, so no password is needed for root:
```bash
mysql -u root -e "
  CREATE DATABASE leafletter CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE USER 'leafletter'@'localhost' IDENTIFIED BY 'leafletter';
  GRANT ALL PRIVILEGES ON leafletter.* TO 'leafletter'@'localhost';
  GRANT ALL PRIVILEGES ON \`test_leafletter\`.* TO 'leafletter'@'localhost';
  FLUSH PRIVILEGES;
"
```

**Linux** — the root MySQL account uses `sudo` by default:
```bash
sudo mysql -e "
  CREATE DATABASE leafletter CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE USER 'leafletter'@'localhost' IDENTIFIED BY 'leafletter';
  GRANT ALL PRIVILEGES ON leafletter.* TO 'leafletter'@'localhost';
  GRANT ALL PRIVILEGES ON \`test_leafletter\`.* TO 'leafletter'@'localhost';
  FLUSH PRIVILEGES;
"
```

> The `test_leafletter` grant is required so the test runner can create and drop the test database.

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

**macOS** — start MySQL via brew services, then use honcho to launch everything else (Celery worker, Django):
```bash
brew services start mysql
honcho start
```

**Linux** — start MySQL as a system service, then use honcho:
```bash
sudo systemctl start mysql
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

Task results are stored in MySQL via `django-celery-results` and visible in Django Admin at:

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
| `/manage/<slug>/` | Campaign detail / edit page |
| `/admin/` | Django Admin |
| `/c/<slug>/` | Worker campaign map |
| `/c/<slug>/streets.geojson` | All street segments (GeoJSON) |
| `/c/<slug>/coverage.geojson` | Covered street segments (GeoJSON) |
| `/c/<slug>/trip/` | POST: log a trip |

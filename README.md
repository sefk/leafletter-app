# Leafletter

A web app for coordinating volunteer leafletting campaigns.

## Current State

### What's built
A working MVP. Workers visit a campaign URL, see a Leaflet map of OSM street segments, tap streets to select them, and submit a trip. Campaign managers use Django Admin to create campaigns, publish them (triggering a background OSM street import), and monitor coverage.

### Tech stack

| Layer | Choice |
|---|---|
| Web framework | Django 5+ / GeoDjango |
| Database | MySQL 8.0+ with spatial support |
| DB driver | `mysqlclient` |
| Spatial backend | `django.contrib.gis.db.backends.mysql` |
| Background tasks | Celery + Redis |
| Task results/debugging | `django-celery-results` (stores in MySQL, visible in admin) |
| File watching | `watchdog` / `watchmedo` (auto-restarts Celery worker) |
| Frontend map | Leaflet.js via CDN |
| OSM data | Overpass API via `requests` |

### Key decisions and constraints
- **MySQL** (not PostgreSQL) — GeoDjango spatial backend via `django.contrib.gis.db.backends.mysql`
- **Coverage GeoJSON returns individual features** — MySQL doesn't support spatial union aggregates, so covered streets are returned as individual GeoJSON features rather than a merged geometry
- **Soft delete** on Campaign — sets `status='deleted'`, never removes rows
- **Cities are editable on published campaigns** — editing triggers a fresh OSM fetch and resets `map_status` to pending
- **Task trigger in `save_model` and `response_change`** — publishing via the status dropdown, the Publish button, or the bulk action all queue an OSM fetch
- **79 tests** covering models, all views, the Overpass task, and admin behaviour

### Known gaps (see TODO.md)
1. City search is name-only — ambiguous for common city names
2. Worker map doesn't enforce a bounding box or minimum zoom
3. Trip selection is whole-street, not block-level
4. Trip UX is basic (no lasso, no undo)
5. Bug: multiple trips may overwrite each other
6. Landing page redirects to admin login instead of a public campaign list
7. No purpose-built campaign manager UI — managers use raw Django Admin

---

## Stack

- Django 5+ with GeoDjango (spatial fields)
- MySQL 8.0+ with spatial support
- Celery + Redis (OSM background fetch)
- Leaflet.js (frontend map)

## System Requirements

**macOS**
```bash
brew install gdal mysql redis
```

**Ubuntu / Debian**
```bash
sudo apt-get update
sudo apt-get install -y \
  gdal-bin libgdal-dev \
  mysql-server libmysqlclient-dev \
  redis-server \
  python3-dev python3-venv
```

> On Ubuntu, MySQL 8.0+ is available from the default repos on 22.04+.
> If your distro ships an older version, add the [MySQL APT repository](https://dev.mysql.com/downloads/repo/apt/) first.

## Setup

### 1. Create virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

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

**macOS** — start Redis manually, MySQL via brew services:
```bash
brew services start mysql   # if not already running
redis-server                # Terminal 1
```

**Linux** — Redis and MySQL run as system services:
```bash
sudo systemctl start mysql   # if not already running
sudo systemctl start redis
```

Then start the app (both platforms):

```bash
# Terminal 1 (Linux only — macOS uses the commands above instead)
redis-server

# Terminal 2 — Celery worker (auto-restarts on .py file changes)
source .venv/bin/activate
watchmedo auto-restart --directory=. --pattern='*.py' --recursive -- \
  celery -A leafletter worker -l info

# Terminal 3 — Django dev server
source .venv/bin/activate
python manage.py runserver
```

## Usage

- `/admin/` — Django Admin for Campaign Managers
  - Create a Campaign, fill in `cities` as a JSON list: `["Palo Alto", "Menlo Park"]`
  - Use the **Publish** action to publish and trigger OSM street import
  - Wait for `map_status` to become **Ready**
- `/c/<slug>/` — Worker map view; tap streets, log trips

## Debugging Celery tasks

Task results are stored in MySQL via `django-celery-results` and visible in Django Admin at:

```
/admin/django_celery_results/taskresult/
```

Each row shows the task name, status (`SUCCESS` / `FAILURE` / `PENDING`), arguments, return value, and full traceback on failure.

**Useful patterns:**

Run a task synchronously in a shell (bypasses Celery/Redis entirely):

```bash
source .venv/bin/activate
python manage.py shell
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

## URLs

| URL | Description |
|-----|-------------|
| `/` | Redirects to `/admin/` |
| `/admin/` | Django Admin |
| `/c/<slug>/` | Worker campaign map |
| `/c/<slug>/streets.geojson` | All street segments (GeoJSON) |
| `/c/<slug>/coverage.geojson` | Covered street segments (GeoJSON) |
| `/c/<slug>/trip/` | POST: log a trip |

# Leafletter

A web app for coordinating volunteer leafletting campaigns.

## Stack

- Django 5+ with GeoDjango (spatial fields)
- MySQL 8.0+ with spatial support
- Celery + Redis (OSM background fetch)
- Leaflet.js (frontend map)

## System Requirements

```bash
brew install gdal mysql redis
```

## Setup

### 1. Create virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Set up MySQL database

```bash
mysql -u root -e "
  CREATE DATABASE leafletter CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE USER 'leafletter'@'localhost' IDENTIFIED BY 'leafletter';
  GRANT ALL PRIVILEGES ON leafletter.* TO 'leafletter'@'localhost';
  FLUSH PRIVILEGES;
"
```

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

Start all services:

```bash
# Terminal 1 — Redis
redis-server

# Terminal 2 — Celery worker
source .venv/bin/activate
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

## URLs

| URL | Description |
|-----|-------------|
| `/` | Redirects to `/admin/` |
| `/admin/` | Django Admin |
| `/c/<slug>/` | Worker campaign map |
| `/c/<slug>/streets.geojson` | All street segments (GeoJSON) |
| `/c/<slug>/coverage.geojson` | Covered street segments (GeoJSON) |
| `/c/<slug>/trip/` | POST: log a trip |

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

## Known Issues / Technical Debt

- `fetch_city_osm_data` missing `acks_late=True` (issue #68)
- No stuck-job watchdog for CityFetchJob (issue #69)
- Overpass timeout too short for large cities like Fresno (issue #70)

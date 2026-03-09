import json
import logging
from collections import Counter

import requests
from celery import shared_task
from django.contrib.gis.geos import LineString

from .models import Campaign, CityFetchJob, Street

logger = logging.getLogger(__name__)

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
NOMINATIM_HEADERS = {'User-Agent': 'Leafletter/1.0 (github.com/sefk/leafletter-app)'}
CITY_TYPES = {'city', 'town', 'village', 'municipality', 'borough'}

# Overpass server-side timeout (embedded in query) and HTTP client timeout.
# Large cities like Fresno can exceed 60s of server processing time, causing
# silent empty-result failures. See GitHub issue #70.
OVERPASS_SERVER_TIMEOUT = 180  # seconds, embedded as [timeout:N] in query
OVERPASS_HTTP_TIMEOUT = 240    # seconds, passed to requests.post(timeout=)

# Highway types to include (exclude footways, paths, etc.)
HIGHWAY_INCLUDE = {
    'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
    'unclassified', 'residential', 'motorway_link', 'trunk_link',
    'primary_link', 'secondary_link', 'tertiary_link', 'living_street',
    'service',
}


def lookup_city(city_name: str) -> None:
    """
    Pre-check that city_name resolves to exactly one city-type place in Nominatim.
    Raises ValueError if the city is not found or is ambiguous.
    """
    resp = requests.get(
        NOMINATIM_URL,
        params={'q': city_name, 'format': 'json', 'limit': 10},
        headers=NOMINATIM_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = [r for r in resp.json() if r.get('class') == 'place' and r.get('type') in CITY_TYPES]
    n = len(results)
    if n == 0:
        raise ValueError(f'City "{city_name}" not found in OpenStreetMap')
    if n > 1:
        raise ValueError(f'{n} places named "{city_name}" found; use a more specific name')


def query_overpass(city) -> list[dict]:
    """
    Query Overpass API for driveable highway ways within a named area.
    city may be a string (city name) or a dict with osm_id/osm_type keys.
    Returns a list of dicts: {osm_id, name, coords, node_ids}.
    coords is a list of (lon, lat) tuples; node_ids is the parallel list of OSM node IDs.
    """
    if isinstance(city, dict) and city.get('osm_type') == 'relation' and 'osm_id' in city:
        area_id = 3600000000 + city['osm_id']
        city_label = city.get('name', str(city['osm_id']))
        query = f"""
[out:json][timeout:{OVERPASS_SERVER_TIMEOUT}];
area({area_id})->.searchArea;
way["highway"](area.searchArea);
out geom;
"""
    else:
        city_name = city if isinstance(city, str) else city.get('name', str(city))
        city_label = city_name
        query = f"""
[out:json][timeout:{OVERPASS_SERVER_TIMEOUT}];
area[name="{city_name}"]->.searchArea;
way["highway"](area.searchArea);
out geom;
"""
    logger.info(
        "Overpass query starting for city %s (server_timeout=%ds, http_timeout=%ds)",
        city_label, OVERPASS_SERVER_TIMEOUT, OVERPASS_HTTP_TIMEOUT,
    )
    try:
        resp = requests.post(OVERPASS_URL, data={'data': query}, timeout=OVERPASS_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Overpass query failed for city %s: %s", city_label, exc)
        raise

    response_bytes = len(resp.content)
    logger.info(
        "Overpass query complete for city %s: %d elements, response size %.1f KB",
        city_label, len(data.get('elements', [])), response_bytes / 1024,
    )

    ways = []
    for element in data.get('elements', []):
        highway_type = element.get('tags', {}).get('highway', '')
        if highway_type not in HIGHWAY_INCLUDE:
            continue
        geometry = element.get('geometry', [])
        if len(geometry) < 2:
            continue
        coords = [(pt['lon'], pt['lat']) for pt in geometry]
        ways.append({
            'osm_id': element['id'],
            'name': element.get('tags', {}).get('name', ''),
            'coords': coords,
            'node_ids': element.get('nodes', []),
        })
    return ways


def find_intersection_nodes(ways: list[dict]) -> set:
    """
    Return the set of OSM node IDs that appear in two or more ways.
    These are the points where ways intersect and where we split blocks.
    """
    node_counts = Counter()
    for way in ways:
        # Use a set per way so a loop road's repeated start/end node
        # doesn't count as an intersection with itself.
        node_counts.update(set(way.get('node_ids', [])))
    return {node_id for node_id, count in node_counts.items() if count >= 2}


def split_way_at_intersections(way: dict, intersection_nodes: set) -> list[dict]:
    """
    Split a way into block segments at every interior intersection node.
    Returns a list of segment dicts with keys:
      coords, start_node_id, end_node_id, block_index
    Each segment has at least 2 coordinate points.
    """
    node_ids = way.get('node_ids', [])
    coords = way['coords']

    if not node_ids or len(node_ids) != len(coords):
        return [{'coords': coords, 'start_node_id': None, 'end_node_id': None, 'block_index': 0}]

    segments = []
    current_coords = [coords[0]]
    current_start_node = node_ids[0]
    block_index = 0

    for i in range(1, len(node_ids)):
        current_coords.append(coords[i])
        node_id = node_ids[i]
        is_last = (i == len(node_ids) - 1)
        # Split at interior intersection nodes and always at the final point
        if node_id in intersection_nodes or is_last:
            segments.append({
                'coords': current_coords,
                'start_node_id': current_start_node,
                'end_node_id': node_id,
                'block_index': block_index,
            })
            block_index += 1
            current_coords = [coords[i]]
            current_start_node = node_id

    return segments or [{'coords': coords, 'start_node_id': node_ids[0], 'end_node_id': node_ids[-1], 'block_index': 0}]


# ── Per-city helpers ───────────────────────────────────────────────────────────

def build_streets_geojson(campaign_id: int, bbox=None, geo_limit=None) -> str:
    """
    Serialize streets for a campaign to a GeoJSON FeatureCollection string.
    If geo_limit (a GEOS Polygon) is provided, only streets intersecting it are included.
    Otherwise, if bbox is [[sw_lat, sw_lon], [ne_lat, ne_lon]], filter by that rectangle.
    """
    from django.contrib.gis.geos import Polygon as GeosPoly
    qs = Street.objects.filter(campaign_id=campaign_id).only('pk', 'osm_id', 'name', 'geometry')
    if geo_limit is not None:
        qs = qs.filter(geometry__intersects=geo_limit)
    elif bbox:
        sw, ne = bbox
        bbox_poly = GeosPoly.from_bbox((sw[1], sw[0], ne[1], ne[0]))
        bbox_poly.srid = 4326
        qs = qs.filter(geometry__intersects=bbox_poly)
    features = []
    for street in qs:
        features.append({
            'type': 'Feature',
            'id': street.pk,
            'geometry': json.loads(street.geometry.geojson),
            'properties': {
                'osm_id': street.osm_id,
                'name': street.name,
            },
        })
    return json.dumps({'type': 'FeatureCollection', 'features': features})


def _sync_campaign_map_status(campaign_id: int) -> None:
    """
    Recompute Campaign.map_status from all CityFetchJob records and save.
    Also recalculates bbox and pre-renders streets_geojson when all cities are ready.
    """
    jobs = list(CityFetchJob.objects.filter(campaign_id=campaign_id))
    if not jobs:
        return

    statuses = [j.status for j in jobs]
    if any(s in ('generating', 'pending') for s in statuses):
        new_status = 'generating'
    elif all(s == 'error' for s in statuses):
        new_status = 'error'
    elif any(s == 'error' for s in statuses):
        new_status = 'warning'
    else:
        new_status = 'ready'

    updates = {'map_status': new_status}

    if new_status == 'error':
        error_jobs = [j for j in jobs if j.status == 'error']
        if len(error_jobs) == 1:
            updates['map_error'] = error_jobs[0].error
        else:
            updates['map_error'] = '; '.join(
                f"{j.city_name}: {j.error}" for j in error_jobs
            )
    elif new_status == 'warning':
        error_jobs = [j for j in jobs if j.status == 'error']
        updates['map_error'] = '; '.join(
            f"{j.city_name}: {j.error}" for j in error_jobs
        )
    if new_status == 'ready':
        updates['map_error'] = ''
    if new_status in ('ready', 'warning'):
        campaign = Campaign.objects.only('geo_limit').get(pk=campaign_id)
        if campaign.geo_limit:
            # Preserve manager-drawn boundary; derive bbox from its extent
            xmin, ymin, xmax, ymax = campaign.geo_limit.extent
            updates['bbox'] = [[ymin, xmin], [ymax, xmax]]
            updates['streets_geojson'] = build_streets_geojson(campaign_id, geo_limit=campaign.geo_limit)
        else:
            min_lon = min_lat = float('inf')
            max_lon = max_lat = float('-inf')
            for street in Street.objects.filter(campaign_id=campaign_id).only('geometry'):
                xmin, ymin, xmax, ymax = street.geometry.extent
                min_lon = min(min_lon, xmin)
                min_lat = min(min_lat, ymin)
                max_lon = max(max_lon, xmax)
                max_lat = max(max_lat, ymax)
            if min_lon != float('inf'):
                updates['bbox'] = [[min_lat, min_lon], [max_lat, max_lon]]
                updates['streets_geojson'] = build_streets_geojson(campaign_id)

    Campaign.objects.filter(pk=campaign_id).update(**updates)


def queue_city_fetches(campaign_id: int, city_indices: list[int] | None = None) -> None:
    """
    Create/reset CityFetchJob records and dispatch fetch_city_osm_data for each city.
    If city_indices is None, fetches all cities.
    Sets campaign.map_status = 'generating'.
    """
    campaign = Campaign.objects.get(pk=campaign_id)
    cities = campaign.cities

    if city_indices is None:
        city_indices = list(range(len(cities)))

    Campaign.objects.filter(pk=campaign_id).update(map_status='generating', map_error='', streets_geojson='')

    for idx in city_indices:
        city = cities[idx]
        city_name = city if isinstance(city, str) else city.get('name', str(city))
        CityFetchJob.objects.update_or_create(
            campaign=campaign,
            city_index=idx,
            defaults={'status': 'pending', 'error': '', 'city_name': city_name},
        )
        result = fetch_city_osm_data.delay(campaign_id, idx)
        CityFetchJob.objects.filter(campaign=campaign, city_index=idx).update(
            celery_task_id=result.id,
        )


# ── Main per-city Celery task ──────────────────────────────────────────────────

@shared_task(bind=True, max_retries=5, acks_late=True, reject_on_worker_lost=True)
def fetch_city_osm_data(self, campaign_id: int, city_index: int) -> None:
    try:
        campaign = Campaign.objects.get(pk=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("fetch_city_osm_data: campaign %s not found", campaign_id)
        return

    cities = campaign.cities
    if city_index >= len(cities):
        logger.error("fetch_city_osm_data: city_index %d out of range for campaign %s", city_index, campaign_id)
        return

    city = cities[city_index]
    city_label = city if isinstance(city, str) else city.get('name', str(city))

    CityFetchJob.objects.update_or_create(
        campaign=campaign,
        city_index=city_index,
        defaults={'status': 'generating', 'celery_task_id': self.request.id or '', 'city_name': city_label},
    )
    logger.info("fetch_city_osm_data: starting city %s (index %d) for campaign %s", city_label, city_index, campaign_id)

    try:
        if isinstance(city, str):
            lookup_city(city)

        ways = query_overpass(city)
        intersection_nodes = find_intersection_nodes(ways)
        block_count = 0
        for way in ways:
            for block in split_way_at_intersections(way, intersection_nodes):
                if len(block['coords']) < 2:
                    continue
                Street.objects.update_or_create(
                    campaign=campaign,
                    osm_id=way['osm_id'],
                    block_index=block['block_index'],
                    defaults={
                        'name': way['name'],
                        'geometry': LineString(block['coords']),
                        'city_index': city_index,
                        'start_node_id': block['start_node_id'],
                        'end_node_id': block['end_node_id'],
                    },
                )
                block_count += 1

        logger.info("fetch_city_osm_data: imported %d blocks for %s", block_count, city_label)
        if block_count == 0:
            raise ValueError(f'City "{city_label}" was found but no streets were imported')

        CityFetchJob.objects.update_or_create(
            campaign=campaign,
            city_index=city_index,
            defaults={'status': 'ready', 'error': '', 'city_name': city_label},
        )

    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError) as exc:
        is_server_error = (
            not isinstance(exc, requests.exceptions.HTTPError)
            or (exc.response is not None and (exc.response.status_code >= 500 or exc.response.status_code == 429))
        )
        if is_server_error and self.request.retries < self.max_retries:
            countdown = min(60 * 2 ** self.request.retries, 600)
            logger.warning(
                "fetch_city_osm_data transient error for %s city %d, retrying in %ds (%d/%d): %s",
                campaign_id, city_index, countdown, self.request.retries + 1, self.max_retries, exc,
            )
            raise self.retry(exc=exc, countdown=countdown)
        logger.error("fetch_city_osm_data failed for campaign %s city %d: %s", campaign_id, city_index, exc)
        CityFetchJob.objects.update_or_create(
            campaign=campaign,
            city_index=city_index,
            defaults={'status': 'error', 'error': str(exc), 'city_name': city_label},
        )

    except Exception as exc:
        logger.error("fetch_city_osm_data failed for campaign %s city %d: %s", campaign_id, city_index, exc)
        CityFetchJob.objects.update_or_create(
            campaign=campaign,
            city_index=city_index,
            defaults={'status': 'error', 'error': str(exc), 'city_name': city_label},
        )

    finally:
        _sync_campaign_map_status(campaign_id)


# ── Backward-compat wrapper ────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=0)
def fetch_osm_segments(self, campaign_id: int) -> None:
    """
    Backward-compat wrapper: runs all cities synchronously (used by legacy
    callers and tests). New code should call queue_city_fetches() instead.
    """
    try:
        campaign = Campaign.objects.get(pk=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("fetch_osm_segments: campaign %s not found", campaign_id)
        return
    Campaign.objects.filter(pk=campaign_id).update(
        map_status='generating', map_error='', bbox=None, streets_geojson='',
    )
    for idx in range(len(campaign.cities)):
        city = campaign.cities[idx]
        city_name = city if isinstance(city, str) else city.get('name', str(city))
        CityFetchJob.objects.update_or_create(
            campaign=campaign,
            city_index=idx,
            defaults={'status': 'pending', 'error': '', 'city_name': city_name},
        )
        fetch_city_osm_data(campaign_id, idx)

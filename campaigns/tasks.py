import gzip
import io
import json
import logging
import os
import subprocess
from collections import Counter
from datetime import timedelta, datetime, timezone as dt_timezone

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import LineString, Point
from django.core.mail import send_mail
from django.db import connection
from django.utils import timezone

from .models import AddressPoint, Campaign, CampaignStreet, CityFetchJob, Street

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

# Maximum total blocks (Street records) allowed across all cities in one campaign.
# Cook County (Chicago) has ~400k blocks, so 1M is a safe upper bound for any
# realistic leafletting campaign.  See GitHub issue #71.
MAX_CAMPAIGN_BLOCKS = 1_000_000

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


def query_overpass_addresses(city, bbox=None) -> list[tuple[float, float]]:
    """
    Query Overpass for addr:housenumber nodes and ways within a city area.
    Returns a list of (lon, lat) tuples (way centroids for ways, coords for nodes).

    If bbox is provided as (west, south, east, north), use it as the spatial filter
    instead of the city area. This avoids timeouts for large city/county areas.
    """
    if bbox is not None:
        west, south, east, north = bbox
        area_clause = ''
        filter_clause = f'({south},{west},{north},{east})'
        city_label = city if isinstance(city, str) else city.get('name', str(city))
        city_label = f'{city_label} (bbox)'
    elif isinstance(city, dict) and city.get('osm_type') == 'relation' and 'osm_id' in city:
        area_id = 3600000000 + city['osm_id']
        city_label = city.get('name', str(city['osm_id']))
        area_clause = f'area({area_id})->.searchArea;'
        filter_clause = '(area.searchArea)'
    else:
        city_name = city if isinstance(city, str) else city.get('name', str(city))
        city_label = city_name
        area_clause = f'area[name="{city_name}"]->.searchArea;'
        filter_clause = '(area.searchArea)'

    query = f"""
[out:json][timeout:{OVERPASS_SERVER_TIMEOUT}];
{area_clause}
(
  node["addr:housenumber"]{filter_clause};
  way["addr:housenumber"]{filter_clause};
);
out center qt;
"""
    logger.info("query_overpass_addresses: fetching address points for %s", city_label)
    try:
        resp = requests.post(OVERPASS_URL, data={'data': query}, timeout=OVERPASS_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("query_overpass_addresses failed for %s: %s", city_label, exc)
        raise

    points = []
    for element in data.get('elements', []):
        if element.get('type') == 'node':
            points.append((element['lon'], element['lat']))
        elif element.get('type') == 'way':
            center = element.get('center')
            if center:
                points.append((center['lon'], center['lat']))
    logger.info("query_overpass_addresses: %d address points for %s", len(points), city_label)
    return points


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
    qs = Street.objects.filter(campaign_streets__campaign_id=campaign_id).only('pk', 'osm_id', 'name', 'geometry')
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


def _write_streets_geojson_chunked(campaign_id: int, geo_limit=None, chunk_size: int = 200) -> None:
    """
    Write the GeoJSON FeatureCollection for a campaign's streets directly into
    the DB using chunked CONCAT updates, so no single MySQL packet ever carries
    the full blob.  Avoids the ``max_allowed_packet`` error that occurs when
    the entire GeoJSON is passed in one UPDATE for large cities like Houston.

    Each raw-SQL CONCAT call appends only one batch of ``chunk_size`` features,
    keeping individual packet sizes well below the server limit.

    ``build_streets_geojson`` is intentionally left unchanged — it is still used
    by the paginated API endpoint in views.py where the result is returned to the
    client (no DB write needed).
    """
    from django.contrib.gis.geos import Polygon as GeosPoly

    qs = Street.objects.filter(campaign_streets__campaign_id=campaign_id).only('pk', 'osm_id', 'name', 'geometry')
    if geo_limit is not None:
        qs = qs.filter(geometry__intersects=geo_limit)

    # Initialise the field with the opening of the FeatureCollection.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE campaigns_campaign SET streets_geojson = %s WHERE id = %s",
            ['{"type":"FeatureCollection","features":[', campaign_id],
        )

    first_feature = True
    batch = []

    def _flush(batch, first_feature):
        """Append one batch of serialised features via CONCAT."""
        fragments = []
        for street in batch:
            feature = {
                'type': 'Feature',
                'id': street.pk,
                'geometry': json.loads(street.geometry.geojson),
                'properties': {
                    'osm_id': street.osm_id,
                    'name': street.name,
                },
            }
            fragments.append(json.dumps(feature, separators=(',', ':')))

        if not fragments:
            return first_feature

        chunk_str = ','.join(fragments)
        if not first_feature:
            chunk_str = ',' + chunk_str

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE campaigns_campaign SET streets_geojson = CONCAT(streets_geojson, %s) WHERE id = %s",
                [chunk_str, campaign_id],
            )
        return False  # first_feature is now False after the first flush

    for street in qs.iterator(chunk_size=chunk_size):
        batch.append(street)
        if len(batch) >= chunk_size:
            first_feature = _flush(batch, first_feature)
            batch = []

    if batch:
        first_feature = _flush(batch, first_feature)  # noqa: F841

    # Close the FeatureCollection.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE campaigns_campaign SET streets_geojson = CONCAT(streets_geojson, %s) WHERE id = %s",
            [']}', campaign_id],
        )


def _sync_campaign_map_status(campaign_id: int) -> None:
    """
    Recompute Campaign.map_status from all CityFetchJob records and save.
    When all cities are ready (or some have errors), compute bbox synchronously
    and set map_status to 'ready' or 'warning'.  streets_geojson is left empty
    until the manager draws a geo_limit boundary and saves it, which triggers
    render_campaign_geojson via manage_campaign_update_geo_limit.
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
        # Compute bbox synchronously (cheap) so the geo_limit editor has a
        # reference frame.  Do NOT dispatch render_campaign_geojson here —
        # rendering is deferred until the manager draws a geo_limit boundary
        # and saves it via manage_campaign_update_geo_limit.
        campaign = Campaign.objects.only('geo_limit').get(pk=campaign_id)
        if campaign.geo_limit:
            # Preserve manager-drawn boundary; derive bbox from its extent
            xmin, ymin, xmax, ymax = campaign.geo_limit.extent
            updates['bbox'] = [[ymin, xmin], [ymax, xmax]]
        else:
            min_lon = min_lat = float('inf')
            max_lon = max_lat = float('-inf')
            for street in Street.objects.filter(campaign_streets__campaign_id=campaign_id).only('geometry'):
                xmin, ymin, xmax, ymax = street.geometry.extent
                min_lon = min(min_lon, xmin)
                min_lat = min(min_lat, ymin)
                max_lon = max(max_lon, xmax)
                max_lat = max(max_lat, ymax)
            if min_lon != float('inf'):
                updates['bbox'] = [[min_lat, min_lon], [max_lat, max_lon]]
        # Leave streets_geojson empty — rendering is triggered by geo_limit save.
        updates['streets_geojson'] = ''

    Campaign.objects.filter(pk=campaign_id).update(**updates)


@shared_task(bind=True)
def render_campaign_geojson(self, campaign_id: int, final_status: str = 'ready') -> None:
    """
    Build and store the pre-rendered GeoJSON blob for a campaign.
    Called asynchronously after city fetches complete or after the geo_limit changes.
    Sets map_status to final_status ('ready' or 'warning') on success,
    or 'error' on failure.
    """
    try:
        campaign = Campaign.objects.only('geo_limit', 'map_status').get(pk=campaign_id)
        # Use chunked CONCAT writes to avoid MySQL max_allowed_packet errors on
        # large cities (e.g. Houston).  build_streets_geojson is kept for the
        # paginated API endpoint in views.py (returns to client, no DB write).
        _write_streets_geojson_chunked(campaign_id, geo_limit=campaign.geo_limit)
        Campaign.objects.filter(pk=campaign_id).update(map_status=final_status)
        logger.info(
            'render_campaign_geojson: campaign %s rendered, final_status=%s',
            campaign_id, final_status,
        )
    except Campaign.DoesNotExist:
        logger.error('render_campaign_geojson: campaign %s not found', campaign_id)
    except Exception as exc:
        logger.error('render_campaign_geojson: campaign %s failed: %s', campaign_id, exc)
        Campaign.objects.filter(pk=campaign_id).update(
            map_status='error', map_error=str(exc),
        )
        raise


@shared_task
def refresh_campaign_address_points(campaign_id: int) -> None:
    """
    Re-fetch address points for all cities using the campaign's current geo_limit bbox.
    Called after geo_limit is saved so address counts reflect the new boundary.
    Best-effort: failures are logged but do not raise.
    """
    try:
        campaign = Campaign.objects.get(pk=campaign_id)
    except Campaign.DoesNotExist:
        logger.error('refresh_campaign_address_points: campaign %s not found', campaign_id)
        return

    ADDRESS_FETCH_BLOCK_LIMIT = 10_000
    streets_qs = (
        campaign.streets.filter(geometry__intersects=campaign.geo_limit)
        if campaign.geo_limit else campaign.streets
    )
    if streets_qs.count() > ADDRESS_FETCH_BLOCK_LIMIT:
        logger.info(
            'refresh_campaign_address_points: skipping campaign %s — area exceeds block limit (see #119)',
            campaign_id,
        )
        return

    geo_limit_bbox = campaign.geo_limit.extent if campaign.geo_limit else None
    for city_index, city in enumerate(campaign.cities):
        try:
            address_coords = query_overpass_addresses(city, bbox=geo_limit_bbox)
            AddressPoint.objects.filter(campaign=campaign, city_index=city_index).delete()
            if address_coords:
                AddressPoint.objects.bulk_create([
                    AddressPoint(campaign=campaign, city_index=city_index,
                                 location=Point(lon, lat, srid=4326))
                    for lon, lat in address_coords
                ], batch_size=2000)
            logger.info(
                'refresh_campaign_address_points: %d address points for city %d of campaign %s',
                len(address_coords), city_index, campaign_id,
            )
        except Exception as exc:
            city_label = city if isinstance(city, str) else city.get('name', str(city))
            logger.warning(
                'refresh_campaign_address_points: failed for %s (non-fatal): %s',
                city_label, exc,
            )


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

        # Guard: check existing block total before importing this city.
        # We exclude blocks already associated with this city_index so that
        # a re-fetch doesn't double-count streets being replaced.
        existing_blocks = (
            campaign.streets
            .exclude(campaign_streets__city_index=city_index)
            .count()
        )
        if existing_blocks >= MAX_CAMPAIGN_BLOCKS:
            raise ValueError(
                f'Campaign already has {existing_blocks:,} blocks (limit {MAX_CAMPAIGN_BLOCKS:,}); '
                f'skipping city "{city_label}". Remove some cities to stay within the limit.'
            )

        block_count = 0
        for way in ways:
            for block in split_way_at_intersections(way, intersection_nodes):
                if len(block['coords']) < 2:
                    continue
                if existing_blocks + block_count >= MAX_CAMPAIGN_BLOCKS:
                    raise ValueError(
                        f'Block limit of {MAX_CAMPAIGN_BLOCKS:,} reached while importing city '
                        f'"{city_label}". Import stopped at {existing_blocks + block_count:,} total blocks. '
                        f'Use a more specific city or draw a tighter geo boundary.'
                    )
                # Upsert the Street keyed by city_name + osm_id + block_index
                street, _ = Street.objects.update_or_create(
                    city_name=city_label,
                    osm_id=way['osm_id'],
                    block_index=block['block_index'],
                    defaults={
                        'name': way['name'],
                        'geometry': LineString(block['coords']),
                        'start_node_id': block['start_node_id'],
                        'end_node_id': block['end_node_id'],
                    },
                )
                # Link street to this campaign (upsert; update city_index if it changed)
                CampaignStreet.objects.update_or_create(
                    campaign=campaign,
                    street=street,
                    defaults={'city_index': city_index},
                )
                block_count += 1

        logger.info("fetch_city_osm_data: imported %d blocks for %s", block_count, city_label)
        if block_count == 0:
            raise ValueError(f'City "{city_label}" was found but no streets were imported')

        # Fetch address points — best-effort, does not fail the city job if it errors.
        # If a geo_limit polygon is set, restrict the query to its bounding box to avoid
        # timeouts when the city is large (e.g. a county).
        # Skip entirely for very large campaigns — reliable fetching requires a parallel
        # tile-based workflow that isn't implemented yet (see #119).
        ADDRESS_FETCH_BLOCK_LIMIT = 10_000
        streets_qs = campaign.streets.filter(geometry__intersects=campaign.geo_limit) if campaign.geo_limit else campaign.streets
        blocks_in_area = streets_qs.count()
        if blocks_in_area > ADDRESS_FETCH_BLOCK_LIMIT:
            logger.info(
                "fetch_city_osm_data: skipping address fetch for %s — %d blocks in area exceeds limit of %d (see #119)",
                city_label, blocks_in_area, ADDRESS_FETCH_BLOCK_LIMIT,
            )
        else:
            try:
                geo_limit_bbox = campaign.geo_limit.extent if campaign.geo_limit else None
                address_coords = query_overpass_addresses(city, bbox=geo_limit_bbox)
                AddressPoint.objects.filter(campaign=campaign, city_index=city_index).delete()
                if address_coords:
                    AddressPoint.objects.bulk_create([
                        AddressPoint(
                            campaign=campaign,
                            city_index=city_index,
                            location=Point(lon, lat, srid=4326),
                        )
                        for lon, lat in address_coords
                    ], batch_size=2000)
                logger.info(
                    "fetch_city_osm_data: imported %d address points for %s",
                    len(address_coords), city_label,
                )
            except Exception as exc:
                logger.warning(
                    "fetch_city_osm_data: address point fetch failed for %s (non-fatal): %s",
                    city_label, exc,
                )

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


# ── Watchdog: detect and recover stuck CityFetchJob records ───────────────────

STUCK_JOB_THRESHOLD_MINUTES = 30


@shared_task
def watchdog_stuck_jobs() -> dict:
    """
    Periodic task that:
    1. Finds CityFetchJob records stuck in 'generating' status for longer than
       STUCK_JOB_THRESHOLD_MINUTES and marks them as 'error'.
    2. Finds Campaign records stuck in 'rendering' status for longer than
       STUCK_JOB_THRESHOLD_MINUTES and re-dispatches render_campaign_geojson.

    Run every 15 minutes via Celery beat (configured in CELERY_BEAT_SCHEDULE).

    Returns a summary dict: {'found': N, 'marked_error': [...], 'requeued_rendering': [...]}.
    """
    cutoff = timezone.now() - timedelta(minutes=STUCK_JOB_THRESHOLD_MINUTES)

    # ── Part 1: stuck CityFetchJob records ────────────────────────────────────
    stuck_jobs = CityFetchJob.objects.filter(
        status='generating',
        updated_at__lt=cutoff,
    ).select_related('campaign')

    error_msg = (
        f'Job stuck in generating status for more than '
        f'{STUCK_JOB_THRESHOLD_MINUTES} minutes; marked as error by watchdog'
    )

    marked = []
    job_details = []  # collected for the admin email body
    for job in stuck_jobs:
        logger.warning(
            'watchdog_stuck_jobs: job pk=%d campaign=%s city=%s stuck since %s — marking error',
            job.pk, job.campaign_id, job.city_name, job.updated_at.isoformat(),
        )
        CityFetchJob.objects.filter(pk=job.pk).update(
            status='error',
            error=error_msg,
        )
        _sync_campaign_map_status(job.campaign_id)
        marked.append(job.pk)
        job_details.append({
            'pk': job.pk,
            'city_name': job.city_name,
            'campaign_id': job.campaign_id,
            'campaign_slug': job.campaign.slug,
            'stuck_since': job.updated_at.isoformat(),
        })

    if marked:
        logger.warning(
            'watchdog_stuck_jobs: marked %d stuck job(s) as error: pks=%s',
            len(marked), marked,
        )
        _send_watchdog_admin_email(job_details)

    # ── Part 2: campaigns stuck in 'rendering' ────────────────────────────────
    stuck_rendering = list(
        Campaign.objects.filter(map_status='rendering', updated_at__lt=cutoff)
    )
    requeued = []
    rendering_details = []
    for campaign in stuck_rendering:
        logger.warning(
            'watchdog_stuck_jobs: campaign pk=%d slug=%s stuck in rendering since %s — re-dispatching',
            campaign.pk, campaign.slug, campaign.updated_at.isoformat(),
        )
        render_campaign_geojson.delay(campaign.pk, final_status='ready')
        requeued.append(campaign.pk)
        rendering_details.append({
            'pk': campaign.pk,
            'campaign_slug': campaign.slug,
            'stuck_since': campaign.updated_at.isoformat(),
        })

    if requeued:
        logger.warning(
            'watchdog_stuck_jobs: re-dispatched render for %d stuck campaign(s): pks=%s',
            len(requeued), requeued,
        )
        _send_watchdog_rendering_email(rendering_details)

    if not marked and not requeued:
        logger.info('watchdog_stuck_jobs: no stuck jobs found')

    return {'found': len(marked), 'marked_error': marked, 'requeued_rendering': requeued}


def _send_watchdog_admin_email(job_details: list[dict]) -> None:
    """
    Send a single batched email to all active superusers summarising the jobs
    the watchdog just marked as error.  Uses send_mail() addressed to every
    User with is_superuser=True, is_active=True, and a non-empty email address.
    Failures are logged but not re-raised — a broken email backend must not
    abort the watchdog task itself.
    """
    User = get_user_model()
    recipients = list(
        User.objects.filter(is_superuser=True, is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    if not recipients:
        logger.warning('watchdog_stuck_jobs: no superuser email addresses found; skipping notification')
        return

    n = len(job_details)
    subject = f'Watchdog: {n} stuck CityFetchJob{"s" if n != 1 else ""} marked as error'

    lines = [
        f'{n} CityFetchJob record{"s were" if n != 1 else " was"} found stuck in '
        f'"generating" status for more than {STUCK_JOB_THRESHOLD_MINUTES} minutes '
        f'and {"have" if n != 1 else "has"} been marked as "error".',
        '',
        'Affected jobs:',
    ]
    for d in job_details:
        lines.append(
            f'  - Job pk={d["pk"]}  city="{d["city_name"]}"'
            f'  campaign slug="{d["campaign_slug"]}" (id={d["campaign_id"]})'
            f'  stuck since {d["stuck_since"]}'
        )
    lines += [
        '',
        f'Threshold: {STUCK_JOB_THRESHOLD_MINUTES} minutes',
        'No action is required if the underlying Celery worker has already recovered.',
        'If the problem persists, check the worker logs and Overpass API availability.',
    ]
    message = '\n'.join(lines)

    try:
        send_mail(subject, message, from_email=None, recipient_list=recipients, fail_silently=False)
    except Exception as exc:
        logger.error('watchdog_stuck_jobs: failed to send admin email: %s', exc)


def _send_watchdog_rendering_email(campaign_details: list[dict]) -> None:
    """
    Notify superusers that one or more campaigns were found stuck in 'rendering'
    and have been re-queued by the watchdog.
    """
    User = get_user_model()
    recipients = list(
        User.objects.filter(is_superuser=True, is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    if not recipients:
        logger.warning('watchdog_stuck_jobs: no superuser email addresses found; skipping rendering notification')
        return

    n = len(campaign_details)
    subject = f'Watchdog: {n} campaign{"s" if n != 1 else ""} stuck in rendering — re-queued'

    lines = [
        f'{n} campaign{"s were" if n != 1 else " was"} found stuck in "rendering" status '
        f'for more than {STUCK_JOB_THRESHOLD_MINUTES} minutes. '
        f'render_campaign_geojson has been re-dispatched for {"each" if n != 1 else "it"}.',
        '',
        'Affected campaigns:',
    ]
    for d in campaign_details:
        lines.append(
            f'  - Campaign pk={d["pk"]}  slug="{d["campaign_slug"]}"'
            f'  stuck since {d["stuck_since"]}'
        )
    lines += [
        '',
        f'Threshold: {STUCK_JOB_THRESHOLD_MINUTES} minutes',
        'The render task has been re-queued. Check worker logs if the problem persists.',
    ]
    message = '\n'.join(lines)

    try:
        send_mail(subject, message, from_email=None, recipient_list=recipients, fail_silently=False)
    except Exception as exc:
        logger.error('watchdog_stuck_jobs: failed to send rendering notification email: %s', exc)


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


# ── Database backup ────────────────────────────────────────────────────────────

BACKUP_RETENTION_DAYS = 30


@shared_task
def backup_database() -> dict:
    """
    Dump the MySQL database, compress it with gzip, and upload to S3.

    Key format: backups/leafletter-YYYY-MM-DD-HHMMSS.sql.gz
    Prunes objects under the backups/ prefix older than BACKUP_RETENTION_DAYS.
    On any failure, emails active superusers and does NOT re-raise, so the
    task is marked successful in Celery rather than endlessly retried.

    Returns a dict with 'key' (uploaded S3 key) and 'pruned' (count deleted).
    """
    try:
        return _run_backup()
    except Exception as exc:
        logger.error('backup_database: failed: %s', exc, exc_info=True)
        _send_backup_failure_email(exc)
        return {'error': str(exc)}


def _run_backup() -> dict:
    """Core logic for backup_database, separated so the task wrapper can catch all exceptions."""
    # ── Build mysqldump command ──────────────────────────────────────────────
    db_name = os.environ.get('MYSQL_DATABASE', 'leafletter')
    db_user = os.environ.get('MYSQL_USER', 'leafletter')
    db_password = os.environ.get('MYSQL_PASSWORD', 'leafletter')
    db_host = os.environ.get('MYSQL_HOST', 'localhost')
    db_port = os.environ.get('MYSQL_PORT', '3306')

    cmd = [
        'mysqldump',
        f'--host={db_host}',
        f'--port={db_port}',
        f'--user={db_user}',
        f'--password={db_password}',
        '--single-transaction',
        '--routines',
        '--triggers',
        db_name,
    ]

    logger.info('backup_database: starting mysqldump for database %s on %s', db_name, db_host)
    result = subprocess.run(
        cmd,
        capture_output=True,
        check=True,
    )
    sql_bytes = result.stdout
    logger.info('backup_database: dump complete, %d bytes raw', len(sql_bytes))

    # ── Compress ─────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
        gz.write(sql_bytes)
    compressed = buf.getvalue()
    logger.info('backup_database: compressed to %d bytes', len(compressed))

    # ── Build S3 key ─────────────────────────────────────────────────────────
    timestamp = datetime.now(dt_timezone.utc).strftime('%Y-%m-%d-%H%M%S')
    key = f'backups/leafletter-{timestamp}.sql.gz'

    # ── S3 client ─────────────────────────────────────────────────────────────
    bucket = os.environ.get(
        'BACKUP_S3_BUCKET',
        os.environ.get('AWS_STORAGE_BUCKET_NAME', 'leafletter'),
    )
    s3 = boto3.client(
        's3',
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
        endpoint_url=os.environ.get('AWS_S3_ENDPOINT_URL') or None,
        region_name=os.environ.get('AWS_S3_REGION_NAME') or None,
    )

    # ── Upload ────────────────────────────────────────────────────────────────
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=compressed,
        ContentType='application/gzip',
    )
    logger.info('backup_database: uploaded s3://%s/%s', bucket, key)

    # ── Prune old backups ─────────────────────────────────────────────────────
    pruned = _prune_old_backups(s3, bucket, retention_days=BACKUP_RETENTION_DAYS)

    return {'key': key, 'pruned': pruned}


def _prune_old_backups(s3_client, bucket: str, retention_days: int) -> int:
    """
    Delete objects under the backups/ prefix in *bucket* that are older than
    *retention_days*.  Returns the number of objects deleted.
    """
    cutoff = datetime.now(dt_timezone.utc) - timedelta(days=retention_days)
    paginator = s3_client.get_paginator('list_objects_v2')
    to_delete = []
    for page in paginator.paginate(Bucket=bucket, Prefix='backups/'):
        for obj in page.get('Contents', []):
            if obj['LastModified'] < cutoff:
                to_delete.append({'Key': obj['Key']})

    if not to_delete:
        logger.info('backup_database: no old backups to prune (cutoff=%s)', cutoff.date())
        return 0

    # S3 delete_objects accepts up to 1000 keys per call
    deleted = 0
    for i in range(0, len(to_delete), 1000):
        batch = to_delete[i:i + 1000]
        s3_client.delete_objects(Bucket=bucket, Delete={'Objects': batch})
        deleted += len(batch)

    logger.info('backup_database: pruned %d old backup(s) (older than %s)', deleted, cutoff.date())
    return deleted


def _send_backup_failure_email(exc: Exception) -> None:
    """
    Email active superusers when the database backup fails.
    Failures are logged but not re-raised.
    """
    User = get_user_model()
    recipients = list(
        User.objects.filter(is_superuser=True, is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    if not recipients:
        logger.warning('backup_database: no superuser email addresses found; skipping failure notification')
        return

    subject = 'Leafletter: database backup failed'
    message = '\n'.join([
        'The scheduled database backup task failed with the following error:',
        '',
        f'  {type(exc).__name__}: {exc}',
        '',
        'Check the Celery worker logs for a full traceback.',
        'No backup was uploaded for this run.',
    ])

    try:
        send_mail(subject, message, from_email=None, recipient_list=recipients, fail_silently=False)
    except Exception as mail_exc:
        logger.error('backup_database: failed to send failure notification email: %s', mail_exc)

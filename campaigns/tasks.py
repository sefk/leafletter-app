import logging
from collections import Counter

import requests
from celery import shared_task
from django.contrib.gis.geos import LineString

from .models import Campaign, Street

logger = logging.getLogger(__name__)

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
NOMINATIM_HEADERS = {'User-Agent': 'Leafletter/1.0 (github.com/sefk/leafletter-app)'}
CITY_TYPES = {'city', 'town', 'village', 'municipality', 'borough'}

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
[out:json][timeout:60];
area({area_id})->.searchArea;
way["highway"](area.searchArea);
out geom;
"""
    else:
        city_name = city if isinstance(city, str) else city.get('name', str(city))
        city_label = city_name
        query = f"""
[out:json][timeout:60];
area[name="{city_name}"]->.searchArea;
way["highway"](area.searchArea);
out geom;
"""
    try:
        resp = requests.post(OVERPASS_URL, data={'data': query}, timeout=90)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Overpass query failed for city %s: %s", city_label, exc)
        raise

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


def query_address_nodes(city) -> list[dict]:
    """
    Query Overpass for OSM address nodes (addr:housenumber + addr:street) within a city.
    Returns a list of dicts: {street, housenumber, lat, lon}.
    """
    if isinstance(city, dict) and city.get('osm_type') == 'relation' and 'osm_id' in city:
        area_id = 3600000000 + city['osm_id']
        query = f"""
[out:json][timeout:30];
area({area_id})->.searchArea;
node["addr:housenumber"]["addr:street"](area.searchArea);
out body;
"""
    else:
        city_name = city if isinstance(city, str) else city.get('name', str(city))
        query = f"""
[out:json][timeout:30];
area[name="{city_name}"]->.searchArea;
node["addr:housenumber"]["addr:street"](area.searchArea);
out body;
"""
    try:
        resp = requests.post(OVERPASS_URL, data={'data': query}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Address node query failed (skipping address ranges): %s", exc)
        return []

    nodes = []
    for element in data.get('elements', []):
        tags = element.get('tags', {})
        raw_num = tags.get('addr:housenumber', '')
        street = tags.get('addr:street', '').lower().strip()
        try:
            housenumber = int(raw_num.split('-')[0].strip())  # handle "100-102" → 100
        except (ValueError, AttributeError):
            continue
        nodes.append({
            'street': street,
            'housenumber': housenumber,
            'lat': element['lat'],
            'lon': element['lon'],
        })
    return nodes


def assign_address_ranges(campaign_id: int, address_nodes: list[dict]) -> None:
    """
    For each Street segment in the campaign, find address nodes with a matching
    street name within ~60 m and set addr_from/addr_to to the min/max house numbers.
    """
    if not address_nodes:
        return

    # Build lookup: street_name_lower → list of address nodes
    by_street: dict[str, list[dict]] = {}
    for node in address_nodes:
        by_street.setdefault(node['street'], []).append(node)

    # Rough degree-to-metre factor: 1 deg lat ≈ 111 km; 60 m ≈ 0.00054 deg
    THRESHOLD_SQ = 0.00054 ** 2

    from .models import Street  # avoid circular import at module level
    streets = Street.objects.filter(campaign_id=campaign_id).exclude(name='')

    updates = []
    for street in streets:
        candidates = by_street.get(street.name.lower().strip(), [])
        if not candidates:
            continue

        # Midpoint of segment
        coords = street.geometry.coords
        mid = coords[len(coords) // 2]
        mid_lon, mid_lat = mid[0], mid[1]

        addr_nums = []
        for node in candidates:
            dlat = node['lat'] - mid_lat
            dlon = (node['lon'] - mid_lon) * 0.7  # rough cos(lat) correction
            if dlat * dlat + dlon * dlon <= THRESHOLD_SQ:
                addr_nums.append(node['housenumber'])

        if addr_nums:
            street.addr_from = min(addr_nums)
            street.addr_to = max(addr_nums)
            updates.append(street)

    if updates:
        Street.objects.bulk_update(updates, ['addr_from', 'addr_to'])
        logger.info("Assigned address ranges to %d segments", len(updates))


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


@shared_task(bind=True, max_retries=5)
def fetch_osm_segments(self, campaign_id: int) -> None:
    try:
        campaign = Campaign.objects.get(pk=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("fetch_osm_segments: campaign %s not found", campaign_id)
        return

    campaign.map_status = 'generating'
    campaign.map_error = ''
    campaign.bbox = None
    campaign.save(update_fields=['map_status', 'map_error', 'bbox'])

    try:
        cities = campaign.cities  # list of city name strings or dicts
        for city in cities:
            city_label = city if isinstance(city, str) else city.get('name', str(city))
            logger.info("Fetching OSM segments for city: %s", city_label)
            # Only call lookup_city for string-format cities (dict cities have osm_id already)
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
                            'start_node_id': block['start_node_id'],
                            'end_node_id': block['end_node_id'],
                        },
                    )
                    block_count += 1
            logger.info("Imported %d blocks for %s", block_count, city_label)
            if block_count == 0:
                raise ValueError(f'City "{city_label}" was found but no streets were imported')
            address_nodes = query_address_nodes(city)
            logger.info("Fetched %d address nodes for %s", len(address_nodes), city_label)
            assign_address_ranges(campaign.pk, address_nodes)
        min_lon = min_lat = float('inf')
        max_lon = max_lat = float('-inf')
        for street in campaign.streets.only('geometry'):
            xmin, ymin, xmax, ymax = street.geometry.extent
            min_lon = min(min_lon, xmin)
            min_lat = min(min_lat, ymin)
            max_lon = max(max_lon, xmax)
            max_lat = max(max_lat, ymax)
        if min_lon != float('inf'):
            campaign.bbox = [[min_lat, min_lon], [max_lat, max_lon]]
        campaign.map_status = 'ready'
    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError) as exc:
        # For HTTPError only retry on 5xx (gateway/server errors), not 4xx
        is_server_error = (
            not isinstance(exc, requests.exceptions.HTTPError)
            or (exc.response is not None and exc.response.status_code >= 500)
        )
        if is_server_error and self.request.retries < self.max_retries:
            countdown = min(60 * 2 ** self.request.retries, 600)
            logger.warning(
                "fetch_osm_segments transient error for campaign %s, retrying in %ds (%d/%d): %s",
                campaign_id, countdown, self.request.retries + 1, self.max_retries, exc,
            )
            raise self.retry(exc=exc, countdown=countdown)
        logger.error("fetch_osm_segments failed for campaign %s: %s", campaign_id, exc)
        campaign.map_status = 'error'
        campaign.map_error = str(exc)
    except Exception as exc:
        logger.error("fetch_osm_segments failed for campaign %s: %s", campaign_id, exc)
        campaign.map_status = 'error'
        campaign.map_error = str(exc)
    finally:
        campaign.save(update_fields=['map_status', 'map_error', 'bbox'])

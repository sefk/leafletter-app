import logging

import requests
from celery import shared_task
from django.contrib.gis.geos import LineString

from .models import Campaign, Street

logger = logging.getLogger(__name__)

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'

# Highway types to include (exclude footways, paths, etc.)
HIGHWAY_INCLUDE = {
    'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
    'unclassified', 'residential', 'motorway_link', 'trunk_link',
    'primary_link', 'secondary_link', 'tertiary_link', 'living_street',
    'service',
}


def query_overpass(city: str) -> list[dict]:
    """
    Query Overpass API for driveable highway ways within a named area.
    Returns a list of dicts: {osm_id, name, coords (list of (lon, lat) tuples)}.
    """
    query = f"""
[out:json][timeout:60];
area[name="{city}"]->.searchArea;
way["highway"](area.searchArea);
out geom;
"""
    try:
        resp = requests.post(OVERPASS_URL, data={'data': query}, timeout=90)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Overpass query failed for city %s: %s", city, exc)
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
        })
    return ways


@shared_task
def fetch_osm_segments(campaign_id: int) -> None:
    try:
        campaign = Campaign.objects.get(pk=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("fetch_osm_segments: campaign %s not found", campaign_id)
        return

    campaign.map_status = 'generating'
    campaign.save(update_fields=['map_status'])

    try:
        cities = campaign.cities  # list of city name strings
        for city in cities:
            logger.info("Fetching OSM segments for city: %s", city)
            ways = query_overpass(city)
            for way in ways:
                Street.objects.update_or_create(
                    campaign=campaign,
                    osm_id=way['osm_id'],
                    defaults={
                        'name': way['name'],
                        'geometry': LineString(way['coords']),
                    },
                )
            logger.info("Imported %d streets for %s", len(ways), city)
        campaign.map_status = 'ready'
    except Exception as exc:
        logger.error("fetch_osm_segments failed for campaign %s: %s", campaign_id, exc)
        campaign.map_status = 'error'
    finally:
        campaign.save(update_fields=['map_status'])

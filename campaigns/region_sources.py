"""Adapters for ingesting RegionSource data into Region rows.

Each adapter takes a RegionSource (which carries source-specific config) and
produces Region rows under it. Wipe-and-replace semantics — existing regions
for the source are deleted, then re-created from the adapter's output.
"""

import json

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.db import transaction
from django.utils import timezone

from .models import Region, RegionSource


class IngestError(Exception):
    """Raised when input data can't be parsed into Region rows."""


# Property names tried in order when no explicit name_property is configured.
NAME_PROPERTY_FALLBACKS = ['name', 'NAME', 'Name', 'NAMELSAD', 'label', 'neighborhood']
ID_PROPERTY_FALLBACKS = ['id', 'ID', 'GEOID', 'geoid']


def ingest_geojson_upload(source: RegionSource, file_or_text) -> dict:
    """Parse uploaded GeoJSON FeatureCollection and replace `source`'s regions.

    `file_or_text` may be a file-like object (as from request.FILES) or a string.
    Reads `name_property` and `id_property` from `source.config` when present.

    Returns a summary dict: {created, skipped, warnings}.
    """
    if hasattr(file_or_text, 'read'):
        raw = file_or_text.read()
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
    else:
        raw = file_or_text

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise IngestError(f'File is not valid JSON: {e}')

    if not isinstance(data, dict) or data.get('type') != 'FeatureCollection':
        raise IngestError('Expected a GeoJSON FeatureCollection at the top level.')

    features = data.get('features') or []
    if not features:
        raise IngestError('FeatureCollection has no features.')

    name_property = (source.config or {}).get('name_property') or ''
    id_property = (source.config or {}).get('id_property') or ''

    parsed = []  # list of (external_id, name, MultiPolygon)
    warnings = []
    skipped = 0

    for idx, feature in enumerate(features):
        if not isinstance(feature, dict):
            skipped += 1
            warnings.append(f'Feature #{idx}: not an object, skipped.')
            continue
        props = feature.get('properties') or {}
        geom_dict = feature.get('geometry')
        if not geom_dict:
            skipped += 1
            warnings.append(f'Feature #{idx}: missing geometry, skipped.')
            continue
        try:
            geom = GEOSGeometry(json.dumps(geom_dict), srid=4326)
        except Exception as e:
            skipped += 1
            warnings.append(f'Feature #{idx}: unreadable geometry ({e}), skipped.')
            continue
        if isinstance(geom, Polygon):
            multi = MultiPolygon(geom, srid=4326)
        elif isinstance(geom, MultiPolygon):
            multi = geom
        else:
            skipped += 1
            warnings.append(
                f'Feature #{idx}: geometry type "{geom.geom_type}" is not Polygon/MultiPolygon, skipped.'
            )
            continue
        if not multi.valid:
            # Try a buffer(0) repair before giving up.
            repaired = multi.buffer(0)
            if isinstance(repaired, Polygon):
                multi = MultiPolygon(repaired, srid=4326)
            elif isinstance(repaired, MultiPolygon) and repaired.valid:
                multi = repaired
            else:
                skipped += 1
                warnings.append(f'Feature #{idx}: invalid geometry that could not be repaired, skipped.')
                continue

        name = _pick_property(props, name_property, NAME_PROPERTY_FALLBACKS) or f'Region {idx + 1}'
        external_id = _pick_property(props, id_property, ID_PROPERTY_FALLBACKS) or ''
        parsed.append((str(external_id), str(name), multi))

    if not parsed:
        raise IngestError('No usable polygon features found in the file.')

    coverage_extent = _union_extent([m for _, _, m in parsed])
    coverage_polygon = Polygon.from_bbox(coverage_extent)
    coverage_polygon.srid = 4326

    with transaction.atomic():
        Region.objects.filter(source=source).delete()
        Region.objects.bulk_create([
            Region(
                source=source,
                external_id=ext_id,
                name=name,
                geometry=geom,
            )
            for ext_id, name, geom in parsed
        ])
        source.coverage = coverage_polygon
        source.last_ingested_at = timezone.now()
        source.save(update_fields=['coverage', 'last_ingested_at', 'updated_at'])

    return {
        'created': len(parsed),
        'skipped': skipped,
        'warnings': warnings,
    }


def _pick_property(props, explicit_key, fallbacks):
    """Return props[explicit_key] if set & non-empty, else first non-empty fallback."""
    if explicit_key:
        v = props.get(explicit_key)
        if v not in (None, ''):
            return v
        return None
    for k in fallbacks:
        if k in props and props[k] not in (None, ''):
            return props[k]
    return None


def _union_extent(geometries):
    """Compute (xmin, ymin, xmax, ymax) over a list of geometries."""
    xmin = ymin = float('inf')
    xmax = ymax = float('-inf')
    for g in geometries:
        gx_min, gy_min, gx_max, gy_max = g.extent
        xmin = min(xmin, gx_min)
        ymin = min(ymin, gy_min)
        xmax = max(xmax, gx_max)
        ymax = max(ymax, gy_max)
    return (xmin, ymin, xmax, ymax)

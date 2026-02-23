import json

from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Campaign, Street, Trip


def campaign_detail(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug, status='published')
    return render(request, 'campaigns/campaign_detail.html', {'campaign': campaign})


@require_GET
def campaign_streets_geojson(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug, status='published')
    streets = campaign.streets.all()

    features = []
    for street in streets:
        features.append({
            'type': 'Feature',
            'id': street.pk,
            'geometry': json.loads(street.geometry.geojson),
            'properties': {
                'osm_id': street.osm_id,
                'name': street.name,
            },
        })

    return JsonResponse({
        'type': 'FeatureCollection',
        'features': features,
    })


@require_GET
def campaign_coverage_geojson(request, slug):
    """
    Return GeoJSON of all street segments that have been covered by at least one trip.
    Returns individual features (MySQL doesn't support spatial union aggregates).
    """
    campaign = get_object_or_404(Campaign, slug=slug, status='published')

    # Streets referenced by any trip for this campaign
    covered_streets = Street.objects.filter(
        trip__campaign=campaign
    ).distinct()

    features = []
    for street in covered_streets:
        features.append({
            'type': 'Feature',
            'id': street.pk,
            'geometry': json.loads(street.geometry.geojson),
            'properties': {
                'osm_id': street.osm_id,
                'name': street.name,
            },
        })

    return JsonResponse({
        'type': 'FeatureCollection',
        'features': features,
    })


@csrf_exempt
@require_POST
def log_trip(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug, status='published')

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON')

    segment_ids = body.get('segment_ids', [])
    worker_name = body.get('worker_name', '').strip()
    notes = body.get('notes', '').strip()

    if not segment_ids:
        return HttpResponseBadRequest('No segments selected')

    # Validate segments belong to this campaign
    streets = Street.objects.filter(campaign=campaign, pk__in=segment_ids)
    if not streets.exists():
        return HttpResponseBadRequest('No valid segments found')

    trip = Trip.objects.create(
        campaign=campaign,
        worker_name=worker_name,
        notes=notes,
    )
    trip.streets.set(streets)

    return JsonResponse({'status': 'ok', 'trip_id': str(trip.pk)})

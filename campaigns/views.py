import json

import requests
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import CampaignForm
from .models import Campaign, Street, Trip
from .tasks import fetch_osm_segments, NOMINATIM_URL, NOMINATIM_HEADERS, CITY_TYPES

_login_required = login_required(login_url='/admin/login/')


def public_campaign_list(request):
    campaigns = Campaign.objects.filter(status='published').order_by('start_date')
    return render(request, 'campaigns/campaign_list.html', {'campaigns': campaigns})


def campaign_detail(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug, status='published')
    return render(request, 'campaigns/campaign_detail.html', {
        'campaign': campaign,
        'bbox_json': json.dumps(campaign.bbox),
    })


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


# ── Manager UI views ──────────────────────────────────────────────────────────

@_login_required
def manage_campaign_list(request):
    campaigns = Campaign.objects.exclude(status='deleted').annotate(
        street_count=Count('streets', distinct=True),
        trip_count=Count('trips', distinct=True),
    )
    inflight = campaigns.filter(map_status__in=('pending', 'generating')).order_by('updated_at')
    return render(request, 'campaigns/manage/campaign_list.html', {
        'campaigns': campaigns,
        'inflight': inflight,
    })


@_login_required
def manage_campaign_create(request):
    if request.method == 'POST':
        form = CampaignForm(request.POST)
        if form.is_valid():
            campaign = form.save(commit=False)
            campaign.status = 'draft'
            campaign.save()
            return redirect('manage_campaign_detail', slug=campaign.slug)
    else:
        form = CampaignForm()
    return render(request, 'campaigns/manage/campaign_form.html', {'form': form, 'action': 'Create'})


@_login_required
def manage_campaign_detail(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    total_blocks = campaign.streets.count()
    covered_blocks = Street.objects.filter(trip__campaign=campaign).distinct().count()
    trip_count = campaign.trips.count()
    pct = round(covered_blocks / total_blocks * 100) if total_blocks else 0
    recent_trips = campaign.trips.prefetch_related('streets').all()[:10]
    return render(request, 'campaigns/manage/campaign_detail.html', {
        'campaign': campaign,
        'total_blocks': total_blocks,
        'covered_blocks': covered_blocks,
        'trip_count': trip_count,
        'pct': pct,
        'recent_trips': recent_trips,
    })


@_login_required
def manage_campaign_edit(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    if request.method == 'POST':
        form = CampaignForm(request.POST, instance=campaign)
        if form.is_valid():
            old_cities = campaign.cities
            updated = form.save()
            if updated.status == 'published' and updated.cities != old_cities:
                updated.map_status = 'pending'
                updated.save(update_fields=['map_status'])
                fetch_osm_segments.delay(updated.pk)
            return redirect('manage_campaign_detail', slug=updated.slug)
    else:
        form = CampaignForm(instance=campaign)
    return render(request, 'campaigns/manage/campaign_form.html', {
        'form': form,
        'campaign': campaign,
        'action': 'Edit',
    })


@_login_required
@require_POST
def manage_campaign_publish(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    campaign.status = 'published'
    campaign.map_status = 'pending'
    campaign.save(update_fields=['status', 'map_status'])
    fetch_osm_segments.delay(campaign.pk)
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_campaign_delete(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    campaign.status = 'deleted'
    campaign.save(update_fields=['status'])
    return redirect('manage_campaign_list')


@_login_required
@require_POST
def manage_campaign_refetch(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    campaign.map_status = 'pending'
    campaign.save(update_fields=['map_status'])
    fetch_osm_segments.delay(campaign.pk)
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_GET
def city_search(request):
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': []})
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={'q': q, 'format': 'json', 'limit': 10},
            headers=NOMINATIM_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    results = []
    for r in data:
        is_place_city = r.get('class') == 'place' and r.get('type') in CITY_TYPES
        is_boundary = r.get('class') == 'boundary' and r.get('type') == 'administrative'
        if is_place_city or is_boundary:
            results.append({
                'name': r.get('name', q),
                'osm_id': int(r['osm_id']),
                'osm_type': r.get('osm_type', 'relation'),
                'display_name': r.get('display_name', ''),
            })
    return JsonResponse({'results': results})

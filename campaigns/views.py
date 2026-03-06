import json

import requests
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.gis.geos import Polygon
from django.db.models import Count
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import CampaignForm
from .models import Campaign, CityFetchJob, Street, Trip
from .tasks import build_streets_geojson, fetch_city_osm_data, queue_city_fetches, NOMINATIM_URL, NOMINATIM_HEADERS, CITY_TYPES

_login_required = login_required(login_url='/manage/login/')


def manage_login(request):
    if request.user.is_authenticated:
        return redirect('/manage/')
    next_url = request.POST.get('next', request.GET.get('next', '/manage/'))
    login_error = False
    if request.method == 'POST':
        user = authenticate(request, username=request.POST.get('username'), password=request.POST.get('password'))
        if user is not None:
            login(request, user)
            return redirect(next_url)
        login_error = True
    return render(request, 'campaigns/manage/login.html', {'next': next_url, 'login_error': login_error})


@require_POST
def manage_logout(request):
    logout(request)
    return redirect('/manage/login/')


def public_campaign_list(request):
    campaigns = Campaign.objects.filter(status='published').order_by('start_date')
    return render(request, 'campaigns/campaign_list.html', {'campaigns': campaigns})


def about(request):
    return render(request, 'campaigns/about.html')


def campaign_detail(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug, status='published')
    return render(request, 'campaigns/campaign_detail.html', {
        'campaign': campaign,
        'bbox_json': json.dumps(campaign.bbox),
    })


@require_GET
def campaign_streets_geojson(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug, status='published')

    if campaign.streets_geojson and not request.GET.get('all'):
        return HttpResponse(campaign.streets_geojson, content_type='application/json')

    streets = campaign.streets.all()

    if campaign.bbox and not request.GET.get('all'):
        sw, ne = campaign.bbox  # [[sw_lat, sw_lon], [ne_lat, ne_lon]]
        bbox_poly = Polygon.from_bbox((sw[1], sw[0], ne[1], ne[0]))  # (xmin, ymin, xmax, ymax)
        bbox_poly.srid = 4326
        streets = streets.filter(geometry__intersects=bbox_poly)

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
    Return GeoJSON of all street segments covered, tagged with trip metadata.
    Each (trip, street) pair becomes a separate feature so the frontend can
    color-code and toggle trips individually.
    """
    campaign = get_object_or_404(Campaign, slug=slug, status='published')

    trips = Trip.objects.filter(campaign=campaign, deleted=False).prefetch_related('streets')

    features = []
    for trip in trips:
        for street in trip.streets.all():
            features.append({
                'type': 'Feature',
                'id': f'{trip.pk}_{street.pk}',
                'geometry': json.loads(street.geometry.geojson),
                'properties': {
                    'trip_id': str(trip.pk),
                    'worker_name': trip.worker_name,
                    'recorded_at': trip.recorded_at.strftime('%Y-%m-%d'),
                    'street_name': street.name,
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


# ── Manager UI helpers ────────────────────────────────────────────────────────

def _incremental_city_indices(old_cities: list, new_cities: list) -> list[int] | None:
    """
    Return a list of indices in new_cities that are not in old_cities.
    Returns None if a full refetch is needed (any old city was removed or changed).
    Returns [] if the city list content is unchanged (e.g. only metadata tweaked).
    Comparison is by osm_id for dict cities, or by name for string cities.
    """
    def city_key(c):
        if isinstance(c, dict) and 'osm_id' in c:
            return ('id', c['osm_id'])
        return ('name', c if isinstance(c, str) else c.get('name', ''))

    old_keys = {city_key(c) for c in old_cities}
    new_keys_indexed = [(city_key(c), i) for i, c in enumerate(new_cities)]
    new_key_set = {k for k, _ in new_keys_indexed}

    if not old_keys.issubset(new_key_set):
        return None  # an old city was removed or changed → full refetch

    return [i for k, i in new_keys_indexed if k not in old_keys]


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
    all_trips = campaign.trips.prefetch_related('streets').all()
    city_fetch_jobs = list(campaign.city_fetch_jobs.all())
    blocks_per_city = dict(
        campaign.streets.values('city_index').annotate(c=Count('id')).values_list('city_index', 'c')
    )
    for job in city_fetch_jobs:
        job.block_count = blocks_per_city.get(job.city_index, 0)
    campaign_url = request.build_absolute_uri(f'/c/{campaign.slug}/')
    return render(request, 'campaigns/manage/campaign_detail.html', {
        'campaign': campaign,
        'campaign_url': campaign_url,
        'total_blocks': total_blocks,
        'all_trips': all_trips,
        'city_fetch_jobs': city_fetch_jobs,
        'bbox_json': json.dumps(campaign.bbox),
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
                new_indices = _incremental_city_indices(old_cities, updated.cities)
                if new_indices is None:
                    # Cities were removed or changed — full refetch
                    queue_city_fetches(updated.pk)
                elif new_indices:
                    # Only new cities added — fetch just those
                    queue_city_fetches(updated.pk, city_indices=new_indices)
                # else: no meaningful city changes, no fetch needed
            return redirect('manage_campaign_detail', slug=updated.slug)
    else:
        form = CampaignForm(instance=campaign)
    campaign_url = request.build_absolute_uri(f'/c/{campaign.slug}/')
    return render(request, 'campaigns/manage/campaign_form.html', {
        'form': form,
        'campaign': campaign,
        'action': 'Edit',
        'campaign_url': campaign_url,
    })


@_login_required
@require_POST
def manage_campaign_publish(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    campaign.status = 'published'
    campaign.save(update_fields=['status'])
    queue_city_fetches(campaign.pk)
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
    queue_city_fetches(campaign.pk)
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_city_refetch(request, slug, city_index):
    campaign = get_object_or_404(Campaign, slug=slug)
    cities = campaign.cities
    if city_index < 0 or city_index >= len(cities):
        return HttpResponseBadRequest('Invalid city index')
    city = cities[city_index]
    city_name = city if isinstance(city, str) else city.get('name', str(city))
    CityFetchJob.objects.update_or_create(
        campaign=campaign,
        city_index=city_index,
        defaults={'status': 'pending', 'error': '', 'city_name': city_name},
    )
    Campaign.objects.filter(pk=campaign.pk).update(map_status='generating')
    result = fetch_city_osm_data.delay(campaign.pk, city_index)
    CityFetchJob.objects.filter(campaign=campaign, city_index=city_index).update(
        celery_task_id=result.id,
    )
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_city_delete(request, slug, city_index):
    campaign = get_object_or_404(Campaign, slug=slug)
    Street.objects.filter(campaign=campaign, city_index=city_index).delete()
    CityFetchJob.objects.filter(campaign=campaign, city_index=city_index).update(status='pending', error='')
    update = {'streets_geojson': ''}
    if not campaign.streets.exists():
        update['map_status'] = 'pending'
    Campaign.objects.filter(pk=campaign.pk).update(**update)
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_campaign_update_bbox(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    try:
        body = json.loads(request.body)
        bbox = body['bbox']
        if (not isinstance(bbox, list) or len(bbox) != 2 or
                not all(isinstance(p, list) and len(p) == 2 and
                        all(isinstance(v, (int, float)) for v in p) for p in bbox)):
            raise ValueError('invalid bbox')
    except (json.JSONDecodeError, KeyError, ValueError):
        return HttpResponseBadRequest('Invalid bbox')
    geojson = build_streets_geojson(campaign.pk, bbox=bbox)
    Campaign.objects.filter(pk=campaign.pk).update(bbox=bbox, streets_geojson=geojson)
    return JsonResponse({'status': 'ok'})


@_login_required
@require_POST
def manage_trip_delete(request, slug, trip_id):
    campaign = get_object_or_404(Campaign, slug=slug)
    trip = get_object_or_404(Trip, pk=trip_id, campaign=campaign)
    trip.deleted = True
    trip.save(update_fields=['deleted'])
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_trip_restore(request, slug, trip_id):
    campaign = get_object_or_404(Campaign, slug=slug)
    trip = get_object_or_404(Trip, pk=trip_id, campaign=campaign)
    trip.deleted = False
    trip.save(update_fields=['deleted'])
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_trip_edit(request, slug, trip_id):
    campaign = get_object_or_404(Campaign, slug=slug)
    trip = get_object_or_404(Trip, pk=trip_id, campaign=campaign)
    trip.worker_name = request.POST.get('worker_name', trip.worker_name).strip()
    trip.notes = request.POST.get('notes', trip.notes).strip()
    trip.save(update_fields=['worker_name', 'notes'])
    return JsonResponse({'status': 'ok'})


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

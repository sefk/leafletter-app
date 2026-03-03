import json
import math

import requests
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import CampaignForm
from .models import Campaign, CityFetchJob, Street, Trip
from .tasks import fetch_city_osm_data, queue_city_fetches, NOMINATIM_URL, NOMINATIM_HEADERS, CITY_TYPES

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


@require_GET
def street_search(request, slug):
    """
    Search for street segments within a campaign by name (and optional block number).

    Query ?q=600+central:
    - Splits tokens into number (600) and word parts (central)
    - Fuzzy-matches word parts against Street.name (case-insensitive LIKE)
    - If a number is present, geocodes "q, city" via Nominatim to find the
      nearest segment; otherwise returns results sorted by name.
    Returns up to 8 results: [{id, name, centroid: [lat, lon], bbox: [[sw], [ne]]}]
    """
    campaign = get_object_or_404(Campaign, slug=slug, status='published')
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    tokens = q.split()
    number_tokens = [t for t in tokens if t.isdigit()]
    word_tokens = [t for t in tokens if not t.isdigit() and len(t) >= 2]

    if not word_tokens:
        return JsonResponse({'results': []})

    streets_qs = campaign.streets.all()
    for word in word_tokens:
        streets_qs = streets_qs.filter(name__icontains=word)
    streets_qs = streets_qs[:30]
    streets = list(streets_qs)

    # For each matching osm_id, count how many total blocks the way has
    from django.db.models import Max
    osm_ids = {s.osm_id for s in streets}
    block_counts = dict(
        campaign.streets.filter(osm_id__in=osm_ids)
        .values('osm_id')
        .annotate(count=Max('block_index'))
        .values_list('osm_id', 'count')
    )

    if not streets:
        return JsonResponse({'results': []})

    # Try to geocode for proximity ranking when a block number is given
    target_point = None
    if number_tokens and campaign.cities:
        city_name = campaign.cities[0] if isinstance(campaign.cities[0], str) else campaign.cities[0].get('name', '')
        geo_query = f"{q}, {city_name}"
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={'q': geo_query, 'format': 'json', 'limit': 1},
                headers=NOMINATIM_HEADERS,
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                target_point = (float(data[0]['lat']), float(data[0]['lon']))
        except Exception:
            pass

    def segment_centroid(street):
        coords = street.geometry.coords  # list of (lon, lat) tuples
        mid = coords[len(coords) // 2]
        return (mid[1], mid[0])  # (lat, lon)

    def haversine_approx(a, b):
        # Fast planar approximation — good enough for ranking within a city
        dlat = a[0] - b[0]
        dlon = (a[1] - b[1]) * math.cos(math.radians(a[0]))
        return dlat * dlat + dlon * dlon

    results = []
    for street in streets:
        ext = street.geometry.extent  # (min_lon, min_lat, max_lon, max_lat)
        centroid = segment_centroid(street)
        dist = haversine_approx(centroid, target_point) if target_point else None

        # Build a human-readable subtitle
        if street.addr_from is not None and street.addr_to is not None:
            if street.addr_from == street.addr_to:
                subtitle = f'{street.addr_from}'
            else:
                subtitle = f'{street.addr_from}–{street.addr_to}'
        else:
            max_block_index = block_counts.get(street.osm_id, 0)
            if max_block_index > 0:
                subtitle = f'block {street.block_index + 1} of {max_block_index + 1}'
            else:
                subtitle = None

        results.append({
            'id': street.pk,
            'name': street.name or 'Unnamed street',
            'subtitle': subtitle,
            'centroid': centroid,
            'bbox': [[ext[1], ext[0]], [ext[3], ext[2]]],  # [[sw_lat, sw_lon], [ne_lat, ne_lon]]
            '_dist': dist,
        })

    if target_point:
        results.sort(key=lambda r: r['_dist'])
    else:
        results.sort(key=lambda r: r['name'])

    for r in results:
        del r['_dist']

    return JsonResponse({'results': results[:8]})


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
    covered_blocks = Street.objects.filter(trip__campaign=campaign).distinct().count()
    trip_count = campaign.trips.count()
    pct = round(covered_blocks / total_blocks * 100) if total_blocks else 0
    recent_trips = campaign.trips.prefetch_related('streets').all()[:10]
    city_fetch_jobs = list(campaign.city_fetch_jobs.all())
    campaign_url = request.build_absolute_uri(f'/c/{campaign.slug}/')
    return render(request, 'campaigns/manage/campaign_detail.html', {
        'campaign': campaign,
        'campaign_url': campaign_url,
        'total_blocks': total_blocks,
        'covered_blocks': covered_blocks,
        'trip_count': trip_count,
        'pct': pct,
        'recent_trips': recent_trips,
        'city_fetch_jobs': city_fetch_jobs,
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

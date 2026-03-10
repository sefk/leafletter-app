import json
from datetime import date

import requests
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.gis.geos import Polygon
from django.db.models import Count, F, Q
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import CampaignForm
from .models import Campaign, CityFetchJob, Street, Trip
from .tasks import build_streets_geojson, fetch_city_osm_data, queue_city_fetches, render_campaign_geojson, _sync_campaign_map_status, NOMINATIM_URL, NOMINATIM_HEADERS, CITY_TYPES

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
    from django.contrib.auth import get_user_model
    User = get_user_model()
    admin_emails = list(User.objects.filter(is_superuser=True).values_list('email', flat=True))
    return render(request, 'campaigns/manage/login.html', {
        'next': next_url,
        'login_error': login_error,
        'admin_emails': admin_emails,
    })


@require_POST
def manage_logout(request):
    logout(request)
    return redirect('/manage/login/')


def public_campaign_list(request):
    today = date.today()
    published = Campaign.objects.filter(status='published')
    current = published.filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    ).order_by(F('end_date').asc(nulls_last=True))
    prior = published.filter(end_date__lt=today).order_by('-end_date')
    return render(request, 'campaigns/campaign_list.html', {
        'current_campaigns': current,
        'prior_campaigns': prior,
    })


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

    if not request.GET.get('all'):
        if campaign.geo_limit:
            streets = streets.filter(geometry__intersects=campaign.geo_limit)
        elif campaign.bbox:
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
                    'osm_id': street.osm_id,
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

    request.session[f'last_trip_{campaign.slug}'] = str(trip.pk)

    return JsonResponse({'status': 'ok', 'trip_id': str(trip.pk)})


@require_GET
def worker_get_trip(request, slug, trip_id):
    """Return trip details — only if this session logged the trip."""
    campaign = get_object_or_404(Campaign, slug=slug, status='published')
    if request.session.get(f'last_trip_{campaign.slug}') != str(trip_id):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Not your trip or session expired')
    trip = get_object_or_404(Trip, pk=trip_id, campaign=campaign, deleted=False)
    return JsonResponse({'trip_id': str(trip.pk), 'worker_name': trip.worker_name, 'notes': trip.notes})


@csrf_exempt
@require_POST
def worker_edit_trip(request, slug, trip_id):
    """Allow a worker to edit their last trip within the same session."""
    campaign = get_object_or_404(Campaign, slug=slug, status='published')
    if request.session.get(f'last_trip_{campaign.slug}') != str(trip_id):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Not your trip or session expired')
    trip = get_object_or_404(Trip, pk=trip_id, campaign=campaign, deleted=False)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON')
    trip.worker_name = body.get('worker_name', trip.worker_name).strip()
    trip.notes = body.get('notes', trip.notes).strip()
    trip.save(update_fields=['worker_name', 'notes'])
    return JsonResponse({'status': 'ok'})


# ── Manager UI helpers ────────────────────────────────────────────────────────

def _city_key(c):
    if isinstance(c, dict) and 'osm_id' in c:
        return ('id', c['osm_id'])
    return ('name', c if isinstance(c, str) else c.get('name', ''))


def _apply_city_list_changes(old_cities: list, campaign) -> None:
    """
    Reconcile city list changes without unnecessary refetches.
    - Removed cities: delete their streets and fetch jobs
    - Kept cities whose index shifted: renumber city_index on streets and jobs
    - New cities: queue fetches only for those
    """
    new_cities = campaign.cities
    old_key_to_idx = {_city_key(c): i for i, c in enumerate(old_cities)}
    new_key_to_idx = {_city_key(c): i for i, c in enumerate(new_cities)}

    old_key_set = set(old_key_to_idx)
    new_key_set = set(new_key_to_idx)
    removed_keys = old_key_set - new_key_set
    added_keys = new_key_set - old_key_set
    kept_keys = old_key_set & new_key_set

    for key in removed_keys:
        old_idx = old_key_to_idx[key]
        Street.objects.filter(campaign=campaign, city_index=old_idx).delete()
        CityFetchJob.objects.filter(campaign=campaign, city_index=old_idx).delete()

    # Renumber kept cities whose index shifted; use a temp offset to avoid
    # unique-constraint conflicts during the two-phase rename.
    moves = [(old_key_to_idx[k], new_key_to_idx[k]) for k in kept_keys
             if old_key_to_idx[k] != new_key_to_idx[k]]
    if moves:
        TEMP_OFFSET = 10000
        for old_idx, _ in moves:
            Street.objects.filter(campaign=campaign, city_index=old_idx).update(city_index=old_idx + TEMP_OFFSET)
            CityFetchJob.objects.filter(campaign=campaign, city_index=old_idx).update(city_index=old_idx + TEMP_OFFSET)
        for old_idx, new_idx in moves:
            Street.objects.filter(campaign=campaign, city_index=old_idx + TEMP_OFFSET).update(city_index=new_idx)
            CityFetchJob.objects.filter(campaign=campaign, city_index=old_idx + TEMP_OFFSET).update(city_index=new_idx)

    if added_keys:
        new_indices = sorted(new_key_to_idx[k] for k in added_keys)
        queue_city_fetches(campaign.pk, city_indices=new_indices)
    elif removed_keys:
        # Cities removed but none added; invalidate cached GeoJSON and sync status.
        Campaign.objects.filter(pk=campaign.pk).update(streets_geojson='')
        if not new_cities:
            Campaign.objects.filter(pk=campaign.pk).update(map_status='pending', map_error='')
        else:
            _sync_campaign_map_status(campaign.pk)


# ── Manager UI views ──────────────────────────────────────────────────────────

@_login_required
def manage_campaign_list(request):
    campaigns = Campaign.objects.exclude(status='deleted').annotate(
        street_count=Count('streets', distinct=True),
        trip_count=Count('trips', distinct=True),
    )
    inflight = campaigns.filter(map_status__in=('pending', 'generating', 'rendering')).order_by('updated_at')
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
            queue_city_fetches(campaign.pk)
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
    geo_limit_json = campaign.geo_limit.geojson if campaign.geo_limit else 'null'
    return render(request, 'campaigns/manage/campaign_detail.html', {
        'campaign': campaign,
        'campaign_url': campaign_url,
        'total_blocks': total_blocks,
        'all_trips': all_trips,
        'city_fetch_jobs': city_fetch_jobs,
        'bbox_json': json.dumps(campaign.bbox),
        'geo_limit_json': geo_limit_json,
    })


@_login_required
@require_GET
def manage_campaign_fetch_status(request, slug):
    """
    Lightweight JSON endpoint polled by the manage detail page while a street
    import is in progress.  Returns the campaign-level map_status plus the
    per-city job table so the page can update in-place without a full reload.
    """
    campaign = get_object_or_404(Campaign, slug=slug)
    city_fetch_jobs = list(campaign.city_fetch_jobs.all())
    blocks_per_city = dict(
        campaign.streets.values('city_index').annotate(c=Count('id')).values_list('city_index', 'c')
    )
    jobs_data = []
    for job in city_fetch_jobs:
        jobs_data.append({
            'city_index': job.city_index,
            'city_name': job.city_name,
            'status': job.status,
            'status_display': job.get_status_display(),
            'block_count': blocks_per_city.get(job.city_index, 0),
            'error': job.error or '',
        })
    return JsonResponse({
        'map_status': campaign.map_status,
        'map_status_display': campaign.get_map_status_display(),
        'total_blocks': campaign.streets.count(),
        'city_fetch_jobs': jobs_data,
    })


@_login_required
def manage_campaign_edit(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    if request.method == 'POST':
        form = CampaignForm(request.POST, instance=campaign)
        if form.is_valid():
            old_cities = campaign.cities
            updated = form.save()
            if updated.cities != old_cities:
                _apply_city_list_changes(old_cities, updated)
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
@require_GET
def manage_campaign_streets_geojson(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)

    if campaign.streets_geojson and not request.GET.get('all'):
        return HttpResponse(campaign.streets_geojson, content_type='application/json')

    streets = campaign.streets.all()

    if not request.GET.get('all'):
        if campaign.geo_limit:
            streets = streets.filter(geometry__intersects=campaign.geo_limit)
        elif campaign.bbox:
            sw, ne = campaign.bbox
            bbox_poly = Polygon.from_bbox((sw[1], sw[0], ne[1], ne[0]))
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


@_login_required
@require_GET
def manage_campaign_coverage_geojson(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)

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
                    'osm_id': street.osm_id,
                },
            })

    return JsonResponse({
        'type': 'FeatureCollection',
        'features': features,
    })


@_login_required
@require_POST
def manage_campaign_update_geo_limit(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    try:
        body = json.loads(request.body)
        coords = body['coordinates']  # GeoJSON polygon coordinate array
        geo_limit = Polygon(coords[0], srid=4326)
        if not geo_limit.valid:
            raise ValueError('invalid polygon')
    except (json.JSONDecodeError, KeyError, ValueError, Exception):
        return HttpResponseBadRequest('Invalid polygon')
    xmin, ymin, xmax, ymax = geo_limit.extent
    bbox = [[ymin, xmin], [ymax, xmax]]
    Campaign.objects.filter(pk=campaign.pk).update(
        geo_limit=geo_limit, bbox=bbox, streets_geojson='', map_status='rendering',
    )
    render_campaign_geojson.delay(campaign.pk, final_status='ready')
    return JsonResponse({'status': 'rendering', 'bbox': bbox})


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


# ---------------------------------------------------------------------------
# JSON API for iOS app
# ---------------------------------------------------------------------------

@require_GET
def api_campaigns(request):
    today = date.today()
    published = Campaign.objects.filter(status='published')
    current = published.filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    ).order_by(F('end_date').asc(nulls_last=True))
    prior = published.filter(end_date__lt=today).order_by('-end_date')
    campaigns = list(current) + list(prior)
    data = [
        {
            'id': c.id,
            'name': c.name,
            'slug': c.slug,
            'start_date': c.start_date.isoformat() if c.start_date else None,
            'end_date': c.end_date.isoformat() if c.end_date else None,
            'hero_image_url': c.hero_image_url or None,
            'map_status': c.map_status,
        }
        for c in campaigns
    ]
    return JsonResponse(data, safe=False)


@require_GET
def api_campaign_detail(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug, status='published')
    return JsonResponse({
        'id': campaign.id,
        'name': campaign.name,
        'slug': campaign.slug,
        'start_date': campaign.start_date.isoformat() if campaign.start_date else None,
        'end_date': campaign.end_date.isoformat() if campaign.end_date else None,
        'instructions': campaign.instructions or '',
        'contact_info': campaign.contact_info or '',
        'hero_image_url': campaign.hero_image_url or None,
        'map_status': campaign.map_status,
        'bbox': campaign.bbox,
    })

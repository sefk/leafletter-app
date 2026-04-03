import io
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

from .forms import CampaignForm, ImageUploadForm
from .models import AddressPoint, Campaign, CampaignImage, CampaignStreet, CityFetchJob, Street, Trip
from .tasks import build_streets_geojson, fetch_city_osm_data, queue_city_fetches, refresh_campaign_address_points, render_campaign_geojson, update_campaign_size_cache, _sync_campaign_map_status, NOMINATIM_URL, NOMINATIM_HEADERS, CITY_TYPES

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
    active = published.filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    current = active.filter(is_test=False).order_by(F('end_date').asc(nulls_last=True))
    current_test = active.filter(is_test=True).order_by(F('end_date').asc(nulls_last=True))
    prior = published.filter(end_date__lt=today, is_test=False).order_by('-end_date')
    return render(request, 'campaigns/campaign_list.html', {
        'current_campaigns': current,
        'current_test_campaigns': current_test,
        'prior_campaigns': prior,
    })


def about(request):
    return render(request, 'campaigns/about.html')


def campaign_detail(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    if campaign.status == 'deleted':
        from django.http import Http404
        raise Http404
    geo_limit_json = campaign.geo_limit.geojson if campaign.geo_limit else 'null'
    is_preview = (campaign.status != 'published')
    return render(request, 'campaigns/campaign_detail.html', {
        'campaign': campaign,
        'bbox_json': json.dumps(campaign.bbox),
        'geo_limit_json': geo_limit_json,
        'is_preview': is_preview,
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

    Worker names and notes are intentionally omitted — they are confidential
    and visible only to campaign managers via the manage interface.
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
                    'recorded_at': trip.recorded_at.isoformat(),
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

    # Validate segments belong to this campaign (via M2M through CampaignStreet)
    streets = campaign.streets.filter(pk__in=segment_ids)
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
    - Removed cities: unlink their streets from the campaign (via M2M) and delete fetch jobs.
      Street objects themselves are NOT deleted — they are shared across campaigns.
    - Kept cities whose index shifted: renumber city_index on CampaignStreet and jobs.
    - New cities: queue fetches only for those.
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
        # Unlink streets from this campaign (do not delete Street objects).
        CampaignStreet.objects.filter(campaign=campaign, city_index=old_idx).delete()
        AddressPoint.objects.filter(campaign=campaign, city_index=old_idx).delete()
        CityFetchJob.objects.filter(campaign=campaign, city_index=old_idx).delete()

    # Renumber kept cities whose index shifted; use a temp offset to avoid
    # unique-constraint conflicts during the two-phase rename.
    moves = [(old_key_to_idx[k], new_key_to_idx[k]) for k in kept_keys
             if old_key_to_idx[k] != new_key_to_idx[k]]
    if moves:
        TEMP_OFFSET = 10000
        for old_idx, _ in moves:
            CampaignStreet.objects.filter(campaign=campaign, city_index=old_idx).update(city_index=old_idx + TEMP_OFFSET)
            AddressPoint.objects.filter(campaign=campaign, city_index=old_idx).update(city_index=old_idx + TEMP_OFFSET)
            CityFetchJob.objects.filter(campaign=campaign, city_index=old_idx).update(city_index=old_idx + TEMP_OFFSET)
        for old_idx, new_idx in moves:
            CampaignStreet.objects.filter(campaign=campaign, city_index=old_idx + TEMP_OFFSET).update(city_index=new_idx)
            AddressPoint.objects.filter(campaign=campaign, city_index=old_idx + TEMP_OFFSET).update(city_index=new_idx)
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
    # Fetch campaigns without any multi-table JOIN annotations — multiple
    # COUNT DISTINCT JOINs in one query cause a row explosion on MySQL.
    campaigns = list(Campaign.objects.exclude(status='deleted').order_by('is_test', '-created_at'))
    campaign_pks = [c.pk for c in campaigns]

    # Bulk counts — each is a separate simple query to avoid JOIN row explosion on MySQL.
    street_counts = dict(
        CampaignStreet.objects.filter(campaign_id__in=campaign_pks)
        .values('campaign_id').annotate(c=Count('id')).values_list('campaign_id', 'c')
    )
    trip_counts = dict(
        Trip.objects.filter(campaign_id__in=campaign_pks, deleted=False)
        .values('campaign_id').annotate(c=Count('id')).values_list('campaign_id', 'c')
    )
    household_counts = dict(
        AddressPoint.objects.filter(campaign_id__in=campaign_pks)
        .values('campaign_id').annotate(c=Count('id')).values_list('campaign_id', 'c')
    )

    # Annotate each campaign object with counts.  size_street_count and
    # size_household_count normally come from pre-cached fields so no
    # per-campaign spatial queries are needed (see cached_size_street_count /
    # cached_size_household_count on Campaign, updated by update_campaign_size_cache).
    #
    # When a campaign has NULL cached counts (not yet populated — e.g. right
    # after the migration that added the fields, or in tests that create streets
    # without going through a task), we fall back to the live spatial query and
    # also persist the result so the next request is fast.
    campaigns_needing_cache = [c for c in campaigns if c.cached_size_street_count is None]
    for c in campaigns_needing_cache:
        update_campaign_size_cache(c.pk)
        # Re-read the freshly written values.
        c.refresh_from_db(fields=['cached_size_street_count', 'cached_size_household_count'])

    for c in campaigns:
        c.street_count = street_counts.get(c.pk, 0)
        c.trip_count = trip_counts.get(c.pk, 0)
        c.household_count = household_counts.get(c.pk, 0)
        c.size_street_count = c.cached_size_street_count
        c.size_household_count = c.cached_size_household_count

    inflight = [c for c in campaigns if c.map_status in ('pending', 'generating', 'rendering')]
    inflight.sort(key=lambda c: c.updated_at)
    return render(request, 'campaigns/manage/campaign_list.html', {
        'campaigns': campaigns,
        'inflight': inflight,
    })


def _resize_hero_image(uploaded_file, max_width=1920, max_height=1080):
    """
    Downscale uploaded_file so it fits within max_width x max_height while
    preserving aspect ratio.  Images already within the limit are returned
    as-is (no re-encoding).  Returns a Django InMemoryUploadedFile.

    Output format:
      - JPEG/JPG  → JPEG (quality 85)
      - WebP      → WebP (quality 85)
      - PNG       → PNG
      - GIF       → PNG (first frame only; GIF animation is not preserved)
    """
    from PIL import Image
    from django.core.files.uploadedfile import InMemoryUploadedFile

    uploaded_file.seek(0)
    try:
        img = Image.open(uploaded_file)
        img.load()
    except Exception:
        # If Pillow can't open it, return the original and let the DB store
        # whatever the user provided.
        uploaded_file.seek(0)
        return uploaded_file

    original_format = (img.format or '').upper()

    # Apply EXIF orientation so the image displays correctly after re-encoding.
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img) or img

    # Convert palette / RGBA modes that don't survive JPEG encoding.
    if img.mode not in ('RGB', 'RGBA', 'L'):
        img = img.convert('RGBA' if img.mode == 'PA' or 'A' in img.mode else 'RGB')

    # Decide output format and content-type.
    ext_map = {'JPEG': ('jpeg', 'image/jpeg'), 'WEBP': ('webp', 'image/webp'), 'PNG': ('png', 'image/png')}
    out_format, content_type = ext_map.get(original_format, ('jpeg', 'image/jpeg'))

    # JPEG does not support transparency — flatten onto white background.
    if out_format == 'jpeg' and img.mode in ('RGBA', 'LA'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif out_format == 'jpeg' and img.mode != 'RGB':
        img = img.convert('RGB')

    w, h = img.size
    if w <= max_width and h <= max_height:
        # Already fits; still re-encode to strip EXIF / reduce weight.
        needs_resize = False
    else:
        needs_resize = True
        ratio = min(max_width / w, max_height / h)
        new_w = max(1, int(w * ratio))
        new_h = max(1, int(h * ratio))
        img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    save_kwargs = {}
    if out_format == 'jpeg':
        save_kwargs = {'quality': 85, 'optimize': True}
    elif out_format == 'webp':
        save_kwargs = {'quality': 85}
    img.save(buf, format=out_format.upper(), **save_kwargs)
    buf.seek(0)

    # Build a new filename with the correct extension.
    import os
    base = os.path.splitext(uploaded_file.name)[0]
    new_name = f'{base}.{ext_map[out_format.upper()][0]}' if out_format.upper() in ext_map else uploaded_file.name

    return InMemoryUploadedFile(
        buf,
        field_name='image',
        name=new_name,
        content_type=content_type,
        size=buf.getbuffer().nbytes,
        charset=None,
    )


def _save_campaign_image(image_form, campaign, user):
    """Create or replace CampaignImage from a validated ImageUploadForm."""
    if campaign.hero_image_url:
        campaign.hero_image_url = ''
        campaign.save(update_fields=['hero_image_url'])
    try:
        existing = campaign.uploaded_image
        existing.image.delete(save=False)
        existing.delete()
    except CampaignImage.DoesNotExist:
        pass
    uploaded_file = image_form.cleaned_data['image']
    resized_file = _resize_hero_image(uploaded_file)
    CampaignImage.objects.create(
        campaign=campaign,
        image=resized_file,
        original_filename=uploaded_file.name,
        content_type=resized_file.content_type or '',
        uploaded_by=user,
    )


@_login_required
def manage_campaign_create(request):
    if request.method == 'POST':
        form = CampaignForm(request.POST)
        image_form = ImageUploadForm(request.POST, request.FILES) if 'image' in request.FILES else None
        campaign_valid = form.is_valid()
        image_valid = image_form is None or image_form.is_valid()
        if campaign_valid and image_valid:
            campaign = form.save(commit=False)
            campaign.status = 'draft'
            campaign.save()
            if image_form:
                _save_campaign_image(image_form, campaign, request.user)
            queue_city_fetches(campaign.pk)
            return redirect('manage_campaign_detail', slug=campaign.slug)
    else:
        form = CampaignForm()
        image_form = None
    return render(request, 'campaigns/manage/campaign_form.html', {
        'form': form, 'image_form': image_form, 'action': 'Create',
    })


@_login_required
def manage_campaign_detail(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    total_blocks = campaign.streets.count()
    all_trips = campaign.trips.prefetch_related('streets').all()
    city_fetch_jobs = list(campaign.city_fetch_jobs.all())
    blocks_per_city = dict(
        campaign.campaign_streets.values('city_index').annotate(c=Count('id')).values_list('city_index', 'c')
    )
    for job in city_fetch_jobs:
        job.block_count = blocks_per_city.get(job.city_index, 0)
    campaign_url = request.build_absolute_uri(f'/c/{campaign.slug}/')
    geo_limit_json = campaign.geo_limit.geojson if campaign.geo_limit else 'null'
    ADDRESS_SPARSE_THRESHOLD = 50  # absolute count below which coverage is considered sparse
    ADDRESS_FETCH_BLOCK_LIMIT = 10_000  # areas larger than this skip address fetch (see #119)
    streets_in_area = campaign.streets.filter(geometry__intersects=campaign.geo_limit).count() if campaign.geo_limit else total_blocks
    total_addresses = campaign.address_points.count()
    total_addresses_sparse = 0 < total_addresses < ADDRESS_SPARSE_THRESHOLD
    estimated_addresses = campaign.estimated_addresses  # filtered by geo_limit if set
    estimated_addresses_sparse = 0 < estimated_addresses < ADDRESS_SPARSE_THRESHOLD
    address_fetch_too_large = streets_in_area > ADDRESS_FETCH_BLOCK_LIMIT
    return render(request, 'campaigns/manage/campaign_detail.html', {
        'campaign': campaign,
        'campaign_url': campaign_url,
        'total_blocks': total_blocks,
        'all_trips': all_trips,
        'city_fetch_jobs': city_fetch_jobs,
        'bbox_json': json.dumps(campaign.bbox),
        'geo_limit_json': geo_limit_json,
        'total_addresses': total_addresses,
        'total_addresses_sparse': total_addresses_sparse,
        'estimated_addresses': estimated_addresses,
        'estimated_addresses_sparse': estimated_addresses_sparse,
        'address_fetch_too_large': address_fetch_too_large,
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
        campaign.campaign_streets.values('city_index').annotate(c=Count('id')).values_list('city_index', 'c')
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
        'total_addresses': campaign.address_points.count(),
        'city_fetch_jobs': jobs_data,
    })


@_login_required
def manage_campaign_edit(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    if request.method == 'POST':
        form = CampaignForm(request.POST, instance=campaign)
        image_form = ImageUploadForm(request.POST, request.FILES) if 'image' in request.FILES else None
        campaign_valid = form.is_valid()
        image_valid = image_form is None or image_form.is_valid()
        if campaign_valid and image_valid:
            old_cities = campaign.cities
            updated = form.save()
            if image_form:
                _save_campaign_image(image_form, updated, request.user)
            if updated.cities != old_cities:
                _apply_city_list_changes(old_cities, updated)
            return redirect('manage_campaign_detail', slug=updated.slug)
    else:
        form = CampaignForm(instance=campaign)
        image_form = None
    campaign_url = request.build_absolute_uri(f'/c/{campaign.slug}/')
    return render(request, 'campaigns/manage/campaign_form.html', {
        'form': form,
        'image_form': image_form,
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
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_campaign_unpublish(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    campaign.status = 'draft'
    campaign.save(update_fields=['status'])
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
    # Unlink streets from this campaign (Street objects persist for re-use).
    CampaignStreet.objects.filter(campaign=campaign, city_index=city_index).delete()
    AddressPoint.objects.filter(campaign=campaign, city_index=city_index).delete()
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
                    'recorded_at': trip.recorded_at.isoformat(),
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
    # Eagerly refresh the size cache so the manage list shows updated counts
    # as soon as the geo_limit is saved, before the async renders complete.
    update_campaign_size_cache(campaign.pk)
    render_campaign_geojson.delay(campaign.pk, final_status='ready')
    refresh_campaign_address_points.delay(campaign.pk)
    estimated_addresses = AddressPoint.objects.filter(
        campaign=campaign, location__within=geo_limit,
    ).count()
    return JsonResponse({'status': 'rendering', 'bbox': bbox, 'estimated_addresses': estimated_addresses})


@_login_required
@require_POST
def manage_campaign_address_preview(request, slug):
    """
    Lightweight endpoint: accepts a GeoJSON polygon body and returns the address
    count within it without saving anything. Used for live preview while drawing.
    """
    campaign = get_object_or_404(Campaign, slug=slug)
    try:
        body = json.loads(request.body)
        coords = body['coordinates']
        poly = Polygon(coords[0], srid=4326)
        if not poly.valid:
            raise ValueError('invalid polygon')
    except (json.JSONDecodeError, KeyError, ValueError, Exception):
        return HttpResponseBadRequest('Invalid polygon')
    count = AddressPoint.objects.filter(campaign=campaign, location__within=poly).count()
    return JsonResponse({'count': count})


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
def cities_prefetched(request):
    """Return display_names of cities that already have streets downloaded.

    We collect display_name values from campaign cities JSON entries whose
    corresponding CityFetchJob completed successfully.  Matching on
    display_name (not just city_name) avoids false positives when multiple
    cities share the same short name (e.g. "Atherton" in CA vs UK vs AU).
    """
    downloaded = set()
    for campaign in Campaign.objects.all():
        cities_list = campaign.cities or []
        ready_names = set(
            CityFetchJob.objects.filter(campaign=campaign, status='ready')
            .values_list('city_name', flat=True)
        )
        for city in cities_list:
            if isinstance(city, dict):
                name = city.get('name', '')
                if name in ready_names:
                    display = city.get('display_name', name)
                    downloaded.add(display)
            elif isinstance(city, str) and city in ready_names:
                downloaded.add(city)
    return JsonResponse({'city_display_names': sorted(downloaded)})


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

def _abs_hero_url(request, campaign):
    """Return an absolute URL for the campaign's hero image, or None."""
    url = campaign.hero_image_effective_url
    if not url:
        return None
    if url.startswith(('http://', 'https://')):
        return url  # Already absolute (e.g. S3)
    return request.build_absolute_uri('/' + url.lstrip('/'))


@require_GET
def api_campaigns(request):
    today = date.today()
    published = Campaign.objects.filter(status='published')
    active = published.filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    current = active.filter(is_test=False).order_by(F('end_date').asc(nulls_last=True))
    current_test = active.filter(is_test=True).order_by(F('end_date').asc(nulls_last=True))
    prior = published.filter(end_date__lt=today, is_test=False).order_by('-end_date')
    campaigns = list(current) + list(prior) + list(current_test)
    data = [
        {
            'id': c.id,
            'name': c.name,
            'slug': c.slug,
            'start_date': c.start_date.isoformat() if c.start_date else None,
            'end_date': c.end_date.isoformat() if c.end_date else None,
            'hero_image_url': _abs_hero_url(request, c),
            'map_status': c.map_status,
            'is_test': c.is_test,
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
        'hero_image_url': _abs_hero_url(request, campaign),
        'map_status': campaign.map_status,
        'bbox': campaign.bbox,
    })


@_login_required
@require_POST
def manage_campaign_remove_image(request, slug):
    campaign = get_object_or_404(Campaign, slug=slug)
    try:
        existing = campaign.uploaded_image
        existing.image.delete(save=False)
        existing.delete()
    except CampaignImage.DoesNotExist:
        pass
    return JsonResponse({'ok': True})

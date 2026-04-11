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
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import CampaignForm, ImageUploadForm
from .models import AddressPoint, Campaign, CampaignImage, CampaignStreet, CityFetchJob, Street, Trip
from .tasks import build_streets_geojson, fetch_city_osm_data, queue_city_fetches, refresh_campaign_address_points, render_campaign_geojson, update_campaign_size_cache, _sync_campaign_map_status, NOMINATIM_URL, NOMINATIM_HEADERS, CITY_TYPES

def _login_required(view):
    """login_required + never_cache — prevents stale CSRF tokens after login."""
    return never_cache(login_required(view, login_url='/manage/login/'))


@never_cache
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
    return render(request, 'campaigns/manage/login.html', {
        'next': next_url,
        'login_error': login_error,
    })


@require_POST
def manage_logout(request):
    logout(request)
    return redirect('/')


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
    if campaign.status in ('deleted', 'draft'):
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
    worker_email = body.get('worker_email', '').strip()
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
        worker_email=worker_email,
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
    return JsonResponse({'trip_id': str(trip.pk), 'worker_name': trip.worker_name, 'worker_email': trip.worker_email, 'notes': trip.notes})


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
    trip.worker_email = body.get('worker_email', trip.worker_email).strip()
    trip.notes = body.get('notes', trip.notes).strip()
    trip.save(update_fields=['worker_name', 'worker_email', 'notes'])
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

    # Also detect "kept" cities that are missing a CityFetchJob (e.g. a city
    # was removed and re-added in the same save, or the original fetch job was
    # lost).  These need to be queued alongside genuinely new cities.
    existing_job_indices = set(
        CityFetchJob.objects.filter(campaign=campaign)
        .values_list('city_index', flat=True)
    )
    orphaned_indices = [
        new_key_to_idx[k] for k in kept_keys
        if new_key_to_idx[k] not in existing_job_indices
    ]

    fetch_indices = sorted(
        [new_key_to_idx[k] for k in added_keys] + orphaned_indices
    )

    if fetch_indices:
        queue_city_fetches(campaign.pk, city_indices=fetch_indices)
    elif removed_keys:
        # Cities removed but none added; invalidate cached GeoJSON and sync status.
        Campaign.objects.filter(pk=campaign.pk).update(streets_geojson='')
        if not new_cities:
            Campaign.objects.filter(pk=campaign.pk).update(map_status='pending', map_error='')
        else:
            _sync_campaign_map_status(campaign.pk)


def _repair_missing_city_jobs(campaign) -> None:
    """Queue fetches for cities that exist in the cities list but have no CityFetchJob."""
    if not campaign.cities:
        return
    existing = set(
        CityFetchJob.objects.filter(campaign=campaign).values_list('city_index', flat=True)
    )
    missing = [i for i in range(len(campaign.cities)) if i not in existing]
    if missing:
        queue_city_fetches(campaign.pk, city_indices=missing)


# ── Manager UI views ──────────────────────────────────────────────────────────

@_login_required
def manage_campaign_list(request):
    from django.contrib.auth import get_user_model
    User = get_user_model()

    is_admin = request.user.is_superuser

    # Admins can filter by user via ?owner=<user_id> or ?owner=all.
    # Non-admins always see only their own campaigns + unowned ones.
    if is_admin:
        owner_param = request.GET.get('owner', str(request.user.pk))
        if owner_param == 'all':
            qs = Campaign.objects.exclude(status='deleted')
        elif owner_param == 'none':
            qs = Campaign.objects.exclude(status='deleted').filter(owner__isnull=True)
        else:
            try:
                filter_user_id = int(owner_param)
            except (ValueError, TypeError):
                filter_user_id = request.user.pk
            qs = Campaign.objects.exclude(status='deleted').filter(
                Q(owner_id=filter_user_id) | Q(owner__isnull=True)
            )
        all_users = list(User.objects.order_by('username'))
    else:
        owner_param = None
        qs = Campaign.objects.exclude(status='deleted').filter(
            Q(owner=request.user) | Q(owner__isnull=True)
        )
        all_users = []

    # Sort params — default: start_date descending.
    # Valid column keys match what we annotate onto each campaign object below.
    VALID_SORT_COLS = {'name', 'status', 'start_date', 'trip_count', 'street_count'}
    sort_col = request.GET.get('sort', 'start_date')
    if sort_col not in VALID_SORT_COLS:
        sort_col = 'start_date'
    sort_dir = request.GET.get('dir', 'desc')
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    # Fetch campaigns without any multi-table JOIN annotations — multiple
    # COUNT DISTINCT JOINs in one query cause a row explosion on MySQL.
    # Pre-sort by is_test so test campaigns stay grouped; secondary sort will
    # be applied in Python after annotated counts are attached (see below).
    campaigns = list(qs.order_by('is_test', '-created_at'))
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

    # Apply requested sort.  None-safe key: None sorts before everything when
    # ascending (i.e. campaigns with no start_date float to the top), which is
    # consistent with the DB NULLs-first behaviour for ascending sorts.
    def _sort_key(c):
        val = getattr(c, sort_col, None)
        # Make None sort consistently (put None/null at end for desc, start for asc).
        return (val is None, val if val is not None else '')

    campaigns.sort(key=_sort_key, reverse=(sort_dir == 'desc'))

    inflight = [c for c in campaigns if c.map_status in ('pending', 'generating', 'rendering') and c.cities]
    inflight.sort(key=lambda c: c.updated_at)

    # Fetch deleted campaigns separately so users can restore them (issue #148).
    if is_admin:
        if owner_param == 'all':
            deleted_qs = Campaign.objects.filter(status='deleted')
        elif owner_param == 'none':
            deleted_qs = Campaign.objects.filter(status='deleted', owner__isnull=True)
        else:
            try:
                filter_user_id_del = int(owner_param)
            except (ValueError, TypeError):
                filter_user_id_del = request.user.pk
            deleted_qs = Campaign.objects.filter(
                status='deleted',
            ).filter(Q(owner_id=filter_user_id_del) | Q(owner__isnull=True))
    else:
        deleted_qs = Campaign.objects.filter(status='deleted').filter(
            Q(owner=request.user) | Q(owner__isnull=True)
        )
    deleted_campaigns = list(deleted_qs.order_by('-updated_at'))

    return render(request, 'campaigns/manage/campaign_list.html', {
        'campaigns': campaigns,
        'inflight': inflight,
        'is_admin': is_admin,
        'all_users': all_users,
        'owner_param': owner_param,
        'deleted_campaigns': deleted_campaigns,
        'sort_col': sort_col,
        'sort_dir': sort_dir,
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
def manage_campaign_quick_create(request):
    """Quick create: GET shows a simple name form, POST creates and redirects to detail."""
    from django.utils.text import slugify as _slugify
    from django.contrib import messages
    if request.method != 'POST':
        return render(request, 'campaigns/manage/campaign_create.html')
    name = request.POST.get('name', '').strip()
    if not name:
        messages.error(request, 'Campaign name is required.')
        return redirect('manage_campaign_list')
    base_slug = _slugify(name) or 'campaign'
    slug = base_slug
    counter = 2
    while Campaign.objects.filter(slug=slug).exists():
        slug = f'{base_slug}-{counter}'
        counter += 1
    campaign = Campaign.objects.create(
        name=name,
        slug=slug,
        cities=[],
        status='draft',
        owner=request.user,
    )
    return redirect('manage_campaign_detail', slug=campaign.slug)


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
            campaign.owner = request.user
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


def _get_step_states(campaign):
    """Derive step completion states for the Phase 1 progress track sidebar."""
    steps = {}

    # Step 1: Basics — name and start_date must be present
    steps['basics'] = 'complete' if (campaign.name and campaign.start_date) else 'current'

    # Step 2: Hero Image — optional step
    steps['hero'] = 'complete' if campaign.hero_image_effective_url else 'optional'

    # Step 3: Cities — derive from city fetch job statuses
    city_jobs = list(campaign.city_fetch_jobs.all())
    if not city_jobs:
        steps['cities'] = 'current'
    elif any(j.status in ('generating', 'pending', 'rendering') for j in city_jobs):
        steps['cities'] = 'in_progress'
    elif any(j.status == 'ready' for j in city_jobs):
        if any(j.status == 'error' for j in city_jobs):
            steps['cities'] = 'attention'
        else:
            steps['cities'] = 'complete'
    elif all(j.status == 'error' for j in city_jobs):
        steps['cities'] = 'attention'
    else:
        steps['cities'] = 'current'

    # Step 4: Boundary
    if campaign.geo_limit:
        steps['boundary'] = 'complete'
    elif campaign.map_status in ('ready', 'warning'):
        steps['boundary'] = 'available'
    else:
        steps['boundary'] = 'blocked'

    # Step 5: Review
    if campaign.geo_limit and campaign.map_status in ('ready', 'warning'):
        steps['review'] = 'complete'
    elif campaign.geo_limit:
        steps['review'] = 'in_progress'
    else:
        steps['review'] = 'blocked'

    # Step 6: Publish
    steps['publish'] = 'complete' if campaign.status == 'published' else 'available'

    return steps


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
    is_admin = request.user.is_superuser
    if is_admin:
        from django.contrib.auth import get_user_model
        all_users = list(get_user_model().objects.order_by('username'))
    else:
        all_users = []
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
        'step_states': _get_step_states(campaign),
        'is_admin': is_admin,
        'all_users': all_users,
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
    """Legacy edit page — now redirects to the detail page (Phase 2 consolidation)."""
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_save_basics(request, slug):
    """
    Section-scoped save for Step 1 (Basics): name, start_date, end_date,
    contact_info, instructions, is_test.  Slug is immutable after creation.
    Uses POST-redirect-GET so a reload never re-submits the form.
    """
    campaign = get_object_or_404(Campaign, slug=slug)

    # Build a limited form that excludes slug and cities_json.
    # We reuse CampaignForm but override __init__ behaviour by passing only
    # the basics fields manually, then saving with update_fields.
    name = request.POST.get('name', '').strip()
    start_date = request.POST.get('start_date', '').strip() or None
    end_date = request.POST.get('end_date', '').strip() or None
    contact_info = request.POST.get('contact_info', '').strip()
    # Quill syncs to the hidden textarea before submit.
    instructions = request.POST.get('instructions', '').replace('&nbsp;', ' ').replace('\u00a0', ' ')
    is_test = bool(request.POST.get('is_test'))

    errors = []
    if not name:
        errors.append('Campaign name is required.')

    if errors:
        # Re-render detail page with validation errors surfaced via session message.
        # For now a simple redirect keeps it straightforward; inline errors are Phase 3+.
        from django.contrib import messages
        for err in errors:
            messages.error(request, err)
        return redirect('manage_campaign_detail', slug=slug)

    campaign.name = name
    campaign.start_date = start_date or None
    campaign.end_date = end_date or None
    campaign.contact_info = contact_info
    campaign.instructions = instructions
    campaign.is_test = is_test
    update_fields = ['name', 'start_date', 'end_date', 'contact_info', 'instructions', 'is_test']

    if request.user.is_superuser:
        owner_value = request.POST.get('owner', '').strip()
        if owner_value == '':
            campaign.owner = None
            update_fields.append('owner')
        else:
            try:
                from django.contrib.auth import get_user_model
                campaign.owner = get_user_model().objects.get(pk=int(owner_value))
                update_fields.append('owner')
            except (ValueError, TypeError, get_user_model().DoesNotExist):
                pass

    campaign.save(update_fields=update_fields)
    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_save_hero(request, slug):
    """
    Section-scoped save for Step 2 (Hero Image): either save a URL or upload a file.
    The two cases are distinguished by the presence of 'image' in request.FILES.
    Uses POST-redirect-GET so a reload never re-submits the form.
    """
    campaign = get_object_or_404(Campaign, slug=slug)

    if 'image' in request.FILES:
        # Upload path: validate and save the uploaded image.
        image_form = ImageUploadForm(request.POST, request.FILES)
        if image_form.is_valid():
            _save_campaign_image(image_form, campaign, request.user)
        else:
            from django.contrib import messages
            for field_errors in image_form.errors.values():
                for err in field_errors:
                    messages.error(request, err)
    else:
        # URL path: save hero_image_url and clear any uploaded image.
        hero_image_url = request.POST.get('hero_image_url', '').strip()
        try:
            existing = campaign.uploaded_image
            existing.image.delete(save=False)
            existing.delete()
        except CampaignImage.DoesNotExist:
            pass
        campaign.hero_image_url = hero_image_url
        campaign.save(update_fields=['hero_image_url'])

    return redirect('manage_campaign_detail', slug=slug)


@_login_required
@require_POST
def manage_save_cities(request, slug):
    """
    Section-scoped save for Step 3 (Cities): add a city from the search widget.
    The city is appended to campaign.cities and a fetch job is queued.
    City removal and per-city refetch/delete are handled by the existing
    manage_city_delete / manage_city_refetch endpoints respectively.
    """
    campaign = get_object_or_404(Campaign, slug=slug)
    import json as _json
    try:
        city_data = _json.loads(request.POST.get('city_json', '{}'))
        if not isinstance(city_data, dict) or 'name' not in city_data or 'osm_id' not in city_data:
            raise ValueError('missing fields')
    except (ValueError, TypeError):
        from django.contrib import messages
        messages.error(request, 'Invalid city data. Please search and select a city from the results.')
        return redirect('manage_campaign_detail', slug=slug)

    old_cities = list(campaign.cities or [])
    # De-duplicate by osm_id.
    if any(c.get('osm_id') == city_data.get('osm_id') for c in old_cities if isinstance(c, dict)):
        return redirect('manage_campaign_detail', slug=slug)

    new_cities = old_cities + [city_data]
    campaign.cities = new_cities
    campaign.save(update_fields=['cities'])
    _apply_city_list_changes(old_cities, campaign)
    return redirect('manage_campaign_detail', slug=slug)


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
def manage_campaign_restore(request, slug):
    """Restore a soft-deleted campaign back to draft status.

    This intentionally does NOT call queue_city_fetches — all related data
    (trips, streets, geo_limit, hero image, etc.) is preserved by the
    soft-delete and needs no re-fetch on restore.
    """
    campaign = get_object_or_404(Campaign, slug=slug)
    if campaign.status != 'deleted':
        return redirect('manage_campaign_detail', slug=slug)
    campaign.status = 'draft'
    campaign.save(update_fields=['status'])
    return redirect('manage_campaign_detail', slug=slug)


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
def manage_export_trips(request, slug):
    """Export all non-deleted trips for a campaign as a CSV download."""
    import csv
    campaign = get_object_or_404(Campaign, slug=slug)
    trips = (
        campaign.trips
        .filter(deleted=False)
        .prefetch_related('streets')
        .order_by('recorded_at')
    )

    response = HttpResponse(content_type='text/csv')
    filename = f"trips-{campaign.slug}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(['trip_entry_gmt', 'worker_name', 'worker_email', 'notes', 'blocks', 'streets', 'cities'])
    for trip in trips:
        streets = trip.streets.all()
        street_names = sorted(set(s.name for s in streets if s.name))
        city_names = sorted(set(s.city_name for s in streets if s.city_name))
        writer.writerow([
            trip.recorded_at.strftime('%Y-%m-%d %H:%M:%S'),
            trip.worker_name,
            trip.worker_email,
            trip.notes,
            streets.count(),
            ', '.join(street_names),
            ', '.join(city_names),
        ])

    return response


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

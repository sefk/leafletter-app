"""
Tests for the campaigns app.

Run with:
    python manage.py test campaigns
"""
import json
import subprocess
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core import mail

import requests

from django.contrib import admin as django_admin
from django.contrib.auth.models import User
from django.contrib.gis.geos import LineString
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from .admin import CampaignAdmin, MAP_STATUS_COLORS
from .models import Campaign, CampaignStreet, CityFetchJob, Street, Trip
from .tasks import (fetch_city_osm_data, fetch_osm_segments, find_intersection_nodes,
                    lookup_city, queue_city_fetches, query_overpass, query_overpass_addresses,
                    render_campaign_geojson,
                    split_way_at_intersections, _sync_campaign_map_status,
                    _write_streets_geojson_chunked,
                    watchdog_stuck_jobs, STUCK_JOB_THRESHOLD_MINUTES,
                    MAX_CAMPAIGN_BLOCKS,
                    backup_database, _run_backup, _prune_old_backups)
from .forms import ImageUploadForm
from .views import _apply_city_list_changes, _resize_hero_image

# ── Shared test geometry ──────────────────────────────────────────────────────

GEOM = LineString((-122.1, 37.4), (-122.15, 37.45), srid=4326)
GEOM2 = LineString((-122.2, 37.5), (-122.25, 37.55), srid=4326)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_campaign(slug='test-campaign', status='published', **kwargs):
    defaults = dict(
        name='Test Campaign',
        slug=slug,
        cities=['Palo Alto'],
        status=status,
    )
    defaults.update(kwargs)
    return Campaign.objects.create(**defaults)


def make_street(campaign, osm_id=1001, name='Main St', geometry=None, block_index=0,
                city_name=None, city_index=None):
    """
    Create a Street and link it to the campaign via CampaignStreet.
    city_name defaults to 'test-city-<campaign.slug>' if not provided.
    """
    if city_name is None:
        city_name = f'test-city-{campaign.slug}'
    street, _ = Street.objects.get_or_create(
        city_name=city_name,
        osm_id=osm_id,
        block_index=block_index,
        defaults={'name': name, 'geometry': geometry or GEOM},
    )
    CampaignStreet.objects.get_or_create(
        campaign=campaign, street=street,
        defaults={'city_index': city_index},
    )
    return street


def make_trip(campaign, streets=None, worker_name='Alice'):
    trip = Trip.objects.create(campaign=campaign, worker_name=worker_name)
    if streets:
        trip.streets.set(streets)
    return trip


def make_admin_request(method='get'):
    """Build a lightweight request suitable for calling admin methods directly."""
    factory = RequestFactory()
    request = getattr(factory, method)('/')
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


# ── Model tests ───────────────────────────────────────────────────────────────

class CampaignModelTest(TestCase):

    def test_str_returns_name(self):
        c = Campaign(name='My Campaign')
        self.assertEqual(str(c), 'My Campaign')

    def test_default_status_is_draft(self):
        c = Campaign.objects.create(name='Draft', slug='draft-x', cities=['x'])
        self.assertEqual(c.status, 'draft')

    def test_default_map_status_is_pending(self):
        c = Campaign.objects.create(name='Draft', slug='draft-y', cities=['x'])
        self.assertEqual(c.map_status, 'pending')

    def test_slug_is_unique(self):
        make_campaign(slug='unique-slug')
        with self.assertRaises(Exception):
            make_campaign(slug='unique-slug')


class StreetModelTest(TestCase):

    def setUp(self):
        self.campaign = make_campaign()

    def test_str_with_name(self):
        s = Street(city_name='Springfield', osm_id=123, name='Oak Ave', geometry=GEOM)
        self.assertEqual(str(s), 'Oak Ave (123 block 0)')

    def test_str_without_name(self):
        s = Street(city_name='Springfield', osm_id=456, name='', geometry=GEOM)
        self.assertEqual(str(s), 'Unnamed (456 block 0)')

    def test_unique_together_city_name_osm_id_block_index(self):
        # Same city_name + osm_id + block_index must be unique
        Street.objects.create(city_name='CityA', osm_id=999, block_index=0, geometry=GEOM)
        with self.assertRaises(IntegrityError):
            Street.objects.create(city_name='CityA', osm_id=999, block_index=0, geometry=GEOM)

    def test_multiple_blocks_same_osm_id_allowed(self):
        # Different block_index for same city+osm_id should not raise
        Street.objects.create(city_name='CityA', osm_id=999, block_index=0, geometry=GEOM)
        Street.objects.create(city_name='CityA', osm_id=999, block_index=1, geometry=GEOM2)

    def test_same_osm_id_allowed_in_different_cities(self):
        # Same osm_id in different city_name must be allowed
        Street.objects.create(city_name='CityA', osm_id=777, block_index=0, geometry=GEOM)
        # Should not raise:
        Street.objects.create(city_name='CityB', osm_id=777, block_index=0, geometry=GEOM)


class TripModelTest(TestCase):

    def setUp(self):
        self.campaign = make_campaign()

    def test_str_with_worker_name(self):
        trip = Trip.objects.create(campaign=self.campaign, worker_name='Bob')
        self.assertIn('Bob', str(trip))

    def test_str_without_worker_name(self):
        trip = Trip.objects.create(campaign=self.campaign, worker_name='')
        self.assertIn('Anonymous', str(trip))

    def test_primary_key_is_uuid(self):
        trip = Trip.objects.create(campaign=self.campaign)
        self.assertIsInstance(trip.pk, uuid.UUID)

    def test_streets_many_to_many(self):
        street = make_street(self.campaign, osm_id=10)
        trip = make_trip(self.campaign, streets=[street])
        self.assertIn(street, trip.streets.all())


# ── View tests: campaign_detail ───────────────────────────────────────────────

class CampaignDetailViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.campaign = make_campaign(slug='detail-camp')

    def test_published_campaign_returns_200(self):
        resp = self.client.get('/c/detail-camp/')
        self.assertEqual(resp.status_code, 200)

    def test_page_contains_campaign_name(self):
        resp = self.client.get('/c/detail-camp/')
        self.assertContains(resp, 'Test Campaign')

    def test_draft_campaign_returns_200(self):
        make_campaign(slug='draft-v', status='draft')
        resp = self.client.get('/c/draft-v/')
        self.assertEqual(resp.status_code, 200)

    def test_draft_campaign_shows_unpublished_modal_to_anonymous(self):
        make_campaign(slug='draft-modal', status='draft')
        resp = self.client.get('/c/draft-modal/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Not Published Yet')

    def test_draft_campaign_shows_preview_banner_to_signed_in_user(self):
        user = User.objects.create_user(username='previewuser', password='pw')
        self.client.force_login(user)
        make_campaign(slug='draft-banner', status='draft')
        resp = self.client.get('/c/draft-banner/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'preview-banner')
        self.assertNotContains(resp, 'Not Published Yet')

    def test_deleted_campaign_returns_404(self):
        make_campaign(slug='del-v', status='deleted')
        resp = self.client.get('/c/del-v/')
        self.assertEqual(resp.status_code, 404)

    def test_unknown_slug_returns_404(self):
        resp = self.client.get('/c/does-not-exist/')
        self.assertEqual(resp.status_code, 404)


# ── Access control tests ──────────────────────────────────────────────────────

class PublicAccessTest(TestCase):
    """Public endpoints must be reachable without authentication."""

    def setUp(self):
        self.client = Client()
        self.campaign = make_campaign(slug='public-camp')

    # Root
    def test_root_returns_200_unauthenticated(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)

    def test_root_shows_published_campaign(self):
        resp = self.client.get('/')
        self.assertContains(resp, self.campaign.name)

    def test_root_does_not_show_draft(self):
        make_campaign(slug='draft-pub', status='draft')
        resp = self.client.get('/')
        self.assertNotContains(resp, 'draft-pub')

    # Worker campaign page
    def test_campaign_detail_returns_200_unauthenticated(self):
        resp = self.client.get(f'/c/{self.campaign.slug}/')
        self.assertEqual(resp.status_code, 200)

    def test_campaign_streets_geojson_returns_200_unauthenticated(self):
        resp = self.client.get(f'/c/{self.campaign.slug}/streets.geojson')
        self.assertEqual(resp.status_code, 200)

    def test_campaign_coverage_geojson_returns_200_unauthenticated(self):
        resp = self.client.get(f'/c/{self.campaign.slug}/coverage.geojson')
        self.assertEqual(resp.status_code, 200)


class AuthGatingTest(TestCase):
    """Manage and admin endpoints must redirect unauthenticated users to login."""

    def setUp(self):
        self.client = Client()
        self.campaign = make_campaign(slug='auth-camp', status='draft')

    def _assert_redirects_to_login(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/login/', resp['Location'])

    def test_manage_list_requires_login(self):
        self._assert_redirects_to_login('/manage/')

    def test_manage_detail_requires_login(self):
        self._assert_redirects_to_login(f'/manage/{self.campaign.slug}/')

    def test_manage_new_requires_login(self):
        self._assert_redirects_to_login('/manage/new/')

    def test_manage_edit_requires_login(self):
        self._assert_redirects_to_login(f'/manage/{self.campaign.slug}/edit/')

    def test_admin_requires_login(self):
        resp = self.client.get('/admin/')
        self.assertEqual(resp.status_code, 302)


class ManageCampaignListAnnotationsTest(TestCase):
    """manage_campaign_list annotates downloaded counts and campaign size (geo_limit boundary)."""

    def setUp(self):
        self.user = User.objects.create_user('mgr', password='pw')
        self.client = Client()
        self.client.login(username='mgr', password='pw')
        self.campaign = make_campaign(slug='list-annot-camp')
        self.s1 = make_street(self.campaign, osm_id=1, name='S1', city_index=0)
        self.s2 = make_street(self.campaign, osm_id=2, name='S2', block_index=1, city_index=0)
        self.s3 = make_street(self.campaign, osm_id=3, name='S3', block_index=2, city_index=1)

    def _get_campaign_from_response(self):
        resp = self.client.get('/manage/')
        self.assertEqual(resp.status_code, 200)
        return next(c for c in resp.context['campaigns'] if c.slug == 'list-annot-camp')

    def test_street_count_matches_downloaded_streets(self):
        c = self._get_campaign_from_response()
        self.assertEqual(c.street_count, 3)

    def test_size_street_count_equals_all_streets_when_no_geo_limit(self):
        c = self._get_campaign_from_response()
        self.assertEqual(c.size_street_count, 3)

    def test_size_street_count_filters_by_geo_limit(self):
        from django.contrib.gis.geos import Polygon
        # geo_limit that contains GEOM (bbox around -122.1...-122.15, 37.4...37.45)
        geo_limit = Polygon.from_bbox((-122.2, 37.3, -122.0, 37.5))
        self.campaign.geo_limit = geo_limit
        self.campaign.save()
        c = self._get_campaign_from_response()
        self.assertEqual(c.size_street_count, 3)

    def test_size_street_count_excludes_streets_outside_geo_limit(self):
        from django.contrib.gis.geos import Polygon
        # geo_limit far away from GEOM — no streets inside
        geo_limit = Polygon.from_bbox((0, 0, 1, 1))
        self.campaign.geo_limit = geo_limit
        self.campaign.save()
        c = self._get_campaign_from_response()
        self.assertEqual(c.size_street_count, 0)


# ── View tests: streets.geojson ───────────────────────────────────────────────

class StreetsGeoJSONViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.campaign = make_campaign(slug='geo-camp')

    def test_returns_200_and_feature_collection(self):
        resp = self.client.get('/c/geo-camp/streets.geojson')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['type'], 'FeatureCollection')

    def test_empty_campaign_returns_no_features(self):
        data = self.client.get('/c/geo-camp/streets.geojson').json()
        self.assertEqual(data['features'], [])

    def test_feature_count_matches_streets(self):
        make_street(self.campaign, osm_id=1)
        make_street(self.campaign, osm_id=2)
        data = self.client.get('/c/geo-camp/streets.geojson').json()
        self.assertEqual(len(data['features']), 2)

    def test_feature_properties_contain_osm_id_and_name(self):
        make_street(self.campaign, osm_id=42, name='Elm St')
        feature = self.client.get('/c/geo-camp/streets.geojson').json()['features'][0]
        self.assertEqual(feature['properties']['osm_id'], 42)
        self.assertEqual(feature['properties']['name'], 'Elm St')

    def test_feature_geometry_is_linestring(self):
        make_street(self.campaign, osm_id=1)
        feature = self.client.get('/c/geo-camp/streets.geojson').json()['features'][0]
        self.assertEqual(feature['geometry']['type'], 'LineString')

    def test_draft_campaign_returns_404(self):
        make_campaign(slug='draft-geo', status='draft')
        resp = self.client.get('/c/draft-geo/streets.geojson')
        self.assertEqual(resp.status_code, 404)

    def test_post_not_allowed(self):
        resp = self.client.post('/c/geo-camp/streets.geojson')
        self.assertEqual(resp.status_code, 405)


# ── View tests: coverage.geojson ─────────────────────────────────────────────

class CoverageGeoJSONViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.campaign = make_campaign(slug='cov-camp')
        self.street1 = make_street(self.campaign, osm_id=1, name='A St')
        self.street2 = make_street(self.campaign, osm_id=2, name='B St')

    def test_no_trips_returns_empty_features(self):
        data = self.client.get('/c/cov-camp/coverage.geojson').json()
        self.assertEqual(data['features'], [])

    def test_returns_covered_street_after_trip(self):
        make_trip(self.campaign, streets=[self.street1])
        data = self.client.get('/c/cov-camp/coverage.geojson').json()
        self.assertEqual(len(data['features']), 1)
        self.assertEqual(data['features'][0]['properties']['osm_id'], 1)

    def test_uncovered_street_not_in_coverage(self):
        make_trip(self.campaign, streets=[self.street1])
        osm_ids = [f['properties']['osm_id'] for f in
                   self.client.get('/c/cov-camp/coverage.geojson').json()['features']]
        self.assertNotIn(2, osm_ids)

    def test_accumulates_streets_across_multiple_trips(self):
        make_trip(self.campaign, streets=[self.street1], worker_name='Alice')
        make_trip(self.campaign, streets=[self.street2], worker_name='Bob')
        data = self.client.get('/c/cov-camp/coverage.geojson').json()
        self.assertEqual(len(data['features']), 2)
        osm_ids = {f['properties']['osm_id'] for f in data['features']}
        self.assertEqual(osm_ids, {1, 2})

    def test_street_covered_by_multiple_trips_appears_once_per_trip(self):
        make_trip(self.campaign, streets=[self.street1], worker_name='Alice')
        make_trip(self.campaign, streets=[self.street1], worker_name='Bob')
        data = self.client.get('/c/cov-camp/coverage.geojson').json()
        self.assertEqual(len(data['features']), 2)

    def test_only_returns_streets_from_this_campaign(self):
        other = make_campaign(slug='other-cov')
        other_street = make_street(other, osm_id=99)
        make_trip(other, streets=[other_street])
        data = self.client.get('/c/cov-camp/coverage.geojson').json()
        self.assertEqual(data['features'], [])


# ── View tests: log_trip ──────────────────────────────────────────────────────

class LogTripViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.campaign = make_campaign(slug='trip-camp')
        self.street = make_street(self.campaign, osm_id=10)

    def _post(self, data, slug='trip-camp'):
        return self.client.post(
            f'/c/{slug}/trip/',
            data=json.dumps(data),
            content_type='application/json',
        )

    def test_valid_trip_returns_200_with_trip_id(self):
        resp = self._post({'segment_ids': [self.street.pk], 'worker_name': 'Alice'})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['status'], 'ok')
        self.assertIn('trip_id', body)

    def test_valid_trip_creates_trip_in_db(self):
        self._post({'segment_ids': [self.street.pk], 'worker_name': 'Alice'})
        self.assertEqual(Trip.objects.filter(campaign=self.campaign).count(), 1)

    def test_valid_trip_associates_streets(self):
        self._post({'segment_ids': [self.street.pk]})
        trip = Trip.objects.get(campaign=self.campaign)
        self.assertIn(self.street, trip.streets.all())

    def test_multiple_streets_in_one_trip(self):
        street2 = make_street(self.campaign, osm_id=11, geometry=GEOM2)
        self._post({'segment_ids': [self.street.pk, street2.pk]})
        trip = Trip.objects.get(campaign=self.campaign)
        self.assertEqual(trip.streets.count(), 2)

    def test_worker_name_and_notes_saved(self):
        self._post({'segment_ids': [self.street.pk], 'worker_name': 'Bob', 'notes': 'Cold day'})
        trip = Trip.objects.get(campaign=self.campaign)
        self.assertEqual(trip.worker_name, 'Bob')
        self.assertEqual(trip.notes, 'Cold day')

    def test_worker_name_is_stripped(self):
        self._post({'segment_ids': [self.street.pk], 'worker_name': '  Alice  '})
        trip = Trip.objects.get(campaign=self.campaign)
        self.assertEqual(trip.worker_name, 'Alice')

    def test_empty_segment_ids_returns_400(self):
        resp = self._post({'segment_ids': []})
        self.assertEqual(resp.status_code, 400)

    def test_missing_segment_ids_returns_400(self):
        resp = self._post({'worker_name': 'Alice'})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        resp = self.client.post('/c/trip-camp/trip/', data='not-json',
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_segments_from_other_campaign_returns_400(self):
        other = make_campaign(slug='other-trip')
        other_street = make_street(other, osm_id=999)
        resp = self._post({'segment_ids': [other_street.pk]})
        self.assertEqual(resp.status_code, 400)

    def test_nonexistent_segment_ids_returns_400(self):
        resp = self._post({'segment_ids': [99999]})
        self.assertEqual(resp.status_code, 400)

    def test_draft_campaign_returns_404(self):
        make_campaign(slug='draft-trip', status='draft')
        resp = self._post({'segment_ids': [self.street.pk]}, slug='draft-trip')
        self.assertEqual(resp.status_code, 404)

    def test_get_not_allowed(self):
        resp = self.client.get('/c/trip-camp/trip/')
        self.assertEqual(resp.status_code, 405)

    def test_partial_ids_only_saves_valid_streets(self):
        """Mix of valid + nonexistent IDs: trip is created with only valid streets."""
        resp = self._post({'segment_ids': [self.street.pk, 99999]})
        self.assertEqual(resp.status_code, 200)
        trip = Trip.objects.get(campaign=self.campaign)
        self.assertEqual(trip.streets.count(), 1)


# ── Task tests: query_overpass ────────────────────────────────────────────────

OVERPASS_RESPONSE = {
    'elements': [
        {
            'id': 111,
            'tags': {'highway': 'residential', 'name': 'Oak Ave'},
            'nodes': [1001, 1002],
            'geometry': [{'lon': -122.1, 'lat': 37.4}, {'lon': -122.2, 'lat': 37.5}],
        },
        {
            'id': 222,
            'tags': {'highway': 'footway'},     # excluded type
            'nodes': [1002, 1003],
            'geometry': [{'lon': -122.1, 'lat': 37.4}, {'lon': -122.2, 'lat': 37.5}],
        },
        {
            'id': 333,
            'tags': {'highway': 'primary'},
            'nodes': [1004],
            'geometry': [{'lon': -122.1, 'lat': 37.4}],  # only 1 point — skip
        },
        {
            'id': 444,
            'tags': {'highway': 'primary', 'name': 'Main St'},
            'nodes': [1005, 1006],
            'geometry': [{'lon': -122.0, 'lat': 37.3}, {'lon': -122.05, 'lat': 37.35}],
        },
    ]
}


def _make_overpass_response(data=None, status_code=200):
    mock_resp = MagicMock()
    mock_resp.json.return_value = data or OVERPASS_RESPONSE
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = Exception(f'HTTP {status_code}')
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


class QueryOverpassTest(TestCase):

    @patch('campaigns.tasks.requests.post')
    def test_included_highway_types_are_returned(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        ways = query_overpass('Palo Alto')
        ids = [w['osm_id'] for w in ways]
        self.assertIn(111, ids)   # residential
        self.assertIn(444, ids)   # primary

    @patch('campaigns.tasks.requests.post')
    def test_excluded_highway_type_is_filtered(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        ways = query_overpass('Palo Alto')
        ids = [w['osm_id'] for w in ways]
        self.assertNotIn(222, ids)   # footway

    @patch('campaigns.tasks.requests.post')
    def test_way_with_single_point_is_skipped(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        ways = query_overpass('Palo Alto')
        ids = [w['osm_id'] for w in ways]
        self.assertNotIn(333, ids)

    @patch('campaigns.tasks.requests.post')
    def test_coords_are_lon_lat_tuples(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        ways = query_overpass('Palo Alto')
        oak = next(w for w in ways if w['osm_id'] == 111)
        self.assertEqual(oak['coords'][0], (-122.1, 37.4))
        self.assertEqual(oak['coords'][1], (-122.2, 37.5))

    @patch('campaigns.tasks.requests.post')
    def test_name_extracted_from_tags(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        ways = query_overpass('Palo Alto')
        main = next(w for w in ways if w['osm_id'] == 444)
        self.assertEqual(main['name'], 'Main St')

    @patch('campaigns.tasks.requests.post')
    def test_missing_name_tag_returns_empty_string(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        ways = query_overpass('Palo Alto')
        # osm_id 222 is filtered; osm_id 333 is filtered; osm_id 111 has name
        # Let's use a response with no name tag
        no_name_data = {'elements': [
            {'id': 1, 'tags': {'highway': 'residential'},
             'geometry': [{'lon': -1.0, 'lat': 1.0}, {'lon': -2.0, 'lat': 2.0}]},
        ]}
        mock_post.return_value = _make_overpass_response(data=no_name_data)
        ways = query_overpass('Anywhere')
        self.assertEqual(ways[0]['name'], '')

    @patch('campaigns.tasks.requests.post')
    def test_raises_on_http_error(self, mock_post):
        mock_post.return_value = _make_overpass_response(status_code=503)
        with self.assertRaises(Exception):
            query_overpass('BadCity')

    @patch('campaigns.tasks.requests.post')
    def test_raises_on_network_error(self, mock_post):
        mock_post.side_effect = ConnectionError('network down')
        with self.assertRaises(Exception):
            query_overpass('BadCity')

    @patch('campaigns.tasks.requests.post')
    def test_node_ids_extracted(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        ways = query_overpass('Palo Alto')
        oak = next(w for w in ways if w['osm_id'] == 111)
        self.assertEqual(oak['node_ids'], [1001, 1002])


# ── Task tests: query_overpass_addresses ─────────────────────────────────────

ADDRESS_RESPONSE = {
    'elements': [
        {'type': 'node', 'id': 1, 'lon': -122.1, 'lat': 37.4},
        {'type': 'way', 'id': 2, 'center': {'lon': -122.2, 'lat': 37.5}},
        {'type': 'way', 'id': 3},  # way with no center — should be skipped
    ]
}


class QueryOverpassAddressesTest(TestCase):

    @patch('campaigns.tasks.requests.post')
    def test_returns_node_and_way_center_coords(self, mock_post):
        mock_post.return_value = _make_overpass_response(data=ADDRESS_RESPONSE)
        points = query_overpass_addresses('Palo Alto')
        self.assertEqual(len(points), 2)
        self.assertIn((-122.1, 37.4), points)
        self.assertIn((-122.2, 37.5), points)

    @patch('campaigns.tasks.requests.post')
    def test_way_without_center_is_skipped(self, mock_post):
        mock_post.return_value = _make_overpass_response(data=ADDRESS_RESPONSE)
        points = query_overpass_addresses('Palo Alto')
        self.assertEqual(len(points), 2)  # 3rd element has no center

    @patch('campaigns.tasks.requests.post')
    def test_bbox_uses_bbox_filter_in_query(self, mock_post):
        mock_post.return_value = _make_overpass_response(data={'elements': []})
        query_overpass_addresses('San Mateo County', bbox=(-122.5, 37.3, -121.9, 37.7))
        call_data = mock_post.call_args[1]['data']['data']
        # Overpass bbox syntax: (south,west,north,east)
        self.assertIn('(37.3,-122.5,37.7,-121.9)', call_data)
        # Should not reference area filter
        self.assertNotIn('area.searchArea', call_data)

    @patch('campaigns.tasks.requests.post')
    def test_no_bbox_uses_area_filter(self, mock_post):
        mock_post.return_value = _make_overpass_response(data={'elements': []})
        query_overpass_addresses('Palo Alto')
        call_data = mock_post.call_args[1]['data']['data']
        self.assertIn('area.searchArea', call_data)

    @patch('campaigns.tasks.requests.post')
    def test_raises_on_http_error(self, mock_post):
        mock_post.return_value = _make_overpass_response(status_code=504)
        with self.assertRaises(Exception):
            query_overpass_addresses('Anywhere')


# ── Task tests: find_intersection_nodes ───────────────────────────────────────

class FindIntersectionNodesTest(TestCase):

    def test_shared_node_is_intersection(self):
        ways = [{'node_ids': [1, 2, 3]}, {'node_ids': [3, 4, 5]}]
        self.assertIn(3, find_intersection_nodes(ways))

    def test_unshared_nodes_not_intersection(self):
        ways = [{'node_ids': [1, 2, 3]}, {'node_ids': [4, 5, 6]}]
        self.assertEqual(find_intersection_nodes(ways), set())

    def test_loop_road_not_self_intersection(self):
        ways = [{'node_ids': [1, 2, 3, 1]}]
        self.assertEqual(find_intersection_nodes(ways), set())

    def test_three_way_intersection(self):
        ways = [
            {'node_ids': [1, 2, 3]},
            {'node_ids': [4, 2, 5]},
            {'node_ids': [6, 2, 7]},
        ]
        self.assertEqual(find_intersection_nodes(ways), {2})

    def test_empty_ways(self):
        self.assertEqual(find_intersection_nodes([]), set())


# ── Task tests: split_way_at_intersections ────────────────────────────────────

class SplitWayAtIntersectionsTest(TestCase):

    def _way(self, node_ids, coords=None):
        if coords is None:
            coords = [(float(-122 - i * 0.01), float(37 + i * 0.01)) for i in range(len(node_ids))]
        return {'node_ids': node_ids, 'coords': coords}

    def test_no_intersections_returns_single_segment(self):
        way = self._way([1, 2, 3])
        blocks = split_way_at_intersections(way, set())
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['block_index'], 0)
        self.assertEqual(len(blocks[0]['coords']), 3)

    def test_midpoint_intersection_splits_into_two(self):
        way = self._way([1, 2, 3])
        blocks = split_way_at_intersections(way, {2})
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]['start_node_id'], 1)
        self.assertEqual(blocks[0]['end_node_id'], 2)
        self.assertEqual(blocks[1]['start_node_id'], 2)
        self.assertEqual(blocks[1]['end_node_id'], 3)
        self.assertEqual([b['block_index'] for b in blocks], [0, 1])

    def test_two_intersections_split_into_three(self):
        way = self._way([1, 2, 3, 4, 5])
        blocks = split_way_at_intersections(way, {2, 4})
        self.assertEqual(len(blocks), 3)
        self.assertEqual([b['block_index'] for b in blocks], [0, 1, 2])

    def test_endpoint_only_intersections_give_single_block(self):
        way = self._way([1, 2, 3])
        blocks = split_way_at_intersections(way, {1, 3})
        self.assertEqual(len(blocks), 1)

    def test_adjacent_blocks_share_endpoint_coord(self):
        coords = [(-122.1, 37.4), (-122.15, 37.45), (-122.2, 37.5)]
        way = {'node_ids': [1, 2, 3], 'coords': coords}
        blocks = split_way_at_intersections(way, {2})
        self.assertEqual(blocks[0]['coords'][-1], (-122.15, 37.45))
        self.assertEqual(blocks[1]['coords'][0], (-122.15, 37.45))

    def test_missing_node_ids_fallback_to_single_segment(self):
        way = {'node_ids': [], 'coords': [(-122.1, 37.4), (-122.2, 37.5)]}
        blocks = split_way_at_intersections(way, {99})
        self.assertEqual(len(blocks), 1)
        self.assertIsNone(blocks[0]['start_node_id'])


# ── Task tests: fetch_osm_segments ────────────────────────────────────────────

class FetchOSMSegmentsTaskTest(TestCase):

    def setUp(self):
        self.campaign = make_campaign(slug='task-camp')
        self.lookup_patcher = patch('campaigns.tasks.lookup_city')
        self.mock_lookup = self.lookup_patcher.start()

    def tearDown(self):
        self.lookup_patcher.stop()

    @patch('campaigns.tasks.query_overpass')
    def test_sets_map_status_to_generating_then_ready(self, mock_qo):
        statuses = []

        def capture_status(city):
            self.campaign.refresh_from_db()
            statuses.append(self.campaign.map_status)
            return [{'osm_id': 1, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]}]

        mock_qo.side_effect = capture_status
        fetch_osm_segments(self.campaign.pk)

        self.assertIn('generating', statuses)
        self.campaign.refresh_from_db()
        # After fetch_osm_segments completes, status is 'ready'.
        # render_campaign_geojson is NOT dispatched here; it fires only after
        # the manager draws a geo_limit boundary.
        self.assertEqual(self.campaign.map_status, 'ready')

    @patch('campaigns.tasks.query_overpass')
    def test_sets_map_status_error_on_failure(self, mock_qo):
        mock_qo.side_effect = RuntimeError('Overpass down')
        fetch_osm_segments(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')

    @patch('campaigns.tasks.query_overpass')
    def test_creates_streets_in_db(self, mock_qo):
        mock_qo.return_value = [
            {'osm_id': 10, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
            {'osm_id': 20, 'name': 'B St', 'coords': [(-122.3, 37.6), (-122.4, 37.7)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        self.assertEqual(self.campaign.streets.count(), 2)
        names = set(self.campaign.streets.values_list('name', flat=True))
        self.assertEqual(names, {'A St', 'B St'})

    @patch('campaigns.tasks.query_overpass')
    def test_update_or_create_prevents_duplicate_streets_on_rerun(self, mock_qo):
        mock_qo.return_value = [
            {'osm_id': 10, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        fetch_osm_segments(self.campaign.pk)
        self.assertEqual(self.campaign.streets.count(), 1)

    @patch('campaigns.tasks.query_overpass')
    def test_iterates_all_cities(self, mock_qo):
        self.campaign.cities = ['City A', 'City B']
        self.campaign.save(update_fields=['cities'])
        mock_qo.return_value = [
            {'osm_id': 1, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        self.assertEqual(mock_qo.call_count, 2)
        mock_qo.assert_any_call('City A')
        mock_qo.assert_any_call('City B')

    def test_missing_campaign_pk_does_not_raise(self):
        fetch_osm_segments(99999)   # should return silently

    @patch('campaigns.tasks.query_overpass')
    def test_intersecting_ways_split_into_blocks(self, mock_qo):
        """A 3-node way sharing a node with another way is split into 2 blocks."""
        mock_qo.return_value = [
            {'osm_id': 10, 'name': 'A St', 'node_ids': [1, 2, 3],
             'coords': [(-122.1, 37.4), (-122.15, 37.45), (-122.2, 37.5)]},
            {'osm_id': 20, 'name': 'B St', 'node_ids': [2, 4],
             'coords': [(-122.15, 37.45), (-122.25, 37.55)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        # A St: 3 nodes, split at shared node 2 → 2 blocks; B St: 2 nodes → 1 block
        self.assertEqual(self.campaign.streets.count(), 3)
        a_blocks = self.campaign.streets.filter(osm_id=10).order_by('block_index')
        self.assertEqual(a_blocks.count(), 2)
        self.assertEqual(a_blocks[0].start_node_id, 1)
        self.assertEqual(a_blocks[0].end_node_id, 2)
        self.assertEqual(a_blocks[1].start_node_id, 2)
        self.assertEqual(a_blocks[1].end_node_id, 3)

    @patch('campaigns.tasks.query_overpass')
    def test_non_intersecting_way_is_single_block(self, mock_qo):
        mock_qo.return_value = [
            {'osm_id': 10, 'name': 'Dead End', 'node_ids': [1, 2, 3],
             'coords': [(-122.1, 37.4), (-122.15, 37.45), (-122.2, 37.5)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        self.assertEqual(self.campaign.streets.count(), 1)
        self.assertEqual(self.campaign.streets.first().block_index, 0)

    @patch('campaigns.tasks.query_overpass')
    def test_rerun_does_not_duplicate_blocks(self, mock_qo):
        mock_qo.return_value = [
            {'osm_id': 10, 'name': 'A St', 'node_ids': [1, 2, 3],
             'coords': [(-122.1, 37.4), (-122.15, 37.45), (-122.2, 37.5)]},
            {'osm_id': 20, 'name': 'B St', 'node_ids': [2, 4],
             'coords': [(-122.15, 37.45), (-122.25, 37.55)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        fetch_osm_segments(self.campaign.pk)
        self.assertEqual(self.campaign.streets.count(), 3)

    @patch('campaigns.tasks.query_overpass')
    def test_bbox_computed_after_successful_import(self, mock_qo):
        mock_qo.return_value = [
            {'osm_id': 10, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
            {'osm_id': 20, 'name': 'B St', 'coords': [(-122.3, 37.6), (-122.0, 37.3)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        self.campaign.refresh_from_db()
        bbox = self.campaign.bbox
        self.assertIsNotNone(bbox)
        # bbox is [[sw_lat, sw_lon], [ne_lat, ne_lon]]
        sw, ne = bbox
        self.assertAlmostEqual(sw[0], 37.3)   # min lat
        self.assertAlmostEqual(sw[1], -122.3)  # min lon
        self.assertAlmostEqual(ne[0], 37.6)   # max lat
        self.assertAlmostEqual(ne[1], -122.0)  # max lon

    @patch('campaigns.tasks.query_overpass')
    def test_bbox_reset_to_none_on_fetch_error(self, mock_qo):
        self.campaign.bbox = [[37.4, -122.1], [37.5, -122.0]]
        self.campaign.save(update_fields=['bbox'])
        mock_qo.side_effect = RuntimeError('Overpass down')
        fetch_osm_segments(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.bbox)

    @patch('campaigns.tasks.query_overpass_addresses')
    @patch('campaigns.tasks.query_overpass')
    def test_address_fetch_uses_geo_limit_bbox(self, mock_qo, mock_qa):
        from django.contrib.gis.geos import Polygon
        mock_qo.return_value = [
            {'osm_id': 10, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
        ]
        mock_qa.return_value = []
        geo_limit = Polygon.from_bbox((-122.3, 37.3, -122.0, 37.6))
        geo_limit.srid = 4326
        self.campaign.geo_limit = geo_limit
        self.campaign.save(update_fields=['geo_limit'])
        fetch_osm_segments(self.campaign.pk)
        mock_qa.assert_called_once()
        _, kwargs = mock_qa.call_args
        self.assertIsNotNone(kwargs.get('bbox'))

    @patch('campaigns.tasks.query_overpass_addresses')
    @patch('campaigns.tasks.query_overpass')
    def test_address_fetch_uses_no_bbox_without_geo_limit(self, mock_qo, mock_qa):
        mock_qo.return_value = [
            {'osm_id': 10, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
        ]
        mock_qa.return_value = []
        fetch_osm_segments(self.campaign.pk)
        mock_qa.assert_called_once()
        _, kwargs = mock_qa.call_args
        self.assertIsNone(kwargs.get('bbox'))


# ── Admin tests ───────────────────────────────────────────────────────────────

class CampaignAdminTest(TestCase):

    def setUp(self):
        self.site = django_admin.AdminSite()
        self.ma = CampaignAdmin(Campaign, self.site)
        self.request = make_admin_request()

    def test_delete_model_soft_deletes_row(self):
        c = make_campaign(slug='to-delete')
        self.ma.delete_model(self.request, c)
        c.refresh_from_db()
        self.assertEqual(c.status, 'deleted')
        self.assertTrue(Campaign.objects.filter(pk=c.pk).exists())

    def test_delete_queryset_soft_deletes(self):
        c = make_campaign(slug='qs-delete')
        self.ma.delete_queryset(self.request, Campaign.objects.filter(pk=c.pk))
        c.refresh_from_db()
        self.assertEqual(c.status, 'deleted')

    def test_get_queryset_excludes_deleted_campaigns(self):
        active = make_campaign(slug='active-one')
        deleted = make_campaign(slug='deleted-one', status='deleted')
        pks = list(self.ma.get_queryset(self.request).values_list('pk', flat=True))
        self.assertIn(active.pk, pks)
        self.assertNotIn(deleted.pk, pks)

    def test_readonly_fields_includes_slug_for_published(self):
        c = make_campaign(slug='pub-ro', status='published')
        readonly = self.ma.get_readonly_fields(self.request, obj=c)
        self.assertIn('slug', readonly)
        self.assertNotIn('cities', readonly)  # cities is editable on published campaigns

    def test_readonly_fields_excludes_slug_for_draft(self):
        c = make_campaign(slug='draft-ro', status='draft')
        readonly = self.ma.get_readonly_fields(self.request, obj=c)
        self.assertNotIn('slug', readonly)
        self.assertNotIn('cities', readonly)

    def test_readonly_fields_for_new_object_excludes_slug(self):
        readonly = self.ma.get_readonly_fields(self.request, obj=None)
        self.assertNotIn('slug', readonly)

    def test_map_status_badge_renders_correct_color_for_each_status(self):
        for status, expected_color in MAP_STATUS_COLORS.items():
            c = Campaign(map_status=status)
            badge = self.ma.map_status_badge(c)
            self.assertIn(expected_color, badge,
                          msg=f"Expected color {expected_color} for status {status}")

    def test_map_status_badge_contains_display_label(self):
        c = Campaign(map_status='ready')
        badge = self.ma.map_status_badge(c)
        self.assertIn('Ready', badge)

    @patch('campaigns.admin.queue_city_fetches')
    def test_publish_action_sets_status_and_queues_task(self, mock_task):
        c = make_campaign(slug='to-publish', status='draft')
        self.ma.publish_campaigns(self.request, Campaign.objects.filter(pk=c.pk))
        c.refresh_from_db()
        self.assertEqual(c.status, 'published')
        mock_task.assert_called_once_with(c.pk)

    @patch('campaigns.admin.queue_city_fetches')
    def test_publish_action_resets_map_status_to_pending(self, mock_task):
        c = make_campaign(slug='repub', status='draft', map_status='error')
        self.ma.publish_campaigns(self.request, Campaign.objects.filter(pk=c.pk))
        c.refresh_from_db()
        self.assertEqual(c.map_status, 'pending')

    @patch('campaigns.admin.queue_city_fetches')
    def test_publish_action_skips_deleted_campaigns(self, mock_task):
        c = make_campaign(slug='skip-deleted', status='deleted')
        self.ma.publish_campaigns(self.request, Campaign.objects.filter(pk=c.pk))
        mock_task.assert_not_called()

    @patch('campaigns.admin.queue_city_fetches')
    def test_response_change_publish_button_sets_published_and_queues_task(self, mock_task):
        c = make_campaign(slug='btn-pub', status='draft')
        request = make_admin_request('post')
        request.POST = {'_publish': '1'}
        self.ma.response_change(request, c)
        c.refresh_from_db()
        self.assertEqual(c.status, 'published')
        self.assertEqual(c.map_status, 'pending')
        mock_task.assert_called_once_with(c.pk)

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_does_not_queue_task_when_publish_button_used(self, mock_task):
        """Prevents double-triggering when Publish button submitted."""
        c = make_campaign(slug='no-double', status='draft')
        c.status = 'published'
        request = make_admin_request('post')
        request.POST = {'_publish': '1'}
        form = MagicMock()
        self.ma.save_model(request, c, form, change=True)
        mock_task.assert_not_called()

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_queues_task_when_creating_published_campaign(self, mock_task):
        form = MagicMock()
        c = Campaign(name='New Camp', slug='new-camp', cities=['x'], status='published')
        self.ma.save_model(self.request, c, form, change=False)
        mock_task.assert_called_once_with(c.pk)

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_does_not_queue_task_for_draft_on_create(self, mock_task):
        form = MagicMock()
        c = Campaign(name='Draft Camp', slug='draft-new', cities=['x'], status='draft')
        self.ma.save_model(self.request, c, form, change=False)
        mock_task.assert_not_called()

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_queues_task_when_editing_draft_to_published(self, mock_task):
        c = make_campaign(slug='edit-to-pub', status='draft')
        c.status = 'published'
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.assert_called_once_with(c.pk)

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_does_not_requeue_task_for_already_published(self, mock_task):
        c = make_campaign(slug='already-pub', status='published')
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.assert_not_called()

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_requeues_task_when_cities_change_on_published(self, mock_task):
        c = make_campaign(slug='cities-change', status='published', cities=['Palo Alto'])
        c.cities = ['Palo Alto', 'Menlo Park']
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.assert_called_once_with(c.pk)

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_resets_map_status_to_pending_when_cities_change(self, mock_task):
        c = make_campaign(slug='cities-pending', status='published', cities=['Palo Alto'])
        c.cities = ['Menlo Park']
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        c.refresh_from_db()
        self.assertEqual(c.map_status, 'pending')

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_does_not_requeue_when_cities_unchanged(self, mock_task):
        c = make_campaign(slug='cities-same', status='published', cities=['Palo Alto'])
        c.cities = ['Palo Alto']  # same value
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.assert_not_called()

    @patch('campaigns.admin.queue_city_fetches')
    def test_save_model_does_not_requeue_cities_change_on_draft(self, mock_task):
        c = make_campaign(slug='cities-draft', status='draft', cities=['Palo Alto'])
        c.cities = ['Menlo Park']
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.assert_not_called()

    def test_soft_delete_action_marks_all_as_deleted(self):
        c1 = make_campaign(slug='del-1')
        c2 = make_campaign(slug='del-2')
        self.ma.soft_delete_campaigns(
            self.request, Campaign.objects.filter(pk__in=[c1.pk, c2.pk])
        )
        c1.refresh_from_db()
        c2.refresh_from_db()
        self.assertEqual(c1.status, 'deleted')
        self.assertEqual(c2.status, 'deleted')


# ── End-to-end: publish → import → worker flow ───────────────────────────────

class WorkerFlowEndToEndTest(TestCase):
    """
    Full pipeline: campaign published → OSM fetch (mocked) splits ways at
    intersections → worker sees blocks on map → worker logs a trip →
    coverage reflects that trip.

    Two intersecting ways share node 2, so 'Main St' (nodes 1-2-3) splits
    into two blocks and 'Cross St' (nodes 2-4) stays as one block.
    Expected Street rows: 3 total (Main St block 0, Main St block 1, Cross St block 0).
    """

    WAYS = [
        {
            'osm_id': 10, 'name': 'Main St',
            'node_ids': [1, 2, 3],
            'coords': [(-122.10, 37.40), (-122.15, 37.45), (-122.20, 37.50)],
        },
        {
            'osm_id': 20, 'name': 'Cross St',
            'node_ids': [2, 4],
            'coords': [(-122.15, 37.45), (-122.25, 37.55)],
        },
    ]

    def setUp(self):
        self.client = Client()
        self.lookup_patcher = patch('campaigns.tasks.lookup_city')
        self.mock_lookup = self.lookup_patcher.start()

    def tearDown(self):
        self.lookup_patcher.stop()

    @patch('campaigns.tasks.query_overpass')
    def test_full_worker_flow(self, mock_qo):
        mock_qo.return_value = self.WAYS

        # ── 1. Publish campaign and run OSM import ────────────────────────────
        campaign = make_campaign(slug='e2e-camp', cities=['Testville'])
        fetch_osm_segments(campaign.pk)
        # Simulate the async render step (normally dispatched to Celery).
        render_campaign_geojson(campaign.pk, final_status='ready')
        campaign.refresh_from_db()

        self.assertEqual(campaign.map_status, 'ready')
        self.assertEqual(campaign.streets.count(), 3,
                         "Main St should split into 2 blocks; Cross St stays 1")

        main_blocks = campaign.streets.filter(osm_id=10).order_by('block_index')
        self.assertEqual(main_blocks.count(), 2)
        self.assertEqual(main_blocks[0].start_node_id, 1)
        self.assertEqual(main_blocks[0].end_node_id, 2)
        self.assertEqual(main_blocks[1].start_node_id, 2)
        self.assertEqual(main_blocks[1].end_node_id, 3)

        cross_block = campaign.streets.get(osm_id=20)
        self.assertEqual(cross_block.block_index, 0)

        # ── 2. Worker fetches streets GeoJSON ─────────────────────────────────
        resp = self.client.get(f'/c/e2e-camp/streets.geojson')
        self.assertEqual(resp.status_code, 200)
        streets_data = resp.json()
        self.assertEqual(len(streets_data['features']), 3)
        returned_ids = {f['id'] for f in streets_data['features']}
        expected_ids = set(campaign.streets.values_list('pk', flat=True))
        self.assertEqual(returned_ids, expected_ids)

        # ── 3. Coverage is empty before any trips ─────────────────────────────
        resp = self.client.get(f'/c/e2e-camp/coverage.geojson')
        self.assertEqual(resp.json()['features'], [])

        # ── 4. Worker logs a trip covering Main St block 0 and Cross St ───────
        trip_segment_ids = [main_blocks[0].pk, cross_block.pk]
        resp = self.client.post(
            f'/c/e2e-camp/trip/',
            data=json.dumps({'segment_ids': trip_segment_ids, 'worker_name': 'Alice'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ok')

        # ── 5. Coverage reflects exactly the logged trip ───────────────────────
        resp = self.client.get(f'/c/e2e-camp/coverage.geojson')
        coverage_data = resp.json()
        covered_osm_ids = {f['properties']['osm_id'] for f in coverage_data['features']}
        self.assertEqual(len(coverage_data['features']), 2)
        self.assertIn(10, covered_osm_ids)   # Main St block 0
        self.assertIn(20, covered_osm_ids)   # Cross St
        # Main St block 1 was not walked — should not appear
        covered_ids = {f['id'] for f in coverage_data['features']}
        self.assertNotIn(main_blocks[1].pk, covered_ids)

        # ── 6. Second worker logs a trip covering the remaining block ──────────
        resp = self.client.post(
            f'/c/e2e-camp/trip/',
            data=json.dumps({'segment_ids': [main_blocks[1].pk], 'worker_name': 'Bob'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f'/c/e2e-camp/coverage.geojson')
        self.assertEqual(len(resp.json()['features']), 3,
                         "All 3 blocks should now be covered")


# ── Manager UI tests ──────────────────────────────────────────────────────────

class ManagerUITest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='manager', password='password123')
        self.campaign = make_campaign(slug='mgr-camp', status='draft')

    def _login(self):
        self.client.login(username='manager', password='password123')

    # ── Authentication ────────────────────────────────────────────────────────

    def test_unauthenticated_list_redirects_to_login(self):
        resp = self.client.get('/manage/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/login/', resp['Location'])

    def test_unauthenticated_detail_redirects_to_login(self):
        resp = self.client.get(f'/manage/{self.campaign.slug}/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/login/', resp['Location'])

    def test_unauthenticated_create_redirects_to_login(self):
        resp = self.client.get('/manage/new/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/login/', resp['Location'])

    # ── Authenticated access ──────────────────────────────────────────────────

    def test_authenticated_can_access_list(self):
        self._login()
        resp = self.client.get('/manage/')
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_can_access_create(self):
        self._login()
        resp = self.client.get('/manage/new/')
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_can_access_detail(self):
        self._login()
        resp = self.client.get(f'/manage/{self.campaign.slug}/')
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_can_access_edit(self):
        self._login()
        resp = self.client.get(f'/manage/{self.campaign.slug}/edit/')
        self.assertEqual(resp.status_code, 200)

    # ── Create ────────────────────────────────────────────────────────────────

    def _city_json(self, name='San Jose', osm_id=112143):
        return json.dumps([{
            'name': name, 'osm_id': osm_id,
            'osm_type': 'relation', 'display_name': f'{name}, CA',
        }])

    def test_create_saves_as_draft(self):
        self._login()
        self.client.post('/manage/new/', {
            'name': 'Brand New Campaign',
            'cities_json': self._city_json('San Jose'),
            'start_date': '2026-06-01',
        })
        campaign = Campaign.objects.get(name='Brand New Campaign')
        self.assertEqual(campaign.status, 'draft')

    def test_create_redirects_to_detail(self):
        self._login()
        resp = self.client.post('/manage/new/', {
            'name': 'Redirect Test',
            'cities_json': self._city_json('Palo Alto', 123),
            'start_date': '2026-06-01',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/', resp['Location'])

    def test_create_autogenerates_slug_when_blank(self):
        self._login()
        self.client.post('/manage/new/', {
            'name': 'Auto Slug Campaign',
            'cities_json': self._city_json('Sunnyvale', 456),
            'start_date': '2026-06-01',
        })
        campaign = Campaign.objects.get(name='Auto Slug Campaign')
        self.assertEqual(campaign.slug, 'auto-slug-campaign')

    # ── Publish ───────────────────────────────────────────────────────────────

    def test_publish_sets_status(self):
        self._login()
        self.client.post(f'/manage/{self.campaign.slug}/publish/')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'published')

    def test_publish_does_not_refetch(self):
        self._login()
        with patch('campaigns.views.queue_city_fetches') as mock_task:
            self.client.post(f'/manage/{self.campaign.slug}/publish/')
            mock_task.assert_not_called()

    def test_publish_redirects_to_detail(self):
        self._login()
        resp = self.client.post(f'/manage/{self.campaign.slug}/publish/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f'/manage/{self.campaign.slug}/', resp['Location'])

    # ── Unpublish ─────────────────────────────────────────────────────────────

    def test_unpublish_sets_status_to_draft(self):
        self.campaign.status = 'published'
        self.campaign.save()
        self._login()
        self.client.post(f'/manage/{self.campaign.slug}/unpublish/')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'draft')

    def test_unpublish_redirects_to_detail(self):
        self.campaign.status = 'published'
        self.campaign.save()
        self._login()
        resp = self.client.post(f'/manage/{self.campaign.slug}/unpublish/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f'/manage/{self.campaign.slug}/', resp['Location'])

    def test_unpublished_campaign_public_url_returns_404(self):
        # After unpublishing, the public URL must return 404
        self.campaign.status = 'published'
        self.campaign.save()
        self._login()
        self.client.post(f'/manage/{self.campaign.slug}/unpublish/')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'draft')
        # Draft campaigns return 404 on the public URL (same as test_draft_campaign_returns_404)
        resp = self.client.get(f'/c/{self.campaign.slug}/')
        self.assertEqual(resp.status_code, 404)

    def test_unpublish_preserves_campaign_data(self):
        self.campaign.status = 'published'
        self.campaign.save()
        original_name = self.campaign.name
        original_cities = self.campaign.cities
        self._login()
        self.client.post(f'/manage/{self.campaign.slug}/unpublish/')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.name, original_name)
        self.assertEqual(self.campaign.cities, original_cities)

    def test_unpublish_requires_login(self):
        self.campaign.status = 'published'
        self.campaign.save()
        resp = self.client.post(f'/manage/{self.campaign.slug}/unpublish/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/login/', resp['Location'])
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'published')

    def test_unpublish_requires_post(self):
        self.campaign.status = 'published'
        self.campaign.save()
        self._login()
        resp = self.client.get(f'/manage/{self.campaign.slug}/unpublish/')
        self.assertEqual(resp.status_code, 405)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'published')

    # ── Delete ────────────────────────────────────────────────────────────────

    def test_soft_delete_marks_as_deleted(self):
        self._login()
        self.client.post(f'/manage/{self.campaign.slug}/delete/')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'deleted')

    def test_deleted_campaign_not_in_list(self):
        self._login()
        self.client.post(f'/manage/{self.campaign.slug}/delete/')
        resp = self.client.get('/manage/')
        self.assertNotContains(resp, self.campaign.name)

    def test_delete_redirects_to_list(self):
        self._login()
        resp = self.client.post(f'/manage/{self.campaign.slug}/delete/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/', resp['Location'])

    # ── Re-fetch ──────────────────────────────────────────────────────────────

    @patch('campaigns.views.queue_city_fetches')
    def test_refetch_triggers_task_on_error_campaign(self, mock_task):
        self.campaign.status = 'published'
        self.campaign.map_status = 'error'
        self.campaign.save()
        self._login()
        self.client.post(f'/manage/{self.campaign.slug}/refetch/')
        mock_task.assert_called_once_with(self.campaign.pk)

    # ── Cities JSON parsing ───────────────────────────────────────────────────

    def test_cities_json_saved_as_dict_list(self):
        self._login()
        cities = [
            {'name': 'San Jose', 'osm_id': 112143, 'osm_type': 'relation', 'display_name': 'San José, CA'},
            {'name': 'Palo Alto', 'osm_id': 999, 'osm_type': 'relation', 'display_name': 'Palo Alto, CA'},
        ]
        self.client.post('/manage/new/', {
            'name': 'City Parse Test',
            'cities_json': json.dumps(cities),
            'start_date': '2026-06-01',
        })
        campaign = Campaign.objects.get(name='City Parse Test')
        self.assertEqual(campaign.cities[0]['name'], 'San Jose')
        self.assertEqual(campaign.cities[1]['osm_id'], 999)

    def test_cities_json_invalid_json_fails_validation(self):
        self._login()
        resp = self.client.post('/manage/new/', {
            'name': 'Bad JSON Test',
            'cities_json': 'not-json',
            'start_date': '2026-06-01',
        })
        self.assertEqual(resp.status_code, 200)  # form re-rendered with errors
        self.assertFalse(Campaign.objects.filter(name='Bad JSON Test').exists())

    def test_cities_json_empty_list_fails_validation(self):
        self._login()
        resp = self.client.post('/manage/new/', {
            'name': 'Empty Cities Test',
            'cities_json': '[]',
            'start_date': '2026-06-01',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Campaign.objects.filter(name='Empty Cities Test').exists())

    # ── List view ─────────────────────────────────────────────────────────────

    def test_list_shows_campaign_name(self):
        self._login()
        resp = self.client.get('/manage/')
        self.assertContains(resp, self.campaign.name)

    def test_list_excludes_deleted_campaigns(self):
        self._login()
        make_campaign(slug='hidden-del', status='deleted', name='Hidden Deleted Camp')
        resp = self.client.get('/manage/')
        self.assertNotContains(resp, 'Hidden Deleted Camp')


# ── Task tests: lookup_city ───────────────────────────────────────────────────

def _make_nominatim_response(results, status_code=200):
    mock_resp = MagicMock()
    mock_resp.json.return_value = results
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = Exception(f'HTTP {status_code}')
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


class LookupCityTest(TestCase):

    @patch('campaigns.tasks.requests.get')
    def test_single_city_result_raises_no_exception(self, mock_get):
        mock_get.return_value = _make_nominatim_response([
            {'class': 'place', 'type': 'city', 'display_name': 'Palo Alto, CA'},
        ])
        lookup_city('Palo Alto')  # should not raise

    @patch('campaigns.tasks.requests.get')
    def test_no_results_raises_value_error_not_found(self, mock_get):
        mock_get.return_value = _make_nominatim_response([])
        with self.assertRaises(ValueError) as ctx:
            lookup_city('xyznotacity')
        self.assertIn('not found', str(ctx.exception))

    @patch('campaigns.tasks.requests.get')
    def test_multiple_results_raises_value_error_with_count(self, mock_get):
        mock_get.return_value = _make_nominatim_response([
            {'class': 'place', 'type': 'city', 'display_name': 'Springfield, IL'},
            {'class': 'place', 'type': 'city', 'display_name': 'Springfield, MA'},
            {'class': 'place', 'type': 'town', 'display_name': 'Springfield, OH'},
        ])
        with self.assertRaises(ValueError) as ctx:
            lookup_city('Springfield')
        self.assertIn('3', str(ctx.exception))

    @patch('campaigns.tasks.requests.get')
    def test_non_place_class_results_are_filtered_out(self, mock_get):
        mock_get.return_value = _make_nominatim_response([
            {'class': 'boundary', 'type': 'city', 'display_name': 'Palo Alto county'},
            {'class': 'place', 'type': 'city', 'display_name': 'Palo Alto, CA'},
        ])
        lookup_city('Palo Alto')  # only one place-class result — should not raise

    @patch('campaigns.tasks.requests.get')
    def test_http_error_propagates(self, mock_get):
        mock_get.return_value = _make_nominatim_response([], status_code=503)
        with self.assertRaises(Exception):
            lookup_city('AnyCity')

    @patch('campaigns.tasks.requests.get')
    def test_network_error_propagates(self, mock_get):
        mock_get.side_effect = ConnectionError('network down')
        with self.assertRaises(Exception):
            lookup_city('AnyCity')


# ── Task tests: fetch_osm_segments error handling ─────────────────────────────

class FetchOSMErrorHandlingTest(TestCase):

    def setUp(self):
        self.campaign = make_campaign(slug='err-camp')
        self.lookup_patcher = patch('campaigns.tasks.lookup_city')
        self.mock_lookup = self.lookup_patcher.start()
        self.overpass_patcher = patch('campaigns.tasks.query_overpass')
        self.mock_overpass = self.overpass_patcher.start()

    def tearDown(self):
        self.lookup_patcher.stop()
        self.overpass_patcher.stop()

    def test_lookup_city_raises_sets_error_status_and_message(self):
        self.mock_lookup.side_effect = ValueError('City "xyznotacity" not found in OpenStreetMap')
        fetch_osm_segments(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')
        self.assertIn('xyznotacity', self.campaign.map_error)
        self.assertIn('not found', self.campaign.map_error)

    def test_query_overpass_returns_empty_sets_no_streets_error(self):
        self.mock_overpass.return_value = []
        fetch_osm_segments(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')
        self.assertIn('no streets', self.campaign.map_error)

    def test_successful_fetch_clears_preexisting_map_error(self):
        self.campaign.map_error = 'Previous error message'
        self.campaign.save(update_fields=['map_error'])
        self.mock_overpass.return_value = [
            {'osm_id': 1, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'ready')
        self.assertEqual(self.campaign.map_error, '')

    def test_map_error_cleared_at_start_of_fetch(self):
        self.campaign.map_error = 'Old error'
        self.campaign.save(update_fields=['map_error'])

        captured_error = []

        def capture_on_overpass_call(city):
            self.campaign.refresh_from_db()
            captured_error.append(self.campaign.map_error)
            return [{'osm_id': 1, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]}]

        self.mock_overpass.side_effect = capture_on_overpass_call
        fetch_osm_segments(self.campaign.pk)
        self.assertEqual(captured_error[0], '', 'map_error should be cleared before processing starts')

    def test_ambiguous_city_error_message_included(self):
        self.mock_lookup.side_effect = ValueError('3 places named "Springfield" found; use a more specific name')
        fetch_osm_segments(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')
        self.assertIn('Springfield', self.campaign.map_error)

    def test_dict_city_skips_lookup_city(self):
        """Dict-format cities (with osm_id) bypass lookup_city entirely."""
        dict_city = {'name': 'San Jose', 'osm_id': 112143, 'osm_type': 'relation',
                     'display_name': 'San José, CA'}
        self.campaign.cities = [dict_city]
        self.campaign.save(update_fields=['cities'])
        self.mock_overpass.return_value = [
            {'osm_id': 1, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
        ]
        fetch_osm_segments(self.campaign.pk)
        self.mock_lookup.assert_not_called()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'ready')


# ── Task tests: block limit (issue #71) ──────────────────────────────────────

class FetchOSMBlockLimitTest(TestCase):
    """Tests for the MAX_CAMPAIGN_BLOCKS guard in fetch_city_osm_data."""

    def setUp(self):
        self.campaign = make_campaign(slug='block-limit-camp')
        self.lookup_patcher = patch('campaigns.tasks.lookup_city')
        self.lookup_patcher.start()
        self.overpass_patcher = patch('campaigns.tasks.query_overpass')
        self.mock_overpass = self.overpass_patcher.start()
        # Suppress address fetch to keep tests fast
        self.addr_patcher = patch('campaigns.tasks.query_overpass_addresses', return_value=[])
        self.addr_patcher.start()

    def tearDown(self):
        self.lookup_patcher.stop()
        self.overpass_patcher.stop()
        self.addr_patcher.stop()

    def _run_task(self):
        task = fetch_city_osm_data
        task.push_request(retries=0)
        try:
            return task(self.campaign.pk, 0)
        finally:
            task.pop_request()

    def test_normal_import_succeeds_under_limit(self):
        """A small import well under MAX_CAMPAIGN_BLOCKS should succeed."""
        self.mock_overpass.return_value = [
            {'osm_id': 1, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
        ]
        self._run_task()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'ready')
        self.assertEqual(self.campaign.streets.count(), 1)

    @patch('campaigns.tasks.MAX_CAMPAIGN_BLOCKS', 2)
    def test_import_stops_at_block_limit_during_import(self):
        """
        When the running total hits MAX_CAMPAIGN_BLOCKS mid-import,
        the task errors and the city job records the limit message.
        """
        # 3 ways would create 3 blocks; limit is 2
        self.mock_overpass.return_value = [
            {'osm_id': 1, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
            {'osm_id': 2, 'name': 'B St', 'coords': [(-122.3, 37.6), (-122.4, 37.7)]},
            {'osm_id': 3, 'name': 'C St', 'coords': [(-122.5, 37.8), (-122.6, 37.9)]},
        ]
        self._run_task()
        job = CityFetchJob.objects.get(campaign=self.campaign, city_index=0)
        self.assertEqual(job.status, 'error')
        self.assertIn('Block limit', job.error)
        self.assertIn('2', job.error)

    @patch('campaigns.tasks.MAX_CAMPAIGN_BLOCKS', 1)
    def test_existing_blocks_over_limit_is_rejected_before_import(self):
        """
        When other-city blocks already exceed MAX_CAMPAIGN_BLOCKS, the task
        rejects this city immediately without writing any streets.
        """
        # Add a block from a different city (city_index=1)
        self.campaign.cities = ['City A', 'City B']
        self.campaign.save(update_fields=['cities'])
        make_street(self.campaign, osm_id=999, city_name='City B', city_index=1)

        self.mock_overpass.return_value = [
            {'osm_id': 1, 'name': 'A St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
        ]

        task = fetch_city_osm_data
        task.push_request(retries=0)
        try:
            task(self.campaign.pk, 0)  # city_index=0, city B already has 1 block
        finally:
            task.pop_request()

        job = CityFetchJob.objects.get(campaign=self.campaign, city_index=0)
        self.assertEqual(job.status, 'error')
        self.assertIn('already has', job.error)
        # No streets should have been linked for city_index=0
        self.assertEqual(
            CampaignStreet.objects.filter(campaign=self.campaign, city_index=0).count(), 0
        )

    @patch('campaigns.tasks.MAX_CAMPAIGN_BLOCKS', 3)
    def test_refetch_does_not_double_count_existing_blocks(self):
        """
        A re-fetch of a city should not count that city's own existing blocks
        against the limit (they'll be replaced).
        """
        # Pre-populate 3 existing blocks for city_index=0
        city_label = 'Palo Alto'  # matches self.campaign.cities[0] set in FetchCityOSMDataTest.setUp
        for i in range(3):
            make_street(self.campaign, osm_id=100 + i, city_name=city_label, city_index=0, block_index=i)

        # Overpass returns 2 blocks — should succeed because the 3 existing
        # blocks for this city are excluded from the pre-check.
        self.mock_overpass.return_value = [
            {'osm_id': 200, 'name': 'New St', 'coords': [(-122.1, 37.4), (-122.2, 37.5)]},
            {'osm_id': 201, 'name': 'New St 2', 'coords': [(-122.3, 37.6), (-122.4, 37.7)]},
        ]
        self._run_task()
        self.campaign.refresh_from_db()
        # Should not have errored on the limit
        job = CityFetchJob.objects.get(campaign=self.campaign, city_index=0)
        self.assertEqual(job.status, 'ready')


# ── Task tests: reuse existing streets (issue #141) ──────────────────────────

class FetchOSMReuseExistingStreetsTest(TestCase):
    """
    When streets for a city already exist globally, fetch_city_osm_data must
    link them to the new campaign without hitting Overpass.
    """

    def setUp(self):
        self.campaign_a = make_campaign(slug='city-reuse-a', cities=['Palo Alto'])
        self.campaign_b = make_campaign(slug='city-reuse-b', cities=['Palo Alto'])
        self.overpass_patcher = patch('campaigns.tasks.query_overpass')
        self.mock_overpass = self.overpass_patcher.start()
        self.addr_patcher = patch('campaigns.tasks.query_overpass_addresses', return_value=[])
        self.addr_patcher.start()

    def tearDown(self):
        self.overpass_patcher.stop()
        self.addr_patcher.stop()

    def test_skips_overpass_when_streets_exist(self):
        """Second campaign for the same city must not call query_overpass."""
        # Pre-populate streets for 'Palo Alto' (as if campaign_a already fetched them)
        for i in range(3):
            make_street(self.campaign_a, osm_id=100 + i, city_name='Palo Alto', city_index=0, block_index=i)

        fetch_city_osm_data(self.campaign_b.pk, 0)

        self.mock_overpass.assert_not_called()

    def test_links_existing_streets_to_new_campaign(self):
        """All streets for the city must be linked to the new campaign."""
        streets = [
            make_street(self.campaign_a, osm_id=200 + i, city_name='Palo Alto', city_index=0, block_index=i)
            for i in range(3)
        ]

        fetch_city_osm_data(self.campaign_b.pk, 0)

        for street in streets:
            self.assertTrue(
                CampaignStreet.objects.filter(campaign=self.campaign_b, street=street).exists(),
                f"Street {street.pk} not linked to campaign_b",
            )

    def test_job_marked_ready(self):
        """CityFetchJob must be marked ready after the fast-path link."""
        make_street(self.campaign_a, osm_id=300, city_name='Palo Alto', city_index=0)
        CityFetchJob.objects.create(campaign=self.campaign_b, city_index=0, city_name='Palo Alto', status='pending')

        fetch_city_osm_data(self.campaign_b.pk, 0)

        job = CityFetchJob.objects.get(campaign=self.campaign_b, city_index=0)
        self.assertEqual(job.status, 'ready')

    def test_does_not_duplicate_existing_link(self):
        """If the campaign already has a CampaignStreet for the city, no duplicate is created."""
        street = make_street(self.campaign_a, osm_id=400, city_name='Palo Alto', city_index=0)
        # Pre-link the street to campaign_b
        CampaignStreet.objects.create(campaign=self.campaign_b, street=street, city_index=0)

        fetch_city_osm_data(self.campaign_b.pk, 0)

        self.assertEqual(
            CampaignStreet.objects.filter(campaign=self.campaign_b, street=street).count(), 1
        )

    @patch('campaigns.tasks.MAX_CAMPAIGN_BLOCKS', 2)
    def test_block_limit_respected_on_fast_path(self):
        """Fast-path must still enforce the per-campaign block limit."""
        # campaign_b already has 2 blocks from another city
        other_street = make_street(self.campaign_b, osm_id=500, city_name='Other City', city_index=1)
        make_street(self.campaign_b, osm_id=501, city_name='Other City', city_index=1, block_index=1)
        # 'Palo Alto' has 1 street globally
        make_street(self.campaign_a, osm_id=600, city_name='Palo Alto', city_index=0)

        fetch_city_osm_data(self.campaign_b.pk, 0)

        job = CityFetchJob.objects.get(campaign=self.campaign_b, city_index=0)
        self.assertEqual(job.status, 'error')
        self.assertIn('already has', job.error)


# ── Task tests: retry on transient errors ─────────────────────────────────────

class FetchOSMRetryTest(TestCase):

    def setUp(self):
        self.campaign = make_campaign(slug='retry-camp')
        self.overpass_patcher = patch('campaigns.tasks.query_overpass')
        self.mock_overpass = self.overpass_patcher.start()
        self.lookup_patcher = patch('campaigns.tasks.lookup_city')
        self.lookup_patcher.start()

    def tearDown(self):
        self.overpass_patcher.stop()
        self.lookup_patcher.stop()

    def _run_task(self, retries=0):
        """Run the task with a mock request context simulating the given retry count."""
        task = fetch_city_osm_data
        task.push_request(retries=retries)
        try:
            return task(self.campaign.pk, 0)  # city_index=0
        finally:
            task.pop_request()

    def test_timeout_retries_and_stays_generating(self):
        self.mock_overpass.side_effect = requests.exceptions.Timeout('timed out')
        try:
            self._run_task(retries=0)
        except Exception:
            pass  # Celery raises Retry internally; we only care about the saved state
        self.campaign.refresh_from_db()
        # Status should remain 'generating' while retrying, not 'error'
        self.assertEqual(self.campaign.map_status, 'generating')

    def test_timeout_on_final_retry_sets_error(self):
        self.mock_overpass.side_effect = requests.exceptions.Timeout('timed out')
        self._run_task(retries=5)  # max_retries=5, so retries=5 means exhausted
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')
        self.assertIn('timed out', self.campaign.map_error)

    def test_connection_error_on_final_retry_sets_error(self):
        self.mock_overpass.side_effect = requests.exceptions.ConnectionError('network down')
        self._run_task(retries=5)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')

    def test_http_503_on_final_retry_sets_error(self):
        resp = MagicMock()
        resp.status_code = 503
        self.mock_overpass.side_effect = requests.exceptions.HTTPError('503', response=resp)
        self._run_task(retries=5)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')

    def test_http_404_not_retried(self):
        resp = MagicMock()
        resp.status_code = 404
        self.mock_overpass.side_effect = requests.exceptions.HTTPError('404', response=resp)
        self._run_task(retries=0)
        self.campaign.refresh_from_db()
        # 4xx should not retry — sets error immediately
        self.assertEqual(self.campaign.map_status, 'error')

    def test_value_error_not_retried(self):
        self.mock_overpass.side_effect = ValueError('city not found')
        self._run_task(retries=0)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')


# ── Task tests: query_overpass dict city ──────────────────────────────────────

class QueryOverpassDictCityTest(TestCase):

    @patch('campaigns.tasks.requests.post')
    def test_dict_city_uses_area_id_query(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        query_overpass({'name': 'San Jose', 'osm_id': 112143, 'osm_type': 'relation',
                        'display_name': 'San José, CA'})
        call_data = mock_post.call_args[1]['data']['data']
        self.assertIn('area(3600112143)', call_data)
        self.assertNotIn('area[name=', call_data)

    @patch('campaigns.tasks.requests.post')
    def test_string_city_uses_name_query(self, mock_post):
        mock_post.return_value = _make_overpass_response()
        query_overpass('Palo Alto')
        call_data = mock_post.call_args[1]['data']['data']
        self.assertIn('area[name="Palo Alto"]', call_data)

    @patch('campaigns.tasks.requests.post')
    def test_query_uses_overpass_server_timeout_constant(self, mock_post):
        """The query must embed OVERPASS_SERVER_TIMEOUT, not a hard-coded value."""
        from campaigns.tasks import OVERPASS_SERVER_TIMEOUT
        mock_post.return_value = _make_overpass_response()
        query_overpass('Palo Alto')
        call_data = mock_post.call_args[1]['data']['data']
        self.assertIn(f'[timeout:{OVERPASS_SERVER_TIMEOUT}]', call_data)

    @patch('campaigns.tasks.requests.post')
    def test_http_request_uses_overpass_http_timeout_constant(self, mock_post):
        """requests.post must be called with OVERPASS_HTTP_TIMEOUT."""
        from campaigns.tasks import OVERPASS_HTTP_TIMEOUT
        mock_post.return_value = _make_overpass_response()
        query_overpass('Palo Alto')
        call_kwargs = mock_post.call_args[1]
        self.assertEqual(call_kwargs['timeout'], OVERPASS_HTTP_TIMEOUT)

    @patch('campaigns.tasks.requests.post')
    def test_server_timeout_is_at_least_180(self, mock_post):
        """Server-side timeout must be large enough to handle big cities (issue #70)."""
        from campaigns.tasks import OVERPASS_SERVER_TIMEOUT
        self.assertGreaterEqual(OVERPASS_SERVER_TIMEOUT, 180)

    @patch('campaigns.tasks.requests.post')
    def test_http_timeout_is_at_least_240(self, mock_post):
        """HTTP client timeout must exceed the server-side timeout by a safe margin."""
        from campaigns.tasks import OVERPASS_HTTP_TIMEOUT
        self.assertGreaterEqual(OVERPASS_HTTP_TIMEOUT, 240)


# ── Manager UI tests: city_search view ───────────────────────────────────────

class CitySearchViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='searcher', password='pass123')

    def _login(self):
        self.client.login(username='searcher', password='pass123')

    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get('/manage/city-search/?q=San+Jose')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/login/', resp['Location'])

    @patch('campaigns.views.requests.get')
    def test_returns_filtered_city_results(self, mock_get):
        mock_get.return_value = _make_nominatim_response([
            # large city stored as boundary/administrative (e.g. Fresno, San Jose)
            {'class': 'boundary', 'type': 'administrative', 'osm_id': '112143', 'osm_type': 'relation',
             'name': 'San Jose', 'display_name': 'San José, Santa Clara County, California, United States'},
            # smaller city stored as place/city
            {'class': 'place', 'type': 'city', 'osm_id': '999', 'osm_type': 'relation',
             'name': 'San Jose', 'display_name': 'San José, Costa Rica'},
            # street — should be excluded
            {'class': 'highway', 'type': 'street', 'osm_id': '1', 'osm_type': 'way',
             'name': 'San Jose Ave', 'display_name': 'San Jose Ave, Anytown'},
        ])
        self._login()
        resp = self.client.get('/manage/city-search/?q=San+Jose')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['results']), 2)
        self.assertEqual(data['results'][0]['osm_id'], 112143)
        self.assertEqual(data['results'][0]['name'], 'San Jose')

    @patch('campaigns.views.requests.get')
    def test_empty_query_returns_empty_results(self, mock_get):
        self._login()
        resp = self.client.get('/manage/city-search/?q=')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'results': []})
        mock_get.assert_not_called()

    @patch('campaigns.views.requests.get')
    def test_nominatim_error_returns_500(self, mock_get):
        mock_get.side_effect = ConnectionError('network down')
        self._login()
        resp = self.client.get('/manage/city-search/?q=Anywhere')
        self.assertEqual(resp.status_code, 500)
        self.assertIn('error', resp.json())


class CitiesPrefetchedViewTest(TestCase):
    """
    Tests for /manage/cities/prefetched/.

    Since streets are now decoupled from campaigns (issue #128), the endpoint
    returns city_names directly from the Street table rather than cross-referencing
    campaign city lists.  The frontend uses these names to bold selected cities and
    badge search results.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='prefetchuser', password='pass123')

    def _login(self):
        self.client.login(username='prefetchuser', password='pass123')

    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get('/manage/cities/prefetched/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/login/', resp['Location'])

    def test_returns_empty_when_no_streets(self):
        self._login()
        resp = self.client.get('/manage/cities/prefetched/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'city_names': []})

    def test_returns_city_names_from_street_table(self):
        # Two streets for the same city — should appear only once.
        Street.objects.create(city_name='Springfield', osm_id=100, block_index=0, geometry=GEOM)
        Street.objects.create(city_name='Springfield', osm_id=100, block_index=1, geometry=GEOM2)
        self._login()
        resp = self.client.get('/manage/cities/prefetched/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('city_names', data)
        self.assertIn('Springfield', data['city_names'])
        self.assertEqual(data['city_names'].count('Springfield'), 1)  # deduplicated

    def test_returns_multiple_distinct_city_names(self):
        Street.objects.create(city_name='Fresno', osm_id=1, block_index=0, geometry=GEOM)
        Street.objects.create(city_name='Clovis', osm_id=2, block_index=0, geometry=GEOM2)
        self._login()
        resp = self.client.get('/manage/cities/prefetched/')
        data = resp.json()
        self.assertIn('Fresno', data['city_names'])
        self.assertIn('Clovis', data['city_names'])

    def test_does_not_require_campaign_to_report_city(self):
        # Streets exist for a city but no campaign references it — still returned.
        Street.objects.create(city_name='Orphan City', osm_id=42, block_index=0, geometry=GEOM)
        self._login()
        resp = self.client.get('/manage/cities/prefetched/')
        data = resp.json()
        self.assertIn('Orphan City', data['city_names'])


# ── _apply_city_list_changes tests ───────────────────────────────────────────

def _make_city(name, osm_id):
    return {'name': name, 'osm_id': osm_id, 'osm_type': 'relation', 'display_name': name}


def _make_fetch_job(campaign, city_index, city_name, status='ready'):
    return CityFetchJob.objects.create(
        campaign=campaign, city_index=city_index, city_name=city_name, status=status,
    )


class ApplyCityListChangesTest(TestCase):

    def _make_campaign_with_cities(self, cities):
        campaign = Campaign.objects.create(
            name='Test', slug=f'test-{id(cities)}', cities=cities, map_status='ready',
        )
        return campaign

    def _make_street(self, campaign, osm_id, city_index):
        return make_street(campaign, osm_id=osm_id, city_index=city_index)

    def test_removing_middle_city_unlinks_its_streets(self):
        """Removing a city unlinks streets from the campaign (M2M removed) but does
        not delete the Street objects themselves — they persist for re-use."""
        cities = [_make_city('A', 1), _make_city('B', 2), _make_city('C', 3)]
        campaign = self._make_campaign_with_cities(cities)
        street_a = self._make_street(campaign, 101, 0)
        street_b = self._make_street(campaign, 102, 1)
        street_c = self._make_street(campaign, 103, 2)
        _make_fetch_job(campaign, 0, 'A')
        _make_fetch_job(campaign, 1, 'B')
        _make_fetch_job(campaign, 2, 'C')

        new_cities = [_make_city('A', 1), _make_city('C', 3)]
        campaign.cities = new_cities
        campaign.save()
        _apply_city_list_changes(cities, campaign)

        # Street objects persist — only the M2M link is removed
        self.assertTrue(Street.objects.filter(pk=street_a.pk).exists())
        self.assertTrue(Street.objects.filter(pk=street_b.pk).exists())
        self.assertTrue(Street.objects.filter(pk=street_c.pk).exists())
        # CampaignStreet for B (city_index=1) removed; A and C still linked
        self.assertTrue(CampaignStreet.objects.filter(campaign=campaign, street=street_a).exists())
        self.assertFalse(CampaignStreet.objects.filter(campaign=campaign, street=street_b).exists())
        self.assertTrue(CampaignStreet.objects.filter(campaign=campaign, street=street_c).exists())

    def test_removing_middle_city_deletes_its_fetch_job(self):
        cities = [_make_city('A', 1), _make_city('B', 2), _make_city('C', 3)]
        campaign = self._make_campaign_with_cities(cities)
        _make_fetch_job(campaign, 0, 'A')
        _make_fetch_job(campaign, 1, 'B')
        _make_fetch_job(campaign, 2, 'C')

        new_cities = [_make_city('A', 1), _make_city('C', 3)]
        campaign.cities = new_cities
        campaign.save()
        _apply_city_list_changes(cities, campaign)

        self.assertFalse(CityFetchJob.objects.filter(campaign=campaign, city_index=1, city_name='B').exists())

    def test_remaining_cities_get_renumbered(self):
        cities = [_make_city('A', 1), _make_city('B', 2), _make_city('C', 3)]
        campaign = self._make_campaign_with_cities(cities)
        self._make_street(campaign, 101, 0)
        self._make_street(campaign, 102, 1)
        self._make_street(campaign, 103, 2)
        _make_fetch_job(campaign, 0, 'A')
        _make_fetch_job(campaign, 1, 'B')
        _make_fetch_job(campaign, 2, 'C')

        new_cities = [_make_city('A', 1), _make_city('C', 3)]
        campaign.cities = new_cities
        campaign.save()
        _apply_city_list_changes(cities, campaign)

        # C moves from index 2 to index 1
        self.assertTrue(CampaignStreet.objects.filter(campaign=campaign, city_index=1, street__osm_id=103).exists())
        self.assertTrue(CityFetchJob.objects.filter(campaign=campaign, city_index=1, city_name='C').exists())
        # Old index 2 should be gone
        self.assertFalse(CampaignStreet.objects.filter(campaign=campaign, city_index=2).exists())

    @patch('campaigns.views.queue_city_fetches')
    def test_removing_city_does_not_trigger_refetch(self, mock_queue):
        cities = [_make_city('A', 1), _make_city('B', 2)]
        campaign = self._make_campaign_with_cities(cities)
        _make_fetch_job(campaign, 0, 'A')
        _make_fetch_job(campaign, 1, 'B')

        new_cities = [_make_city('A', 1)]
        campaign.cities = new_cities
        campaign.save()
        _apply_city_list_changes(cities, campaign)

        mock_queue.assert_not_called()

    @patch('campaigns.views.queue_city_fetches')
    def test_adding_city_only_fetches_new_city(self, mock_queue):
        cities = [_make_city('A', 1)]
        campaign = self._make_campaign_with_cities(cities)
        _make_fetch_job(campaign, 0, 'A')

        new_cities = [_make_city('A', 1), _make_city('B', 2)]
        campaign.cities = new_cities
        campaign.save()
        _apply_city_list_changes(cities, campaign)

        mock_queue.assert_called_once_with(campaign.pk, city_indices=[1])

    @patch('campaigns.views.queue_city_fetches')
    def test_remove_and_add_fetches_only_new_city(self, mock_queue):
        cities = [_make_city('A', 1), _make_city('B', 2)]
        campaign = self._make_campaign_with_cities(cities)
        _make_fetch_job(campaign, 0, 'A')
        _make_fetch_job(campaign, 1, 'B')
        self._make_street(campaign, 101, 0)
        self._make_street(campaign, 102, 1)

        # Remove B, add C
        new_cities = [_make_city('A', 1), _make_city('C', 3)]
        campaign.cities = new_cities
        campaign.save()
        _apply_city_list_changes(cities, campaign)

        # B's street should be unlinked from this campaign (but Street object persists)
        self.assertFalse(CampaignStreet.objects.filter(campaign=campaign, street__osm_id=102).exists())
        self.assertTrue(Street.objects.filter(osm_id=102).exists())
        # Only C (new index 1) should be queued
        mock_queue.assert_called_once_with(campaign.pk, city_indices=[1])

    def test_no_changes_makes_no_modifications(self):
        cities = [_make_city('A', 1), _make_city('B', 2)]
        campaign = self._make_campaign_with_cities(cities)
        _make_fetch_job(campaign, 0, 'A')
        _make_fetch_job(campaign, 1, 'B')

        _apply_city_list_changes(cities, campaign)

        self.assertEqual(CityFetchJob.objects.filter(campaign=campaign).count(), 2)


# ── Fetch-status endpoint tests ───────────────────────────────────────────────

class FetchStatusEndpointTest(TestCase):
    """Tests for the manage_campaign_fetch_status JSON polling endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='manager', password='pw')
        self.campaign = make_campaign(
            slug='fetch-status-camp',
            status='draft',
            map_status='generating',
        )

    def _login(self):
        self.client.login(username='manager', password='pw')

    def _url(self):
        return f'/manage/{self.campaign.slug}/fetch-status/'

    # ── Auth guard ────────────────────────────────────────────────────────────

    def test_anonymous_redirects_to_login(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/manage/login/', resp['Location'])

    # ── Basic response shape ──────────────────────────────────────────────────

    def test_returns_200_json_for_authenticated_user(self):
        self._login()
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/json')

    def test_response_contains_required_top_level_keys(self):
        self._login()
        data = self.client.get(self._url()).json()
        for key in ('map_status', 'map_status_display', 'total_blocks', 'city_fetch_jobs'):
            self.assertIn(key, data)

    def test_map_status_matches_campaign(self):
        self._login()
        data = self.client.get(self._url()).json()
        self.assertEqual(data['map_status'], 'generating')

    # ── Per-city job data ─────────────────────────────────────────────────────

    def test_city_fetch_jobs_empty_when_no_jobs(self):
        self._login()
        data = self.client.get(self._url()).json()
        self.assertEqual(data['city_fetch_jobs'], [])

    def test_city_fetch_jobs_includes_each_job(self):
        CityFetchJob.objects.create(
            campaign=self.campaign,
            city_index=0,
            city_name='Springfield',
            status='generating',
        )
        CityFetchJob.objects.create(
            campaign=self.campaign,
            city_index=1,
            city_name='Shelbyville',
            status='pending',
        )
        self._login()
        data = self.client.get(self._url()).json()
        self.assertEqual(len(data['city_fetch_jobs']), 2)
        names = {j['city_name'] for j in data['city_fetch_jobs']}
        self.assertEqual(names, {'Springfield', 'Shelbyville'})

    def test_city_job_contains_required_fields(self):
        CityFetchJob.objects.create(
            campaign=self.campaign,
            city_index=0,
            city_name='Springfield',
            status='ready',
        )
        self._login()
        job = self.client.get(self._url()).json()['city_fetch_jobs'][0]
        for key in ('city_index', 'city_name', 'status', 'status_display', 'block_count', 'error'):
            self.assertIn(key, job, f'Missing key: {key}')

    def test_block_count_reflects_streets(self):
        CityFetchJob.objects.create(
            campaign=self.campaign,
            city_index=0,
            city_name='Springfield',
            status='ready',
        )
        # Create 3 streets tagged to city_index=0.
        for i in range(3):
            make_street(self.campaign, osm_id=1000 + i, name=f'Street {i}', block_index=i, city_index=0)
        self._login()
        data = self.client.get(self._url()).json()
        self.assertEqual(data['total_blocks'], 3)
        job = data['city_fetch_jobs'][0]
        self.assertEqual(job['block_count'], 3)

    def test_error_field_present_when_job_has_error(self):
        CityFetchJob.objects.create(
            campaign=self.campaign,
            city_index=0,
            city_name='Errorville',
            status='error',
            error='City not found in Nominatim',
        )
        self._login()
        job = self.client.get(self._url()).json()['city_fetch_jobs'][0]
        self.assertEqual(job['error'], 'City not found in Nominatim')

    def test_error_field_empty_string_when_no_error(self):
        CityFetchJob.objects.create(
            campaign=self.campaign,
            city_index=0,
            city_name='Happytown',
            status='ready',
            error='',
        )
        self._login()
        job = self.client.get(self._url()).json()['city_fetch_jobs'][0]
        self.assertEqual(job['error'], '')

    # ── Terminal states ───────────────────────────────────────────────────────

    def test_map_status_ready_returned_correctly(self):
        self.campaign.map_status = 'ready'
        self.campaign.save(update_fields=['map_status'])
        self._login()
        data = self.client.get(self._url()).json()
        self.assertEqual(data['map_status'], 'ready')

    def test_404_for_unknown_campaign(self):
        self._login()
        resp = self.client.get('/manage/no-such-campaign/fetch-status/')
        self.assertEqual(resp.status_code, 404)


# ── Watchdog tests ─────────────────────────────────────────────────────────────

class WatchdogStuckJobsTest(TestCase):
    """Tests for watchdog_stuck_jobs periodic task (issue #69)."""

    def setUp(self):
        self.campaign = make_campaign(slug='watchdog-test', cities=['Springfield', 'Shelbyville'])
        # A superuser with an email address, so watchdog notification tests work
        # without any override_settings — the task queries the DB for recipients.
        self.superuser = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='secret',
        )

    def _make_stuck_job(self, city_index=0, city_name='Springfield', minutes_old=None):
        """Create a CityFetchJob in 'generating' status with an old updated_at."""
        if minutes_old is None:
            minutes_old = STUCK_JOB_THRESHOLD_MINUTES + 10
        job = CityFetchJob.objects.create(
            campaign=self.campaign,
            city_index=city_index,
            city_name=city_name,
            status='generating',
        )
        # Backdating updated_at requires a direct DB update (auto_now ignores assignments).
        old_time = timezone.now() - timedelta(minutes=minutes_old)
        CityFetchJob.objects.filter(pk=job.pk).update(updated_at=old_time)
        return job

    # ── Detection ─────────────────────────────────────────────────────────────

    def test_returns_zero_when_no_stuck_jobs(self):
        result = watchdog_stuck_jobs()
        self.assertEqual(result['found'], 0)
        self.assertEqual(result['marked_error'], [])

    def test_detects_single_stuck_job(self):
        job = self._make_stuck_job()
        result = watchdog_stuck_jobs()
        self.assertEqual(result['found'], 1)
        self.assertIn(job.pk, result['marked_error'])

    def test_detects_multiple_stuck_jobs(self):
        job0 = self._make_stuck_job(city_index=0, city_name='Springfield')
        job1 = self._make_stuck_job(city_index=1, city_name='Shelbyville')
        result = watchdog_stuck_jobs()
        self.assertEqual(result['found'], 2)
        self.assertIn(job0.pk, result['marked_error'])
        self.assertIn(job1.pk, result['marked_error'])

    # ── Threshold boundary ────────────────────────────────────────────────────

    def test_recent_generating_job_not_flagged(self):
        """A job that just started (5 min ago) should not be touched."""
        CityFetchJob.objects.create(
            campaign=self.campaign,
            city_index=0,
            city_name='Springfield',
            status='generating',
        )
        # updated_at is auto_now — it's only seconds old, well within threshold.
        result = watchdog_stuck_jobs()
        self.assertEqual(result['found'], 0)

    def test_job_just_over_threshold_is_flagged(self):
        """A job updated exactly threshold+1 minutes ago should be caught."""
        job = self._make_stuck_job(minutes_old=STUCK_JOB_THRESHOLD_MINUTES + 1)
        result = watchdog_stuck_jobs()
        self.assertIn(job.pk, result['marked_error'])

    # ── Status transition ─────────────────────────────────────────────────────

    def test_stuck_job_marked_as_error(self):
        job = self._make_stuck_job()
        watchdog_stuck_jobs()
        job.refresh_from_db()
        self.assertEqual(job.status, 'error')

    def test_stuck_job_error_message_set(self):
        job = self._make_stuck_job()
        watchdog_stuck_jobs()
        job.refresh_from_db()
        self.assertIn('watchdog', job.error)
        self.assertTrue(len(job.error) > 0)

    def test_non_generating_jobs_not_touched(self):
        """Jobs in ready/error/pending should never be flagged."""
        for idx, status in enumerate(['ready', 'error', 'pending']):
            job = CityFetchJob.objects.create(
                campaign=self.campaign,
                city_index=idx,
                city_name=f'City{idx}',
                status=status,
            )
            old_time = timezone.now() - timedelta(minutes=STUCK_JOB_THRESHOLD_MINUTES + 60)
            CityFetchJob.objects.filter(pk=job.pk).update(updated_at=old_time)

        result = watchdog_stuck_jobs()
        self.assertEqual(result['found'], 0)

    # ── Campaign map_status sync ──────────────────────────────────────────────

    def test_campaign_map_status_updated_after_watchdog(self):
        """
        When the only generating job is marked error by the watchdog,
        campaign.map_status should transition out of 'generating'.
        """
        self.campaign.map_status = 'generating'
        self.campaign.save(update_fields=['map_status'])
        self._make_stuck_job()
        watchdog_stuck_jobs()
        self.campaign.refresh_from_db()
        # With one job now in 'error', map_status should be 'error' (not 'generating').
        self.assertNotEqual(self.campaign.map_status, 'generating')

    # ── Email notification ────────────────────────────────────────────────────
    # The watchdog emails active superusers (queried from the DB at runtime).
    # setUp already creates self.superuser with email='admin@example.com'.

    def test_no_email_sent_when_no_stuck_jobs(self):
        """Watchdog must not send email when everything is clean."""
        watchdog_stuck_jobs()
        self.assertEqual(len(mail.outbox), 0)

    def test_no_email_sent_when_no_superuser_has_email(self):
        """Watchdog should skip notification silently if no superuser has an email."""
        self.superuser.email = ''
        self.superuser.save(update_fields=['email'])
        self._make_stuck_job()
        watchdog_stuck_jobs()
        self.assertEqual(len(mail.outbox), 0)

    def test_email_sent_when_stuck_job_found(self):
        """At least one email should be sent when a stuck job is detected."""
        self._make_stuck_job()
        watchdog_stuck_jobs()
        self.assertEqual(len(mail.outbox), 1)

    def test_single_email_for_multiple_stuck_jobs(self):
        """All stuck jobs should be batched into one email, not one per job."""
        self._make_stuck_job(city_index=0, city_name='Springfield')
        self._make_stuck_job(city_index=1, city_name='Shelbyville')
        watchdog_stuck_jobs()
        self.assertEqual(len(mail.outbox), 1)

    def test_email_recipient_is_superuser(self):
        """Email should be addressed to the superuser's email."""
        self._make_stuck_job()
        watchdog_stuck_jobs()
        self.assertIn('admin@example.com', mail.outbox[0].to)

    def test_email_sent_to_all_superusers(self):
        """When multiple superusers exist, all receive the notification."""
        User.objects.create_superuser(
            username='admin2', email='admin2@example.com', password='secret',
        )
        self._make_stuck_job()
        watchdog_stuck_jobs()
        recipients = mail.outbox[0].to
        self.assertIn('admin@example.com', recipients)
        self.assertIn('admin2@example.com', recipients)

    def test_inactive_superuser_excluded(self):
        """An inactive superuser should not receive the notification."""
        self.superuser.is_active = False
        self.superuser.save(update_fields=['is_active'])
        self._make_stuck_job()
        watchdog_stuck_jobs()
        # No active superusers with email remain, so no email should be sent.
        self.assertEqual(len(mail.outbox), 0)

    def test_email_subject_contains_count(self):
        self._make_stuck_job()
        watchdog_stuck_jobs()
        self.assertIn('1', mail.outbox[0].subject)

    def test_email_subject_contains_watchdog(self):
        self._make_stuck_job()
        watchdog_stuck_jobs()
        self.assertIn('Watchdog', mail.outbox[0].subject)

    def test_email_body_contains_city_name(self):
        self._make_stuck_job(city_name='Springfield')
        watchdog_stuck_jobs()
        self.assertIn('Springfield', mail.outbox[0].body)

    def test_email_body_contains_campaign_slug(self):
        self._make_stuck_job()
        watchdog_stuck_jobs()
        self.assertIn(self.campaign.slug, mail.outbox[0].body)

    def test_email_body_contains_threshold(self):
        self._make_stuck_job()
        watchdog_stuck_jobs()
        self.assertIn(str(STUCK_JOB_THRESHOLD_MINUTES), mail.outbox[0].body)

    def test_email_body_lists_all_stuck_jobs(self):
        """When two jobs are stuck, both city names should appear in the email body."""
        self._make_stuck_job(city_index=0, city_name='Springfield')
        self._make_stuck_job(city_index=1, city_name='Shelbyville')
        watchdog_stuck_jobs()
        body = mail.outbox[0].body
        self.assertIn('Springfield', body)
        self.assertIn('Shelbyville', body)

    # ── Stuck-rendering watchdog ──────────────────────────────────────────────

    def _make_stuck_rendering_campaign(self, minutes_old=None):
        """Return a campaign stuck in 'rendering' state past the threshold."""
        if minutes_old is None:
            minutes_old = STUCK_JOB_THRESHOLD_MINUTES + 10
        campaign = make_campaign(slug=f'rendering-stuck-{minutes_old}', map_status='rendering')
        old_time = timezone.now() - timedelta(minutes=minutes_old)
        Campaign.objects.filter(pk=campaign.pk).update(updated_at=old_time)
        return campaign

    def test_returns_requeued_rendering_key(self):
        result = watchdog_stuck_jobs()
        self.assertIn('requeued_rendering', result)

    def test_no_stuck_rendering_when_clean(self):
        result = watchdog_stuck_jobs()
        self.assertEqual(result['requeued_rendering'], [])

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_detects_campaign_stuck_in_rendering(self, mock_render):
        campaign = self._make_stuck_rendering_campaign()
        result = watchdog_stuck_jobs()
        self.assertIn(campaign.pk, result['requeued_rendering'])

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_redispatches_render_for_stuck_rendering_campaign(self, mock_render):
        campaign = self._make_stuck_rendering_campaign()
        watchdog_stuck_jobs()
        mock_render.delay.assert_called_once_with(campaign.pk, final_status='ready')

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_recent_rendering_campaign_not_flagged(self, mock_render):
        """A campaign that just started rendering should not be re-queued."""
        campaign = make_campaign(slug='rendering-recent', map_status='rendering')
        # updated_at is auto_now — only seconds old, well within threshold
        result = watchdog_stuck_jobs()
        self.assertNotIn(campaign.pk, result['requeued_rendering'])
        mock_render.delay.assert_not_called()

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_email_sent_for_stuck_rendering_campaign(self, mock_render):
        self._make_stuck_rendering_campaign()
        watchdog_stuck_jobs()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('rendering', mail.outbox[0].subject.lower())

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_email_body_contains_campaign_slug_for_stuck_rendering(self, mock_render):
        campaign = self._make_stuck_rendering_campaign()
        watchdog_stuck_jobs()
        self.assertIn(campaign.slug, mail.outbox[0].body)


# ── Task tests: render_campaign_geojson ───────────────────────────────────────

class RenderCampaignGeoJSONTaskTest(TestCase):
    """Tests for the async render_campaign_geojson Celery task (issue #78)."""

    def setUp(self):
        self.campaign = make_campaign(slug='render-camp', map_status='rendering')
        make_street(self.campaign, osm_id=1001, name='Elm St')

    def test_sets_streets_geojson_on_success(self):
        render_campaign_geojson(self.campaign.pk, final_status='ready')
        self.campaign.refresh_from_db()
        self.assertTrue(len(self.campaign.streets_geojson) > 0)
        data = json.loads(self.campaign.streets_geojson)
        self.assertEqual(data['type'], 'FeatureCollection')

    def test_sets_map_status_to_final_status_ready(self):
        render_campaign_geojson(self.campaign.pk, final_status='ready')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'ready')

    def test_sets_map_status_to_final_status_warning(self):
        render_campaign_geojson(self.campaign.pk, final_status='warning')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'warning')

    def test_sets_map_status_error_on_exception(self):
        with patch('campaigns.tasks._write_streets_geojson_chunked', side_effect=RuntimeError('OOM')):
            with self.assertRaises(RuntimeError):
                render_campaign_geojson(self.campaign.pk, final_status='ready')
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')
        self.assertIn('OOM', self.campaign.map_error)

    def test_silently_does_nothing_for_missing_campaign(self):
        # Should log an error but not raise.
        render_campaign_geojson(99999, final_status='ready')  # no exception expected

    def test_geojson_respects_geo_limit(self):
        """When campaign has a geo_limit, only streets within it appear in geojson."""
        from django.contrib.gis.geos import Polygon as GeosPoly
        # Tight box that does NOT contain GEOM (which spans -122.1 to -122.15, 37.4 to 37.45)
        outside_box = GeosPoly.from_bbox((-100.0, 10.0, -99.0, 11.0))
        outside_box.srid = 4326
        self.campaign.geo_limit = outside_box
        self.campaign.save(update_fields=['geo_limit'])
        render_campaign_geojson(self.campaign.pk, final_status='ready')
        self.campaign.refresh_from_db()
        data = json.loads(self.campaign.streets_geojson)
        self.assertEqual(data['features'], [])


# ── Task tests: _write_streets_geojson_chunked ───────────────────────────────

class WriteStreetsGeoJSONChunkedTest(TestCase):
    """Tests for the chunked DB writer that avoids max_allowed_packet errors."""

    def setUp(self):
        self.campaign = make_campaign(slug='chunked-camp', map_status='rendering')

    def test_writes_valid_feature_collection(self):
        """A campaign with streets gets a valid GeoJSON FeatureCollection written."""
        make_street(self.campaign, osm_id=1001, name='Elm St')
        make_street(self.campaign, osm_id=1002, name='Oak Ave', geometry=GEOM2, block_index=0)
        _write_streets_geojson_chunked(self.campaign.pk)
        self.campaign.refresh_from_db()
        data = json.loads(self.campaign.streets_geojson)
        self.assertEqual(data['type'], 'FeatureCollection')
        self.assertEqual(len(data['features']), 2)

    def test_feature_count_matches_street_count(self):
        """Each street produces exactly one Feature."""
        n = 7
        for i in range(n):
            make_street(self.campaign, osm_id=2000 + i, name=f'Street {i}', block_index=i)
        _write_streets_geojson_chunked(self.campaign.pk)
        self.campaign.refresh_from_db()
        data = json.loads(self.campaign.streets_geojson)
        self.assertEqual(len(data['features']), n)

    def test_empty_campaign_writes_empty_feature_collection(self):
        """A campaign with no streets gets an empty FeatureCollection."""
        _write_streets_geojson_chunked(self.campaign.pk)
        self.campaign.refresh_from_db()
        data = json.loads(self.campaign.streets_geojson)
        self.assertEqual(data['type'], 'FeatureCollection')
        self.assertEqual(data['features'], [])

    def test_chunking_produces_same_result_as_single_chunk(self):
        """chunk_size=1 (one SQL CONCAT per feature) yields identical feature count."""
        n = 5
        for i in range(n):
            make_street(self.campaign, osm_id=3000 + i, name=f'Road {i}', block_index=i)
        _write_streets_geojson_chunked(self.campaign.pk, chunk_size=1)
        self.campaign.refresh_from_db()
        data = json.loads(self.campaign.streets_geojson)
        self.assertEqual(data['type'], 'FeatureCollection')
        self.assertEqual(len(data['features']), n)

    def test_geo_limit_filters_streets(self):
        """Only streets intersecting geo_limit are included."""
        from django.contrib.gis.geos import Polygon as GeosPoly
        make_street(self.campaign, osm_id=4001, name='Inside St')  # GEOM is at ~-122/37
        outside_box = GeosPoly.from_bbox((-100.0, 10.0, -99.0, 11.0))
        outside_box.srid = 4326
        _write_streets_geojson_chunked(self.campaign.pk, geo_limit=outside_box)
        self.campaign.refresh_from_db()
        data = json.loads(self.campaign.streets_geojson)
        self.assertEqual(data['features'], [])

    def test_chunk_size_one_writes_correct_features(self):
        """chunk_size=1 exercises one CONCAT per feature; result must still be valid JSON."""
        n = 4
        for i in range(n):
            make_street(self.campaign, osm_id=5000 + i, name=f'Blvd {i}', block_index=i)
        _write_streets_geojson_chunked(self.campaign.pk, chunk_size=1)
        self.campaign.refresh_from_db()
        data = json.loads(self.campaign.streets_geojson)
        self.assertEqual(data['type'], 'FeatureCollection')
        self.assertEqual(len(data['features']), n)


# ── Task tests: _sync_campaign_map_status dispatches render task ──────────────

class SyncCampaignMapStatusRenderTest(TestCase):
    """Verify _sync_campaign_map_status sets map_status to 'ready'/'warning' but
    does NOT dispatch render_campaign_geojson.  Rendering is deferred until the
    manager saves a geo_limit boundary via manage_campaign_update_geo_limit."""

    def setUp(self):
        self.campaign = make_campaign(slug='sync-render-camp', map_status='generating')
        make_street(self.campaign, osm_id=2001, name='Oak Ave')

    def _make_ready_job(self, city_index=0, city_name='Testville', status='ready'):
        return CityFetchJob.objects.create(
            campaign=self.campaign, city_index=city_index,
            city_name=city_name, status=status,
        )

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_does_not_dispatch_render_when_all_cities_ready(self, mock_render):
        mock_render.delay = MagicMock()
        self._make_ready_job(status='ready')
        _sync_campaign_map_status(self.campaign.pk)
        mock_render.delay.assert_not_called()

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_sets_map_status_ready_when_all_cities_ready(self, mock_render):
        mock_render.delay = MagicMock()
        self._make_ready_job(status='ready')
        _sync_campaign_map_status(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'ready')

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_does_not_dispatch_render_with_warning_when_some_cities_error(self, mock_render):
        mock_render.delay = MagicMock()
        self._make_ready_job(city_index=0, city_name='Good City', status='ready')
        CityFetchJob.objects.create(
            campaign=self.campaign, city_index=1, city_name='Bad City',
            status='error', error='Not found',
        )
        _sync_campaign_map_status(self.campaign.pk)
        mock_render.delay.assert_not_called()

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_sets_map_status_warning_when_some_cities_error(self, mock_render):
        mock_render.delay = MagicMock()
        self._make_ready_job(city_index=0, city_name='Good City', status='ready')
        CityFetchJob.objects.create(
            campaign=self.campaign, city_index=1, city_name='Bad City',
            status='error', error='Not found',
        )
        _sync_campaign_map_status(self.campaign.pk)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'warning')

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_does_not_dispatch_render_when_still_generating(self, mock_render):
        mock_render.delay = MagicMock()
        CityFetchJob.objects.create(
            campaign=self.campaign, city_index=0, city_name='Pending City', status='pending',
        )
        _sync_campaign_map_status(self.campaign.pk)
        mock_render.delay.assert_not_called()

    @patch('campaigns.tasks.render_campaign_geojson')
    def test_does_not_dispatch_render_when_all_cities_error(self, mock_render):
        mock_render.delay = MagicMock()
        CityFetchJob.objects.create(
            campaign=self.campaign, city_index=0, city_name='Bad City',
            status='error', error='Not found',
        )
        _sync_campaign_map_status(self.campaign.pk)
        mock_render.delay.assert_not_called()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'error')


# ── View tests: manage_campaign_update_geo_limit async ───────────────────────

class ManageCampaignUpdateGeoLimitAsyncTest(TestCase):
    """Verify that manage_campaign_update_geo_limit returns 'rendering' and
    dispatches render_campaign_geojson instead of building GeoJSON inline."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='geo_manager', password='pw')
        self.campaign = make_campaign(
            slug='geo-async-camp', status='draft', map_status='ready',
            bbox=[[-122.2, 37.3], [-122.0, 37.5]],
        )
        make_street(self.campaign, osm_id=3001, name='Pine St')

    def _login(self):
        self.client.login(username='geo_manager', password='pw')

    def _post_polygon(self, slug=None):
        slug = slug or self.campaign.slug
        polygon = {
            'type': 'Polygon',
            'coordinates': [[
                [-122.15, 37.35],
                [-122.05, 37.35],
                [-122.05, 37.45],
                [-122.15, 37.45],
                [-122.15, 37.35],
            ]],
        }
        return self.client.post(
            f'/manage/{slug}/update-geo-limit/',
            data=json.dumps(polygon),
            content_type='application/json',
        )

    @patch('campaigns.views.render_campaign_geojson')
    def test_returns_rendering_status(self, mock_render):
        mock_render.delay = MagicMock()
        self._login()
        resp = self._post_polygon()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'rendering')

    @patch('campaigns.views.render_campaign_geojson')
    def test_returns_bbox_in_response(self, mock_render):
        mock_render.delay = MagicMock()
        self._login()
        resp = self._post_polygon()
        data = resp.json()
        self.assertIn('bbox', data)

    @patch('campaigns.views.render_campaign_geojson')
    def test_dispatches_render_task(self, mock_render):
        mock_render.delay = MagicMock()
        self._login()
        self._post_polygon()
        mock_render.delay.assert_called_once_with(self.campaign.pk, final_status='ready')

    @patch('campaigns.views.render_campaign_geojson')
    def test_sets_map_status_rendering_in_db(self, mock_render):
        mock_render.delay = MagicMock()
        self._login()
        self._post_polygon()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.map_status, 'rendering')

    @patch('campaigns.views.render_campaign_geojson')
    def test_clears_streets_geojson_in_db(self, mock_render):
        mock_render.delay = MagicMock()
        self.campaign.streets_geojson = '{"type":"FeatureCollection","features":[]}'
        self.campaign.save(update_fields=['streets_geojson'])
        self._login()
        self._post_polygon()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.streets_geojson, '')

    @patch('campaigns.views.render_campaign_geojson')
    def test_does_not_call_build_streets_geojson_inline(self, mock_render):
        mock_render.delay = MagicMock()
        self._login()
        with patch('campaigns.views.build_streets_geojson') as mock_build:
            self._post_polygon()
            mock_build.assert_not_called()


# ── run_task management command ───────────────────────────────────────────────

from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError


class RunTaskCommandTest(TestCase):
    """Tests for the run_task management command."""

    def test_list_shows_app_tasks(self):
        out = StringIO()
        call_command('run_task', '--list', stdout=out)
        output = out.getvalue()
        self.assertIn('campaigns.tasks.watchdog_stuck_jobs', output)
        self.assertIn('campaigns.tasks.fetch_city_osm_data', output)
        self.assertIn('campaigns.tasks.render_campaign_geojson', output)

    def test_list_excludes_celery_internals(self):
        """Internal celery.* tasks are not listed as selectable items."""
        out = StringIO()
        call_command('run_task', '--list', stdout=out)
        lines = out.getvalue().splitlines()
        # Only lines starting with two spaces (the indented task listing) are checked;
        # the summary footer may mention celery.* in passing.
        task_lines = [ln for ln in lines if ln.startswith('  ')]
        for line in task_lines:
            self.assertFalse(line.strip().startswith('celery.'),
                             f'Internal task listed: {line}')

    def test_no_task_name_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('run_task')
        self.assertIn('task name', str(ctx.exception).lower())

    def test_unknown_task_name_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('run_task', 'campaigns.tasks.does_not_exist')
        self.assertIn('not found', str(ctx.exception))

    def test_unknown_task_suggests_matches(self):
        """A partial name match in the error message helps the user find the right task."""
        err = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command('run_task', 'campaigns.tasks.watchdog_unknown', stderr=err)
        # 'watchdog_stuck_jobs' contains 'watchdog' — should appear as suggestion
        self.assertIn('watchdog', str(ctx.exception))

    def test_run_watchdog_inline(self):
        """Watchdog task runs successfully with no stuck jobs."""
        out = StringIO()
        call_command('run_task', 'campaigns.tasks.watchdog_stuck_jobs', stdout=out)
        output = out.getvalue()
        self.assertIn('completed successfully', output)

    def test_run_watchdog_returns_result(self):
        """Return value dict is printed when task succeeds."""
        out = StringIO()
        call_command('run_task', 'campaigns.tasks.watchdog_stuck_jobs', stdout=out)
        # Result contains 'found' and 'marked_error' keys
        self.assertIn('found', out.getvalue())

    def test_positional_int_arg_decoded(self):
        """Integer CLI args are JSON-decoded so tasks receive the correct type."""
        out = StringIO()
        # Campaign pk 99999 doesn't exist — task logs a warning and returns cleanly
        call_command('run_task', 'campaigns.tasks.fetch_city_osm_data', '99999', '0', stdout=out)
        self.assertIn('completed successfully', out.getvalue())

    def test_invalid_kwargs_json_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('run_task', 'campaigns.tasks.watchdog_stuck_jobs',
                         kwargs='not-valid-json')
        self.assertIn('not valid JSON', str(ctx.exception))

    @patch('campaigns.tasks.watchdog_stuck_jobs.apply_async')
    def test_async_flag_calls_apply_async(self, mock_apply_async):
        mock_result = MagicMock()
        mock_result.id = 'test-task-id-123'
        mock_apply_async.return_value = mock_result

        out = StringIO()
        call_command('run_task', 'campaigns.tasks.watchdog_stuck_jobs',
                     use_async=True, stdout=out)
        mock_apply_async.assert_called_once_with(args=[], kwargs={})
        self.assertIn('test-task-id-123', out.getvalue())


# ── ImageUploadForm ────────────────────────────────────────────────────────────

class ImageUploadFormTest(TestCase):
    def _make_file(self, name, size, content_type='image/jpeg'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, b'x' * size, content_type=content_type)

    def _valid_data(self):
        return {'attest_rights': True, 'attest_content': True}

    def test_accepts_image_under_limit(self):
        f = self._make_file('photo.jpg', 512 * 1024)
        form = ImageUploadForm(data=self._valid_data(), files={'image': f})
        self.assertTrue(form.is_valid())

    def test_accepts_image_previously_over_old_2mb_limit(self):
        """Images up to 20 MB are now accepted (server-side resize handles large uploads)."""
        f = self._make_file('medium.jpg', 5 * 1024 * 1024)
        form = ImageUploadForm(data=self._valid_data(), files={'image': f})
        self.assertTrue(form.is_valid())

    def test_rejects_image_over_20mb(self):
        f = self._make_file('huge.jpg', 21 * 1024 * 1024)
        form = ImageUploadForm(data=self._valid_data(), files={'image': f})
        self.assertFalse(form.is_valid())
        self.assertIn('20 MB', str(form.errors['image']))

    def test_accepts_exactly_20mb(self):
        f = self._make_file('exact.jpg', 20 * 1024 * 1024)
        form = ImageUploadForm(data=self._valid_data(), files={'image': f})
        self.assertTrue(form.is_valid())


# ── _resize_hero_image ─────────────────────────────────────────────────────────

class ResizeHeroImageTest(TestCase):
    """Tests for _resize_hero_image() in views.py."""

    def _make_jpeg(self, width, height):
        """Return an InMemoryUploadedFile containing a real JPEG of the given size."""
        from PIL import Image
        from django.core.files.uploadedfile import InMemoryUploadedFile
        import io as _io
        img = Image.new('RGB', (width, height), color=(100, 150, 200))
        buf = _io.BytesIO()
        img.save(buf, format='JPEG', quality=80)
        buf.seek(0)
        return InMemoryUploadedFile(buf, 'image', 'test.jpg', 'image/jpeg', buf.getbuffer().nbytes, None)

    def _make_png_rgba(self, width, height):
        from PIL import Image
        from django.core.files.uploadedfile import InMemoryUploadedFile
        import io as _io
        img = Image.new('RGBA', (width, height), color=(100, 150, 200, 128))
        buf = _io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return InMemoryUploadedFile(buf, 'image', 'test.png', 'image/png', buf.getbuffer().nbytes, None)

    def _open_result(self, result):
        from PIL import Image
        result.seek(0)
        return Image.open(result)

    def test_small_image_passes_through(self):
        f = self._make_jpeg(800, 600)
        result = _resize_hero_image(f)
        img = self._open_result(result)
        self.assertEqual(img.size, (800, 600))

    def test_wide_image_is_resized_to_max_width(self):
        f = self._make_jpeg(3840, 2160)
        result = _resize_hero_image(f)
        img = self._open_result(result)
        self.assertLessEqual(img.width, 1920)
        self.assertLessEqual(img.height, 1080)

    def test_aspect_ratio_preserved(self):
        # 4:3 image that is too wide
        f = self._make_jpeg(2400, 1800)
        result = _resize_hero_image(f)
        img = self._open_result(result)
        self.assertAlmostEqual(img.width / img.height, 4 / 3, delta=0.02)

    def test_tall_image_bounded_by_height(self):
        # Taller than max_height, not wider than max_width
        f = self._make_jpeg(800, 2200)
        result = _resize_hero_image(f)
        img = self._open_result(result)
        self.assertLessEqual(img.height, 1080)

    def test_png_rgba_output_is_png(self):
        f = self._make_png_rgba(100, 100)
        result = _resize_hero_image(f)
        result.seek(0)
        from PIL import Image
        img = Image.open(result)
        self.assertEqual(img.format, 'PNG')

    def test_returns_inmemory_file(self):
        from django.core.files.uploadedfile import InMemoryUploadedFile
        f = self._make_jpeg(3000, 2000)
        result = _resize_hero_image(f)
        self.assertIsInstance(result, InMemoryUploadedFile)


# ── Database backup tests ──────────────────────────────────────────────────────

class BackupDatabaseTaskTest(TestCase):
    """Tests for the backup_database Celery task and helpers (issue #109)."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='backupadmin',
            email='backupadmin@example.com',
            password='secret',
        )

    # ── _run_backup: happy path ──────────────────────────────────────────────

    @patch('campaigns.tasks.boto3.client')
    @patch('campaigns.tasks.subprocess.run')
    def test_run_backup_uploads_to_s3(self, mock_run, mock_boto_client):
        """_run_backup should call put_object with a gzip-compressed SQL dump."""
        mock_run.return_value = MagicMock(stdout=b'-- SQL dump content --', returncode=0)
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_s3.get_paginator.return_value.paginate.return_value = [{'Contents': []}]

        result = _run_backup()

        self.assertIn('key', result)
        self.assertTrue(result['key'].startswith('backups/leafletter-'))
        self.assertTrue(result['key'].endswith('.sql.gz'))
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        self.assertEqual(call_kwargs['ContentType'], 'application/gzip')

    @patch('campaigns.tasks.boto3.client')
    @patch('campaigns.tasks.subprocess.run')
    def test_run_backup_body_is_gzip(self, mock_run, mock_boto_client):
        """Uploaded body must be valid gzip data."""
        import gzip as gz_module
        sql_content = b'SELECT 1;'
        mock_run.return_value = MagicMock(stdout=sql_content, returncode=0)
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_s3.get_paginator.return_value.paginate.return_value = [{'Contents': []}]

        _run_backup()

        body = mock_s3.put_object.call_args[1]['Body']
        decompressed = gz_module.decompress(body)
        self.assertEqual(decompressed, sql_content)

    @patch('campaigns.tasks.boto3.client')
    @patch('campaigns.tasks.subprocess.run')
    def test_run_backup_uses_backup_bucket_env(self, mock_run, mock_boto_client):
        """BACKUP_S3_BUCKET env var should override default bucket name."""
        mock_run.return_value = MagicMock(stdout=b'-- dump --', returncode=0)
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_s3.get_paginator.return_value.paginate.return_value = [{'Contents': []}]

        with patch.dict('os.environ', {'BACKUP_S3_BUCKET': 'my-backup-bucket'}):
            _run_backup()

        call_kwargs = mock_s3.put_object.call_args[1]
        self.assertEqual(call_kwargs['Bucket'], 'my-backup-bucket')

    @patch('campaigns.tasks.boto3.client')
    @patch('campaigns.tasks.subprocess.run')
    def test_run_backup_returns_pruned_count(self, mock_run, mock_boto_client):
        """Result dict should include the count of pruned old backups."""
        mock_run.return_value = MagicMock(stdout=b'-- dump --', returncode=0)
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_s3.get_paginator.return_value.paginate.return_value = [{'Contents': []}]

        result = _run_backup()

        self.assertIn('pruned', result)
        self.assertIsInstance(result['pruned'], int)

    # ── _run_backup: mysqldump failure ───────────────────────────────────────

    @patch('campaigns.tasks.subprocess.run')
    def test_run_backup_raises_on_mysqldump_failure(self, mock_run):
        """If mysqldump exits non-zero, _run_backup should propagate the exception."""
        mock_run.side_effect = subprocess.CalledProcessError(1, 'mysqldump', stderr=b'Access denied')
        with self.assertRaises(subprocess.CalledProcessError):
            _run_backup()

    # ── backup_database task: failure → email, no re-raise ──────────────────

    @patch('campaigns.tasks._run_backup')
    def test_task_emails_superusers_on_failure(self, mock_run_backup):
        """On _run_backup failure the task should email superusers and not re-raise."""
        mock_run_backup.side_effect = RuntimeError('S3 unreachable')

        result = backup_database()

        self.assertIn('error', result)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('backupadmin@example.com', mail.outbox[0].to)

    @patch('campaigns.tasks._run_backup')
    def test_task_does_not_raise_on_failure(self, mock_run_backup):
        """backup_database must never propagate exceptions."""
        mock_run_backup.side_effect = Exception('boom')
        # Should not raise:
        result = backup_database()
        self.assertIn('error', result)

    @patch('campaigns.tasks._run_backup')
    def test_task_no_email_on_success(self, mock_run_backup):
        """No email should be sent when the backup succeeds."""
        mock_run_backup.return_value = {'key': 'backups/leafletter-2026-01-01-020000.sql.gz', 'pruned': 0}

        backup_database()

        self.assertEqual(len(mail.outbox), 0)

    @patch('campaigns.tasks._run_backup')
    def test_task_no_email_when_no_superuser_has_email(self, mock_run_backup):
        """Failure notification silently skipped when no superuser has an email."""
        self.superuser.email = ''
        self.superuser.save(update_fields=['email'])
        mock_run_backup.side_effect = RuntimeError('boom')

        backup_database()

        self.assertEqual(len(mail.outbox), 0)

    # ── _prune_old_backups ────────────────────────────────────────────────────

    def test_prune_deletes_old_objects(self):
        """Objects older than retention_days should be deleted."""
        from datetime import datetime, timezone as dt_timezone
        mock_s3 = MagicMock()
        old_time = datetime(2020, 1, 1, tzinfo=dt_timezone.utc)
        mock_s3.get_paginator.return_value.paginate.return_value = [{
            'Contents': [
                {'Key': 'backups/old.sql.gz', 'LastModified': old_time},
            ]
        }]

        count = _prune_old_backups(mock_s3, 'test-bucket', retention_days=30)

        self.assertEqual(count, 1)
        mock_s3.delete_objects.assert_called_once()
        deleted_keys = mock_s3.delete_objects.call_args[1]['Delete']['Objects']
        self.assertEqual(deleted_keys, [{'Key': 'backups/old.sql.gz'}])

    def test_prune_keeps_recent_objects(self):
        """Objects newer than retention_days must not be deleted."""
        from datetime import datetime, timezone as dt_timezone
        mock_s3 = MagicMock()
        recent_time = datetime.now(dt_timezone.utc)
        mock_s3.get_paginator.return_value.paginate.return_value = [{
            'Contents': [
                {'Key': 'backups/recent.sql.gz', 'LastModified': recent_time},
            ]
        }]

        count = _prune_old_backups(mock_s3, 'test-bucket', retention_days=30)

        self.assertEqual(count, 0)
        mock_s3.delete_objects.assert_not_called()

    def test_prune_returns_zero_when_nothing_to_delete(self):
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [{'Contents': []}]

        count = _prune_old_backups(mock_s3, 'test-bucket', retention_days=30)

        self.assertEqual(count, 0)

    # ── Management command ────────────────────────────────────────────────────

    @patch('campaigns.tasks._run_backup')
    def test_management_command_success(self, mock_run_backup):
        """backup_database management command should succeed and print the key."""
        from django.core.management import call_command
        import io as _io
        mock_run_backup.return_value = {
            'key': 'backups/leafletter-2026-01-01-020000.sql.gz',
            'pruned': 2,
        }
        out = _io.StringIO()
        call_command('backup_database', stdout=out)
        output = out.getvalue()
        self.assertIn('backups/leafletter', output)
        self.assertIn('2', output)  # pruned count

    @patch('campaigns.tasks._run_backup')
    def test_management_command_failure_exits_nonzero(self, mock_run_backup):
        """backup_database management command should exit with SystemExit on failure."""
        from django.core.management import call_command
        mock_run_backup.side_effect = RuntimeError('connection refused')
        with self.assertRaises(SystemExit):
            call_command('backup_database')

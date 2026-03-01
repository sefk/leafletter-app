"""
Tests for the campaigns app.

Run with:
    python manage.py test campaigns
"""
import json
import uuid
from unittest.mock import MagicMock, patch

from django.contrib import admin as django_admin
from django.contrib.gis.geos import LineString
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError
from django.test import Client, RequestFactory, TestCase

from .admin import CampaignAdmin, MAP_STATUS_COLORS
from .models import Campaign, Street, Trip
from .tasks import fetch_osm_segments, find_intersection_nodes, query_overpass, split_way_at_intersections

# ── Shared test geometry ──────────────────────────────────────────────────────

GEOM = LineString((-122.1, 37.4), (-122.15, 37.45), srid=4326)
GEOM2 = LineString((-122.2, 37.5), (-122.25, 37.55), srid=4326)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_campaign(slug='test-campaign', status='published', **kwargs):
    defaults = dict(
        name='Test Campaign',
        slug=slug,
        goal='Get out the vote',
        cities=['Palo Alto'],
        status=status,
    )
    defaults.update(kwargs)
    return Campaign.objects.create(**defaults)


def make_street(campaign, osm_id=1001, name='Main St', geometry=None, block_index=0):
    return Street.objects.create(
        campaign=campaign,
        osm_id=osm_id,
        name=name,
        geometry=geometry or GEOM,
        block_index=block_index,
    )


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
        c = Campaign.objects.create(name='Draft', slug='draft-x', goal='g', cities=['x'])
        self.assertEqual(c.status, 'draft')

    def test_default_map_status_is_pending(self):
        c = Campaign.objects.create(name='Draft', slug='draft-y', goal='g', cities=['x'])
        self.assertEqual(c.map_status, 'pending')

    def test_slug_is_unique(self):
        make_campaign(slug='unique-slug')
        with self.assertRaises(Exception):
            make_campaign(slug='unique-slug')


class StreetModelTest(TestCase):

    def setUp(self):
        self.campaign = make_campaign()

    def test_str_with_name(self):
        s = Street(campaign=self.campaign, osm_id=123, name='Oak Ave', geometry=GEOM)
        self.assertEqual(str(s), 'Oak Ave (123 block 0)')

    def test_str_without_name(self):
        s = Street(campaign=self.campaign, osm_id=456, name='', geometry=GEOM)
        self.assertEqual(str(s), 'Unnamed (456 block 0)')

    def test_unique_together_campaign_osm_id_block_index(self):
        make_street(self.campaign, osm_id=999, block_index=0)
        with self.assertRaises(IntegrityError):
            make_street(self.campaign, osm_id=999, block_index=0)

    def test_multiple_blocks_same_osm_id_allowed(self):
        make_street(self.campaign, osm_id=999, block_index=0)
        # Different block_index for same osm_id should not raise
        make_street(self.campaign, osm_id=999, block_index=1, geometry=GEOM2)

    def test_same_osm_id_allowed_across_campaigns(self):
        other = make_campaign(slug='other-camp')
        make_street(self.campaign, osm_id=777)
        # Should not raise:
        make_street(other, osm_id=777)


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

    def test_draft_campaign_returns_404(self):
        make_campaign(slug='draft-v', status='draft')
        resp = self.client.get('/c/draft-v/')
        self.assertEqual(resp.status_code, 404)

    def test_deleted_campaign_returns_404(self):
        make_campaign(slug='del-v', status='deleted')
        resp = self.client.get('/c/del-v/')
        self.assertEqual(resp.status_code, 404)

    def test_unknown_slug_returns_404(self):
        resp = self.client.get('/c/does-not-exist/')
        self.assertEqual(resp.status_code, 404)


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

    def test_deduplicates_street_covered_by_multiple_trips(self):
        make_trip(self.campaign, streets=[self.street1], worker_name='Alice')
        make_trip(self.campaign, streets=[self.street1], worker_name='Bob')
        data = self.client.get('/c/cov-camp/coverage.geojson').json()
        self.assertEqual(len(data['features']), 1)

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
        mock_qo.return_value = []
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

    @patch('campaigns.admin.fetch_osm_segments')
    def test_publish_action_sets_status_and_queues_task(self, mock_task):
        c = make_campaign(slug='to-publish', status='draft')
        self.ma.publish_campaigns(self.request, Campaign.objects.filter(pk=c.pk))
        c.refresh_from_db()
        self.assertEqual(c.status, 'published')
        mock_task.delay.assert_called_once_with(c.pk)

    @patch('campaigns.admin.fetch_osm_segments')
    def test_publish_action_resets_map_status_to_pending(self, mock_task):
        c = make_campaign(slug='repub', status='draft', map_status='error')
        self.ma.publish_campaigns(self.request, Campaign.objects.filter(pk=c.pk))
        c.refresh_from_db()
        self.assertEqual(c.map_status, 'pending')

    @patch('campaigns.admin.fetch_osm_segments')
    def test_publish_action_skips_deleted_campaigns(self, mock_task):
        c = make_campaign(slug='skip-deleted', status='deleted')
        self.ma.publish_campaigns(self.request, Campaign.objects.filter(pk=c.pk))
        mock_task.delay.assert_not_called()

    @patch('campaigns.admin.fetch_osm_segments')
    def test_response_change_publish_button_sets_published_and_queues_task(self, mock_task):
        c = make_campaign(slug='btn-pub', status='draft')
        request = make_admin_request('post')
        request.POST = {'_publish': '1'}
        self.ma.response_change(request, c)
        c.refresh_from_db()
        self.assertEqual(c.status, 'published')
        self.assertEqual(c.map_status, 'pending')
        mock_task.delay.assert_called_once_with(c.pk)

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_does_not_queue_task_when_publish_button_used(self, mock_task):
        """Prevents double-triggering when Publish button submitted."""
        c = make_campaign(slug='no-double', status='draft')
        c.status = 'published'
        request = make_admin_request('post')
        request.POST = {'_publish': '1'}
        form = MagicMock()
        self.ma.save_model(request, c, form, change=True)
        mock_task.delay.assert_not_called()

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_queues_task_when_creating_published_campaign(self, mock_task):
        form = MagicMock()
        c = Campaign(name='New Camp', slug='new-camp', goal='g', cities=['x'], status='published')
        self.ma.save_model(self.request, c, form, change=False)
        mock_task.delay.assert_called_once_with(c.pk)

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_does_not_queue_task_for_draft_on_create(self, mock_task):
        form = MagicMock()
        c = Campaign(name='Draft Camp', slug='draft-new', goal='g', cities=['x'], status='draft')
        self.ma.save_model(self.request, c, form, change=False)
        mock_task.delay.assert_not_called()

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_queues_task_when_editing_draft_to_published(self, mock_task):
        c = make_campaign(slug='edit-to-pub', status='draft')
        c.status = 'published'
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.delay.assert_called_once_with(c.pk)

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_does_not_requeue_task_for_already_published(self, mock_task):
        c = make_campaign(slug='already-pub', status='published')
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.delay.assert_not_called()

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_requeues_task_when_cities_change_on_published(self, mock_task):
        c = make_campaign(slug='cities-change', status='published', cities=['Palo Alto'])
        c.cities = ['Palo Alto', 'Menlo Park']
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.delay.assert_called_once_with(c.pk)

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_resets_map_status_to_pending_when_cities_change(self, mock_task):
        c = make_campaign(slug='cities-pending', status='published', cities=['Palo Alto'])
        c.cities = ['Menlo Park']
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        c.refresh_from_db()
        self.assertEqual(c.map_status, 'pending')

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_does_not_requeue_when_cities_unchanged(self, mock_task):
        c = make_campaign(slug='cities-same', status='published', cities=['Palo Alto'])
        c.cities = ['Palo Alto']  # same value
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.delay.assert_not_called()

    @patch('campaigns.admin.fetch_osm_segments')
    def test_save_model_does_not_requeue_cities_change_on_draft(self, mock_task):
        c = make_campaign(slug='cities-draft', status='draft', cities=['Palo Alto'])
        c.cities = ['Menlo Park']
        form = MagicMock()
        self.ma.save_model(self.request, c, form, change=True)
        mock_task.delay.assert_not_called()

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

    @patch('campaigns.tasks.query_overpass')
    def test_full_worker_flow(self, mock_qo):
        mock_qo.return_value = self.WAYS

        # ── 1. Publish campaign and run OSM import ────────────────────────────
        campaign = make_campaign(slug='e2e-camp', cities=['Testville'])
        fetch_osm_segments(campaign.pk)
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

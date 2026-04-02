import uuid
from datetime import date

from django.conf import settings
from django.contrib.gis.db import models


class Campaign(models.Model):
    STATUS = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('deleted', 'Deleted'),
    ]
    MAP_STATUS = [
        ('pending', 'Pending'),
        ('generating', 'Fetching'),
        ('rendering', 'Rendering'),
        ('ready', 'Ready'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ]

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    cities = models.JSONField()  # list of city name strings
    start_date = models.DateField(default=date.today)
    end_date = models.DateField(null=True, blank=True)
    instructions = models.TextField(blank=True)  # stores HTML from rich text editor
    contact_info = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='draft')
    map_status = models.CharField(max_length=20, choices=MAP_STATUS, default='pending')
    map_error = models.TextField(blank=True, default='')
    bbox = models.JSONField(null=True, blank=True)  # [[sw_lat, sw_lon], [ne_lat, ne_lon]]
    geo_limit = models.PolygonField(srid=4326, null=True, blank=True)  # free-form campaign boundary
    is_test = models.BooleanField(default=False)
    # Cached counts — recomputed whenever streets or address points change, so
    # the manage list page never needs to run per-campaign spatial queries.
    # NULL means "not yet computed"; 0 means "computed and genuinely zero".
    cached_size_street_count = models.IntegerField(null=True, blank=True, default=None)
    cached_size_household_count = models.IntegerField(null=True, blank=True, default=None)
    hero_image_url = models.URLField(blank=True, default='')  # optional campaign hero image (landscape/16:9 recommended)
    streets_geojson = models.TextField(blank=True, default='')  # pre-rendered GeoJSON FeatureCollection
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def hero_image_effective_url(self):
        if self.hero_image_url:
            return self.hero_image_url
        try:
            return self.uploaded_image.image.url
        except Exception:
            return None

    @property
    def estimated_addresses(self):
        """Count address points within geo_limit, or all address points if no geo_limit."""
        qs = self.address_points.all()
        if self.geo_limit:
            qs = qs.filter(location__within=self.geo_limit)
        return qs.count()

    class Meta:
        ordering = ['-created_at']


ALLOWED_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp']


def _campaign_image_upload_path(instance, filename):
    import os
    ext = os.path.splitext(filename)[1].lower()
    return f'campaign_images/{uuid.uuid4().hex}{ext}'


class CampaignImage(models.Model):
    campaign = models.OneToOneField(
        Campaign, on_delete=models.CASCADE, related_name='uploaded_image'
    )
    image = models.FileField(upload_to=_campaign_image_upload_path)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.campaign.name}"


class Street(models.Model):
    city_name = models.CharField(max_length=200, db_index=True)  # canonical city identifier
    osm_id = models.BigIntegerField()
    name = models.CharField(max_length=200, blank=True)
    geometry = models.LineStringField(srid=4326)
    block_index = models.PositiveSmallIntegerField(default=0)
    start_node_id = models.BigIntegerField(null=True, blank=True)
    end_node_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('city_name', 'osm_id', 'block_index')

    def __str__(self):
        return f"{self.name or 'Unnamed'} ({self.osm_id} block {self.block_index})"


class CampaignStreet(models.Model):
    """Through table for the Campaign ↔ Street M2M, tracking per-campaign city_index."""
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='campaign_streets')
    street = models.ForeignKey(Street, on_delete=models.CASCADE, related_name='campaign_streets')
    city_index = models.IntegerField(null=True, blank=True)  # index in campaign.cities list

    class Meta:
        unique_together = ('campaign', 'street')
        indexes = [
            models.Index(fields=['campaign', 'city_index'], name='campaigns_campaignstreet_idx'),
        ]

    def __str__(self):
        return f"Campaign {self.campaign_id} - Street {self.street_id} (city_index={self.city_index})"


# Add the streets M2M to Campaign here, after CampaignStreet is defined,
# so we can reference it directly without string indirection.
Campaign.add_to_class(
    'streets',
    models.ManyToManyField(Street, through=CampaignStreet, related_name='campaigns'),
)


class CityFetchJob(models.Model):
    STATUS = [
        ('pending', 'Pending'),
        ('generating', 'Fetching'),
        ('ready', 'Ready'),
        ('error', 'Error'),
    ]
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='city_fetch_jobs')
    city_index = models.IntegerField()       # position in campaign.cities list
    city_name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    error = models.TextField(blank=True, default='')
    celery_task_id = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('campaign', 'city_index')
        ordering = ['city_index']

    def __str__(self):
        return f"{self.city_name} (campaign {self.campaign_id}, idx {self.city_index})"


class AddressPoint(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='address_points')
    city_index = models.IntegerField(null=True, blank=True)
    location = models.PointField(srid=4326)

    class Meta:
        indexes = [
            models.Index(fields=['campaign', 'city_index'], name='campaigns_addresspoint_idx'),
        ]

    def __str__(self):
        return f"AddressPoint for campaign {self.campaign_id} city {self.city_index}"


class Trip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='trips')
    streets = models.ManyToManyField(Street)
    worker_name = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    deleted = models.BooleanField(default=False)

    def __str__(self):
        return f"Trip by {self.worker_name or 'Anonymous'} on {self.recorded_at:%Y-%m-%d}"

    class Meta:
        ordering = ['-recorded_at']

import uuid
from datetime import date

from django.contrib.gis.db import models


class Campaign(models.Model):
    STATUS = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('deleted', 'Deleted'),
    ]
    MAP_STATUS = [
        ('pending', 'Pending'),
        ('generating', 'Generating'),
        ('ready', 'Ready'),
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


class Street(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='streets')
    osm_id = models.BigIntegerField()
    name = models.CharField(max_length=200, blank=True)
    geometry = models.LineStringField(srid=4326)
    block_index = models.PositiveSmallIntegerField(default=0)
    start_node_id = models.BigIntegerField(null=True, blank=True)
    end_node_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('campaign', 'osm_id', 'block_index')

    def __str__(self):
        return f"{self.name or 'Unnamed'} ({self.osm_id} block {self.block_index})"


class Trip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='trips')
    streets = models.ManyToManyField(Street)
    worker_name = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Trip by {self.worker_name or 'Anonymous'} on {self.recorded_at:%Y-%m-%d}"

    class Meta:
        ordering = ['-recorded_at']

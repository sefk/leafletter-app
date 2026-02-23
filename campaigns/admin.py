from django.contrib import admin
from django.http import HttpResponseRedirect
from django.utils.html import format_html
from django.utils.text import slugify

from .models import Campaign, Street, Trip
from .tasks import fetch_osm_segments


MAP_STATUS_COLORS = {
    'pending': '#888888',
    'generating': '#e6ac00',
    'ready': '#2e7d32',
    'error': '#c62828',
}


class TripInline(admin.TabularInline):
    model = Trip
    fields = ('id', 'worker_name', 'notes', 'recorded_at')
    readonly_fields = ('id', 'recorded_at')
    extra = 0
    can_delete = True
    show_change_link = True


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'status', 'map_status_badge', 'start_date', 'end_date')
    list_filter = ('status', 'map_status')
    search_fields = ('name', 'slug')
    actions = ['publish_campaigns', 'soft_delete_campaigns']
    inlines = [TripInline]

    def get_prepopulated_fields(self, request, obj=None):
        if obj and obj.status == 'published':
            return {}
        return {'slug': ('name',)}

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == 'published':
            return ('slug', 'map_status_badge', 'status')
        return ('map_status_badge',)

    def get_fields(self, request, obj=None):
        fields = [
            'name', 'slug', 'goal', 'cities',
            'start_date', 'end_date',
            'instructions', 'materials_url', 'contact_info',
            'status', 'map_status_badge',
        ]
        return fields

    def map_status_badge(self, obj):
        color = MAP_STATUS_COLORS.get(obj.map_status, '#888888')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-weight:bold;">{}</span>',
            color,
            obj.get_map_status_display(),
        )
    map_status_badge.short_description = 'Map Status'

    def save_model(self, request, obj, form, change):
        if not change:
            if not obj.slug:
                obj.slug = slugify(obj.name)
            super().save_model(request, obj, form, change)
            if obj.status == 'published':
                fetch_osm_segments.delay(obj.pk)
            return

        # For edits, detect transitions that require a re-fetch.
        # Skip if the Publish button was clicked — response_change handles that.
        if '_publish' not in request.POST:
            old = Campaign.objects.get(pk=obj.pk)
            super().save_model(request, obj, form, change)
            transitioning_to_published = obj.status == 'published' and old.status != 'published'
            cities_changed = obj.status == 'published' and obj.cities != old.cities
            if transitioning_to_published or cities_changed:
                obj.map_status = 'pending'
                obj.save(update_fields=['map_status'])
                fetch_osm_segments.delay(obj.pk)
                if cities_changed:
                    self.message_user(request, 'Cities updated — OSM street fetch re-queued.')
        else:
            super().save_model(request, obj, form, change)

    def response_change(self, request, obj):
        if '_publish' in request.POST:
            obj.status = 'published'
            obj.map_status = 'pending'
            obj.save(update_fields=['status', 'map_status'])
            fetch_osm_segments.delay(obj.pk)
            self.message_user(request, f'"{obj}" published and OSM street fetch queued.')
            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)

    def delete_model(self, request, obj):
        # Soft delete
        obj.status = 'deleted'
        obj.save(update_fields=['status'])

    def delete_queryset(self, request, queryset):
        # Soft delete for bulk actions
        queryset.update(status='deleted')

    @admin.action(description='Publish selected campaigns and fetch OSM streets')
    def publish_campaigns(self, request, queryset):
        for campaign in queryset.exclude(status='deleted'):
            campaign.status = 'published'
            campaign.map_status = 'pending'
            campaign.save(update_fields=['status', 'map_status'])
            fetch_osm_segments.delay(campaign.pk)
        self.message_user(request, f"Published {queryset.count()} campaign(s) and queued OSM fetch.")

    @admin.action(description='Soft-delete selected campaigns')
    def soft_delete_campaigns(self, request, queryset):
        queryset.update(status='deleted')
        self.message_user(request, f"Soft-deleted {queryset.count()} campaign(s).")

    def get_queryset(self, request):
        # Show all non-deleted campaigns in the list
        return super().get_queryset(request).exclude(status='deleted')


@admin.register(Street)
class StreetAdmin(admin.ModelAdmin):
    list_display = ('name', 'osm_id', 'campaign')
    list_filter = ('campaign',)
    search_fields = ('name', 'osm_id')
    raw_id_fields = ('campaign',)


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ('id', 'worker_name', 'campaign', 'recorded_at')
    list_filter = ('campaign',)
    search_fields = ('worker_name',)
    readonly_fields = ('id', 'recorded_at')
    filter_horizontal = ('streets',)

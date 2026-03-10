from django.contrib import admin
from django.urls import path, include

from campaigns.urls import manage_urlpatterns
from campaigns.views import public_campaign_list, about, api_campaigns, api_campaign_detail

urlpatterns = [
    path('', public_campaign_list, name='public_campaign_list'),
    path('about/', about, name='about'),
    path('admin/dj-celery-panel/', include('dj_celery_panel.urls')),
    path('admin/', admin.site.urls),
    path('api/campaigns/', api_campaigns, name='api_campaigns'),
    path('api/campaigns/<slug:slug>/', api_campaign_detail, name='api_campaign_detail'),
    path('c/', include('campaigns.urls', namespace='campaigns')),
    path('manage/', include(manage_urlpatterns)),
]

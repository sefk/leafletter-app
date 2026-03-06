from django.contrib import admin
from django.urls import path, include

from campaigns.urls import manage_urlpatterns
from campaigns.views import public_campaign_list, about

urlpatterns = [
    path('', public_campaign_list, name='public_campaign_list'),
    path('about/', about, name='about'),
    path('admin/', admin.site.urls),
    path('c/', include('campaigns.urls', namespace='campaigns')),
    path('manage/', include(manage_urlpatterns)),
]

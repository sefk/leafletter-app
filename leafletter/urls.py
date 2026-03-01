from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

from campaigns.urls import manage_urlpatterns

urlpatterns = [
    path('', RedirectView.as_view(url='/manage/', permanent=False)),
    path('admin/', admin.site.urls),
    path('c/', include('campaigns.urls', namespace='campaigns')),
    path('manage/', include(manage_urlpatterns)),
]

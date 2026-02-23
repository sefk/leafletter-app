from django.urls import path
from . import views

app_name = 'campaigns'

urlpatterns = [
    path('<slug:slug>/', views.campaign_detail, name='campaign_detail'),
    path('<slug:slug>/streets.geojson', views.campaign_streets_geojson, name='streets_geojson'),
    path('<slug:slug>/coverage.geojson', views.campaign_coverage_geojson, name='coverage_geojson'),
    path('<slug:slug>/trip/', views.log_trip, name='log_trip'),
]

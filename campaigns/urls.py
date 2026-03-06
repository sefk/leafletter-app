from django.urls import path
from . import views

app_name = 'campaigns'

urlpatterns = [
    path('<slug:slug>/', views.campaign_detail, name='campaign_detail'),
    path('<slug:slug>/streets.geojson', views.campaign_streets_geojson, name='streets_geojson'),
    path('<slug:slug>/coverage.geojson', views.campaign_coverage_geojson, name='coverage_geojson'),
    path('<slug:slug>/trip/', views.log_trip, name='log_trip'),
]

manage_urlpatterns = [
    path('', views.manage_campaign_list, name='manage_campaign_list'),
    path('new/', views.manage_campaign_create, name='manage_campaign_create'),
    path('city-search/', views.city_search, name='city_search'),
    path('<slug:slug>/', views.manage_campaign_detail, name='manage_campaign_detail'),
    path('<slug:slug>/edit/', views.manage_campaign_edit, name='manage_campaign_edit'),
    path('<slug:slug>/publish/', views.manage_campaign_publish, name='manage_campaign_publish'),
    path('<slug:slug>/delete/', views.manage_campaign_delete, name='manage_campaign_delete'),
    path('<slug:slug>/refetch/', views.manage_campaign_refetch, name='manage_campaign_refetch'),
    path('<slug:slug>/refetch-city/<int:city_index>/', views.manage_city_refetch, name='manage_city_refetch'),
    path('<slug:slug>/update-bbox/', views.manage_campaign_update_bbox, name='manage_campaign_update_bbox'),
    path('<slug:slug>/trip/<uuid:trip_id>/delete/', views.manage_trip_delete, name='manage_trip_delete'),
    path('<slug:slug>/trip/<uuid:trip_id>/restore/', views.manage_trip_restore, name='manage_trip_restore'),
]

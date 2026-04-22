from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

app_name = 'campaigns'

urlpatterns = [
    path('<slug:slug>/', views.campaign_detail, name='campaign_detail'),
    path('<slug:slug>/streets.geojson', views.campaign_streets_geojson, name='streets_geojson'),
    path('<slug:slug>/coverage.geojson', views.campaign_coverage_geojson, name='coverage_geojson'),
    path('<slug:slug>/validate-code/', views.validate_access_code, name='validate_access_code'),
    path('<slug:slug>/trip/', views.log_trip, name='log_trip'),
    path('<slug:slug>/trip/<uuid:trip_id>/', views.worker_get_trip, name='worker_get_trip'),
    path('<slug:slug>/trip/<uuid:trip_id>/edit/', views.worker_edit_trip, name='worker_edit_trip'),
]

manage_urlpatterns = [
    path('login/', views.manage_login, name='manage_login'),
    path('logout/', views.manage_logout, name='manage_logout'),
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='campaigns/manage/password_reset.html',
        email_template_name='campaigns/manage/password_reset_email.txt',
        subject_template_name='campaigns/manage/password_reset_subject.txt',
        success_url='/manage/password-reset/sent/',
    ), name='password_reset'),
    path('password-reset/sent/', auth_views.PasswordResetDoneView.as_view(
        template_name='campaigns/manage/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='campaigns/manage/password_reset_confirm.html',
        success_url='/manage/password-reset/complete/',
    ), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='campaigns/manage/password_reset_complete.html',
    ), name='password_reset_complete'),
    path('', views.manage_campaign_list, name='manage_campaign_list'),
    path('new/', views.manage_campaign_quick_create, name='manage_campaign_create'),
    path('city-search/', views.city_search, name='city_search'),
    path('cities/prefetched/', views.cities_prefetched, name='cities_prefetched'),
    path('usage-report/', views.manage_usage_report, name='manage_usage_report'),
    path('<slug:slug>/', views.manage_campaign_detail, name='manage_campaign_detail'),
    path('<slug:slug>/edit/', views.manage_campaign_edit, name='manage_campaign_edit'),
    path('<slug:slug>/save-basics/', views.manage_save_basics, name='manage_save_basics'),
    path('<slug:slug>/save-hero/', views.manage_save_hero, name='manage_save_hero'),
    path('<slug:slug>/save-cities/', views.manage_save_cities, name='manage_save_cities'),
    path('<slug:slug>/publish/', views.manage_campaign_publish, name='manage_campaign_publish'),
    path('<slug:slug>/unpublish/', views.manage_campaign_unpublish, name='manage_campaign_unpublish'),
    path('<slug:slug>/delete/', views.manage_campaign_delete, name='manage_campaign_delete'),
    path('<slug:slug>/restore/', views.manage_campaign_restore, name='manage_campaign_restore'),
    path('<slug:slug>/fetch-status/', views.manage_campaign_fetch_status, name='manage_campaign_fetch_status'),
    path('<slug:slug>/refetch/', views.manage_campaign_refetch, name='manage_campaign_refetch'),
    path('<slug:slug>/refetch-city/<int:city_index>/', views.manage_city_refetch, name='manage_city_refetch'),
    path('<slug:slug>/delete-city/<int:city_index>/', views.manage_city_delete, name='manage_city_delete'),
    path('<slug:slug>/update-geo-limit/', views.manage_campaign_update_geo_limit, name='manage_campaign_update_geo_limit'),
    path('<slug:slug>/address-count/', views.manage_campaign_address_preview, name='manage_campaign_address_preview'),
    path('<slug:slug>/streets.geojson', views.manage_campaign_streets_geojson, name='manage_streets_geojson'),
    path('<slug:slug>/coverage.geojson', views.manage_campaign_coverage_geojson, name='manage_coverage_geojson'),
    path('<slug:slug>/trip/<uuid:trip_id>/delete/', views.manage_trip_delete, name='manage_trip_delete'),
    path('<slug:slug>/trip/<uuid:trip_id>/restore/', views.manage_trip_restore, name='manage_trip_restore'),
    path('<slug:slug>/trip/<uuid:trip_id>/edit/', views.manage_trip_edit, name='manage_trip_edit'),
    path('<slug:slug>/export-trips/', views.manage_export_trips, name='manage_export_trips'),
    path('<slug:slug>/remove-image/', views.manage_campaign_remove_image, name='manage_remove_image'),
]

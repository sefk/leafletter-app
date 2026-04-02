"""
Django settings for leafletter project.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-wti#q9%lysc97#8y%cxo2ucna_kurpg2@gxhm(4-n01)t5=p4s')

# Default DEBUG=True locally; auto-disable in production (Railway sets RAILWAY_ENVIRONMENT=production).
# Can always be overridden explicitly via the DEBUG env var.
_on_railway = os.environ.get('RAILWAY_ENVIRONMENT') == 'production'
DEBUG = os.environ.get('DEBUG', 'False' if _on_railway else 'True') == 'True'

# In production, restrict ALLOWED_HOSTS to the known Railway domains.
# DEBUG mode keeps the wildcard so local runserver works without extra config.
if DEBUG:
    ALLOWED_HOSTS = ['*']
else:
    _public_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
    _private_domain = os.environ.get('RAILWAY_PRIVATE_DOMAIN', '')
    ALLOWED_HOSTS = ['localhost', '127.0.0.1']
    if _public_domain:
        ALLOWED_HOSTS.append(_public_domain)
    if _private_domain:
        ALLOWED_HOSTS.append(_private_domain)

# Railway terminates SSL at the load balancer and forwards requests to gunicorn over HTTP,
# setting X-Forwarded-Proto: https. Tell Django to trust that header so request.is_secure()
# returns True and CSRF referer validation works correctly.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# In production, mark session and CSRF cookies as secure-only (HTTPS).
# SECURE_SSL_REDIRECT is left False because Railway's load balancer already
# handles the HTTP→HTTPS redirect; enabling it here would cause redirect loops.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Set CSRF_TRUSTED_ORIGINS to a comma-separated list of trusted origins, e.g.
# "https://web-production-b863b.up.railway.app,https://yourdomain.com"
# Default includes both the Railway subdomain and custom domain so this works
# without needing a Railway env var override.
_csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS',
    'https://web-production-b863b.up.railway.app,https://leafletter.app')
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(',') if o.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'django.contrib.gis',
    'django_celery_results',
    'django_celery_beat',
    'dj_celery_panel',
    'campaigns',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'leafletter.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'leafletter.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.mysql',
        'NAME': os.environ.get('MYSQL_DATABASE', 'leafletter'),
        'USER': os.environ.get('MYSQL_USER', 'leafletter'),
        'PASSWORD': os.environ.get('MYSQL_PASSWORD', 'leafletter'),
        'HOST': os.environ.get('MYSQL_HOST', 'localhost'),
        'PORT': os.environ.get('MYSQL_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
# WhiteNoise serves static files directly from gunicorn (no nginx needed).
# CompressedManifestStaticFilesStorage adds content-hash cache-busting in production;
# dev uses the plain default so runserver works without running collectstatic.
STORAGES = {
    'staticfiles': {
        'BACKEND': (
            'django.contrib.staticfiles.storage.StaticFilesStorage'
            if DEBUG
            else 'whitenoise.storage.CompressedManifestStaticFilesStorage'
        ),
    },
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
}

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Image storage: S3 in production (when AWS_ACCESS_KEY_ID is set), filesystem in dev
_aws_key = os.environ.get('AWS_ACCESS_KEY_ID', '')
_s3_endpoint = os.environ.get('AWS_S3_ENDPOINT_URL', '')
if _aws_key and _s3_endpoint:
    STORAGES['default'] = {
        'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
        'OPTIONS': {
            'access_key': _aws_key,
            'secret_key': os.environ.get('AWS_SECRET_ACCESS_KEY', ''),
            'bucket_name': os.environ.get('AWS_STORAGE_BUCKET_NAME', 'images'),
            'region_name': os.environ.get('AWS_S3_REGION_NAME', ''),
            'endpoint_url': _s3_endpoint,
            'file_overwrite': False,
            'querystring_auth': True,
            'querystring_expire': 604799,  # 7 days (Tigris max)
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/manage/login/'

# ── Email ──────────────────────────────────────────────────────────────────────
# Watchdog notification emails are sent to all active superusers (queried at
# runtime), so no ADMINS list is needed here.

# Email backend: defaults to Django's standard smtp backend so that Django's
# test runner can substitute locmem (it only does so when the backend is smtp).
# In local dev you can set EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
# in your .env to avoid needing a real mail server.
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.smtp.EmailBackend',
)

EMAIL_HOST = os.environ.get('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'webmaster@localhost')
SERVER_EMAIL = os.environ.get('SERVER_EMAIL', DEFAULT_FROM_EMAIL)

# Celery
_mysql_user = os.environ.get('MYSQL_USER', 'leafletter')
_mysql_password = os.environ.get('MYSQL_PASSWORD', 'leafletter')
_mysql_host = os.environ.get('MYSQL_HOST', 'localhost')
_mysql_port = os.environ.get('MYSQL_PORT', '3306')
_mysql_db = os.environ.get('MYSQL_DATABASE', 'leafletter')
CELERY_BROKER_URL = f'sqla+mysql://{_mysql_user}:{_mysql_password}@{_mysql_host}:{_mysql_port}/{_mysql_db}'
CELERY_RESULT_BACKEND = 'django-db'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'

from celery.schedules import crontab  # noqa: E402
CELERY_BEAT_SCHEDULE = {
    'watchdog-stuck-cityfetchjobs': {
        'task': 'campaigns.tasks.watchdog_stuck_jobs',
        # Run every 15 minutes; detects jobs stuck in 'generating' for >30 min.
        'schedule': crontab(minute='*/15'),
    },
    'backup-database-daily': {
        'task': 'campaigns.tasks.backup_database',
        # Run daily at 02:00 UTC, off-peak for Railway/Tigris.
        'schedule': crontab(hour=2, minute=0),
    },
}

# S3 bucket for database backups.  Defaults to the main media bucket so no
# extra bucket is required; set BACKUP_S3_BUCKET to use a dedicated bucket.
BACKUP_S3_BUCKET = os.environ.get(
    'BACKUP_S3_BUCKET',
    os.environ.get('AWS_STORAGE_BUCKET_NAME', 'leafletter'),
)

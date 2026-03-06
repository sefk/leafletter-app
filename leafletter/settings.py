"""
Django settings for leafletter project.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-wti#q9%lysc97#8y%cxo2ucna_kurpg2@gxhm(4-n01)t5=p4s')

DEBUG = os.environ.get('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = ['*']

# Railway terminates SSL at the load balancer and forwards requests to gunicorn over HTTP,
# setting X-Forwarded-Proto: https. Tell Django to trust that header so request.is_secure()
# returns True and CSRF referer validation works correctly.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Set CSRF_TRUSTED_ORIGINS to a comma-separated list of trusted origins, e.g.
# "https://web-production-b863b.up.railway.app,https://yourdomain.com"
_csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', 'https://leafletter.app')
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(',') if o.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.gis',
    'django_celery_results',
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

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/manage/login/'

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

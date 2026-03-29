"""
Test settings — extends production settings with overrides for the test runner.

Use with:  python manage.py test --settings=leafletter.settings_test
"""

from leafletter.settings import *  # noqa: F401, F403

# Use the plain staticfiles backend so tests don't require a pre-built manifest
# (CompressedManifestStaticFilesStorage requires collectstatic to have run first).
DEBUG = True
ALLOWED_HOSTS = ['*']
STORAGES = {
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
}

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'leafletter.settings')

app = Celery('leafletter')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

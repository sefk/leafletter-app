redis: redis-server
worker: watchmedo auto-restart --directory=. --pattern='*.py' --recursive -- celery -A leafletter worker -l info
web: python manage.py runserver

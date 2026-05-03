#!/bin/bash
set -e
python manage.py migrate --noinput
exec gunicorn leafletter.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${GUNICORN_WORKERS:-2}

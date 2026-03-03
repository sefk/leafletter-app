#!/bin/bash
set -e
python manage.py migrate --noinput
exec gunicorn leafletter.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 2

"""
Management command: backup_database

Runs the database backup task synchronously (in-process, bypassing the Celery
broker).  Useful from the Railway console for on-demand or emergency backups.

Usage:
    python manage.py backup_database
"""

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Run the database backup task synchronously.  '
        'Dumps MySQL, compresses with gzip, and uploads to S3.'
    )

    def handle(self, *args, **options):
        from campaigns.tasks import _run_backup

        self.stdout.write('Starting database backup...')
        try:
            result = _run_backup()
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Backup failed: {exc}'))
            raise SystemExit(1) from exc

        if 'error' in result:
            self.stderr.write(self.style.ERROR(f"Backup failed: {result['error']}"))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(
            f"Backup complete.\n"
            f"  Uploaded: {result['key']}\n"
            f"  Old backups pruned: {result['pruned']}"
        ))

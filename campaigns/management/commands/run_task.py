"""
Management command: run_task

Invoke a Celery task directly (synchronously, bypassing the broker) for
debugging and manual recovery.  Useful from both the CLI and manage.py shell.

Usage:
    # List all registered tasks
    python manage.py run_task --list

    # Run a task with positional args (JSON-decoded)
    python manage.py run_task campaigns.tasks.fetch_city_osm_data 42 0

    # Run a task with keyword args
    python manage.py run_task campaigns.tasks.render_campaign_geojson --kwargs '{"campaign_id": 42}'

    # Dispatch via broker instead of running inline (uses .delay())
    python manage.py run_task campaigns.tasks.watchdog_stuck_jobs --async

Examples from manage.py shell:
    from campaigns.tasks import fetch_city_osm_data, render_campaign_geojson
    fetch_city_osm_data.apply(args=[42, 0])          # run inline, see result
    render_campaign_geojson.apply(args=[42])
    render_campaign_geojson.delay(42)                # send to broker
"""

import json
import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Invoke a Celery task synchronously for debugging. '
        'Use --list to see available tasks, --async to send via broker.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'task_name',
            nargs='?',
            help='Dotted task name, e.g. campaigns.tasks.fetch_city_osm_data',
        )
        parser.add_argument(
            'task_args',
            nargs='*',
            help='Positional arguments for the task (JSON-decoded automatically)',
        )
        parser.add_argument(
            '--kwargs',
            default='{}',
            help='Keyword arguments as a JSON object, e.g. \'{"campaign_id": 42}\'',
        )
        parser.add_argument(
            '--async',
            action='store_true',
            dest='use_async',
            help='Send the task to the broker via .delay() instead of running inline',
        )
        parser.add_argument(
            '--list',
            action='store_true',
            dest='list_tasks',
            help='List all registered Celery task names and exit',
        )

    def handle(self, *args, **options):
        from leafletter.celery import app as celery_app  # noqa: import here to avoid circular import

        if options['list_tasks']:
            self._list_tasks(celery_app)
            return

        task_name = options.get('task_name')
        if not task_name:
            raise CommandError(
                'Provide a task name or use --list to see available tasks.\n'
                'Example: python manage.py run_task campaigns.tasks.watchdog_stuck_jobs'
            )

        # Resolve the task
        try:
            task = celery_app.tasks[task_name]
        except KeyError:
            # Friendly error: show close matches
            registered = sorted(celery_app.tasks.keys())
            matches = [t for t in registered if task_name.split('.')[-1] in t]
            hint = ''
            if matches:
                hint = '\nDid you mean one of these?\n  ' + '\n  '.join(matches)
            raise CommandError(f'Task "{task_name}" not found.{hint}')

        # Parse positional args (JSON-decode each token if possible)
        positional = []
        for token in options['task_args']:
            try:
                positional.append(json.loads(token))
            except (json.JSONDecodeError, ValueError):
                positional.append(token)  # leave as string

        # Parse keyword args
        try:
            kwargs = json.loads(options['kwargs'])
        except (json.JSONDecodeError, ValueError) as exc:
            raise CommandError(f'--kwargs is not valid JSON: {exc}')

        self.stdout.write(
            f'Task:  {task_name}\n'
            f'Args:  {positional}\n'
            f'Kwargs: {kwargs}\n'
        )

        if options['use_async']:
            result = task.apply_async(args=positional, kwargs=kwargs)
            self.stdout.write(
                self.style.SUCCESS(f'Task dispatched to broker. Task ID: {result.id}')
            )
        else:
            self.stdout.write('Running task inline (synchronous, bypasses broker)...\n')
            try:
                result = task.apply(args=positional, kwargs=kwargs)
                self.stdout.write(self.style.SUCCESS(f'Task completed successfully.'))
                if result.result is not None:
                    self.stdout.write(f'Return value: {result.result}')
                if result.traceback:
                    self.stdout.write(self.style.ERROR(f'Traceback:\n{result.traceback}'))
            except Exception as exc:
                raise CommandError(f'Task raised an exception: {exc}') from exc

    def _list_tasks(self, celery_app):
        registered = sorted(celery_app.tasks.keys())
        # Filter out internal celery tasks — show only app tasks
        app_tasks = [t for t in registered if not t.startswith('celery.')]
        self.stdout.write(self.style.HTTP_INFO('Registered application tasks:\n'))
        for name in app_tasks:
            self.stdout.write(f'  {name}')
        self.stdout.write(f'\n{len(app_tasks)} task(s) registered.')
        if len(registered) > len(app_tasks):
            self.stdout.write(
                f'(Plus {len(registered) - len(app_tasks)} internal celery.* tasks, omitted)'
            )

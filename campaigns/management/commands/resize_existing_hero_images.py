"""
Management command: resize_existing_hero_images

Re-resizes every existing campaign hero image using the current
`_resize_hero_image` defaults (see campaigns/views.py).  New uploads are
already resized at upload time; this command brings older uploads in line
after the resize defaults have been tightened.

Each image is downloaded from storage, re-encoded, and written back under a
new filename (the OneToOne `CampaignImage.image` field points at the new
file on success).  The old S3 object is deleted after a successful re-upload
so orphans do not accumulate.

Usage:
    python manage.py resize_existing_hero_images --dry-run
    python manage.py resize_existing_hero_images

Notes:
    - Re-encoding a previously-resized JPEG loses a little quality each pass.
      Running this once after a resize-default change is fine; running it
      repeatedly degrades quality over time.
    - Safe to run on staging first; the only state touched is the
      `CampaignImage.image` file pointer and the corresponding S3 object.
"""

import io

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.management.base import BaseCommand

from campaigns.models import CampaignImage
from campaigns.views import _resize_hero_image


class Command(BaseCommand):
    help = (
        'Re-resize existing campaign hero images using the current '
        '_resize_hero_image defaults.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        qs = CampaignImage.objects.select_related('campaign').all()
        self.stdout.write(f'Found {qs.count()} CampaignImage row(s).')

        for ci in qs:
            try:
                ci.image.open('rb')
                data = ci.image.read()
                ci.image.close()
            except Exception as e:
                self.stderr.write(f'  [{ci.campaign.slug}] read failed: {e}')
                continue

            name = ci.image.name.rsplit('/', 1)[-1] or 'hero.jpg'
            original_size = len(data)
            upload = InMemoryUploadedFile(
                io.BytesIO(data),
                'image',
                name,
                ci.content_type or 'image/jpeg',
                original_size,
                None,
            )

            try:
                resized = _resize_hero_image(upload)
            except Exception as e:
                self.stderr.write(f'  [{ci.campaign.slug}] resize failed: {e}')
                continue

            resized.seek(0, 2)
            new_size = resized.tell()
            resized.seek(0)
            pct = 100 - int(100 * new_size / original_size) if original_size else 0
            msg = f'  [{ci.campaign.slug}] {original_size} -> {new_size} bytes ({pct}% smaller)'

            if dry_run:
                self.stdout.write(f'{msg} [dry-run]')
                continue

            old_path = ci.image.name
            ci.image.save(resized.name, resized, save=True)
            try:
                ci.image.storage.delete(old_path)
            except Exception as e:
                self.stderr.write(f'    (old file {old_path} not deleted: {e})')
            self.stdout.write(self.style.SUCCESS(msg))

        self.stdout.write('Done.')

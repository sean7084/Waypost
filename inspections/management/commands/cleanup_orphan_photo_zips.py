"""Delete orphaned photo-archive ZIPs left behind by the retired photo_zip field.

Before migration 0006, every report generation persisted a per-store ZIP of all
photos (``<store> Photo.zip``) next to the workbook, storing each photo a second
time. The field is gone and archives are now streamed on demand via
``bundle_export``, but files already on disk remain. Run this once after
migrating and before any OSS backfill so the orphans are not copied to object
storage.

Only ``*Photo*.zip`` under ``MEDIA_ROOT/inspections/`` is removed; workbooks
(``.xlsx``), signatures and the photos themselves are untouched. Uses
``default_storage`` so it works on the filesystem or an object-storage backend.

Usage:
  python manage.py cleanup_orphan_photo_zips --dry-run
  python manage.py cleanup_orphan_photo_zips
"""
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

ROOT = 'inspections'


class Command(BaseCommand):
    help = ('Delete orphaned <store> Photo.zip archives left by the retired '
            'photo_zip field (photos are now streamed on demand).')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would be deleted without deleting')

    def _walk(self, path):
        try:
            directories, files = default_storage.listdir(path)
        except (FileNotFoundError, OSError, NotADirectoryError):
            return
        for name in files:
            yield f'{path}/{name}'
        for sub in directories:
            yield from self._walk(f'{path}/{sub}')

    @staticmethod
    def _is_orphan(path):
        name = path.rsplit('/', 1)[-1]
        return name.endswith('.zip') and 'Photo' in name

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        orphans = sorted(p for p in self._walk(ROOT) if self._is_orphan(p))
        if not orphans:
            self.stdout.write('No orphaned photo archives found.')
            return

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(f'{prefix}Found {len(orphans)} orphaned photo archive(s):')
        for path in orphans:
            self.stdout.write(f'  {path}')
            if not dry_run:
                default_storage.delete(path)

        if dry_run:
            self.stdout.write('[DRY RUN] nothing was deleted.')
        else:
            self.stdout.write(f'Deleted {len(orphans)} file(s).')

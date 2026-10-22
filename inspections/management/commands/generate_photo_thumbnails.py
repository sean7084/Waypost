"""Backfill the ``thumbs/`` derivative for photos uploaded before the thumbnail pipeline.

Thumbnails are rendered on upload (``inspections/services/thumbnails.py`` explains
why on-upload), so every photo stored before that change has no derivative and
every list view - web or mini program - silently falls back to the full-size
original for it. That is the whole cost this command removes: the fallback is by
design, so nothing is broken until a photo is listed, but a store with 200 photos
sends 200 x ~300 KB where 200 x ~30 KB would do.

Skips photos that already have a derivative, so it is safe to re-run and safe to
interleave with live uploads. Uses ``default_storage`` for the existence check,
so it behaves identically on the filesystem and on object storage: run it before
the OSS cutover and the derivatives are there to be synced, run it after and it
writes straight into the bucket.

Usage:
  python manage.py generate_photo_thumbnails --all --dry-run
  python manage.py generate_photo_thumbnails --all
  python manage.py generate_photo_thumbnails --jda 22149
  python manage.py generate_photo_thumbnails --inspection <store-inspection-id>
"""
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from inspections.models import InspectionPhoto
from inspections.services.thumbnails import ensure_thumbnail, thumbnail_storage_name


class Command(BaseCommand):
    help = ('Render missing thumbs/ derivatives for inspection photos. '
            'Thumbnails are written on upload, so only photos predating that '
            'pipeline need backfilling.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would be written without writing it')
        scope = parser.add_mutually_exclusive_group()
        scope.add_argument('--inspection', metavar='ID',
                           help='Only photos of one store inspection (its id)')
        scope.add_argument('--jda', metavar='CODE',
                           help='Only photos of store inspections with this JDA code')
        scope.add_argument('--all', action='store_true',
                           help='Every photo (the one-shot backfill)')

    def _photos(self, options):
        if options['all']:
            return InspectionPhoto.objects.all()
        if options['jda']:
            return InspectionPhoto.objects.filter(store_inspection__jda_code=options['jda'])
        if options['inspection']:
            try:
                return InspectionPhoto.objects.filter(store_inspection_id=options['inspection'])
            except (ValidationError, ValueError) as exc:
                raise CommandError(f'--inspection is not a valid store inspection id: {exc}') from exc
        raise CommandError('Give one of --all, --jda CODE or --inspection ID.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        photos = self._photos(options)

        missing = [
            photo for photo in photos
            if photo.image and not default_storage.exists(thumbnail_storage_name(photo))
        ]
        if not missing:
            self.stdout.write('No photos without a thumbnail.')
            return

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(f'{prefix}Found {len(missing)} photo(s) without a thumbnail:')
        written = 0
        for photo in missing:
            name = thumbnail_storage_name(photo)
            if dry_run:
                self.stdout.write(f'  {name}')
                continue
            # ensure_thumbnail() reports failure rather than raising: an
            # undecodable original is skipped and the rest of the batch goes on.
            if ensure_thumbnail(photo):
                written += 1
            else:
                self.stdout.write(self.style.ERROR(f'  skipped (could not render) photo {photo.pk}'))

        if dry_run:
            self.stdout.write('[DRY RUN] nothing was written.')
        else:
            self.stdout.write(f'Wrote {written} of {len(missing)} thumbnail(s).')

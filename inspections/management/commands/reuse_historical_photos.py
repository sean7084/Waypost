"""
Reuse historical photos for planned store inspections.

DB-native port of the EUS historical-photo step: for a target inspection (or all
planned inspections for a store/date), copy reusable overall/serial photos from the
most recent prior inspection of the same store, matched by serial number, and mark
matched devices photo_required=False so engineers do not re-shoot them onsite.

The prior round is left in place by default, because it is normally history from an
earlier visit. When it is instead a second row for the SAME store, date and slot -
the shape ``uniq_store_inspection_location_date_slot``
(inspections/migrations/0007) refuses - that leftover row is a duplicate, not
history, and ``--prune-superseded`` deletes it once its evidence has been adopted.
A morning and an afternoon round of one store on one day are two legitimate rounds,
so a slot mismatch alone is enough to make the prior row history.

Usage:
  python manage.py reuse_historical_photos --inspection <uuid>
  python manage.py reuse_historical_photos --jda 22149 --date 2026-09-02
  python manage.py reuse_historical_photos --all-planned
  python manage.py reuse_historical_photos --all-planned --prune-superseded
"""
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from inspections.models import StoreInspection
from inspections.services.historical_photos import (
    REUSABLE_KINDS,
    # Private on purpose: the prune guard has to decide "was this adopted?" with
    # the same key the copy step used, so it imports that step's helper instead of
    # re-deriving a normalisation that can drift from it.
    _sn_key,
    reuse_historical_photos,
)
from inspections.services.thumbnails import thumbnail_storage_name


class Command(BaseCommand):
    help = ('Copy reusable photos from prior inspections into planned store '
            'inspections, optionally deleting the superseded same-store-same-date-'
            'same-slot round afterwards (off by default).')

    def add_arguments(self, parser):
        parser.add_argument('--inspection', default=None, help='Target StoreInspection UUID')
        parser.add_argument('--jda', default=None, help='Filter planned inspections by JDA code')
        parser.add_argument('--date', default=None, help='Filter planned inspections by inspection_date (YYYY-MM-DD)')
        parser.add_argument('--all-planned', action='store_true', help='Process every planned inspection')
        parser.add_argument('--prune-superseded', action='store_true',
                            help='Delete the source inspection after its evidence was adopted, '
                                 'but only when it is the same store, the same date AND the same '
                                 'slot as the target (the duplicate shape migrations/0007 rejects). '
                                 'A prior round from a different date, or the other slot of the '
                                 'same day, is a real visit and is never touched. '
                                 'Default: keep both rows.')

    def handle(self, *args, **options):
        targets = self._resolve_targets(options)
        if not targets:
            raise CommandError('No target inspections matched.')

        total_devices = 0
        total_photos = 0
        pruned = 0
        kept = 0
        for inspection in targets:
            result = reuse_historical_photos(inspection)
            total_devices += result['devices_matched']
            total_photos += result['photos_copied']
            self.stdout.write(
                f'{inspection.store_label or inspection.id}: '
                f"{result['devices_matched']} devices, {result['photos_copied']} photos "
                f"(source: {result['source_inspection'] or 'none'})"
            )
            if options['prune_superseded']:
                source = self._resolve_source(inspection, result)
                if source is None:
                    continue
                reason = self._prune_block(source, inspection)
                if reason:
                    kept += 1
                    self.stdout.write(f'  kept {source.id}: {reason}')
                else:
                    self._delete_with_media(source)
                    pruned += 1
                    self.stdout.write(
                        f'  pruned superseded {source.id} '
                        f'(same store + date + slot as {inspection.id})'
                    )

        suffix = ''
        if options['prune_superseded']:
            suffix = f' {pruned} superseded round(s) pruned, {kept} kept.'
        self.stdout.write(self.style.SUCCESS(
            f'Done. {total_devices} devices matched, {total_photos} photos copied.' + suffix
        ))

    def _resolve_source(self, inspection, result):
        """The source row the service just adopted evidence from, if it is a duplicate."""
        if not result['source_inspection']:
            return None
        source = StoreInspection.objects.filter(pk=result['source_inspection']).first()
        if source is None:
            return None
        if source.location_id != inspection.location_id:
            return None
        if source.inspection_date != inspection.inspection_date:
            # A round from another date is history, not a superseded duplicate.
            return None
        if source.slot != inspection.slot:
            # The other half of the same day: reuse may borrow its photos, because
            # adoption copies rather than moves, but the row records a visit that
            # really happened and migrations/0007 permits it.
            return None
        return source

    @staticmethod
    def _prune_block(source, target):
        """Why this source must be kept, or None when pruning loses nothing.

        Reuse copies *reusable-kind photos of SN-matched devices* and nothing
        else, so anything the source holds that the copy did not adopt is lost by
        the delete: issues, signoff history, deliverables, rack/issue photos,
        photos of a device the target does not have, and device rows with no SN
        to match on. Each of those is a reason to keep the row, not to merge it.
        """
        if source.status in (StoreInspection.Status.SUBMITTED,
                             StoreInspection.Status.COMPLETED):
            return 'status is ' + source.status + '; a finished round is an evidentiary record'
        if source.signoff_logs.exists():
            return 'it carries signoff-log history that reuse does not copy'
        if source.issues.exists():
            return 'it carries issue rows that reuse does not copy'
        if (source.report_file.name or source.store_signature.name
                or source.engineer_signature.name):
            return 'it holds a generated deliverable or a captured signature'

        target_keys = {_sn_key(device.sn) for device in target.devices.exclude(sn='')}
        unmatched = [
            device for device in source.devices.all()
            if not device.sn or _sn_key(device.sn) not in target_keys
        ]
        if unmatched:
            return (f'{len(unmatched)} of its device rows have no serial-number counterpart '
                    'in the target, so they would be deleted unreconciled')
        for photo in source.photos.select_related('device'):
            if photo.device_id is None:
                return 'it holds a store-level photo that reuse does not copy'
            if photo.kind not in REUSABLE_KINDS:
                return f'it holds a {photo.kind} photo that reuse does not copy'
        return None

    @staticmethod
    def _delete_with_media(source):
        """Delete the row and its media through the storage API, never a path.

        ``Model.delete()`` drops rows only: the bytes stay on storage forever,
        which is worse here than anywhere else because this branch exists to
        remove a redundant round. ``FieldFile.delete()`` and
        ``default_storage.delete()`` are backend-neutral, so the same code works
        on the filesystem and on OSS. The photo bytes go before their rows, and
        the thumbnail name is derived from the photo id, so it goes too.
        """
        for photo in source.photos.all():
            if photo.image.name:
                photo.image.delete(save=False)
                try:
                    name = thumbnail_storage_name(photo)
                    if default_storage.exists(name):
                        default_storage.delete(name)
                except (OSError, NotImplementedError):
                    # An unreachable derivative must not fail the prune; the
                    # original is what the row pointed at.
                    pass
        for field in (source.report_file, source.store_signature,
                      source.engineer_signature):
            if field.name:
                field.delete(save=False)
        source.delete()


    def _resolve_targets(self, options):
        if options['inspection']:
            inspection = StoreInspection.objects.filter(pk=options['inspection']).first()
            return [inspection] if inspection else []

        queryset = StoreInspection.objects.filter(status=StoreInspection.Status.PLANNED)
        if not options['all_planned'] and not options['jda'] and not options['date']:
            return []
        if options['jda']:
            queryset = queryset.filter(jda_code=options['jda'])
        if options['date']:
            queryset = queryset.filter(inspection_date=options['date'])
        return list(queryset)

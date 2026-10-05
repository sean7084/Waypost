"""
Import the Kering master dataset into Waypost.

Thin CLI wrapper around :func:`inspections.services.kering_import.import_kering_master`.
The heavy lifting (parsing, get-or-create logic, rollback tracking) lives in the
service so the web UI can drive the same flow from uploaded files.

Usage:
  python manage.py import_kering_master --schedule schedule.xlsx --assets asset_list_CN_2026.xlsx
  python manage.py import_kering_master --schedule ... --assets ... --dry-run
  python manage.py import_kering_master --schedule ... --assets ... --batch-name "Kering 2026-09"

Without --batch/--batch-name the imported inspections are left unassigned, which
means they never show up on /inspections/batches/ (adopt them later with
``group_unbatched_inspections``).
"""
import os

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from inspections.services.kering_import import import_kering_master


class Command(BaseCommand):
    help = 'Import the Kering schedule + master asset list into Waypost (store inspections).'

    def add_arguments(self, parser):
        parser.add_argument('--schedule', required=True, help='Path to schedule.xlsx')
        parser.add_argument('--assets', required=True, help='Path to asset_list_CN_2026.xlsx')
        parser.add_argument('--company-name', default='Kering', help='Client company name (default: Kering)')
        parser.add_argument('--company-code', default='KER', help='Client company code (default: KER)')
        parser.add_argument('--engineer', default=None, help='Optional username to assign as onsite engineer')
        parser.add_argument('--dry-run', action='store_true', help='Preview without persisting changes')
        parser.add_argument('--no-track-rollback', action='store_true',
                            help='Skip ImportRun rollback tracking (faster for very large imports)')
        parser.add_argument('--batch', default=None,
                            help='Attach the imported inspections to this existing batch (name or UUID)')
        parser.add_argument('--batch-name', default=None,
                            help='Create (or reuse) a batch with this name and attach the imports; '
                                 'its date range is tightened to what was imported')

    def _resolve_batch(self, options):
        """Resolve --batch / --batch-name into an InspectionBatch (or None)."""
        from companies.models import Company
        from inspections.models import InspectionBatch

        reference = (options['batch'] or '').strip()
        name = (options['batch_name'] or '').strip()
        if reference and name:
            raise CommandError('Use either --batch or --batch-name, not both.')
        if not (reference or name):
            return None

        if reference:
            batch = InspectionBatch.objects.filter(name__iexact=reference).first()
            if batch is None:
                try:
                    batch = InspectionBatch.objects.filter(id=reference).first()
                except (TypeError, ValueError):
                    batch = None
            if batch is None:
                raise CommandError(f'Batch not found: {reference}')
            return batch

        if options['dry_run']:
            self.stdout.write(
                f'[DRY RUN] would create/reuse batch "{name}"; '
                'no batch is written in a dry run.'
            )
            return None

        company, _created = Company.objects.get_or_create(
            name=options['company_name'],
            defaults={'code': options['company_code'],
                      'status': Company.CompanyStatus.ACTIVE},
        )
        today = timezone.localdate()
        batch, created = InspectionBatch.objects.get_or_create(
            company=company,
            name=name,
            defaults={'start_date': today, 'end_date': today,
                      'source': InspectionBatch.Source.UPLOAD},
        )
        self.stdout.write(f'{"Created" if created else "Reusing"} batch "{batch.name}".')
        return batch

    def _tighten_batch_range(self, batch):
        """Match the batch window to the inspections it actually holds."""
        from inspections.models import StoreInspection

        rows = StoreInspection.objects.filter(batch=batch)
        if not rows.exists():
            return
        batch.start_date = rows.order_by('inspection_date').first().inspection_date
        batch.end_date = rows.order_by('-inspection_date').first().inspection_date
        batch.save(update_fields=['start_date', 'end_date', 'updated_at'])

    def handle(self, *args, **options):
        schedule_path = options['schedule']
        assets_path = options['assets']
        for path in (schedule_path, assets_path):
            if not os.path.exists(path):
                raise CommandError(f'File not found: {path}')

        engineer = None
        if options['engineer']:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            engineer = User.objects.filter(username=options['engineer']).first()
            if engineer is None:
                raise CommandError(f'Engineer user not found: {options["engineer"]}')

        batch = self._resolve_batch(options)

        result = import_kering_master(
            schedule_path,
            assets_path,
            company_name=options['company_name'],
            company_code=options['company_code'],
            engineer=engineer,
            batch=batch,
            user=None,
            dry_run=options['dry_run'],
            track_rollback=not options['no_track_rollback'],
        )

        if batch is not None and not options['dry_run']:
            self._tighten_batch_range(batch)

        stats = result['stats']
        per_store = result['per_store']

        prefix = '[DRY RUN] ' if options['dry_run'] else ''
        self.stdout.write(f'{prefix}Import complete:')
        for key in ('companies', 'divisions', 'locations', 'inspections',
                    'categories', 'brands', 'models', 'assets', 'devices'):
            self.stdout.write(f'  {key}: {stats[key]}')
        if stats['devices_skipped_duplicate']:
            self.stdout.write(f'  devices_skipped_duplicate: {stats["devices_skipped_duplicate"]}')
        if batch is not None:
            self.stdout.write(f'  batch: {batch.name} ({batch.start_date} .. {batch.end_date})')
        if per_store:
            self.stdout.write(f'{prefix}Per-store summary:')
            for jda in sorted(per_store):
                entry = per_store[jda]
                self.stdout.write(f'  {jda} {entry["store"]}: {entry["devices"]} devices')

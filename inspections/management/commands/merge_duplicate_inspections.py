"""Report (and optionally merge) two inspections of one store, one date, one slot.

``StoreInspection`` now carries ``uniq_store_inspection_location_date_slot`` (added
in migration 0007), so a second row for the same
(location, inspection_date, slot) is refused at INSERT time. That migration cannot
be deployed while such rows already exist: the container entrypoint runs ``migrate``
on start, so a duplicate left in production data means the app never comes up. This
command is the safety net that has to be run *before* that migration goes out.

``slot`` is part of the key because a morning and an afternoon visit at one store on
one day are two legitimate rounds, not a duplicate: the day view reads
``(date, AM)`` and ``(date, PM)`` buckets and the bundle export names one folder per
slot. Grouping on (location, date) alone would therefore report a healthy schedule as
a duplicate and ``--apply`` would merge two real rounds into one.

**The default is a dry run.** Nothing is written unless ``--apply`` is passed.

What is reported per group: every row's child counts (devices, photos, issues,
signoff logs, wifi weak points) and whether it holds a generated deliverable
(report workbook / signatures), which row would survive, and — for ``--apply`` —
whether the group is mergeable at all.

Survivor rule: the **oldest row wins**, ordered by ``created_at`` and then by
``id`` as the deterministic tie-break. ``id`` alone is not an age ordering here —
pks are random UUID4 — so ``created_at`` carries the rule and ``id`` only makes
the choice reproducible. Every other row in the group is deleted after its
non-photo children are re-parented onto the survivor.

Rows with a NULL ``location`` are **excluded from the grouping on purpose**. Both
PostgreSQL and SQLite treat NULL as a distinct value in a unique index, so those
rows are not constrained by 0007 at all (any number of them may share a date), and
"merging" them would destroy data the constraint does not forbid. The count of
such rows is printed so nobody reads the empty report as "nothing to do".

Never-automated part: **photos are not merged.** Re-parenting an
``InspectionPhoto`` moves the row but not the bytes it points at, which stay under
``inspections/<owning-inspection-id>/photos/``, and the evidence trail (which round
shot which photo) is exactly what a duplicate pair makes ambiguous. So a group is
refused — reason printed, non-zero exit — when

* more than one row in it has photos, or
* the only photo-bearing row is not the survivor (that would be a photo merge), or
* a row that would be dropped holds a report workbook or a signature (deleting it
  orphans bytes, which is a human call), or
* a row that would be dropped is a completed/submitted round and the survivor is
  not: only child rows move, so that round's cover and confirmation values (times,
  counts, ratings, notes) would be lost with it, or
* a dropped row's device ``client_device_uid`` already exists on the survivor, so
  the move would violate ``uniq_inspection_device_uid``.

Everything else on the survivor is left exactly as it is - status, dates, engineer
and batch are not reconciled from the dropped rows, which is why the finished-round
case above is a refusal rather than a silent choice. ``slot`` needs no reconciliation:
every row in a group carries the same one, because the group is keyed on it.

Idempotent by construction: once a group is merged it stops being a duplicate and
is no longer reported.

Usage:
  python manage.py merge_duplicate_inspections            # the dry run, which is the default
  python manage.py merge_duplicate_inspections --dry-run  # same thing, spelled the usual way
  python manage.py merge_duplicate_inspections --json
  python manage.py merge_duplicate_inspections --apply
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from inspections.models import (
    InspectionDevice,
    InspectionIssue,
    InspectionSignoffLog,
    InspectionWifiWeakPoint,
    StoreInspection,
)

# Child models re-parented onto the survivor on merge. Photos are deliberately
# absent — see the module docstring.
MOVABLE_CHILDREN = (
    ('devices', InspectionDevice),
    ('issues', InspectionIssue),
    ('signoff_logs', InspectionSignoffLog),
    ('wifi_weak_points', InspectionWifiWeakPoint),
)

# Per-row counters, in the order they are printed.
CHILD_KEYS = ('devices', 'photos', 'issues', 'signoff_logs', 'wifi_weak_points')


class Command(BaseCommand):
    help = ('Report duplicate store inspections for the same (location, '
            'inspection_date, slot) and, with --apply, merge them into the oldest '
            'row. Default is a dry run. Photos are never auto-merged: a group whose '
            'photo evidence is ambiguous is refused for a human to decide.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually merge the mergeable groups (default: dry run)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Explicit no-op: this is already the default. Accepted because '
                                 'every other command in this app spells the preview mode this way')
        parser.add_argument('--json', action='store_true',
                            help='Emit machine-readable JSON instead of text')

    def _unconstrained_null_location_count(self):
        return StoreInspection.objects.filter(location__isnull=True).count()

    def _duplicate_groups(self):
        """(location_id, inspection_date, slot) keys holding more than one inspection."""
        return list(
            StoreInspection.objects.exclude(location__isnull=True)
            .values('location_id', 'inspection_date', 'slot')
            .annotate(row_count=Count('id'))
            .filter(row_count__gt=1)
            .order_by('inspection_date', 'location_id', 'slot')
        )

    def _row_profile(self, inspection):
        """Child counts plus deliverable presence for one inspection row."""
        profile = {
            'id': str(inspection.id),
            'created_at': inspection.created_at.isoformat(),
            'status': inspection.status,
            'batch': inspection.batch.name if inspection.batch_id else None,
            'devices': inspection.devices.count(),
            'photos': inspection.photos.count(),
            'issues': inspection.issues.count(),
            'signoff_logs': inspection.signoff_logs.count(),
            'wifi_weak_points': inspection.wifi_weak_points.count(),
            'report_file': bool(inspection.report_file.name),
            'signature': bool(inspection.store_signature.name) or bool(inspection.engineer_signature.name),
        }
        profile['child_total'] = sum(profile[key] for key in CHILD_KEYS)
        return profile

    def _blockers(self, survivor, losers):
        """Reasons this group must not be auto-merged; empty means mergeable."""
        blockers = []
        photo_rows = [row for row in (survivor, *losers) if row.photos.exists()]
        if len(photo_rows) > 1:
            blockers.append(
                f'photos live on {len(photo_rows)} rows of this group - deciding which '
                'round owns the evidence is a human decision, not a merge'
            )
        elif photo_rows and photo_rows[0].pk != survivor.pk:
            blockers.append(
                f'the only photo-bearing row ({photo_rows[0].pk}) is not the survivor; '
                'keeping the oldest row would mean moving photos between inspections'
            )
        deliverables = [row for row in losers if row.report_file.name
                        or row.store_signature.name or row.engineer_signature.name]
        if deliverables:
            blockers.append(
                'row(s) ' + ', '.join(str(row.pk) for row in deliverables)
                + ' hold a report workbook or a signature; deleting them orphans those '
                'bytes, which needs a human'
            )
        finished = [row for row in losers
                    if row.status in (StoreInspection.Status.COMPLETED,
                                      StoreInspection.Status.SUBMITTED)
                    and survivor.status not in (StoreInspection.Status.COMPLETED,
                                                StoreInspection.Status.SUBMITTED)]
        if finished:
            blockers.append(
                'row(s) ' + ', '.join(str(row.pk) for row in finished) + ' are a finished '
                f'round ({", ".join(sorted({row.status for row in finished}))}) while the '
                'survivor is ' + survivor.status + '; only child rows move, so the cover and '
                'confirmation values the engineer entered there would be lost'
            )
        if losers:
            owned = set(InspectionDevice.objects.filter(store_inspection=survivor)
                        .values_list('client_device_uid', flat=True))
            clashing = set(InspectionDevice.objects.filter(store_inspection__in=losers)
                           .values_list('client_device_uid', flat=True)) & owned
            clashing.discard('')
            if clashing:
                blockers.append(
                    'device client_device_uid(s) ' + ', '.join(sorted(clashing))
                    + ' exist on both the survivor and a dropped row, so the move would '
                    'violate uniq_inspection_device_uid'
                )
        return blockers

    def _merge(self, survivor, losers):
        """Re-parent non-photo children onto the survivor, then delete the losers."""
        moved = {}
        with transaction.atomic():
            for key, model in MOVABLE_CHILDREN:
                queryset = model.objects.filter(store_inspection__in=losers)
                moved[key] = queryset.count()
                if moved[key]:
                    queryset.update(store_inspection=survivor)
            merged_ids = ', '.join(str(row.pk) for row in losers)
            note = (f'Merged duplicate inspection(s) {merged_ids} into this row by '
                    f'merge_duplicate_inspections --apply.')
            survivor.notes = f'{survivor.notes}\n{note}'.strip()
            survivor.save(update_fields=['notes', 'updated_at'])
            for row in losers:
                row.delete()
        return moved

    def _describe(self, group, profiles, survivor_id, moved, blockers, apply_changes):
        """Text lines for one group, shared by the dry run and --apply paths."""
        label = group['location_label'] or f'location {group["location_id"]}'
        self.stdout.write(
            f'{label} on {group["inspection_date"]} slot {group["slot"]} '
            f'- {len(profiles)} rows'
        )
        for profile in profiles:
            role = 'survivor' if profile['id'] == survivor_id else 'dropped '
            counts = '  '.join(f'{key} {profile[key]}' for key in CHILD_KEYS)
            self.stdout.write(
                f'  {role} {profile["id"]} (created {profile["created_at"]}, '
                f'status {profile["status"]}, batch {profile["batch"] or "-"}): {counts}'
            )
            deliverables = [name for name, flag in (
                ('report_file', profile['report_file']),
                ('signature', profile['signature']),
            ) if flag]
            if deliverables:
                self.stdout.write(f'          holds: {", ".join(deliverables)}')
        if blockers:
            self.stdout.write('  REFUSED:' if apply_changes else '  WOULD REFUSE:')
            for reason in blockers:
                self.stdout.write(f'    - {reason}')
        else:
            moved_text = '  '.join(f'{key} {moved[key]}' for key in moved) if moved else (
                '  '.join(f'{key} {sum(profile[key] for profile in profiles[1:])}'
                          for key in CHILD_KEYS if key != 'photos')
            )
            verb = 'MERGED' if apply_changes else 'WOULD MERGE'
            self.stdout.write(
                f'  {verb}: {moved_text} (photos never moved) into survivor {survivor_id}; '
                f'{len(profiles) - 1} row(s) {"deleted" if apply_changes else "would be deleted"}'
            )

    def handle(self, *args, **options):
        if options['apply'] and options['dry_run']:
            raise CommandError('--dry-run and --apply contradict each other.')
        apply_changes = options['apply']
        as_json = options['json']
        prefix = '' if apply_changes else '[DRY RUN] '

        groups = self._duplicate_groups()
        null_location_rows = self._unconstrained_null_location_count()

        reported = []
        merged_count = 0
        refused_count = 0
        for entry in groups:
            rows = list(
                StoreInspection.objects.filter(
                    location_id=entry['location_id'],
                    inspection_date=entry['inspection_date'],
                    slot=entry['slot'],
                ).select_related('location', 'batch')
                .order_by('created_at', 'id')
            )
            survivor, losers = rows[0], rows[1:]
            profiles = [self._row_profile(row) for row in rows]
            # Blockers are computed in the dry run too: the whole point of the
            # default mode is to tell the operator which groups --apply can fix.
            blockers = self._blockers(survivor, losers)
            moved = self._merge(survivor, losers) if (apply_changes and not blockers) else {}
            if blockers:
                refused_count += 1
            if moved:
                merged_count += 1
            reported.append({
                'location_id': str(entry['location_id']),
                'location_label': (rows[0].location.name
                                   or rows[0].location.code
                                   or str(rows[0].location_id)),
                'inspection_date': entry['inspection_date'].isoformat(),
                'slot': entry['slot'],
                'row_count': len(rows),
                'survivor_id': str(survivor.pk),
                'rows': profiles,
                'mergeable': not blockers,
                'merged': bool(moved),
                'moved_children': moved,
                'blockers': blockers,
            })

        if as_json:
            self.stdout.write(json.dumps({
                'dry_run': not apply_changes,
                'group_count': len(reported),
                'merged_count': merged_count,
                'refused_count': refused_count,
                'unconstrained_null_location_rows': null_location_rows,
                'groups': reported,
            }, ensure_ascii=False, indent=2))
        else:
            if not reported:
                self.stdout.write(
                    f'{prefix}No duplicate (location, inspection_date, slot) groups. '
                    'Nothing to merge before migrations/0007.'
                )
            else:
                self.stdout.write(
                    f'{prefix}{len(reported)} duplicate (location, inspection_date, slot) '
                    'group(s) found:'
                )
                for group in reported:
                    self.stdout.write('')
                    self._describe(group, group['rows'], group['survivor_id'],
                                   group['moved_children'], group['blockers'],
                                   apply_changes)
            if null_location_rows:
                self.stderr.write(
                    f'  ({null_location_rows} row(s) with a NULL location were not '
                    'grouped: a unique index treats NULLs as distinct on Postgres and '
                    'SQLite, so those rows stay unconstrained and are not this '
                    'migration\'s problem)'
                )

        if apply_changes and refused_count:
            raise CommandError(
                f'{refused_count} group(s) refused: merging them would move or delete photo '
                'evidence, a deliverable, or collide a device uid. Those are human decisions - '
                'see the REFUSED lines above. Groups that were safe have already been merged.'
            )

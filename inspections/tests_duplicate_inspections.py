"""Tests for the one-store-inspection-per-(store, date, slot) invariant.

Three things are pinned here, because all three are load-bearing for
``inspections/migrations/0007_remove_storeinspection_inspections_locatio_f449d8_idx_and_more``:

1. the constraint rejects a second row for one store+date+slot, still allows the
   morning and afternoon rounds of one store on one day (the shape the day view and
   the bundle export are built around), and stays silent about NULL locations
   (Postgres and SQLite both treat NULL as distinct in a unique index, so those rows
   are deliberately unconstrained);
2. ``merge_duplicate_inspections`` defaults to a dry run, groups by the same three
   columns the index does, and its ``--apply`` refuses any group whose photo evidence
   would move - re-parenting a photo row leaves the bytes under the dropped
   inspection's folder and makes the evidence trail ambiguous, which is the
   destruction this command exists to prevent;
3. ``reuse_historical_photos --prune-superseded`` is opt-in, only ever deletes the
   adopted prior round for the *same* store, date and slot, and removes that round's
   media through the storage API - a row ``delete()`` leaves the bytes behind.

The classes that need duplicate rows run the database at ``inspections/0006`` (see
``PreConstraintTestCase``): those rows exist only because 0007 had not landed, and
with the index live the ORM refuses to write the very data the cleanup command
reads. That is also why they are ``TransactionTestCase`` - the schema editor cannot
run inside the per-test transaction SQLite wraps tests in.
"""
import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings

from companies.models import Company, Location
from inspections.models import (
    InspectionDevice,
    InspectionIssue,
    InspectionPhoto,
    InspectionSignoffLog,
    StoreInspection,
)
from inspections.services.thumbnails import ensure_thumbnail, thumbnail_storage_name
from tests.storage import MEDIA_STORAGE_OVERRIDES, png_bytes

CONSTRAINT_NAME = 'uniq_store_inspection_location_date_slot'
STORE_DATE = date(2026, 9, 2)
OLDER_STAMP = datetime(2026, 8, 1, tzinfo=dt_timezone.utc)
NEWER_STAMP = datetime(2026, 8, 2, tzinfo=dt_timezone.utc)
DEFAULT_LOCATION = object()


class InspectionFixtureMixin:
    """One company, one store, and inspections whose age is pinned, not implied."""

    def setUp(self):
        self.company = Company.objects.create(name='Kering', code='KER')
        self.location = Location.objects.create(
            company=self.company, name='SZOL', code='22149',
            location_type=Location.LocationType.STORE,
        )

    def make_inspection(self, when=STORE_DATE, location=DEFAULT_LOCATION, stamp=None, **kwargs):
        kwargs.setdefault('jda_code', '22149')
        kwargs.setdefault('store_name', 'SZOL')
        if location is DEFAULT_LOCATION:
            location = self.location
        inspection = StoreInspection.objects.create(
            company=self.company, location=location, inspection_date=when, **kwargs,
        )
        # created_at is auto_now_add, so the survivor rule has to be pinned from
        # outside a test; otherwise which row survives depends on test speed.
        if stamp is not None:
            StoreInspection.objects.filter(pk=inspection.pk).update(created_at=stamp)
            inspection.refresh_from_db()
        return inspection

    def make_device(self, inspection, sn='PC1', uid=None):
        return InspectionDevice.objects.create(
            store_inspection=inspection,
            client_device_uid=uid or f'{inspection.pk}-{sn}',
            category='Desktop', sn=sn,
        )

    def make_photo(self, inspection, device=None, kind='overall', name='p.png'):
        photo = InspectionPhoto(
            store_inspection=inspection, device=device, kind=kind, sort_index=1,
        )
        photo.image.save(name, ContentFile(png_bytes()), save=False)
        photo.save()
        return photo

    def duplicate_pair(self, location=DEFAULT_LOCATION):
        """The pre-0007 shape: two rows for one store on one date, oldest first."""
        older = self.make_inspection(stamp=OLDER_STAMP, location=location)
        newer = self.make_inspection(stamp=NEWER_STAMP, location=location)
        return older, newer

    def run_command(self, *args, **kwargs):
        """Run a command, capture both streams, and let CommandError propagate."""
        out, err = StringIO(), StringIO()
        error = None
        try:
            call_command(*args, stdout=out, stderr=err, **kwargs)
        except CommandError as caught:
            error = caught
        return out.getvalue() + err.getvalue(), error


class PreConstraintTestCase(InspectionFixtureMixin, TransactionTestCase):
    """Base for tests that must be able to write a duplicate (store, date) pair."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Unapplies 0007, which is what drops the unique index. On SQLite a plain
        # UniqueConstraint lives inside the table definition, so this is the only
        # route that does not hand-edit a model's Meta from a test.
        MigrationExecutor(connection).migrate([('inspections', '0006_remove_photo_zip')])

    @classmethod
    def tearDownClass(cls):
        try:
            # The refusal tests deliberately leave duplicates behind, and
            # re-applying 0007 would (correctly) fail on them. This debris is the
            # test's, not production's, so it is cleared before the schema goes
            # back for every other class in the suite.
            StoreInspection.objects.all().delete()
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDownClass()


class UniqueConstraintTests(InspectionFixtureMixin, TestCase):
    """What the database now guarantees, and what it pointedly does not."""

    def test_second_row_for_the_same_store_date_and_slot_is_rejected(self):
        self.make_inspection()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.make_inspection(status=StoreInspection.Status.COMPLETED)
        self.assertEqual(StoreInspection.objects.count(), 1)

    def test_the_same_store_in_both_slots_on_one_date_is_allowed(self):
        """A morning and an afternoon visit are two rounds, not a duplicate.

        This is the case a (location, date) index would have destroyed: the day view
        reads ``(date, am)`` and ``(date, pm)`` buckets, and the bundle export names
        one folder per slot - so the constraint has to carry the slot or the feature
        becomes unrepresentable at INSERT time.
        """
        morning = self.make_inspection()
        afternoon = self.make_inspection(slot=StoreInspection.Slot.PM)
        self.assertEqual(StoreInspection.objects.count(), 2)
        self.assertEqual(morning.slot, StoreInspection.Slot.AM)
        self.assertEqual(afternoon.slot, StoreInspection.Slot.PM)

    def test_same_store_on_a_different_date_is_allowed(self):
        self.make_inspection()
        self.make_inspection(when=STORE_DATE + timedelta(days=1))
        self.assertEqual(StoreInspection.objects.count(), 2)

    def test_two_null_location_rows_on_one_date_are_both_allowed(self):
        """NULL is distinct in a unique index on Postgres and SQLite alike.

        Location-less rows are outside 0007 and must stay insertable, or the
        migration would itself be a data-loss trap for rows it does not even
        constrain.
        """
        self.make_inspection(location=None)
        self.make_inspection(location=None, status=StoreInspection.Status.COMPLETED)
        self.assertEqual(StoreInspection.objects.filter(location__isnull=True).count(), 2)

    def test_the_constraint_is_declared_by_name_on_the_model(self):
        self.assertIn(CONSTRAINT_NAME, {c.name for c in StoreInspection._meta.constraints})


class MergeDryRunTests(PreConstraintTestCase):
    """The default path reports and mutates nothing."""

    def test_detects_the_group_and_changes_nothing(self):
        older, newer = self.duplicate_pair()
        self.make_device(older, sn='PC1')
        InspectionIssue.objects.create(store_inspection=newer, seq=1, description='dead switch')
        InspectionSignoffLog.objects.create(
            store_inspection=older, description='signoff', operation='signoff',
        )

        out, error = self.run_command('merge_duplicate_inspections')
        self.assertIsNone(error, out)
        self.assertIn(f'{STORE_DATE} slot {StoreInspection.Slot.AM} - 2 rows', out)
        self.assertIn('WOULD MERGE', out)
        self.assertNotIn('MERGED:', out)
        self.assertEqual(StoreInspection.objects.count(), 2)
        self.assertEqual(InspectionDevice.objects.filter(store_inspection=older).count(), 1)
        self.assertEqual(InspectionDevice.objects.filter(store_inspection=newer).count(), 0)
        self.assertEqual(InspectionIssue.objects.filter(store_inspection=newer).count(), 1)
        self.assertEqual(InspectionSignoffLog.objects.filter(store_inspection=older).count(), 1)
        older.refresh_from_db()
        self.assertEqual(older.notes, '')

    def test_names_the_survivor_by_the_pinned_oldest_row(self):
        older, newer = self.duplicate_pair()
        out, _error = self.run_command('merge_duplicate_inspections')
        self.assertIn(f'survivor {older.pk}', out)
        self.assertIn(f'dropped  {newer.pk}', out)

    def test_null_location_rows_are_excluded_from_the_grouping(self):
        """Two NULL locations on one date are not a duplicate: the constraint
        allows them, so the cleanup must not merge them away."""
        self.make_inspection(location=None, stamp=OLDER_STAMP)
        self.make_inspection(location=None, stamp=NEWER_STAMP)
        out, _error = self.run_command('merge_duplicate_inspections')
        self.assertIn('No duplicate', out)
        self.assertIn('NULL location', out)
        self.assertEqual(StoreInspection.objects.filter(location__isnull=True).count(), 2)

    def test_two_slots_of_one_day_are_not_a_group(self):
        """Grouping follows the index, so a healthy AM/PM schedule is not "duplicate".

        Were the group keyed on (location, date) alone, ``--apply`` would fold the
        afternoon round into the morning one: two real visits, one row, and the
        afternoon's devices re-parented onto the wrong report.
        """
        self.make_inspection(stamp=OLDER_STAMP)
        self.make_inspection(stamp=NEWER_STAMP, slot=StoreInspection.Slot.PM)
        out, error = self.run_command('merge_duplicate_inspections')
        self.assertIsNone(error, out)
        self.assertIn('No duplicate', out)
        self.assertNotIn('WOULD MERGE', out)
        self.assertEqual(StoreInspection.objects.count(), 2)

    def test_dry_run_flag_is_the_default_and_conflicts_with_apply(self):
        """docs/DATABASE_SCHEMA.md points at ``--dry-run``, so the word is accepted
        even though it is what happens when nothing is passed - and it may not be
        passed together with ``--apply``, which would silently pick one of them."""
        _older, _newer = self.duplicate_pair()
        out, error = self.run_command('merge_duplicate_inspections', dry_run=True)
        self.assertIsNone(error, out)
        self.assertIn('[DRY RUN]', out)
        self.assertEqual(StoreInspection.objects.count(), 2)
        out, error = self.run_command('merge_duplicate_inspections', dry_run=True, apply=True)
        self.assertIsNotNone(error, out)
        self.assertIn('contradict', str(error))
        self.assertEqual(StoreInspection.objects.count(), 2)

    def test_json_output_describes_the_group(self):
        self.duplicate_pair()
        out, _error = self.run_command('merge_duplicate_inspections', json=True)
        payload = json.loads(out)
        self.assertTrue(payload['dry_run'])
        self.assertEqual(payload['group_count'], 1)
        self.assertEqual(payload['refused_count'], 0)
        self.assertEqual(payload['unconstrained_null_location_rows'], 0)
        group = payload['groups'][0]
        self.assertEqual(group['row_count'], 2)
        self.assertTrue(group['mergeable'])
        self.assertEqual([row['devices'] for row in group['rows']], [0, 0])


@override_settings(**MEDIA_STORAGE_OVERRIDES)
class MergeApplyTests(PreConstraintTestCase):
    """``--apply`` merges what is unambiguous and refuses anything touching evidence."""

    def test_apply_merges_a_group_whose_photos_live_on_only_one_row(self):
        older, newer = self.duplicate_pair()
        photo = self.make_photo(older, device=self.make_device(older, sn='PC1'))
        self.make_device(newer, sn='MON1')
        InspectionIssue.objects.create(store_inspection=newer, seq=1, description='dead switch')
        InspectionSignoffLog.objects.create(
            store_inspection=newer, description='signoff', operation='signoff',
        )

        out, error = self.run_command('merge_duplicate_inspections', apply=True)
        self.assertIsNone(error, out)
        self.assertIn('MERGED', out)
        self.assertFalse(StoreInspection.objects.filter(pk=newer.pk).exists())

        survivor = StoreInspection.objects.get(pk=older.pk)
        self.assertEqual(survivor.devices.count(), 2)
        self.assertEqual(survivor.issues.count(), 1)
        self.assertEqual(survivor.signoff_logs.count(), 1)
        # The photo row never moved, and its bytes are still reachable.
        self.assertEqual(survivor.photos.count(), 1)
        self.assertEqual(InspectionPhoto.objects.get(pk=photo.pk).store_inspection_id, older.pk)
        self.assertTrue(survivor.photos.first().image.name)
        self.assertIn(str(newer.pk), survivor.notes)

    def test_apply_refuses_a_group_where_two_rows_both_have_photos(self):
        older, newer = self.duplicate_pair()
        self.make_photo(older, device=self.make_device(older, sn='PC1'))
        self.make_photo(newer, device=self.make_device(newer, sn='MON1'), name='q.png')

        out, error = self.run_command('merge_duplicate_inspections', apply=True)
        self.assertIsNotNone(error, out)
        self.assertIn('REFUSED', out)
        self.assertIn('human decision', out)
        self.assertIn('refused', str(error))
        self.assertEqual(StoreInspection.objects.count(), 2)
        self.assertEqual(InspectionPhoto.objects.filter(store_inspection=older).count(), 1)
        self.assertEqual(InspectionPhoto.objects.filter(store_inspection=newer).count(), 1)

    def test_apply_refuses_when_the_photos_would_have_to_move(self):
        """Photos on the *dropped* row only: keeping the oldest row would mean
        re-parenting them, the one thing this command never does."""
        _older, newer = self.duplicate_pair()
        self.make_photo(newer, device=self.make_device(newer, sn='PC1'))
        out, error = self.run_command('merge_duplicate_inspections', apply=True)
        self.assertIsNotNone(error, out)
        self.assertIn('not the survivor', out)
        self.assertEqual(StoreInspection.objects.count(), 2)

    def test_apply_refuses_when_a_dropped_row_is_the_finished_round(self):
        """Only child rows move, so merging a completed round into a planned shell
        would silently discard that engineer's cover/confirmation answers."""
        older, newer = self.duplicate_pair()
        newer.status = StoreInspection.Status.COMPLETED
        newer.store_issue_found_count = 7
        newer.save()
        out, error = self.run_command('merge_duplicate_inspections', apply=True)
        self.assertIsNotNone(error, out)
        self.assertIn('finished round', out)
        self.assertEqual(StoreInspection.objects.count(), 2)

    def test_apply_refuses_when_a_dropped_row_holds_the_deliverable(self):
        _older, newer = self.duplicate_pair()
        newer.report_file.save('report.xlsx', ContentFile(b'xlsx-bytes'), save=False)
        newer.save()
        out, error = self.run_command('merge_duplicate_inspections', apply=True)
        self.assertIsNotNone(error, out)
        self.assertIn('report workbook', out)
        self.assertEqual(StoreInspection.objects.count(), 2)

    def test_apply_merges_the_safe_group_and_still_refuses_the_ambiguous_one(self):
        """Partial progress plus a non-zero exit: the run says "not finished"
        instead of pretending nothing was refused."""
        _safe_older, safe_newer = self.duplicate_pair()
        self.make_device(safe_newer, sn='MON1')
        other = Location.objects.create(
            company=self.company, name='PKPP', code='31000',
            location_type=Location.LocationType.STORE,
        )
        bad_older, bad_newer = self.duplicate_pair(location=other)
        self.make_photo(bad_older, device=self.make_device(bad_older, sn='PC1'))
        self.make_photo(bad_newer, device=self.make_device(bad_newer, sn='PC1'), name='q.png')

        out, error = self.run_command('merge_duplicate_inspections', apply=True)
        self.assertIsNotNone(error, out)
        self.assertFalse(StoreInspection.objects.filter(pk=safe_newer.pk).exists())
        self.assertEqual(StoreInspection.objects.filter(location=other).count(), 2)


@override_settings(**MEDIA_STORAGE_OVERRIDES)
class PruneSupersededTests(PreConstraintTestCase):
    """``reuse_historical_photos --prune-superseded`` is opt-in and narrow."""

    def _adopted_pair(self, source_date=STORE_DATE, source_slot=StoreInspection.Slot.AM):
        """A planned AM target plus a prior round for the same store carrying a photo."""
        prior = self.make_inspection(when=source_date, slot=source_slot, stamp=OLDER_STAMP)
        target = self.make_inspection(stamp=NEWER_STAMP)
        self.make_photo(prior, device=self.make_device(prior, sn='MON1', uid='p-mon1'))
        self.make_device(target, sn='MON1', uid='t-mon1')
        return prior, target

    def test_prune_superseded_defaults_off_and_leaves_both_rows(self):
        prior, target = self._adopted_pair()
        out, error = self.run_command('reuse_historical_photos', inspection=str(target.pk))
        self.assertIsNone(error, out)
        self.assertTrue(StoreInspection.objects.filter(pk=prior.pk).exists())
        self.assertTrue(StoreInspection.objects.filter(pk=target.pk).exists())
        self.assertEqual(target.photos.count(), 1)

    def test_prune_superseded_deletes_the_adopted_same_date_round(self):
        prior, target = self._adopted_pair()
        out, error = self.run_command('reuse_historical_photos', inspection=str(target.pk),
                                      prune_superseded=True)
        self.assertIsNone(error, out)
        self.assertIn('pruned superseded', out)
        self.assertFalse(StoreInspection.objects.filter(pk=prior.pk).exists())
        self.assertEqual(target.photos.count(), 1)
        self.assertEqual(InspectionPhoto.objects.filter(store_inspection=prior).count(), 0)

    def test_prune_superseded_deletes_the_superseded_round_media(self):
        """``delete()`` drops rows, not objects - so the prune must delete bytes.

        Without the storage calls the row disappears and its original plus the
        derived thumbnail stay on the bucket forever, named only by a primary key
        that no longer exists, so no command can find them again. The adopted copy
        belongs to the target and must survive the prune untouched.
        """
        prior, target = self._adopted_pair()
        photo = prior.photos.first()
        self.assertTrue(ensure_thumbnail(photo))
        source_image = photo.image.name
        source_thumb = thumbnail_storage_name(photo)
        self.assertTrue(default_storage.exists(source_image))
        self.assertTrue(default_storage.exists(source_thumb))

        out, error = self.run_command('reuse_historical_photos', inspection=str(target.pk),
                                      prune_superseded=True)
        self.assertIsNone(error, out)
        self.assertFalse(default_storage.exists(source_image), 'original bytes leaked')
        self.assertFalse(default_storage.exists(source_thumb), 'thumbnail bytes leaked')

        adopted = target.photos.first()
        self.assertTrue(default_storage.exists(adopted.image.name))
        self.assertTrue(default_storage.exists(thumbnail_storage_name(adopted)))

    def test_prune_superseded_never_touches_a_round_from_another_date(self):
        prior, target = self._adopted_pair(source_date=date(2025, 9, 2))
        out, _error = self.run_command('reuse_historical_photos', inspection=str(target.pk),
                                       prune_superseded=True)
        self.assertNotIn('pruned superseded', out)
        self.assertTrue(StoreInspection.objects.filter(pk=prior.pk).exists())

    def test_prune_superseded_never_touches_the_other_slot_of_one_day(self):
        """Reuse borrows the afternoon round's photos; it does not delete the visit.

        Adoption copies, so the AM target gains the evidence and the PM row still
        stands as the record of a round that happened - and 0007 permits it, so this
        is not the duplicate shape the flag exists to clear.
        """
        prior, target = self._adopted_pair(source_slot=StoreInspection.Slot.PM)
        out, _error = self.run_command('reuse_historical_photos', inspection=str(target.pk),
                                       prune_superseded=True)
        self.assertNotIn('pruned superseded', out)
        self.assertTrue(StoreInspection.objects.filter(pk=prior.pk).exists())
        self.assertEqual(target.photos.count(), 1)

    def test_prune_superseded_keeps_a_round_whose_evidence_was_not_adopted(self):
        """An issue row on the prior round is not copied by reuse, so pruning would
        delete evidence: the flag keeps the row instead."""
        prior, target = self._adopted_pair()
        InspectionIssue.objects.create(store_inspection=prior, seq=1, description='dead switch')
        out, _error = self.run_command('reuse_historical_photos', inspection=str(target.pk),
                                       prune_superseded=True)
        self.assertIn('kept', out)
        self.assertTrue(StoreInspection.objects.filter(pk=prior.pk).exists())

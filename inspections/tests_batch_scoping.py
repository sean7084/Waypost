"""Batch scoping and the unbatched-inspection recovery path.

Regression coverage for the report "the dashboard shows inspections but
/inspections/batches/ shows nothing": batches used to be filtered by
``get_accessible_companies()`` while inspections are not, so an IT administrator
could see inspections whose batch was invisible (and unopenable) to them.
"""
import io
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from accounts.models import AdminRole
from companies.models import Company, Division, Location
from inspections.models import InspectionBatch, StoreInspection

User = get_user_model()


def _ensure_roles():
    for code, name in [('superadmin', 'Superadmin'), ('it_administrator', 'IT Administrator'),
                       ('inspection_engineer', 'Field Engineer')]:
        AdminRole.objects.get_or_create(code=code, defaults={'name': name, 'is_active': True})


class BatchScopingTestBase(TestCase):
    def setUp(self):
        _ensure_roles()
        # Company the IT admin manages, and a client company they do not.
        self.home = Company.objects.create(name='iStore Tech', code='IST')
        self.client_company = Company.objects.create(name='Kering', code='KER')
        self.division = Division.objects.create(
            company=self.client_company, name='YSL', code='YSL')
        self.location = Location.objects.create(
            company=self.client_company, division=self.division, name='Store', code='38076',
            location_type=Location.LocationType.STORE,
        )

        self.superadmin = User.objects.create_user(username='root', password='pw')
        self.superadmin.set_admin_roles([User.AdminRole.SUPERADMIN])

        self.it_admin = User.objects.create_user(username='itadmin', password='pw')
        self.it_admin.managed_company = self.home
        self.it_admin.save()
        self.it_admin.set_admin_roles([User.AdminRole.IT_ADMINISTRATOR])

        self.engineer = User.objects.create_user(username='fe', password='pw')
        self.engineer.set_admin_roles([User.AdminRole.INSPECTION_ENGINEER])

        # A batch in the client company: outside the IT admin's accessible companies.
        self.batch = InspectionBatch.objects.create(
            name='Kering 2026-09', company=self.client_company, division=self.division,
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 30),
        )
        self.inspection = StoreInspection.objects.create(
            company=self.client_company, division=self.division, location=self.location,
            jda_code='38076', brand_name='YSL', store_name='Store',
            store_label='38076 YSL Store', inspection_date=date(2026, 9, 10),
            batch=self.batch, engineer=self.engineer,
        )

    def get_batch_list(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('inspections:batch_list'))


class BatchScopingTests(BatchScopingTestBase):
    def test_it_admin_sees_batch_of_inspections_they_can_see(self):
        """The bug: an IT admin sees every inspection, so they must see its batch."""
        self.assertIn(self.inspection, self.it_admin.get_assigned_inspections())
        response = self.get_batch_list(self.it_admin)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.batch, list(response.context['batches']))
        self.assertContains(response, 'Kering 2026-09')

    def test_it_admin_can_open_that_batch(self):
        self.client.force_login(self.it_admin)
        response = self.client.get(reverse('inspections:batch_detail', args=[self.batch.id]))
        self.assertEqual(response.status_code, 200)

    def test_superadmin_sees_all_batches(self):
        response = self.get_batch_list(self.superadmin)
        self.assertIn(self.batch, list(response.context['batches']))

    def test_engineer_sees_batch_containing_own_inspection(self):
        response = self.get_batch_list(self.engineer)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.batch, list(response.context['batches']))

    def test_engineer_does_not_see_unrelated_batch(self):
        other = InspectionBatch.objects.create(
            name='Kering 2026-10', company=self.client_company,
            start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
        )
        response = self.get_batch_list(self.engineer)
        batches = list(response.context['batches'])
        self.assertIn(self.batch, batches)
        self.assertNotIn(other, batches)

    def test_dashboard_and_review_dropdowns_use_the_same_scope(self):
        self.client.force_login(self.it_admin)
        for name in ('inspections:dashboard', 'inspections:review'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertIn(self.batch, list(response.context['batches']), name)


class UnbatchedHintTests(BatchScopingTestBase):
    def test_batch_list_reports_unbatched_inspections(self):
        StoreInspection.objects.create(
            company=self.client_company, division=self.division, location=self.location,
            jda_code='38114', brand_name='YSL', store_name='Other',
            store_label='38114 YSL Other', inspection_date=date(2026, 9, 11),
        )
        response = self.get_batch_list(self.superadmin)
        self.assertEqual(response.context['unbatched_count'], 1)
        self.assertContains(response, 'not assigned to any batch')

    def test_no_hint_when_everything_is_batched(self):
        response = self.get_batch_list(self.superadmin)
        self.assertEqual(response.context['unbatched_count'], 0)
        self.assertNotContains(response, 'not assigned to any batch')


class GroupUnbatchedInspectionsTests(BatchScopingTestBase):
    def setUp(self):
        super().setUp()
        # Detach the seeded inspection and add one in another month.
        self.inspection.batch = None
        self.inspection.save(update_fields=['batch', 'updated_at'])
        self.later = StoreInspection.objects.create(
            company=self.client_company, division=self.division, location=self.location,
            jda_code='38114', brand_name='YSL', store_name='Other',
            store_label='38114 YSL Other', inspection_date=date(2026, 10, 5),
        )

    def test_groups_by_company_month_and_links(self):
        out = io.StringIO()
        call_command('group_unbatched_inspections', stdout=out)
        self.assertEqual(InspectionBatch.objects.filter(name='Kering 2026-09').count(), 1)
        september = InspectionBatch.objects.get(name='Kering 2026-09')
        october = InspectionBatch.objects.get(name='Kering 2026-10')
        self.inspection.refresh_from_db()
        self.later.refresh_from_db()
        self.assertEqual(self.inspection.batch_id, september.id)
        self.assertEqual(self.later.batch_id, october.id)
        self.assertEqual(september.division_id, self.division.id)
        self.assertIn('Adopting 2 inspection(s) into 2 batch(es)', out.getvalue())

    def test_existing_batch_is_reused_and_widened(self):
        self.batch.start_date = date(2026, 9, 20)
        self.batch.end_date = date(2026, 9, 20)
        self.batch.save()
        call_command('group_unbatched_inspections')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.start_date, date(2026, 9, 10))
        self.inspection.refresh_from_db()
        self.assertEqual(self.inspection.batch_id, self.batch.id)
        # The October inspection still got its own new batch.
        self.assertTrue(InspectionBatch.objects.filter(name='Kering 2026-10').exists())

    def test_dry_run_writes_nothing(self):
        out = io.StringIO()
        call_command('group_unbatched_inspections', '--dry-run', stdout=out)
        self.assertIn('[DRY RUN]', out.getvalue())
        self.assertEqual(InspectionBatch.objects.count(), 1)  # only the seeded batch
        self.inspection.refresh_from_db()
        self.assertIsNone(self.inspection.batch_id)

    def test_idempotent_second_run(self):
        call_command('group_unbatched_inspections')
        created = InspectionBatch.objects.count()
        out = io.StringIO()
        call_command('group_unbatched_inspections', stdout=out)
        self.assertEqual(InspectionBatch.objects.count(), created)
        self.assertIn('No unbatched inspections to adopt.', out.getvalue())

    def test_weekly_grouping_and_company_filter(self):
        call_command('group_unbatched_inspections', '--by', 'week', '--company', 'KER')
        self.assertTrue(InspectionBatch.objects.filter(name='Kering 2026-W37').exists())
        self.assertTrue(InspectionBatch.objects.filter(name='Kering 2026-W41').exists())

    def test_unknown_company_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command('group_unbatched_inspections', '--company', 'NOPE')

    def test_single_named_batch_adopts_every_period(self):
        out = io.StringIO()
        call_command('group_unbatched_inspections', '--name', '2026 Kering Gucci & YSL', stdout=out)
        batch = InspectionBatch.objects.get(name='2026 Kering Gucci & YSL')
        self.inspection.refresh_from_db()
        self.later.refresh_from_db()
        self.assertEqual(self.inspection.batch_id, batch.id)
        self.assertEqual(self.later.batch_id, batch.id)
        self.assertEqual(batch.start_date, date(2026, 9, 10))
        self.assertEqual(batch.end_date, date(2026, 10, 5))
        self.assertEqual(batch.company_id, self.client_company.id)
        self.assertEqual(batch.division_id, self.division.id)
        self.assertIn('Adopting 2 inspection(s) into 1 batch(es)', out.getvalue())

    def test_single_named_batch_rejects_mixed_companies(self):
        other_company = Company.objects.create(name='Another Client', code='OTH')
        StoreInspection.objects.create(
            company=other_company, jda_code='99999', brand_name='Other', store_name='Store',
            store_label='99999 Other Store', inspection_date=date(2026, 9, 12),
        )
        with self.assertRaises(CommandError):
            call_command('group_unbatched_inspections', '--name', 'Everything')

    def test_single_named_batch_honours_company_filter(self):
        other_company = Company.objects.create(name='Another Client', code='OTH')
        StoreInspection.objects.create(
            company=other_company, jda_code='99999', brand_name='Other', store_name='Store',
            store_label='99999 Other Store', inspection_date=date(2026, 9, 12),
        )
        call_command('group_unbatched_inspections', '--name', 'Kering only', '--company', 'KER')
        batch = InspectionBatch.objects.get(name='Kering only')
        self.assertEqual(batch.company_id, self.client_company.id)
        self.assertEqual(batch.inspections.count(), 2)
        # The other company's inspection is still unassigned.
        self.assertEqual(StoreInspection.objects.filter(batch__isnull=True).count(), 1)

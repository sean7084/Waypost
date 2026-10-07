"""Dashboard window modes: month / week / day switching, navigation and scoping."""
import json
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AdminRole
from companies.models import Company, Division, Location
from inspections.models import InspectionBatch, StoreInspection

User = get_user_model()


class DashboardViewModeTests(TestCase):
    # 2026-10-05 is a Monday, so 2026-10-07 is the Wednesday of that week.
    ANCHOR = date(2026, 10, 7)

    def setUp(self):
        AdminRole.objects.get_or_create(
            code='superadmin', defaults={'name': 'Superadmin', 'is_active': True})
        self.company = Company.objects.create(name='Kering', code='KER')
        self.division = Division.objects.create(company=self.company, name='YSL', code='YSL')
        self.location = Location.objects.create(
            company=self.company, division=self.division, name='Store', code='38076',
            location_type=Location.LocationType.STORE)
        self.user = User.objects.create_user(username='admin', password='pw')
        self.user.set_admin_roles([User.AdminRole.SUPERADMIN])
        self.inspection = self.make_inspection(self.ANCHOR, StoreInspection.Slot.AM)
        self.client.force_login(self.user)

    def make_inspection(self, day, slot, jda='38076', **kwargs):
        return StoreInspection.objects.create(
            company=self.company, division=self.division, location=self.location,
            jda_code=jda, brand_name='YSL', store_name='Store',
            store_label=f'{jda} YSL Store', inspection_date=day, slot=slot, **kwargs)

    def get(self, **params):
        response = self.client.get(reverse('inspections:dashboard'), params)
        self.assertEqual(response.status_code, 200)
        return response

    @staticmethod
    def days(response):
        return json.loads(response.context['days_json'])

    def test_month_view_spans_the_calendar_month(self):
        response = self.get(view='month', date=self.ANCHOR.isoformat())
        self.assertEqual(response.context['view'], 'month')
        days = self.days(response)
        self.assertEqual(len(days), 31)
        self.assertEqual(days[0]['date'], '2026-10-01')
        self.assertEqual(days[-1]['date'], '2026-10-31')
        self.assertEqual(response.context['window_start'], date(2026, 10, 1))

    def test_week_view_is_monday_to_sunday(self):
        response = self.get(view='week', date=self.ANCHOR.isoformat())
        days = self.days(response)
        self.assertEqual(response.context['view'], 'week')
        self.assertEqual([day['date'] for day in days],
                         [f'2026-10-0{index}' for index in range(5, 10)] +
                         ['2026-10-10', '2026-10-11'])
        self.assertEqual(days[0]['weekday'], 'Mon')

    def test_day_view_is_a_single_row_holding_the_slot_cards(self):
        self.make_inspection(self.ANCHOR, StoreInspection.Slot.PM, jda='38114')
        response = self.get(view='day', date=self.ANCHOR.isoformat())
        days = self.days(response)
        self.assertEqual(response.context['view'], 'day')
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0]['date'], '2026-10-07')
        self.assertEqual(len(days[0]['am']), 1)
        self.assertEqual(len(days[0]['pm']), 1)

    def test_day_view_cards_carry_detail_fields(self):
        self.inspection.arriving_time = time(9, 30)
        self.inspection.leaving_time = time(11, 45)
        self.inspection.status = StoreInspection.Status.IN_PROGRESS
        self.inspection.save()
        card = self.days(self.get(view='day', date=self.ANCHOR.isoformat()))[0]['am'][0]
        self.assertEqual(card['jda'], '38076')
        self.assertEqual(card['arriving'], '09:30')
        self.assertEqual(card['leaving'], '11:45')
        self.assertEqual(card['status_display'], 'In Progress')
        self.assertEqual(card['brand'], 'YSL')

    def test_navigation_anchors_shift_by_a_whole_period(self):
        cases = {
            'month': ('2026-09-01', '2026-11-01'),
            'week': ('2026-09-30', '2026-10-14'),
            'day': ('2026-10-06', '2026-10-08'),
        }
        for view, (previous, following) in cases.items():
            response = self.get(view=view, date=self.ANCHOR.isoformat())
            self.assertEqual(response.context['prev_anchor'], previous, view)
            self.assertEqual(response.context['next_anchor'], following, view)
            self.assertEqual(response.context['anchor'], self.ANCHOR.isoformat(), view)

    def test_legacy_month_param_still_selects_the_month_view(self):
        response = self.get(month='2026-10')
        self.assertEqual(response.context['view'], 'month')
        self.assertEqual(response.context['window_start'], date(2026, 10, 1))
        self.assertEqual(len(self.days(response)), 31)

    def test_unknown_view_falls_back_to_month(self):
        response = self.get(view='year', date=self.ANCHOR.isoformat())
        self.assertEqual(response.context['view'], 'month')

    def test_defaults_to_the_current_month_without_params(self):
        response = self.get()
        today = response.context['today']
        self.assertEqual(response.context['view'], 'month')
        self.assertTrue(response.context['is_current_period'])
        self.assertEqual(response.context['window_start'].isoformat(), today[:8] + '01')

    def test_batch_selection_shows_the_whole_batch_range(self):
        batch = InspectionBatch.objects.create(
            name='Q4 cycle', company=self.company, division=self.division,
            start_date=date(2026, 10, 1), end_date=date(2026, 12, 31))
        self.inspection.batch = batch
        self.inspection.save(update_fields=['batch', 'updated_at'])
        response = self.get(batch=batch.id)
        self.assertEqual(response.context['view'], 'batch')
        self.assertEqual(response.context['window_start'], date(2026, 10, 1))
        self.assertEqual(response.context['window_end'], date(2026, 12, 31))
        self.assertEqual(len(self.days(response)), 92)
        # Batch mode has no single period to step through, so the arrows are off.
        self.assertEqual(response.context['prev_anchor'], '')
        self.assertEqual(response.context['next_anchor'], '')
        # An explicit view still wins over the batch range.
        self.assertEqual(self.get(batch=batch.id, view='day',
                                  date=self.ANCHOR.isoformat()).context['view'], 'day')

    def test_batch_selector_keeps_an_explicitly_requested_window(self):
        response = self.get(view='week', date=self.ANCHOR.isoformat())
        self.assertContains(response, 'name="view" value="week"')
        self.assertContains(response, f'name="date" value="{self.ANCHOR.isoformat()}"')

    def test_batch_selector_omits_the_window_by_default(self):
        """A bare ?batch= link must still open that batch's whole range."""
        response = self.get()
        self.assertNotContains(response, 'name="view" value=')
        self.assertNotContains(response, 'name="date" value=')

    def test_legacy_month_param_is_echoed_as_the_anchor(self):
        self.assertContains(self.get(month='2026-10'), 'name="date" value="2026-10"')

    def test_summary_tiles_are_scoped_to_the_window(self):
        self.make_inspection(date(2026, 11, 20), StoreInspection.Slot.AM, jda='38999')
        week = self.get(view='week', date=self.ANCHOR.isoformat())
        month = self.get(view='month', date=self.ANCHOR.isoformat())
        self.assertEqual(week.context['total_count'], 1)
        self.assertEqual(month.context['total_count'], 1)
        november = self.get(view='month', date='2026-11-01')
        self.assertEqual(november.context['total_count'], 1)
        self.assertEqual(november.context['planned_count'], 1)

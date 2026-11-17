"""Language/timezone defaults on the two user forms, and what the dropdown must not do.

Two failure modes here are invisible to a coverage number: a default that lives
only in the widget, and a ChoiceField that silently rewrites a zone saved before
the dropdown existed. Both are asserted behaviourally.
"""

from zoneinfo import ZoneInfo

from django import forms
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .forms import COMMON_TIMEZONES, SuperuserUserForm, UserSettingsForm
from .models import User

LEGACY_ZONE = 'Pacific/Chatham'  # a real IANA zone, deliberately not in the curated list


def create_payload(**overrides):
    payload = {
        'username': 'new_engineer',
        'email': 'new-engineer@example.com',
        'first_name': 'New',
        'last_name': 'Engineer',
        'employee_id': '',
        'phone_number': '',
        'department': '',
        'job_title': '',
        'company': '',
        'manager': '',
        'roles': [],
        'managed_company': '',
        'managed_divisions': [],
        'managed_locations': [],
        'language_preference': 'zh-cn',
        'timezone': 'Asia/Shanghai',
        'is_active': 'on',
        'must_change_password': 'on',
    }
    payload.update(overrides)
    return payload


class CommonTimezoneListTests(SimpleTestCase):
    def test_every_offered_zone_is_a_real_iana_zone(self):
        for zone in COMMON_TIMEZONES:
            with self.subTest(zone=zone):
                ZoneInfo(zone)

    def test_list_has_no_duplicates_and_carries_both_defaults(self):
        self.assertEqual(len(COMMON_TIMEZONES), len(set(COMMON_TIMEZONES)))
        self.assertIn('Asia/Shanghai', COMMON_TIMEZONES)
        self.assertIn('UTC', COMMON_TIMEZONES)


class CreateUserFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username='admin_user', email='admin@example.com', password='AdminPass123!')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_create_page_preselects_chinese_and_shanghai(self):
        response = self.client.get(reverse('accounts:user_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<option value="zh-cn" selected>', html=False)
        self.assertContains(response, '<option value="Asia/Shanghai" selected>', html=False)

    def test_timezone_renders_as_a_select_not_a_text_input(self):
        response = self.client.get(reverse('accounts:user_create'))

        self.assertContains(response, '<select name="timezone"', html=False)
        self.assertNotContains(response, 'placeholder="UTC"', html=False)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_submitted_defaults_are_persisted(self):
        response = self.client.post(reverse('accounts:user_create'), create_payload())

        self.assertRedirects(response, reverse('accounts:user_list'))
        created = User.objects.get(username='new_engineer')
        self.assertEqual(created.language_preference, 'zh-cn')
        self.assertEqual(created.timezone, 'Asia/Shanghai')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_unknown_zone_is_rejected_and_creates_nothing(self):
        response = self.client.post(
            reverse('accounts:user_create'), create_payload(timezone='Mars/Olympus'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice')
        self.assertFalse(User.objects.filter(username='new_engineer').exists())

    def test_edit_keeps_a_zone_that_predates_the_dropdown(self):
        legacy = User.objects.create_user(
            username='legacy_zone', email='legacy@example.com', password='LegacyPass123!',
            timezone=LEGACY_ZONE)

        response = self.client.get(reverse('accounts:user_edit', args=[legacy.pk]))

        self.assertContains(response, f'<option value="{LEGACY_ZONE}" selected>', html=False)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_editing_other_fields_does_not_rewrite_a_legacy_zone(self):
        legacy = User.objects.create_user(
            username='legacy_zone', email='legacy@example.com', password='LegacyPass123!',
            first_name='Legacy', last_name='Zone', timezone=LEGACY_ZONE)
        payload = create_payload(
            username=legacy.username, email=legacy.email, first_name='Renamed',
            last_name='Zone', timezone=LEGACY_ZONE, is_staff='on')

        self.client.post(reverse('accounts:user_edit', args=[legacy.pk]), payload)

        legacy.refresh_from_db()
        self.assertEqual(legacy.timezone, LEGACY_ZONE)
        self.assertEqual(legacy.first_name, 'Renamed')


class UserSettingsFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='engineer', email='engineer@example.com', password='EngineerPass123!',
            language_preference='en-us', timezone='UTC')

    def setUp(self):
        self.client.force_login(self.user)

    def preferences_payload(self, **overrides):
        payload = {
            'save_preferences': '1',
            'preferences-language_preference': 'zh-cn',
            'preferences-timezone': 'Asia/Tokyo',
        }
        payload.update(overrides)
        return payload

    def test_timezone_is_a_choice_field(self):
        form = UserSettingsForm(instance=self.user)

        self.assertIsInstance(form.fields['timezone'], forms.ChoiceField)

    def test_unknown_zone_is_rejected_and_nothing_is_saved(self):
        response = self.client.post(
            reverse('accounts:settings'), self.preferences_payload(**{'preferences-timezone': 'Not/AZone'}))

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, 'UTC')
        self.assertEqual(self.user.language_preference, 'en-us')

    def test_valid_zone_and_language_are_saved(self):
        response = self.client.post(reverse('accounts:settings'), self.preferences_payload())

        self.assertRedirects(response, reverse('accounts:settings'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, 'Asia/Tokyo')
        self.assertEqual(self.user.language_preference, 'zh-cn')

    def test_legacy_zone_stays_selectable_and_survives_a_save(self):
        self.user.timezone = LEGACY_ZONE
        self.user.save(update_fields=['timezone'])

        page = self.client.get(reverse('accounts:settings'))
        self.assertContains(page, f'<option value="{LEGACY_ZONE}" selected>', html=False)

        self.client.post(reverse('accounts:settings'),
                         self.preferences_payload(**{'preferences-timezone': LEGACY_ZONE}))

        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, LEGACY_ZONE)


class WidgetClassTests(TestCase):
    def test_selects_carry_form_select_and_not_form_control(self):
        user = User.objects.create_user(
            username='widget_user', email='widget@example.com', password='WidgetPass123!')

        for form in (SuperuserUserForm(), UserSettingsForm(instance=user)):
            with self.subTest(form=type(form).__name__):
                classes = form.fields['timezone'].widget.attrs.get('class', '').split()
                self.assertIn('form-select', classes)
                self.assertNotIn('form-control', classes)
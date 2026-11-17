"""Two guards that used to be documentation instead of code.

``accounts/dev-login/`` logged anyone in as ``admin`` with no password, and the
route was registered unconditionally; ``WEB_ROUTES.md`` asked the operator to
"confirm it is unreachable when DJANGO_DEBUG=False". ``TEST_OUTBOUND_EMAIL_OVERRIDE``
defaulted to a real personal address and was honoured in production by both
outbound paths, so a ``.env`` missing one line redirected every customer email.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.mail import EmailMessage
from django.test import RequestFactory, TestCase, override_settings
from django.urls import NoReverseMatch, clear_url_caches, reverse

from accounts.email_backends import DatabaseSMTPEmailBackend, test_outbound_override
from accounts.models import SystemSMTPSettings, User
from invoices.services import _get_effective_dispatch_recipients

OVERRIDE = 'redirect@example.com'


def reload_urlconf():
    """Rebuild the URLconf so a DEBUG override actually changes the routes."""
    importlib.reload(importlib.import_module('accounts.urls'))
    importlib.reload(importlib.import_module(settings.ROOT_URLCONF))
    clear_url_caches()


class DevLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username='admin', email='admin@example.com', password='AdminPass123!')

    def test_route_does_not_exist_when_debug_is_off(self):
        self.addCleanup(reload_urlconf)
        with override_settings(DEBUG=False):
            reload_urlconf()
            with self.assertRaises(NoReverseMatch):
                reverse('accounts:dev_login')

    def test_route_exists_when_debug_is_on(self):
        self.addCleanup(reload_urlconf)
        with override_settings(DEBUG=True):
            reload_urlconf()
            self.assertTrue(reverse('accounts:dev_login').endswith('/dev-login/'))

    def test_view_refuses_when_debug_is_off_even_if_reached_directly(self):
        from django.http import Http404

        from accounts.views import dev_login
        request = RequestFactory().get('/accounts/dev-login/')

        with override_settings(DEBUG=False):
            with self.assertRaises(Http404):
                dev_login(request)

    def test_debug_login_still_works_for_development(self):
        self.addCleanup(reload_urlconf)
        with override_settings(DEBUG=True):
            reload_urlconf()
            response = self.client.get(reverse('accounts:dev_login'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.admin.pk))


class OutboundOverrideTests(TestCase):
    def test_override_is_empty_when_debug_is_off(self):
        with override_settings(DEBUG=False, TEST_OUTBOUND_EMAIL_OVERRIDE=OVERRIDE):
            self.assertEqual(test_outbound_override(), '')

    def test_override_is_honoured_when_debug_is_on(self):
        with override_settings(DEBUG=True, TEST_OUTBOUND_EMAIL_OVERRIDE=OVERRIDE):
            self.assertEqual(test_outbound_override(), OVERRIDE)

    def test_dispatch_recipients_survive_production(self):
        dispatch = SimpleNamespace(
            sent_to='customer@example.com', cc='colleague@example.com', bcc='archive@example.com')

        with override_settings(DEBUG=False, TEST_OUTBOUND_EMAIL_OVERRIDE=OVERRIDE):
            effective = _get_effective_dispatch_recipients(dispatch)

        self.assertEqual(effective['send_to'], ['customer@example.com'])
        self.assertEqual(effective['send_cc'], ['colleague@example.com'])
        self.assertEqual(effective['send_bcc'], ['archive@example.com'])

    def test_dispatch_recipients_are_redirected_in_debug(self):
        dispatch = SimpleNamespace(sent_to='customer@example.com', cc='', bcc='')

        with override_settings(DEBUG=True, TEST_OUTBOUND_EMAIL_OVERRIDE=OVERRIDE):
            effective = _get_effective_dispatch_recipients(dispatch)

        self.assertEqual(effective['send_to'], [OVERRIDE])
        self.assertEqual(effective['header_to'], [OVERRIDE])

    def _smtp_config(self):
        config = SystemSMTPSettings.get_solo()
        config.from_email = 'noreply@example.com'
        config.smtp_host = 'smtp.example.com'
        config.smtp_port = 587
        config.smtp_security = SystemSMTPSettings.ConnectionSecurity.STARTTLS
        config.is_active = True
        config.set_password('smtp-pass-123')
        config.save()

    def test_backend_leaves_recipients_alone_when_debug_is_off(self):
        self._smtp_config()
        captured = {}

        class DummySMTPBackend:
            def __init__(self, *args, **kwargs):
                pass

            def open(self):
                return True

            def close(self):
                return None

            def send_messages(self, messages):
                captured['messages'] = messages
                return len(messages)

        message = EmailMessage(
            subject='Invoice', body='body',
            from_email='noreply@example.com',
            to=['customer@example.com'], cc=['colleague@example.com'])

        with override_settings(DEBUG=False, TEST_OUTBOUND_EMAIL_OVERRIDE=OVERRIDE):
            with patch('accounts.email_backends.SMTPEmailBackend', DummySMTPBackend):
                DatabaseSMTPEmailBackend().send_messages([message])

        self.assertEqual(captured['messages'][0].to, ['customer@example.com'])
        self.assertEqual(captured['messages'][0].cc, ['colleague@example.com'])
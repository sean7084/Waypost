"""Tests for the project-level health probe (waypost/health.py).

The probe is the contract that deployment automation relies on: the container
HEALTHCHECK, `docker/bin/ci-deploy.sh` (which rolls a release back when the
probe does not come good) and host monitoring all consume it. These tests pin
the properties that matter - it answers without credentials, it stays reachable
over plain HTTP when SSL redirection is on, and it never leaks a dependency's
error message.
"""
import os
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings


class HealthEndpointTests(TestCase):
    """Behaviour of GET /healthz/ against the real (test) database and cache."""

    def test_healthy_response_is_200_and_reports_dependencies(self):
        response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['database'], 'ok')
        self.assertEqual(payload['cache'], 'ok')

    def test_requires_no_authentication(self):
        # An anonymous client must get a real answer, not a redirect to the
        # login page: a probe behind auth cannot detect an unhealthy app.
        response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_is_not_cached_and_not_indexed(self):
        response = self.client.get('/healthz/')

        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')

    def test_post_is_rejected(self):
        response = self.client.post('/healthz/')

        self.assertEqual(response.status_code, 405)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_stays_reachable_over_plain_http_when_ssl_redirect_is_on(self):
        # The container healthcheck and the deploy script poll Gunicorn on the
        # loopback interface with no proxy in front to set X-Forwarded-Proto, so
        # this path must be exempt (settings.SECURE_REDIRECT_EXEMPT) while every
        # real request is still redirected.
        probe = self.client.get('/healthz/')
        redirected = self.client.get('/admin/')

        self.assertEqual(probe.status_code, 200)
        self.assertEqual(redirected.status_code, 301)
        self.assertTrue(redirected['Location'].startswith('https://'))

    def test_reports_deployed_version_from_environment(self):
        with mock.patch.dict(os.environ, {'WAYPOST_VERSION': 'v9.9.9', 'WAYPOST_GIT_SHA': 'abc1234'}):
            payload = self.client.get('/healthz/').json()

        self.assertEqual(payload['version'], 'v9.9.9')
        self.assertEqual(payload['revision'], 'abc1234')


class HealthEndpointFailureTests(SimpleTestCase):
    """Degraded paths: status code, and no leakage of dependency internals."""

    SECRET_DSN = 'postgres://waypost_django:super-secret@10.0.0.9:5432/waypost_db'

    def test_database_failure_returns_503_without_leaking_the_message(self):
        def _explode(*args, **kwargs):
            raise RuntimeError(self.SECRET_DSN)

        with mock.patch('waypost.health.connections') as connections:
            connections.__getitem__.side_effect = _explode
            response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload['status'], 'degraded')
        # Only the exception class name is reported - the message could carry
        # the DSN, and this endpoint is public by design.
        self.assertEqual(payload['database'], 'RuntimeError')
        body = response.content.decode()
        self.assertNotIn(self.SECRET_DSN, body)
        self.assertNotIn('super-secret', body)

    def test_cache_failure_returns_503(self):
        with mock.patch('waypost.health.cache') as cache:
            cache.set.side_effect = ConnectionError('redis://127.0.0.1:6379/1 refused')
            response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['cache'], 'ConnectionError')

    def test_cache_read_back_mismatch_is_degraded(self):
        with mock.patch('waypost.health.cache') as cache:
            cache.get.return_value = None
            response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['cache'], 'read-back-mismatch')


class ProxySecuritySettingTests(SimpleTestCase):
    """Guards on the settings the probe and the reverse proxy depend on."""

    def test_healthz_is_listed_in_secure_redirect_exempt(self):
        self.assertIn(r'^healthz/$', settings.SECURE_REDIRECT_EXEMPT)

    def test_proxy_ssl_header_is_not_trusted_by_default(self):
        # Opt-in via DJANGO_SECURE_PROXY_SSL_HEADER only. Without it, a
        # developer running runserver directly must never have Django treat a
        # plain-HTTP request as secure just because a client said so.
        self.assertFalse(getattr(settings, 'SECURE_PROXY_SSL_HEADER', None))

    def test_request_hardening_is_off_under_the_test_runner(self):
        # `manage.py test` flips settings.DEBUG to False for the run, and a
        # production-shaped .env does the same permanently. Without the
        # _RUNNING_TESTS guard in settings.py, SECURE_SSL_REDIRECT would switch
        # on and every test-client request would assert against a 301 to https
        # instead of the page it asked for - 57 spurious failures that look like
        # an application bug.
        from waypost import settings as settings_module

        self.assertTrue(settings_module._RUNNING_TESTS)
        self.assertFalse(settings.SESSION_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_SECURE)
        self.assertFalse(settings.SECURE_SSL_REDIRECT)

from email.utils import formataddr

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend
from django.db.utils import OperationalError, ProgrammingError


def test_outbound_override():
    """The address that replaces every recipient, or '' when there is none.

    Debug-only on purpose. This setting used to default to a real personal
    address and was honoured regardless of DEBUG, so a production deploy whose
    ``.env`` omitted the key redirected every customer email to one inbox. Both
    outbound paths read it through here so they cannot drift apart again.
    """
    if not settings.DEBUG:
        return ''
    return (getattr(settings, 'TEST_OUTBOUND_EMAIL_OVERRIDE', '') or '').strip()


class DatabaseSMTPEmailBackend(BaseEmailBackend):
    """Resolve SMTP connection settings from the database at send time."""

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.timeout_override = kwargs.get('timeout')
        self._config = None
        self._backend = None

    def _get_config(self):
        if self._config is not None:
            return self._config

        from .models import SystemSMTPSettings

        try:
            self._config = SystemSMTPSettings.get_active()
        except (OperationalError, ProgrammingError):
            if self.fail_silently:
                return None
            raise

        if self._config is None and not self.fail_silently:
            raise ImproperlyConfigured('System SMTP settings are not configured.')
        return self._config

    def _get_backend(self):
        if self._backend is not None:
            return self._backend

        config = self._get_config()
        if config is None:
            return None

        self._backend = SMTPEmailBackend(
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.username or None,
            password=config.password or None,
            use_tls=config.use_tls,
            use_ssl=config.use_ssl,
            timeout=self.timeout_override or config.timeout or None,
            fail_silently=self.fail_silently,
        )
        return self._backend

    def open(self):
        backend = self._get_backend()
        if backend is None:
            return False
        return backend.open()

    def close(self):
        if self._backend is not None:
            return self._backend.close()
        return None

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        backend = self._get_backend()
        config = self._get_config()
        if backend is None or config is None:
            return 0

        default_from_email = config.from_email
        if config.from_display_name:
            default_from_email = formataddr((config.from_display_name, config.from_email))

        override_recipient = test_outbound_override()

        for email_message in email_messages:
            if not email_message.from_email:
                email_message.from_email = default_from_email
            if override_recipient:
                email_message.to = [override_recipient]
                email_message.cc = []
                email_message.bcc = []

        return backend.send_messages(email_messages)
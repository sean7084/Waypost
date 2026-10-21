"""Tests for the observability contract of ``accounts.crypto.decrypt_secret``.

``decrypt_secret`` must keep returning '' rather than raising (callers such as
``UserMailboxSettings.password`` and the mailbox sync treat an unreadable
credential as "not configured"), but a silent '' is indistinguishable from an
empty password everywhere except the log. These tests pin the three distinct
cases, and that the log line is safe to read out loud: counts and identifiers,
never the token, the plaintext or a key.
"""
import base64
import hashlib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.crypto import decrypt_secret, encrypt_secret
from accounts.models import SystemSMTPSettings, UserMailboxSettings

LOGGER_NAME = 'accounts.crypto'
PLAINTEXT = 'sup3r-s3cret-passw0rd'
# Neither valid Fernet nor valid url-safe base64, so both decrypt paths fail and
# the "token present but undecryptable" branch is the only one that can run.
CORRUPT_TOKEN = 'corrupt@@@token-not-base64'

User = get_user_model()


def _legacy_xor_value(plaintext):
    """Reproduce the pre-Fernet obfuscation so its read path can be exercised."""
    key = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    raw = plaintext.encode('utf-8')
    scrambled = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(raw))
    return base64.urlsafe_b64encode(scrambled).decode('ascii')


class DecryptSecretLoggingTests(TestCase):
    def test_undecryptable_token_logs_a_warning(self):
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(decrypt_secret(CORRUPT_TOKEN, context='mailbox pk=7'), '')
        message = captured.records[0].getMessage()
        self.assertIn('mailbox pk=7', message)
        self.assertIn('re-enter', message.lower())
        self.assertIn('DJANGO_FIELD_ENCRYPTION_KEY', message)

    def test_warning_names_the_symptom_the_user_sees(self):
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            decrypt_secret(CORRUPT_TOKEN, context='mailbox pk=7')
        message = captured.records[0].getMessage().lower()
        self.assertIn('empty password', message)
        self.assertIn('imap', message)

    def test_warning_omits_the_token_and_the_plaintext(self):
        token = encrypt_secret(PLAINTEXT)
        broken = token[:-4] + '!!!!'
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(decrypt_secret(broken, context='mailbox pk=7'), '')
        for record in captured.records:
            rendered = record.getMessage()
            self.assertNotIn(PLAINTEXT, rendered)
            self.assertNotIn(token, rendered)
            self.assertNotIn(broken, rendered)

    def test_missing_context_still_logs(self):
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(decrypt_secret(CORRUPT_TOKEN), '')
        self.assertIn('unknown mailbox', captured.records[0].getMessage())

    def test_empty_value_does_not_log(self):
        with self.assertNoLogs(LOGGER_NAME, level='WARNING'):
            self.assertEqual(decrypt_secret(''), '')

    def test_legacy_xor_value_decrypts_and_does_not_log(self):
        legacy = _legacy_xor_value(PLAINTEXT)
        with self.assertNoLogs(LOGGER_NAME, level='WARNING'):
            self.assertEqual(decrypt_secret(legacy, context='mailbox pk=7'), PLAINTEXT)

    def test_good_token_decrypts_and_logs_nothing(self):
        token = encrypt_secret(PLAINTEXT)
        with self.assertNoLogs(LOGGER_NAME, level='WARNING'):
            self.assertEqual(decrypt_secret(token, context='mailbox pk=7'), PLAINTEXT)


class PasswordPropertyContextTests(TestCase):
    """The two ``decrypt_secret`` call sites must pass a safe, useful label."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='mailbox_owner', password='pw')

    def _mailbox(self):
        return UserMailboxSettings.objects.create(
            user=self.user,
            email_address='orders@example-mailhost.com',
            username='orders@example-mailhost.com',
            imap_host='imap.example.com',
        )

    def test_mailbox_password_logs_the_pk_not_the_address(self):
        mailbox = self._mailbox()
        mailbox.encrypted_password = CORRUPT_TOKEN
        mailbox.save()
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(mailbox.password, '')
        message = captured.records[0].getMessage()
        self.assertIn(f'mailbox pk={mailbox.pk}', message)
        self.assertNotIn('orders@example-mailhost.com', message)
        self.assertNotIn(CORRUPT_TOKEN, message)

    def test_mailbox_context_names_the_host_it_connects_to(self):
        # An incident line that prints the address *domain* sends an operator at
        # the wrong server: this row authenticates against imap.example.com.
        mailbox = self._mailbox()
        mailbox.encrypted_password = CORRUPT_TOKEN
        mailbox.save()
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(mailbox.password, '')
        message = captured.records[0].getMessage()
        self.assertIn('IMAP host=imap.example.com', message)
        self.assertNotIn('example-mailhost.com', message)

    def test_pop3_mailbox_context_names_the_pop3_host(self):
        mailbox = self._mailbox()
        mailbox.receive_protocol = UserMailboxSettings.ReceiveProtocol.POP3
        mailbox.pop3_host = 'pop.example.com'
        mailbox.encrypted_password = CORRUPT_TOKEN
        mailbox.save()
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(mailbox.password, '')
        message = captured.records[0].getMessage()
        self.assertIn('POP3 host=pop.example.com', message)
        self.assertNotIn('imap.example.com', message)

    def test_mailbox_context_falls_back_to_the_domain_when_no_host_is_set(self):
        # imap_host is blankable, so the label must degrade instead of printing
        # "host=" with nothing after it.
        mailbox = self._mailbox()
        mailbox.imap_host = ''
        mailbox.encrypted_password = CORRUPT_TOKEN
        mailbox.save()
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(mailbox.password, '')
        self.assertIn('IMAP host=example-mailhost.com',
                      captured.records[0].getMessage())

    def test_mailbox_context_says_unset_when_nothing_is_available(self):
        mailbox = UserMailboxSettings.objects.create(
            user=self.user, email_address='no-domain', username='no-domain',
        )
        mailbox.encrypted_password = CORRUPT_TOKEN
        mailbox.save()
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(mailbox.password, '')
        self.assertIn('IMAP host=unset', captured.records[0].getMessage())

    def test_system_smtp_password_logs_the_row(self):
        smtp = SystemSMTPSettings.objects.create(
            id=1, from_email='system@example.com', smtp_host='smtp.example.com',
        )
        smtp.encrypted_password = CORRUPT_TOKEN
        smtp.save()
        with self.assertLogs(LOGGER_NAME, level='WARNING') as captured:
            self.assertEqual(smtp.password, '')
        message = captured.records[0].getMessage()
        self.assertIn('system SMTP pk=1', message)
        self.assertNotIn(CORRUPT_TOKEN, message)

    def test_readable_password_does_not_log(self):
        mailbox = self._mailbox()
        mailbox.set_password(PLAINTEXT)
        mailbox.save()
        with self.assertNoLogs(LOGGER_NAME, level='WARNING'):
            self.assertEqual(mailbox.password, PLAINTEXT)

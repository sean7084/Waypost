"""Tests for ``manage.py check_encrypted_secrets``.

The command is the whole-database version of the ``decrypt_secret`` warning, so
the two things that must be pinned are the counts (an operator decides on them)
and the absence of secret material (its output is meant to be pasted into an
issue or a CI log). A non-zero exit with no credential in it is the point.

The bucket split matters as much as the counts: a legacy XOR row *reads back*, so
reporting it as unreadable would page on a mailbox that works. These tests pin
that only a both-schemes-fail row exits non-zero.
"""
import base64
import hashlib
import io
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounts.crypto import encrypt_secret
from accounts.models import SystemSMTPSettings, UserMailboxSettings

PLAINTEXT = 'plaintext-must-never-be-printed'
# Neither valid Fernet nor valid url-safe base64: the only branch it can take is
# "token present but undecryptable".
CORRUPT_TOKEN = 'corrupt@@@token-not-base64'

User = get_user_model()


def _legacy_xor_value(plaintext):
    """Reproduce the pre-Fernet obfuscation so its row can be audited."""
    key = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    raw = plaintext.encode('utf-8')
    scrambled = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(raw))
    return base64.urlsafe_b64encode(scrambled).decode('ascii')


class CheckEncryptedSecretsCommandTests(TestCase):
    def _mailbox(self, stored, username):
        # One mailbox per user: `UserMailboxSettings.user` is a OneToOneField, so
        # several rows need several owners.
        owner = User.objects.create_user(username=username, password='pw')
        mailbox = UserMailboxSettings.objects.create(
            user=owner, email_address=f'{username}@example.com', username=username,
        )
        mailbox.encrypted_password = stored
        mailbox.save()
        return mailbox

    def _run(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            call_command('check_encrypted_secrets', *args, stdout=out, stderr=err)
        except SystemExit as exc:  # the audit verdict, not a crash
            code = exc.code
        return out.getvalue(), err.getvalue(), code

    def test_one_readable_and_one_corrupt_row_are_counted(self):
        good_token = encrypt_secret(PLAINTEXT)
        self._mailbox(good_token, 'goodbox')
        bad = self._mailbox(CORRUPT_TOKEN, 'badbox')
        self._mailbox('', 'blankbox')

        out, err, code = self._run()

        self.assertIn(
            'accounts.UserMailboxSettings.encrypted_password: total 3  '
            'fernet 1  legacy 0  unreadable 1  empty 1',
            out,
        )
        self.assertIn(f'unreadable row pks: {bad.pk}', out)
        self.assertIn('TOTAL: total 3  fernet 1  legacy 0  unreadable 1  empty 1', out)
        self.assertEqual(code, 1, msg=out + err)
        # Counts and pks only: neither stored token nor the plaintext appears.
        for rendered in (out, err):
            self.assertNotIn(CORRUPT_TOKEN, rendered)
            self.assertNotIn(PLAINTEXT, rendered)
            self.assertNotIn(good_token, rendered)
        self.assertIn('re-enter', err.lower())

    def test_legacy_row_is_counted_separately_and_exits_zero(self):
        # The whole point of the fourth bucket: this row still authenticates, so
        # it is a cleanup item, not an outage.
        legacy = _legacy_xor_value(PLAINTEXT)
        row = self._mailbox(legacy, 'legacybox')

        out, err, code = self._run()

        self.assertEqual(code, 0, msg=out + err)
        self.assertIn('fernet 0  legacy 1  unreadable 0  empty 0', out)
        self.assertIn(f'legacy pre-Fernet row pks: {row.pk}', out)
        self.assertIn('this is not a failure', out)
        self.assertNotIn(legacy, out + err)
        self.assertNotIn(PLAINTEXT, out + err)
        self.assertEqual(err, '')

    def test_legacy_and_unreadable_together_still_exit_one(self):
        self._mailbox(_legacy_xor_value(PLAINTEXT), 'legacybox')
        bad = self._mailbox(CORRUPT_TOKEN, 'badbox')

        out, err, code = self._run('--json')
        payload = json.loads(out)

        self.assertEqual(code, 1, msg=out + err)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['totals']['legacy'], 1)
        self.assertEqual(payload['totals']['unreadable'], 1)
        self.assertEqual(payload['fields'][0]['unreadable_pks'], [bad.pk])
        self.assertEqual(len(payload['fields'][0]['legacy_pks']), 1)

    def test_json_output_is_machine_readable_and_secret_free(self):
        self._mailbox(encrypt_secret(PLAINTEXT), 'goodbox')
        bad = self._mailbox(CORRUPT_TOKEN, 'badbox')

        out, err, code = self._run('--json')
        payload = json.loads(out)

        self.assertEqual(code, 1)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['totals']['unreadable'], 1)
        self.assertEqual(payload['totals']['fernet'], 1)
        mailbox_report = payload['fields'][0]
        self.assertEqual(mailbox_report['unreadable_pks'], [bad.pk])
        self.assertNotIn(CORRUPT_TOKEN, out)
        self.assertNotIn(PLAINTEXT, out)

    def test_a_fully_decryptable_database_exits_zero(self):
        self._mailbox(encrypt_secret(PLAINTEXT), 'goodbox')
        SystemSMTPSettings.objects.create(
            id=1, from_email='system@example.com', smtp_host='smtp.example.com',
            encrypted_password=encrypt_secret(PLAINTEXT),
        )

        out, err, code = self._run()

        self.assertEqual(code, 0, msg=out + err)
        self.assertIn('unreadable 0', out)
        self.assertIn('TOTAL: total 2  fernet 2  legacy 0  unreadable 0  empty 0', out)

    def test_json_output_exits_zero_when_clean(self):
        self._mailbox(encrypt_secret(PLAINTEXT), 'goodbox')
        out, err, code = self._run('--json')
        self.assertEqual(code, 0, msg=out + err)
        self.assertTrue(json.loads(out)['ok'])

    def test_system_smtp_rows_are_reported(self):
        SystemSMTPSettings.objects.create(
            id=1, from_email='system@example.com', smtp_host='smtp.example.com',
            encrypted_password=CORRUPT_TOKEN,
        )
        out, err, code = self._run('--json')
        payload = json.loads(out)
        smtp_report = next(f for f in payload['fields'] if f['model'] == 'accounts.SystemSMTPSettings')
        self.assertEqual(smtp_report['total'], 1)
        self.assertEqual(smtp_report['unreadable'], 1)
        self.assertEqual(smtp_report['unreadable_pks'], [1])
        self.assertEqual(code, 1)
        self.assertNotIn(CORRUPT_TOKEN, out + err)


class SecretSchemeProbeTests(TestCase):
    """``secret_scheme`` is what the buckets are built on, so pin its four answers."""

    def test_answers_for_each_stored_shape(self):
        from accounts.crypto import secret_scheme

        self.assertIsNone(secret_scheme(''))
        self.assertIsNone(secret_scheme(None))
        self.assertEqual(secret_scheme(encrypt_secret(PLAINTEXT)), 'fernet')
        self.assertEqual(secret_scheme(_legacy_xor_value(PLAINTEXT)), 'legacy')
        self.assertIsNone(secret_scheme(CORRUPT_TOKEN))

    def test_scheme_agrees_with_what_the_user_can_read(self):
        # A row is "usable" exactly when decrypt_secret returns non-empty for it;
        # the audit must not disagree with the call site.
        from accounts.crypto import decrypt_secret, secret_scheme

        for stored in ('', CORRUPT_TOKEN, _legacy_xor_value(PLAINTEXT),
                       encrypt_secret(PLAINTEXT)):
            with self.subTest(stored=bool(stored)):
                usable = decrypt_secret(stored) == PLAINTEXT
                self.assertEqual(secret_scheme(stored) is not None, usable)


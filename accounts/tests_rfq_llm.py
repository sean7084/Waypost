"""Tests for the RFQ / LLM cost cluster (go-live blockers 13, 14, 15).

There is no API key, no network and no provider sandbox here: every outbound
call is patched, and the assertions that matter are the negative ones - that
`urlopen` was *not* called when the budget is spent or no key is configured, and
that a bounce never reaches the model. A guard that returns the right status but
still touched the socket would cost real money, so `assert_not_called` is the
point of several of these tests.
"""
import io
import itertools
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from companies.models import Company, CompanyUser

from accounts import llm_client, mailbox_sync, rfq_ai
from accounts.llm_client import call_llm_json, extract_usage, monthly_spend_cny
from accounts.management.commands.sync_mailboxes import SYNC_LOCK_KEY
from accounts.models import LlmCallRecord, ReceivedEmailMessage, User, UserMailboxSettings

EXTERNAL_IDS = itertools.count(1)

# A plausible RFQ carrying none of the machine-generated signatures.
RFQ_SUBJECT = '请帮忙报价 ThinkPad T14'
RFQ_BODY = 'Brand: Lenovo\nProcurement item: ThinkPad T14 Gen 5 x 5\n联系方式: 13800000000'


def _stub(subject='', body_text='', metadata=None):
    """A message-shaped object for the pure-text tests, which need no database."""
    return SimpleNamespace(subject=subject, body_text=body_text, body_preview=body_text, metadata=metadata)


# Pairs that must be recognised as definitely not an RFQ.
NON_RFQ_CASES = [
    ('unsubscribe in subject', _stub(subject='Weekly newsletter - unsubscribe here')),
    ('chinese unsubscribe', _stub(subject='本周资讯，点击退订')),
    ('list-unsubscribe footer', _stub(body_text='List-Unsubscribe: <mailto:bounce@vendor.example>')),
    ('auto-submitted', _stub(body_text='This message was auto-submitted by a robot')),
    ('automatic reply', _stub(subject='Automatic reply: your ticket')),
    ('chinese autoreply', _stub(subject='自动回复：已收到')),
    ('out of office', _stub(subject='Out of office until Monday')),
    ('chinese out of office', _stub(body_text='未来两周我不在办公室')),
    ('delivery status notification', _stub(subject='Delivery Status Notification (Delay)')),
    ('undeliverable', _stub(subject='Undeliverable: your invoice')),
    ('mailer daemon', _stub(body_text='MAILER-DAEMON: your message could not be delivered')),
    ('postmaster', _stub(body_text='Report from postmaster@vendor.example')),
    ('no-reply', _stub(body_text='From no-reply@vendor.example with a price list')),
    ('noreply without a hyphen', _stub(body_text='noreply@vendor.example sent this quotation')),
    ('donotreply', _stub(body_text='donotreply@alerts.example sent this')),
    ('auto-submitted header', _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata={'Auto-Submitted': 'auto-replied'})),
    ('x-autoreply header', _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata={'X-Autoreply': 'yes'})),
    ('list-unsubscribe header', _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata={'List-Unsubscribe': '<https://x.example/u>'})),
]

# Pairs that must be allowed to reach the model.
RFQ_CASES = [
    ('bare rfq subject', _stub(subject=RFQ_SUBJECT)),
    ('structured rfq body', _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY)),
    ('english quotation request', _stub(subject='Request for quotation', body_text='Please quote 5 monitors, delivery to Shanghai')),
    ('auto-submitted says manual', _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata={'Auto-Submitted': 'manual'})),
    ('empty metadata', _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata={})),
    ('metadata not stored yet', _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata=None)),
    ('metadata is not a mapping', _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata=['unexpected'])),
]

ANTHROPIC_ENVELOPE = {
    'content': [{'type': 'text', 'text': json.dumps({'is_rfq': True, 'summary': '询价'})}],
    'usage': {'input_tokens': 1234, 'output_tokens': 567},
}
OPENAI_ENVELOPE = {
    'output_text': json.dumps({'is_rfq': True}),
    'usage': {'prompt_tokens': 90, 'completion_tokens': 12},
}


def _mock_response(payload_bytes):
    response = mock.MagicMock()
    response.read.return_value = payload_bytes
    response.__enter__.return_value = response
    return response


class DefiniteNonRfqTests(SimpleTestCase):
    """Blocker 15: the half of the gate that can be decided from the mail alone."""

    def test_machine_generated_mail_is_skipped(self):
        for label, message in NON_RFQ_CASES:
            with self.subTest(label):
                skip, reason = rfq_ai._definite_non_rfq(message)
                self.assertTrue(skip)
                self.assertTrue(reason)

    def test_plausible_rfqs_are_not_skipped(self):
        for label, message in RFQ_CASES:
            with self.subTest(label):
                skip, reason = rfq_ai._definite_non_rfq(message)
                self.assertFalse(skip)
                self.assertEqual(reason, '')

    def test_headers_are_read_before_the_body(self):
        # A reply that also contains a price request is still a machine reply:
        # the header is what the sending server said about itself.
        message = _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata={'Auto-Submitted': 'auto-generated'})
        skip, reason = rfq_ai._definite_non_rfq(message)
        self.assertTrue(skip)
        self.assertEqual(reason, 'auto_submitted_header')

    def test_reason_names_the_rule_that_fired(self):
        skip, reason = rfq_ai._definite_non_rfq(_stub(subject='Out of office'))
        self.assertTrue(skip)
        self.assertEqual(reason, 'non_rfq_signature:out of office')

    def test_a_coincidental_signature_does_not_veto_a_heuristic_positive_rfq(self):
        # A footer word is prose a customer wrote, not a header the server wrote:
        # vetoing here would silently drop a real quotation request.
        message = _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY + '\n--\nYou may unsubscribe from this list at any time.')
        skip, reason = rfq_ai._definite_non_rfq(message, {'is_rfq': True})
        self.assertFalse(skip)
        self.assertEqual(reason, '')
        skip, reason = rfq_ai._definite_non_rfq(message, {'is_rfq': False})
        self.assertTrue(skip)
        self.assertEqual(reason, 'non_rfq_signature:unsubscribe')

    def test_a_header_still_vetoes_a_heuristic_positive_rfq(self):
        message = _stub(subject=RFQ_SUBJECT, body_text=RFQ_BODY, metadata={'X-Autoreply': 'yes'})
        skip, reason = rfq_ai._definite_non_rfq(message, {'is_rfq': True})
        self.assertTrue(skip)
        self.assertEqual(reason, 'autoreply_header')


class MailboxHeaderCaptureTests(SimpleTestCase):
    """Header-based detection is only as good as the headers the sync keeps."""

    def test_automation_headers_land_in_metadata(self):
        raw = (
            b'Message-ID: <1@x>\r\nSubject: hi\r\nFrom: a@b.example\r\n'
            b'Auto-Submitted: auto-replied\r\nX-Autoreply: yes\r\n'
            b'List-Unsubscribe: <mailto:u@x.example>\r\n\r\nbody\r\n'
        )
        parsed = mailbox_sync.email.message_from_bytes(raw)
        defaults = mailbox_sync._build_defaults(
            None, parsed, 'body', ReceivedEmailMessage.MessageDirection.INBOX, 'INBOX', None, 'imap',
        )
        self.assertEqual(defaults['metadata']['Auto-Submitted'], 'auto-replied')
        self.assertEqual(defaults['metadata']['X-Autoreply'], 'yes')
        self.assertEqual(defaults['metadata']['List-Unsubscribe'], '<mailto:u@x.example>')

    def test_absent_headers_are_not_stored_as_empty_values(self):
        parsed = mailbox_sync.email.message_from_bytes(b'Message-ID: <2@x>\r\nSubject: hi\r\n\r\nbody\r\n')
        defaults = mailbox_sync._build_defaults(
            None, parsed, 'body', ReceivedEmailMessage.MessageDirection.INBOX, 'INBOX', None, 'imap',
        )
        self.assertEqual(defaults['metadata'], {'protocol': 'imap'})


class ExtractUsageTests(SimpleTestCase):
    """Blocker 14: the response field the old parser threw away."""

    def test_anthropic_shape(self):
        self.assertEqual(extract_usage(ANTHROPIC_ENVELOPE), (1234, 567))

    def test_openai_shape(self):
        self.assertEqual(extract_usage(OPENAI_ENVELOPE), (90, 12))

    def test_missing_usage_is_zero_not_an_error(self):
        self.assertEqual(extract_usage({'content': [{'type': 'text', 'text': '{}'}]}), (0, 0))

    def test_malformed_usage_never_raises(self):
        for envelope in [
            {'usage': None},
            {'usage': 'not-a-mapping'},
            {'usage': {'input_tokens': 'many', 'output_tokens': None}},
            {'usage': {'input_tokens': [], 'output_tokens': {}}},
            {},
            None,
            'a string',
        ]:
            with self.subTest(envelope=envelope):
                self.assertEqual(extract_usage(envelope), (0, 0))

    def test_negative_counts_are_clamped(self):
        self.assertEqual(extract_usage({'usage': {'input_tokens': -5, 'output_tokens': -1}}), (0, 0))

    def test_usage_survives_the_wrapped_payload(self):
        payload, envelope = llm_client._parse_response(json.dumps(ANTHROPIC_ENVELOPE), 'test')
        self.assertTrue(payload['is_rfq'])
        self.assertEqual(extract_usage(envelope), (1234, 567))

    def test_non_json_body_is_a_pair_of_nones(self):
        payload, envelope = llm_client._parse_response('<html>gateway error</html>', 'test')
        self.assertIsNone(payload)
        self.assertEqual(envelope, {})

    def test_openai_wrapper_still_yields_both_shapes(self):
        payload, envelope = llm_client._parse_response(json.dumps(OPENAI_ENVELOPE), 'test')
        self.assertTrue(payload['is_rfq'])
        self.assertEqual(extract_usage(envelope), (90, 12))


@override_settings(
    RFQ_LLM_API_KEY='test-key',
    RFQ_LLM_API_URL='https://llm.example.invalid/v1/messages',
    RFQ_LLM_MODEL='Test-Model-1',
    RFQ_LLM_MONTHLY_BUDGET_CNY=10,
    RFQ_LLM_PRICE_PER_MTOK_INPUT=1000,
    RFQ_LLM_PRICE_PER_MTOK_OUTPUT=1000,
)
class BudgetGuardTests(TestCase):
    """The guard has to fire before the socket, or it saves nothing."""

    def _overspend_this_month(self):
        LlmCallRecord.objects.create(
            purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION,
            status=LlmCallRecord.Status.OK,
            input_tokens=100_000,
        )

    def test_spent_budget_blocks_the_request(self):
        self._overspend_this_month()
        self.assertGreaterEqual(monthly_spend_cny(), 10)
        with mock.patch('urllib.request.urlopen') as urlopen:
            result = call_llm_json('system', 'user', purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION)
        self.assertEqual(result.status, LlmCallRecord.Status.BUDGET_EXCEEDED)
        self.assertIsNone(result.payload)
        urlopen.assert_not_called()
        record = LlmCallRecord.objects.get(status=LlmCallRecord.Status.BUDGET_EXCEEDED)
        self.assertIn('budget', record.error.lower())
        self.assertEqual(record.provider, 'llm.example.invalid')

    def test_last_month_spend_does_not_block_this_month(self):
        self._overspend_this_month()
        last_month = (timezone.now().replace(day=1) - timedelta(days=1)).replace(day=1)
        LlmCallRecord.objects.update(created_at=last_month)
        response = _mock_response(json.dumps(ANTHROPIC_ENVELOPE).encode('utf-8'))
        with mock.patch('urllib.request.urlopen', return_value=response) as urlopen:
            result = call_llm_json('system', 'user', purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION)
        urlopen.assert_called_once()
        self.assertEqual(result.status, LlmCallRecord.Status.OK)

    @override_settings(RFQ_LLM_MONTHLY_BUDGET_CNY=0)
    def test_zero_budget_means_unlimited(self):
        self._overspend_this_month()
        response = _mock_response(json.dumps(ANTHROPIC_ENVELOPE).encode('utf-8'))
        with mock.patch('urllib.request.urlopen', return_value=response) as urlopen:
            result = call_llm_json('system', 'user', purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION)
        urlopen.assert_called_once()
        self.assertEqual(result.status, LlmCallRecord.Status.OK)
        self.assertTrue(result.payload['is_rfq'])
        self.assertEqual((result.input_tokens, result.output_tokens), (1234, 567))

    def test_attempt_is_recorded_with_its_cost(self):
        response = _mock_response(json.dumps(ANTHROPIC_ENVELOPE).encode('utf-8'))
        with mock.patch('urllib.request.urlopen', return_value=response):
            call_llm_json('system', 'user', purpose=LlmCallRecord.Purpose.PRODUCT_DISAMBIGUATION)
        record = LlmCallRecord.objects.get()
        self.assertEqual(record.purpose, LlmCallRecord.Purpose.PRODUCT_DISAMBIGUATION)
        self.assertEqual(record.status, LlmCallRecord.Status.OK)
        self.assertEqual(record.input_tokens, 1234)
        self.assertEqual(record.output_tokens, 567)
        self.assertEqual(record.model, 'Test-Model-1')
        self.assertEqual(record.provider, 'llm.example.invalid')

    def test_transport_failure_is_recorded_and_never_raises(self):
        with mock.patch('urllib.request.urlopen', side_effect=OSError('connection refused')):
            result = call_llm_json('system', 'user', purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION)
        self.assertEqual(result.status, LlmCallRecord.Status.ERROR)
        self.assertIsNone(result.payload)
        self.assertIn('connection refused', LlmCallRecord.objects.get().error)

    def test_unusable_response_body_is_an_error_not_a_crash(self):
        response = _mock_response(b'<html>gateway error</html>')
        with mock.patch('urllib.request.urlopen', return_value=response):
            result = call_llm_json('system', 'user', purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION)
        self.assertEqual(result.status, LlmCallRecord.Status.ERROR)
        self.assertIsNone(result.payload)


@override_settings(RFQ_LLM_API_KEY='', RFQ_LLM_API_URL='')
class NotConfiguredTests(TestCase):
    """A development box has no key: it must touch no socket and spend nothing."""

    def test_unconfigured_client_never_reaches_the_network(self):
        with mock.patch('urllib.request.urlopen') as urlopen:
            result = call_llm_json('system', 'user', purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION)
        self.assertEqual(result.status, LlmCallRecord.Status.NOT_CONFIGURED)
        self.assertIsNone(result.payload)
        urlopen.assert_not_called()
        self.assertTrue(LlmCallRecord.objects.filter(status=LlmCallRecord.Status.NOT_CONFIGURED).exists())


class ClassifyRfqEmailTests(TestCase):
    """The gate as the mail loop sees it."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='mailboxowner', password='pw')
        cls.mailbox = UserMailboxSettings.objects.create(
            user=cls.user,
            email_address='rfq@istore-tech.example',
            username='rfq@istore-tech.example',
            smtp_host='smtp.example.invalid',
        )

    def _message(self, subject='', body_text='', metadata=None, sender='buyer@customer.example'):
        return ReceivedEmailMessage.objects.create(
            mailbox=self.mailbox,
            direction=ReceivedEmailMessage.MessageDirection.INBOX,
            external_id=f'classify-{next(EXTERNAL_IDS)}',
            subject=subject,
            sender=sender,
            body_text=body_text,
            body_preview=body_text,
            metadata=metadata if metadata is not None else {},
        )

    def _rfq_from_a_contact(self, authorized):
        company = Company.objects.create(name='Customer Co', code='CUSTCO')
        CompanyUser.objects.create(
            user=User.objects.create_user(username=f'buyer{authorized}', password='pw'),
            company=company,
            name='New Buyer',
            work_email='newbuyer@customer.example',
            is_authorized_rfq_sender=authorized,
        )
        return self._message(subject=RFQ_SUBJECT, body_text=RFQ_BODY, sender='New Buyer <newbuyer@customer.example>')

    @override_settings(RFQ_LLM_REQUIRE_AUTHORIZED_SENDER=False)
    def test_definite_non_rfq_never_reaches_the_model(self):
        message = self._message(subject='Automatic reply: received', body_text='Do not reply to this message.')
        with mock.patch('accounts.rfq_ai.call_llm_json') as llm:
            result = rfq_ai.classify_rfq_email(message)
        llm.assert_not_called()
        self.assertFalse(result['is_rfq'])
        self.assertTrue(result['llm_skipped'])
        self.assertEqual(result['llm_skip_reason'], 'non_rfq_signature:automatic reply')

    @override_settings(RFQ_LLM_REQUIRE_AUTHORIZED_SENDER=False)
    def test_skip_reason_is_persisted_and_counted(self):
        message = self._message(subject='Out of office', body_text='Back next week.')
        with mock.patch('accounts.rfq_ai.call_llm_json'):
            rfq_ai.process_rfq_message(message)
        self.assertEqual(message.rfq_extracted_data['llm_skip_reason'], 'non_rfq_signature:out of office')
        self.assertEqual(message.rfq_status, ReceivedEmailMessage.RFQStatus.CLASSIFIED_NON_RFQ)
        record = LlmCallRecord.objects.get()
        self.assertEqual(record.status, LlmCallRecord.Status.SKIPPED)
        self.assertEqual(record.purpose, LlmCallRecord.Purpose.RFQ_CLASSIFICATION)
        self.assertEqual(record.message, message)
        self.assertEqual(record.mailbox, self.mailbox)

    @override_settings(RFQ_LLM_REQUIRE_AUTHORIZED_SENDER=False)
    def test_heuristic_positive_with_a_footer_signature_still_buys_one_call(self):
        message = self._message(
            subject=RFQ_SUBJECT,
            body_text=RFQ_BODY + '\n--\nYou may unsubscribe from this list at any time.',
        )
        with mock.patch('accounts.rfq_ai.call_llm_json') as llm:
            llm.return_value = SimpleNamespace(payload={'is_rfq': True, 'summary': '询价'})
            result = rfq_ai.classify_rfq_email(message)
        self.assertEqual(llm.call_count, 1)
        self.assertNotIn('llm_skipped', result)
        self.assertTrue(result['is_rfq'])

    @override_settings(RFQ_LLM_REQUIRE_AUTHORIZED_SENDER=False)
    def test_plausible_rfq_calls_the_model_once(self):
        message = self._message(subject=RFQ_SUBJECT, body_text=RFQ_BODY)
        with mock.patch('accounts.rfq_ai.call_llm_json') as llm:
            llm.return_value = SimpleNamespace(payload={'is_rfq': True, 'summary': '询价'})
            result = rfq_ai.classify_rfq_email(message)
        self.assertEqual(llm.call_count, 1)
        self.assertNotIn('llm_skipped', result)
        self.assertEqual(result['summary'], '询价')

    @override_settings(RFQ_LLM_REQUIRE_AUTHORIZED_SENDER=False)
    def test_llm_without_a_payload_falls_back_to_the_heuristic(self):
        message = self._message(subject=RFQ_SUBJECT, body_text=RFQ_BODY)
        with mock.patch('accounts.rfq_ai.call_llm_json') as llm:
            llm.return_value = SimpleNamespace(payload=None)
            result = rfq_ai.classify_rfq_email(message)
        self.assertEqual(llm.call_count, 1)
        self.assertNotIn('llm_skipped', result)
        self.assertEqual(result['requested_items'], ['ThinkPad T14 Gen 5 x 5'])

    @override_settings(RFQ_LLM_REQUIRE_AUTHORIZED_SENDER=False)
    def test_unknown_sender_is_only_checked_when_the_flag_is_on(self):
        message = self._rfq_from_a_contact(authorized=False)
        with mock.patch('accounts.rfq_ai.call_llm_json') as llm:
            llm.return_value = SimpleNamespace(payload={'is_rfq': True})
            result = rfq_ai.classify_rfq_email(message)
        self.assertEqual(llm.call_count, 1)
        self.assertNotIn('llm_skipped', result)

    @override_settings(RFQ_LLM_REQUIRE_AUTHORIZED_SENDER=True)
    def test_unknown_sender_is_skipped_when_the_flag_is_on(self):
        message = self._rfq_from_a_contact(authorized=False)
        with mock.patch('accounts.rfq_ai.call_llm_json') as llm:
            result = rfq_ai.classify_rfq_email(message)
        llm.assert_not_called()
        self.assertEqual(result['llm_skip_reason'], 'sender_not_authorized')
        self.assertFalse(result['is_rfq'])

    @override_settings(RFQ_LLM_REQUIRE_AUTHORIZED_SENDER=True)
    def test_authorized_sender_is_still_called_when_the_flag_is_on(self):
        message = self._rfq_from_a_contact(authorized=True)
        with mock.patch('accounts.rfq_ai.call_llm_json') as llm:
            llm.return_value = SimpleNamespace(payload={'is_rfq': True})
            result = rfq_ai.classify_rfq_email(message)
        self.assertEqual(llm.call_count, 1)
        self.assertNotIn('llm_skipped', result)


class SyncMailboxesCommandTests(TestCase):
    """Blocker 13: the polling production never had, and its overlap guard."""

    def setUp(self):
        cache.delete(SYNC_LOCK_KEY)
        self.addCleanup(cache.delete, SYNC_LOCK_KEY)

    def test_once_runs_one_pass_and_reports_counts(self):
        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes', return_value=(2, 7)) as synced:
            out = io.StringIO()
            call_command('sync_mailboxes', '--once', stdout=out)
        synced.assert_called_once_with(deadline=None)
        self.assertIn('synced 2 mailbox(es), 7 new message(s)', out.getvalue())

    def test_default_is_one_pass(self):
        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes', return_value=(0, 0)) as synced:
            call_command('sync_mailboxes')
        synced.assert_called_once()

    def test_max_runtime_hands_a_deadline_to_the_pass(self):
        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes', return_value=(0, 0)) as synced:
            call_command('sync_mailboxes', '--once', '--max-runtime', '30')
        self.assertGreater(synced.call_args.kwargs['deadline'], time.monotonic())

    def test_second_holder_is_refused_without_syncing(self):
        self.assertTrue(cache.add(SYNC_LOCK_KEY, 'someone-else', timeout=60))
        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes') as synced:
            err = io.StringIO()
            call_command('sync_mailboxes', '--once', stderr=err)
        synced.assert_not_called()
        self.assertIn('lock', err.getvalue())

    def test_lock_is_released_after_a_pass(self):
        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes', return_value=(0, 0)):
            call_command('sync_mailboxes', '--once')
        self.assertIsNone(cache.get(SYNC_LOCK_KEY))

    def test_lock_is_released_after_a_failed_pass(self):
        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                call_command('sync_mailboxes', '--once')
        self.assertIsNone(cache.get(SYNC_LOCK_KEY))

    def test_a_pass_that_outlived_its_lease_does_not_release_the_next_holders_lock(self):
        def takeover(*_args, **_kwargs):
            cache.delete(SYNC_LOCK_KEY)
            cache.add(SYNC_LOCK_KEY, 'second-holder', timeout=60)
            return (0, 0)

        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes', side_effect=takeover):
            call_command('sync_mailboxes', '--once')
        self.assertEqual(cache.get(SYNC_LOCK_KEY), 'second-holder')

    def test_stale_records_are_pruned_in_the_same_pass(self):
        keep = LlmCallRecord.objects.create(purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION, status='ok')
        stale = LlmCallRecord.objects.create(purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION, status='ok')
        LlmCallRecord.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(days=200))
        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes', return_value=(0, 0)) as synced:
            out = io.StringIO()
            call_command('sync_mailboxes', '--once', stdout=out)
        synced.assert_called_once()
        self.assertEqual(list(LlmCallRecord.objects.values_list('pk', flat=True)), [keep.pk])
        self.assertIn('pruned 1 LLM call record(s)', out.getvalue())

    def test_nothing_is_pruned_when_the_lock_is_held(self):
        LlmCallRecord.objects.create(purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION, status='ok')
        LlmCallRecord.objects.update(created_at=timezone.now() - timedelta(days=200))
        cache.add(SYNC_LOCK_KEY, 'someone-else', timeout=60)
        with mock.patch.object(mailbox_sync, 'sync_all_active_mailboxes'):
            call_command('sync_mailboxes', '--once')
        self.assertEqual(LlmCallRecord.objects.count(), 1)

    def test_single_mailbox_pass_syncs_that_mailbox_only(self):
        mailbox = UserMailboxSettings.objects.create(
            user=User.objects.create_user(username='onemanager', password='pw'),
            email_address='one@istore-tech.example',
            username='one@istore-tech.example',
            smtp_host='smtp.example.invalid',
        )
        with mock.patch.object(mailbox_sync, 'sync_mailbox_messages', return_value={'inbox': 3, 'outbox': 1}) as single:
            out = io.StringIO()
            call_command('sync_mailboxes', '--mailbox', str(mailbox.pk), stdout=out)
        self.assertEqual(single.call_args.args[0], mailbox)
        self.assertIn('synced 1 mailbox(es), 4 new message(s)', out.getvalue())

    def test_unknown_mailbox_is_a_clear_error(self):
        with mock.patch.object(mailbox_sync, 'sync_mailbox_messages') as single:
            with self.assertRaises(CommandError):
                call_command('sync_mailboxes', '--mailbox', '999999')
        single.assert_not_called()


class RfqLlmUsageCommandTests(TestCase):
    """The operator surface blocker 14 asks for: counts, cost, budget share."""

    def _record(self, **kwargs):
        defaults = {
            'purpose': LlmCallRecord.Purpose.RFQ_CLASSIFICATION,
            'status': LlmCallRecord.Status.OK,
            'input_tokens': 1_000_000,
            'output_tokens': 0,
        }
        defaults.update(kwargs)
        return LlmCallRecord.objects.create(**defaults)

    def test_reports_counts_skips_and_budget_share(self):
        self._record()
        self._record(purpose=LlmCallRecord.Purpose.PRODUCT_DISAMBIGUATION, input_tokens=500_000)
        self._record(status=LlmCallRecord.Status.SKIPPED, input_tokens=0)
        out = io.StringIO()
        with self.settings(RFQ_LLM_PRICE_PER_MTOK_INPUT=4.2, RFQ_LLM_MONTHLY_BUDGET_CNY=200):
            call_command('rfq_llm_usage', stdout=out)
        report = out.getvalue()
        self.assertIn('rfq_classification', report)
        self.assertIn('product_disambiguation', report)
        self.assertIn('skipped    1', report)
        self.assertIn('CNY 6.3000 of budget CNY 200', report)

    def test_json_output_is_machine_readable(self):
        self._record()
        out = io.StringIO()
        with self.settings(RFQ_LLM_MONTHLY_BUDGET_CNY=0):
            call_command('rfq_llm_usage', '--json', '--by-mailbox', stdout=out)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload['by_purpose'][0]['calls'], 1)
        self.assertEqual(payload['by_purpose'][0]['input_tokens'], 1_000_000)
        self.assertIn('by_mailbox', payload)
        self.assertIn('budget is 0', payload['budget_line'])

    def test_other_months_are_reportable(self):
        self._record()
        last_month = (timezone.now().replace(day=1) - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        LlmCallRecord.objects.update(created_at=last_month)
        out = io.StringIO()
        call_command('rfq_llm_usage', '--month', last_month.strftime('%Y-%m'), stdout=out)
        self.assertIn('calls    1', out.getvalue())
        empty = io.StringIO()
        call_command('rfq_llm_usage', stdout=empty)
        self.assertIn('no records', empty.getvalue())

    def test_a_malformed_month_is_refused(self):
        with self.assertRaises(CommandError):
            call_command('rfq_llm_usage', '--month', 'October')

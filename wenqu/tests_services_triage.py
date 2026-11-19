"""Service tests: the guided-triage engine end to end (seed, branches, assets).

Ported from helpdesk/backend/tests/test_triage_engine.py (engine-level cases).
The model boundary is stubbed at the triage import sites with topic-dimension
embeddings and keyword classification, so no provider key is needed.
"""
import os
import tempfile
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from wenqu.models import Customer, TriageAsset, TriagePlaybook, TriageSession, TriageStep, WenquProfile
from wenqu.services.triage import engine, seed
from wenqu.services.triage.assets import asset_exists, asset_relative_path
from wenqu.services_testing import triage_chat_completion, triage_vector

User = get_user_model()


class _TriageMixin:
    def setUp(self):
        super().setUp()
        self.private_root = tempfile.mkdtemp(prefix='wenqu-triage-')
        ov = override_settings(WENQU_PRIVATE_MEDIA_ROOT=self.private_root)
        ov.enable()
        self.addCleanup(ov.disable)

        self.customer = Customer.objects.create(name='Acme')
        self.user = User.objects.create_user(username='triage-user', password='pw')
        WenquProfile.objects.create(user=self.user, external_id='triage-user', customer=self.customer)

        p1 = mock.patch.object(engine, 'embed_query', side_effect=triage_vector)
        p2 = mock.patch.object(seed, 'embed_query', side_effect=triage_vector)
        p3 = mock.patch.object(engine, 'chat_completion', side_effect=triage_chat_completion)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)

        seed.seed(customer_id=None)

    def _start(self, text='我的 iPad 突然上不了网了'):
        session_id = f'triage_{uuid.uuid4().hex[:8]}'
        return session_id, engine.start_or_advance(user=self.user, session_id=session_id, user_text=text)

    def _answer(self, session_id, value):
        turn = engine.start_or_advance(user=self.user, session_id=session_id, user_text=value, quick_value=value)
        self.assertIsNotNone(turn, f'flow ended earlier than expected at {value!r}')
        return turn


class SeedTests(_TriageMixin, TestCase):
    def test_seed_loads_assets_playbook_and_steps(self):
        self.assertEqual(TriageAsset.objects.count(), len(seed.ASSETS))
        playbook = TriagePlaybook.objects.get()
        self.assertEqual(playbook.slug, 'no-internet')
        self.assertTrue(playbook.is_active)
        self.assertIsNotNone(playbook.embedding)
        self.assertEqual(TriageStep.objects.filter(playbook=playbook).count(), len(seed.PLAYBOOKS[0]['steps']))
        asset = TriageAsset.objects.get(asset_key='ios_wifi_settings')
        self.assertGreater(asset.width, 0)
        self.assertGreater(asset.height, 0)

    def test_seed_materializes_screenshots_to_storage(self):
        for entry in seed.ASSETS:
            self.assertTrue(asset_exists(asset_relative_path(entry['filename'])))

    def test_reseed_is_idempotent(self):
        seed.seed(customer_id=None)
        self.assertEqual(TriagePlaybook.objects.count(), 1)
        self.assertEqual(TriageAsset.objects.count(), len(seed.ASSETS))


class EngineFlowTests(_TriageMixin, TestCase):
    def test_intake_step_asks_for_device(self):
        _, turn = self._start()
        self.assertIsNotNone(turn)
        triage = turn.meta['triage']
        self.assertEqual(triage['playbook_slug'], 'no-internet')
        self.assertEqual(triage['kind'], 'intake_device')
        self.assertEqual(triage['step_index'], 0)
        self.assertEqual([r['value'] for r in turn.quick_replies], ['ios', 'windows', 'printer', 'other'])
        self.assertTrue(turn.reply_text.strip())
        self.assertEqual(turn.attachments, [])

    def test_ios_branch_walks_to_resolved_end(self):
        session_id, _ = self._start()
        kinds = [self._answer(session_id, v).meta['triage']['kind'] for v in ('ios', 'connected', 'ok')]
        session = TriageSession.objects.get(session_id=session_id)
        self.assertEqual(kinds, ['check', 'check', 'end'])
        self.assertEqual(session.current_step_index, 10)
        self.assertEqual(session.status, 'resolved')

    def test_step_screenshot_protected_url_and_caption(self):
        session_id, _ = self._start(text='iPad 上不了网')
        turn = self._answer(session_id, 'ios')
        self.assertEqual(turn.meta['triage']['step_index'], 1)
        attachment = turn.attachments[0]
        self.assertEqual(attachment['type'], 'image')
        self.assertTrue(attachment['url'].startswith('/api/uploads/triage_assets/'))
        self.assertIn('ios_wifi_settings', attachment['url'])
        self.assertEqual(turn.meta['image_paths'], [attachment['path']])
        expected = next(e for e in seed.ASSETS if e['asset_key'] == 'ios_wifi_settings')
        self.assertEqual(attachment['caption'], expected['caption_zh'])
        self.assertTrue(asset_exists(attachment['path']))

    def test_printer_branch_escalates(self):
        session_id, _ = self._start(text='收银打印机连不上网')
        kinds = []
        turn = None
        for value in ('printer', 'ok', 'unsolved'):
            turn = self._answer(session_id, value)
            kinds.append(turn.meta['triage']['kind'])
        session = TriageSession.objects.get(session_id=session_id)
        self.assertEqual(kinds, ['check', 'solution', 'escalate'])
        self.assertEqual(session.status, 'resolved')
        self.assertEqual([r['value'] for r in turn.quick_replies], ['restart'])

    def test_answers_and_position_persisted(self):
        session_id, _ = self._start(text='Windows 电脑上不了网')
        self._answer(session_id, 'windows')
        session = TriageSession.objects.get(session_id=session_id)
        self.assertEqual(session.current_step_index, 4)
        self.assertEqual(session.answers_json['device'], 'windows')
        self.assertEqual(session.answers_json['0'], 'windows')

    def test_restart_chip_reopens_finished_flow(self):
        session_id, _ = self._start()
        for value in ('ios', 'connected', 'ok'):
            turn = self._answer(session_id, value)
        self.assertEqual(turn.meta['triage']['kind'], 'end')
        restarted = self._answer(session_id, 'restart')
        self.assertEqual(restarted.meta['triage']['kind'], 'intake_device')
        self.assertEqual(restarted.meta['triage']['step_index'], 0)

    def test_unrelated_message_defers_to_rag(self):
        _, turn = self._start(text='今天天气怎么样')
        self.assertIsNone(turn)
        self.assertEqual(TriageSession.objects.count(), 0)

    def test_free_text_on_finished_flow_falls_through(self):
        session_id, _ = self._start()
        for value in ('ios', 'connected', 'ok'):
            self._answer(session_id, value)
        # Free text (no quick_value) on a resolved flow must not advance it.
        turn = engine.start_or_advance(user=self.user, session_id=session_id, user_text='今天天气怎么样')
        self.assertIsNone(turn)


class MatchAndAnswerTests(_TriageMixin, TestCase):
    def test_match_playbook_above_threshold(self):
        result = engine.match_playbook(query='设备上不了网', customer_id=self.customer.id)
        self.assertIsNotNone(result)
        playbook, score = result
        self.assertEqual(playbook.slug, 'no-internet')
        self.assertGreaterEqual(score, engine.MATCH_THRESHOLD)

    def test_match_playbook_unrelated_below_threshold(self):
        self.assertIsNone(engine.match_playbook(query='完全无关的烹饪食谱内容', customer_id=self.customer.id))

    def test_match_playbook_empty_query(self):
        self.assertIsNone(engine.match_playbook(query='   ', customer_id=self.customer.id))

    def test_extract_answer_local_value(self):
        step = TriageStep.objects.get(playbook__slug='no-internet', step_index=1)
        self.assertEqual(engine.extract_answer(step=step, user_text='connected'), 'connected')

    def test_extract_answer_label_substring(self):
        step = TriageStep.objects.get(playbook__slug='no-internet', step_index=1)
        self.assertEqual(engine.extract_answer(step=step, user_text='已连接'), 'connected')

    def test_extract_answer_default_fallback(self):
        step = TriageStep.objects.get(playbook__slug='no-internet', step_index=1)
        # The stub classifier answers "default" for the 可选答案 prompt.
        self.assertEqual(engine.extract_answer(step=step, user_text='某种无法归类的回答'), 'default')

    def test_classify_intake_detects_device(self):
        result = engine.classify_intake(query='我的 iPad 上不了网', customer_id=self.customer.id)
        self.assertEqual(result['playbook_slug'], 'no-internet')
        self.assertEqual(result['device'], 'ios')

    def test_classify_intake_small_talk_empty_slug(self):
        result = engine.classify_intake(query='今天天气怎么样', customer_id=self.customer.id)
        self.assertEqual(result['playbook_slug'], '')
"""Service tests: the chat orchestrator core chain (RAG + multimodal + streaming).

Ported from helpdesk/backend/tests/test_chat_api.py (service-level assertions) and
test_attachment_captions.py. Providers are stubbed at the orchestrator import site;
no API keys, ffmpeg or tesseract are required.
"""
import io
import json
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from PIL import Image

from hongwen.models import ChatSession, Customer, KBChunk, KBDocument, Message, UploadFile, UserProfile, HongwenProfile
from hongwen.services.llm import orchestrator
from hongwen.services.utils.file_store import store_upload_bytes
from hongwen.services_testing import DEFAULT_REPLY, fake_stream_chat, stub_vector

User = get_user_model()


def _json_reply(content):
    return {'choices': [{'message': {'role': 'assistant', 'content': content}}], '_model_used': 'qwen/test'}


class _CustomerMixin:
    def setUp(self):
        super().setUp()
        self.customer = Customer.objects.create(name='Acme')
        self.user = User.objects.create_user(username='chat-user', password='pw')
        HongwenProfile.objects.create(user=self.user, external_id='chat-user', customer=self.customer)
        self.session = ChatSession.objects.create(session_id='sess-1', user=self.user)

    def _message(self, type='text', content=None):
        if content is None:
            content = {'text': '路由器亮红灯怎么办'}
        return Message.objects.create(
            session=self.session, user=self.user, role='user', type=type,
            content=json.dumps(content, ensure_ascii=False),
        )


class OrchestratorTextTests(_CustomerMixin, TestCase):
    def test_round_trip_parses_json_reply_and_profile(self):
        reply_json = json.dumps({
            'assistant_reply': '请先重启路由器。',
            'updated_weak_points': '{"network": true}',
            'updated_suggested_solutions': '{}',
            'meta': {},
        }, ensure_ascii=False)
        entries = [{'source': 'manual.pdf', 'snippet': '先重启设备。', 'score': 0.9, 'page_or_section': 'page_1', 'metadata': {}}]
        with mock.patch.object(orchestrator, 'search_kb', return_value=entries), \
             mock.patch.object(orchestrator, 'chat_completion_with_fallback', return_value=_json_reply(reply_json)):
            reply, meta, updates = orchestrator.generate_reply_and_update_profile(
                user=self.user, new_message=self._message(), session_id='sess-1',
            )
        self.assertEqual(reply, '请先重启路由器。')
        self.assertTrue(meta['used_knowledge_base'])
        self.assertEqual(meta['knowledge_sources'], ['manual.pdf'])
        self.assertEqual(updates['weak_points'], '{"network": true}')

    def test_model_cannot_override_provenance(self):
        contradictory = json.dumps({
            'assistant_reply': '请先重启设备。',
            'meta': {'used_knowledge_base': False, 'knowledge_sources': ['bogus.pdf']},
        })
        entries = [{'source': 'manual.pdf', 'snippet': '先重启设备。', 'score': 0.9, 'page_or_section': '', 'metadata': {}}]
        with mock.patch.object(orchestrator, 'search_kb', return_value=entries), \
             mock.patch.object(orchestrator, 'chat_completion_with_fallback', return_value=_json_reply(contradictory)):
            _, meta, _ = orchestrator.generate_reply_and_update_profile(
                user=self.user, new_message=self._message(), session_id='sess-1',
            )
        self.assertTrue(meta['used_knowledge_base'])
        self.assertEqual(meta['knowledge_sources'], ['manual.pdf'])

    def test_broken_embedding_degrades(self):
        with mock.patch.object(orchestrator, 'search_kb', side_effect=RuntimeError('401')), \
             mock.patch.object(orchestrator, 'chat_completion_with_fallback', return_value=_json_reply('直接回答')):
            reply, meta, _ = orchestrator.generate_reply_and_update_profile(
                user=self.user, new_message=self._message(), session_id='sess-1',
            )
        self.assertTrue(reply)
        self.assertFalse(meta['used_knowledge_base'])
        self.assertEqual(meta['knowledge_sources'], [])

    def test_non_json_reply_passes_through(self):
        with mock.patch.object(orchestrator, 'search_kb', return_value=[]), \
             mock.patch.object(orchestrator, 'chat_completion_with_fallback', return_value=_json_reply('纯文本回答')):
            reply, _, updates = orchestrator.generate_reply_and_update_profile(
                user=self.user, new_message=self._message(), session_id='sess-1',
            )
        self.assertEqual(reply, '纯文本回答')
        self.assertEqual(updates['weak_points'], '{}')

    def test_guidance_payload_is_second_user_message(self):
        with mock.patch.object(orchestrator, 'search_kb', return_value=[]):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=self._message(), session_id='sess-1')
        messages = prep.build_messages_for_model(prep.primary_model, mode='chat')
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[1]['role'], 'user')
        self.assertEqual(messages[2]['role'], 'user')
        payload = json.loads(messages[2]['content'])['guidance_payload']
        self.assertIn('conversation_history', payload)
        self.assertIn('user_profile', payload)
        self.assertIn('knowledge_base', payload)

    def test_history_is_capped_at_20_and_unwrapped(self):
        for i in range(25):
            Message.objects.create(
                session=self.session, user=self.user, role='user', type='text',
                content=json.dumps({'type': 'text', 'content': {'text': f'msg {i}'}}),
            )
        # An unsaved new_message keeps the DB at exactly 25 rows for a deterministic cap.
        new_message = Message(
            session=self.session, user=self.user, role='user', type='text',
            content=json.dumps({'text': '路由器亮红灯怎么办'}),
        )
        with mock.patch.object(orchestrator, 'search_kb', return_value=[]):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=new_message, session_id='sess-1')
        history = json.loads(prep.guidance_payload['content'])['guidance_payload']['conversation_history']
        self.assertEqual(len(history), 20)
        # The request envelope was unwrapped to the nested content payload.
        self.assertTrue(all(isinstance(h['content'], dict) and 'text' in h['content'] for h in history))

    def test_profile_defaults_when_absent(self):
        with mock.patch.object(orchestrator, 'search_kb', return_value=[]):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=self._message(), session_id='sess-1')
        self.assertEqual(prep.user_profile['weak_points'], '{}')
        self.assertEqual(prep.user_profile['notes'], '')

    def test_apply_profile_updates_persists(self):
        orchestrator.apply_profile_updates(self.user, {'weak_points': '{"a":1}', 'suggested_solutions': '{}'})
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.weak_points, '{"a":1}')

    def test_stream_deltas_reassemble(self):
        with mock.patch.object(orchestrator, 'search_kb', return_value=[]):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=self._message(), session_id='sess-1')
        with mock.patch.object(orchestrator, 'stream_chat_completion_with_fallback', side_effect=fake_stream_chat):
            deltas = list(orchestrator.stream_reply_deltas(preparation=prep))
        self.assertEqual(''.join(text for text, _ in deltas), DEFAULT_REPLY)
        self.assertTrue(all(model for _, model in deltas))


class OrchestratorRerankTests(TestCase):
    def _entry(self, source, snippet, score):
        return {'source': source, 'snippet': snippet, 'score': score, 'page_or_section': '', 'metadata': {}}

    def test_epson_roll_paper_procedure_outranks_toc(self):
        # >5 candidates so rerank engages; the procedure snippet must beat a TOC page.
        candidates = [
            self._entry('epson_tm_t88vi.pdf', 'Table of contents ............ 1 ............ 2', 0.80),
            self._entry('epson_tm_t88vi.pdf', 'How to replace the roll paper: open the cover and load a new roll.', 0.70),
            self._entry('other.pdf', 'unrelated cooking recipe', 0.75),
            self._entry('epson_tm_t88vi.pdf', 'paper jam clearance steps', 0.68),
            self._entry('epson_tm_t88vi.pdf', 'installing the printer driver', 0.66),
            self._entry('epson_tm_t88vi.pdf', 'specifications and warranty', 0.64),
        ]
        ranked = orchestrator._rerank_knowledge_base('how to replace roll paper epson tm t88vi', candidates, limit=5)
        self.assertIn('replace the roll paper', ranked[0]['snippet'])
        # The dotted TOC line is penalized below the procedure pages.
        toc_rank = next(i for i, e in enumerate(ranked) if 'Table of contents' in e['snippet'])
        self.assertGreater(toc_rank, 0)

    def test_small_candidate_set_passes_through(self):
        candidates = [self._entry('a.pdf', 'x', 0.9)]
        self.assertEqual(orchestrator._rerank_knowledge_base('x', candidates, limit=5), candidates)


class OrchestratorAttachmentTests(_CustomerMixin, TestCase):
    def test_inline_image_attachment_gets_caption(self):
        entries = [{
            'source': '0' * 32 + '_manual.pdf', 'snippet': 'see ![img](kb_assets/x.png)',
            'score': 0.9, 'page_or_section': 'page_2', 'metadata': {'image_paths': ['kb_assets/x.png']},
        }]
        with mock.patch.object(orchestrator, 'search_kb', return_value=entries):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=self._message(), session_id='sess-1')
        self.assertEqual(len(prep.kb_attachments), 1)
        attachment = prep.kb_attachments[0]
        self.assertEqual(attachment['path'], 'kb_assets/x.png')
        self.assertEqual(attachment['url'], '/api/uploads/kb_assets/x.png')
        self.assertEqual(attachment['caption'], 'manual.pdf · page_2')

    def test_pdf_page_render_attachment(self):
        import fitz
        kb_root = tempfile.mkdtemp(prefix='hongwen-orch-kb-')
        private_root = tempfile.mkdtemp(prefix='hongwen-orch-private-')
        pdf_path = os.path.join(kb_root, 'guide.pdf')
        doc = fitz.open()
        doc.new_page()
        doc.save(pdf_path)
        doc.close()
        entries = [{'source': 'guide.pdf', 'snippet': 'text only', 'score': 0.9, 'page_or_section': 'page_1', 'metadata': {}}]
        with override_settings(HONGWEN_KB_SOURCES_DIR=kb_root, HONGWEN_PRIVATE_MEDIA_ROOT=private_root), \
             mock.patch.object(orchestrator, 'search_kb', return_value=entries):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=self._message(), session_id='sess-1')
        self.assertEqual(len(prep.kb_attachments), 1)
        attachment = prep.kb_attachments[0]
        self.assertTrue(attachment['path'].startswith('kb_assets/'))
        self.assertTrue(attachment['path'].endswith('_page_1.png'))
        self.assertEqual(attachment['caption'], 'guide.pdf · page_1')

    def test_inline_images_stripped_from_reply_when_attachments_exist(self):
        entries = [{'source': 'm.pdf', 'snippet': 'x', 'score': 0.9, 'page_or_section': '', 'metadata': {'image_paths': ['kb_assets/x.png']}}]
        reply_json = json.dumps({'assistant_reply': '看这里 ![img](kb_assets/x.png) 步骤一'})
        with mock.patch.object(orchestrator, 'search_kb', return_value=entries), \
             mock.patch.object(orchestrator, 'chat_completion_with_fallback', return_value=_json_reply(reply_json)):
            reply, _, _ = orchestrator.generate_reply_and_update_profile(
                user=self.user, new_message=self._message(), session_id='sess-1',
            )
        self.assertNotIn('![img]', reply)
        self.assertIn('步骤一', reply)


class OrchestratorMultimodalTests(_CustomerMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.private_root = tempfile.mkdtemp(prefix='hongwen-orch-mm-')
        ov = override_settings(HONGWEN_PRIVATE_MEDIA_ROOT=self.private_root)
        ov.enable()
        self.addCleanup(ov.disable)

    def _png_bytes(self):
        buf = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(buf, format='PNG')
        return buf.getvalue()

    def test_image_message_builds_image_url_part(self):
        upload = store_upload_bytes(user=self.user, filename='shot.png', data=self._png_bytes(), file_type='image', mime_type='image/png')
        message = self._message(type='image', content={'file_id': upload.file_id})
        with mock.patch.object(orchestrator, 'search_kb', return_value=[]):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=message, session_id='sess-1')
        parts = prep.user_message['content']
        self.assertTrue(any(p.get('type') == 'image_url' for p in parts))
        image_part = next(p for p in parts if p.get('type') == 'image_url')
        self.assertTrue(image_part['image_url']['url'].startswith('data:image/png;base64,'))

    def test_image_only_query_uses_placeholder(self):
        upload = store_upload_bytes(user=self.user, filename='shot.png', data=self._png_bytes(), file_type='image', mime_type='image/png')
        message = self._message(type='image', content={'file_id': upload.file_id})
        seen = {}

        def capture_search(**kwargs):
            seen['query'] = kwargs['query']
            return []

        with mock.patch.object(orchestrator, 'search_kb', side_effect=capture_search):
            orchestrator.prepare_reply_generation(user=self.user, new_message=message, session_id='sess-1')
        self.assertEqual(seen['query'], 'helpdesk image problem')

    def test_audio_transcript_feeds_kb_and_message(self):
        upload = store_upload_bytes(user=self.user, filename='voice.mp3', data=b'ID3fake', file_type='audio', mime_type='audio/mpeg')
        message = self._message(type='audio', content={'file_id': upload.file_id})
        seen = {}

        def capture_search(**kwargs):
            seen['query'] = kwargs['query']
            return []

        with mock.patch.object(orchestrator, 'transcribe_audio', return_value='录音转写结果'), \
             mock.patch.object(orchestrator, 'search_kb', side_effect=capture_search):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=message, session_id='sess-1')
        self.assertIn('录音转写结果', seen['query'])
        parts = prep.user_message['content']
        self.assertTrue(any('录音转写结果' in str(p.get('text', '')) for p in parts))
        # With a transcript, raw audio is not attached.
        self.assertFalse(any(p.get('type') == 'input_audio' for p in parts))

    def test_audio_without_transcript_attaches_raw_audio(self):
        upload = store_upload_bytes(user=self.user, filename='voice.mp3', data=b'ID3fake', file_type='audio', mime_type='audio/mpeg')
        message = self._message(type='audio', content={'file_id': upload.file_id})
        with mock.patch.object(orchestrator, 'transcribe_audio', side_effect=RuntimeError('asr down')), \
             mock.patch.object(orchestrator, 'search_kb', return_value=[]), \
             mock.patch('hongwen.services.utils.audio_utils.convert_to_mp3', side_effect=RuntimeError('no ffmpeg')):
            prep = orchestrator.prepare_reply_generation(user=self.user, new_message=message, session_id='sess-1')
        parts = prep.user_message['content']
        self.assertTrue(any(p.get('type') == 'input_audio' for p in parts))

    def test_temp_files_cleaned_up(self):
        upload = store_upload_bytes(user=self.user, filename='shot.png', data=self._png_bytes(), file_type='image', mime_type='image/png')
        message = self._message(type='image', content={'file_id': upload.file_id})
        created = []
        real_materialize = orchestrator.materialize_upload_to_tempfile

        def tracking_materialize(u):
            tmp = real_materialize(u)
            created.append(tmp)
            return tmp

        with mock.patch.object(orchestrator, 'search_kb', return_value=[]), \
             mock.patch.object(orchestrator, 'materialize_upload_to_tempfile', side_effect=tracking_materialize):
            orchestrator.prepare_reply_generation(user=self.user, new_message=message, session_id='sess-1')
        self.assertTrue(created)
        self.assertTrue(all(not os.path.exists(tmp) for tmp in created))


class OrchestratorRealRetrievalTests(_CustomerMixin, TestCase):
    def test_real_sqlite_retrieval_flows_into_meta(self):
        doc = KBDocument.objects.create(path='manual.pdf', source_type='pdf', content_hash='h', customer=self.customer)
        KBChunk.objects.create(
            document=doc, chunk_index=0, content='router power adapter reset procedure',
            chunk_type='text', metadata_json={}, embedding=stub_vector('router power adapter reset procedure'),
            source_path='manual.pdf', page_or_section='page_1',
        )
        message = self._message(content={'text': 'router power adapter reset procedure'})
        with mock.patch('hongwen.services.kb.vector_store.embed_text', side_effect=lambda *, model, texts, **_: [stub_vector(t) for t in texts]), \
             mock.patch.object(orchestrator, 'chat_completion_with_fallback', return_value=_json_reply('重启路由器')):
            _, meta, _ = orchestrator.generate_reply_and_update_profile(
                user=self.user, new_message=message, session_id='sess-1',
            )
        self.assertTrue(meta['used_knowledge_base'])
        self.assertEqual(meta['knowledge_sources'], ['manual.pdf'])
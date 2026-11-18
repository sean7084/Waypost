"""Service tests: model selection, prompt packs, provider clients and router failover.

Provider network boundaries are stubbed (no API keys / WebSockets). The failover
tests pin the helpdesk semantics: chat/TTS fall back on failure, but a stream
that has already yielded a chunk must NOT switch mid-stream.
"""
import json
from unittest import mock

from django.test import TestCase, override_settings

from wenqu.services.llm import dashscope_client, model_selector, prompts, provider_router, volcengine_client
from wenqu.services.llm.model_selector import ProviderModelRef, parse_model_ref


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, lines=None, headers=None, text=''):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self._lines = lines or []
        self.headers = headers or {}
        self.text = text
        self.reason = 'Error' if status_code >= 400 else 'OK'

    def json(self):
        return self._payload

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} error', response=self)

    def iter_lines(self, decode_unicode=False):
        yield from self._lines

    def close(self):
        pass


class ModelSelectorTests(TestCase):
    def test_parse_model_ref_aliases(self):
        self.assertEqual(parse_model_ref('qwen/qwen3.6-plus').provider, 'dashscope')
        self.assertEqual(parse_model_ref('doubao/seed').provider, 'volcengine')
        self.assertEqual(parse_model_ref('ark/x').provider, 'volcengine')
        self.assertEqual(parse_model_ref('bare-model').provider, 'dashscope')
        self.assertEqual(parse_model_ref('bare-model').model_id, 'bare-model')
        with self.assertRaises(ValueError):
            parse_model_ref('')

    def test_defaults(self):
        self.assertEqual(model_selector.select_primary_embed_ref().qualified_name, 'dashscope/text-embedding-v4')
        self.assertEqual(model_selector.select_primary_asr_ref().qualified_name, 'dashscope/qwen3-asr-flash')
        self.assertEqual(model_selector.select_backup_tts_ref().qualified_name, 'volcengine/seed-tts-2.0')

    def test_omni_override_wins(self):
        with override_settings(PRIMARY_OMNI_MODEL='volcengine/doubao-seed-2-0-lite-260428'):
            self.assertEqual(model_selector.select_primary_omni_ref().provider, 'volcengine')
            self.assertEqual(model_selector.select_primary_omni_ref(multimodal=True).provider, 'volcengine')

    def test_text_vision_split_when_no_omni_override(self):
        with override_settings(PRIMARY_OMNI_MODEL='', PRIMARY_TEXT_MODEL='qwen/text-x', PRIMARY_VISION_MODEL='qwen/vision-x'):
            self.assertEqual(model_selector.select_primary_omni_ref(multimodal=False).model_id, 'text-x')
            self.assertEqual(model_selector.select_primary_omni_ref(multimodal=True).model_id, 'vision-x')

    def test_fallback_omni_defaults_to_primary(self):
        with override_settings(PRIMARY_OMNI_MODEL='volcengine/doubao', BACKUP_OMNI_MODEL=''):
            self.assertEqual(model_selector.select_fallback_omni_ref().qualified_name, 'volcengine/doubao')

    def test_tts_default_voice(self):
        self.assertEqual(model_selector.select_tts_default_voice('qwen/cosyvoice-v3-flash'), 'longanyang')
        self.assertEqual(
            model_selector.select_tts_default_voice('volcengine/seed-tts-2.0'),
            'zh_male_kailangxuezhang_uranus_bigtts',
        )
        with override_settings(QWEN_TTS_DEFAULT_VOICE='serena'):
            # cosyvoice rejects serena/bella -> longanyang
            self.assertEqual(model_selector.select_tts_default_voice('qwen/cosyvoice-v3-flash'), 'longanyang')


class PromptPackTests(TestCase):
    def test_pack_resolution(self):
        self.assertEqual(prompts.load_prompt_pack('qwen/qwen3.6-plus').pack_name, 'qwen-3.6')
        self.assertEqual(prompts.load_prompt_pack('volcengine/doubao-seed-2-0-lite-260428').pack_name, 'doubao-seed-2.0')
        self.assertEqual(prompts.load_prompt_pack('volcengine/other').pack_name, 'doubao-seed-2.0')
        self.assertEqual(prompts.load_prompt_pack('unknown/model').pack_name, 'default-zh')

    def test_pack_loads_markdown_from_template_files(self):
        pack = prompts.load_prompt_pack('qwen/qwen3.6-plus')
        self.assertIn('问渠', pack.sections['SOUL'])
        self.assertEqual(pack.locale, 'zh-CN')

    def test_default_pack_uses_branded_fallback(self):
        pack = prompts.load_prompt_pack('unknown/model')
        self.assertIn('问渠', pack.sections['SOUL'])

    def test_chat_mode_asks_for_json(self):
        rendered = prompts.render_helpdesk_system_prompt(model='qwen/qwen3.6-plus', mode='chat')
        self.assertIn('assistant_reply', rendered)
        self.assertIn('[OUTPUT]', rendered)
        self.assertNotIn('[REGENERATE]', rendered)

    def test_stream_mode_asks_for_plain_text(self):
        rendered = prompts.render_helpdesk_system_prompt(model='qwen/qwen3.6-plus', mode='stream')
        self.assertIn('只输出纯文本', rendered)
        self.assertNotIn('assistant_reply', rendered)

    def test_regenerate_mode_adds_rules(self):
        for mode in ('chat_regenerate', 'stream_regenerate'):
            rendered = prompts.render_helpdesk_system_prompt(model='qwen/qwen3.6-plus', mode=mode)
            self.assertIn('[REGENERATE]', rendered)

    def test_build_llm_messages(self):
        messages = prompts.build_llm_messages(
            conversation_history=[], latest_user_input={'type': 'text', 'text': 'hi'},
            user_profile={}, knowledge_base=[], model='qwen/qwen3.6-plus',
        )
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[1]['role'], 'user')
        payload = json.loads(messages[1]['content'])
        self.assertIn('knowledge_base', payload)


class ChatFailoverTests(TestCase):
    def test_primary_success(self):
        with mock.patch.object(dashscope_client, 'chat_completion', return_value={'choices': [{'message': {'content': 'ok'}}]}):
            resp = provider_router.chat_completion_with_fallback(
                primary_model='qwen/a', fallback_model='volcengine/b', messages=[],
            )
        self.assertEqual(resp['_model_used'], 'dashscope/a')

    def test_falls_back_on_primary_failure(self):
        with mock.patch.object(dashscope_client, 'chat_completion', side_effect=RuntimeError('primary down')), \
             mock.patch.object(volcengine_client, 'chat_completion', return_value={'choices': [{'message': {'content': 'backup'}}]}):
            resp = provider_router.chat_completion_with_fallback(
                primary_model='qwen/a', fallback_model='volcengine/b', messages=[],
            )
        self.assertEqual(resp['_model_used'], 'volcengine/b')

    def test_same_model_propagates_error(self):
        with mock.patch.object(dashscope_client, 'chat_completion', side_effect=RuntimeError('down')):
            with self.assertRaises(RuntimeError):
                provider_router.chat_completion_with_fallback(
                    primary_model='qwen/a', fallback_model='qwen/a', messages=[],
                )

    def test_fallback_messages_used(self):
        seen = {}

        def backup(**kwargs):
            seen['messages'] = kwargs['messages']
            return {'choices': [{'message': {'content': 'b'}}]}

        with mock.patch.object(dashscope_client, 'chat_completion', side_effect=RuntimeError('down')), \
             mock.patch.object(volcengine_client, 'chat_completion', side_effect=backup):
            provider_router.chat_completion_with_fallback(
                primary_model='qwen/a', fallback_model='volcengine/b',
                messages=[{'role': 'user', 'content': 'primary'}],
                fallback_messages=[{'role': 'user', 'content': 'fallback'}],
            )
        self.assertEqual(seen['messages'], [{'role': 'user', 'content': 'fallback'}])


class StreamChatFailoverTests(TestCase):
    def test_no_switch_after_first_chunk(self):
        def primary_stream(**kwargs):
            yield {'choices': [{'delta': {'content': 'partial'}}]}
            raise RuntimeError('mid-stream failure')

        backup_called = {'n': 0}

        def backup_stream(**kwargs):
            backup_called['n'] += 1
            yield {'choices': [{'delta': {'content': 'backup'}}]}

        with mock.patch.object(dashscope_client, 'stream_chat_completion', side_effect=primary_stream), \
             mock.patch.object(volcengine_client, 'stream_chat_completion', side_effect=backup_stream):
            gen = provider_router.stream_chat_completion_with_fallback(
                primary_model='qwen/a', fallback_model='volcengine/b', messages=[],
            )
            first = next(gen)
            self.assertEqual(first['_model_used'], 'dashscope/a')
            with self.assertRaises(RuntimeError):
                next(gen)
        self.assertEqual(backup_called['n'], 0)

    def test_switch_when_primary_fails_before_yielding(self):
        def primary_stream(**kwargs):
            raise RuntimeError('connect failed')
            yield  # pragma: no cover

        with mock.patch.object(dashscope_client, 'stream_chat_completion', side_effect=primary_stream), \
             mock.patch.object(volcengine_client, 'stream_chat_completion', return_value=iter([{'choices': [{'delta': {'content': 'backup'}}]}])):
            chunks = list(provider_router.stream_chat_completion_with_fallback(
                primary_model='qwen/a', fallback_model='volcengine/b', messages=[],
            ))
        self.assertEqual(chunks[0]['_model_used'], 'volcengine/b')

    def test_same_model_no_switch(self):
        def primary_stream(**kwargs):
            raise RuntimeError('down')
            yield  # pragma: no cover

        with mock.patch.object(dashscope_client, 'stream_chat_completion', side_effect=primary_stream):
            with self.assertRaises(RuntimeError):
                list(provider_router.stream_chat_completion_with_fallback(
                    primary_model='qwen/a', fallback_model='qwen/a', messages=[],
                ))


class TTSFailoverTests(TestCase):
    def test_synthesize_falls_back(self):
        with mock.patch.object(dashscope_client, 'synthesize_speech', side_effect=RuntimeError('down')), \
             mock.patch.object(volcengine_client, 'synthesize_speech', return_value={'audio_url': '/api/uploads/tts/x.mp3'}):
            result = provider_router.synthesize_speech_with_fallback(
                primary_model='qwen/cosyvoice-v3-flash', fallback_model='volcengine/seed-tts-2.0',
                text='你好', primary_voice='longanyang', fallback_voice='zh_male',
            )
        self.assertEqual(result['_model_used'], 'volcengine/seed-tts-2.0')
        self.assertEqual(result['_voice_used'], 'zh_male')

    def test_stream_tts_no_switch_after_first_chunk(self):
        def primary(**kwargs):
            yield b'CHUNK'
            raise RuntimeError('mid-stream')

        backup_called = {'n': 0}

        def backup(**kwargs):
            backup_called['n'] += 1
            yield b'BACKUP'

        with mock.patch.object(dashscope_client, 'stream_speech_audio', side_effect=primary), \
             mock.patch.object(volcengine_client, 'stream_speech_audio', side_effect=backup):
            gen = provider_router.stream_speech_audio_with_fallback(
                primary_model='qwen/cosyvoice', fallback_model='volcengine/seed-tts',
                text='hi', primary_voice='v1', fallback_voice='v2',
            )
            self.assertEqual(next(gen), b'CHUNK')
            with self.assertRaises(RuntimeError):
                next(gen)
        self.assertEqual(backup_called['n'], 0)


class TranscribeFailoverTests(TestCase):
    def test_falls_back_to_backup(self):
        with mock.patch.object(dashscope_client, 'transcribe_audio', side_effect=RuntimeError('down')), \
             mock.patch.object(volcengine_client, 'transcribe_audio', return_value='备份转写'):
            result = provider_router.transcribe_audio_with_fallback(
                primary_model='qwen/qwen3-asr-flash', fallback_model='volcengine/volc.seedasr.auc',
                file_path='/tmp/x.mp3', public_file_url='https://example.com/x.mp3',
            )
        self.assertEqual(result['text'], '备份转写')
        self.assertEqual(result['model_used'], 'volcengine/volc.seedasr.auc')

    def test_both_fail_raises(self):
        with mock.patch.object(dashscope_client, 'transcribe_audio', side_effect=RuntimeError('a')), \
             mock.patch.object(volcengine_client, 'transcribe_audio', side_effect=RuntimeError('b')):
            with self.assertRaises(RuntimeError):
                provider_router.transcribe_audio_with_fallback(
                    primary_model='qwen/a', fallback_model='volcengine/b', file_path='/tmp/x.mp3',
                )


class DashScopeClientTests(TestCase):
    def test_chat_payload_stream_flags(self):
        payload = dashscope_client._build_chat_payload(
            model='m', messages=[], temperature=0.2, tools=None, tool_choice=None, response_format=None, stream=True,
        )
        self.assertTrue(payload['stream'])
        self.assertTrue(payload['incremental_output'])
        self.assertFalse(payload['enable_thinking'])

    def test_resolve_generation_url_variants(self):
        base = 'https://dashscope.aliyuncs.com'
        self.assertEqual(
            dashscope_client._resolve_generation_url(f'{base}/compatible-mode/v1'),
            dashscope_client.DASHSCOPE_GENERATION_URL,
        )
        self.assertEqual(
            dashscope_client._resolve_generation_url('wss://x/api-ws/v1/inference'),
            dashscope_client.DASHSCOPE_GENERATION_URL,
        )
        self.assertEqual(dashscope_client._resolve_generation_url(''), dashscope_client.DASHSCOPE_GENERATION_URL)

    def test_extract_transcription_text_shapes(self):
        self.assertEqual(dashscope_client._extract_transcription_text({'choices': [{'message': {'content': 'hi'}}]}), 'hi')
        self.assertEqual(dashscope_client._extract_transcription_text({'output': {'choices': [{'message': {'content': [{'text': 'a'}, {'text': 'b'}]}}]}}), 'ab')
        self.assertEqual(dashscope_client._extract_transcription_text({'text': ' raw '}), 'raw')

    def test_chat_completion_posts_and_parses(self):
        with override_settings(QWEN_API_KEY='sk-test'), \
             mock.patch.object(dashscope_client, '_get_session') as session:
            session.return_value.post.return_value = FakeResponse({'choices': [{'message': {'content': 'ok'}}]})
            resp = dashscope_client.chat_completion(model='qwen3.6-plus', messages=[{'role': 'user', 'content': 'hi'}])
        self.assertEqual(resp['choices'][0]['message']['content'], 'ok')
        args, kwargs = session.return_value.post.call_args
        self.assertEqual(args[0], dashscope_client.DASHSCOPE_CHAT_URL)
        self.assertEqual(kwargs['json']['model'], 'qwen3.6-plus')

    def test_missing_api_key_raises(self):
        with override_settings(QWEN_API_KEY=''):
            with self.assertRaises(RuntimeError):
                dashscope_client.chat_completion(model='m', messages=[])

    def test_stream_chat_parses_sse_data_lines(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"你好"}}]}',
            '',
            'data: {"choices":[{"delta":{"content":"世界"}}]}',
            'data: [DONE]',
        ]
        with override_settings(QWEN_API_KEY='sk-test'), mock.patch.object(dashscope_client, '_get_session') as session:
            session.return_value.post.return_value = FakeResponse(lines=lines)
            chunks = list(dashscope_client.stream_chat_completion(model='m', messages=[]))
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]['choices'][0]['delta']['content'], '你好')

    def test_http_error_is_wrapped(self):
        with override_settings(QWEN_API_KEY='sk-test'), mock.patch.object(dashscope_client, '_get_session') as session:
            session.return_value.post.return_value = FakeResponse(status_code=401, text='bad key')
            with self.assertRaises(RuntimeError) as ctx:
                dashscope_client.embed_text(model='m', texts=['x'])
        self.assertIn('401', str(ctx.exception))


class VolcengineClientTests(TestCase):
    def test_convert_messages_multimodal(self):
        messages = [
            {'role': 'system', 'content': 'sys'},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': 'look'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,xxx'}},
            ]},
        ]
        converted = volcengine_client._convert_messages(messages)
        self.assertEqual(converted[0]['content'], 'sys')
        self.assertEqual(converted[1]['content'][0]['type'], 'input_text')
        self.assertEqual(converted[1]['content'][1]['type'], 'input_image')

    def test_raw_audio_input_rejected(self):
        with self.assertRaises(RuntimeError):
            volcengine_client._build_chat_payload(
                model='m', messages=[{'role': 'user', 'content': [{'type': 'input_audio'}]}],
                temperature=0.2, tools=None, tool_choice=None, response_format=None,
            )

    def test_tools_rejected(self):
        with self.assertRaises(RuntimeError):
            volcengine_client._build_chat_payload(
                model='m', messages=[], temperature=0.2, tools=[{'x': 1}], tool_choice=None, response_format=None,
            )

    def test_extract_text_from_response(self):
        payload = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '答案'}]}]}
        self.assertEqual(volcengine_client._extract_text_from_response(payload), '答案')
        self.assertEqual(volcengine_client._extract_text_from_response({'output_text': ' direct '}), 'direct')

    def test_normalize_chat_response(self):
        resp = volcengine_client._normalize_chat_response({'output_text': 'hi', 'id': 'r1'}, model='doubao')
        self.assertEqual(resp['choices'][0]['message']['content'], 'hi')
        self.assertEqual(resp['id'], 'r1')

    def test_stream_delta_extraction(self):
        self.assertEqual(volcengine_client._extract_stream_delta_text('message', {'type': 'response.output_text.delta', 'delta': 'x'}), 'x')
        self.assertEqual(volcengine_client._extract_stream_delta_text('message', {'type': 'response.reasoning.delta', 'delta': 'x'}), '')

    def test_ws_frame_round_trip(self):
        # Use a session event the server-frame parser recognizes (it reads the
        # session id only for connection/session events, not client task requests).
        frame = volcengine_client._build_tts_ws_frame(
            event=volcengine_client._WS_EVENT_SESSION_STARTED, payload={'text': '你好'}, session_id='sess123',
        )
        parsed = volcengine_client._parse_tts_ws_frame(frame, model='seed-tts-2.0')
        self.assertEqual(parsed['event'], volcengine_client._WS_EVENT_SESSION_STARTED)
        self.assertEqual(parsed['session_id'], 'sess123')
        self.assertEqual(parsed['payload_json'], {'text': '你好'})

    def test_ws_audio_frame_payload_preserved(self):
        # Build an audio-only server response frame manually and parse the payload bytes.
        audio = b'\x01\x02\x03MP3'
        header = bytes([0x11, (0xB << 4) | 0x4, (0x0 << 4) | 0x0, 0])
        event = volcengine_client._WS_EVENT_TTS_RESPONSE
        sid = b'sess'
        frame = header + event.to_bytes(4, 'big') + len(sid).to_bytes(4, 'big') + sid + len(audio).to_bytes(4, 'big') + audio
        parsed = volcengine_client._parse_tts_ws_frame(frame, model='m')
        self.assertEqual(parsed['event'], volcengine_client._WS_EVENT_TTS_RESPONSE)
        self.assertEqual(parsed['payload'], audio)

    def test_ws_error_frame_raises(self):
        header = bytes([0x11, (0xF << 4) | 0x0, (0x1 << 4) | 0x0, 0])
        err_payload = json.dumps({'message': 'boom'}).encode()
        frame = header + (500).to_bytes(4, 'big') + len(err_payload).to_bytes(4, 'big') + err_payload
        with self.assertRaises(RuntimeError) as ctx:
            volcengine_client._parse_tts_ws_frame(frame, model='m')
        self.assertIn('boom', str(ctx.exception))

    def test_asr_status_helpers(self):
        ok = FakeResponse(headers={'X-Api-Status-Code': '20000000'})
        self.assertEqual(volcengine_client._raise_for_asr_status(ok, action='a', model='m'), '20000000')
        pending = FakeResponse(headers={'X-Api-Status-Code': '20000001'})
        self.assertEqual(volcengine_client._raise_for_asr_status(pending, action='a', model='m', allow_pending=True), '20000001')
        with self.assertRaises(RuntimeError):
            volcengine_client._raise_for_asr_status(pending, action='a', model='m')

    def test_transcribe_polls_until_success(self):
        submit = FakeResponse(headers={'X-Api-Status-Code': '20000000'})
        pending = FakeResponse(headers={'X-Api-Status-Code': '20000001'})
        done = FakeResponse(payload={'result': {'text': '转写结果'}}, headers={'X-Api-Status-Code': '20000000'})
        with override_settings(VOLCENGINE_SPEECH_API_KEY='k', VOLCENGINE_ASR_POLL_INTERVAL_SECONDS='0.2'), \
             mock.patch.object(volcengine_client, '_get_session') as session:
            session.return_value.post.side_effect = [submit, pending, done]
            text = volcengine_client.transcribe_audio(
                model='volc.seedasr.auc', file_path='/tmp/x.mp3', public_file_url='https://example.com/x.mp3', language='zh',
            )
        self.assertEqual(text, '转写结果')
        self.assertEqual(session.return_value.post.call_count, 3)

    def test_transcribe_requires_public_url(self):
        with override_settings(VOLCENGINE_SPEECH_API_KEY='k'):
            with self.assertRaises(RuntimeError):
                volcengine_client.transcribe_audio(model='m', file_path='/tmp/x.mp3', public_file_url='')

    def test_embed_multimodal_model_branch(self):
        with override_settings(VOLCENGINE_API_KEY='k'), mock.patch.object(volcengine_client, '_get_session') as session:
            session.return_value.post.return_value = FakeResponse({'data': {'embedding': [0.1, 0.2]}})
            vecs = volcengine_client.embed_text(model='doubao-embedding-vision-251215', texts=['x'])
        self.assertEqual(vecs, [[0.1, 0.2]])
        args, _ = session.return_value.post.call_args
        self.assertIn('/embeddings/multimodal', args[0])
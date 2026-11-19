"""Service tests: pure utils (chunking, media encoding, OCR), private file storage,
WeChat token / content moderation and reset email.

Ported from helpdesk/backend/tests (test_attachment_captions.py,
test_content_moderation.py service-level cases, test_password_reset.py email
helpers). Provider/network boundaries are stubbed; no credentials or binaries
(ffmpeg / tesseract) are required.
"""
import io
import os
import tempfile
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from PIL import Image

from hongwen.models import UploadFile
from hongwen.services.utils import chunking, email as email_utils, moderation, wechat_token
from hongwen.services.utils.audio_utils import encode_audio_as_base64
from hongwen.services.utils.file_store import (
    build_attachment_caption,
    build_protected_upload_url,
    kb_document_display_name,
    materialize_upload_to_tempfile,
    normalize_upload_relative_path,
    open_upload,
    storage_name_for_relative_path,
    store_upload_bytes,
    upload_exists,
)
from hongwen.services.utils.image_utils import encode_image_as_data_url
from hongwen.services.utils.ocr import ocr_image_bytes
from hongwen.services.utils.video_utils import encode_video_as_base64

User = get_user_model()
OPENID = 'o' * 28


class _PrivateRootMixin:
    """Point HONGWEN_PRIVATE_MEDIA_ROOT at a scratch dir for the duration of a test."""

    def setUp(self):
        super().setUp()
        self.tmp_root = tempfile.mkdtemp(prefix='hongwen-test-private-')
        ov = override_settings(HONGWEN_PRIVATE_MEDIA_ROOT=self.tmp_root)
        ov.enable()
        self.addCleanup(ov.disable)


class ChunkingTests(TestCase):
    def test_extract_image_paths_dedupes(self):
        text = '![a](kb_assets/x.png) text ![b](kb_assets/x.png) ![c](kb_assets/y.png)'
        self.assertEqual(chunking.extract_image_paths(text), ['kb_assets/x.png', 'kb_assets/y.png'])

    def test_extract_image_paths_empty(self):
        self.assertEqual(chunking.extract_image_paths(''), [])

    def test_chunk_text_overlap(self):
        text = 'a' * 2000
        chunks = chunking.chunk_text(text, chunk_size=900, overlap=150)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 900 for c in chunks))

    def test_chunk_text_empty(self):
        self.assertEqual(chunking.chunk_text('   '), [])

    def test_extract_qa_pairs(self):
        text = 'Q: 路由器亮红灯怎么办？\nA: 先检查电源适配器。\nQ: 无法上网？\nA: 重启路由器。'
        pairs = chunking.extract_qa_pairs(text)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0]['question'], '路由器亮红灯怎么办？')
        self.assertTrue(pairs[0]['metadata']['is_qa'])


class CaptionTests(TestCase):
    def test_caption_strips_the_generated_storage_prefix(self):
        name = '0' * 32 + '_Gigaset A190.pdf'
        self.assertEqual(kb_document_display_name(name), 'Gigaset A190.pdf')
        self.assertEqual(build_attachment_caption(name, 'page_3'), 'Gigaset A190.pdf · page_3')

    def test_caption_leaves_unprefixed_names_alone(self):
        self.assertEqual(kb_document_display_name('manual.pdf'), 'manual.pdf')

    def test_caption_tolerates_missing_pieces(self):
        self.assertEqual(build_attachment_caption('', ''), '')
        self.assertEqual(build_attachment_caption('manual.pdf', ''), 'manual.pdf')


class UploadPathTests(TestCase):
    def test_normalize_strips_url_prefixes(self):
        self.assertEqual(normalize_upload_relative_path('/api/uploads/kb_assets/x.png'), 'kb_assets/x.png')
        self.assertEqual(normalize_upload_relative_path('uploads/abc__name.mp3'), 'abc__name.mp3')
        self.assertEqual(normalize_upload_relative_path('\\kb_assets\\x.png'), 'kb_assets/x.png')

    def test_build_protected_upload_url(self):
        self.assertEqual(build_protected_upload_url('kb_assets/x.png'), '/api/uploads/kb_assets/x.png')
        self.assertEqual(build_protected_upload_url('uploads/abc__name.mp3'), '/api/uploads/abc__name.mp3')
        self.assertEqual(build_protected_upload_url(''), '/api/uploads')

    def test_storage_name_mapping(self):
        self.assertEqual(storage_name_for_relative_path('kb_assets/x.png'), 'hongwen/kb_assets/x.png')
        self.assertEqual(storage_name_for_relative_path('triage_assets/x.png'), 'hongwen/triage_assets/x.png')
        self.assertEqual(storage_name_for_relative_path('tts/x.mp3'), 'hongwen/tts/x.mp3')
        # A bare filename (uploads/ collapsed by the URL) is a customer upload.
        self.assertEqual(storage_name_for_relative_path('abc__name.mp3'), 'hongwen/uploads/abc__name.mp3')
        self.assertEqual(storage_name_for_relative_path('uploads/abc__name.mp3'), 'hongwen/uploads/abc__name.mp3')


class PrivateStorageTests(_PrivateRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username='uploader', password='pw')

    def test_store_and_open_upload_round_trip(self):
        upload = store_upload_bytes(
            user=self.user, filename='voice.mp3', data=b'AUDIO', file_type='audio', mime_type='audio/mpeg',
        )
        self.assertIsInstance(upload, UploadFile)
        self.assertEqual(len(upload.file_id), 32)
        self.assertTrue(upload.file.name.startswith('hongwen/uploads/'))
        self.assertTrue(upload.file.name.endswith('__voice.mp3'))
        # Reached through the storage API, never .path.
        with open_upload(f'uploads/{upload.file_id}__voice.mp3') as fh:
            self.assertEqual(fh.read(), b'AUDIO')
        self.assertTrue(upload_exists(f'uploads/{upload.file_id}__voice.mp3'))

    def test_materialize_upload_to_tempfile(self):
        upload = store_upload_bytes(user=self.user, filename='clip.wav', data=b'RIFF', file_type='audio')
        tmp = materialize_upload_to_tempfile(upload)
        try:
            with open(tmp, 'rb') as fh:
                self.assertEqual(fh.read(), b'RIFF')
            self.assertTrue(tmp.endswith('.wav'))
        finally:
            os.remove(tmp)

    def test_kb_asset_bytes_round_trip(self):
        from hongwen.services.utils.file_store import save_kb_asset_bytes
        rel = save_kb_asset_bytes(b'PNG', 'png', subdir='kb_assets')
        self.assertTrue(rel.startswith('kb_assets/'))
        with open_upload(rel) as fh:
            self.assertEqual(fh.read(), b'PNG')


class MediaEncodingTests(TestCase):
    def test_encode_image_png_passthrough(self):
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4), 'red').save(buffer, format='PNG')
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as fh:
            fh.write(buffer.getvalue())
            path = fh.name
        try:
            mime, data_url = encode_image_as_data_url(path)
            self.assertEqual(mime, 'image/png')
            self.assertTrue(data_url.startswith('data:image/png;base64,'))
        finally:
            os.remove(path)

    def test_encode_image_converts_unsupported_to_jpeg(self):
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4), 'blue').save(buffer, format='BMP')
        with tempfile.NamedTemporaryFile(suffix='.bmp', delete=False) as fh:
            fh.write(buffer.getvalue())
            path = fh.name
        try:
            mime, data_url = encode_image_as_data_url(path)
            self.assertEqual(mime, 'image/jpeg')
            self.assertTrue(data_url.startswith('data:image/jpeg;base64,'))
        finally:
            os.remove(path)

    def test_encode_audio_falls_back_when_ffmpeg_missing(self):
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as fh:
            fh.write(b'ID3fake')
            path = fh.name
        try:
            with mock.patch('hongwen.services.utils.audio_utils.convert_to_mp3', side_effect=RuntimeError('no ffmpeg')):
                data_url = encode_audio_as_base64(path)
            self.assertTrue(data_url.startswith('data:audio/mpeg;base64,'))
        finally:
            os.remove(path)

    def test_encode_audio_uses_converted_mp3(self):
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as fh:
            fh.write(b'RIFFfake')
            path = fh.name
        converted = path + '.converted.mp3'
        with open(converted, 'wb') as fh:
            fh.write(b'MP3DATA')
        try:
            with mock.patch('hongwen.services.utils.audio_utils.convert_to_mp3', return_value=converted):
                data_url = encode_audio_as_base64(path)
            self.assertTrue(data_url.startswith('data:audio/mpeg;base64,'))
            self.assertFalse(os.path.exists(converted))  # temp cleaned up
        finally:
            os.remove(path)
            if os.path.exists(converted):
                os.remove(converted)

    def test_encode_video_falls_back_when_ffmpeg_missing(self):
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as fh:
            fh.write(b'fakevideo')
            path = fh.name
        try:
            with mock.patch('hongwen.services.utils.video_utils.convert_to_mp4', side_effect=RuntimeError('no ffmpeg')):
                data_url = encode_video_as_base64(path)
            self.assertTrue(data_url.startswith('data:video/mp4;base64,'))
        finally:
            os.remove(path)

    def test_ocr_returns_empty_without_tesseract(self):
        with mock.patch('pytesseract.image_to_string', side_effect=RuntimeError('no tesseract')):
            self.assertEqual(ocr_image_bytes(b'not-an-image'), '')


class WeChatTokenTests(TestCase):
    def setUp(self):
        wechat_token.reset_cache()
        self.addCleanup(wechat_token.reset_cache)

    def test_no_credentials_returns_none(self):
        with override_settings(WECHAT_HONGWEN_APPID='', WECHAT_HONGWEN_APPSECRET=''):
            self.assertFalse(wechat_token.has_credentials())
            self.assertIsNone(wechat_token.get_access_token())

    def test_token_is_cached(self):
        calls = {'n': 0}

        def fake_get(url, params=None, timeout=None):
            calls['n'] += 1
            return SimpleNamespace(json=lambda: {'access_token': f'tok-{calls["n"]}', 'expires_in': 7200})

        with override_settings(WECHAT_HONGWEN_APPID='appid', WECHAT_HONGWEN_APPSECRET='secret'):
            with mock.patch.object(wechat_token.requests, 'get', side_effect=fake_get):
                self.assertEqual(wechat_token.get_access_token(), 'tok-1')
                self.assertEqual(wechat_token.get_access_token(), 'tok-1')
                self.assertEqual(calls['n'], 1)
                self.assertEqual(wechat_token.get_access_token(force_refresh=True), 'tok-2')
                self.assertEqual(calls['n'], 2)

    def test_refreshable_errors(self):
        self.assertTrue(wechat_token.is_refreshable_error(40001))
        self.assertTrue(wechat_token.is_refreshable_error(42001))
        self.assertFalse(wechat_token.is_refreshable_error(87014))


class _ModerationStub:
    """Record both security endpoints instead of calling them (conftest.wechat_stub)."""

    def __init__(self):
        self.token_fetches = 0
        self.text_checks = []
        self.image_checks = []
        self._reply = {'errcode': 0, 'errmsg': 'ok', 'result': {'suggest': 'pass', 'label': 100}}
        self._queue = []

    def suggest(self, value):
        self._reply = {'errcode': 0, 'errmsg': 'ok', 'result': {'suggest': value, 'label': 21001}}

    def errcode(self, code, errmsg='system error'):
        self._reply = {'errcode': code, 'errmsg': errmsg}

    def answer_next(self, payload):
        self._queue.append(dict(payload))

    def next_reply(self):
        return dict(self._queue.pop(0)) if self._queue else dict(self._reply)

    def install(self, testcase):
        testcase.addCleanup(wechat_token.reset_cache)
        wechat_token.reset_cache()
        ov = override_settings(
            WECHAT_HONGWEN_APPID='test-app-id', WECHAT_HONGWEN_APPSECRET='test-secret', MODERATION_ENABLED=True,
        )
        ov.enable()
        testcase.addCleanup(ov.disable)

        def fake_get(url, params=None, timeout=None):
            self.token_fetches += 1
            return SimpleNamespace(json=lambda: {'access_token': f'token-{self.token_fetches}', 'expires_in': 7200})

        def fake_post_json(url, *, token, json_body=None, files=None):
            if files is None:
                self.text_checks.append({'url': url, 'token': token, 'body': json_body})
            else:
                self.image_checks.append({'url': url, 'token': token, 'files': files})
            return self.next_reply()

        p1 = mock.patch.object(wechat_token.requests, 'get', side_effect=fake_get)
        p2 = mock.patch.object(moderation, '_post_json', side_effect=fake_post_json)
        p1.start()
        p2.start()
        testcase.addCleanup(p1.stop)
        testcase.addCleanup(p2.stop)


class ModerationPolicyTests(TestCase):
    def test_should_block_fail_open(self):
        with override_settings(MODERATION_FAIL_OPEN=True):
            self.assertTrue(moderation.should_block(moderation.Suggest.RISKY))
            self.assertFalse(moderation.should_block(moderation.Suggest.ERROR))
            self.assertFalse(moderation.should_block(moderation.Suggest.REVIEW))
        with override_settings(MODERATION_FAIL_OPEN=False):
            self.assertTrue(moderation.should_block(moderation.Suggest.ERROR))
            self.assertTrue(moderation.should_block(moderation.Suggest.RISKY))

    def test_blank_text_is_skipped(self):
        self.assertIs(moderation.check_text('   ', OPENID), moderation.Suggest.SKIPPED)

    def test_disabled_is_skipped(self):
        with override_settings(MODERATION_ENABLED=False):
            self.assertIs(moderation.check_text('hello', OPENID), moderation.Suggest.SKIPPED)

    def test_no_openid_is_skipped(self):
        stub = _ModerationStub()
        stub.install(self)
        self.assertIs(moderation.check_text('hello', None), moderation.Suggest.SKIPPED)
        self.assertEqual(stub.token_fetches, 0)

    def test_clean_text_passes_and_records_request_shape(self):
        stub = _ModerationStub()
        stub.install(self)
        self.assertIs(moderation.check_text('路由器亮红灯怎么办', OPENID), moderation.Suggest.PASS)
        body = stub.text_checks[0]['body']
        self.assertEqual(body['content'], '路由器亮红灯怎么办')
        self.assertEqual(body['version'], 2)
        self.assertEqual(body['scene'], 2)
        self.assertEqual(body['openid'], OPENID)

    def test_risky_text_blocks(self):
        stub = _ModerationStub()
        stub.install(self)
        stub.suggest('risky')
        self.assertIs(moderation.check_text('bad', OPENID), moderation.Suggest.RISKY)

    def test_review_passes(self):
        stub = _ModerationStub()
        stub.install(self)
        stub.suggest('review')
        self.assertIs(moderation.check_text('maybe', OPENID), moderation.Suggest.REVIEW)
        self.assertFalse(moderation.should_block(moderation.Suggest.REVIEW))

    def test_infrastructure_error_is_fail_open(self):
        stub = _ModerationStub()
        stub.install(self)
        for code in (45009, 48001, -1, 66000):
            stub.errcode(code)
            self.assertIs(moderation.check_text('hello', OPENID), moderation.Suggest.ERROR)
            self.assertFalse(moderation.should_block(moderation.Suggest.ERROR))

    def test_long_text_is_chunked(self):
        stub = _ModerationStub()
        stub.install(self)
        self.assertIs(moderation.check_text('故' * 5001, OPENID), moderation.Suggest.PASS)
        self.assertEqual([len(c['body']['content']) for c in stub.text_checks], [2500, 2500, 1])

    def test_risky_chunk_stops_the_rest(self):
        stub = _ModerationStub()
        stub.install(self)
        stub.suggest('risky')
        self.assertIs(moderation.check_text('障' * 6000, OPENID), moderation.Suggest.RISKY)
        self.assertEqual(len(stub.text_checks), 1)

    def test_rejected_token_is_refreshed_and_retried(self):
        stub = _ModerationStub()
        stub.install(self)
        stub.answer_next({'errcode': 40001, 'errmsg': 'invalid credential'})
        self.assertIs(moderation.check_text('hello', OPENID), moderation.Suggest.PASS)
        self.assertEqual([c['token'] for c in stub.text_checks], ['token-1', 'token-2'])
        self.assertEqual(stub.token_fetches, 2)

    def test_token_fetched_once_for_a_conversation(self):
        stub = _ModerationStub()
        stub.install(self)
        moderation.check_text('first', OPENID)
        moderation.check_text('second', OPENID)
        self.assertEqual(stub.token_fetches, 1)
        self.assertEqual({c['token'] for c in stub.text_checks}, {'token-1'})

    def test_broken_token_endpoint_is_error_not_crash(self):
        stub = _ModerationStub()
        stub.install(self)
        with mock.patch.object(wechat_token.requests, 'get', side_effect=ConnectionError('no route')):
            self.assertIs(moderation.check_text('hello', OPENID), moderation.Suggest.ERROR)


class ModerationImageTests(TestCase):
    def _png_bytes(self, width, height):
        image = Image.frombytes('RGB', (width, height), os.urandom(width * height * 3))
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        return buffer.getvalue()

    def test_risky_image_errcode(self):
        stub = _ModerationStub()
        stub.install(self)
        stub.errcode(moderation.IMG_ERRCODE_RISKY, 'risky content')
        self.assertIs(moderation.check_image(self._png_bytes(64, 64), 'image/png'), moderation.Suggest.RISKY)
        self.assertEqual(len(stub.image_checks), 1)

    def test_clean_image_passes(self):
        stub = _ModerationStub()
        stub.install(self)
        self.assertIs(moderation.check_image(self._png_bytes(64, 64), 'image/png'), moderation.Suggest.PASS)

    def test_oversized_image_is_shrunk(self):
        stub = _ModerationStub()
        stub.install(self)
        oversized = self._png_bytes(1600, 1200)
        self.assertGreater(len(oversized), moderation.IMAGE_MAX_BYTES)
        self.assertIs(moderation.check_image(oversized, 'image/png'), moderation.Suggest.PASS)
        filename, payload, mime = stub.image_checks[0]['files']['media']
        self.assertEqual(filename, 'upload')
        self.assertLessEqual(len(payload), moderation.IMAGE_MAX_BYTES)
        self.assertEqual(mime, 'image/jpeg')
        with Image.open(io.BytesIO(payload)) as sent:
            self.assertLessEqual(sent.size[0], moderation.IMAGE_MAX_WIDTH)
            self.assertLessEqual(sent.size[1], moderation.IMAGE_MAX_HEIGHT)

    def test_undecodable_image_is_error(self):
        stub = _ModerationStub()
        stub.install(self)
        self.assertIs(moderation.check_image(b'not really a png', 'image/png'), moderation.Suggest.ERROR)
        self.assertEqual(stub.image_checks, [])

    def test_empty_image_is_skipped(self):
        self.assertIs(moderation.check_image(b'', 'image/png'), moderation.Suggest.SKIPPED)


class EmailTests(TestCase):
    def test_is_email_address(self):
        self.assertTrue(email_utils.is_email_address('user@example.com'))
        self.assertFalse(email_utils.is_email_address('no-at-sign'))
        self.assertFalse(email_utils.is_email_address('a@b'))
        self.assertFalse(email_utils.is_email_address(''))
        self.assertFalse(email_utils.is_email_address(None))

    def test_not_configured_skips(self):
        with override_settings(SMTP_HOST='', SMTP_PASSWORD=''):
            self.assertFalse(email_utils.is_configured())
            message = email_utils.build_email(to_addr='a@b.com', subject='s', text_body='t')
            self.assertFalse(email_utils.send_email(message))

    def test_send_records_and_returns_true(self):
        sent = []
        with override_settings(
            PASSWORD_RESET_EMAIL_ENABLED=True, SMTP_HOST='smtp.test.invalid', SMTP_PORT='465',
            SMTP_USERNAME='noreply@test.invalid', SMTP_PASSWORD='pw', SMTP_FROM='泓问 <noreply@test.invalid>',
        ):
            with mock.patch.object(email_utils, '_send', side_effect=lambda m: sent.append(m)):
                message = email_utils.build_password_reset_email(to_addr='user@example.com', code='123456', minutes=10)
                self.assertTrue(email_utils.send_email(message))
        self.assertEqual(len(sent), 1)
        self.assertIn('泓问', sent[0]['Subject'])
        self.assertIn('123456', sent[0].get_body(preferencelist=('plain',)).get_content())

    def test_send_failure_is_fail_closed(self):
        with override_settings(SMTP_HOST='smtp.test.invalid', SMTP_PASSWORD='pw'):
            with mock.patch.object(email_utils, '_send', side_effect=OSError('relay down')):
                message = email_utils.build_email(to_addr='a@b.com', subject='s', text_body='t')
                self.assertFalse(email_utils.send_email(message))

    def test_disabled_skips(self):
        with override_settings(PASSWORD_RESET_EMAIL_ENABLED=False, SMTP_HOST='h', SMTP_PASSWORD='p'):
            message = email_utils.build_email(to_addr='a@b.com', subject='s', text_body='t')
            self.assertFalse(email_utils.send_email(message))